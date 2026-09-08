"""Weekly reset: summarizes what's changed in memory recently (projects,
notes, wishes) and flags entries that look stalled — nothing touched in a
while. Deterministic, no LLM call of its own: returns a plain-text digest
and leaves narrating it conversationally to whichever Gemini Live companion
called the tool, the same way list_desktop()/get_desktop_stats() hand back
plain formatted text in actions/desktop.py. Reads the same memory store
actions/action_items.py files commitments into, so an item extracted from a
meeting a few days ago naturally shows up here later as either recent or
stalled. Inspired by the Hermes Agent skill catalog's
`weekly-review-planning` skill — see the 2026-09-09 conversation with the
user for context.

Companion-namespace aware the same way main.py's own "save_memory" tool
branch is — see actions/action_items.py's module docstring for why the
namespace is passed in by the caller rather than looked up here.
"""

from datetime import datetime

from memory import memory_manager

STALE_AFTER_DAYS = 14

TOOL_DECLARATIONS = [
    {
        "name": "weekly_review",
        "description": (
            "Pulls together a weekly-reset style summary from memory: what's been "
            "added or changed recently under projects/notes/wishes, and which older "
            "commitments look stalled (haven't been touched in a while). Use this "
            "when the user asks for a weekly review, a recap of what's going on, or "
            "'what have I got going / what's stalled'."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _days_since(date_str: str) -> int | None:
    try:
        then = datetime.fromisoformat(date_str).date()
    except Exception:
        return None
    return (datetime.now().date() - then).days


def weekly_review(parameters: dict = None, player=None, namespace: str | None = None) -> str:
    memory = memory_manager.load_memory(namespace)

    recent: list[str] = []
    stalled: list[tuple[int, str]] = []
    for category in ("projects", "notes", "wishes"):
        for key, entry in memory.get(category, {}).items():
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            line = f"[{category}] {key}: {entry['value']}"
            age = _days_since(entry.get("updated", ""))
            if age is None or age <= 7:
                recent.append(line)  # no timestamp — treat as recent rather than lose it
            elif age >= STALE_AFTER_DAYS:
                stalled.append((age, line))

    lines = ["WEEKLY REVIEW", ""]

    lines.append(f"Updated in the last 7 days ({len(recent)}):")
    if recent:
        lines.extend(f"  - {l}" for l in recent)
    else:
        lines.append("  (nothing)")

    lines.append("")
    stalled.sort(reverse=True)
    lines.append(f"Looks stalled, {STALE_AFTER_DAYS}+ days untouched ({len(stalled)}):")
    if stalled:
        lines.extend(f"  - ({age}d) {l}" for age, l in stalled)
    else:
        lines.append("  (nothing stalled)")

    return "\n".join(lines)
