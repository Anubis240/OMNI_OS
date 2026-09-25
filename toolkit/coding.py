"""code_helper — write, change, explain, run or repair a single source file.

Running or building executes real code on the user's machine, so both
need a real confirmation first (security audit 2026-09-12, Critical #3).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from toolkit import llm, places, runner
from toolkit.base import ToolContext, register, S, I

_EXTENSIONS = {
    "python": ".py", "javascript": ".js", "typescript": ".ts", "html": ".html",
    "css": ".css", "java": ".java", "c": ".c", "c++": ".cpp", "cpp": ".cpp",
    "c#": ".cs", "go": ".go", "rust": ".rs", "bash": ".sh", "shell": ".sh",
    "powershell": ".ps1", "sql": ".sql", "ruby": ".rb", "php": ".php",
}
_REPAIR_ROUNDS = 3


def _only_code(reply: str) -> str:
    """The model sometimes wraps code in a fence or adds a sentence; keep the code."""
    fenced = re.findall(r"```[\w+#-]*\n(.*?)```", reply, re.DOTALL)
    return max(fenced, key=len).strip() + "\n" if fenced else llm.strip_fence(reply) + "\n"


def _generate(instruction: str, language: str, current: str | None = None) -> str:
    system = (f"You write {language} code. Reply with the complete file contents only — "
              "no commentary before or after.")
    if current is None:
        prompt = f"Write a complete, runnable {language} program that does this:\n{instruction}"
    else:
        prompt = f"Here is a file:\n\n{current}\n\nRewrite it in full with this change:\n{instruction}"
    return _only_code(llm.ask(prompt, system=system))


def _save_target(args: dict, language: str, description: str) -> Path:
    wanted = (args.get("output_path") or "").strip()
    if wanted:
        return places.resolve(wanted)
    stem = re.sub(r"[^a-z0-9]+", "_", description.lower()).strip("_")[:40] or "program"
    return places.folder("desktop") / f"{stem}{_EXTENSIONS.get(language.lower(), '.txt')}"


def _existing(args: dict) -> Path | None:
    raw = (args.get("file_path") or "").strip()
    return places.resolve(raw) if raw else None


def _confirmed(ctx: ToolContext, what: str) -> bool:
    ask = getattr(ctx.ui, "confirm_action", None)
    return bool(callable(ask) and ask(f"Run {what}? This executes real code on your computer."))


def _write(args, ctx, language, description):
    if not description:
        return "Tell me what the code should do."
    code = _generate(description, language)
    dest = _save_target(args, language, description)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(code, encoding="utf-8")
    first_lines = "\n".join(code.splitlines()[:12])
    return f"Saved to: {dest}\n\n{first_lines}"


def _edit(args, ctx, language, description, *, goal=None):
    path = _existing(args)
    if not path or not path.is_file():
        return "Which file should I change? I need its path."
    if not description and goal is None:
        return "What change should I make?"
    original = path.read_text(encoding="utf-8", errors="replace")
    change = goal or description
    updated = _generate(change, language, current=original)
    backup = path.with_name(path.name + ".bak")
    backup.write_text(original, encoding="utf-8")
    path.write_text(updated, encoding="utf-8")
    return (f"Updated {path} ({len(original.splitlines())} → {len(updated.splitlines())} lines). "
            f"The previous version is in {backup.name}.")


def _explain(args, ctx, language, description):
    path = _existing(args)
    source = path.read_text(encoding="utf-8", errors="replace") if path and path.is_file() else (args.get("code") or "")
    if not source.strip():
        return "Give me a file path or paste the code to explain."
    focus = f" Focus on: {description}." if description else ""
    return llm.ask(f"Explain what this code does in plain language, in a short paragraph.{focus}\n\n{source[:12000]}")


def _run(args, ctx, language, description):
    path = _existing(args)
    if not path or not path.is_file():
        return "Which file should I run?"
    if not _confirmed(ctx, str(path)):
        return "Not run — the user didn't confirm."
    result = runner.run_file(path, (args.get("args") or "").split(), int(args.get("timeout") or 30))
    return result.summary()


def _build(args, ctx, language, description):
    """Write (or take) a program, run it, and let the model repair it from
    the real error output until it runs cleanly or we give up."""
    path = _existing(args)
    if path is None or not path.is_file():
        if not description:
            return "Tell me what to build."
        path = _save_target(args, language, description)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_generate(description, language), encoding="utf-8")
    if not _confirmed(ctx, f"and repair {path}"):
        return f"The code is saved at {path}, but I didn't run it — the user didn't confirm."
    timeout = int(args.get("timeout") or 30)
    for round_no in range(1, _REPAIR_ROUNDS + 1):
        ctx.log(f"[Code] run {round_no}/{_REPAIR_ROUNDS}: {path.name}")
        result = runner.run_file(path, (args.get("args") or "").split(), timeout)
        if result.ok:
            return f"{path} runs cleanly (round {round_no}).\n{result.summary()}"
        if round_no == _REPAIR_ROUNDS:
            break
        source = path.read_text(encoding="utf-8", errors="replace")
        path.write_text(_generate(
            f"It fails with this output — fix the cause:\n{result.output[-3000:]}\n"
            f"(The program's purpose: {description or 'as written'})", language, current=source), encoding="utf-8")
    return f"It still fails after {_REPAIR_ROUNDS} rounds. Last output:\n{result.summary()}"


def _optimize(args, ctx, language, description):
    return _edit(args, ctx, language, description,
                 goal="Make it cleaner and faster without changing what it does. " + (description or ""))


def _debug_screen(args, ctx, language, description):
    import pyautogui
    shot = io.BytesIO()
    pyautogui.screenshot().save(shot, format="PNG")
    path = _existing(args)
    extra = ""
    if path and path.is_file():
        extra = f"\n\nThe related source file ({path.name}):\n{path.read_text(encoding='utf-8', errors='replace')[:8000]}"
    return llm.ask([llm.image_part(shot.getvalue()),
                    (description or "What's the error on screen and how do I fix it?") + extra],
                   system="You are debugging with the user. Quote the exact error text you can see, "
                          "explain the cause briefly, and give the concrete fix.")


_ACTIONS = {"write": _write, "edit": _edit, "explain": _explain, "run": _run,
            "build": _build, "optimize": _optimize, "screen_debug": _debug_screen}


def _pick_action(args: dict, description: str) -> str:
    path = _existing(args)
    words = description.lower()
    if "screen" in words or "screenshot" in words:
        return "screen_debug"
    if path and path.is_file():
        if any(w in words for w in ("run", "execute", "start")):
            return "run"
        return "edit" if description else "explain"
    if args.get("code"):
        return "explain"
    return "write"


@register(
    "code_helper",
    "Works on a single PROGRAMMING source file (Python, JavaScript, HTML, etc.): write "
    "new code, edit or optimize an existing file, explain code, run it, build-and-fix "
    "until it runs, or debug an error visible on screen. Never for prose, stories, "
    "poems or other writing — answer those directly.",
    {
        "action": S("write | edit | explain | run | build | optimize | screen_debug | auto (default)"),
        "description": S("In plain words: the program wanted, or how the existing file should change"),
        "language": S("Programming language (default python)"),
        "output_path": S("Where to save new code"),
        "file_path": S("Path of a source file already on disk, for edit, explain, run or build"),
        "code": S("Code text to explain"),
        "args": S("Command-line arguments for run/build"),
        "timeout": I("Seconds a run may take (default 30)"),
    },
    ["action"],
)
def code_helper(args: dict, ctx: ToolContext) -> str:
    description = (args.get("description") or args.get("instruction") or "").strip()
    language = (args.get("language") or "python").strip() or "python"
    action = (args.get("action") or "auto").strip().lower()
    if action == "auto":
        action = _pick_action(args, description)
    handler = _ACTIONS.get(action)
    if handler is None:
        return f"Unknown code action {action!r}. Options: {', '.join(_ACTIONS)}."
    ctx.log(f"[Code] {action}")
    try:
        return handler(args, ctx, language, description)
    except Exception as err:
        return f"The {action} step failed: {err}"
