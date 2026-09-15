"""Startup regressions with real components and mocked hardware/cloud boundaries."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from croom.core.agent import CroomAgent
from croom.core.config import Config
from croom.core.service import ServiceManager, ServiceState
from croom.core.startup_compat import (
    ComponentService,
    audio_config,
    calendar_config,
    display_config,
    video_config,
)


def make_agent(config=None):
    agent = object.__new__(CroomAgent)
    agent.config = config or Config()
    agent.capabilities = None
    agent.service_manager = ServiceManager()
    agent._running = False
    return agent


def test_entry_point_and_public_imports():
    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    commands = [
        [sys.executable, "-Werror", "-m", "croom.core.agent", "--help"],
        [
            sys.executable,
            "-c",
            "from croom import CroomAgent; from croom.core import CroomAgent as Other; assert CroomAgent is Other",
        ],
    ]
    for command in commands:
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("integration", ["dashboard", "microsoft"])
def test_unsupported_integrations_fail_before_registration(integration):
    agent = make_agent()
    if integration == "dashboard":
        agent.config.dashboard.url = "wss://example.invalid"
    else:
        agent.config.calendar.microsoft_client_id = "test-client"
    with pytest.raises((RuntimeError, ValueError)):
        agent._initialize_services()
    assert agent.service_manager.get_all_services() == {}


@pytest.mark.asyncio
async def test_lifecycle_keeps_display_power_state_separate():
    calls = []

    class Device:
        state = "display-on"

        async def initialize(self):
            calls.append("initialize")
            return True

        async def start(self):
            calls.append("start")

        async def shutdown(self):
            calls.append("shutdown")

    device = Device()
    service = ComponentService("display", device)
    manager = ServiceManager()
    manager.register(service)
    assert await manager.start_all()
    assert calls == ["initialize", "start"]
    assert service.state == ServiceState.RUNNING
    assert device.state == "display-on"
    assert manager.get_status()["display"].state == ServiceState.RUNNING
    await manager.stop_all()
    assert calls == ["initialize", "start", "shutdown"]
    assert service.state == ServiceState.STOPPED


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["initialize_false", "initialize_exception", "start_exception"])
async def test_failure_cleans_partial_and_started_components(failure):
    good = SimpleNamespace(
        initialize=AsyncMock(return_value=True), start=AsyncMock(), shutdown=AsyncMock()
    )
    bad = SimpleNamespace(
        initialize=AsyncMock(return_value=failure != "initialize_false"),
        start=AsyncMock(),
        shutdown=AsyncMock(),
    )
    if failure == "initialize_exception":
        bad.initialize.side_effect = RuntimeError("device failure")
    if failure == "start_exception":
        bad.start.side_effect = RuntimeError("device failure")
    manager = ServiceManager()
    manager.register(ComponentService("good", good))
    manager.register(ComponentService("bad", bad), dependencies=["good"])
    assert not await manager.start_all()
    good.shutdown.assert_awaited_once()
    bad.shutdown.assert_awaited_once()
    if failure != "start_exception":
        bad.start.assert_not_awaited()


def test_device_configuration_translation():
    config = Config()
    assert audio_config(config)["input_device"] == "default"
    config.audio.input_device = "usb-mic"
    config.audio.noise_reduction_level = "off"
    assert audio_config(config)["input_device"] == "usb-mic"
    assert not audio_config(config)["noise_reduction"]
    config.audio.noise_reduction_level = "medium"
    config.ai.privacy_mode = True
    assert not audio_config(config)["noise_reduction"]
    config.video.device = "/dev/video2"
    config.video.resolution = "720p"
    config.video.framerate = 15
    assert video_config(config) == {"camera": "/dev/video2", "resolution": "1280x720", "fps": 15}
    config.display.backend = "none"
    assert display_config(config) == {"cec_enabled": False, "ddc_enabled": False}


def test_calendar_configuration_and_unsupported_credentials(tmp_path):
    config = Config()
    assert calendar_config(config) is None
    credential_path = tmp_path / "credentials.json"
    config.calendar.google_credentials_path = str(credential_path)
    credential_path.write_text('{"type":"service_account"}')
    assert calendar_config(config)["credentials"] == {"service_account_file": str(credential_path)}
    credential_path.write_text('{"type":"authorized_user", "token":"fixture-token"}')
    assert calendar_config(config)["credentials"]["oauth_token"]["access_token"] == "fixture-token"
    credential_path.write_text('{"installed":{}}')
    with pytest.raises(ValueError, match="OAuth client configuration alone"):
        calendar_config(config)


@pytest.mark.asyncio
async def test_real_services_initialize_start_report_and_shutdown():
    from croom.audio.service import AudioService
    from croom.video.service import VideoService
    from croom.display.service import DisplayService

    agent = make_agent()
    agent.config.ai.enabled = False
    agent.config.meeting.platforms = []
    agent.config.display.backend = "none"
    agent._initialize_services()
    services = agent.service_manager.get_all_services()
    assert set(services) == {"audio", "video", "display", "meeting"}
    assert isinstance(services["audio"].component, AudioService)
    assert isinstance(services["video"].component, VideoService)
    assert isinstance(services["display"].component, DisplayService)
    # Keep real initialize/start/shutdown methods; replace hardware discovery only.
    with (
        patch("croom.audio.service.get_audio_devices", return_value=[]),
        patch("croom.video.service.get_cameras", return_value=[]),
        patch.object(DisplayService, "_detect_displays", new=AsyncMock()),
    ):
        assert await agent.service_manager.start_all()
        try:
            assert services["audio"].component._pipeline is not None
            assert services["video"].component._pipeline is not None
            assert all(service.is_running for service in services.values())
            agent._running = True
            agent.platform_info = SimpleNamespace(
                device=SimpleNamespace(value="pc"), os_name="Test", arch="test", ai_accelerators=[]
            )
            agent.capabilities = SimpleNamespace(to_dict=lambda: {})
            assert isinstance(agent.get_status()["services"]["meeting"], dict)
        finally:
            await agent.service_manager.stop_all()
        assert services["audio"].component._pipeline is None
        assert services["video"].component._pipeline is None


@pytest.mark.asyncio
async def test_agent_propagates_start_failure():
    agent = make_agent()
    agent._setup_signal_handlers = lambda: None
    agent._initialize_services = lambda: None
    agent.service_manager = SimpleNamespace(
        start_all=AsyncMock(return_value=False), stop_all=AsyncMock()
    )
    with pytest.raises(RuntimeError, match="Failed to start all services"):
        await agent.start()
    assert not agent._running
    agent.service_manager.stop_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_google_calendar_lifecycle_with_mock_auth(tmp_path):
    from croom.calendar.service import CalendarService
    from croom.calendar.providers.google import GoogleCalendarProvider

    config = Config()
    credential_path = tmp_path / "credentials.json"
    credential_path.write_text('{"type":"service_account"}')
    config.calendar.google_credentials_path = str(credential_path)
    component = CalendarService(calendar_config(config))
    manager = ServiceManager()
    manager.register(ComponentService("calendar", component))
    with (
        patch.object(
            GoogleCalendarProvider, "authenticate", new=AsyncMock(return_value=True)
        ) as auth,
        patch.object(
            GoogleCalendarProvider,
            "get_calendars",
            new=AsyncMock(return_value=[{"id": "fixture", "primary": True}]),
        ),
        patch.object(
            GoogleCalendarProvider, "get_events", new=AsyncMock(return_value=[])
        ) as events,
    ):
        assert await manager.start_all()
        try:
            auth.assert_awaited_once_with({"service_account_file": str(credential_path)})
            events.assert_awaited_once()
            assert component._poll_task is not None
        finally:
            await manager.stop_all()
        assert component._poll_task is None
        assert component.provider is None


@pytest.mark.parametrize(
    "enabled,privacy,expect_ai", [(True, False, True), (True, True, False), (False, False, False)]
)
def test_ai_dependency_order_and_privacy_registration(enabled, privacy, expect_ai):
    agent = make_agent()
    agent.config.ai.enabled = enabled
    agent.config.ai.privacy_mode = privacy
    agent._initialize_services()
    assert (agent.service_manager.get_service("ai") is not None) == expect_ai
    if expect_ai:
        order = agent.service_manager._start_order
        assert order.index("ai") < order.index("audio")
        assert order.index("ai") < order.index("video")


def test_desktop_browser_media_does_not_open_raw_capture_services():
    agent = make_agent()
    agent.config.meeting.browser_media = True
    agent.config.ai.enabled = False
    agent._initialize_services()
    assert agent.service_manager.get_service('audio') is None
    assert agent.service_manager.get_service('video') is None
    assert agent.service_manager.get_service('meeting') is not None
