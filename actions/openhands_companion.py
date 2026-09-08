"""Turn-based conversation driver for openhands_agent-backend companions —
same public shape as actions/codex_companion.py (send/forget_session/
get_status), so main.py can dispatch to it identically. Drives the
OpenHands CLI (docs.openhands.dev) directly via subprocess + its
`--headless --json` mode.

Flags verified against OpenHands' own headless-mode docs
(docs.openhands.dev/openhands/usage/cli/headless, fetched 2026-09-09):
`openhands --headless -t "<task>"` for a one-shot task, `--json` for
streaming JSONL events, and headless mode always runs in "always-approve"
mode (no confirmation prompts — there's no flag to turn this off, so
nothing to configure here, unlike claude_agent's bypassPermissions or
codex_agent's --ask-for-approval never).

No session/resume support here — deliberately, not by oversight. The
official docs page above does not document a resume flag or conversation
id at all; a secondary community source mentions a `--resume` flag, but
without a confirmed exact syntax (does it take a conversation id? how is
that id surfaced from --json output?) writing code against it would be a
guess, which this project's established discipline is to avoid (see
codex_companion.py's docstring for the same standard applied to that
CLI). Every turn here is therefore a fresh, independent OpenHands task —
each companion still gets a real reply, just without cross-turn memory
inside OpenHands itself. Revisit if OpenHands publishes a documented
resume flag.

--json's exact event schema also isn't documented on that page beyond
"streaming JSONL events" — this module parses defensively: any JSON object
with a plausible message/content/text string field is collected, and if
none parse as useful JSON at all, the raw stdout is returned instead of an
empty "No response." (verified schemas, like Codex's, get exact field
lookups; unverified ones get best-effort extraction plus a stdout
fallback, not silence).
"""

import json
import subprocess

from core import settings_store

TIMEOUT = 180

_status: dict[str, str] = {}


def get_status(companion_id: str) -> str:
    return _status.get(companion_id, "idle")


async def send(companion: dict, text: str) -> str:
    import asyncio

    settings = settings_store.load_settings()
    ca = settings.get("openhands_agent", {})
    if not ca.get("enabled") or not ca.get("cliPath"):
        return "OpenHands delegation isn't set up yet — enable it and set a CLI path in Settings."

    companion_id = companion["id"]
    _status[companion_id] = "running"
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run_openhands, companion, text, ca), timeout=TIMEOUT
        )
    except asyncio.TimeoutError:
        return "Timed out before finishing."
    except Exception as e:
        return f"Error: {e}"
    finally:
        _status[companion_id] = "idle"


def _run_openhands(companion: dict, text: str, ca: dict) -> str:
    cli_path = ca["cliPath"]
    cwd = ca.get("vaultDir") or None

    identity = (companion.get("system_prompt") or "").strip()
    task = f"{identity}\n\nUser request:\n{text}" if identity else text

    argv = [cli_path, "--headless", "--json", "-t", task]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return "Timed out before finishing."
    except FileNotFoundError:
        return "The configured OpenHands CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Error: {e}"

    reply_parts: list[str] = []
    any_json_parsed = False
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        any_json_parsed = True
        if not isinstance(event, dict):
            continue
        for field in ("message", "content", "text"):
            value = event.get(field)
            if isinstance(value, str) and value.strip():
                reply_parts.append(value.strip())
                break

    if reply_parts:
        return "\n".join(reply_parts)
    if any_json_parsed:
        # Parsed JSON events but none carried a recognized text field —
        # rather than guess further at the schema, hand back whatever
        # OpenHands printed so the answer isn't silently lost.
        return proc.stdout.strip()[:2000] or "No response."
    if proc.returncode != 0:
        return f"Error: openhands exited with code {proc.returncode}. {proc.stderr.strip()[:500]}"
    return proc.stdout.strip()[:2000] or "No response."


def forget_session(companion_id: str) -> None:
    """No-op — this backend keeps no cross-turn state to forget (see module
    docstring). Exists only so main.py/settings_panel.py/world_panel.py can
    call it unconditionally, same as every other companion backend."""
    pass
