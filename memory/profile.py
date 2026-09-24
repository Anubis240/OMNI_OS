"""Long-term facts about the user, one JSON file per companion.

File layout (unchanged from earlier releases, so existing memories load):

    {
      "identity":      {"name": {"value": "Ana", "updated": "2026-09-01"}, ...},
      "preferences":   {...}, "projects": {...}, "relationships": {...},
      "wishes":        {...}, "notes": {...},
      "sessions":      [{"date": "...", "summary": "...", "language": "..."}]
    }

The default companion uses <data>/memory/long_term.json; any other
companion with a memory_namespace gets <data>/memory/<namespace>/long_term.json,
so what one companion learns never shows up in another's prompt.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from core.app_paths import get_data_dir

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")
VALUE_LIMIT = 380        # characters kept per remembered value
FILE_BUDGET = 2200       # rough cap on the facts part of the file, in characters
SESSIONS_KEPT = 3
SUMMARY_LIMIT = 280
PROMPT_LIMIT = 2000

_io_lock = threading.RLock()


def _root() -> Path:
    return get_data_dir() / "memory"


def path_for(namespace: str | None = None) -> Path:
    return (_root() / namespace if namespace else _root()) / "long_term.json"


def _today() -> str:
    return date.today().isoformat()


def _blank() -> dict:
    return {c: {} for c in CATEGORIES}


def load(namespace: str | None = None) -> dict:
    """The whole memory, with every category present. Never raises; a
    missing or unreadable file reads as empty (and is left untouched)."""
    with _io_lock:
        try:
            data = json.loads(path_for(namespace).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _blank()
    if not isinstance(data, dict):
        return _blank()
    for c in CATEGORIES:
        if not isinstance(data.get(c), dict):
            data[c] = {}
    return data


def _write(data: dict, namespace: str | None) -> None:
    target = path_for(namespace)
    with _io_lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)


def _facts_size(data: dict) -> int:
    return len(json.dumps({c: data.get(c, {}) for c in CATEGORIES}, ensure_ascii=False))


def _shrink(data: dict) -> None:
    """Drop the least recently updated facts until under FILE_BUDGET."""
    if _facts_size(data) <= FILE_BUDGET:
        return
    ages = sorted(
        ((entry.get("updated", ""), cat, key)
         for cat in CATEGORIES for key, entry in data[cat].items() if isinstance(entry, dict)),
    )
    for _, cat, key in ages:
        del data[cat][key]
        if _facts_size(data) <= FILE_BUDGET:
            return


def _clean_value(raw) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    text = str(raw)
    if not text.strip():
        return None
    return text if len(text) <= VALUE_LIMIT else text[:VALUE_LIMIT].rstrip() + "…"


def remember(facts: dict, namespace: str | None = None) -> dict:
    """Merge {category: {key: value-or-{"value": v}}} into memory and save.

    Blank/None values are ignored; a value equal to what's stored keeps its
    original date. Returns the updated memory."""
    data = load(namespace)
    changed = False
    for cat, items in (facts or {}).items():
        if not isinstance(items, dict):
            continue
        bucket = data.setdefault(cat, {})
        for key, raw in items.items():
            value = _clean_value(raw)
            if value is None:
                continue
            old = bucket.get(key)
            if isinstance(old, dict) and old.get("value") == value:
                continue
            bucket[key] = {"value": value, "updated": _today()}
            changed = True
    if changed:
        _shrink(data)
        _write(data, namespace)
    return data


def remember_one(category: str, key: str, value: str, namespace: str | None = None) -> None:
    remember({category if category in CATEGORIES else "notes": {key: value}}, namespace)


def forget_fact(category: str, key: str, namespace: str | None = None) -> bool:
    data = load(namespace)
    if key not in data.get(category, {}):
        return False
    del data[category][key]
    _write(data, namespace)
    return True


# --- the prompt block --------------------------------------------------------

_SECTION_TITLES = {
    "preferences": "Likes and preferences",
    "projects": "What they're working on",
    "relationships": "People in their life",
    "wishes": "Plans and wishes",
    "notes": "Other things to keep in mind",
}
_IDENTITY_FIRST = ("name", "age", "birthday", "city", "job", "language", "nationality")


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def prompt_block(memory: dict | None) -> str:
    """The facts as a compact block for the system prompt ('' if none)."""
    if not memory:
        return ""
    lines: list[str] = []
    identity = memory.get("identity", {})
    ordered = [k for k in _IDENTITY_FIRST if k in identity] + [k for k in identity if k not in _IDENTITY_FIRST]
    for key in ordered:
        value = _clean_value(identity[key])
        if value:
            lines.append(f"{_label(key)}: {value}")
    for cat, title in _SECTION_TITLES.items():
        entries = [(k, _clean_value(v)) for k, v in memory.get(cat, {}).items()]
        entries = [(k, v) for k, v in entries if v][-12:]
        if entries:
            lines.append(f"{title}: " + "; ".join(f"{_label(k)} — {v}" for k, v in entries))
    if not lines:
        return ""
    block = "[ABOUT THE USER — weave in naturally when relevant; never read it out as a list]\n" + "\n".join(lines)
    return (block if len(block) <= PROMPT_LIMIT else block[:PROMPT_LIMIT - 1] + "…") + "\n"


# --- end-of-session summaries ------------------------------------------------

def add_session_summary(summary: str, language: str = "", namespace: str | None = None) -> None:
    text = (summary or "").strip()
    if not text:
        return
    with _io_lock:
        data = load(namespace)
        sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
        item = {"date": _today(), "summary": text[:SUMMARY_LIMIT]}
        if language:
            item["language"] = language
        data["sessions"] = (sessions + [item])[-SESSIONS_KEPT:]
        _write(data, namespace)


def take_last_session(namespace: str | None = None) -> dict | None:
    """Remove and return the newest session summary, so it's brought up once."""
    with _io_lock:
        if not path_for(namespace).exists():
            return None
        data = load(namespace)
        sessions = data.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            return None
        last = sessions.pop()
        _write(data, namespace)
        return last
