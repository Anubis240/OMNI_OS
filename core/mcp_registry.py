"""Bridges user-configured custom MCP servers (see core/settings_store.py)
into Gemini Live's function-calling tool list. Reuses trader/mcp_client.py's
McpClient as-is — it's already server-agnostic (Streamable HTTP JSON-RPC),
just defaulted to the built-in Seraph server elsewhere.

Tool names are prefixed "mcp__<serverId>__<toolName>" (matching the MCP
naming convention already used for tools elsewhere in this environment) so
_execute_tool can route a call back to the right server, and so two
different user servers exposing a same-named tool never collide.
"""

from __future__ import annotations

from core import settings_store
from trader.mcp_client import McpClient

_clients: dict[str, McpClient] = {}


def _client_for(server: dict) -> McpClient:
    sid = server["id"]
    client = _clients.get(sid)
    if client is None or client.url != server.get("url"):
        # apiKey may be a literal secret or a {{CUSTOM_KEY_NAME}} placeholder
        # pointing at Settings -> API Keys -> custom keys (see
        # settings_store.resolve_key_placeholders) — either works.
        api_key = settings_store.resolve_key_placeholders(server.get("apiKey") or "") or None
        client = McpClient(url=server.get("url", ""), api_key=api_key)
        _clients[sid] = client
    return client


def _json_schema_to_gemini(schema: dict) -> dict:
    """Gemini's function-parameter schema is a small OpenAPI-style subset
    with UPPERCASE type names; MCP's inputSchema is plain JSON Schema
    (lowercase). Recursively re-cases the parts Gemini actually uses."""
    if not isinstance(schema, dict):
        return {"type": "STRING"}
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, str):
        out["type"] = t.upper()
    elif isinstance(t, list) and t:
        out["type"] = str(t[0]).upper()
    else:
        out["type"] = "STRING"
    if schema.get("description"):
        out["description"] = schema["description"]
    if schema.get("enum"):
        out["enum"] = schema["enum"]
    if out["type"] == "OBJECT" and isinstance(schema.get("properties"), dict):
        out["properties"] = {k: _json_schema_to_gemini(v) for k, v in schema["properties"].items()}
        if schema.get("required"):
            out["required"] = schema["required"]
    if out["type"] == "ARRAY" and isinstance(schema.get("items"), dict):
        out["items"] = _json_schema_to_gemini(schema["items"])
    return out


def gather_custom_tool_declarations(servers: list[dict]) -> tuple[list[dict], dict[str, tuple[dict, str]]]:
    """Returns (function_declarations for the Gemini tools list, a dispatch
    map of {prefixed_name: (server, original_tool_name)}). A server that
    fails to respond is skipped silently — one misconfigured/offline custom
    server must never block the rest of the assistant from starting."""
    declarations: list[dict] = []
    dispatch: dict[str, tuple[dict, str]] = {}
    for server in servers:
        if not server.get("url"):
            continue
        try:
            result = _client_for(server).tools()
        except Exception:
            continue
        if not result.get("ok"):
            continue
        for tool in result.get("tools", []):
            prefixed = f"mcp__{server['id']}__{tool['name']}"
            declarations.append({
                "name": prefixed,
                "description": f"[{server.get('name', server['id'])}] {tool.get('description', '')}",
                "parameters": _json_schema_to_gemini(tool.get("schema") or {"type": "object"}),
            })
            dispatch[prefixed] = (server, tool["name"])
    return declarations, dispatch


def call_custom_tool(server: dict, tool_name: str, args: dict) -> dict:
    return _client_for(server).call(tool_name, args)
