"""Desktop entry point. The UI and agent share one asyncio/Qt event loop."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qasync import QEventLoop


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
        self.resize(800, 480)
        self.setStyleSheet(
            "QWidget { background: #101b2b; color: #edf4ff; font-size: 16px; } QPushButton { background: #243b54; border: 1px solid #476381; border-radius: 7px; padding: 8px; } QPushButton:disabled { color: #7d8d9f; border-color: #2c3f55; } QListWidget { background: #17283c; border: 1px solid #30485f; border-radius: 7px; } QListWidget::item:selected { background: #245998; }"
        )
        layout = QVBoxLayout(self)
        self.title = title = QLabel(agent.config.room.name)
        title.setStyleSheet("font-size: 28px; font-weight: bold")
        title.setTextFormat(Qt.PlainText)
        header = QHBoxLayout()
        header.addWidget(title, 1)
        setup_button = QPushButton("Room setup")
        setup_button.setMinimumHeight(44)
        setup_button.clicked.connect(self.show_setup)
        header.addWidget(setup_button)
        layout.addLayout(header)
        self.status = QLabel("Starting room services…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:14px;color:#bfd0e2;")
        layout.addWidget(self.status)
        self.bookings = QListWidget()
        self.bookings.setStyleSheet("QListWidget::item { padding: 14px; }")
        layout.addWidget(self.bookings)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        self.message.setStyleSheet("font-size:14px;color:#f4ce87;")
        layout.addWidget(self.message)
        controls = QGridLayout()
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
            button.setMinimumHeight(48)
            button.clicked.connect(lambda checked=False, fn=handler: self.submit(fn))
            self.buttons[name] = button
        controls.addWidget(self.buttons["join"], 0, 0, 1, 2)
        controls.addWidget(self.buttons["leave"], 0, 2)
        controls.addWidget(self.buttons["mute"], 1, 0)
        controls.addWidget(self.buttons["camera"], 1, 1)
        controls.addWidget(self.buttons["refresh"], 1, 2)
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
                self.message.setText(
                    "Action failed. Check calendar status and the meeting browser, then retry."
                )
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
        self.title.setText(self.agent.config.room.name)
        self._zone = ZoneInfo(self.agent.config.room.timezone)
        ready = bool(meeting and meeting.is_running) and not getattr(
            self.agent, "reconfiguring", False
        )
        fresh = bool(
            ready
            and calendar
            and calendar.is_running
            and calendar.last_sync
            and not calendar.sync_error
        )
        state = meeting.state.value if ready else "unavailable"
        self.buttons["refresh"].setEnabled(
            bool(calendar and calendar.is_running) and not self._busy
        )
        display = getattr(self.agent, "display", None)
        display_issue = display.readiness() if display else None
        self.buttons["join"].setEnabled(
            fresh
            and not display_issue
            and bool(meeting.get_available_platforms())
            and not self._busy
            and state in ("idle", "error")
        )
        self.buttons["leave"].setEnabled(ready and state not in ("idle", "leaving"))
        for name in ("mute", "camera"):
            self.buttons[name].setEnabled(ready and state == "connected" and not self._busy)
        if not ready:
            self.status.setText(
                getattr(self.agent, "start_error", None)
                or "Starting room services. Room setup is available now."
            )
            self.bookings.clear()
            self._snapshot = None
            return
        if not calendar:
            sync = "Calendar not configured — tap Room setup"
        elif calendar.sync_error:
            sync = calendar.sync_error
        elif calendar.last_sync:
            sync = "Calendar synced " + calendar.last_sync.astimezone(self._zone).strftime(
                "%H:%M:%S"
            )
        else:
            sync = "Calendar has not synced"
        if not meeting.get_available_platforms():
            state = "no meeting provider available"
        self.status.setText(
            f"{sync} · Meeting: {state}" + ("\n" + display_issue if display_issue else "")
        )
        current = meeting.current_meeting
        if current:
            self.buttons["mute"].setText("Unmute" if current.is_muted else "Mute")
            self.buttons["camera"].setText("Camera off" if current.is_camera_on else "Camera on")
        events = (
            [e for e in calendar.events if e.end_time > datetime.now(timezone.utc)]
            if calendar
            else []
        )
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

    def show_setup(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Room setup")
        dialog.setMinimumWidth(min(650, self.width() - 40))
        layout = QVBoxLayout(dialog)
        server = getattr(self.agent, "setup_server", None)
        if server:
            import socket

            text = (
                "On your laptop, open:\n\n"
                f"https://{socket.gethostname()}.local:{server.port}\n\n"
                f"Setup password: {server.password}\n\n"
                "This Pi uses a local HTTPS certificate. Your browser will ask you to accept it.\n"
                "Choose the touchscreen and TV displays in Room setup. Meetings open on the TV."
            )
        else:
            text = "The setup server is not available. Check the room application log."
        label = QLabel(text)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        close = QPushButton("Back to room")
        close.setMinimumHeight(48)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.open()
        self._setup_dialog = dialog

    def closeEvent(self, event):
        event.ignore()
        self.closed.set()


async def run_room(config_path, fullscreen=False, state_dir=None, setup_port=3000):
    from pathlib import Path
    from croom.setup.server import SetupServer
    from croom_ui.runtime import RoomRuntime
    from croom_ui.displays import RoomDisplays

    state_dir = state_dir or Path.home() / ".local/state/croom"
    runtime = RoomRuntime(config_path, state_dir)
    window = RoomWindow(runtime)
    runtime.display = RoomDisplays(runtime, window, fullscreen)
    import signal

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, window.closed.set)
    server = SetupServer(runtime, state_dir, setup_port)
    runtime.setup_server = server
    try:
        await server.start()
        await runtime.start()
        await window.closed.wait()
    finally:
        window.timer.stop()
        for task in list(window.tasks):
            task.cancel()
        await asyncio.gather(*window.tasks, return_exceptions=True)
        await server.stop()
        await runtime.stop_agent()
        runtime.display.close()
        window.hide()


def main():
    parser = argparse.ArgumentParser(description="Croom room calendar and manual meeting controls")
    parser.add_argument("-c", "--config", required=True)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--state-dir", help="Protected setup state directory")
    parser.add_argument("--setup-port", type=int, default=3000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    with QEventLoop(app) as loop:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            run_room(args.config, args.fullscreen, args.state_dir, args.setup_port)
        )


if __name__ == "__main__":
    main()
