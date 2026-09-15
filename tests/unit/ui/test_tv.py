from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from croom.calendar.providers.base import CalendarEvent
from croom.core.config import Config
from croom_ui.tv import tv_snapshot

NOW = datetime(2026, 9, 15, 3, 0, tzinfo=timezone.utc)


def booking(name, start, end, **kwargs):
    return CalendarEvent(
        name, name, NOW + timedelta(minutes=start), NOW + timedelta(minutes=end), **kwargs
    )


def snapshot(events, **kwargs):
    cfg = Config()
    cfg.room.timezone = "Australia/Melbourne"
    cfg.calendar.microsoft_auth_mode = "client_credentials"
    calendar = SimpleNamespace(events=events, last_sync=NOW, sync_error=None)
    for key, value in kwargs.items():
        setattr(calendar, key, value)
    return tv_snapshot(cfg, calendar, NOW)


def test_available_between_bookings_with_local_time_and_filtered_agenda():
    m = snapshot(
        [
            booking("Ended", -30, 0),
            booking("Next", 30, 60),
            booking("Cancelled", 1, 20, status="cancelled"),
            booking("Declined", 1, 20, response_status="declined"),
        ]
    )
    assert m["state"] == "Available"
    assert m["detail"] == "Free until 13:30"
    assert m["starts"] == "In 30 min"
    assert m["agenda"] == [("Next", "Today · 13:30 – 14:00", "")]


def test_overlapping_bookings_extend_busy_until_last_contiguous_end():
    m = snapshot(
        [
            booking("Now", -15, 10),
            booking("Overlap", 5, 45),
            booking("Adjacent", 45, 60),
            booking("Later", 90, 120),
        ]
    )
    assert m["state"] == "Room booked"
    assert m["detail"] == "Reserved until 14:00"
    assert m["title"] == "Now"


def test_stale_or_failed_calendar_never_claims_availability_or_shows_old_titles():
    for extra in (
        {"last_sync": NOW - timedelta(minutes=6)},
        {"sync_error": "Network error"},
        {"last_sync": None},
        {"last_sync": NOW + timedelta(minutes=1)},
    ):
        m = snapshot([booking("Old title", 10, 20)], **extra)
        assert m["state"] == "Check calendar"
        assert not m["agenda"]
        assert "Old title" not in repr(m)


def test_private_mode_hides_primary_and_agenda_titles_and_clips_to_four_bookings():
    cfg = Config()
    cfg.display.hide_meeting_titles = True
    calendar = SimpleNamespace(
        events=[booking(f"Secret {i}", i * 30, i * 30 + 20) for i in range(6)],
        last_sync=NOW,
        sync_error=None,
    )
    m = tv_snapshot(cfg, calendar, NOW)
    assert m["title"] == "Reserved meeting"
    assert "Secret" not in repr(m)
    assert len(m["agenda"]) == 4 and m["extra"] == 2


def test_unconfigured_and_empty_calendar_are_distinct():
    assert tv_snapshot(Config(), None, NOW)["state"] == "Welcome in."
    assert snapshot([])["state"] == "Available"
