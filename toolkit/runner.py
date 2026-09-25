"""Run a source file with a real interpreter from PATH.

The installed app is a frozen executable, so sys.executable is Omni-OS
itself, not Python — scripts must be run with whatever the user has
installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python() -> list[str] | None:
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    for name in (("py", "-3"), ("python",), ("python3",)):
        if shutil.which(name[0]):
            return list(name)
    return None


_INTERPRETERS = {
    ".py": _python,
    ".js": lambda: ["node"] if shutil.which("node") else None,
    ".mjs": lambda: ["node"] if shutil.which("node") else None,
    ".ts": lambda: ["npx", "--yes", "tsx"] if shutil.which("npx") else None,
    ".ps1": lambda: ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"],
    ".sh": lambda: ["bash"] if shutil.which("bash") else None,
    ".rb": lambda: ["ruby"] if shutil.which("ruby") else None,
    ".php": lambda: ["php"] if shutil.which("php") else None,
}


@dataclass
class RunResult:
    ok: bool
    output: str

    def summary(self, limit: int = 3000) -> str:
        text = self.output.strip() or "(no output)"
        if len(text) > limit:
            text = "…" + text[-limit:]
        return ("Ran successfully." if self.ok else "It failed.") + "\n" + text


def interpreter_for(path: Path) -> list[str] | None:
    finder = _INTERPRETERS.get(path.suffix.lower())
    return finder() if finder else None


def run_file(path: Path, args: list[str] | None = None, timeout: int = 30) -> RunResult:
    cmd = interpreter_for(path)
    if cmd is None:
        what = "Python" if path.suffix == ".py" else f"a program that runs {path.suffix} files"
        return RunResult(False, f"I can't run this: {what} isn't installed on this computer.")
    try:
        done = subprocess.run(cmd + [str(path)] + list(args or []), cwd=path.parent,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return RunResult(False, f"Stopped after {timeout} seconds without finishing.")
    output = "\n".join(p for p in (done.stdout, done.stderr) if p)
    return RunResult(done.returncode == 0, output)


def run_command(cmd: list[str], cwd: Path, timeout: int = 300) -> RunResult:
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        return RunResult(False, f"{cmd[0]} isn't installed.")
    except subprocess.TimeoutExpired:
        return RunResult(False, f"{cmd[0]} didn't finish within {timeout} seconds.")
    return RunResult(done.returncode == 0, "\n".join(p for p in (done.stdout, done.stderr) if p))
