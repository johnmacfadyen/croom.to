import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from croom.core.config import Config
from croom.meeting.providers.base import MeetingState
from croom.meeting.providers.teams import TeamsProvider
from croom.meeting.service import MeetingService


@pytest.mark.asyncio
async def test_toggle_reads_actual_control_and_confirms_state():
    provider = TeamsProvider()
    label = {"value": "Unmute microphone"}

    async def attribute(name):
        return label["value"] if name == "aria-label" else None

    async def click():
        label["value"] = "Mute microphone"

    button = SimpleNamespace(get_attribute=attribute, click=click)
    provider._page = SimpleNamespace(query_selector=AsyncMock(return_value=button))
    provider._state = MeetingState.CONNECTED
    provider._current_meeting = SimpleNamespace(is_muted=True)
    assert await provider.toggle_mute() is False
    assert provider.current_meeting.is_muted is False


@pytest.mark.asyncio
async def test_missing_media_control_does_not_report_success():
    provider = TeamsProvider()
    provider._page = SimpleNamespace(query_selector=AsyncMock(return_value=None))
    provider._state = MeetingState.CONNECTED
    provider._current_meeting = SimpleNamespace(is_camera_on=True)
    with pytest.raises(RuntimeError, match="unavailable"):
        await provider.toggle_camera()
    assert provider.current_meeting.is_camera_on is True


@pytest.mark.asyncio
async def test_leave_failure_keeps_error_state():
    provider = TeamsProvider()
    provider._page = SimpleNamespace(
        query_selector=AsyncMock(return_value=None),
        goto=AsyncMock(side_effect=RuntimeError("browser error")),
    )
    provider._state = MeetingState.CONNECTED
    provider._current_meeting = SimpleNamespace(state=MeetingState.CONNECTED)
    with pytest.raises(RuntimeError, match="Could not leave"):
        await provider.leave_meeting()
    assert provider.state == MeetingState.ERROR
    assert provider.current_meeting is not None


@pytest.mark.asyncio
async def test_leave_cancels_join_and_duplicate_join_is_rejected():
    service = MeetingService(Config())
    started = asyncio.Event()

    async def join(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    provider = SimpleNamespace(
        join_meeting=join, state=MeetingState.IN_LOBBY, leave_meeting=AsyncMock()
    )
    service._providers = {"teams": provider}
    task = asyncio.create_task(
        service.join_meeting("https://teams.microsoft.com/l/meetup-join/test")
    )
    await started.wait()
    with pytest.raises(RuntimeError, match="already in progress"):
        await service.join_meeting("https://teams.microsoft.com/l/meetup-join/test")
    await service.leave_meeting()
    assert task.cancelled()
    provider.leave_meeting.assert_awaited_once()
    assert service.state == MeetingState.IDLE


@pytest.mark.parametrize(
    "url",
    ["https://teams.microsoft.com.evil.example/", "https://evil.example/?teams.microsoft.com"],
)
def test_teams_rejects_lookalike_hosts(url):
    assert not TeamsProvider.can_handle_url(url)


@pytest.mark.asyncio
async def test_failed_browser_launch_can_be_left_and_retried():
    provider = TeamsProvider()
    provider._state = MeetingState.ERROR
    provider._current_meeting = SimpleNamespace(error_message="launch failed")
    provider._playwright = SimpleNamespace(stop=AsyncMock())
    handle = provider._playwright
    await provider.leave_meeting()
    handle.stop.assert_awaited_once()
    assert provider.state == MeetingState.IDLE
    assert provider.current_meeting is None


@pytest.mark.asyncio
async def test_system_browser_initialization_is_lazy(tmp_path, monkeypatch):
    import croom.meeting.providers.teams as module

    path = tmp_path / "chromium"
    path.write_text("#!/bin/sh\n")
    path.chmod(0o700)
    monkeypatch.setattr(module, "PLAYWRIGHT_AVAILABLE", True)
    provider = TeamsProvider()
    provider.configure_browser(str(path))
    provider._open_browser = AsyncMock()
    await provider.initialize()
    provider._open_browser.assert_not_awaited()
    provider.set_window_bounds({"x": 800, "y": 0, "width": 1920, "height": 1080})
    assert "--window-position=800,0" in provider._placement_args()


@pytest.mark.asyncio
async def test_prejoin_waits_for_delayed_controls_without_joining(monkeypatch):
    provider = TeamsProvider()
    calls = {"count": 0}

    async def control(kind):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("Not loaded yet")
        return object(), False

    provider._media_control = control
    provider._page = SimpleNamespace(
        is_closed=lambda: False, query_selector=AsyncMock(return_value=None)
    )
    provider._click_join_button = AsyncMock()
    monkeypatch.setattr("croom.meeting.providers.teams.asyncio.sleep", AsyncMock())
    await provider._wait_for_prejoin(timeout=1)
    assert calls["count"] >= 4
    provider._click_join_button.assert_not_awaited()


@pytest.mark.asyncio
async def test_blank_prejoin_fails_with_stage_and_never_clicks_join():
    from croom.core.room_calendar import RoomActionError

    provider = TeamsProvider()
    provider._open_browser = AsyncMock()
    provider._page = SimpleNamespace(goto=AsyncMock())
    provider._wait_for_prejoin = AsyncMock(
        side_effect=RuntimeError("secret-url-or-browser-details")
    )
    provider._handle_prejoin = AsyncMock()
    provider._click_join_button = AsyncMock()
    with pytest.raises(RoomActionError, match="load the Teams pre-join screen") as error:
        await provider.join_meeting("https://teams.microsoft.com/l/meetup-join/fixture")
    assert "secret-url" not in str(error.value)
    assert provider.state == MeetingState.ERROR
    assert provider.current_meeting.progress == ""
    provider._handle_prejoin.assert_not_awaited()
    provider._click_join_button.assert_not_awaited()
    provider._page.goto.assert_awaited_once_with(
        "https://teams.microsoft.com/l/meetup-join/fixture",
        wait_until="domcontentloaded",
        timeout=60000,
    )


@pytest.mark.asyncio
async def test_prejoin_follows_browser_choice_once_and_waits_for_navigation(monkeypatch):
    from croom.meeting.providers.teams import PlaywrightError as Error

    provider = TeamsProvider()
    browser_button = SimpleNamespace(is_visible=AsyncMock(return_value=True), click=AsyncMock())

    async def query(selector):
        return None if selector.startswith("input") else browser_button

    provider._page = SimpleNamespace(
        is_closed=lambda: False, query_selector=AsyncMock(side_effect=query)
    )
    provider._media_control = AsyncMock(
        side_effect=[
            RuntimeError("loading"),
            Error("context destroyed"),
            RuntimeError("loading"),
            (object(), False),
            (object(), False),
        ]
    )
    monkeypatch.setattr("croom.meeting.providers.teams.asyncio.sleep", AsyncMock())
    await provider._wait_for_prejoin(timeout=1)
    browser_button.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_fullscreen_applies_to_created_window_after_placement():
    provider = TeamsProvider()
    provider.set_window_bounds({"x": 800, "y": 0, "width": 1920, "height": 1080})
    session = SimpleNamespace(send=AsyncMock(return_value={"windowId": 9}), detach=AsyncMock())
    provider._context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    provider._page = object()
    await provider._place_browser_window()
    calls = session.send.await_args_list
    assert calls[0].args == ("Browser.getWindowForTarget",)
    assert calls[1].args[1]["bounds"] == {"windowState": "normal"}
    assert calls[2].args[1]["bounds"] == {"left": 800, "top": 0, "width": 1920, "height": 1080}
    assert calls[3].args[1]["bounds"] == {"windowState": "fullscreen"}
    session.detach.assert_awaited_once()


@pytest.mark.asyncio
async def test_guest_name_is_filled_before_media_settings_and_join():
    provider = TeamsProvider()
    name = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        fill=AsyncMock(),
        input_value=AsyncMock(return_value="Cubby House"),
    )
    provider._page = SimpleNamespace(
        query_selector=AsyncMock(return_value=name), is_closed=lambda: False
    )
    provider._media_control = AsyncMock(return_value=(object(), False))
    provider._set_media = AsyncMock()
    await provider._handle_prejoin("Cubby House", False, False)
    name.fill.assert_awaited_once_with("Cubby House")
    assert provider._set_media.await_args_list[0].args == ("camera", False)
    assert provider._set_media.await_args_list[1].args == ("microphone", False)


@pytest.mark.asyncio
async def test_failed_name_entry_stops_before_media_or_join():
    provider = TeamsProvider()
    name = SimpleNamespace(
        is_visible=AsyncMock(return_value=True),
        fill=AsyncMock(),
        input_value=AsyncMock(return_value=""),
    )
    provider._page = SimpleNamespace(query_selector=AsyncMock(return_value=name))
    provider._set_media = AsyncMock()
    with pytest.raises(RuntimeError, match="room name"):
        await provider._handle_prejoin("Cubby House", False, False)
    provider._set_media.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["camera", "microphone"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_native_checkbox_reads_live_checked_state(kind, enabled):
    provider = TeamsProvider()

    async def attribute(name):
        return {"type": "checkbox", "aria-label": kind.title()}.get(name)

    checkbox = SimpleNamespace(get_attribute=attribute, is_checked=AsyncMock(return_value=enabled))
    provider._page = SimpleNamespace(query_selector=AsyncMock(return_value=checkbox))
    control, state = await provider._media_control(kind)
    assert control is checkbox
    assert state is enabled


@pytest.mark.asyncio
async def test_microphone_toggle_is_preferred_to_device_picker():
    provider = TeamsProvider()

    async def attribute(name):
        return {
            "type": "checkbox",
            "role": "switch",
            "data-tid": "toggle-mute",
            "title": "Mute mic (Ctrl+Shift+M)",
        }.get(name)

    toggle = SimpleNamespace(get_attribute=attribute, is_checked=AsyncMock(return_value=True))
    picker = SimpleNamespace(
        get_attribute=AsyncMock(return_value="Selected microphone: BRIO, open microphone options")
    )

    async def query(selector):
        return picker if "aria-label" in selector else toggle

    provider._page = SimpleNamespace(query_selector=AsyncMock(side_effect=query))
    control, enabled = await provider._media_control("microphone")
    assert control is toggle and enabled is True
    picker.get_attribute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fullscreen_guard_leaves_correct_window_untouched():
    provider = TeamsProvider()
    provider.set_window_bounds({"x": 800, "y": 0, "width": 3840, "height": 2160})
    session = SimpleNamespace(
        send=AsyncMock(
            return_value={
                "windowId": 9,
                "bounds": {
                    "left": 800,
                    "top": 0,
                    "width": 3840,
                    "height": 2160,
                    "windowState": "fullscreen",
                },
            }
        ),
        detach=AsyncMock(),
    )
    provider._context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    provider._page = object()
    await provider._place_browser_window(only_if_needed=True)
    session.send.assert_awaited_once_with("Browser.getWindowForTarget")
    session.detach.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actual",
    [
        {"left": 800, "top": 0, "width": 3840, "height": 2160, "windowState": "normal"},
        {"left": 0, "top": 0, "width": 800, "height": 480, "windowState": "fullscreen"},
    ],
)
async def test_fullscreen_guard_repairs_exited_fullscreen_or_wrong_output(actual):
    provider = TeamsProvider()
    provider.set_window_bounds({"x": 800, "y": 0, "width": 3840, "height": 2160})
    session = SimpleNamespace(
        send=AsyncMock(return_value={"windowId": 9, "bounds": actual}), detach=AsyncMock()
    )
    provider._context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=session))
    provider._page = object()
    await provider._place_browser_window(only_if_needed=True)
    assert session.send.await_args_list[-1].args[1]["bounds"] == {"windowState": "fullscreen"}
    assert session.send.await_args_list[-2].args[1]["bounds"]["left"] == 800


def admission_fixture(provider, monkeypatch, *, text_lobby=False, id_lobby=False, visible=False):
    frame = {"time": 0, "text_lobby": text_lobby, "id_lobby": id_lobby, "connected": visible}
    toolbar = SimpleNamespace(is_visible=AsyncMock(side_effect=lambda: frame["connected"]))
    lobby = SimpleNamespace(is_visible=AsyncMock(side_effect=lambda: frame["id_lobby"]))
    text = SimpleNamespace(
        first=SimpleNamespace(is_visible=AsyncMock(side_effect=lambda: frame["text_lobby"]))
    )

    async def query(selector):
        return lobby if "lobby-screen" in selector else toolbar

    provider._state = MeetingState.JOINING
    provider._current_meeting = SimpleNamespace(state=MeetingState.JOINING, progress="")
    provider._page = SimpleNamespace(
        is_closed=lambda: False,
        query_selector=AsyncMock(side_effect=query),
        get_by_text=lambda pattern: text,
    )
    monkeypatch.setattr(
        "croom.meeting.providers.teams.asyncio.get_running_loop",
        lambda: SimpleNamespace(time=lambda: frame["time"]),
    )
    return frame


@pytest.mark.asyncio
@pytest.mark.parametrize("lobby_kind", ["text_lobby", "id_lobby"])
async def test_verified_lobby_waits_beyond_connection_timeout_until_admitted(
    monkeypatch, lobby_kind
):
    provider = TeamsProvider()
    frame = admission_fixture(provider, monkeypatch, **{lobby_kind: True})

    async def tick(seconds):
        assert provider.state == MeetingState.IN_LOBBY
        assert "organiser" in provider.current_meeting.progress
        frame["time"] += seconds
        if frame["time"] == 8:
            frame["connected"] = True
            frame[lobby_kind] = False

    monkeypatch.setattr("croom.meeting.providers.teams.asyncio.sleep", tick)
    await provider._wait_for_connection(timeout=3)
    assert frame["time"] == 8


@pytest.mark.asyncio
async def test_hidden_lobby_and_hidden_call_controls_do_not_prove_admission(monkeypatch):
    provider = TeamsProvider()
    frame = admission_fixture(provider, monkeypatch)

    async def tick(seconds):
        frame["time"] += seconds

    monkeypatch.setattr("croom.meeting.providers.teams.asyncio.sleep", tick)
    with pytest.raises(RuntimeError, match="admission could not be verified"):
        await provider._wait_for_connection(timeout=3)
    assert provider.state == MeetingState.JOINING


@pytest.mark.asyncio
async def test_lobby_disappearing_returns_to_connecting_then_times_out(monkeypatch):
    provider = TeamsProvider()
    frame = admission_fixture(provider, monkeypatch, text_lobby=True)

    async def tick(seconds):
        frame["time"] += seconds
        frame["text_lobby"] = False

    monkeypatch.setattr("croom.meeting.providers.teams.asyncio.sleep", tick)
    with pytest.raises(RuntimeError, match="admission could not be verified"):
        await provider._wait_for_connection(timeout=3)
    assert provider.state == MeetingState.JOINING
    assert provider.current_meeting.progress == "Connecting to Teams…"


@pytest.mark.asyncio
async def test_browser_shutdown_cancels_fullscreen_guard():
    provider = TeamsProvider()
    provider._window_task = asyncio.create_task(asyncio.Event().wait())
    task = provider._window_task
    await provider.shutdown()
    assert task.cancelled()
    assert provider._window_task is None
