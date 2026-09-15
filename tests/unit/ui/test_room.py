"""Headless Qt checks of the real room UI calling service interfaces."""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6")
pytest.importorskip("qasync")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from croom.core.config import Config
from croom.calendar.providers.base import CalendarEvent
from croom_ui.main import RoomWindow


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    now = datetime.now(timezone.utc)
    event = CalendarEvent("event", "A real calendar booking", now, now + timedelta(hours=1),
                          meeting_url="https://teams.microsoft.com/l/meetup-join/test")
    calendar = SimpleNamespace(is_running=True, last_sync=now, sync_error=None, events=[event], refresh=AsyncMock())
    meeting = SimpleNamespace(is_running=True, state=SimpleNamespace(value="idle"), current_meeting=None,
                              get_available_platforms=lambda: ["teams"], leave_meeting=AsyncMock(),
                              toggle_mute=AsyncMock(), toggle_camera=AsyncMock())
    services = {"calendar": calendar, "meeting": meeting}
    agent = SimpleNamespace(config=Config(), service_manager=SimpleNamespace(get_service=services.get),
                            join_calendar_event=AsyncMock())
    widget = RoomWindow(agent)
    widget.show()
    app.processEvents()
    yield widget, agent, calendar, meeting
    widget.timer.stop()
    widget.hide()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.asyncio
async def test_ui_select_join_and_real_controls(window):
    widget, agent, calendar, meeting = window
    assert widget.bookings.count() == 1
    assert widget.bookings.item(0).data(Qt.UserRole) == "event"
    widget.bookings.setCurrentRow(0)
    widget.buttons["join"].click()
    await asyncio.gather(*widget.tasks)
    agent.join_calendar_event.assert_awaited_once_with("event")
    # Service remains idle; no timer can manufacture connected state.
    assert "Meeting: idle" in widget.status.text()
    meeting.state.value = "connected"
    widget.update_status()
    for name in ("mute", "camera", "leave"):
        widget.buttons[name].click()
        await asyncio.gather(*widget.tasks)
    meeting.toggle_mute.assert_awaited_once()
    meeting.toggle_camera.assert_awaited_once()
    meeting.leave_meeting.assert_awaited_once()


@pytest.mark.asyncio
async def test_ui_error_is_visible_and_stale_calendar_disables_join(window):
    widget, agent, calendar, meeting = window
    calendar.sync_error = "Calendar sync failed; showing previous bookings"
    widget.update_status()
    assert not widget.buttons["join"].isEnabled()
    assert "previous bookings" in widget.status.text()
    calendar.refresh.side_effect = RuntimeError("private backend error")
    widget.buttons["refresh"].click()
    await asyncio.gather(*widget.tasks)
    assert "Action failed" in widget.message.text()
    assert "private" not in widget.message.text()
    assert widget.bookings.count() == 1
