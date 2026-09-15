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
    provider._page = SimpleNamespace(query_selector=AsyncMock(return_value=None),
                                      goto=AsyncMock(side_effect=RuntimeError("browser error")))
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
    provider = SimpleNamespace(join_meeting=join, state=MeetingState.IN_LOBBY, leave_meeting=AsyncMock())
    service._providers = {"teams": provider}
    task = asyncio.create_task(service.join_meeting("https://teams.microsoft.com/l/meetup-join/test"))
    await started.wait()
    with pytest.raises(RuntimeError, match="already in progress"):
        await service.join_meeting("https://teams.microsoft.com/l/meetup-join/test")
    await service.leave_meeting()
    assert task.cancelled()
    provider.leave_meeting.assert_awaited_once()
    assert service.state == MeetingState.IDLE


@pytest.mark.parametrize("url", ["https://teams.microsoft.com.evil.example/", "https://evil.example/?teams.microsoft.com"])
def test_teams_rejects_lookalike_hosts(url):
    assert not TeamsProvider.can_handle_url(url)
