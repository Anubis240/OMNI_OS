"""Omni-OS Remote Dashboard — lets a phone on the same network chat with
Omni, talk to it, hear it, open shared files and use the trader view.

Pairing: the desktop shows a QR code / 6-character key (only the newest key
works, once, for ten minutes). Pairing hands the phone a session token that
every later request must carry. Deliberately small: no file upload, no
firewall changes.

Wire protocol (matches the JavaScript in dashboard/pages.py):
  POST /login {pin}            → {ok, token}
  GET  /auto-login?key=…       → stores the token, redirects to /
  POST /api/command {text}     → a typed message for Omni
  GET  /api/trader/state       → trader snapshot
  POST /api/trader/action      → {pending, jobId}; poll /api/trader/action/result?jobId=
  WS   /ws?token=…             → JSON: history + live messages; {type:"ping"} ↔ {type:"pong"}
  WS   /ws/audio?token=…       → binary: 16 kHz PCM in, 24 kHz PCM out
  GET  /f/<id>                 → a file the desktop chose to share
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import string
import threading
import time
from pathlib import Path

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
KEY_LIFETIME = 600
HISTORY_KEPT = 100
_KEY_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "OIL01")


class PairingKey:
    """At most one key is valid at a time — issuing a new one revokes the
    last (GEMZ4US Item G) — and each works once."""

    def __init__(self):
        self._key: str | None = None
        self._until = 0.0

    def issue(self, lifetime: float = KEY_LIFETIME) -> str:
        self._key = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(6))
        self._until = time.time() + lifetime
        return self._key

    def pending(self) -> str | None:
        return self._key if self._key and time.time() < self._until else None

    def redeem(self, attempt: str) -> bool:
        current = self.pending()
        if current and secrets.compare_digest(attempt.strip().upper(), current):
            self._key = None
            return True
        return False


class Phones:
    """Connected chat sockets. drop() is the only way one leaves, and the
    'last phone gone' callback fires exactly once however many paths notice
    the same disconnect (heartbeat, failed send, handler exit)."""

    def __init__(self):
        self.sockets: set = set()
        self.on_last_left = None

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


class DashboardServer:
    def __init__(self):
        self.ip = net.lan_address()
        self.port = self._configured_port()
        self.keys = PairingKey()
        self.phones = Phones()
        self.commands: asyncio.Queue = asyncio.Queue()
        self.phone_audio: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.running = False
        self.start_error: str | None = None
        self._tokens: set[str] = set()
        self._listeners: set = set()             # /ws/audio sockets
        self._history: list[dict] = []
        self._shared: dict[str, Path] = {}
        self._jobs: dict[str, dict] = {}
        self._https = False
        self._on_connect = self._on_error = self._on_warning = None
        self._trader_state = self._trader_action = None

    # --- wiring -----------------------------------------------------------------
    def set_connect_callback(self, fn) -> None:
        self._on_connect = fn

    def set_disconnect_callback(self, fn) -> None:
        self.phones.on_last_left = fn

    def set_error_callback(self, fn) -> None:
        """fn(message) when the server can't start (e.g. the port is taken)."""
        self._on_error = fn

    def set_warning_callback(self, fn) -> None:
        """fn(message) for problems that don't stop the server."""
        self._on_warning = fn

    def set_trader_state_callback(self, fn) -> None:
        self._trader_state = fn

    def set_trader_action_callback(self, fn) -> None:
        self._trader_action = fn

    @staticmethod
    def _configured_port() -> int:
        from core import settings_store
        try:
            port = int(settings_store.load_settings().get("dashboard_port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            return DEFAULT_PORT
        return port if 0 < port < 65536 else DEFAULT_PORT

    # --- used by the desktop ------------------------------------------------------
    def new_key(self) -> str:
        return self.keys.issue()

    def get_url(self) -> str:
        return f"{'https' if self._https else 'http'}://{self.ip}:{self.port}"

    def register_file(self, path) -> str | None:
        """A long random link to one local file. The randomness is the access
        control, so the link also works before any phone has paired."""
        path = Path(path)
        if not path.is_file():
            return None
        file_id = secrets.token_urlsafe(16)
        self._shared[file_id] = path
        return f"{self.get_url()}/f/{file_id}"

    async def broadcast(self, message: dict) -> None:
        self._history = (self._history + [message])[-HISTORY_KEPT:]
        await self.phones.send_all(message)

    async def broadcast_audio(self, chunk: bytes) -> None:
        for sock in list(self._listeners):
            try:
                await sock.send_bytes(chunk)
            except Exception:
                self._listeners.discard(sock)

    def _warn(self, message: str) -> None:
        if self._on_warning:
            try:
                self._on_warning(message)
            except Exception:
                pass

    # --- HTTP/WebSocket app -----------------------------------------------------
    def _new_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens.add(token)
        return token

    def _authorised(self, token: str | None) -> bool:
        return bool(token) and token.strip() in self._tokens

    def _app(self):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        background = get_resource_dir() / "dashboard" / "static" / "avatar_bg.jpg"

        def bearer(req: Request) -> str:
            return req.headers.get("authorization", "").removeprefix("Bearer ").strip()

        def denied():
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        @app.get("/")
        async def home():
            from core import settings_store
            enabled = settings_store.load_settings()["trader"]["enabled"]
            return HTMLResponse(APP_HTML.replace("__TRADER_ENABLED__", "true" if enabled else "false"))

        @app.get("/login")
        async def login_page():
            return HTMLResponse(LOGIN_HTML)

        @app.get("/static/avatar_bg.jpg")
        async def backdrop():
            if background.is_file():
                return FileResponse(background, media_type="image/jpeg")
            return JSONResponse({"error": "not found"}, status_code=404)

        @app.post("/login")
        async def pair_by_key(req: Request):
            body = await req.json()
            if not self.keys.redeem(str(body.get("pin", ""))):
                return JSONResponse({"ok": False, "error": "Invalid or expired key"}, status_code=401)
            await self.broadcast({"type": "sys", "text": "Remote connection established."})
            return JSONResponse({"ok": True, "token": self._new_token()})

        @app.get("/auto-login")
        async def pair_by_qr(key: str = ""):
            page = ("<body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                    "background:#000;color:#f0f0f0;font-family:sans-serif;text-align:center'>{}</body>")
            if not self.keys.redeem(key):
                return HTMLResponse(page.format("<div><h2 style='color:#ef476f'>This code has expired</h2>"
                                                "<p style='color:#9e9e9e'>Press Remote Control in Omni-OS for a new one.</p></div>"))
            token = self._new_token()
            await self.broadcast({"type": "sys", "text": "Phone paired by scanning the QR code."})
            return HTMLResponse(page.format(f"<script>sessionStorage.setItem('seraph_token','{token}');"
                                            "location.replace('/');</script><p>Connecting…</p>"))

        @app.get("/f/{file_id}")
        async def shared_file(file_id: str):
            path = self._shared.get(file_id)
            if path is None or not path.is_file():
                return JSONResponse({"error": "Not found or expired"}, status_code=404)
            return FileResponse(path)   # shown in the browser, not forced to download

        @app.post("/api/command")
        async def typed(req: Request):
            if not self._authorised(bearer(req)):
                return denied()
            text = ((await req.json()).get("text") or "").strip()
            if text:
                await self.commands.put(text)
            return JSONResponse({"ok": True})

        @app.get("/api/trader/state")
        async def trader_state(req: Request):
            if not self._authorised(bearer(req)):
                return denied()
            if self._trader_state is None:
                return JSONResponse({"open": False})
            try:
                # Can hit the network on a cache miss — never on the shared event loop.
                state = await asyncio.to_thread(self._trader_state)
            except Exception as err:
                return JSONResponse({"open": False, "error": str(err)})
            return JSONResponse({"open": True, **state} if state else {"open": False})

        @app.post("/api/trader/action")
        async def trader_action(req: Request):
            if not self._authorised(bearer(req)):
                return denied()
            if self._trader_action is None:
                return JSONResponse({"ok": False, "message": "trader not available"}, status_code=503)
            body = await req.json()
            action = (body.pop("action", "") or "").strip()
            if not action:
                return JSONResponse({"ok": False, "message": "missing action"}, status_code=400)
            # A trade can wait minutes on-chain; answer now and let the phone poll.
            job = secrets.token_urlsafe(12)
            self._jobs[job] = {"status": "pending"}

            def work():
                try:
                    outcome = self._trader_action(action, body)
                except Exception as err:
                    outcome = {"ok": False, "message": str(err)}
                self._jobs[job] = {"status": "done", "result": outcome}

            threading.Thread(target=work, name="trader-job", daemon=True).start()
            return JSONResponse({"pending": True, "jobId": job})

        @app.get("/api/trader/action/result")
        async def trader_result(req: Request, jobId: str = ""):
            if not self._authorised(bearer(req)):
                return denied()
            job = self._jobs.get(jobId)
            if job is None:
                return JSONResponse({"status": "unknown"}, status_code=404)
            if job["status"] == "done":
                self._jobs.pop(jobId, None)   # collected once, then forgotten
            return JSONResponse(job)

        @app.websocket("/ws")
        async def chat_socket(sock: WebSocket, token: str = ""):
            if not self._authorised(token):
                await sock.close(code=4001)
                return
            await sock.accept()
            self.phones.sockets.add(sock)
            # The phone counts as connected once this socket is open — not at
            # login — because this is what replies actually travel over.
            if self._on_connect:
                self._on_connect()
            for old in self._history[-50:]:
                try:
                    await sock.send_json(old)
                except Exception:
                    break
            heard = time.monotonic()

            async def keepalive():
                while True:
                    await asyncio.sleep(PING_EVERY)
                    if time.monotonic() - heard > SILENT_LIMIT:
                        break
                    try:
                        await sock.send_json({"type": "ping"})
                    except Exception:
                        break
                # A half-open connection never raises by itself — end it here.
                self.phones.drop(sock)
                try:
                    await sock.close(code=4000)
                except Exception:
                    pass

            watcher = asyncio.create_task(keepalive())
            try:
                while True:
                    msg = await sock.receive_json()
                    heard = time.monotonic()
                    kind = msg.get("type")
                    if kind == "command" and (msg.get("text") or "").strip():
                        await self.commands.put(msg["text"].strip())
                    elif kind == "mic_dropped":
                        self._warn("Phone mic buffer overflowed while reconnecting — some audio was dropped.")
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                watcher.cancel()
                self.phones.drop(sock)

        @app.websocket("/ws/audio")
        async def audio_socket(sock: WebSocket, token: str = ""):
            if not self._authorised(token):
                await sock.close(code=4001)
                return
            await sock.accept()
            self._listeners.add(sock)
            await self.broadcast({"type": "sys", "text": "Phone audio connected."})
            overflowing = False
            try:
                while True:
                    frame = await sock.receive_bytes()
                    try:
                        self.phone_audio.put_nowait(frame)
                        overflowing = False
                    except asyncio.QueueFull:
                        if not overflowing:   # say it once per episode, not per frame
                            self._warn("Phone audio queue full — dropping frames.")
                        overflowing = True
            except (WebSocketDisconnect, RuntimeError):
                pass
            finally:
                self._listeners.discard(sock)
                await self.broadcast({"type": "sys", "text": "Phone audio disconnected."})

        return app

    # --- running ---------------------------------------------------------------
    def _failed(self, message: str) -> None:
        self.running = False
        self.start_error = message
        print(f"[dashboard] {message}")
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:
                pass

    async def serve(self) -> None:
        if not _AVAILABLE:
            self._failed('the web server packages are missing (fastapi, uvicorn[standard]) — reinstall Omni-OS or pip-install them')
            return
        if net.is_cgnat(self.ip):
            self._warn(f"is using {self.ip}, which looks like a VPN (e.g. Tailscale) address rather than your "
                       "Wi-Fi — a phone on the Wi-Fi can't reach it. Disconnect the VPN, then fully quit and "
                       "reopen Omni-OS (the address is only chosen at startup).")
        cert = net.ensure_certificate(self.ip)
        tls = {}
        if cert:
            self._https = True
            tls = {"ssl_certfile": str(cert[0]), "ssl_keyfile": str(cert[1])}
        else:
            print("[dashboard] no 'cryptography' package — serving plain HTTP; the phone mic needs HTTPS.")

        # uvicorn turns a bind failure into a bare exit with no reason, so try
        # the port ourselves first to report something useful.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as trial:
                trial.bind(("0.0.0.0", self.port))
        except OSError as err:
            self._failed(f"couldn't use port {self.port} ({err.strerror or err}) — another copy of Omni-OS or "
                         "another app (e.g. Seraph Guardian) may be using it. Close it or pick a different port "
                         "in Settings → Remote Dashboard.")
            return

        config = uvicorn.Config(self._app(), host="0.0.0.0", port=self.port, log_level="warning", **tls)
        print(f"[dashboard] {self.get_url()}")
        try:
            self.running = True
            await uvicorn.Server(config).serve()
        except asyncio.CancelledError:
            raise
        except BaseException as err:   # includes uvicorn's SystemExit; must not kill the shared loop
            self._failed(f"failed to start ({err})")
        finally:
            self.running = False
