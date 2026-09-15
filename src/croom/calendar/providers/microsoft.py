"""Read a room mailbox using app-only Microsoft Graph calendarView access."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from croom.calendar.providers.base import (
    CalendarEvent, CalendarProvider, detect_meeting_platform, extract_meeting_url,
)

try:
    import msal
except ImportError:
    msal = None

logger = logging.getLogger(__name__)


class CalendarSyncError(RuntimeError):
    """A failed/incomplete response must not look like an empty calendar."""


class MicrosoftCalendarProvider(CalendarProvider):
    GRAPH_API_ENDPOINT = "https://graph.microsoft.com/v1.0"
    AUTHORITY = "https://login.microsoftonline.com"
    SCOPES = ["https://graph.microsoft.com/.default"]

    def __init__(self):
        super().__init__()
        self._access_token = None
        self._token_expiry = None
        self._msal_app = None
        self._room_mailbox = None
        self._token_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "microsoft"

    @property
    def display_name(self) -> str:
        return "Microsoft 365"

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        self._authenticated = False
        self._access_token = None
        self._msal_app = None
        required = ("tenant_id", "client_id", "client_secret", "room_mailbox")
        if (credentials.get("auth_mode") != "client_credentials"
                or not all(isinstance(credentials.get(k), str) and credentials[k].strip()
                           for k in required)
                or credentials.get("tenant_id") in ("common", "organizations", "consumers")):
            logger.error("Microsoft room calendar requires explicit app credentials and mailbox")
            return False
        if msal is None:
            logger.error("Install croom[microsoft] for Microsoft calendar access")
            return False
        self._room_mailbox = credentials["room_mailbox"]
        try:
            # MSAL performs synchronous discovery and token requests; keep Qt and
            # the service loop responsive. Reuse its token cache on refresh.
            self._msal_app = await asyncio.to_thread(
                msal.ConfidentialClientApplication,
                credentials["client_id"],
                authority=f"{self.AUTHORITY}/{quote(credentials['tenant_id'], safe='')}",
                client_credential=credentials["client_secret"],
                timeout=30,
            )
            return await self.refresh_auth()
        except Exception:
            logger.error("Microsoft authentication failed; check tenant, credentials and network")
            return False

    async def refresh_auth(self) -> bool:
        async with self._token_lock:
            if (self._access_token and self._token_expiry
                    and datetime.now(timezone.utc) < self._token_expiry - timedelta(minutes=5)):
                return True
            if self._msal_app is None:
                return False
            try:
                result = await asyncio.to_thread(
                    self._msal_app.acquire_token_for_client, scopes=self.SCOPES,
                )
                if not result or not result.get("access_token"):
                    raise ValueError("No token")
                self._access_token = result["access_token"]
                self._token_expiry = datetime.now(timezone.utc) + timedelta(
                    seconds=int(result.get("expires_in", 3600)))
                self._authenticated = True
                return True
            except Exception:
                # Never log MSAL responses, exceptions, bearer tokens or credentials.
                self._access_token = None
                self._authenticated = False
                logger.error("Microsoft token acquisition failed")
                return False

    def _request_url(self, endpoint: str) -> str:
        url = (f"{self.GRAPH_API_ENDPOINT}{endpoint}"
               if endpoint.startswith("/") else endpoint)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                or not unquote(parsed.path).startswith("/v1.0/users/" + self._room_mailbox + "/")
                or parsed.fragment):
            raise CalendarSyncError("Graph returned an invalid paging URL")
        return url

    async def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        url = self._request_url(endpoint)
        if not await self.refresh_auth():
            raise CalendarSyncError("Microsoft calendar authentication unavailable")
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Prefer": 'outlook.timezone="UTC", IdType="ImmutableId"',
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(url, headers=headers, params=params, allow_redirects=False) as resp:
                    if resp.status != 200:
                        # Do not log Graph response bodies (may contain meeting details).
                        if resp.status == 401:
                            self._access_token = None
                            self._authenticated = False
                            # Evict MSAL's cached token before the next poll.
                            await asyncio.to_thread(self._msal_app.remove_tokens_for_client)
                        raise CalendarSyncError(f"Microsoft calendar request failed (HTTP {resp.status})")
                    result = await resp.json()
                    if not isinstance(result, dict) or not isinstance(result.get("value"), list):
                        raise CalendarSyncError("Invalid Microsoft calendar response")
                    return result
        except CalendarSyncError:
            raise
        except Exception:
            raise CalendarSyncError("Microsoft calendar request failed; check network") from None

    async def _items(self, endpoint, params=None):
        seen = set()
        while endpoint:
            if endpoint in seen or len(seen) >= 1000:
                raise CalendarSyncError("Microsoft calendar paging did not complete")
            seen.add(endpoint)
            result = await self._make_request(endpoint, params=params)
            for item in result["value"]:
                yield item
            endpoint = result.get("@odata.nextLink")
            params = None  # Graph nextLink already contains all query parameters.

    @property
    def _base(self):
        if not self._room_mailbox:
            raise CalendarSyncError("Microsoft room mailbox is not configured")
        return f"/users/{quote(self._room_mailbox, safe='')}"

    async def get_calendars(self) -> List[Dict[str, str]]:
        return [{"id": item["id"], "name": item.get("name", "Calendar"),
                 "primary": item.get("isDefaultCalendar", False)}
                async for item in self._items(f"{self._base}/calendars")]

    async def get_events(self, calendar_id: str, time_min: datetime,
                         time_max: datetime, max_results: int = 100) -> List[CalendarEvent]:
        # max_results controls page size, not completeness of the sync window.
        if max_results < 1:
            raise ValueError("max_results must be positive")
        time_min = time_min.replace(tzinfo=time_min.tzinfo or timezone.utc)
        time_max = time_max.replace(tzinfo=time_max.tzinfo or timezone.utc)
        calendar = "calendar" if calendar_id == "default" else f"calendars/{quote(calendar_id, safe='')}"
        params = {
            "startDateTime": time_min.isoformat(), "endDateTime": time_max.isoformat(),
            "$orderby": "start/dateTime", "$top": str(min(max_results, 1000)),
            "$select": "id,subject,start,end,organizer,body,bodyPreview,location,onlineMeeting,"
                       "onlineMeetingUrl,isAllDay,type,seriesMasterId,isCancelled,attendees,responseStatus",
        }
        events = {}
        async for item in self._items(f"{self._base}/{calendar}/calendarView", params):
            event = self._parse_event(item, calendar_id)
            if event is None:
                raise CalendarSyncError("Microsoft calendar contained an invalid event")
            events[event.id] = event
        return sorted(events.values(), key=lambda e: e.start_time)

    @staticmethod
    def _parse_time(value):
        result = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if result.tzinfo is None:
            # Graph is explicitly requested in UTC. Also accept IANA timezone
            # responses; unknown Windows timezone names fail instead of shifting a booking.
            zone = value.get("timeZone", "UTC")
            result = result.replace(tzinfo=ZoneInfo(zone))
        return result.astimezone(timezone.utc)

    def _parse_event(self, item: Dict, calendar_id: str) -> Optional[CalendarEvent]:
        try:
            meeting_url = None
            online = item.get("onlineMeeting") or {}
            for candidate in (online.get("joinUrl"), item.get("onlineMeetingUrl"),
                              (item.get("location") or {}).get("displayName"),
                              (item.get("body") or {}).get("content"), item.get("bodyPreview")):
                meeting_url = extract_meeting_url(unescape(candidate or ""))
                if meeting_url:
                    break
            return CalendarEvent(
                id=item["id"], title=item.get("subject") or "No title",
                start_time=self._parse_time(item["start"]), end_time=self._parse_time(item["end"]),
                meeting_url=meeting_url, meeting_platform=detect_meeting_platform(meeting_url),
                organizer=(item.get("organizer") or {}).get("emailAddress", {}).get("address", ""),
                description=item.get("bodyPreview", ""),
                location=(item.get("location") or {}).get("displayName", ""),
                calendar_id=calendar_id, is_all_day=item.get("isAllDay", False),
                is_recurring=item.get("type") in ("occurrence", "exception", "seriesMaster"),
                recurrence_id=item.get("seriesMasterId"),
                status="cancelled" if item.get("isCancelled") else "confirmed",
                attendees=[a.get("emailAddress", {}).get("address", "") for a in item.get("attendees", [])],
                response_status=(item.get("responseStatus") or {}).get("response", "none"),
            )
        except (KeyError, ValueError, TypeError, AttributeError):
            logger.error("Could not parse Microsoft calendar event")
            return None
