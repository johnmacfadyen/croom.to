from pathlib import Path
import json
from unittest.mock import patch

import pytest
import yaml

from croom.setup.settings import RoomSettings
from croom.core.config import Config


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "config.yaml"
    data = Config().to_dict()
    data["security"]["admin_pin"] = "preserve-local-pin"
    data["dashboard"]["enrollment_token"] = "preserve-local-token"
    path.write_text(yaml.safe_dump(data))
    return RoomSettings(path, tmp_path / "state")


def test_nonsecret_settings_preserve_unedited_fields(store):
    result = store.save(
        {
            "room_name": "Boardroom",
            "timezone": "Australia/Melbourne",
            "controller_output": "DSI-1",
            "meeting_output": "HDMI-A-1",
        }
    )
    data = yaml.safe_load(store.path.read_text())
    assert data["security"]["admin_pin"] == "preserve-local-pin"
    assert data["dashboard"]["enrollment_token"] == "preserve-local-token"
    assert result["room_name"] == "Boardroom"
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert "enrollment_token" not in json.dumps(result)


def test_secret_is_saved_protected_and_never_returned(store):
    data = {
        "calendar_enabled": True,
        "tenant_id": "tenant",
        "client_id": "client",
        "room_mailbox": "room@example.com",
        "client_secret": "fixture-super-secret",
    }
    result = store.save(data)
    assert result["has_secret"]
    assert "fixture-super-secret" not in json.dumps(result)
    assert "fixture-super-secret" not in store.path.read_text()
    config = Config.from_dict(yaml.safe_load(store.path.read_text()))
    credential = Path(config.calendar.microsoft_credentials_path)
    assert credential.stat().st_mode & 0o777 == 0o600
    assert json.loads(credential.read_text())["client_secret"] == data["client_secret"]
    store.save({"room_name": "Changed name", "client_secret": ""})
    assert Config.from_dict(
        yaml.safe_load(store.path.read_text())
    ).calendar.microsoft_credentials_path == str(credential)


@pytest.mark.parametrize(
    "data",
    [
        {"timezone": "Not/AZone"},
        {"room_name": ""},
        {"calendar_enabled": "yes"},
        {"controller_output": "DSI-1", "meeting_output": "DSI-1"},
        {"calendar_enabled": True},
        {"command": "whoami"},
        {"microsoft_credentials_path": "/etc/passwd"},
    ],
)
def test_bad_settings_leave_original_untouched(store, data):
    original = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save(data)
    assert store.path.read_bytes() == original


def test_configuration_commit_failure_keeps_old_settings_and_removes_new_secret(store):
    original = store.path.read_bytes()
    from croom.setup.settings import atomic_write

    def fail_config(path, content, mode=0o600):
        if path == store.path:
            raise OSError("disk full")
        return atomic_write(path, content, mode)

    with patch("croom.setup.settings.atomic_write", side_effect=fail_config):
        with pytest.raises(OSError):
            store.save(
                {
                    "calendar_enabled": True,
                    "tenant_id": "tenant",
                    "client_id": "client",
                    "room_mailbox": "room@example.com",
                    "client_secret": "fixture",
                }
            )
    assert store.path.read_bytes() == original
    assert not list((store.state_dir / "credentials").glob("*.json"))
