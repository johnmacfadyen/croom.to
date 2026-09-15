"""Allowlisted setup changes, written atomically without returning secrets."""

import json
import os
import re
from pathlib import Path
import secrets
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from croom.core.config import Config


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".croom-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class RoomSettings:
    def __init__(self, config_path, state_dir):
        self.path = Path(config_path)
        self.state_dir = Path(state_dir)

    def read(self):
        data = yaml.safe_load(self.path.read_text()) or {}
        cfg = Config.from_dict(data)
        return {
            "room_name": cfg.room.name,
            "timezone": cfg.room.timezone,
            "controller_output": cfg.display.controller_output,
            "meeting_output": cfg.display.meeting_output,
            "brand_name": cfg.display.brand_name,
            "accent_color": cfg.display.accent_color,
            "welcome_message": cfg.display.welcome_message,
            "logo_data": cfg.display.logo_data,
            "hide_meeting_titles": cfg.display.hide_meeting_titles,
            "camera_default_on": cfg.meeting.camera_default_on,
            "mic_default_on": cfg.meeting.mic_default_on,
            "calendar_enabled": bool(cfg.calendar.microsoft_auth_mode),
            "tenant_id": cfg.calendar.microsoft_tenant_id,
            "client_id": cfg.calendar.microsoft_client_id,
            "room_mailbox": cfg.calendar.microsoft_room_mailbox,
            "has_secret": bool(cfg.calendar.microsoft_credentials_path),
        }

    def save(self, values):
        if not isinstance(values, dict):
            raise ValueError("Expected settings object")
        allowed = set(self.read()) - {"has_secret"}
        if set(values) - allowed - {"client_secret"}:
            raise ValueError("Unknown setup setting")
        merged = self.read() | values
        for key in (
            "room_name",
            "timezone",
            "controller_output",
            "meeting_output",
            "tenant_id",
            "client_id",
            "room_mailbox",
            "brand_name",
            "welcome_message",
        ):
            if not isinstance(merged[key], str) or len(merged[key]) > 255:
                raise ValueError("Invalid room setting")
            merged[key] = merged[key].strip()
        if not merged["room_name"]:
            raise ValueError("Enter a room name")
        try:
            ZoneInfo(merged["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Enter a valid timezone, such as Australia/Melbourne") from None
        if not isinstance(merged["accent_color"], str) or not re.fullmatch(
            r"#[0-9a-fA-F]{6}", merged["accent_color"]
        ):
            raise ValueError("Choose a valid brand colour")
        from croom.setup.branding import normalize_logo

        merged["logo_data"] = normalize_logo(merged["logo_data"])
        for key in (
            "calendar_enabled",
            "camera_default_on",
            "mic_default_on",
            "hide_meeting_titles",
        ):
            if type(merged[key]) is not bool:
                raise ValueError("Invalid switch value")
        if merged["meeting_output"] and merged["meeting_output"] == merged["controller_output"]:
            raise ValueError("Select different displays for the touchscreen and TV")
        secret = values.get("client_secret", "")
        if not isinstance(secret, str) or len(secret) > 8192:
            raise ValueError("Invalid client secret")
        # Start with the actual YAML, preserving fields omitted by Config.to_dict.
        data = yaml.safe_load(self.path.read_text()) or {}
        data.setdefault("room", {}).update(name=merged["room_name"], timezone=merged["timezone"])
        data.setdefault("display", {}).update(
            **{
                key: merged[key]
                for key in (
                    "controller_output",
                    "meeting_output",
                    "brand_name",
                    "accent_color",
                    "welcome_message",
                    "logo_data",
                    "hide_meeting_titles",
                )
            }
        )
        data.setdefault("meeting", {}).update(
            join_policy="manual",
            camera_default_on=merged["camera_default_on"],
            mic_default_on=merged["mic_default_on"],
        )
        calendar = data.setdefault("calendar", {})
        new_secret = None
        if merged["calendar_enabled"]:
            calendar.update(
                providers=["microsoft"],
                microsoft_auth_mode="client_credentials",
                microsoft_tenant_id=merged["tenant_id"],
                microsoft_client_id=merged["client_id"],
                microsoft_room_mailbox=merged["room_mailbox"],
            )
            if secret:
                new_secret = self.state_dir / "credentials" / (secrets.token_hex(16) + ".json")
                calendar["microsoft_credentials_path"] = str(new_secret.resolve())
            elif not calendar.get("microsoft_credentials_path"):
                raise ValueError("Enter the Microsoft application client secret")
        else:
            calendar["providers"] = []
            for key in list(calendar):
                if key.startswith("microsoft_"):
                    calendar[key] = ""
        Config.from_dict(data).calendar.validate()
        try:
            if new_secret:
                atomic_write(new_secret, json.dumps({"client_secret": secret}))
            atomic_write(self.path, yaml.safe_dump(data, sort_keys=False))
        except Exception:
            if new_secret:
                new_secret.unlink(missing_ok=True)
            raise
        return self.read()
