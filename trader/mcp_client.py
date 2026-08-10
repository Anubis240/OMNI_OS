"""Seraph MCP client — Streamable HTTP JSON-RPC, port of main.js's
mcpToolsDirect/mcpCallDirect (the Electron trader app's hand-rolled MCP
client). Reuses `requests` (already a project dependency) rather than
introducing an async HTTP client, matching this codebase's existing
sync-call convention (see actions/image_generator.py).

Reads the Seraph API key from the already-installed Seraph Guardian app's
settings.json so the user doesn't have to re-enter it (see the
seraph-mcp-server-access memory note for why that path is authoritative).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

SERAPH_MCP_URL = "https://seraph.kondux.io/mcp"
SERAPH_SERVER_ID = "seraph-kondux"

_GUARDIAN_SETTINGS_PATH = Path(os.environ.get("APPDATA", "")) / "Seraph Guardian" / "settings.json"

TOOLS_CACHE_SECONDS = 10 * 60


def _load_seraph_api_key() -> str:
    try:
        data = json.loads(_GUARDIAN_SETTINGS_PATH.read_text(encoding="utf-8"))
        for server in data.get("mcpServers", []):
            if server.get("id") == SERAPH_SERVER_ID:
                return server.get("apiKey") or ""
    except Exception:
        pass
    return ""


class McpClient:
    """One client per MCP server. Holds session id + cached tool list,
    mirroring main.js's per-server `mcpState` entry."""

    def __init__(self, url: str = SERAPH_MCP_URL, api_key: str | None = None):
        self.url = url
        self.api_key = api_key if api_key is not None else _load_seraph_api_key()
        self._session: str | None = None
        self._req_id = 1
        self._tools: list[dict] | None = None
        self._tools_at = 0.0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _request(self, method: str, params: dict, is_notification: bool = False):
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if not is_notification:
            body["id"] = self._req_id
            self._req_id += 1
        timeout = 45 if method == "tools/call" else 15
        res = requests.post(self.url, headers=self._headers(), json=body, timeout=timeout)
        sid = res.headers.get("mcp-session-id")
        if sid:
            self._session = sid
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

    def _ensure_ready(self):
        if self._session:
            return
        self._request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "Seraph", "version": "1.0.0"},
        })
        try:
            self._request("notifications/initialized", {}, is_notification=True)
        except Exception:
            pass

    def tools(self) -> dict:
        try:
            if self._tools is None or (time.time() - self._tools_at) > TOOLS_CACHE_SECONDS:
                self._ensure_ready()
                result = self._request("tools/list", {})
                self._tools = [
                    {"name": t["name"], "description": t.get("description", ""), "schema": t.get("inputSchema", {})}
                    for t in (result.get("tools") or [])
                ]
                self._tools_at = time.time()
            return {"ok": True, "tools": self._tools}
        except Exception as err:
            self._session = None
            self._tools = None
            return {"ok": False, "error": str(err)}

    def call(self, name: str, args: dict | None = None) -> dict:
        try:
            self._ensure_ready()
            result = self._request("tools/call", {"name": name, "arguments": args or {}})
            text = "\n".join(c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text")
            return {"ok": True, "text": text[:6000], "isError": bool(result.get("isError"))}
        except Exception as err:
            self._session = None
            return {"ok": False, "error": str(err)}


_default_client: McpClient | None = None


def get_default_client() -> McpClient:
    global _default_client
    if _default_client is None:
        _default_client = McpClient()
    return _default_client


def mcp_tools(server_id: str = SERAPH_SERVER_ID) -> dict:
    return get_default_client().tools()


def mcp_call(server_id: str, name: str, args: dict | None = None) -> dict:
    return get_default_client().call(name, args)
