from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer
import pytest

from croom.setup.server import SetupServer


@pytest.fixture
async def client(tmp_path):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(read=lambda: {"room_name": "Fixture", "has_secret": True}),
        status=lambda: {"room_name": "Fixture"},
        save=AsyncMock(return_value={"message": "Saved"}),
        test_calendar=AsyncMock(return_value={"ok": True}),
        identify_displays=MagicMock(),
    )
    setup = SetupServer(runtime, tmp_path)
    test = TestClient(TestServer(setup.app))
    await test.start_server()
    yield test, setup, runtime
    await test.close()


async def login(client):
    test, setup, _ = client
    origin = str(test.make_url("/")).rstrip("/")
    response = await test.post(
        "/api/login", json={"password": setup.password}, headers={"Origin": origin}
    )
    assert response.status == 200
    data = await response.json()
    cookie = response.cookies["croom_setup"]
    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
    return {"Origin": origin, "Cookie": "croom_setup=" + cookie.value, "X-Croom-CSRF": data["csrf"]}


@pytest.mark.asyncio
async def test_every_api_requires_login(client):
    test, _, runtime = client
    for path in ("status", "settings"):
        assert (await test.get("/api/" + path)).status == 401
    for path in ("settings", "calendar/test", "displays/identify", "logout"):
        response = await test.post(
            "/api/" + path, json={}, headers={"Origin": str(test.make_url("/")).rstrip("/")}
        )
        assert response.status == 401
    runtime.save.assert_not_awaited()
    runtime.test_calendar.assert_not_awaited()


@pytest.mark.asyncio
async def test_authenticated_write_requires_origin_and_csrf(client):
    test, setup, runtime = client
    headers = await login(client)
    for changed in ({"Origin": "https://evil.example"}, {"X-Croom-CSRF": "incorrect"}):
        assert (await test.post("/api/settings", json={}, headers=headers | changed)).status == 403
    runtime.save.assert_not_awaited()
    assert (
        await test.post("/api/settings", json={"room_name": "New"}, headers=headers)
    ).status == 200
    runtime.save.assert_awaited_once_with({"room_name": "New"})
    response = await test.get("/api/settings", headers=headers)
    assert setup.password not in await response.text()


@pytest.mark.asyncio
async def test_logout_and_expiry(client):
    test, setup, _ = client
    headers = await login(client)
    assert (await test.post("/api/logout", json={}, headers=headers)).status == 200
    assert (await test.get("/api/settings", headers=headers)).status == 401
    headers = await login(client)
    for session in setup.sessions.values():
        session["expires"] = 0
    assert (await test.get("/api/settings", headers=headers)).status == 401


@pytest.mark.asyncio
async def test_login_throttling_and_secret_redaction(client):
    test, setup, runtime = client
    origin = str(test.make_url("/")).rstrip("/")
    for _ in range(10):
        assert (
            await test.post("/api/login", json={"password": "wrong"}, headers={"Origin": origin})
        ).status == 401
    assert (
        await test.post("/api/login", json={"password": setup.password}, headers={"Origin": origin})
    ).status == 429


@pytest.mark.asyncio
async def test_no_filesystem_fallback_and_no_fake_action(client):
    test, setup, runtime = client
    headers = await login(client)
    assert (await test.get("/api/settings/extra", headers=headers)).status == 404
    assert (await test.get("/etc/passwd")).status == 404
    runtime.save.side_effect = RuntimeError("fixture-private-secret")
    response = await test.post("/api/settings", json={}, headers=headers)
    assert response.status == 500
    assert "fixture-private-secret" not in await response.text()
    page = await test.get("/")
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
