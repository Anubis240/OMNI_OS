"""open_app — launch an installed application (or a website) by name.

Windows: match the spoken name against the Start menu's own app list
(`Get-StartApps`, which covers both Win32 and Store apps) and launch the
exact AppID through shell:AppsFolder. Falls back to anything on PATH, then
to a URI/shell verb via os.startfile.
macOS: `open -a`. Linux: PATH binary, then gtk-launch, then xdg-open.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser

from toolkit.base import ToolContext, register, S

# Spoken names that don't match the Start menu entry closely enough.
_SAYINGS = {
    "vs code": "visual studio code", "vscode": "visual studio code",
    "word": "word", "excel": "excel", "powerpoint": "powerpoint",
    "file explorer": "file explorer", "explorer": "file explorer",
    "task manager": "task manager", "settings": "settings",
    "calculator": "calculator", "terminal": "terminal", "cmd": "command prompt",
}

# Last-resort executables when nothing in the Start menu matches.
_WINDOWS_COMMANDS = {
    "notepad": "notepad.exe", "paint": "mspaint.exe", "command prompt": "cmd.exe",
    "powershell": "powershell.exe", "task manager": "taskmgr.exe",
    "file explorer": "explorer.exe", "calculator": "calc.exe", "settings": "ms-settings:",
}

_URLISH = re.compile(r"^(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?$", re.IGNORECASE)

_start_apps_cache: tuple[float, list[dict]] | None = None


def _start_apps() -> list[dict]:
    """[{Name, AppID}] from the Start menu, cached for five minutes."""
    global _start_apps_cache
    if _start_apps_cache and time.monotonic() - _start_apps_cache[0] < 300:
        return _start_apps_cache[1]
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    apps = json.loads(out) if out else []
    if isinstance(apps, dict):
        apps = [apps]
    _start_apps_cache = (time.monotonic(), apps)
    return apps


def _best_start_app(wanted: str) -> dict | None:
    apps = _start_apps()
    by_name = {a["Name"].lower(): a for a in apps if a.get("Name")}
    if wanted in by_name:
        return by_name[wanted]
    # Prefer entries that start with / contain every spoken word, shortest first
    # ("chrome" → "Google Chrome", not "Chrome Remote Desktop").
    words = wanted.split()
    containing = [n for n in by_name if all(w in n for w in words)]
    if containing:
        return by_name[min(containing, key=len)]
    close = difflib.get_close_matches(wanted, list(by_name), n=1, cutoff=0.75)
    return by_name[close[0]] if close else None


def _open_windows(name: str) -> str | None:
    wanted = _SAYINGS.get(name.lower(), name.lower())
    try:
        app = _best_start_app(wanted)
    except Exception as err:
        print(f"[apps] couldn't read the Start menu app list ({err}); trying known install folders")
        app = None
    if app:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app['AppID']}"])
        return app["Name"]
    command = _WINDOWS_COMMANDS.get(wanted) or shutil.which(wanted) or shutil.which(wanted.replace(" ", ""))
    if command:
        os.startfile(command)
        return name
    return None


def _open_mac(name: str) -> str | None:
    for target in (name, f"{name}.app"):
        if subprocess.run(["open", "-a", target], capture_output=True).returncode == 0:
            return name
    return None


def _open_linux(name: str) -> str | None:
    slug = name.lower().replace(" ", "-")
    binary = shutil.which(name) or shutil.which(slug) or shutil.which(slug.replace("-", ""))
    if binary:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return name
    for launcher in (["gtk-launch", slug], ["xdg-open", name]):
        if shutil.which(launcher[0]) and subprocess.run(launcher, capture_output=True).returncode == 0:
            return name
    return None


def launch(name: str) -> str | None:
    """Returns the name actually launched, or None if nothing matched."""
    if _URLISH.match(name) and " " not in name:
        webbrowser.open(name if name.startswith("http") else f"https://{name}")
        return name
    if sys.platform == "win32":
        return _open_windows(name)
    if sys.platform == "darwin":
        return _open_mac(name)
    return _open_linux(name)


@register(
    "open_app",
    "Launches a program installed on this PC by its name (found through the Start "
    "menu and known install folders), or opens a web address in the default browser. "
    "Nothing opens unless this tool runs.",
    {"app_name": S("The application's name as the user said it, e.g. 'Spotify', 'Chrome', or a site like 'github.com'")},
    ["app_name"],
)
def open_app(args: dict, ctx: ToolContext) -> str:
    name = (args.get("app_name") or "").strip()
    if not name:
        return "Which app should I open?"
    ctx.log(f"[open_app] {name}")
    try:
        launched = launch(name)
    except Exception as err:
        return f"Opening {name} failed: {err}"
    if launched:
        return f"Opened {launched}."
    return f"I couldn't find an app called {name} on this computer."
