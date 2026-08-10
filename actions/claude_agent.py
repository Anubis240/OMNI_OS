import subprocess

from core import settings_store

DEFAULT_TIMEOUT = 180

SYSTEM_NOTE = (
    "You are being invoked headlessly (non-interactively) by a voice assistant "
    "on the user's behalf, and must return output promptly. If the task requires "
    "starting a long-running background process (a dev server, 'npm run dev', "
    "a watcher, etc.), you MUST launch it detached/non-blocking — e.g. PowerShell "
    "Start-Process with -WindowStyle Hidden, redirecting output to a log file — "
    "so your own process can finish and report back immediately. NEVER run a "
    "long-lived server in the foreground of a tool call; it will never return and "
    "you will time out. After launching, verify it actually started (check the "
    "log or port), then report the local URL back in one short sentence."
)


def claude_agent(parameters=None, player=None, speak=None):
    """Delegates a request to Claude (Anthropic) running as a headless
    Claude Code CLI session, scoped to a working directory the user
    configures in Settings (see settings_panel.py's CLAUDE CODE DELEGATION
    section) — off, and declining politely, until they set it up.
    Use for real coding/project work or anything referencing notes,
    memory, or specific projects, rather than answering from general
    knowledge.
    """
    parameters = parameters or {}
    request = (parameters.get("request") or "").strip()
    if not request:
        return "No request was given to the Claude agent."

    ca = settings_store.load_settings()["claude_agent"]
    if not ca.get("enabled") or not ca.get("cliPath"):
        return (
            "Claude Code delegation isn't set up yet. Sir can enable it and point it "
            "at a Claude CLI install from the Settings panel."
        )

    cli_path = ca["cliPath"]
    vault_dir = ca.get("vaultDir") or None
    extra_dir = ca.get("extraDir") or None

    cmd = [cli_path, "-p", request, "--dangerously-skip-permissions",
           "--append-system-prompt", SYSTEM_NOTE, "--output-format", "text"]
    if extra_dir:
        cmd[3:3] = ["--add-dir", extra_dir]

    try:
        result = subprocess.run(
            cmd,
            cwd=vault_dir,
            capture_output=True,
            text=True,
            timeout=int(parameters.get("timeout", DEFAULT_TIMEOUT)),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return output or "Claude agent returned no output."
    except subprocess.TimeoutExpired:
        return "Claude agent timed out before finishing."
    except FileNotFoundError:
        return "Sir, the configured Claude CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Claude agent error: {e}"
