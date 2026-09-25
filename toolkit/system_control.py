"""computer_settings — one-shot system and window commands.

Most actions are just the platform's own keyboard shortcut, kept in one
table. The rest (volume level, brightness, theme, Wi-Fi, lock, power) have
real implementations below. Restart and shutdown always need the user's
OK in a real dialog (security audit 2026-09-12, Critical #1).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time

from toolkit import llm
from toolkit.base import ToolContext, register, S

_WIN, _MAC = sys.platform == "win32", sys.platform == "darwin"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# action → (Windows/Linux keys, macOS keys). None means "not on this OS".
_SHORTCUTS: dict[str, tuple] = {
    "close_app": (("alt", "f4"), ("command", "q")),
    "close_window": (("ctrl", "w"), ("command", "w")),
    "close_tab": (("ctrl", "w"), ("command", "w")),
    "new_tab": (("ctrl", "t"), ("command", "t")),
    "next_tab": (("ctrl", "tab"), ("command", "shift", "]")),
    "prev_tab": (("ctrl", "shift", "tab"), ("command", "shift", "[")),
    "fullscreen": (("f11",), ("ctrl", "command", "f")),
    "minimize": (("win", "down"), ("command", "m")),
    "maximize": (("win", "up"), ("ctrl", "command", "f")),
    "snap_left": (("win", "left"), None),
    "snap_right": (("win", "right"), None),
    "switch_window": (("alt", "tab"), ("command", "tab")),
    "show_desktop": (("win", "d"), ("fn", "f11")),
    "task_manager": (("ctrl", "shift", "esc"), None),
    "open_settings": (("win", "i"), None),
    "file_explorer": (("win", "e"), None),
    "open_run": (("win", "r"), None),
    "focus_search": (("ctrl", "l"), ("command", "l")),
    "refresh_page": (("f5",), ("command", "r")),
    "go_back": (("alt", "left"), ("command", "[")),
    "go_forward": (("alt", "right"), ("command", "]")),
    "zoom_in": (("ctrl", "="), ("command", "=")),
    "zoom_out": (("ctrl", "-"), ("command", "-")),
    "zoom_reset": (("ctrl", "0"), ("command", "0")),
    "find_on_page": (("ctrl", "f"), ("command", "f")),
    "scroll_top": (("ctrl", "home"), ("command", "up")),
    "scroll_bottom": (("ctrl", "end"), ("command", "down")),
    "page_up": (("pageup",), ("pageup",)),
    "page_down": (("pagedown",), ("pagedown",)),
    "copy": (("ctrl", "c"), ("command", "c")),
    "paste": (("ctrl", "v"), ("command", "v")),
    "cut": (("ctrl", "x"), ("command", "x")),
    "undo": (("ctrl", "z"), ("command", "z")),
    "redo": (("ctrl", "y"), ("command", "shift", "z")),
    "select_all": (("ctrl", "a"), ("command", "a")),
    "save": (("ctrl", "s"), ("command", "s")),
    "enter": (("enter",), ("enter",)),
    "escape": (("esc",), ("esc",)),
    "play_pause": (("playpause",), ("playpause",)),
    "next_track": (("nexttrack",), ("nexttrack",)),
    "previous_track": (("prevtrack",), ("prevtrack",)),
    "screenshot": (("win", "shift", "s"), ("command", "shift", "4")),
}

_ALIASES = {
    "full_screen": "fullscreen", "reload": "refresh_page", "refresh": "refresh_page",
    "pause_video": "play_pause", "pause": "play_pause", "play": "play_pause",
    "unmute": "mute", "toggle_mute": "mute", "screen_off": "sleep_display",
    "type": "type_text", "write": "type_text", "write_on_screen": "type_text",
    "key": "press_key", "reload_n": "refresh_times", "refresh_n": "refresh_times",
    "set_volume": "volume_set", "lock": "lock_screen", "wifi": "toggle_wifi",
    "reboot": "restart", "power_off": "shutdown", "theme": "dark_mode",
}

_NEEDS_CONFIRMATION = {"restart", "shutdown"}


def _keys(*combo):
    import pyautogui
    if len(combo) == 1:
        pyautogui.press(combo[0])
    else:
        pyautogui.hotkey(*combo)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, **kw)


# --- volume ------------------------------------------------------------------

def _endpoint():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def set_volume(percent: int) -> str:
    percent = max(0, min(100, int(percent)))
    if _WIN:
        ep = _endpoint()
        ep.SetMute(0, None)
        ep.SetMasterVolumeLevelScalar(percent / 100, None)
    elif _MAC:
        _run(["osascript", "-e", f"set volume output volume {percent}"])
    else:
        _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])
    return f"Volume set to {percent}%."


def nudge_volume(step: int) -> str:
    if _WIN:
        try:
            now = round(_endpoint().GetMasterVolumeLevelScalar() * 100)
            return set_volume(now + step)
        except Exception:
            _keys("volumeup" if step > 0 else "volumedown")
            return "Volume adjusted."
    if _MAC:
        _run(["osascript", "-e", f"set volume output volume ((output volume of (get volume settings)) + {step})"])
    else:
        _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{step:+d}%"])
    return "Volume turned " + ("up." if step > 0 else "down.")


def toggle_mute() -> str:
    if _WIN:
        try:
            ep = _endpoint()
            ep.SetMute(0 if ep.GetMute() else 1, None)
            return "Sound unmuted." if not ep.GetMute() else "Sound muted."
        except Exception:
            _keys("volumemute")
    elif _MAC:
        _run(["osascript", "-e", "set volume output muted (not (output muted of (get volume settings)))"])
    else:
        _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    return "Mute toggled."


# --- display -----------------------------------------------------------------

def nudge_brightness(step: int) -> str:
    if _WIN:
        script = ("$b = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness; "
                  f"$n = [math]::Max(0, [math]::Min(100, $b + ({step}))); "
                  "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
                  "Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=$n} | Out-Null; $n")
        out = _run(["powershell", "-NoProfile", "-Command", script], timeout=10)
        if out.returncode != 0:
            return "This display doesn't allow brightness control from Windows (external monitors usually don't)."
        return f"Brightness now {out.stdout.strip()}%."
    if _MAC:
        _run(["osascript", "-e", f'tell application "System Events" to key code {144 if step > 0 else 145}'])
        return "Brightness adjusted."
    if shutil.which("brightnessctl"):
        _run(["brightnessctl", "set", f"{abs(step)}%{'+' if step > 0 else '-'}"])
        return "Brightness adjusted."
    return "I can't change brightness here (brightnessctl isn't installed)."


def sleep_display() -> str:
    if _WIN:
        import ctypes
        HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, OFF = 0xFFFF, 0x0112, 0xF170, 2
        ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, OFF)
    elif _MAC:
        _run(["pmset", "displaysleepnow"])
    else:
        _run(["xset", "dpms", "force", "off"])
    return "Display turned off."


def toggle_dark_mode() -> str:
    if _WIN:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            light = winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if light else 1)
        return "Switched to dark mode." if light else "Switched to light mode."
    if _MAC:
        _run(["osascript", "-e", 'tell application "System Events" to tell appearance preferences '
                                 'to set dark mode to not dark mode'])
        return "Theme toggled."
    now = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]).stdout
    _run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme",
          "default" if "dark" in now else "prefer-dark"])
    return "Theme toggled."


# --- network / session / power ----------------------------------------------

def toggle_wifi() -> str:
    if _WIN:
        script = ("$a = Get-NetAdapter | Where-Object { $_.PhysicalMediaType -match '802.11' } | Select-Object -First 1; "
                  "if (-not $a) { 'none' } elseif ($a.Status -eq 'Disabled') "
                  "{ Enable-NetAdapter -Name $a.Name -Confirm:$false; 'on' } "
                  "else { Disable-NetAdapter -Name $a.Name -Confirm:$false; 'off' }")
        out = _run(["powershell", "-NoProfile", "-Command", script], timeout=20)
        state = out.stdout.strip()
        if state == "none":
            return "I couldn't find a Wi-Fi adapter."
        if out.returncode != 0 or state not in ("on", "off"):
            return "Windows needs administrator rights to switch Wi-Fi — use the quick settings panel instead."
        return f"Wi-Fi turned {state}."
    if _MAC:
        port = _run(["networksetup", "-listallhardwareports"]).stdout
        device = "en0"
        lines = port.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line and i + 1 < len(lines):
                device = lines[i + 1].split(":", 1)[-1].strip()
        power = _run(["networksetup", "-getairportpower", device]).stdout
        target = "off" if power.strip().endswith("On") else "on"
        _run(["networksetup", "-setairportpower", device, target])
        return f"Wi-Fi turned {target}."
    target = "off" if "enabled" in _run(["nmcli", "radio", "wifi"]).stdout else "on"
    _run(["nmcli", "radio", "wifi", target])
    return f"Wi-Fi turned {target}."


def lock_screen() -> str:
    if _WIN:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
    elif _MAC:
        _keys("ctrl", "command", "q")
    else:
        _run(["loginctl", "lock-session"])
    return "Screen locked."


def power(action: str) -> str:
    if _WIN:
        _run(["shutdown", "/r" if action == "restart" else "/s", "/t", "15"])
        return f"{'Restarting' if action == 'restart' else 'Shutting down'} in 15 seconds (run 'shutdown /a' to cancel)."
    if _MAC:
        _run(["osascript", "-e", f'tell application "System Events" to {"restart" if action == "restart" else "shut down"}'])
    else:
        _run(["systemctl", "reboot" if action == "restart" else "poweroff"])
    return f"{action.capitalize()} started."


def type_text(text: str, then_enter: bool = False) -> str:
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.1)
    _keys("command" if _MAC else "ctrl", "v")
    if then_enter:
        time.sleep(0.1)
        _keys("enter")
    return f"Typed {len(text)} characters."


_SPECIAL = {
    "volume_up": lambda v: nudge_volume(int(v or 10)),
    "volume_down": lambda v: nudge_volume(-int(v or 10)),
    "volume_set": lambda v: set_volume(int(v if v not in (None, "") else 50)),
    "mute": lambda v: toggle_mute(),
    "brightness_up": lambda v: nudge_brightness(int(v or 10)),
    "brightness_down": lambda v: nudge_brightness(-int(v or 10)),
    "sleep_display": lambda v: sleep_display(),
    "dark_mode": lambda v: toggle_dark_mode(),
    "toggle_wifi": lambda v: toggle_wifi(),
    "lock_screen": lambda v: lock_screen(),
    "restart": lambda v: power("restart"),
    "shutdown": lambda v: power("shutdown"),
    "scroll_up": lambda v: (__import__("pyautogui").scroll(int(v or 5) * 100), "Scrolled up.")[1],
    "scroll_down": lambda v: (__import__("pyautogui").scroll(-int(v or 5) * 100), "Scrolled down.")[1],
    "press_key": lambda v: (_keys(str(v)), f"Pressed {v}.")[1] if v else "Which key?",
    "refresh_times": lambda v: _refresh_times(int(v or 1)),
}

ACTIONS = sorted(set(_SHORTCUTS) | set(_SPECIAL) | {"type_text"})


def _refresh_times(n: int) -> str:
    for _ in range(max(1, min(n, 50))):
        _perform_shortcut("refresh_page")
        time.sleep(0.8)
    return f"Refreshed {n} time(s)."


def _perform_shortcut(action: str) -> str:
    win_keys, mac_keys = _SHORTCUTS[action]
    combo = mac_keys if _MAC else win_keys
    if combo is None:
        return f"{action.replace('_', ' ')} isn't available on this system."
    if not _WIN and not _MAC and combo and combo[0] == "win":
        combo = ("super",) + combo[1:]
    _keys(*combo)
    return f"Done: {action.replace('_', ' ')}."


def _guess_action(description: str) -> tuple[str, object]:
    reply = llm.ask_json(
        f"A user said: {description!r}\nPick the single best computer action from this list: "
        f"{', '.join(ACTIONS)}.\nReply as JSON {{\"action\": <name>, \"value\": <number, text, key or null>}}.",
        model=llm.LITE,
    )
    return str(reply.get("action") or ""), reply.get("value")


@register(
    "computer_settings",
    "One-shot computer commands: volume (up/down/set/mute), brightness, window "
    "management, keyboard shortcuts, typing text, media keys, tabs, zoom, page "
    "navigation, screenshots, dark mode, Wi-Fi, lock screen, display off, restart "
    "and shutdown. Each call performs one of these directly; several in a row are "
    "just several calls here — agent_task is for jobs these controls can't do.",
    {
        "action": S(" | ".join(ACTIONS)),
        "value": S("Extra input some actions take: a level for volume or brightness, the text for typing, a key name, or how many times to repeat"),
        "description": S("If no action fits, what the user wants in plain words"),
    },
)
def computer_settings(args: dict, ctx: ToolContext) -> str:
    raw = (args.get("action") or "").strip().lower().replace(" ", "_").replace("-", "_")
    value = args.get("value")
    if not raw and (args.get("description") or "").strip():
        try:
            raw, guessed = _guess_action(args["description"])
            value = value if value not in (None, "") else guessed
        except Exception as err:
            return f"I couldn't work out which setting you meant: {err}"
    action = _ALIASES.get(raw, raw)
    if not action:
        return "Which setting should I change?"
    ctx.log(f"[Settings] {action}")

    if action in _NEEDS_CONFIRMATION:
        ask = getattr(ctx.ui, "confirm_action", None)
        if not (callable(ask) and ask(f"Omni wants to {action} this computer. Allow it?")):
            return f"{action.capitalize()} cancelled — not confirmed."
    try:
        if action == "type_text":
            text = str(value or args.get("text") or "")
            return type_text(text) if text else "What should I type?"
        if action in _SPECIAL:
            return _SPECIAL[action](value)
        if action in _SHORTCUTS:
            return _perform_shortcut(action)
    except Exception as err:
        return f"{action.replace('_', ' ')} failed: {err}"
    return f"I don't know the action {raw!r}."
