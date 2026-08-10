"""User-configurable settings: custom MCP servers, general-purpose API keys
(for those servers/skills to use — Seraph's own core LLM stays Gemini),
skill prompt add-ons, and optional Claude Code CLI delegation.

Separate from config/api_keys.json (Gemini key + system settings) since
that file predates this feature and other code already reads it directly —
no reason to touch a working format. This one is purely additive.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


SETTINGS_PATH = _base_dir() / "config" / "settings.json"

DEFAULT_SETTINGS = {
    "mcp_servers": [],   # [{id, name, url, apiKey}]
    "api_keys": {"openai": "", "anthropic": ""},
    "skills": [],        # [{id, name, content, enabled}]
    "claude_agent": {"enabled": False, "cliPath": "", "vaultDir": "", "extraDir": ""},
}


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    for key in DEFAULT_SETTINGS:
        if key in data:
            if isinstance(DEFAULT_SETTINGS[key], dict) and isinstance(data[key], dict):
                merged[key].update(data[key])
            else:
                merged[key] = data[key]
    return merged


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def new_id() -> str:
    return uuid.uuid4().hex[:12]
