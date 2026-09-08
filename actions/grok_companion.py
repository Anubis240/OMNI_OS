"""Turn-based conversation driver for grok_agent-backend companions — same
public shape as actions/codex_companion.py (send/forget_session/
get_status), so main.py can dispatch to it identically. Drives xAI's Grok
Build CLI directly via subprocess.

CONFIDENCE NOTE: unlike codex_companion.py (verified against OpenAI's own
docs) and opencode_companion.py/openhands_companion.py (verified against
each project's official docs site), Grok Build has no single official CLI
reference this module could be checked against as of 2026-09-09 — the
flags below come from community sources (toolsbase.dev's cheat sheet,
codeagentswarm.com's guide) that agree with each other but aren't the
vendor's own documentation. Specifically:
  - `-p "<prompt>"` (alias `--single`) for a one-shot headless prompt.
  - `--continue`/`-c` to continue the most recent session for the current
    working directory (same cwd-scoped semantics as opencode_companion.py
    uses `--continue` for — plausible given Grok Build's permission-mode
    names below are literally borrowed from Claude Code, suggesting the
    whole CLI UX is modeled on it, but not independently confirmed here).
  - `--permission-mode bypassPermissions` to run without interactive
    approval prompts — again, a Claude Code-style flag name, used the same
    way claude_companion.py uses the SDK's own bypassPermissions mode.
Run `grok --help` against the actual installed CLI before depending on
this in production; adjust the argv below if it disagrees.

Session tracking mirrors opencode_companion.py exactly: a per-companion
"has this companion sent a first turn yet" flag, not a captured session
id, for the same reason (no verified way to extract one from output).
"""

import asyncio
import subprocess

from core import settings_store

TIMEOUT = 180

_started: dict[str, bool] = {}
_status: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    settings = settings_store.load_settings()
    ca = settings.get("grok_agent", {})
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "Grok Build delegation isn't set up yet — enable it and set a CLI path in Settings."

    companion_id = companion["id"]
    lock = _locks.setdefault(companion_id, asyncio.Lock())
    async with lock:
        _status[companion_id] = "running"
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_grok, companion, text, ca), timeout=TIMEOUT
            )
        except asyncio.TimeoutError:
            return "Timed out before finishing."
        except Exception as e:
            return f"Error: {e}"
        finally:
            _status[companion_id] = "idle"


def _run_grok(companion: dict, text: str, ca: dict) -> str:
    companion_id = companion["id"]
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None
    is_first_turn = not _started.get(companion_id)

    prompt = text
    if is_first_turn:
        identity = (companion.get("system_prompt") or "").strip()
        if identity:
            prompt = f"{identity}\n\nUser request:\n{text}"

    argv = [cli_path, "-p", prompt, "--permission-mode", "bypassPermissions"]
    if not is_first_turn:
        argv.append("--continue")

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return "Timed out before finishing."
    except FileNotFoundError:
        return "The configured Grok Build CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Error: {e}"

    _started[companion_id] = True

    reply = proc.stdout.strip()
    if reply:
        return reply
    if proc.returncode != 0:
        return f"Error: grok exited with code {proc.returncode}. {proc.stderr.strip()[:500]}"
    return "No response."


def forget_session(companion_id: str) -> None:
    _started.pop(companion_id, None)
    _locks.pop(companion_id, None)
