"""Graph boundary tests; no tenant, credentials or network needed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from croom.calendar.providers.microsoft import CalendarSyncError, MicrosoftCalendarProvider
from croom.calendar.providers.base import extract_meeting_url, detect_meeting_platform, MeetingPlatform
from croom.calendar.service import CalendarService
from croom.core.config import Config, load_config
from croom.core.startup_compat import calendar_config, check_supported_config

NOW = datetime.now(timezone.utc)
URL = "https://teams.microsoft.com/l/meetup-join/fixture?context=a&b=c"


def event(event_id="occurrence-1", **overrides):
    return {
        "id": event_id, "subject": "Room meeting", "type": "occurrence",
        "seriesMasterId": "series", "start": {"dateTime": (NOW + timedelta(seconds=30)).isoformat()},
        "end": {"dateTime": (NOW + timedelta(hours=1)).isoformat()},
        "onlineMeeting": {"joinUrl": URL}, **overrides,
    }


def config(tmp_path):
    credential = tmp_path / "room.json"
    credential.write_text('{"client_secret": "fixture-secret"}')
    credential.chmod(0o600)
    return Config.from_dict({"calendar": {
        "providers": ["microsoft"], "microsoft_tenant_id": "tenant",
        "microsoft_client_id": "client", "microsoft_room_mailbox": "room@example.com",
        "microsoft_auth_mode": "client_credentials", "microsoft_credentials_path": str(credential),
    }})


def test_config_roundtrip_and_secret_separation(tmp_path):
    original = config(tmp_path)
    path = tmp_path / "config.yaml"
    original.save(path)
    assert "fixture-secret" not in path.read_text()
    loaded = load_config(path)
    assert loaded.calendar == original.calendar
    mapped = calendar_config(loaded)
    assert mapped["credentials"]["client_secret"] == "fixture-secret"
    assert mapped["credentials"]["room_mailbox"] == "room@example.com"
    assert mapped["calendar_ids"] == ["default"]
    assert loaded.meeting.join_policy == "manual"


@pytest.mark.parametrize("field,value", [
    ("microsoft_auth_mode", "device_code"), ("microsoft_room_mailbox", ""),
    ("microsoft_tenant_id", "common"), ("microsoft_credentials_path", "relative.json"),
    ("providers", ["google"]), ("sync_interval_seconds", 0),
])
def test_invalid_configuration(tmp_path, field, value):
    cfg = config(tmp_path)
    setattr(cfg.calendar, field, value)
    with pytest.raises(ValueError):
        check_supported_config(cfg)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660])
def test_reject_readable_credentials(tmp_path, mode):
    cfg = config(tmp_path)
    from pathlib import Path
    Path(cfg.calendar.microsoft_credentials_path).chmod(mode)
    with pytest.raises(ValueError, match="0600"):
        calendar_config(cfg)


def test_reject_symlink(tmp_path):
    cfg = config(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(cfg.calendar.microsoft_credentials_path)
    cfg.calendar.microsoft_credentials_path = str(link)
    with pytest.raises(OSError):
        calendar_config(cfg)


@pytest.mark.asyncio
async def test_app_auth_refresh_and_redaction(tmp_path, caplog):
    app = MagicMock()
    app.acquire_token_for_client.return_value = {"access_token": "fixture-token", "expires_in": 3600}
    msal = SimpleNamespace(ConfidentialClientApplication=MagicMock(return_value=app))
    provider = MicrosoftCalendarProvider()
    credentials = calendar_config(config(tmp_path))["credentials"]
    with patch("croom.calendar.providers.microsoft.msal", msal):
        assert await provider.authenticate(credentials)
        assert provider._base == "/users/room%40example.com"
        assert await provider.refresh_auth()
        assert app.acquire_token_for_client.call_count == 1
        provider._token_expiry = NOW - timedelta(seconds=1)
        assert await provider.refresh_auth()
        assert app.acquire_token_for_client.call_count == 2
        provider._token_expiry = NOW - timedelta(seconds=1)
        app.acquire_token_for_client.side_effect = RuntimeError("fixture-secret fixture-token")
        assert not await provider.refresh_auth()
        assert provider._access_token is None
    assert "fixture-secret" not in caplog.text
    assert "fixture-token" not in caplog.text


@pytest.mark.asyncio
async def test_calendarview_paging_occurrences_and_dedup():
    provider = MicrosoftCalendarProvider()
    provider._room_mailbox = "room@example.com"
    next_url = "https://graph.microsoft.com/v1.0/users/room%40example.com/calendar/calendarView?$skiptoken=x"
    provider._make_request = AsyncMock(side_effect=[
        {"value": [event()], "@odata.nextLink": next_url},
        {"value": [event(), event("occurrence-2", isCancelled=True)]},
    ])
    events = await provider.get_events("default", NOW, NOW + timedelta(days=7), max_results=1)
    assert len(events) == 2
    assert events[0].is_recurring
    assert events[0].recurrence_id == "series"
    assert events[1].status == "cancelled"
    call = provider._make_request.call_args_list[0]
    assert call.args[0] == "/users/room%40example.com/calendar/calendarView"
    assert call.kwargs["params"]["startDateTime"] == NOW.isoformat()
    assert "$filter" not in call.kwargs["params"]
    provider._make_request.assert_awaited_with(next_url, params=None)


@pytest.mark.parametrize("url", [
    "https://evil.example/v1.0/users/room%40example.com/calendar/calendarView",
    "https://graph.microsoft.com.evil.example/v1.0/users/room%40example.com/calendar/calendarView",
    "http://graph.microsoft.com/v1.0/users/room%40example.com/calendar/calendarView",
    "https://graph.microsoft.com/v1.0/users/other/calendar/calendarView",
])
def test_paging_cannot_send_token_elsewhere(url):
    provider = MicrosoftCalendarProvider()
    provider._room_mailbox = "room@example.com"
    with pytest.raises(CalendarSyncError):
        provider._request_url(url)


@pytest.mark.parametrize("value,expected", [
    ({"dateTime": "2026-09-15T10:00:00.0000000", "timeZone": "UTC"}, "2026-09-15T10:00:00+00:00"),
    ({"dateTime": "2026-09-15T10:00:00-07:00"}, "2026-09-15T17:00:00+00:00"),
    ({"dateTime": "2026-09-15T10:00:00", "timeZone": "Australia/Melbourne"}, "2026-09-15T00:00:00+00:00"),
])
def test_timezone(value, expected):
    assert MicrosoftCalendarProvider._parse_time(value).isoformat() == expected


def test_html_body_and_url_host_validation():
    provider = MicrosoftCalendarProvider()
    parsed = provider._parse_event(event(onlineMeeting=None, body={
        "content": "<a href='" + URL.replace("&", "&amp;") + "'>Join Teams</a>"
    }), "default")
    assert parsed.meeting_url == URL
    assert extract_meeting_url("https://teams.cloud.microsoft/meet/123?p=abc")
    assert not extract_meeting_url("https://teams.microsoft.com.evil.example/l/meetup-join/abc")
    assert detect_meeting_platform("https://evil.example/?teams.microsoft.com") == MeetingPlatform.UNKNOWN


@pytest.mark.asyncio
async def test_sync_cancellations_failure_and_duplicate_notifications():
    provider = MicrosoftCalendarProvider()
    first = provider._parse_event(event(), "default")
    service = CalendarService()
    service._provider = SimpleNamespace(get_events=AsyncMock(return_value=[first, first]))
    service._calendar_ids = ["default"]
    notified = []
    service.on_meeting_starting(notified.append)
    await service.refresh()
    await service.refresh()
    assert len(notified) == 1
    assert service.next_meeting == first
    service._provider.get_events.side_effect = CalendarSyncError("HTTP 403")
    with pytest.raises(RuntimeError):
        await service.refresh()
    assert service.events == [first]
    assert service.sync_error
    service._provider.get_events.side_effect = None
    first.status = "cancelled"
    service._provider.get_events.return_value = [first]
    await service.refresh()
    assert service.events == []
    assert service.next_meeting is None
    assert service.sync_error is None


@pytest.mark.asyncio
async def test_first_sync_failure_fails_startup():
    service = CalendarService()
    service._provider = SimpleNamespace(get_events=AsyncMock(side_effect=CalendarSyncError("403")))
    service._calendar_ids = ["default"]
    with pytest.raises(RuntimeError):
        await service.start()
    assert service._poll_task is None
    await service.shutdown()


@pytest.mark.asyncio
async def test_request_headers_refresh_and_no_redirects():
    provider = MicrosoftCalendarProvider()
    provider._room_mailbox = "room@example.com"
    provider._access_token = "fixture-token"
    provider.refresh_auth = AsyncMock(return_value=True)
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"value": []})
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=response)
    with patch("croom.calendar.providers.microsoft.aiohttp.ClientSession") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        assert await provider._make_request(provider._base + "/calendar/calendarView") == {"value": []}
    provider.refresh_auth.assert_awaited_once()
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert 'outlook.timezone="UTC"' in session.get.call_args.kwargs["headers"]["Prefer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_http_failure_is_not_an_empty_calendar(status, caplog):
    provider = MicrosoftCalendarProvider()
    provider._room_mailbox = "room@example.com"
    provider._access_token = "fixture-token"
    provider._msal_app = MagicMock()
    provider.refresh_auth = AsyncMock(return_value=True)
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value={"private": "details"})
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=response)
    with patch("croom.calendar.providers.microsoft.aiohttp.ClientSession") as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        with pytest.raises(CalendarSyncError, match=f"HTTP {status}"):
            await provider._make_request(provider._base + "/calendar/calendarView")
    response.json.assert_not_awaited()
    if status == 401:
        assert provider._access_token is None
        provider._msal_app.remove_tokens_for_client.assert_called_once()
    assert "fixture-token" not in caplog.text


@pytest.mark.asyncio
async def test_incomplete_second_page_does_not_return_partial_success():
    provider = MicrosoftCalendarProvider()
    provider._room_mailbox = "room@example.com"
    provider._make_request = AsyncMock(side_effect=[
        {"value": [event()], "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/room%40example.com/calendar/calendarView?$skiptoken=fixture"},
        CalendarSyncError("503"),
    ])
    with pytest.raises(CalendarSyncError):
        await provider.get_events("default", NOW, NOW + timedelta(days=7))


@pytest.mark.asyncio
async def test_microsoft_calendar_lifecycle_with_actual_mapping(tmp_path):
    from croom.core.startup_compat import ComponentService
    from croom.core.service import ServiceManager
    app = MagicMock()
    app.acquire_token_for_client.return_value = {"access_token": "fixture-token"}
    msal = SimpleNamespace(ConfidentialClientApplication=MagicMock(return_value=app))
    service = CalendarService(calendar_config(config(tmp_path)))
    manager = ServiceManager()
    manager.register(ComponentService("calendar", service))
    with patch("croom.calendar.providers.microsoft.msal", msal), patch.object(
        MicrosoftCalendarProvider, "_make_request", new=AsyncMock(return_value={"value": [event()]})
    ) as request:
        assert await manager.start_all()
        try:
            assert service.events[0].meeting_url == URL
            assert service.last_sync
            assert service._poll_task
            assert request.call_args.args[0] == "/users/room%40example.com/calendar/calendarView"
        finally:
            await manager.stop_all()
        assert service.provider is None
        assert service._poll_task is None
