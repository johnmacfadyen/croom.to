"""HTTPS setup server. Every API requires a session; writes also require CSRF."""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import re
import socket
import ssl
import time

from aiohttp import web

from croom.setup.settings import atomic_write


def tls_context(state_dir):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    state = Path(state_dir)
    cert_path, key_path = state / "setup.crt", state / "setup.key"
    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Croom local setup")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName(socket.gethostname()),
                        x509.DNSName(socket.gethostname() + ".local"),
                    ]
                ),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        atomic_write(
            key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
        )
        atomic_write(cert_path, certificate.public_bytes(serialization.Encoding.PEM).decode())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_setup_password():
    """Eight random, unambiguous characters, grouped for reading from a screen."""
    code = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(8))
    return code[:4] + "-" + code[4:]


class SetupServer:
    def __init__(self, runtime, state_dir, port=3000):
        self.runtime = runtime
        self.state_dir = Path(state_dir)
        self.port = port
        self.runner = None
        self.sessions = {}
        self.attempts = defaultdict(list)
        password_path = self.state_dir / "setup-password"
        if not password_path.exists():
            atomic_write(password_path, generate_setup_password() + "\n")
        self.password = password_path.read_text().strip()
        self.short_password = bool(
            re.fullmatch(f"[{PASSWORD_ALPHABET}]{{4}}-[{PASSWORD_ALPHABET}]{{4}}", self.password)
        )
        if not self.short_password and len(self.password) < 16:
            raise ValueError(
                "Setup password must be a generated room code or at least 16 characters"
            )
        self._salt = secrets.token_bytes(16)
        self._password_hash = self._hash(self.password)
        self.app = web.Application(client_max_size=400000, middlewares=[self.boundary])
        self.app.add_routes(
            [
                web.get("/", self.index),
                web.get("/app.js", self.script),
                web.get("/style.css", self.style),
                web.post("/api/login", self.login),
                web.post("/api/logout", self.logout),
                web.get("/api/status", self.status),
                web.get("/api/settings", self.settings),
                web.post("/api/settings", self.save),
                web.post("/api/calendar/test", self.test_calendar),
                web.post("/api/displays/identify", self.identify),
            ]
        )

    def _hash(self, password):
        if self.short_password:
            password = "".join(password.split()).replace("-", "").upper()
        return hashlib.scrypt(password.encode(), salt=self._salt, n=16384, r=8, p=1)

    def _session(self, request):
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
        return self.sessions.get(request.cookies.get("croom_setup", ""))

    @web.middleware
    async def boundary(self, request, handler):
        if request.method not in ("GET", "HEAD"):
            # API calls must originate from this setup page, not another LAN site.
            if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
                return web.json_response({"error": "Open setup in this browser first"}, status=403)
        try:
            if request.path.startswith("/api/") and request.path != "/api/login":
                session = self._session(request)
                if not session:
                    raise web.HTTPUnauthorized()
                if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
                    request.headers.get("X-Croom-CSRF", ""), session["csrf"]
                ):
                    raise web.HTTPForbidden()
                request["session"] = session
            response = await handler(request)
        except web.HTTPException as error:
            response = web.json_response({"error": error.reason}, status=error.status)
        except (ValueError, TypeError, json.JSONDecodeError):
            response = web.json_response(
                {"error": "Invalid settings. Check all required fields and timezone."}, status=400
            )
        except Exception:
            # No exception text, bodies, credentials or meeting URLs in responses/logs.
            response = web.json_response(
                {"error": "Setup action failed. Existing settings were retained where possible."},
                status=500,
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    async def login(self, request):
        now = time.monotonic()
        # Global bound prevents an unbounded map and source-address cycling.
        attempts = [t for t in self.attempts["all"] if now - t < 60]
        self.attempts["all"] = attempts
        if len(attempts) >= 10:
            return web.json_response(
                {"error": "Too many attempts. Try again in a minute."}, status=429
            )
        attempts.append(now)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Expected an object")
        password = body.get("password", "")
        if not isinstance(password, str) or len(password) > 256:
            raise web.HTTPUnauthorized()
        candidate = await asyncio.to_thread(self._hash, password)
        if not hmac.compare_digest(candidate, self._password_hash):
            return web.json_response({"error": "Incorrect setup password"}, status=401)
        if len(self.sessions) >= 64:
            self.sessions.clear()
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self.sessions[session_id] = {"csrf": csrf, "expires": now + 8 * 3600}
        response = web.json_response({"csrf": csrf})
        response.set_cookie(
            "croom_setup",
            session_id,
            secure=True,
            httponly=True,
            samesite="Strict",
            max_age=8 * 3600,
        )
        return response

    async def logout(self, request):
        self.sessions.pop(request.cookies.get("croom_setup", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("croom_setup")
        return response

    async def status(self, request):
        return web.json_response(self.runtime.status() | {"csrf": request["session"]["csrf"]})

    async def settings(self, request):
        return web.json_response(self.runtime.settings.read())

    async def save(self, request):
        return web.json_response(await self.runtime.save(await request.json()))

    async def test_calendar(self, request):
        return web.json_response(await self.runtime.test_calendar())

    async def identify(self, request):
        self.runtime.identify_displays()
        return web.json_response({"ok": True})

    async def index(self, request):
        return web.FileResponse(Path(__file__).parent / "static" / "index.html")

    async def script(self, request):
        return web.FileResponse(Path(__file__).parent / "static" / "app.js")

    async def style(self, request):
        return web.FileResponse(Path(__file__).parent / "static" / "style.css")

    async def start(self):
        context = await asyncio.to_thread(tls_context, self.state_dir)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "0.0.0.0", self.port, ssl_context=context).start()
        except BaseException:
            await self.runner.cleanup()
            raise

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
