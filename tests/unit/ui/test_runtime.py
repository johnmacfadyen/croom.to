"""Desktop reconfiguration and display gating at service boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from croom.core.config import Config, load_config
from croom_ui.runtime import RoomRuntime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("room:\n  name: Before\nai:\n  enabled: false\n")

    def make_agent(self):
        async def run():
            await asyncio.Event().wait()

        return SimpleNamespace(
            config=load_config(path),
            start=run,
            stop=AsyncMock(),
            service_manager=SimpleNamespace(get_service=lambda name: None),
            join_calendar_event=AsyncMock(),
        )

    monkeypatch.setattr(RoomRuntime, "make_agent", make_agent)
    return RoomRuntime(path, tmp_path / "state")


@pytest.mark.asyncio
async def test_save_replaces_agent_and_stops_previous(runtime):
    old = runtime.agent
    await runtime.start()
    result = await runtime.save({"room_name": "After"})
    assert result["settings"]["room_name"] == "After"
    assert runtime.config.room.name == "After"
    assert runtime.agent is not old
    old.stop.assert_awaited_once()
    assert not runtime.reconfiguring
    await runtime.stop_agent()


@pytest.mark.asyncio
async def test_invalid_save_preserves_running_agent(runtime):
    old = runtime.agent
    await runtime.start()
    with pytest.raises(ValueError):
        await runtime.save({"timezone": "invalid"})
    assert runtime.agent is old
    old.stop.assert_not_awaited()
    assert not runtime.reconfiguring
    await runtime.stop_agent()


@pytest.mark.asyncio
async def test_start_failure_can_be_recovered_by_setup(runtime):
    runtime.agent.start = AsyncMock(side_effect=RuntimeError("private failure"))
    await runtime.start()
    await runtime.agent_task
    assert runtime.start_error and "private" not in runtime.start_error
    await runtime.save({"room_name": "Recovered"})
    assert runtime.start_error is None
    await runtime.stop_agent()


@pytest.mark.asyncio
async def test_reconfigure_blocked_during_join_and_missing_tv_blocks_join(runtime):
    runtime._joining = True
    with pytest.raises(ValueError):
        await runtime.save({"room_name": "After"})
    runtime._joining = False

    def missing():
        raise RuntimeError("TV not connected")

    runtime.display = SimpleNamespace(prepare_meeting=missing)
    with pytest.raises(RuntimeError, match="TV not connected"):
        await runtime.join_calendar_event("booking")
    runtime.agent.join_calendar_event.assert_not_awaited()
