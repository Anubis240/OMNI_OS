"""Turn-based conversation driver for opencode_agent-backend companions —
same public shape as actions/codex_companion.py (send/forget_session/
get_status), so main.py can dispatch to it identically. Drives the OpenCode
CLI (opencode.ai) directly via subprocess.

Flags verified against OpenCode's own CLI docs (opencode.ai/docs/cli/,
fetched 2026-09-09): `opencode run [message]` for a one-shot prompt,
`--dir <path>` for working directory, `--continue`/`-c` to continue the
most recent session, `--session <id>`/`-s` to resume a specific one.

Session tracking is deliberately NOT a thread/session id string like
codex_companion.py's — it's a per-companion "has this companion sent a
first turn yet" flag. Two reasons:
  1. OpenCode's docs describe `--continue` as resuming "the most recent
     session for the current working directory" — i.e. continuity here is
     scoped by --dir, not by an id this module would have to capture from
     output. Since every companion using this backend shares the one
     configured vaultDir (opencode_agent.vaultDir in Settings), --continue
     resumes the RIGHT conversation as long as only one opencode_agent
     companion is actively used against that directory at a time.
  2. Getting a real per-companion session id would mean parsing
     `opencode run --format json` output, and that JSON event schema isn't
     independently verified the way Codex's NDJSON schema was (see
     codex_companion.py's docstring) — rather than guess at unverified
     field names, this module runs in plain-text output mode and skips
     `--format json` entirely, reading stdout directly as the reply.

If more than one opencode_agent companion is ever run against the same
vaultDir, `--continue` could resume the wrong one's thread — a real but
narrow limitation, not a silent guess. Give each such companion its own
vaultDir if that matters.
"""

import asyncio
import subprocess

from core import settings_store

TIMEOUT = 180

# companion id -> True once its first turn has been sent — see module
# docstring for why this is a flag, not a captured session id.
_started: dict[str, bool] = {}

_status: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    settings = settings_store.load_settings()
    ca = settings.get("opencode_agent", {})
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "OpenCode delegation isn't set up yet — enable it and set a CLI path in Settings."

    companion_id = companion["id"]
    lock = _locks.setdefault(companion_id, asyncio.Lock())
    async with lock:
        _status[companion_id] = "running"
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_opencode, companion, text, ca), timeout=TIMEOUT
            )
        except asyncio.TimeoutError:
            return "Timed out before finishing."
        except Exception as e:
            return f"Error: {e}"
        finally:
            _status[companion_id] = "idle"


def _run_opencode(companion: dict, text: str, ca: dict) -> str:
    companion_id = companion["id"]
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None
    is_first_turn = not _started.get(companion_id)

    argv = [cli_path, "run"]
    if cwd:
        argv += ["--dir", cwd]
    if not is_first_turn:
        argv += ["--continue"]

    prompt = text
    if is_first_turn:
        identity = (companion.get("system_prompt") or "").strip()
        if identity:
            prompt = f"{identity}\n\nUser request:\n{text}"

    argv.append(prompt)

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return "Timed out before finishing."
    except FileNotFoundError:
        return "The configured OpenCode CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Error: {e}"

    _started[companion_id] = True

    reply = proc.stdout.strip()
    if reply:
        return reply
    if proc.returncode != 0:
        return f"Error: opencode exited with code {proc.returncode}. {proc.stderr.strip()[:500]}"
    return "No response."


def forget_session(companion_id: str) -> None:
    _started.pop(companion_id, None)
    _locks.pop(companion_id, None)
