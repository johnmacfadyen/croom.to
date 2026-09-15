"""Shared booking eligibility and messages safe to show on the room screen."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class RoomActionError(RuntimeError):
    """An expected room action failure with a user-facing explanation."""


def calendar_join_issue(event, early_minutes, room_timezone="UTC", now=None):
    now = now or datetime.now(timezone.utc)
    if event is None:
        return "This booking is no longer on the room calendar. Refresh and select another booking."
    if event.status == "cancelled" or event.response_status == "declined":
        return "This booking was cancelled or declined by the room."
    if event.end_time <= now:
        return "This booking has ended."
    if not event.meeting_url:
        return "This booking has no supported meeting link. Add a Teams link to the invitation."
    opens = event.start_time - timedelta(minutes=early_minutes)
    if now < opens:
        local = opens.astimezone(ZoneInfo(room_timezone))
        return "Join available " + local.strftime("%a %d %b at %H:%M") + "."
    return None
