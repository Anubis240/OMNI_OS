"""Omni-OS Remote Dashboard — a phone on the same network can chat with Omni,
speak to it and hear it, open files Omni shares, and use the trader view.

Pairing: the desktop shows a QR code and a 6-character code. Only the newest
code works, once, for ten minutes. Redeeming it opens a session whose token
rides on every later request. Deliberately small: no uploads from the phone,
no firewall changes.

Routes (dashboard/pages.py is the only client):
  GET  /                       the phone app
  GET  /pair                   code-entry page
  POST /pair {code}            → {token}
  GET  /pair/scan?code=…       QR landing page: keeps the token, opens /
  POST /api/say {text}         a typed message for Omni
  GET  /api/trader/state       trader snapshot
  POST /api/trader/action      → {pending, jobId}; then GET /api/trader/action/result?jobId=
  WS   /socket/chat?token=…    JSON: backlog, then live messages; heartbeat ping ↔ pong
  WS   /socket/voice?token=…   binary: phone mic in (PCM16, 16 kHz), Omni's voice out (PCM16, 24 kHz)
  GET  /f/<id>                 a file Omni chose to share

Message types sent to the phone: you, seraph (Omni's words), notice, image,
link, ping.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import string
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.app_paths import get_resource_dir
from dashboard import net
from dashboard.pages import APP_HTML, LOGIN_HTML

try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    import uvicorn
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

regenerate_certificate = net.regenerate_certificate   # used by the Settings panel

DEFAULT_PORT = 8000   # configurable: the older Seraph Guardian app also defaults to 8000
PING_EVERY = 15       # seconds
SILENT_LIMIT = 35     # seconds without any message before a phone counts as gone
CODE_LIFETIME = 600
BACKLOG = 100         # messages kept for a phone that (re)connects
REPLAYED = 50         # of those, how many a newly opened chat socket is sent
MIC_BACKLOG = 200     # phone mic frames waiting for the voice session
CLOSE_NOT_PAIRED = 4401
CLOSE_WENT_QUIET = 4408
_CODE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "OIL01")


def notice(text: str) -> dict:
    return {"type": "notice", "text": text}


class PairingCode:
    """At most one code is valid at a time — issuing a new one revokes the
    last (GEMZ4US Item G) — and each works once."""

    def __init__(self):
        self._code: str | None = None
        self._until = 0.0

    def issue(self, lifetime: float = CODE_LIFETIME) -> str:
        self._code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        self._until = time.time() + lifetime
        return self._code

    def pending(self) -> str | None:
        return self._code if self._code and time.time() < self._until else None

    def redeem(self, attempt: str) -> bool:
        current = self.pending()
        if current and secrets.compare_digest(attempt.strip().upper(), current):
            self._code = None
            return True
        return False


class Phones:
    """Connected chat sockets. drop() is the only way one leaves, and the
    'last phone gone' callback fires exactly once however many paths notice
    the same disconnect (heartbeat, failed send, handler exit)."""

    def __init__(self):
        self.sockets: set = set()
        self.on_last_left: Callable[[], None] | None = None

    def drop(self, sock) -> None:
        if sock in self.sockets:
            self.sockets.discard(sock)
            if not self.sockets and self.on_last_left:
                try:
                    self.on_last_left()
                except Exception:
                    pass

    async def send_all(self, message: dict) -> None:
        for sock in list(self.sockets):
            try:
                await sock.send_json(message)
            except Exception:
                self.drop(sock)


@dataclass
class LinkHooks:
    """What the desktop wants to hear about, and how it serves the trader view.
    Every field is optional."""
    phone_joined: Callable[[], None] | None = None
    phone_left: Callable[[], None] | None = None
    failed: Callable[[str], None] | None = None       # the server could not start
    warning: Callable[[str], None] | None = None      # a problem that didn't stop it
    trader_state: Callable[[], dict | None] | None = None
    trader_action: Callable[[str, dict], dict] | None = None


class PhoneLink:
    def __init__(self, hooks: LinkHooks | None = None):
        self.hooks = hooks or LinkHooks()
        self.ip = net.lan_address()
        self.port = self._configured_port()
        self.codes = PairingCode()
        self.phones = Phones()
        self.phones.on_last_left = lambda: self.hooks.phone_left and self.hooks.phone_left()
        self.inbox: asyncio.Queue[str] = asyncio.Queue()                    # typed on the phone
        self.mic_frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MIC_BACKLOG)
        self.running = False
        self.start_error: str | None = None
        self._sessions: set[str] = set()
        self._voice_sockets: set = set()
        self._backlog: deque[dict] = deque(maxlen=BACKLOG)
        self._shared: dict[str, Path] = {}
        self._jobs: dict[str, dict] = {}
        self._https = False

    @staticmethod
    def _configured_port() -> int:
        from core import settings_store
        try:
            port = int(settings_store.load_settings().get("dashboard_port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_PORT
        return port if 0 < port < 65536 else DEFAULT_PORT

    # --- what the desktop calls -----------------------------------------------------
    @property
    def base_url(self) -> str:
        return f"{'https' if self._https else 'http'}://{self.ip}:{self.port}"

    def issue_pairing_code(self) -> str:
        return self.codes.issue()

    def share(self, path) -> str | None:
        """A long random link to one local file. The randomness is the access
        control, so the link also works before any phone has paired."""
        path = Path(path)
        if not path.is_file():
            return None
        file_id = secrets.token_urlsafe(16)
        self._shared[file_id] = path
        return f"{self.base_url}/f/{file_id}"

    async def publish(self, message: dict) -> None:
        """Send to every paired phone, and keep it for phones that join later."""
        self._backlog.append(message)
        await self.phones.send_all(message)

    async def play_on_phone(self, pcm: bytes) -> None:
        for sock in list(self._voice_sockets):
            try:
                await sock.send_bytes(pcm)
            except Exception:
                self._voice_sockets.discard(sock)

    def _report(self, hook: Callable[[str], None] | None, message: str) -> None:
        if hook:
            try:
                hook(message)
            except Exception:
                pass

    # --- sessions -------------------------------------------------------------
    def _open_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions.add(token)
        return token

    def _paired(self, token: str | None) -> bool:
        return bool(token) and token.strip() in self._sessions

    def _request_paired(self, req: "Request") -> bool:
        return self._paired(req.headers.get("authorization", "").removeprefix("Bearer "))

    async def _admit(self, sock: "WebSocket", token: str) -> bool:
        """Accept a socket from a paired phone; turn anyone else away."""
        if not self._paired(token):
            await sock.close(code=CLOSE_NOT_PAIRED)
            return False
        await sock.accept()
        return True

    # --- the two sockets ---------------------------------------------------------
    async def _chat_session(self, sock: "WebSocket") -> None:
        self.phones.sockets.add(sock)
        # The phone counts as connected once this socket is open — not at
        # pairing — because this is what replies actually travel over.
        if self.hooks.phone_joined:
            self.hooks.phone_joined()
        for earlier in list(self._backlog)[-REPLAYED:]:
            try:
                await sock.send_json(earlier)
            except Exception:
                break
        last_heard = [time.monotonic()]
        heartbeat = asyncio.create_task(self._heartbeat(sock, last_heard))
        try:
            while True:
                message = await sock.receive_json()
                last_heard[0] = time.monotonic()
                if message.get("type") == "mic_dropped":
                    self._report(self.hooks.warning,
                                 "Phone mic buffer overflowed while reconnecting — some audio was dropped.")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            heartbeat.cancel()
            self.phones.drop(sock)

    async def _heartbeat(self, sock: "WebSocket", last_heard: list[float]) -> None:
        """Ping every PING_EVERY seconds; a phone silent for SILENT_LIMIT is
        dropped here, since a half-open connection never raises on its own."""
        while time.monotonic() - last_heard[0] <= SILENT_LIMIT:
            await asyncio.sleep(PING_EVERY)
            try:
                await sock.send_json({"type": "ping"})
            except Exception:
                break
        self.phones.drop(sock)
        try:
            await sock.close(code=CLOSE_WENT_QUIET)
        except Exception:
            pass

    async def _voice_session(self, sock: "WebSocket") -> None:
        self._voice_sockets.add(sock)
        await self.publish(notice("Phone speaker and mic ready."))
        spilling = False
        try:
            async for frame in sock.iter_bytes():
                queued = self._queue_mic(frame)
                if not queued and not spilling:   # say it once per episode, not per frame
                    self._report(self.hooks.warning, "Phone audio queue full — dropping frames.")
                spilling = not queued
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._voice_sockets.discard(sock)
            await self.publish(notice("Phone speaker and mic closed."))

    def _queue_mic(self, frame: bytes) -> bool:
        try:
            self.mic_frames.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            return False

    # --- trader jobs ----------------------------------------------------------
    def _start_trader_job(self, action: str, payload: dict) -> str:
        """A trade can wait minutes on-chain, so it runs on a thread and the
        phone polls for the outcome by job id."""
        job_id = secrets.token_urlsafe(12)
        self._jobs[job_id] = {"status": "pending"}

        def work():
            try:
                outcome = self.hooks.trader_action(action, payload)
            except Exception as err:
                outcome = {"ok": False, "message": str(err)}
            self._jobs[job_id] = {"status": "done", "result": outcome}

        threading.Thread(target=work, name="trader-job", daemon=True).start()
        return job_id

    # --- routes ---------------------------------------------------------------
    def _routes(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        backdrop_file = get_resource_dir() / "dashboard" / "static" / "avatar_bg.jpg"
        not_paired = lambda: JSONResponse({"error": "not paired"}, status_code=401)  # noqa: E731
        landing = ("<body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                   "background:#000;color:#f0f0f0;font-family:sans-serif;text-align:center'>{}</body>")

        @app.get("/")
        async def phone_app():
            from core import settings_store
            enabled = settings_store.load_settings()["trader"]["enabled"]
            return HTMLResponse(APP_HTML.replace("__TRADER_ENABLED__", "true" if enabled else "false"))

        @app.get("/pair")
        async def code_entry():
            return HTMLResponse(LOGIN_HTML)

        @app.post("/pair")
        async def pair_with_code(req: Request):
            code = str((await req.json()).get("code", ""))
            if not self.codes.redeem(code):
                return JSONResponse({"ok": False, "error": "That code is wrong or has expired."}, status_code=401)
            await self.publish(notice("Phone paired with the code."))
            return JSONResponse({"ok": True, "token": self._open_session()})

        @app.get("/pair/scan")
        async def pair_with_qr(code: str = ""):
            if not self.codes.redeem(code):
                return HTMLResponse(landing.format(
                    "<div><h2 style='color:#ef476f'>This code has expired</h2>"
                    "<p style='color:#9e9e9e'>Press Remote Control in Omni-OS for a new one.</p></div>"))
            token = self._open_session()
            await self.publish(notice("Phone paired by scanning the QR code."))
            return HTMLResponse(landing.format(f"<script>sessionStorage.setItem('omni_session','{token}');"
                                               "location.replace('/');</script><p>Connecting…</p>"))

        @app.get("/static/avatar_bg.jpg")
        async def backdrop():
            if backdrop_file.is_file():
                return FileResponse(backdrop_file, media_type="image/jpeg")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.get("/f/{file_id}")
        async def shared_file(file_id: str):
            path = self._shared.get(file_id)
            if path is None or not path.is_file():
                return JSONResponse({"error": "Not found or expired"}, status_code=404)
            return FileResponse(path)   # shown in the browser, not forced to download

        @app.post("/api/say")
        async def say(req: Request):
            if not self._request_paired(req):
                return not_paired()
            text = ((await req.json()).get("text") or "").strip()
            if text:
                await self.inbox.put(text)
            return JSONResponse({"ok": True})

        @app.get("/api/trader/state")
        async def trader_state(req: Request):
            if not self._request_paired(req):
                return not_paired()
            if self.hooks.trader_state is None:
                return JSONResponse({"open": False})
            try:
                # Can hit the network on a cache miss — never on the shared event loop.
                state = await asyncio.to_thread(self.hooks.trader_state)
            except Exception as err:
                return JSONResponse({"open": False, "error": str(err)})
            return JSONResponse({"open": True, **state} if state else {"open": False})

        @app.post("/api/trader/action")
        async def trader_action(req: Request):
            if not self._request_paired(req):
                return not_paired()
            if self.hooks.trader_action is None:
                return JSONResponse({"ok": False, "message": "trader not available"}, status_code=503)
            payload = await req.json()
            action = (payload.pop("action", "") or "").strip()
            if not action:
                return JSONResponse({"ok": False, "message": "missing action"}, status_code=400)
            return JSONResponse({"pending": True, "jobId": self._start_trader_job(action, payload)})

        @app.get("/api/trader/action/result")
        async def trader_result(req: Request, jobId: str = ""):
            if not self._request_paired(req):
                return not_paired()
            job = self._jobs.get(jobId)
            if job is None:
                return JSONResponse({"status": "unknown"}, status_code=404)
            if job["status"] == "done":
                self._jobs.pop(jobId, None)   # collected once, then forgotten
            return JSONResponse(job)

        @app.websocket("/socket/chat")
        async def chat_socket(sock: WebSocket, token: str = ""):
            if await self._admit(sock, token):
                await self._chat_session(sock)

        @app.websocket("/socket/voice")
        async def voice_socket(sock: WebSocket, token: str = ""):
            if await self._admit(sock, token):
                await self._voice_session(sock)

        return app

    # --- running ---------------------------------------------------------------
    def _could_not_start(self, message: str) -> None:
        self.running = False
        self.start_error = message
        print(f"[dashboard] {message}")
        self._report(self.hooks.failed, message)

    def _port_is_free(self) -> bool:
        """uvicorn reports a bind failure as a bare exit with no reason, so
        try the port first and say what's wrong."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as trial:
                trial.bind(("0.0.0.0", self.port))
            return True
        except OSError as err:
            self._could_not_start(
                f"couldn't use port {self.port} ({err.strerror or err}) — another copy of Omni-OS or another app "
                "(e.g. Seraph Guardian) may be using it. Close it or pick a different port in Settings → Remote Dashboard.")
            return False

    async def run(self) -> None:
        if not _AVAILABLE:
            self._could_not_start("the web server packages are missing (fastapi, uvicorn[standard]) — "
                                  "reinstall Omni-OS or pip-install them")
            return
        if net.is_cgnat(self.ip):
            self._report(self.hooks.warning,
                         f"is using {self.ip}, which looks like a VPN (e.g. Tailscale) address rather than your "
                         "Wi-Fi — a phone on the Wi-Fi can't reach it. Disconnect the VPN, then fully quit and "
                         "reopen Omni-OS (the address is only chosen at startup).")
        tls = {}
        cert = net.ensure_certificate(self.ip)
        if cert:
            self._https = True
            tls = {"ssl_certfile": str(cert[0]), "ssl_keyfile": str(cert[1])}
        else:
            print("[dashboard] no 'cryptography' package — serving plain HTTP; the phone mic needs HTTPS.")
        if not self._port_is_free():
            return

        config = uvicorn.Config(self._routes(), host="0.0.0.0", port=self.port, log_level="warning", **tls)
        print(f"[dashboard] {self.base_url}")
        try:
            self.running = True
            await uvicorn.Server(config).serve()
        except asyncio.CancelledError:
            raise
        except BaseException as err:   # includes uvicorn's SystemExit; must not kill the shared loop
            self._could_not_start(f"failed to start ({err})")
        finally:
            self.running = False
