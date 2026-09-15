"""Quiet, scalable room signage using the same live calendar as the controller."""

import base64
from datetime import datetime, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


def tv_snapshot(config, calendar, now=None, service_error=None):
    """Project trusted bookings into display text; do not claim free on stale data."""
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(config.room.timezone)
    local = now.astimezone(zone)
    synced = calendar.last_sync if calendar else None
    fresh = bool(
        synced
        and not calendar.sync_error
        and not service_error
        and timedelta(0)
        <= now - synced
        <= timedelta(seconds=max(300, config.calendar.sync_interval_seconds * 3))
    )
    configured = bool(config.calendar.microsoft_auth_mode or synced)
    events = sorted(
        [
            event
            for event in (calendar.events if calendar and fresh else [])
            if event.end_time > now
            and event.status != "cancelled"
            and event.response_status != "declined"
        ],
        key=lambda event: (event.start_time, event.end_time, event.id),
    )
    current = [event for event in events if event.start_time <= now]
    primary = events[0] if events else None

    def day(value):
        return "Today" if value.date() == local.date() else value.strftime("%a %d %b")

    def when(event):
        start, end = event.start_time.astimezone(zone), event.end_time.astimezone(zone)
        if event.is_all_day:
            return day(start) + " · All day"
        ending = (
            end.strftime("%H:%M") if start.date() == end.date() else end.strftime("%a %d %b %H:%M")
        )
        return f"{day(start)} · {start:%H:%M} – {ending}"

    def title(event):
        if config.display.hide_meeting_titles or getattr(event, "is_private", False):
            return "Reserved meeting"
        return " ".join((event.title or "Reserved meeting").split())

    if not fresh:
        state = "Check calendar" if configured else "Welcome in."
        detail = (
            "Availability is temporarily unknown."
            if configured
            else "Connect a room calendar in Room setup."
        )
        caption = "CALENDAR UNAVAILABLE" if configured else "YOUR ROOM, READY TO MEET"
    elif current:
        state = "Room booked"
        # Merge overlapping bookings so the displayed end does not promise an earlier gap.
        busy_until = max(event.end_time for event in current)
        for event in events:
            if event.start_time <= busy_until:
                busy_until = max(busy_until, event.end_time)
        detail = "Reserved until " + busy_until.astimezone(zone).strftime(
            "%H:%M" if busy_until.astimezone(zone).date() == local.date() else "%a %d %b %H:%M"
        )
        caption = "HAPPENING NOW"
    else:
        state = "Available"
        detail = "No bookings in the next seven days."
        if primary:
            start = primary.start_time.astimezone(zone)
            detail = "Free until " + (
                start.strftime("%H:%M")
                if start.date() == local.date()
                else start.strftime("%a %d %b %H:%M")
            )
        caption = "NEXT MEETING" if primary else "ROOM TO THINK"
    starts = ""
    if primary and not current:
        minutes = max(1, ceil((primary.start_time - now).total_seconds() / 60))
        starts = (
            f"In {minutes} min"
            if minutes < 60
            else (
                f"In {minutes // 60} hr {minutes % 60} min"
                if minutes < 1440
                else primary.start_time.astimezone(zone).strftime("%a %d %b")
            )
        )
    return {
        "clock": local.strftime("%H:%M"),
        "date": local.strftime("%A, %d %B %Y"),
        "state": state,
        "detail": detail,
        "fresh": fresh,
        "busy": bool(current),
        "caption": caption,
        "starts": starts,
        "title": (
            title(primary)
            if primary
            else ("Make yourself at home." if fresh else "Let's get connected.")
        ),
        "time": (
            when(primary)
            if primary
            else (
                "Your next booking will appear here."
                if fresh
                else "Check Room setup on the touchscreen."
            )
        ),
        "agenda": [
            (title(event), when(event), "NOW" if event.start_time <= now else "")
            for event in events[:4]
        ],
        "extra": max(0, len(events) - 4),
        "sync": (
            "Calendar up to date"
            if fresh
            else ("Waiting for calendar sync" if configured else "Calendar not connected")
        ),
    }


class RoomTV(QWidget):
    """A 1920×1080 design scaled by Qt for the selected TV, including 4K outputs."""

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.setWindowTitle("Croom TV")
        self._key = None
        self._logo_data = None
        self.logo = QPixmap()
        self.refresh()

    def refresh(self, now=None):
        config = self.runtime.config
        self.model = tv_snapshot(
            config,
            self.runtime.service_manager.get_service("calendar"),
            now,
            getattr(self.runtime, "start_error", None),
        )
        branding = config.display
        key = (
            repr(self.model),
            config.room.name,
            branding.brand_name,
            branding.accent_color,
            branding.welcome_message,
            branding.logo_data,
        )
        if key != self._key:
            self._key = key
            if branding.logo_data != self._logo_data:
                self._logo_data = branding.logo_data
                self.logo = QPixmap()
                try:
                    self.logo.loadFromData(
                        base64.b64decode(branding.logo_data.partition(",")[2], validate=True), "PNG"
                    )
                except (ValueError, TypeError):
                    pass
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b171c"))
        scale = min(self.width() / 1920, self.height() / 1080)
        p.translate((self.width() - 1920 * scale) / 2, (self.height() - 1080 * scale) / 2)
        p.scale(scale, scale)
        cfg, m = self.runtime.config, self.model
        accent = QColor(cfg.display.accent_color)
        if not accent.isValid():
            accent = QColor("#53d6c5")
        # Keep text legible even when the saved brand colour is nearly black.
        highlight = QColor.fromHsvF(
            accent.hsvHueF(), min(accent.hsvSaturationF(), 0.65), max(accent.valueF(), 0.8)
        )
        ink, muted = "#f3f7f6", "#a6babd"
        gradient = QLinearGradient(0, 1080, 1920, 0)
        gradient.setColorAt(0, QColor("#0b171c"))
        tint = QColor(accent)
        tint.setAlpha(35)
        gradient.setColorAt(1, tint)
        p.fillRect(QRectF(0, 0, 1920, 1080), gradient)
        p.setPen(QPen(QColor("#284047"), 1))
        for offset in (0, 65, 130):
            p.drawEllipse(QRectF(1370 + offset, -540 + offset, 1100, 1100))

        def text(x, y, w, h, value, size=24, color=ink, weight=QFont.Normal, align=Qt.AlignLeft):
            font = QFont("DejaVu Sans")
            font.setPixelSize(size)
            font.setWeight(weight)
            p.setFont(font)
            p.setPen(QColor(color))
            value = QFontMetricsF(font).elidedText(value, Qt.ElideRight, w)
            p.drawText(QRectF(x, y, w, h), align | Qt.AlignVCenter, value)

        def lines(x, y, w, value, size, maximum=2):
            font = QFont("DejaVu Sans")
            font.setPixelSize(size)
            font.setWeight(QFont.DemiBold)
            metrics = QFontMetricsF(font)
            words = value.split()
            for row in range(maximum):
                if not words:
                    break
                if row == maximum - 1:
                    line = " ".join(words)
                else:
                    line = words.pop(0)
                    while words and metrics.horizontalAdvance(line + " " + words[0]) <= w:
                        line += " " + words.pop(0)
                text(x, y + row * size * 1.3, w, size * 1.3, line, size, weight=QFont.DemiBold)

        brand_x = 88
        if not self.logo.isNull():
            logo_size = self.logo.size().scaled(240, 70, Qt.KeepAspectRatio)
            p.drawPixmap(
                QRectF(88, 75, logo_size.width(), logo_size.height()),
                self.logo,
                QRectF(self.logo.rect()),
            )
            brand_x += logo_size.width() + 24
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(88, 83, 52, 52), 15, 15)
            p.setPen(QPen(QColor("#102329"), 4))
            p.drawLine(105, 119, 105, 99)
            p.drawLine(105, 99, 124, 99)
            brand_x = 164
        text(
            brand_x,
            75,
            950 - brand_x,
            42,
            cfg.display.brand_name or cfg.room.name,
            28,
            weight=QFont.DemiBold,
        )
        text(brand_x, 117, 850, 28, "ROOM CALENDAR", 15, muted)
        text(1380, 56, 450, 72, m["clock"], 64, weight=QFont.Light, align=Qt.AlignRight)
        text(1290, 134, 540, 34, m["date"], 22, muted, align=Qt.AlignRight)
        p.setPen(QPen(QColor("#31474d"), 1))
        p.drawLine(88, 200, 1832, 200)
        text(88, 240, 1040, 112, cfg.room.name, 82, weight=QFont.DemiBold)
        status_color = "#f3c983" if m["busy"] or not m["fresh"] else highlight
        p.setBrush(QColor(status_color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(92, 398, 14, 14))
        text(126, 365, 970, 78, m["state"], 54, status_color, QFont.DemiBold)
        text(88, 452, 1010, 50, m["detail"], 27, muted)
        p.setPen(QPen(QColor("#355057"), 1))
        p.setBrush(QColor("#152a31"))
        p.drawRoundedRect(QRectF(88, 554, 1030, 316), 24, 24)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(QRectF(88, 584, 5, 256), 2, 2)
        text(128, 580, 540, 42, m["caption"], 17, highlight, QFont.DemiBold)
        text(700, 580, 377, 42, m["starts"], 22, muted, align=Qt.AlignRight)
        lines(128, 648, 944, m["title"], 42)
        text(128, 800, 944, 36, m["time"], 25, muted)

        text(1212, 255, 620, 52, "On the calendar", 32, weight=QFont.DemiBold)
        text(1212, 312, 620, 30, "NEXT SEVEN DAYS", 15, muted)
        if not m["agenda"]:
            text(
                1212,
                413,
                620,
                46,
                "A clear calendar." if m["fresh"] else "Bookings will appear here.",
                28,
            )
            text(
                1212,
                466,
                620,
                38,
                (
                    "Enjoy a little breathing room."
                    if m["fresh"]
                    else "Connect and sync in Room setup."
                ),
                21,
                muted,
            )
        for index, (title, time, current) in enumerate(m["agenda"]):
            y = 374 + index * 126
            text(1212, y, 540, 38, time, 20, highlight if current else muted)
            if current:
                text(1760, y, 72, 38, current, 14, highlight, QFont.DemiBold, Qt.AlignRight)
            text(1212, y + 40, 620, 45, title, 29, weight=QFont.DemiBold)
            p.setPen(QPen(QColor("#2a4148"), 1))
            p.drawLine(1212, y + 105, 1832, y + 105)
        if m["extra"]:
            text(1212, 887, 620, 30, f"+ {m['extra']} more on the touchscreen", 19, muted)
        p.setPen(QPen(QColor("#31474d"), 1))
        p.drawLine(88, 945, 1832, 945)
        text(88, 968, 1040, 42, cfg.display.welcome_message, 25, weight=QFont.Medium)
        text(88, 1013, 1120, 32, "Choose a meeting on the touchscreen to join.", 21, muted)
        text(1350, 988, 482, 32, m["sync"], 19, muted, align=Qt.AlignRight)
        p.end()
