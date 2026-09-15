"""Bounded, local raster branding; never fetch a logo URL or read a supplied path."""

import base64
import binascii


def normalize_logo(value):
    if not isinstance(value, str) or len(value) > 350000:
        raise ValueError("Logo must be a PNG or JPEG under 256 KB")
    if not value:
        return ""
    prefix, separator, encoded = value.partition(",")
    if not separator or prefix not in ("data:image/png;base64", "data:image/jpeg;base64"):
        raise ValueError("Choose a PNG or JPEG logo")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid logo image") from None
    if len(data) > 262144:
        raise ValueError("Logo must be under 256 KB")

    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImageReader

    source = QBuffer()
    source.setData(QByteArray(data))
    source.open(QIODevice.ReadOnly)
    reader = QImageReader(source)
    size = reader.size()
    if bytes(reader.format()) not in (b"png", b"jpeg") or not (
        0 < size.width() <= 2048 and 0 < size.height() <= 2048
    ):
        raise ValueError("Logo must be a PNG or JPEG no larger than 2048 × 2048")
    reader.setAutoTransform(True)
    picture = reader.read()
    if picture.isNull():
        raise ValueError("Invalid logo image")
    if picture.width() > 480 or picture.height() > 120:
        picture = picture.scaled(480, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    target = QBuffer()
    target.open(QIODevice.WriteOnly)
    if not picture.save(target, "PNG"):
        raise ValueError("Could not save logo image")
    return "data:image/png;base64," + base64.b64encode(bytes(target.data())).decode("ascii")
