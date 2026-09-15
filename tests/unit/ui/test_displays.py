import os
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QRect
from croom.core.config import Config
from croom_ui.displays import RoomDisplays


def display_pair():
    controller = SimpleNamespace(geometry=lambda: QRect(0, 0, 800, 480))
    tv = SimpleNamespace(geometry=lambda: QRect(800, 0, 1920, 1080))
    display = object.__new__(RoomDisplays)
    config = Config()
    config.display.meeting_output = "HDMI-A-1"
    provider = SimpleNamespace(set_window_bounds=MagicMock())
    display.runtime = SimpleNamespace(
        config=config,
        service_manager=SimpleNamespace(
            get_service=lambda name: SimpleNamespace(_providers={"teams": provider})
        ),
    )
    display.roles = lambda: (controller, tv)
    display.placement_error = None
    return display, controller, tv, provider


def test_meeting_bounds_come_from_selected_tv():
    display, _, _, provider = display_pair()
    assert display.readiness() is None
    display.prepare_meeting()
    provider.set_window_bounds.assert_called_once_with(
        {"x": 800, "y": 0, "width": 1920, "height": 1080}
    )


def test_missing_same_or_mirrored_tv_blocks_join():
    display, controller, tv, provider = display_pair()
    for pair in ((controller, None), (controller, controller)):
        display.roles = lambda: pair
        with pytest.raises(RuntimeError):
            display.prepare_meeting()
    display.roles = lambda: (controller, tv)
    tv.geometry = lambda: QRect(0, 0, 1920, 1080)
    with pytest.raises(RuntimeError, match="mirrored"):
        display.prepare_meeting()
    provider.set_window_bounds.assert_not_called()
