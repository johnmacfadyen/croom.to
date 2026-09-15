"""
Microsoft Teams provider.

Handles joining and controlling Microsoft Teams meetings using browser automation.
"""

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

from croom.meeting.providers.base import MeetingProvider, MeetingInfo, MeetingState

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class TeamsProvider(MeetingProvider):
    """
    Microsoft Teams meeting provider.

    Uses Playwright for browser automation to join Teams meetings
    via the web client.
    """

    # URL patterns for Teams
    TEAMS_URL_PATTERNS = [
        # Standard Teams meeting link
        re.compile(r"teams\.microsoft\.com/l/meetup-join/", re.IGNORECASE),
        # Teams live event
        re.compile(r"teams\.live\.com/meet/", re.IGNORECASE),
        # Short link
        re.compile(r"aka\.ms/", re.IGNORECASE),
    ]

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Optional["Browser"] = None
        self._context: Optional["BrowserContext"] = None
        self._page: Optional["Page"] = None

    @property
    def name(self) -> str:
        return "teams"

    @property
    def display_name(self) -> str:
        return "Microsoft Teams"

    @classmethod
    def can_handle_url(cls, url: str) -> bool:
        """Check if URL is a Teams meeting link."""
        from croom.meeting.providers.base import detect_platform
        return detect_platform(url) == "teams"

    @classmethod
    def extract_meeting_id(cls, url: str) -> Optional[str]:
        """Extract meeting ID from Teams URL."""
        # Teams URLs contain the meeting info in the path/query
        parsed = urlparse(url)

        # Try to extract from path
        if "/l/meetup-join/" in parsed.path:
            # Meeting ID is URL-encoded in the path
            parts = parsed.path.split("/")
            for i, part in enumerate(parts):
                if part == "meetup-join" and i + 1 < len(parts):
                    return parts[i + 1][:20]  # Truncate for readability

        # For other formats, use hash of URL
        import hashlib
        return hashlib.md5(url.encode()).hexdigest()[:12]

    async def initialize(self) -> None:
        """Initialize browser for Teams."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        logger.info("Initializing Teams provider...")

        self._playwright = await async_playwright().start()

        # Teams web works best with Edge/Chrome
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=[
                "--use-fake-ui-for-media-stream",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
            ]
        )

        self._context = await self._browser.new_context(
            permissions=["camera", "microphone"],
            viewport={"width": 1920, "height": 1080}
        )

        self._page = await self._context.new_page()

        logger.info("Teams provider initialized")

    async def shutdown(self) -> None:
        """Shutdown browser."""
        if self._state == MeetingState.CONNECTED:
            await self.leave_meeting()

        if self._page:
            await self._page.close()
            self._page = None

        if self._context:
            await self._context.close()
            self._context = None

        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        logger.info("Teams provider shutdown")

    async def join_meeting(
        self,
        meeting_url: str,
        display_name: str = "Conference Room",
        camera_on: bool = True,
        mic_on: bool = True
    ) -> MeetingInfo:
        """Join a Teams meeting."""
        if not self._page:
            raise RuntimeError("Provider not initialized")

        if not self.can_handle_url(meeting_url):
            raise ValueError("Unsupported Teams meeting URL")
        meeting_id = self.extract_meeting_id(meeting_url)

        self._current_meeting = MeetingInfo(
            platform=self.name,
            meeting_id=meeting_id,
            meeting_url=meeting_url,
            is_camera_on=camera_on,
            is_muted=not mic_on
        )

        self._set_state(MeetingState.JOINING)
        logger.info(f"Joining Teams meeting: {meeting_id}")

        try:
            # Navigate to meeting
            await self._page.goto(meeting_url, wait_until="networkidle")
            await asyncio.sleep(2)

            # Handle "Continue on this browser" option
            await self._select_browser_option()

            # Handle pre-join screen
            await self._handle_prejoin(display_name, camera_on, mic_on)

            # Click join button
            await self._click_join_button()

            # Wait for connection
            await self._wait_for_connection()

            _, self._current_meeting.is_camera_on = await self._media_control("camera")
            _, microphone_on = await self._media_control("microphone")
            self._current_meeting.is_muted = not microphone_on
            self._set_state(MeetingState.CONNECTED)
            logger.info(f"Connected to Teams meeting: {meeting_id}")

            return self._current_meeting

        except Exception as e:
            logger.error("Failed to join Teams meeting; check the browser")
            self._current_meeting.error_message = "Teams join failed"
            self._set_state(MeetingState.ERROR)
            raise

    async def _select_browser_option(self) -> None:
        """Select 'Continue on this browser' option."""
        try:
            # Look for browser option
            selectors = [
                'button:has-text("Continue on this browser")',
                'button:has-text("Join on the web")',
                '[data-tid="joinOnWeb"]',
            ]

            for selector in selectors:
                try:
                    btn = await self._page.wait_for_selector(selector, timeout=5000)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(2)
                        return
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"No browser selection needed: {e}")

    async def _handle_prejoin(
        self,
        display_name: str,
        camera_on: bool,
        mic_on: bool
    ) -> None:
        """Handle Teams pre-join screen."""
        # Set display name
        try:
            name_input = await self._page.wait_for_selector(
                'input[placeholder*="name" i], input[aria-label*="name" i]',
                timeout=5000
            )
            if name_input:
                await name_input.fill(display_name)
        except Exception:
            pass

        # Verify the actual controls before joining; unknown media state must
        # not silently enable a microphone or camera against room defaults.
        await self._set_media("camera", camera_on)
        await self._set_media("microphone", mic_on)

    async def _media_control(self, kind):
        selectors = {
            "camera": '[data-tid="toggle-video"], [data-tid="prejoin-video-toggle"], button[aria-label*="camera" i], [role="switch"][aria-label*="camera" i]',
            "microphone": '[data-tid="toggle-mute"], [data-tid="prejoin-audio-toggle"], button[aria-label*="mic" i], [role="switch"][aria-label*="mic" i]',
        }
        button = await self._page.query_selector(selectors[kind])
        if not button:
            raise RuntimeError(f"Teams {kind} control is unavailable")
        checked = await button.get_attribute("aria-checked")
        label = (await button.get_attribute("aria-label") or "").lower()
        if checked in ("true", "false"):
            return button, checked == "true"
        if kind == "microphone":
            if "unmute" in label:
                return button, False
            if "mute" in label:
                return button, True
        elif "turn off" in label:
            return button, True
        elif "turn on" in label:
            return button, False
        raise RuntimeError(f"Cannot verify Teams {kind} state")

    async def _set_media(self, kind, enabled):
        button, current = await self._media_control(kind)
        if current != enabled:
            await button.click()
        for _ in range(10):
            _, current = await self._media_control(kind)
            if current == enabled:
                return current
            await asyncio.sleep(0.1)
        raise RuntimeError(f"Teams {kind} state did not change")

    async def _click_join_button(self) -> None:
        """Click Teams join button."""
        join_selectors = [
            'button:has-text("Join now")',
            'button:has-text("Join meeting")',
            '[data-tid="prejoin-join-button"]',
            'button[aria-label*="Join" i]',
        ]

        for selector in join_selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=3000)
                if btn:
                    await btn.click()
                    return
            except Exception:
                continue

        raise RuntimeError("Could not find Teams join button")

    async def _wait_for_connection(self) -> None:
        """Only connected call controls prove admission; a lobby can also have Leave."""
        for _ in range(300):
            toolbar = await self._page.query_selector('[data-tid="call-controls"]')
            mute = await self._page.query_selector('[data-tid="toggle-mute"]')
            if toolbar and mute:
                return
            lobby = await self._page.query_selector('[data-tid="lobby-screen"], [data-tid="lobby-message"]')
            if lobby and self.state != MeetingState.IN_LOBBY:
                self._set_state(MeetingState.IN_LOBBY)
            await asyncio.sleep(1)
        raise RuntimeError("Teams admission could not be verified; check the browser")

    async def leave_meeting(self) -> None:
        """Leave Teams meeting."""
        if not self._page or self._state == MeetingState.IDLE:
            return

        self._set_state(MeetingState.LEAVING)
        logger.info("Leaving Teams meeting...")

        try:
            # Click hangup button
            hangup_btn = await self._page.query_selector(
                '[aria-label*="hang up" i], [aria-label*="leave" i], [data-tid="hangup-main-btn"]'
            )
            if hangup_btn:
                await hangup_btn.click()
                await asyncio.sleep(1)

            await self._page.goto("about:blank")

        except Exception as e:
            self._set_state(MeetingState.ERROR)
            raise RuntimeError("Could not leave Teams; check the browser") from None

        self._current_meeting = None
        self._set_state(MeetingState.IDLE)
        logger.info("Left Teams meeting")

    async def toggle_camera(self) -> bool:
        if not self._page or self._state != MeetingState.CONNECTED:
            raise RuntimeError("Teams is not connected")
        _, current = await self._media_control("camera")
        actual = await self._set_media("camera", not current)
        self._current_meeting.is_camera_on = actual
        return actual

    async def toggle_mute(self) -> bool:
        if not self._page or self._state != MeetingState.CONNECTED:
            raise RuntimeError("Teams is not connected")
        _, current = await self._media_control("microphone")
        actual = await self._set_media("microphone", not current)
        self._current_meeting.is_muted = not actual
        return not actual
