"""Extracts action items (task, owner, deadline) from pasted meeting notes
or document text using Gemini, then files each one into memory under the
"projects" category via memory_manager.update_memory — the same store and
same "projects" bucket a companion's own remembered work already lives in —
so weekly_review.py can later surface them as commitments and flag ones
that have gone stale. Inspired by the Hermes Agent skill catalog's
`meeting-action-items`/`document-to-action-items` skills — see the
2026-09-09 conversation with the user for context.

Companion-namespace aware the same way main.py's own "save_memory" tool
branch is: the caller (main.py::_execute_tool) resolves self._companion's
memory_namespace and passes it in explicitly, since that's the only place
the active companion is known — this module has no access to it otherwise.
"""

import json

from core.app_paths import get_data_dir
from memory import memory_manager

TOOL_DECLARATIONS = [
    {
        "name": "extract_action_items",
        "description": (
            "Reads a chunk of meeting notes or document text the user has pasted or "
            "described, pulls out concrete action items (who owns what, and any "
            "deadline mentioned), reads them back, and files each one into memory as "
            "a tracked commitment so a later weekly_review call can follow up on it. "
            "Use this whenever the user pastes meeting notes, a document, or a wall "
            "of text and asks what needs to get done, or to 'pull out the action "
            "items' / 'what are my tasks from this'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "The meeting notes or document text to extract action items from"},
                "source_label": {"type": "STRING", "description": "Short label for where this came from, e.g. 'Tuesday standup' — used as the memory key prefix"},
            },
            "required": ["text"],
        },
    },
]


def _get_api_key() -> str:
    path = get_data_dir() / "config" / "api_keys.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("gemini_api_key", "")
    except Exception:
        return ""


def _extract_with_gemini(text: str) -> list[dict]:
    from google import genai

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("No Gemini API key configured.")

    client = genai.Client(api_key=api_key)
    prompt = (
        "Extract concrete action items from the text below. Reply with ONLY a JSON "
        'array, no markdown, no explanation: [{"task": "...", "owner": "..." or '
        '"unassigned", "deadline": "..." or "none"}]. If there are no clear action '
        "items, reply with exactly: []\n\nText:\n" + text
    )
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    raw = (response.text or "").strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:-1]).strip()
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("Model did not return a JSON array.")
    return items


def extract_action_items(parameters: dict, player=None, namespace: str | None = None) -> str:
    params = parameters or {}
    text = (params.get("text") or "").strip()
    if not text:
        return "No text given to extract action items from."
    source = (params.get("source_label") or "note").strip()[:40] or "note"

    try:
        items = _extract_with_gemini(text)
    except Exception as e:
        return f"Could not extract action items: {e}"

    updates: dict = {"projects": {}}
    lines = []
    for i, item in enumerate(items, start=1):
        task = str(item.get("task", "")).strip()
        if not task:
            continue
        owner = str(item.get("owner", "unassigned")).strip() or "unassigned"
        deadline = str(item.get("deadline", "none")).strip() or "none"
        key = f"{source}-{i}".lower().replace(" ", "_")[:60]
        updates["projects"][key] = {"value": f"{task} (owner: {owner}, deadline: {deadline})"}
        lines.append(f"{i}. {task} — {owner}" + (f" (due {deadline})" if deadline not in ("none", "") else ""))

    if not lines:
        return "No clear action items found in that text."

    memory_manager.update_memory(updates, namespace=namespace)
    if player:
        try:
            player.write_log(f"[ActionItems] Filed {len(lines)} item(s) under '{source}'")
        except Exception:
            pass
    return f"Found {len(lines)} action item(s), filed under \"{source}\":\n" + "\n".join(lines)
