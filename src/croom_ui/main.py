"""Desktop entry point. The UI and agent share one asyncio/Qt event loop."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)
from qasync import QEventLoop

from croom.core.agent import CroomAgent


class RoomWindow(QWidget):
    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.closed = asyncio.Event()
        self.tasks = set()
        self._busy = False
        self._snapshot = None
        self._zone = ZoneInfo(agent.config.room.timezone)
        self.setWindowTitle(f"Croom — {agent.config.room.name}")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        title = QLabel(agent.config.room.name)
        title.setStyleSheet("font-size: 28px; font-weight: bold")
        title.setTextFormat(Qt.PlainText)
        layout.addWidget(title)
        self.status = QLabel("Starting room services…")
        layout.addWidget(self.status)
        self.bookings = QListWidget()
        self.bookings.setStyleSheet("QListWidget::item { padding: 18px; }")
        layout.addWidget(self.bookings)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.buttons = {}
        for name, label, handler in (
            ("refresh", "Refresh calendar", self.refresh),
            ("join", "Join selected booking", self.join),
            ("leave", "Leave meeting", self.leave),
            ("mute", "Mute / unmute", self.mute),
            ("camera", "Camera on / off", self.camera),
        ):
            button = QPushButton(label)
            button.setMinimumHeight(56)
            button.clicked.connect(lambda checked=False, fn=handler: self.submit(fn))
            controls.addWidget(button)
            self.buttons[name] = button
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(500)
        self.update_status()

    def submit(self, action):
        async def run():
            # Leaving remains available while waiting for admission.
            is_leave = action == self.leave
            if self._busy and not is_leave:
                return
            if not is_leave:
                self._busy = True
            self.message.setText("")
            self.update_status()
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.message.setText("Action failed. Check calendar status and the meeting browser, then retry.")
            finally:
                if not is_leave:
                    self._busy = False
                self.update_status()
        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def service(self, name):
        return self.agent.service_manager.get_service(name)

    def update_status(self):
        calendar, meeting = self.service("calendar"), self.service("meeting")
        ready = bool(calendar and meeting and calendar.is_running and meeting.is_running)
        fresh = bool(ready and calendar.last_sync and not calendar.sync_error)
        state = meeting.state.value if ready else "unavailable"
        self.buttons["refresh"].setEnabled(ready and not self._busy)
        self.buttons["join"].setEnabled(fresh and bool(meeting.get_available_platforms()) and not self._busy and state in ("idle", "error"))
        self.buttons["leave"].setEnabled(ready and state not in ("idle", "leaving"))
        for name in ("mute", "camera"):
            self.buttons[name].setEnabled(ready and state == "connected" and not self._busy)
        if not ready:
            self.status.setText("Room services are starting or unavailable.")
            return
        if calendar.sync_error:
            sync = calendar.sync_error
        elif calendar.last_sync:
            sync = "Calendar synced " + calendar.last_sync.astimezone(self._zone).strftime("%H:%M:%S")
        else:
            sync = "Calendar has not synced"
        if not meeting.get_available_platforms():
            state = "no meeting provider available"
        self.status.setText(f"{sync} · Meeting: {state}")
        current = meeting.current_meeting
        if current:
            self.buttons["mute"].setText("Unmute" if current.is_muted else "Mute")
            self.buttons["camera"].setText("Camera off" if current.is_camera_on else "Camera on")
        events = [e for e in calendar.events if e.end_time > datetime.now(timezone.utc)]
        snapshot = [e.to_dict() for e in events]
        if snapshot != self._snapshot:
            selected = self.bookings.currentItem()
            selected_id = selected.data(Qt.UserRole) if selected else None
            self.bookings.clear()
            for event in events:
                start = event.start_time.astimezone(self._zone).strftime("%a %d %b, %H:%M")
                end = event.end_time.astimezone(self._zone).strftime("%H:%M")
                suffix = "" if event.meeting_url else " · No meeting link"
                item = QListWidgetItem(f"{start}–{end}   {event.title}{suffix}")
                item.setData(Qt.UserRole, event.id)
                self.bookings.addItem(item)
                if event.id == selected_id:
                    self.bookings.setCurrentItem(item)
            self._snapshot = snapshot

    async def refresh(self):
        await self.service("calendar").refresh()

    async def join(self):
        item = self.bookings.currentItem()
        if item is None:
            self.message.setText("Select a booking first.")
            return
        await self.agent.join_calendar_event(item.data(Qt.UserRole))

    async def leave(self):
        await self.service("meeting").leave_meeting()

    async def mute(self):
        await self.service("meeting").toggle_mute()

    async def camera(self):
        await self.service("meeting").toggle_camera()

    def closeEvent(self, event):
        event.ignore()
        self.closed.set()


async def run_room(config_path, fullscreen=False):
    agent = CroomAgent(config_path)
    window = RoomWindow(agent)
    window.showFullScreen() if fullscreen else window.show()

    async def start():
        try:
            await agent.start()
        except Exception:
            window.message.setText("Room startup failed. Check configuration and agent logs.")
        else:
            window.closed.set()

    agent_task = asyncio.create_task(start())
    try:
        await window.closed.wait()
    finally:
        window.timer.stop()
        for task in list(window.tasks):
            task.cancel()
        await asyncio.gather(*window.tasks, return_exceptions=True)
        # Cancel startup too, so shutdown cannot race with service registration.
        agent_task.cancel()
        await asyncio.gather(agent_task, return_exceptions=True)
        await agent.stop()
        window.hide()


def main():
    parser = argparse.ArgumentParser(description="Croom room calendar and manual meeting controls")
    parser.add_argument("-c", "--config", required=True)
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    with QEventLoop(app) as loop:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_room(args.config, args.fullscreen))


if __name__ == "__main__":
    main()
