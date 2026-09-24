"""One place for tools that need a quick Gemini text/vision call.

The live voice session has its own client in main.py; this is for the
short, one-shot requests tools make (summaries, code, plans, vision).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from core.app_paths import get_data_dir

FAST = "gemini-2.5-flash"
LITE = "gemini-2.5-flash-lite"


def api_key() -> str:
    path = get_data_dir() / "config" / "api_keys.json"
    try:
        key = json.loads(path.read_text(encoding="utf-8")).get("gemini_api_key", "")
    except (OSError, ValueError):
        key = ""
    if not key:
        raise RuntimeError("no Gemini API key configured")
    return key


@lru_cache(maxsize=1)
def _client_for(key: str):
    from google import genai
    return genai.Client(api_key=key)


def client():
    return _client_for(api_key())


def ask(prompt, *, model: str = FAST, system: str | None = None,
        search: bool = False, temperature: float | None = None,
        json_mode: bool = False) -> str:
    """Send `prompt` (a string, or a list of parts for images) and return
    the reply text. Raises on an empty reply so callers can fall back."""
    config: dict = {}
    if system:
        config["system_instruction"] = system
    if search:
        config["tools"] = [{"google_search": {}}]
    if temperature is not None:
        config["temperature"] = temperature
    if json_mode:
        config["response_mime_type"] = "application/json"
    resp = client().models.generate_content(model=model, contents=prompt, config=config or None)
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise RuntimeError("the model returned no text")
    return text


def image_part(data: bytes, mime: str = "image/png"):
    from google.genai import types
    return types.Part.from_bytes(data=data, mime_type=mime)


_FENCE = re.compile(r"^```[\w+-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def strip_fence(text: str) -> str:
    """Drop a single surrounding ```lang … ``` block if the model added one."""
    m = _FENCE.match(text.strip())
    return m.group(1) if m else text.strip()


def ask_json(prompt, **kw):
    """Like ask(), but asks for and parses a JSON reply (tolerating a fence)."""
    if not kw.get("search"):   # grounding and JSON mode can't be combined
        kw.setdefault("json_mode", True)
    return json.loads(strip_fence(ask(prompt, **kw)))
