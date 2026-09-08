"""Turn-based conversation driver for blackbox_agent-backend companions —
same public shape as actions/codex_companion.py (send/forget_session/
get_status), so main.py can dispatch to it identically. Drives the
Blackbox AI CLI directly via subprocess.

CONFIDENCE NOTE — lowest of the four new CLI backends added 2026-09-09.
Blackbox's own docs (docs.blackbox.ai/features/blackbox-cli/commands-
reference, fetched 2026-09-09) confirm a `-p` headless one-shot flag exists
but do not spell out its exact syntax, and document no resume/session
flag, no JSON output flag, and no working-directory flag. Rather than
invent any of those, this module:
  - Uses only `-p "<prompt>"`, the one flag the docs actually confirm.
  - Passes the working directory via subprocess's own `cwd=`, not a CLI
    flag (unverified whether one exists).
  - Does NOT attempt multi-turn continuity — every call is independent,
    same reasoning as openhands_companion.py's no-resume decision. The
    docs mention `blackbox session`/`/tasks resume <task-id>` for its own
    *interactive* chat mode, but nothing that maps cleanly onto a single
    non-interactive `-p` call, so implementing "resume" here would be a
    guess about a mechanism this module never actually uses.
Run `blackbox --help` against the actual installed CLI before depending on
this in production — this is the module most likely to need adjustment
once real docs/behavior are available.
"""

import asyncio
import subprocess

from core import settings_store

TIMEOUT = 180

_status: dict[str, str] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    settings = settings_store.load_settings()
    ca = settings.get("blackbox_agent", {})
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "Blackbox delegation isn't set up yet — enable it and set a CLI path in Settings."

    companion_id = companion["id"]
    _status[companion_id] = "running"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_blackbox, companion, text, ca), timeout=TIMEOUT
        )
    except asyncio.TimeoutError:
        return "Timed out before finishing."
    except Exception as e:
        return f"Error: {e}"
    finally:
        _status[companion_id] = "idle"


def _run_blackbox(companion: dict, text: str, ca: dict) -> str:
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None

    identity = (companion.get("system_prompt") or "").strip()
    prompt = f"{identity}\n\nUser request:\n{text}" if identity else text

    argv = [cli_path, "-p", prompt]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return "Timed out before finishing."
    except FileNotFoundError:
        return "The configured Blackbox CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Error: {e}"

    reply = proc.stdout.strip()
    if reply:
        return reply
    if proc.returncode != 0:
        return f"Error: blackbox exited with code {proc.returncode}. {proc.stderr.strip()[:500]}"
    return "No response."


def forget_session(companion_id: str) -> None:
    """No-op — this backend keeps no cross-turn state (see module
    docstring). Exists so callers can call it unconditionally like every
    other companion backend's forget_session."""
    pass
