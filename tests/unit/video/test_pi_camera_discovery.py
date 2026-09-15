"""Pi processing nodes must never be opened as webcam capture devices."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from croom.video.camera import get_cameras, _is_pi_processing_device


@pytest.mark.parametrize("name", [
    "bcm2835-codec-decode", "bcm2835-codec-encode_image", "bcm2835-isp-capture1",
    "bcm2835-isp-output0", "bcm2835-isp-stats2", "rpi-hevc-dec",
])
def test_pi_processing_nodes_are_not_probed(name):
    cv = MagicMock()
    with patch("croom.video.camera.LIBCAMERA_AVAILABLE", False), patch(
        "croom.video.camera.OPENCV_AVAILABLE", True
    ), patch("croom.video.camera.cv2", cv, create=True), patch.object(
        Path, "glob", return_value=[Path("/dev/video22")]
    ), patch.object(Path, "read_text", return_value=name):
        assert get_cameras() == []
        cv.VideoCapture.assert_not_called()


@pytest.mark.parametrize("name", ["USB Camera", "HD Pro Webcam C920", "unicam"])
def test_camera_nodes_are_not_excluded(name):
    with patch.object(Path, "read_text", return_value=name):
        assert not _is_pi_processing_device(Path("/dev/video0"))


def test_missing_sysfs_keeps_existing_discovery():
    with patch.object(Path, "read_text", side_effect=FileNotFoundError):
        assert not _is_pi_processing_device(Path("/dev/video0"))
