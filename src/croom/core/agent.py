"""
Croom Agent - Main coordinator for the Croom system.

The agent orchestrates all services and handles the main event loop.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, Any

from croom.core.startup_compat import (
    ComponentService, audio_config, video_config, display_config,
    calendar_config, check_supported_config,
)
from croom.core.config import Config, load_config
from croom.core.service import ServiceManager, Service, ServiceState
from croom.platform.detector import PlatformDetector, PlatformInfo
from croom.platform.capabilities import CapabilityDetector, Capabilities

logger = logging.getLogger(__name__)


class CroomAgent:
    """
    Main Croom agent that coordinates all system components.

    The agent is responsible for:
    - Loading configuration
    - Detecting platform capabilities
    - Managing service lifecycle
    - Handling system signals
    - Coordinating between services
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the Croom agent.

        Args:
            config_path: Optional path to configuration file.
        """
        self.config: Config = load_config(config_path)
        self.platform_info: PlatformInfo = PlatformDetector.detect()
        self.capabilities: Capabilities = CapabilityDetector.detect()
        self.service_manager = ServiceManager()

        self._running = False
        self._main_task: Optional[asyncio.Task] = None

        logger.info(f"Croom Agent initialized on {self.platform_info.device.value}")
        logger.info(f"AI accelerators: {self.platform_info.ai_accelerators}")

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_signal(s))
            )

    async def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signal."""
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        await self.stop()

    def _initialize_services(self) -> None:
        """Initialize and register all services based on configuration."""
        # Import services dynamically to avoid circular imports
        # and allow optional dependencies

        check_supported_config(self.config)

        # AI Service (if enabled)
        if self.config.ai.enabled and not self.config.ai.privacy_mode:
            try:
                from croom.ai.service import AIService
                ai_service = AIService(self.config, self.capabilities)
                self.service_manager.register(ai_service)
                logger.info("AI service registered")
            except ImportError as e:
                logger.warning(f"AI service not available: {e}")

        # Audio Service
        try:
            from croom.audio.service import AudioService
            audio_service = ComponentService("audio", AudioService(audio_config(self.config)))
            self.service_manager.register(audio_service, dependencies=["ai"] if self.service_manager.get_service("ai") else None)
            logger.info("Audio service registered")
        except ImportError as e:
            logger.warning(f"Audio service not available: {e}")

        # Video Service
        try:
            from croom.video.service import VideoService
            video_service = ComponentService("video", VideoService(video_config(self.config)))
            self.service_manager.register(video_service, dependencies=["ai"] if self.service_manager.get_service("ai") else None)
            logger.info("Video service registered")
        except ImportError as e:
            logger.warning(f"Video service not available: {e}")

        # Display Service
        try:
            from croom.display.service import DisplayService
            display_service = ComponentService("display", DisplayService(display_config(self.config)))
            self.service_manager.register(display_service)
            logger.info("Display service registered")
        except ImportError as e:
            logger.warning(f"Display service not available: {e}")

        # Meeting Service
        try:
            from croom.meeting.service import MeetingService
            meeting_service = MeetingService(self.config)
            self.service_manager.register(meeting_service, dependencies=["audio", "video"])
            logger.info("Meeting service registered")
        except ImportError as e:
            logger.warning(f"Meeting service not available: {e}")

        # Calendar Service
        try:
            from croom.calendar.service import CalendarService
            calendar_settings = calendar_config(self.config)
            if calendar_settings is not None:
                calendar_service = ComponentService("calendar", CalendarService(calendar_settings))
                self.service_manager.register(calendar_service)
                logger.info("Calendar service registered")
        except ImportError as e:
            logger.warning(f"Calendar service not available: {e}")

        # Dashboard Connection Service
        if self.config.dashboard.enabled and self.config.dashboard.url:
            try:
                from croom.dashboard.client import DashboardClient
                dashboard_client = DashboardClient(self.config, self.capabilities)
                self.service_manager.register(dashboard_client)
                logger.info("Dashboard client registered")
            except ImportError as e:
                logger.warning(f"Dashboard client not available: {e}")

    async def join_calendar_event(self, event_id: str):
        """Join only after a room user selects a current, freshly synced booking."""
        from datetime import datetime, timezone
        calendar = self.service_manager.get_service("calendar")
        meeting = self.service_manager.get_service("meeting")
        if not calendar or not meeting or not meeting.is_running:
            raise RuntimeError("Room services are not ready")
        # Revalidate against Graph, including cancellations and changed URLs.
        await calendar.refresh()
        event = calendar.get_event_by_id(event_id)
        if (not event or not event.meeting_url or event.status == "cancelled"
                or event.response_status == "declined"
                or event.end_time <= datetime.now(timezone.utc)):
            raise ValueError("This booking is no longer available to join")
        if not (event.is_happening_now() or event.is_starting_soon(self.config.meeting.join_early_minutes)):
            raise ValueError("This booking is not ready to join yet")
        info = await meeting.join_meeting(event.meeting_url)
        info.title = event.title
        info.calendar_event_id = event.id
        info.start_time = event.start_time
        info.end_time = event.end_time
        return info

    async def start(self) -> None:
        """Start the Croom agent and all services."""
        if self._running:
            logger.warning("Agent already running")
            return

        logger.info("Starting Croom Agent...")
        self._running = True

        try:
            # Setup signal handlers
            self._setup_signal_handlers()

            # Initialize services
            self._initialize_services()

            # Start all services
            success = await self.service_manager.start_all()
            if not success:
                raise RuntimeError("Failed to start all services; see preceding logs")

            logger.info("Croom Agent started successfully")

            # Wait for shutdown
            await self.service_manager.wait_for_shutdown()

        except Exception as e:
            logger.exception("Agent error")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Croom agent and all services."""
        if not self._running:
            return

        logger.info("Stopping Croom Agent...")
        self._running = False

        # Stop all services
        await self.service_manager.stop_all()

        logger.info("Croom Agent stopped")

    def get_status(self) -> Dict[str, Any]:
        """
        Get current agent status.

        Returns:
            Dictionary containing agent and service status.
        """
        return {
            "running": self._running,
            "platform": {
                "device": self.platform_info.device.value,
                "os": self.platform_info.os_name,
                "arch": self.platform_info.arch,
                "ai_accelerators": self.platform_info.ai_accelerators,
            },
            "capabilities": self.capabilities.to_dict(),
            "services": {
                name: status if isinstance(status, dict) else status.__dict__
                for name, status in self.service_manager.get_status().items()
            },
            "config": {
                "room_name": self.config.room.name,
                "ai_enabled": self.config.ai.enabled,
                "platforms": self.config.meeting.platforms,
            }
        }

    def get_capabilities(self) -> Capabilities:
        """Get detected platform capabilities."""
        return self.capabilities

    def get_platform_info(self) -> PlatformInfo:
        """Get platform information."""
        return self.platform_info


async def run_agent(config_path: Optional[str] = None) -> None:
    """
    Run the Croom agent.

    Args:
        config_path: Optional path to configuration file.
    """
    agent = CroomAgent(config_path)
    await agent.start()


def main() -> None:
    """Main entry point for Croom agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Croom Agent")
    parser.add_argument(
        "-c", "--config",
        help="Path to configuration file",
        default=None
    )
    parser.add_argument(
        "-v", "--verbose",
        help="Enable verbose logging",
        action="store_true"
    )
    parser.add_argument(
        "--debug",
        help="Enable debug logging",
        action="store_true"
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.debug else (logging.INFO if args.verbose else logging.WARNING)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run agent
    try:
        asyncio.run(run_agent(args.config))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
