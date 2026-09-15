"""
Abstract base class for calendar providers.

All calendar providers must implement this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from enum import Enum
import re


class MeetingPlatform(Enum):
    """Supported video meeting platforms."""
    GOOGLE_MEET = "google_meet"
    MICROSOFT_TEAMS = "teams"
    ZOOM = "zoom"
    WEBEX = "webex"
    UNKNOWN = "unknown"


@dataclass
class CalendarEvent:
    """Represents a calendar event with video meeting info."""

    # Core event info
    id: str
    title: str
    start_time: datetime
    end_time: datetime

    # Meeting info
    meeting_url: Optional[str] = None
    meeting_platform: MeetingPlatform = MeetingPlatform.UNKNOWN
    meeting_id: Optional[str] = None

    # Event metadata
    organizer: str = ""
    description: str = ""
    location: str = ""
    calendar_id: str = ""
    is_all_day: bool = False

    # Status
    is_recurring: bool = False
    recurrence_id: Optional[str] = None
    status: str = "confirmed"  # confirmed, tentative, cancelled

    # Attendee info
    attendees: List[str] = field(default_factory=list)
    response_status: str = "accepted"  # accepted, declined, tentative, needsAction

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "meeting_url": self.meeting_url,
            "meeting_platform": self.meeting_platform.value,
            "meeting_id": self.meeting_id,
            "organizer": self.organizer,
            "description": self.description,
            "location": self.location,
            "is_all_day": self.is_all_day,
            "is_recurring": self.is_recurring,
            "status": self.status,
        }

    @property
    def duration_minutes(self) -> int:
        """Get event duration in minutes."""
        delta = self.end_time - self.start_time
        return int(delta.total_seconds() / 60)

    @property
    def has_video_meeting(self) -> bool:
        """Check if event has a video meeting link."""
        return self.meeting_url is not None and len(self.meeting_url) > 0

    def is_happening_now(self) -> bool:
        """Check if event is currently happening."""
        now = datetime.now(self.start_time.tzinfo)
        return self.start_time <= now <= self.end_time

    def is_starting_soon(self, minutes: int = 5) -> bool:
        """Check if event is starting within given minutes."""
        now = datetime.now(self.start_time.tzinfo)
        time_until_start = self.start_time - now
        return timedelta(0) <= time_until_start <= timedelta(minutes=minutes)

    def time_until_start(self) -> timedelta:
        """Get time until event starts."""
        now = datetime.now(self.start_time.tzinfo)
        return self.start_time - now


def detect_meeting_platform(url: str) -> MeetingPlatform:
    """
    Detect meeting platform from URL.

    Args:
        url: Meeting URL

    Returns:
        Detected platform
    """
    if not url:
        return MeetingPlatform.UNKNOWN

    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return MeetingPlatform.UNKNOWN
    host = (parsed.hostname or "").lower()
    if host in ("meet.google.com", "g.co"):
        return MeetingPlatform.GOOGLE_MEET
    if host in ("teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft"):
        return MeetingPlatform.MICROSOFT_TEAMS
    if any(host == domain or host.endswith("." + domain) for domain in ("zoom.us", "zoomgov.com")):
        return MeetingPlatform.ZOOM
    if host == "webex.com" or host.endswith(".webex.com"):
        return MeetingPlatform.WEBEX

    return MeetingPlatform.UNKNOWN


def extract_meeting_url(text: str) -> Optional[str]:
    """
    Extract video meeting URL from text.

    Args:
        text: Text that may contain meeting URLs

    Returns:
        First meeting URL found, or None
    """
    if not text:
        return None

    # Parse whole URLs before inspecting hosts, so attacker-controlled lookalikes
    # and links embedded in another URL cannot become browser join targets.
    for match in re.finditer(r"https?://[^\s<>\"']+", text, re.IGNORECASE):
        url = match.group(0).rstrip(".,;)")
        if detect_meeting_platform(url) != MeetingPlatform.UNKNOWN:
            return url
    return None



class CalendarProvider(ABC):
    """
    Abstract base class for calendar providers.

    Each provider handles authentication and fetching events
    from a specific calendar service.
    """

    def __init__(self):
        self._authenticated = False
        self._credentials = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Return provider name (e.g., 'google', 'microsoft')."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Return human-readable provider name."""
        pass

    @property
    def is_authenticated(self) -> bool:
        """Check if provider is authenticated."""
        return self._authenticated

    @abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """
        Authenticate with the calendar service.

        Args:
            credentials: Provider-specific credentials

        Returns:
            True if authentication successful
        """
        pass

    @abstractmethod
    async def refresh_auth(self) -> bool:
        """
        Refresh authentication tokens.

        Returns:
            True if refresh successful
        """
        pass

    @abstractmethod
    async def get_calendars(self) -> List[Dict[str, str]]:
        """
        Get list of available calendars.

        Returns:
            List of calendar info dicts with 'id' and 'name'
        """
        pass

    @abstractmethod
    async def get_events(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
        max_results: int = 100
    ) -> List[CalendarEvent]:
        """
        Get events from a calendar.

        Args:
            calendar_id: Calendar ID to fetch from
            time_min: Start of time range
            time_max: End of time range
            max_results: Maximum events to return

        Returns:
            List of calendar events
        """
        pass

    async def get_upcoming_events(
        self,
        calendar_id: str,
        hours: int = 24,
        max_results: int = 20
    ) -> List[CalendarEvent]:
        """
        Get upcoming events.

        Args:
            calendar_id: Calendar ID
            hours: Hours to look ahead
            max_results: Maximum events

        Returns:
            List of upcoming events sorted by start time
        """
        from datetime import timezone

        now = datetime.now(timezone.utc)
        time_max = now + timedelta(hours=hours)

        events = await self.get_events(
            calendar_id=calendar_id,
            time_min=now,
            time_max=time_max,
            max_results=max_results
        )

        # Sort by start time
        events.sort(key=lambda e: e.start_time)

        return events

    async def get_next_meeting(self, calendar_id: str) -> Optional[CalendarEvent]:
        """
        Get the next meeting with a video link.

        Args:
            calendar_id: Calendar ID

        Returns:
            Next meeting event, or None
        """
        events = await self.get_upcoming_events(calendar_id, hours=24)

        for event in events:
            if event.has_video_meeting and event.status == "confirmed":
                return event

        return None

    async def get_current_or_next_meeting(
        self,
        calendar_id: str
    ) -> Optional[CalendarEvent]:
        """
        Get current meeting (if in one) or next upcoming meeting.

        Args:
            calendar_id: Calendar ID

        Returns:
            Current or next meeting, or None
        """
        events = await self.get_upcoming_events(calendar_id, hours=24)

        for event in events:
            if not event.has_video_meeting or event.status != "confirmed":
                continue

            # If meeting is happening now, return it
            if event.is_happening_now():
                return event

            # If meeting hasn't started yet, it's the next one
            if event.time_until_start() > timedelta(0):
                return event

        return None

    def _extract_meeting_info(self, event: CalendarEvent) -> None:
        """
        Extract meeting URL and platform from event fields.

        Updates event in place with meeting_url and meeting_platform.
        """
        # Check location field
        if event.location:
            url = extract_meeting_url(event.location)
            if url:
                event.meeting_url = url
                event.meeting_platform = detect_meeting_platform(url)
                return

        # Check description
        if event.description:
            url = extract_meeting_url(event.description)
            if url:
                event.meeting_url = url
                event.meeting_platform = detect_meeting_platform(url)
                return
