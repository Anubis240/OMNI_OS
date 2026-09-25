"""dev_agent — create a brand-new small project, get it running, open it.

1. One model call returns the whole project as JSON (files, requirements,
   how to run it).
2. Python projects get their own .venv for their requirements, so nothing
   is installed into the user's global Python.
3. Run it; on failure, send the real error output back and apply the files
   the model changes. A run that is still going at the timeout counts as
   started (servers and GUIs don't exit).

Installing packages and running generated code need the user's OK first
(security audit 2026-09-12, Critical #3).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from toolkit import llm, places, runner
from toolkit.base import ToolContext, register, S, I

_REPAIR_ROUNDS = 4

_SPEC = """Reply with JSON only:
{"name": "short_snake_case_name",
 "entry": "relative path of the file to run",
 "requirements": ["third-party packages, empty if none"],
 "files": {"relative/path.ext": "complete file contents", ...}}
Keep it small and complete: no placeholders or TODOs, every import resolvable."""


def projects_root() -> Path:
    return places.folder("desktop") / "Omni-OS Projects"


def _safe_relpath(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise ValueError(f"refusing file path {rel!r}")
    return p


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        dest = root / _safe_relpath(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def _read_project(root: Path) -> dict[str, str]:
    out = {}
    for f in root.rglob("*"):
        if f.is_file() and ".venv" not in f.parts and "node_modules" not in f.parts and f.stat().st_size < 60_000:
            try:
                out[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
    return out


def _python_env(root: Path, requirements: list[str], ctx: ToolContext) -> list[str] | None:
    """Create root/.venv, install requirements, return its python command."""
    base = runner.interpreter_for(Path("x.py"))
    if base is None:
        return None
    venv = root / ".venv"
    if not venv.exists():
        made = runner.run_command(base + ["-m", "venv", str(venv)], cwd=root, timeout=180)
        if not made.ok:
            ctx.log(f"[DevAgent] couldn't create a virtual environment: {made.output[-200:]}")
            return base
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if requirements:
        ctx.log(f"[DevAgent] installing {', '.join(requirements)}")
        installed = runner.run_command([str(py), "-m", "pip", "install", "--disable-pip-version-check", *requirements],
                                       cwd=root, timeout=600)
        if not installed.ok:
            ctx.log(f"[DevAgent] pip reported a problem: {installed.output[-300:]}")
    return [str(py)]


def _run(root: Path, entry: str, python: list[str] | None, timeout: int) -> runner.RunResult:
    target = root / _safe_relpath(entry)
    if target.suffix == ".py" and python:
        try:
            done = subprocess.run(python + [str(target)], cwd=root, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return runner.RunResult(True, f"(still running after {timeout}s — treated as started)")
        return runner.RunResult(done.returncode == 0, "\n".join(p for p in (done.stdout, done.stderr) if p))
    if target.suffix == ".html":
        return runner.RunResult(True, "(a web page — nothing to execute)")
    result = runner.run_file(target, timeout=timeout)
    if not result.ok and result.output.startswith("Stopped after"):
        return runner.RunResult(True, "(still running at the timeout — treated as started)")
    return result


def _open_editor(root: Path) -> None:
    code = shutil.which("code")
    if code:
        subprocess.Popen([code, str(root)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    elif sys.platform == "win32":
        import os
        os.startfile(root)


def build(description: str, language: str, name: str, timeout: int, ctx: ToolContext) -> str:
    ctx.log("[DevAgent] designing the project")
    spec = llm.ask_json(f"Create a {language} project that does this:\n{description}\n\n{_SPEC}")
    files = spec.get("files") or {}
    if not files:
        return "The model didn't produce any files — try describing the project differently."
    slug = re.sub(r"[^\w-]+", "_", name or spec.get("name") or "project").strip("_") or "project"
    root = projects_root() / slug
    n = 2
    while root.exists():
        root = projects_root() / f"{slug}_{n}"
        n += 1
    root.mkdir(parents=True)
    _write_files(root, files)
    entry = spec.get("entry") or next(iter(files))
    requirements = [r for r in spec.get("requirements") or [] if isinstance(r, str) and r.strip()]
    ctx.log(f"[DevAgent] wrote {len(files)} file(s) to {root}")

    python = _python_env(root, requirements, ctx) if entry.endswith(".py") else None
    _open_editor(root)

    for round_no in range(1, _REPAIR_ROUNDS + 1):
        ctx.log(f"[DevAgent] run {round_no}/{_REPAIR_ROUNDS}")
        result = _run(root, entry, python, timeout)
        if result.ok:
            return f"'{root.name}' works (after {round_no} run(s)). It's in {root}.\n{result.summary(800)}"
        if round_no == _REPAIR_ROUNDS:
            break
        fix = llm.ask_json(
            f"This project fails when run.\n\nError output:\n{result.output[-4000:]}\n\n"
            f"Project files:\n{json.dumps(_read_project(root))[:60000]}\n\n"
            "Reply with JSON {\"files\": {path: full new contents}, \"requirements\": [extra packages]} "
            "containing only the files that must change."
        )
        _write_files(root, fix.get("files") or {})
        extra = [r for r in fix.get("requirements") or [] if isinstance(r, str) and r.strip()]
        if extra and python:
            _python_env(root, extra, ctx)
    return f"'{root.name}' still fails after {_REPAIR_ROUNDS} runs. It's saved in {root}.\n{result.summary(800)}"


@register(
    "dev_agent",
    "Builds a BRAND NEW small project from scratch: writes the files, installs its "
    "packages into the project's own environment, opens it in the editor, and runs "
    "and repairs it until it works. Never for an EXISTING project in the user's "
    "files — use claude_agent for those.",
    {
        "description": S("Plain-language description of the program to build"),
        "language": S("Programming language (default python)"),
        "project_name": S("Optional folder name"),
        "timeout": I("Seconds each test run may take (default 30)"),
    },
    ["description"],
)
def dev_agent(args: dict, ctx: ToolContext) -> str:
    description = (args.get("description") or "").strip()
    if not description:
        return "Tell me what the new program should do and I'll build it."
    ask = getattr(ctx.ui, "confirm_action", None)
    if not (callable(ask) and ask(f"Build a new project (\"{description[:80]}\")? This installs packages "
                                  f"and runs code on your computer.")):
        return "Not built — the user didn't confirm."
    try:
        return build(description, (args.get("language") or "python").strip() or "python",
                     (args.get("project_name") or "").strip(), int(args.get("timeout") or 30), ctx)
    except Exception as err:
        return f"The build stopped: {err}"
