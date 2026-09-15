"""Explicit touchscreen/TV roles, with a standby view on the selected TV."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from croom_ui.tv import RoomTV


class RoomDisplays:
    def __init__(self, runtime, controller, fullscreen=True):
        self.runtime = runtime
        self.controller = controller
        self.fullscreen = fullscreen
        self.tv = RoomTV(runtime)
        self.labels = []
        self._placement = None
        self._inventory_signature = None
        self._arranged_signature = None
        self.placement_error = None
        self.timer = QTimer(controller)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.apply()

    def inventory(self):
        results = []
        for screen in QApplication.screens():
            geometry = screen.geometry()
            results.append(
                {
                    "name": screen.name(),
                    "connected": True,
                    "width": geometry.width(),
                    "height": geometry.height(),
                    "x": geometry.x(),
                    "y": geometry.y(),
                }
            )
        names = {r["name"] for r in results}
        for path in Path("/sys/class/drm").glob("card*-*/status"):
            name = path.parent.name.split("-", 1)[1]
            if name not in names and name.startswith(("HDMI-", "DSI-", "DP-")):
                results.append({"name": name, "connected": False})
        return results

    def roles(self):
        screens = {screen.name(): screen for screen in QApplication.screens()}
        config = self.runtime.config.display
        # Autoselect a DSI control panel, never infer a TV from the only screen.
        controller = screens.get(config.controller_output)
        if not config.controller_output:
            controller = next((s for name, s in screens.items() if name.startswith("DSI")), None)
            controller = controller or QApplication.primaryScreen()
        tv = screens.get(config.meeting_output) if config.meeting_output else None
        return controller, tv

    def readiness(self):
        controller, tv = self.roles()
        config = self.runtime.config.display
        if not controller:
            return "Touchscreen not connected"
        if not config.meeting_output:
            return "Connect the TV, then select its display in Room setup"
        if not tv:
            return "TV not connected — room setup is still available"
        if tv == controller:
            return "Choose separate displays for the touchscreen and TV"
        a, b = controller.geometry(), tv.geometry()
        if a.intersects(b):
            return "Displays are mirrored. Extend the desktop before joining a meeting"
        if self.placement_error:
            return self.placement_error
        return None

    @staticmethod
    def place(widget, screen, fullscreen):
        widget.winId()
        widget.windowHandle().setScreen(screen)
        widget.setGeometry(screen.geometry())
        widget.showFullScreen() if fullscreen else widget.show()

    def apply(self):
        self.placement_error = None
        try:
            self._apply_compositor_rule()
        except Exception:
            self.placement_error = (
                "TV placement could not be configured. Check the desktop display settings"
            )
        self._placement = None
        self.refresh()

    def _apply_compositor_rule(self):
        # labwc controls placement of XWayland browser windows. Preserve all user
        # rules and add only our class-specific rule when a TV is selected.
        import os
        import shutil
        import subprocess
        import xml.etree.ElementTree as ET

        output = self.runtime.config.display.meeting_output
        config = Path.home() / ".config/labwc/rc.xml"
        if (
            output
            and os.environ.get("XDG_SESSION_TYPE") == "wayland"
            and shutil.which("labwc")
            and config.exists()
        ):
            from croom.setup.settings import atomic_write

            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            tree = ET.parse(config, parser=parser)
            root = tree.getroot()
            namespace = root.tag.partition("}")[0].lstrip("{") if "}" in root.tag else ""
            if namespace:
                ET.register_namespace("", namespace)
            tag = lambda name: "{" + namespace + "}" + name if namespace else name
            rules = root.find(tag("windowRules"))
            if rules is None:
                rules = ET.SubElement(root, tag("windowRules"))
            for rule in list(rules):
                if rule.get("identifier") == "croom-meeting":
                    rules.remove(rule)
            rule = ET.SubElement(rules, tag("windowRule"), identifier="croom-meeting")
            ET.SubElement(rule, tag("action"), name="MoveToOutput", output=output)
            atomic_write(config, ET.tostring(root, encoding="unicode"))
            subprocess.run(["labwc", "--reconfigure"], check=True, timeout=5, capture_output=True)

    def refresh(self):
        self.tv.refresh()
        controller, tv = self.roles()
        roles = (
            controller.name() if controller else None,
            tv.name() if tv else None,
            self.runtime.config.room.name,
        )
        signature = repr(self.inventory())
        # Selected roles imply an extended desktop: put the TV to the right of
        # the control panel. Only run on labwc/Wayland and on inventory changes.
        if controller and tv and controller != tv and signature != self._arranged_signature:
            self._arranged_signature = signature
            import os
            import shutil
            import subprocess

            if (
                controller.geometry().intersects(tv.geometry())
                and os.environ.get("XDG_SESSION_TYPE") == "wayland"
                and shutil.which("wlr-randr")
            ):
                try:
                    result = subprocess.run(
                        [
                            "wlr-randr",
                            "--output",
                            controller.name(),
                            "--pos",
                            "0,0",
                            "--output",
                            tv.name(),
                            "--pos",
                            f"{controller.geometry().width()},0",
                        ],
                        timeout=5,
                        capture_output=True,
                    )
                except (OSError, subprocess.SubprocessError):
                    return
                if result.returncode != 0:
                    # Readiness remains false if the compositor did not apply it.
                    return
        if roles != self._placement or signature != self._inventory_signature:
            self._placement, self._inventory_signature = roles, signature
            if controller:
                self.place(self.controller, controller, self.fullscreen)
            if tv and tv != controller:
                self.place(self.tv, tv, True)
                # Keep control focus on the touch panel; do not repeatedly raise either window.
                self.controller.activateWindow()
            else:
                self.tv.hide()

    def prepare_meeting(self):
        issue = self.readiness()
        if issue:
            raise RuntimeError(issue)
        _, screen = self.roles()
        meeting = self.runtime.service_manager.get_service("meeting")
        provider = meeting._providers.get("teams") if meeting else None
        if provider:
            geometry = screen.geometry()
            provider.set_window_bounds(
                {
                    "x": geometry.x(),
                    "y": geometry.y(),
                    "width": geometry.width(),
                    "height": geometry.height(),
                }
            )

    def identify(self):
        for label in self.labels:
            label.close()
        self.labels = []
        for screen in QApplication.screens():
            label = QLabel(screen.name())
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("background:#173252;color:white;font-size:64px;padding:30px;")
            label.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
            self.place(label, screen, True)
            QTimer.singleShot(4000, label.close)
            self.labels.append(label)

    def close(self):
        self.timer.stop()
        for label in self.labels:
            label.close()
        self.tv.hide()
