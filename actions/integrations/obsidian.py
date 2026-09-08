"""Obsidian vault integration — local filesystem only, no account, no API,
no network call. auth_type "local" in the catalog (see settings_store.py):
the only "credential" is a vault directory path. Modeled after Hermes
Agent's `obsidian` skill ("read, search, create, and edit notes in the
Obsidian vault"), but implemented directly against the vault's plain
markdown files rather than shelling out to Obsidian itself — Obsidian has
no official CLI, and the vault is just a folder of .md files on disk, so
there's nothing to "connect" to beyond knowing where that folder is.

Path safety: every operation resolves its note path relative to the
configured vault root and refuses anything that escapes it (symlink tricks
included, via Path.resolve()) — same defensive pattern as
actions/computer_control.py's _safe_screenshot_path.
"""

from pathlib import Path

from ._common import get_credentials, log

TOOL_DECLARATIONS = [
    {
        "name": "obsidian_search",
        "description": "Searches note titles and content in the configured Obsidian vault for a keyword or phrase.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"query": {"type": "STRING", "description": "Text to search for"}},
            "required": ["query"],
        },
    },
    {
        "name": "obsidian_read_note",
        "description": "Reads the full content of a note in the Obsidian vault.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"path": {"type": "STRING", "description": "Note path relative to the vault root, e.g. 'Projects/Omni-OS.md' (.md optional)"}},
            "required": ["path"],
        },
    },
    {
        "name": "obsidian_create_note",
        "description": "Creates a new note in the Obsidian vault. Fails if a note already exists at that path — use obsidian_append_note to add to one instead.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Note path relative to the vault root, e.g. 'Ideas/New idea.md' (.md optional)"},
                "content": {"type": "STRING", "description": "Markdown content for the note"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "obsidian_append_note",
        "description": "Appends text to the end of an existing note in the Obsidian vault. Creates the note if it doesn't exist yet.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Note path relative to the vault root"},
                "content": {"type": "STRING", "description": "Text to append"},
            },
            "required": ["path", "content"],
        },
    },
]


def _vault_root(creds: dict) -> Path:
    return Path(creds["vault_path"]).expanduser().resolve()


def _resolve_note_path(vault: Path, raw: str) -> Path | None:
    """Returns an absolute path inside the vault, or None if `raw` would
    escape it. Adds .md if no extension was given, matching how a user
    would actually refer to a note by name."""
    raw = raw.strip().lstrip("/\\")
    if not raw:
        return None
    if not raw.lower().endswith(".md"):
        raw += ".md"
    try:
        candidate = (vault / raw).resolve()
        candidate.relative_to(vault)
    except Exception:
        return None
    return candidate


def dispatch(name: str, args: dict, player=None) -> str:
    creds = get_credentials("obsidian")
    if not creds or not creds.get("vault_path"):
        return "Obsidian isn't connected — set your vault folder path in the Integrations tab."

    vault = _vault_root(creds)
    if not vault.is_dir():
        msg = f"Configured Obsidian vault path doesn't exist: {vault}"
        log("Obsidian", msg, player)
        return msg

    try:
        if name == "obsidian_search":
            query = (args.get("query") or "").strip().lower()
            if not query:
                return "No search text given."
            hits = []
            for md in sorted(vault.rglob("*.md")):
                rel = md.relative_to(vault).as_posix()
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if query in rel.lower() or query in text.lower():
                    snippet = ""
                    idx = text.lower().find(query)
                    if idx != -1:
                        start = max(0, idx - 40)
                        snippet = "…" + text[start:idx + len(query) + 40].replace("\n", " ") + "…"
                    hits.append(f"{rel}" + (f" — {snippet}" if snippet else ""))
                if len(hits) >= 15:
                    break
            if not hits:
                return f"No notes found matching '{args.get('query')}'."
            return "Obsidian results:\n" + "\n".join(hits)

        if name == "obsidian_read_note":
            note = _resolve_note_path(vault, args.get("path", ""))
            if note is None:
                return "Invalid note path."
            if not note.exists():
                return f"No note found at '{args.get('path')}'."
            return note.read_text(encoding="utf-8", errors="replace")

        if name == "obsidian_create_note":
            note = _resolve_note_path(vault, args.get("path", ""))
            if note is None:
                return "Invalid note path."
            if note.exists():
                return f"A note already exists at '{args.get('path')}' — use obsidian_append_note instead."
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(args.get("content", ""), encoding="utf-8")
            return f"Created note: {note.relative_to(vault).as_posix()}"

        if name == "obsidian_append_note":
            note = _resolve_note_path(vault, args.get("path", ""))
            if note is None:
                return "Invalid note path."
            note.parent.mkdir(parents=True, exist_ok=True)
            existing = note.read_text(encoding="utf-8", errors="replace") if note.exists() else ""
            separator = "\n" if existing and not existing.endswith("\n") else ""
            note.write_text(existing + separator + args.get("content", "") + "\n", encoding="utf-8")
            return f"Appended to note: {note.relative_to(vault).as_posix()}"

    except Exception as e:
        msg = f"Obsidian error: {e}"
        log("Obsidian", msg, player)
        return msg

    return "Unknown Obsidian action."
