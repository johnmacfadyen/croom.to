"""Manual joining must revalidate the booking and never follow notifications."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from croom.core.agent import CroomAgent
from croom.core.config import Config
from croom.core.service import ServiceManager
from croom.calendar.providers.base import CalendarEvent


def room():
    now = datetime.now(timezone.utc)
    event = CalendarEvent(
        "event",
        "Room",
        now - timedelta(minutes=1),
        now + timedelta(hours=1),
        meeting_url="https://teams.microsoft.com/l/meetup-join/fixture",
    )
    calendar = SimpleNamespace(
        name="calendar", refresh=AsyncMock(), get_event_by_id=lambda _: event
    )
    info = SimpleNamespace()
    meeting = SimpleNamespace(
        name="meeting", is_running=True, join_meeting=AsyncMock(return_value=info)
    )
    agent = object.__new__(CroomAgent)
    agent.config = Config()
    agent.service_manager = ServiceManager()
    agent.service_manager.register(calendar)
    agent.service_manager.register(meeting)
    return agent, calendar, meeting, event


@pytest.mark.asyncio
async def test_manual_join_routes_current_booking():
    agent, calendar, meeting, event = room()
    info = await agent.join_calendar_event("event")
    calendar.refresh.assert_awaited_once()
    meeting.join_meeting.assert_awaited_once_with(event.meeting_url)
    assert info.calendar_event_id == "event"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "condition", ["cancelled", "declined", "ended", "future", "missing", "sync_error"]
)
async def test_stale_or_unavailable_booking_never_joins(condition):
    agent, calendar, meeting, event = room()
    if condition == "cancelled":
        event.status = "cancelled"
    elif condition == "declined":
        event.response_status = "declined"
    elif condition == "ended":
        event.end_time = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif condition == "future":
        event.start_time = datetime.now(timezone.utc) + timedelta(hours=2)
    elif condition == "missing":
        calendar.get_event_by_id = lambda _: None
    else:
        calendar.refresh.side_effect = RuntimeError("offline")
    with pytest.raises((ValueError, RuntimeError)):
        await agent.join_calendar_event("event")
    meeting.join_meeting.assert_not_awaited()


def test_join_window_boundary_and_local_time_message():
    from croom.core.room_calendar import calendar_join_issue

    agent, _, _, event = room()
    event.start_time = datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc)
    event.end_time = event.start_time + timedelta(minutes=30)
    opens = event.start_time - timedelta(minutes=1)
    assert (
        calendar_join_issue(event, 1, "Australia/Melbourne", opens - timedelta(seconds=1))
        == "Join available Tue 15 Sep at 13:59."
    )
    assert calendar_join_issue(event, 1, "Australia/Melbourne", opens) is None
    assert (
        calendar_join_issue(event, 1, "Australia/Melbourne", event.end_time)
        == "This booking has ended."
    )


@pytest.mark.asyncio
async def test_missing_link_and_sync_failure_have_safe_explanations():
    from croom.core.room_calendar import RoomActionError

    agent, calendar, meeting, event = room()
    event.meeting_url = None
    with pytest.raises(RoomActionError, match="no supported meeting link"):
        await agent.join_calendar_event("event")
    calendar.refresh.side_effect = RuntimeError("private credential detail")
    with pytest.raises(RoomActionError, match="Calendar refresh failed") as result:
        await agent.join_calendar_event("event")
    assert "private" not in str(result.value)
    meeting.join_meeting.assert_not_awaited()
