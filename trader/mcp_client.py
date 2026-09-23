"""Seraph MCP client — Streamable HTTP JSON-RPC, port of main.js's
mcpToolsDirect/mcpCallDirect (the Electron trader app's hand-rolled MCP
client). Reuses `requests` (already a project dependency) rather than
introducing an async HTTP client, matching this codebase's existing
sync-call convention (see actions/image_generator.py).

Resolves credentials per request using D6' precedence: explicit constructor
key, SeraphAuth provider (manual or OAuth), Guardian settings.json, then the
app's legacy api_keys.json. New manual keys use encrypted settings storage.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import requests

from core.app_paths import get_data_dir
from trader.seraph_auth import get_default_auth

logger = logging.getLogger(__name__)

SERAPH_MCP_URL = "https://seraph.kondux.io/mcp"
SERAPH_SERVER_ID = "seraph-kondux"
SERAPH_KEY_SIGNUP_URL = "https://seraph.kondux.io/"

_GUARDIAN_SETTINGS_PATH = Path(os.environ.get("APPDATA", "")) / "Seraph Guardian" / "settings.json"

TOOLS_CACHE_SECONDS = 10 * 60


def _app_api_keys_path() -> Path:
    """Resolved lazily: get_data_dir() depends on runtime state, and the old
    module-level Path(__file__).parent.parent pointed at the source tree, which
    is read-only in a frozen build."""
    return get_data_dir() / "config" / "api_keys.json"


class McpUnauthorized(RuntimeError):
    """HTTP 401 from the MCP endpoint. Separated from every other transport
    failure so only a genuine credential rejection can trigger a re-mint."""


def _load_guardian_api_key() -> str:
    try:
        data = json.loads(_GUARDIAN_SETTINGS_PATH.read_text(encoding="utf-8"))
        for server in data.get("mcpServers", []):
            if server.get("id") == SERAPH_SERVER_ID:
                key = server.get("apiKey") or ""
                if key:
                    return key
    except Exception:
        return ""
    return ""


def _load_app_json_api_key() -> str:
    try:
        data = json.loads(_app_api_keys_path().read_text(encoding="utf-8"))
        return data.get("seraph_mcp_api_key") or ""
    except Exception:
        return ""


def _load_legacy_api_key() -> tuple[str, str]:
    """Returns (key, source): guardian_settings, api_keys_json, or none."""
    key = _load_guardian_api_key()
    if key:
        return key, "guardian_settings"
    key = _load_app_json_api_key()
    if key:
        return key, "api_keys_json"
    return "", "none"


def save_seraph_api_key(key: str) -> None:
    """Stores a user-entered key in the encrypted settings block (never in
    plaintext api_keys.json) and drops the live session so the next call uses it."""
    get_default_auth().set_manual_api_key(key)
    get_default_client().invalidate()


def migrate_legacy_api_key() -> bool:
    """Move the plaintext app key into encrypted settings once.

    Preserve other entries; cleanup failure keeps the legacy copy readable.
    """
    try:
        auth = get_default_auth()
        if auth.get_api_key():
            return False
        path = _app_api_keys_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        key = data.get("seraph_mcp_api_key")
        if not key:
            return False
        auth.set_manual_api_key(key)
        del data["seraph_mcp_api_key"]
        try:
            path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        except Exception:
            logger.warning("legacy_api_key_cleanup_failed")
        return True
    except Exception:
        return False


class McpClient:
    """One client per MCP server. Holds session id + cached tool list,
    mirroring main.js's per-server `mcpState` entry."""

    def __init__(self, url: str = SERAPH_MCP_URL, api_key: str | None = None,
                 key_provider: Callable[[], str | None] | None = None,
                 on_unauthorized: Callable[[], str | None] | None = None) -> None:
        self.url = url
        self.api_key = api_key
        self._key_provider = key_provider
        self._on_unauthorized = on_unauthorized
        self._session: str | None = None
        self._req_id = 1
        self._tools: list[dict] | None = None
        self._tools_at = 0.0

    def _resolve_credential(self) -> tuple[str, str, str]:
        """Return (secret, mode, source), once per request. Empty means absent."""
        if isinstance(self.api_key, str) and self.api_key:
            return self.api_key, "api_key_explicit", "ctor"
        provider = self._key_provider or (lambda: get_default_auth().get_api_key())
        # A raising provider must not escape: _resolve_credential runs BEFORE the
        # try in _call_with_auth_retry, so an exception here would surface as a
        # traceback instead of the {"ok": False} contract the engine relies on.
        try:
            minted = provider() or ""
        except Exception:
            logger.warning("credential_provider_failed")
            minted = ""
        if minted:
            mode = "api_key_oauth"
            try:
                if get_default_auth().status().mode == "api_key_manual":
                    mode = "api_key_manual"
            except Exception:
                mode = "api_key_oauth"
            return minted, mode, "manual" if mode == "api_key_manual" else "oauth"
        key, source = _load_legacy_api_key()
        if key:
            return key, "api_key_legacy", source
        return "", "none", "none"

    def credential_mode(self) -> str:
        return self._resolve_credential()[1]

    def _headers(self, secret: str) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if secret:
            h["Authorization"] = f"Bearer {secret}"
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _request(self, method: str, params: dict, secret: str, is_notification: bool = False):
        body: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not is_notification:
            body["id"] = self._req_id
            self._req_id += 1
        timeout = 45 if method == "tools/call" else 15
        res = requests.post(self.url, headers=self._headers(secret), json=body, timeout=timeout)
        sid = res.headers.get("mcp-session-id")
        if sid:
            self._session = sid
        if res.status_code == 401:
            raise McpUnauthorized("unauthorized")
        res.raise_for_status()
        if is_notification:
            return None
        ctype = res.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            lines = [l for l in res.text.split("\n") if l.startswith("data:")]
            data = json.loads(lines[-1][5:].strip())
        else:
            data = res.json()
        if data.get("error"):
            raise RuntimeError(data["error"].get("message", "MCP error"))
        return data.get("result")

    def _ensure_ready(self, secret: str):
        if self._session:
            return
        self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "Seraph", "version": "1.0.0"},
        }, secret)
        try:
            self._request("notifications/initialized", {}, secret, is_notification=True)
        except McpUnauthorized:
            raise
        except Exception:
            logger.debug("mcp_initialized_notification_failed")

    def invalidate(self) -> None:
        """Drop session and cached tools; the next request resolves credentials anew."""
        self._session = None
        self._tools = None
        self._tools_at = 0.0

    def _call_with_auth_retry(self, operation: Callable[[str], dict]) -> dict:
        secret, mode, _source = self._resolve_credential()
        if not secret:
            return {"ok": False, "error": "seraph_login_required"}
        try:
            return operation(secret)
        except McpUnauthorized:
            self._session = None
            self._tools = None
            if mode != "api_key_oauth":
                # User-owned keys must never be deleted automatically.
                if self._on_unauthorized is not None:
                    try:
                        self._on_unauthorized()
                    except Exception:
                        logger.warning("mcp_unauthorized_callback_failed")
                return {"ok": False, "error": "seraph_unauthorized"}
            auth = get_default_auth()
            try:
                # on_unauthorized is typed Callable[[], str | None] precisely because it
                # IS the re-minter: it hands back a fresh key or None. Honouring it here
                # keeps the constructor's contract true in the OAuth mode too, and is what
                # lets an integration test point this client at a throwaway SeraphAuth
                # instead of the process-wide singleton. invalidate_session stays on the
                # default auth because it only clears local storage, which the caller and
                # the singleton share.
                fresh = (
                    self._on_unauthorized()
                    if self._on_unauthorized is not None
                    else auth.remint_api_key()
                )
            except Exception:
                fresh = None
            if not fresh:
                auth.invalidate_session()
                return {"ok": False, "error": "seraph_login_required"}
            self.invalidate()
            try:
                return operation(fresh)
            except McpUnauthorized:
                # A rejected fresh key means the OAuth session itself is dead.
                self._session = None
                self._tools = None
                auth.invalidate_session()
                return {"ok": False, "error": "seraph_login_required"}
            except Exception as err:
                self._session = None
                return {"ok": False, "error": str(err)}
        except Exception as err:
            self._session = None
            self._tools = None
            return {"ok": False, "error": str(err)}

    def tools(self) -> dict:
        def op(secret: str) -> dict:
            if self._tools is None or (time.time() - self._tools_at) > TOOLS_CACHE_SECONDS:
                self._ensure_ready(secret)
                result = self._request("tools/list", {}, secret)
                self._tools = [
                    {"name": t["name"], "description": t.get("description", ""), "schema": t.get("inputSchema", {})}
                    for t in (result.get("tools") or [])
                ]
                self._tools_at = time.time()
            return {"ok": True, "tools": self._tools}
        return self._call_with_auth_retry(op)

    def call(self, name: str, args: dict | None = None) -> dict:
        def op(secret: str) -> dict:
            self._ensure_ready(secret)
            result = self._request("tools/call", {"name": name, "arguments": args or {}}, secret)
            text = "\n".join(c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text")
            return {"ok": True, "text": text[:6000], "isError": bool(result.get("isError"))}
        return self._call_with_auth_retry(op)


_default_client: McpClient | None = None


def get_default_client() -> McpClient:
    global _default_client
    if _default_client is None:
        _default_client = McpClient()
        try:
            migrate_legacy_api_key()
        except Exception:
            logger.warning("legacy_api_key_migration_failed")
    return _default_client


def has_credentials() -> bool:
    """Pure credential read: never opens a socket."""
    return bool(get_default_client()._resolve_credential()[0])


def credential_status() -> dict:
    client = get_default_client()
    _secret, mode, source = client._resolve_credential()
    try:
        st = get_default_auth().status()
    except Exception:
        st = None
    return {
        "mode": mode,
        "api_key_prefix": getattr(st, "api_key_prefix", None),
        "subject": getattr(st, "subject", None),
        "org_id": getattr(st, "org_id", None),
        "api_key_created_at": getattr(st, "api_key_created_at", None),
        "api_key_scopes": list(getattr(st, "api_key_scopes", ()) or ()),
        "needs_login": bool(getattr(st, "needs_login", False)),
        "secure_storage": bool(getattr(st, "secure_storage", True)),
        "source": source,
        "last_error": getattr(st, "last_error", None),
    }


def mcp_tools(server_id: str = SERAPH_SERVER_ID) -> dict:
    return get_default_client().tools()


def mcp_call(server_id: str, name: str, args: dict | None = None) -> dict:
    return get_default_client().call(name, args)
