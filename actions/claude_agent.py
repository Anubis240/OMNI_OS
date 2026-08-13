import asyncio

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

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


async def claude_agent(parameters=None, player=None, speak=None) -> str:
    """Delegates a request to Claude (Anthropic) via the Claude Agent SDK,
    scoped to a working directory the user configures in Settings (see
    settings_panel.py's CLAUDE CODE DELEGATION section) — off, and declining
    politely, until they set it up. Use for real coding/project work or
    anything referencing notes, memory, or specific projects, rather than
    answering from general knowledge.

    The SDK still spawns the Claude Code CLI as a subprocess under the hood
    (cli_path points it at the same install the old CLI-shell version used),
    but drives it through typed options and structured messages instead of
    building argv strings and parsing stdout text.
    """
    parameters = parameters or {}
    request = (parameters.get("request") or "").strip()
    if not request:
        return "No request was given to the Claude agent."

    settings = settings_store.load_settings()
    ca = settings["claude_agent"]
    if not ca.get("enabled") or not ca.get("cliPath"):
        return (
            "Claude Code delegation isn't set up yet. Sir can enable it and point it "
            "at a Claude CLI install from the Settings panel."
        )

    # No API key configured is not an error — it means "use whatever Claude
    # Code login is already active on this machine" (subscription or
    # otherwise), same as the original CLI shell-out did. An explicit key
    # in Settings still wins when present, for anyone who wants metered
    # API billing instead of their subscription.
    api_key = settings["api_keys"].get("anthropic") or None

    options = ClaudeAgentOptions(
        cli_path=ca["cliPath"],
        cwd=ca.get("vaultDir") or None,
        add_dirs=[ca["extraDir"]] if ca.get("extraDir") else [],
        system_prompt={"type": "preset", "preset": "claude_code", "append": SYSTEM_NOTE},
        # No human is present to approve tool calls in a headless voice-assistant
        # invocation — same trust model as the old CLI's --dangerously-skip-permissions.
        permission_mode="bypassPermissions",
        # No override at all when there's no key (the SDK requires a dict,
        # not None) — an empty dict still gets merged onto this process's
        # inherited environment, so the CLI falls back to its own stored
        # login instead of requiring a separate paid API key.
        env={"ANTHROPIC_API_KEY": api_key} if api_key else {},
    )

    async def run() -> str:
        final_result = None
        try:
            async for message in query(prompt=request, options=options):
                if isinstance(message, ResultMessage):
                    final_result = f"Claude agent error: {message.result}" if message.is_error else message.result
        except Exception:
            # The CLI can report a structured error result (billing, max-turns,
            # rate limits, ...) and then still exit non-zero, which raises here
            # *after* we already captured that result above. Prefer the clear
            # message we already have over the SDK's generic trailing-crash text.
            if final_result is None:
                raise
        return str(final_result) if final_result else "Claude agent returned no output."

    try:
        timeout = int(parameters.get("timeout", DEFAULT_TIMEOUT))
        return await asyncio.wait_for(run(), timeout=timeout)
    except asyncio.TimeoutError:
        return "Claude agent timed out before finishing."
    except FileNotFoundError:
        return "Sir, the configured Claude CLI path doesn't exist — check it in Settings."
    except Exception as e:
        return f"Claude agent error: {e}"
