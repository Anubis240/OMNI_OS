"""Turn-based conversation driver for codex_agent-backend companions —
same public shape as actions/claude_companion.py (send/forget_session/
get_status), so main.py can dispatch to either one identically. Drives
OpenAI's Codex CLI directly via subprocess + its `--json` NDJSON event
stream, since there's no Codex equivalent of the (Anthropic-specific)
claude_agent_sdk package claude_companion.py uses.

Verified against OpenAI's own docs before writing this (developers.openai.com/
codex/noninteractive, /codex/cli/reference) rather than guessed — the flags and
event schema below are sourced from there, not inferred:

  codex exec "<prompt>" --cd <dir> --sandbox workspace-write \
      --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json
  codex exec resume <THREAD_ID> "<prompt>" --cd <dir> ... --json

--json streams one JSON object per line:
  {"type": "thread.started", "thread_id": "..."}
  {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}
  {"type": "turn.failed", "error": {"message": "..."}}
  {"type": "error", "message": "..."}
"""

import asyncio
import json
import subprocess

from core import settings_store

TIMEOUT = 180

# companion id -> last Codex thread_id, for multi-turn continuity within
# this run of the app — same lifetime/reset behavior as claude_companion's
# _sessions (not persisted across restarts).
_sessions: dict[str, str] = {}

# companion id -> "idle" | "running", polled by the World view to show live
# status badges — same contract as claude_companion.get_status.
_status: dict[str, str] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    """Sends one turn to a codex_agent-backend companion's own Codex
    session, returning its reply text. Each companion keeps its own thread
    (keyed by companion id) via `codex exec resume`, mirroring
    claude_companion.send's per-companion session isolation."""
    settings = settings_store.load_settings()
    ca = settings.get("codex_agent", {})
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "Codex delegation isn't set up yet — enable it and set a CLI path in Settings."

    _status[companion["id"]] = "running"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_codex, companion["id"], text, ca), timeout=TIMEOUT
        )
    except asyncio.TimeoutError:
        return "Timed out before finishing."
    except Exception as e:
        return f"Error: {e}"
    finally:
        _status[companion["id"]] = "idle"


def _run_codex(companion_id: str, text: str, ca: dict) -> str:
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None
    thread_id = _sessions.get(companion_id)

    # Positional args first (prompt, or "resume <id> <prompt>"), matching
    # the exact ordering in OpenAI's own documented examples, then flags.
    if thread_id:
        argv = [cli_path, "exec", "resume", thread_id, text]
    else:
        argv = [cli_path, "exec", text]
    argv += ["--json", "--skip-git-repo-check"]
    if cwd:
        argv += ["--cd", cwd]
    # No human is present to approve commands in a headless voice-assistant
    # invocation — same trust model as claude_agent's permission_mode=
    # "bypassPermissions" (see actions/claude_agent.py).
    argv += ["--dangerously-bypass-approvals-and-sandbox"]

    try:
        # A second, inner timeout on top of the caller's asyncio.wait_for:
        # that one unblocks the async caller after TIMEOUT regardless, but
        # can't kill a blocking subprocess running on a worker thread —
        # this one ensures the OS process itself actually gets terminated
        # rather than orphaned. (A prior bug in a different tool shipped
        # with no timeout at all here and hung 30+ minutes on a stuck
        # connection — not repeating that.)
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "Timed out before finishing."
    except FileNotFoundError:
        return "The configured Codex CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Error: {e}"

    reply_parts: list[str] = []
    error_msg: str | None = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # Codex can interleave non-JSON banner lines; skip rather than fail
        etype = event.get("type")
        if etype == "thread.started" and event.get("thread_id"):
            _sessions[companion_id] = event["thread_id"]
        elif etype == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                reply_parts.append(item["text"])
        elif etype == "turn.failed":
            error_msg = (event.get("error") or {}).get("message") or "turn failed"
        elif etype == "error":
            error_msg = event.get("message") or "error"

    if reply_parts:
        return "\n".join(reply_parts)
    if error_msg:
        return f"Error: {error_msg}"
    if proc.returncode != 0:
        return f"Error: codex exited with code {proc.returncode}. {proc.stderr.strip()[:500]}"
    return "No response."


def forget_session(companion_id: str) -> None:
    """Drops the cached thread id so this companion's next turn starts a
    fresh Codex conversation instead of resuming — used when the user
    removes a companion via Settings. Same contract as
    claude_companion.forget_session."""
    _sessions.pop(companion_id, None)
