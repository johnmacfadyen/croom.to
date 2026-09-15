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
from croom.core.room_calendar import RoomActionError

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        Page,
        BrowserContext,
        Error as PlaywrightError,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

    class PlaywrightError(Exception):
        """Placeholder for optional-browser-free service imports and tests."""

        pass


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
        self._executable_path = None
        self._window_bounds = None
        self._playwright = None
        self._browser: Optional["Browser"] = None
        self._context: Optional["BrowserContext"] = None
        self._page: Optional["Page"] = None
        self._window_task = None

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

    def configure_browser(self, executable_path=""):
        self._executable_path = executable_path or None

    def set_window_bounds(self, bounds):
        self._window_bounds = bounds

    async def initialize(self) -> None:
        """Check prerequisites; open a visible browser only after the user joins."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")
        if self._executable_path:
            import os

            if not os.path.isfile(self._executable_path) or not os.access(
                self._executable_path, os.X_OK
            ):
                raise RuntimeError("Configured browser executable is unavailable")
        else:
            from pathlib import Path

            async with async_playwright() as playwright:
                if not Path(playwright.chromium.executable_path).exists():
                    raise RuntimeError("Install the Playwright Chromium browser")

    async def _open_browser(self):
        if self._page and not self._page.is_closed():
            return
        # Recover a closed window or a previous partial launch before retrying.
        await self.shutdown()
        self._playwright = await async_playwright().start()

        # Teams web works best with Edge/Chrome
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            executable_path=self._executable_path,
            args=[
                "--use-fake-ui-for-media-stream",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--class=croom-meeting",
                "--ozone-platform=x11",
            ]
            + self._placement_args(),
        )

        self._context = await self._browser.new_context(
            permissions=["camera", "microphone"], no_viewport=True
        )

        self._page = await self._context.new_page()
        await self._place_browser_window()
        self._window_task = asyncio.create_task(self._maintain_browser_window())

        logger.info("Teams browser opened")

    def _placement_args(self):
        bounds = self._window_bounds or {"x": 0, "y": 0, "width": 1280, "height": 720}
        return [
            f"--window-position={bounds['x']},{bounds['y']}",
            f"--window-size={bounds['width']},{bounds['height']}",
        ]

    async def _place_browser_window(self, only_if_needed=False):
        # A new Playwright context creates its own window after Chromium's launch
        # flags were processed. Place that actual window before going fullscreen.
        if not self._window_bounds:
            return
        session = await self._context.new_cdp_session(self._page)
        try:
            window = await session.send("Browser.getWindowForTarget")
            window_id = window["windowId"]
            bounds = self._window_bounds
            actual = window.get("bounds", {})
            if (
                only_if_needed
                and actual.get("windowState") == "fullscreen"
                and all(
                    actual.get(key) == bounds[target]
                    for key, target in (
                        ("left", "x"),
                        ("top", "y"),
                        ("width", "width"),
                        ("height", "height"),
                    )
                )
            ):
                return
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
            await session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": bounds["x"],
                        "top": bounds["y"],
                        "width": bounds["width"],
                        "height": bounds["height"],
                    },
                },
            )
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "fullscreen"}},
            )
        finally:
            await session.detach()

    async def _maintain_browser_window(self):
        """Recover fullscreen lost after mapping/navigation without reloading Teams."""
        warned = False
        while self._page and not self._page.is_closed():
            await asyncio.sleep(5)
            try:
                await asyncio.wait_for(self._place_browser_window(only_if_needed=True), 10)
                warned = False
            except Exception as error:
                if not warned:
                    logger.warning("Could not restore Teams fullscreen (%s)", type(error).__name__)
                    warned = True

    async def shutdown(self) -> None:
        """Shutdown browser."""
        if self._window_task:
            self._window_task.cancel()
            await asyncio.gather(self._window_task, return_exceptions=True)
            self._window_task = None
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
        mic_on: bool = True,
    ) -> MeetingInfo:
        """Join a Teams meeting."""
        if not self.can_handle_url(meeting_url):
            raise ValueError("Unsupported Teams meeting URL")
        meeting_id = self.extract_meeting_id(meeting_url)

        self._current_meeting = MeetingInfo(
            platform=self.name,
            meeting_id=meeting_id,
            meeting_url=meeting_url,
            is_camera_on=camera_on,
            is_muted=not mic_on,
        )

        self._set_state(MeetingState.JOINING)
        logger.info("Joining Teams meeting")

        stage = "open the meeting browser"
        try:
            self._current_meeting.progress = "Opening meeting browser…"
            await self._open_browser()
            stage = "load the Teams page"
            self._current_meeting.progress = "Loading Teams…"
            await self._page.goto(meeting_url, wait_until="domcontentloaded", timeout=60000)

            stage = "load the Teams pre-join screen"
            self._current_meeting.progress = "Waiting for Teams pre-join screen…"
            await self._wait_for_prejoin()

            stage = "enter the room name and verify camera and microphone settings"
            self._current_meeting.progress = "Checking camera and microphone settings…"
            await self._handle_prejoin(display_name, camera_on, mic_on)

            stage = "submit the Teams join request"
            self._current_meeting.progress = "Joining Teams…"
            await self._click_join_button()

            stage = "confirm admission to the meeting"
            self._current_meeting.progress = "Waiting for Teams to admit this room…"
            await self._wait_for_connection()

            _, self._current_meeting.is_camera_on = await self._media_control("camera")
            _, microphone_on = await self._media_control("microphone")
            self._current_meeting.is_muted = not microphone_on
            self._set_state(MeetingState.CONNECTED)
            self._current_meeting.progress = ""
            logger.info("Connected to Teams meeting")

            return self._current_meeting

        except Exception as e:
            message = f"Teams could not {stage}. Check the TV, then tap Leave before retrying."
            logger.error("Teams join failed at %s (%s)", stage, type(e).__name__)
            self._current_meeting.progress = ""
            self._current_meeting.error_message = message
            self._set_state(MeetingState.ERROR)
            raise RoomActionError(message) from None

    async def _wait_for_prejoin(self, timeout=180):
        """Wait for actual controls, including slow navigation from Teams' launcher."""
        deadline = asyncio.get_running_loop().time() + timeout
        browser_options = (
            'button:has-text("Continue on this browser"), '
            'button:has-text("Continue in this browser"), '
            'button:has-text("Join on the web"), '
            '[data-tid="joinOnWeb"]'
        )
        browser_selected = False
        while asyncio.get_running_loop().time() < deadline:
            if self._page.is_closed():
                raise RuntimeError("Meeting browser was closed")
            try:
                name = await self._page.query_selector(
                    'input[placeholder*="name" i], input[aria-label*="name" i]'
                )
                if name and await name.is_visible():
                    return
                try:
                    await self._media_control("camera")
                    await self._media_control("microphone")
                    return
                except RuntimeError:
                    # Missing/unreadable controls do not authorize unknown media.
                    pass
                if not browser_selected:
                    button = await self._page.query_selector(browser_options)
                    if button and await button.is_visible():
                        await button.click(timeout=5000)
                        browser_selected = True
            except PlaywrightError:
                # Redirects can destroy the old page's execution context while
                # a query is in flight. Continue until the overall deadline.
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Teams pre-join controls did not become ready")

    async def _handle_prejoin(self, display_name: str, camera_on: bool, mic_on: bool) -> None:
        """Handle Teams pre-join screen."""
        # The guest name field is optional only for a signed-in pre-join page.
        # Do not silently swallow a failed fill and continue with an empty name.
        name_input = await self._page.query_selector(
            'input[placeholder*="name" i], input[aria-label*="name" i]'
        )
        if name_input and await name_input.is_visible():
            await name_input.fill(display_name)
            if await name_input.input_value() != display_name:
                raise RuntimeError("Teams room name could not be entered")

        deadline = asyncio.get_running_loop().time() + 30
        while True:
            try:
                await self._media_control("camera")
                await self._media_control("microphone")
                break
            except (RuntimeError, PlaywrightError):
                if asyncio.get_running_loop().time() >= deadline or self._page.is_closed():
                    raise RuntimeError("Teams media controls are not ready") from None
                await asyncio.sleep(0.5)

        # Verify the actual controls before joining; unknown media state must
        # not silently enable a microphone or camera against room defaults.
        await self._set_media("camera", camera_on)
        await self._set_media("microphone", mic_on)

    async def _media_control(self, kind):
        # Prefer the actual toggle. A device-picker button can mention "microphone"
        # and precede it in DOM order, so a combined broad selector is unsafe.
        toggles = {
            "camera": '[data-tid="toggle-video"], [data-tid="prejoin-video-toggle"]',
            "microphone": '[data-tid="toggle-mute"], [data-tid="prejoin-audio-toggle"]',
        }
        fallback = {
            "camera": 'button[aria-label*="turn" i][aria-label*="camera" i], [role="switch"][aria-label*="camera" i]',
            "microphone": 'button[aria-label^="mute" i], button[aria-label^="unmute" i], [role="switch"][aria-label*="mic" i]',
        }
        button = await self._page.query_selector(toggles[kind])
        if not button:
            button = await self._page.query_selector(fallback[kind])
        if not button:
            raise RuntimeError(f"Teams {kind} control is unavailable")
        # Current Teams uses input[type=checkbox][role=switch] without aria-checked.
        # Read the live checked property, not the HTML default/checked attribute.
        if await button.get_attribute("type") == "checkbox":
            return button, await button.is_checked()
        checked = await button.get_attribute("aria-checked")
        label = (
            await button.get_attribute("aria-label") or await button.get_attribute("title") or ""
        ).lower()
        if checked in ("true", "false"):
            return button, checked == "true"
        if kind == "microphone":
            if "unmute" in label:
                return button, False
            if "mute" in label:
                return button, True
        elif "turn off" in label or "turn camera off" in label:
            return button, True
        elif "turn on" in label or "turn camera on" in label:
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

    async def _wait_for_connection(self, timeout=300) -> None:
        """Only connected call controls prove admission; a lobby can also have Leave."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._page.is_closed():
                raise RuntimeError("Meeting browser was closed")
            try:
                toolbar = await self._page.query_selector('[data-tid="call-controls"]')
                mute = await self._page.query_selector('[data-tid="toggle-mute"]')
                if toolbar and mute and await toolbar.is_visible() and await mute.is_visible():
                    return
                lobby = await self._page.query_selector(
                    '[data-tid="lobby-screen"], [data-tid="lobby-message"]'
                )
                in_lobby = bool(lobby and await lobby.is_visible())
                if not in_lobby:
                    # Current Teams keeps the pre-join layout while waiting for
                    # the organiser, without either of the older lobby data IDs.
                    in_lobby = await self._page.get_by_text(
                        re.compile(r"Someone will let you in when the meeting starts", re.I)
                    ).first.is_visible()
                if in_lobby:
                    if self.state != MeetingState.IN_LOBBY:
                        self._set_state(MeetingState.IN_LOBBY)
                    self._current_meeting.progress = (
                        "Waiting for the organiser to start or admit the room…"
                    )
                    # A confirmed lobby is healthy waiting, not a connection
                    # timeout. Leave still cancels this task immediately.
                    deadline = loop.time() + timeout
                elif self.state == MeetingState.IN_LOBBY:
                    self._set_state(MeetingState.JOINING)
                    self._current_meeting.progress = "Connecting to Teams…"
            except PlaywrightError:
                # Admission can replace the page's execution context.
                pass
            await asyncio.sleep(1)
        raise RuntimeError("Teams admission could not be verified; check the browser")

    async def leave_meeting(self) -> None:
        """Leave Teams meeting."""
        if self._state == MeetingState.IDLE:
            return
        if not self._page:
            # Launch can fail before a page exists; Leave must still reset the
            # pending meeting so setup and a subsequent join remain available.
            self._set_state(MeetingState.LEAVING)
            await self.shutdown()
            self._current_meeting = None
            self._set_state(MeetingState.IDLE)
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

            await self.shutdown()

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
