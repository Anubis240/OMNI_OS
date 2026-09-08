"""Turn-based conversation driver for codex_agent-backend companions —
same public shape as actions/claude_companion.py (send/forget_session/
get_status), so main.py can dispatch to either one identically. Drives
OpenAI's Codex CLI directly via subprocess + its `--json` NDJSON event
stream, since there's no Codex equivalent of the (Anthropic-specific)
claude_agent_sdk package claude_companion.py uses.

Verified against OpenAI's own docs (developers.openai.com/codex/noninteractive,
/codex/agent-approvals-security) — the flags and event schema below are
sourced from there, not inferred. Two things fixed 2026-09-08 after a Codex
code review found them, each re-verified independently rather than taken on
the review's word:

  1. `--cd` (and every other global option) is a `codex exec`-level flag —
     it must come BEFORE `resume <id> <prompt>`, not after. A live CLI test
     (0.153.4) confirmed the old ordering below actually failed whenever
     vaultDir was configured on a resumed turn:
       codex exec --json --skip-git-repo-check --cd <dir> resume <id> "<prompt>"
     (was: `... resume <id> "<prompt>" ... --cd <dir>` — rejected outright.)

  2. `--dangerously-bypass-approvals-and-sandbox` (aka `--yolo`) is
     documented as "No sandbox; no approvals (not recommended)" — genuinely
     unrestricted machine access, a materially bigger blast radius than
     claude_agent's bypassPermissions mode this was modeled on. The
     review's suggested replacement, `--approve-for-me`, isn't a real
     documented flag (checked — absent from both the noninteractive and
     approvals-security docs). The actual documented non-interactive-safe
     combination is `--sandbox workspace-write --ask-for-approval never`:
     never blocks on a prompt (no hang risk with no human present, same
     requirement the old flag was trying to satisfy), but keeps filesystem
     access scoped to the working directory instead of the whole machine.

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

# companion id -> asyncio.Lock, so two overlapping voice/text turns for the
# SAME companion can't both resume (or both start) its shared Codex thread
# id concurrently — found in review: without this, the first turn to finish
# could also flip _status back to "idle" while the other was still running.
_locks: dict[str, asyncio.Lock] = {}


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

    companion_id = companion["id"]
    lock = _locks.setdefault(companion_id, asyncio.Lock())
    async with lock:
        _status[companion_id] = "running"
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_codex, companion, text, ca), timeout=TIMEOUT
            )
        except asyncio.TimeoutError:
            return "Timed out before finishing."
        except Exception as e:
            return f"Error: {e}"
        finally:
            _status[companion_id] = "idle"


def _run_codex(companion: dict, text: str, ca: dict) -> str:
    companion_id = companion["id"]
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None
    thread_id = _sessions.get(companion_id)

    # Global options (--json, --skip-git-repo-check, --cd, sandbox/approval)
    # must all precede the subcommand/prompt — codex exec rejects --cd (and
    # presumably any other global flag) placed after `resume`. See the
    # module docstring for the live-tested confirmation.
    argv = [cli_path, "exec", "--json", "--skip-git-repo-check"]
    if cwd:
        argv += ["--cd", cwd]
    # Never blocks on an approval prompt (no human is present to answer one
    # in a headless voice-assistant invocation), while keeping filesystem
    # access scoped to the working directory — see module docstring for why
    # this replaced the old unrestricted bypass flag.
    argv += ["--sandbox", "workspace-write", "--ask-for-approval", "never"]

    prompt = text
    if not thread_id:
        # Only the first turn of a new thread needs the persona — a resumed
        # thread already has it from that first turn's context, and Codex
        # has no documented per-turn "developer instruction" flag to prefer
        # over this composition (checked against the same docs above).
        identity = (companion.get("system_prompt") or "").strip()
        if identity:
            prompt = f"{identity}\n\nUser request:\n{text}"

    if thread_id:
        argv += ["resume", thread_id, prompt]
    else:
        argv += [prompt]

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
    _locks.pop(companion_id, None)  # don't accumulate lock objects for
    # companions that get deleted and never recreated with the same id.
