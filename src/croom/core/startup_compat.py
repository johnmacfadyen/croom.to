"""Compatibility bridge for the verified Croom 2.0.0.dev0 startup API.

The wrapped components retain their own state and public interfaces. Lifecycle
state belongs to the adapter, particularly for DisplayService.state.
"""

import json
import logging
import os
import stat
from pathlib import Path

from croom.core.service import Service

logger = logging.getLogger(__name__)


class ComponentService(Service):
    def __init__(self, name, component):
        super().__init__(name)
        self.component = component
        self._initialization_attempted = False

    def __getattr__(self, name):
        return getattr(self.component, name)

    async def start(self):
        self._initialization_attempted = True
        if not await self.component.initialize():
            raise RuntimeError(f"{self.name} initialization failed; see preceding logs")
        await self.component.start()

    async def stop(self):
        if self._initialization_attempted:
            try:
                await self.component.shutdown()
            finally:
                self._initialization_attempted = False


def audio_config(config):
    audio = config.audio
    ai_active = config.ai.enabled and not config.ai.privacy_mode
    return {
        "input_device": "default" if audio.input_device == "auto" else audio.input_device,
        "output_device": "default" if audio.output_device == "auto" else audio.output_device,
        "noise_reduction": ai_active
        and config.ai.noise_reduction
        and audio.noise_reduction_level != "off",
        "echo_cancellation": audio.echo_cancellation,
    }


def video_config(config):
    video = config.video
    return {
        "camera": "default" if video.device == "auto" else video.device,
        "resolution": {"720p": "1280x720", "1080p": "1920x1080", "4k": "3840x2160"}.get(
            video.resolution, video.resolution
        ),
        "fps": video.framerate,
    }


def display_config(config):
    backend = config.display.backend
    if backend not in ("auto", "hdmi_cec", "ddc", "none"):
        raise ValueError(f"Unsupported display backend: {backend}")
    return {
        "cec_enabled": backend in ("auto", "hdmi_cec"),
        "ddc_enabled": backend in ("auto", "ddc"),
    }


def calendar_config(config):
    calendar = config.calendar
    calendar.validate()
    if calendar.microsoft_auth_mode:
        path = Path(calendar.microsoft_credentials_path)
        # Check the opened file, not an earlier path stat. Reject symlinks and
        # group/world access; credentials are read as the runtime service user.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("Microsoft credentials must be a regular file with mode 0600 or 0400")
            try:
                data = json.load(stream)
            except (ValueError, UnicodeError):
                raise ValueError("Microsoft credential file must contain JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("client_secret"), str) or not data["client_secret"].strip():
            raise ValueError("Microsoft credential file requires client_secret")
        return {
            "provider": "microsoft",
            "credentials": {
                "tenant_id": calendar.microsoft_tenant_id,
                "client_id": calendar.microsoft_client_id,
                "room_mailbox": calendar.microsoft_room_mailbox,
                "auth_mode": calendar.microsoft_auth_mode,
                "client_secret": data["client_secret"],
            },
            "calendar_ids": ["default"],
            "poll_interval": calendar.sync_interval_seconds,
            "auto_join_minutes": config.meeting.join_early_minutes,
        }
    if not calendar.google_credentials_path or "google" not in calendar.providers:
        logger.warning("Calendar not started: no supported calendar credentials configured")
        return None

    # Read at runtime as the service user. Never log credential contents.
    path = Path(calendar.google_credentials_path)
    data = json.loads(path.read_text())
    if data.get("type") == "service_account":
        credentials = {"service_account_file": str(path)}
    elif data.get("type") == "authorized_user":
        credentials = {
            "oauth_token": {
                "access_token": data.get("token") or data.get("access_token"),
                "refresh_token": data.get("refresh_token"),
                "client_id": data.get("client_id"),
                "client_secret": data.get("client_secret"),
            }
        }
    else:
        raise ValueError(
            "Google credentials must be a service-account key or authorized-user token; "
            "an OAuth client configuration alone cannot authenticate a calendar"
        )
    return {
        "provider": "google",
        "credentials": credentials,
        "poll_interval": calendar.sync_interval_seconds,
        "auto_join_minutes": config.meeting.join_early_minutes,
    }


def check_supported_config(config):
    if config.dashboard.enabled and config.dashboard.url:
        raise RuntimeError(
            "A dashboard URL is configured. This build also has an incompatible "
            "dashboard client API and credential mapping; this standalone-agent "
            "startup hotfix does not repair dashboard enrollment."
        )
    config.calendar.validate()
    if config.meeting.join_policy != "manual":
        raise ValueError("Only manual meeting joining is supported")
