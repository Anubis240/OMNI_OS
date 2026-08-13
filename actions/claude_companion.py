"""Turn-based conversation driver for claude_agent-backend companions —
distinct from actions/claude_agent.py's one-shot task-delegation tool.

A gemini_live companion gets a realtime voice session; a claude_agent
companion has no equivalent (no bidirectional audio API on the Anthropic
side — see main.py::_resolve_active_companion's docstring), so its
interaction surface is the same typed-text input box every companion
already has (see ui.py's on_text_command), routed here instead of into a
Gemini Live session.
"""

import asyncio

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from core import settings_store

TIMEOUT = 180

# companion id -> last Claude Agent SDK session id, for multi-turn
# continuity within this run of the app. Not persisted across restarts —
# a fresh launch starts each companion's Claude conversation over.
_sessions: dict[str, str] = {}

# companion id -> "idle" | "running", polled by the World view to show live
# status badges. In-memory only, same lifetime as _sessions above.
_status: dict[str, str] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    """Sends one turn to a claude_agent-backend companion's own Claude
    session, returning its reply text. Each companion keeps its own
    session (keyed by companion id) via `resume`, so conversations with
    different companions never bleed into each other and the CLI itself
    tracks history — the caller never re-sends prior turns."""
    settings = settings_store.load_settings()
    ca = settings["claude_agent"]
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "Claude Code delegation isn't set up yet — enable it and set a CLI path in Settings."

    # No API key configured is not an error — it means "use whatever Claude
    # Code login is already active on this machine" (subscription or
    # otherwise), same as the original CLI shell-out did. An explicit key
    # in Settings still wins when present, for anyone who wants metered
    # API billing instead of their subscription.
    api_key = settings["api_keys"].get("anthropic") or None

    _status[companion["id"]] = "running"
    try:
        return await asyncio.wait_for(_send(companion, text, ca, api_key), timeout=TIMEOUT)
    except asyncio.TimeoutError:
        return "Timed out before finishing."
    except Exception as e:
        return f"Error: {e}"
    finally:
        _status[companion["id"]] = "idle"


async def _send(companion: dict, text: str, ca: dict, api_key: str | None) -> str:
    options = ClaudeAgentOptions(
        cli_path=ca["cliPath"],
        cwd=ca.get("vaultDir") or None,
        add_dirs=[ca["extraDir"]] if ca.get("extraDir") else [],
        # Identity text is fixed for the session's lifetime and never mixes
        # in per-turn content (current time, etc.) — that stable prefix is
        # what lets Claude Code's own prompt caching help across turns. The
        # SDK doesn't expose raw cache_control breakpoints at this layer;
        # keeping this prefix byte-identical turn to turn is the whole lever.
        system_prompt=companion.get("system_prompt") or "You are a helpful assistant.",
        permission_mode="bypassPermissions",
        # No override at all when there's no key (the SDK requires a dict,
        # not None) — an empty dict still gets merged onto this process's
        # inherited environment, so the CLI falls back to its own stored
        # login (subscription or otherwise) exactly like running `claude`
        # directly in a terminal would.
        env={"ANTHROPIC_API_KEY": api_key} if api_key else {},
        resume=_sessions.get(companion["id"]),
    )

    final_result = None
    try:
        async for message in query(prompt=text, options=options):
            if isinstance(message, ResultMessage):
                if message.session_id:
                    _sessions[companion["id"]] = message.session_id
                final_result = f"Error: {message.result}" if message.is_error else message.result
    except Exception:
        # The CLI can report a structured error result (billing, max-turns,
        # rate limits, ...) and then still exit non-zero, which raises here
        # *after* we already captured that result above. Prefer the clear
        # message we already have over the SDK's generic trailing-crash text.
        if final_result is None:
            raise
    return str(final_result) if final_result else "No response."


def forget_session(companion_id: str) -> None:
    """Drops the cached session id so this companion's next turn starts a
    fresh Claude conversation instead of resuming — used when the user
    removes a companion via Settings."""
    _sessions.pop(companion_id, None)
