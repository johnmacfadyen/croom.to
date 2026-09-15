"""One room process owns the agent, configuration server and both displays."""

import asyncio

from croom.core.agent import CroomAgent
from croom.core.config import load_config
from croom.core.startup_compat import calendar_config
from croom.calendar.service import CalendarService
from croom.setup.settings import RoomSettings


class RoomRuntime:
    def __init__(self, config_path, state_dir):
        self.config_path = str(config_path)
        self.settings = RoomSettings(config_path, state_dir)
        self.agent = self.make_agent()
        self.agent_task = None
        self.display = None
        self.start_error = None
        self._lock = asyncio.Lock()
        self._joining = False
        self.reconfiguring = False

    def make_agent(self):
        agent = CroomAgent(self.config_path)
        # The desktop owns signals and the meeting browser owns camera/audio.
        agent._setup_signal_handlers = lambda: None
        agent.config.meeting.browser_media = True
        return agent

    @property
    def config(self):
        return self.agent.config

    @property
    def service_manager(self):
        return self.agent.service_manager

    async def start(self):
        self.start_error = None
        agent = self.agent

        async def run():
            try:
                await agent.start()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.start_error = "Room services could not start. Review settings and test the calendar connection."

        self.agent_task = asyncio.create_task(run())

    async def stop_agent(self):
        if self.agent_task:
            self.agent_task.cancel()
            await asyncio.gather(self.agent_task, return_exceptions=True)
            self.agent_task = None
        await self.agent.stop()

    async def save(self, values):
        async with self._lock:
            meeting = self.service_manager.get_service("meeting")
            if self._joining or (meeting and meeting.current_meeting):
                raise ValueError("Leave the meeting before changing room settings")
            self.reconfiguring = True
            try:
                settings = await asyncio.to_thread(self.settings.save, values)
                await self.stop_agent()
                self.agent = self.make_agent()
                if self.display:
                    self.display.apply()
                await self.start()
                return {
                    "settings": settings,
                    "message": "Settings saved. Room services are restarting.",
                }
            finally:
                self.reconfiguring = False

    def status(self):
        calendar = self.service_manager.get_service("calendar")
        meeting = self.service_manager.get_service("meeting")
        return {
            "room_name": self.config.room.name,
            "service_error": self.start_error,
            "reconfiguring": self.reconfiguring,
            "screens": self.display.inventory() if self.display else [],
            "display_message": (
                self.display.readiness() if self.display else "Desktop display service unavailable"
            ),
            "calendar": {
                "configured": bool(self.config.calendar.microsoft_auth_mode),
                "last_sync": (
                    calendar.last_sync.isoformat() if calendar and calendar.last_sync else None
                ),
                "error": calendar.sync_error if calendar else None,
                "bookings": len(calendar.events) if calendar else 0,
            },
            "meeting": {
                "state": meeting.state.value if meeting else "unavailable",
                "platforms": meeting.get_available_platforms() if meeting else [],
            },
        }

    async def test_calendar(self):
        config = load_config(self.config_path)
        mapped = calendar_config(config)
        if not mapped:
            return {"ok": False, "message": "Save Microsoft 365 settings first."}
        calendar = CalendarService(mapped)
        try:
            if not await calendar.initialize():
                return {
                    "ok": False,
                    "message": "Microsoft sign-in failed. Check tenant, application ID and client secret.",
                }
            await calendar.refresh()
            return {
                "ok": True,
                "message": f"Calendar connected: {len(calendar.events)} bookings in the next seven days.",
            }
        except Exception:
            return {
                "ok": False,
                "message": "Calendar access failed. Check room mailbox, scoped calendar permissions and network.",
            }
        finally:
            await calendar.shutdown()

    async def join_calendar_event(self, event_id):
        if self.reconfiguring or self._joining:
            raise RuntimeError("The room is busy")
        if self.display:
            self.display.prepare_meeting()
        self._joining = True
        try:
            return await self.agent.join_calendar_event(event_id)
        finally:
            self._joining = False

    def identify_displays(self):
        if self.display:
            self.display.identify()
