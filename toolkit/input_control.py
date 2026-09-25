"""computer_control — direct mouse and keyboard control, plus finding and
clicking things on screen by description.

Locating an element uses Gemini's object-detection output (box_2d on a
0–1000 grid), then maps the box centre onto the logical screen size, so
display scaling doesn't skew the click.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

from toolkit import llm, places
from toolkit.base import ToolContext, register, S, I, N, B

_PRIMARY = "command" if sys.platform == "darwin" else "ctrl"


def _gui():
    import pyautogui
    pyautogui.FAILSAFE = True   # slam the mouse into a corner to abort
    return pyautogui


def _paste(text: str) -> None:
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.1)
    _gui().hotkey(_PRIMARY, "v")


def locate(description: str) -> tuple[int, int] | None:
    """Centre of the on-screen element matching `description`, or None."""
    gui = _gui()
    shot = gui.screenshot()
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    found = llm.ask_json(
        [llm.image_part(buf.getvalue()),
         f"On this screenshot, where is {description!r}? Reply with JSON "
         f"{{\"box_2d\": [ymin, xmin, ymax, xmax]}} on a 0-1000 scale, or "
         f"{{\"box_2d\": null}} if it isn't visible."],
        model=llm.FAST,
    )
    box = found.get("box_2d") if isinstance(found, dict) else None
    if not box or len(box) != 4:
        return None
    width, height = gui.size()
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    return round((xmin + xmax) / 2000 * width), round((ymin + ymax) / 2000 * height)


def focus_window(title: str) -> bool:
    if sys.platform == "win32":
        import pygetwindow
        matches = [w for w in pygetwindow.getAllWindows() if title.lower() in (w.title or "").lower()]
        if not matches:
            return False
        win = matches[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        return True
    import subprocess
    if sys.platform == "darwin":
        script = f'tell application "System Events" to set frontmost of (first process whose name contains "{title}") to true'
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    for cmd in (["wmctrl", "-a", title], ["xdotool", "search", "--name", title, "windowactivate"]):
        try:
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def _remembered(field: str) -> str:
    from memory import profile
    identity = profile.load().get("identity", {})
    entry = identity.get(field) or {}
    return entry.get("value", "") if isinstance(entry, dict) else str(entry)


def _coords(args) -> tuple:
    x, y = args.get("x"), args.get("y")
    return (int(x), int(y)) if x is not None and y is not None else ()


def _do(action: str, args: dict) -> str:
    gui = _gui()
    text = args.get("text") or ""
    if action == "type":
        gui.write(text, interval=0.02) if text.isascii() else _paste(text)
        return f"Typed {len(text)} characters."
    if action == "smart_type":
        if args.get("clear_first", True):
            gui.hotkey(_PRIMARY, "a")
            gui.press("backspace")
        _paste(text)
        return f"Entered {len(text)} characters."
    if action in ("click", "double_click", "right_click"):
        where = _coords(args)
        gui.click(*where, clicks=2 if action == "double_click" else 1,
                  button="right" if action == "right_click" else "left")
        return f"{action.replace('_', ' ').capitalize()} at {where or 'the pointer'}."
    if action == "move":
        where = _coords(args)
        if not where:
            return "I need x and y to move the mouse."
        gui.moveTo(*where, duration=0.25)
        return f"Pointer moved to {where}."
    if action == "hotkey":
        combo = [k.strip().lower() for k in str(args.get("keys") or "").split("+") if k.strip()]
        if not combo:
            return "Which keys?"
        gui.hotkey(*combo)
        return f"Pressed {'+'.join(combo)}."
    if action == "press":
        key = (args.get("key") or "enter").lower()
        gui.press(key)
        return f"Pressed {key}."
    if action == "scroll":
        direction = (args.get("direction") or "down").lower()
        amount = int(args.get("amount") or 3)
        if direction in ("left", "right"):
            gui.hscroll(amount if direction == "right" else -amount)
        else:
            gui.scroll(amount if direction == "up" else -amount)
        return f"Scrolled {direction}."
    if action == "copy":
        import pyperclip
        gui.hotkey(_PRIMARY, "c")
        time.sleep(0.2)
        return pyperclip.paste() or "(the clipboard is empty)"
    if action == "paste":
        if not text:
            gui.hotkey(_PRIMARY, "v")
            return "Pasted the clipboard."
        _paste(text)
        return f"Pasted {len(text)} characters."
    if action == "clear_field":
        gui.hotkey(_PRIMARY, "a")
        gui.press("backspace")
        return "Field cleared."
    if action == "wait":
        seconds = max(0.0, min(float(args.get("seconds") or 1), 30.0))
        time.sleep(seconds)
        return f"Waited {seconds:g}s."
    if action == "screenshot":
        dest = places.resolve(args.get("path") or f"desktop/screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png")
        if not places.within_home(dest):
            return "Screenshots can only be saved inside your home folder."
        dest.parent.mkdir(parents=True, exist_ok=True)
        gui.screenshot().save(dest)
        return f"Screenshot saved to {dest}"
    if action == "focus_window":
        title = (args.get("title") or "").strip()
        return f"Switched to {title}." if title and focus_window(title) else f"No window titled like {title!r}."
    if action in ("screen_find", "screen_click"):
        desc = (args.get("description") or "").strip()
        if not desc:
            return "Describe what to look for."
        spot = locate(desc)
        if spot is None:
            return f"I can't see {desc!r} on screen."
        if action == "screen_find":
            return f"{spot[0]},{spot[1]}"
        gui.click(*spot)
        return f"Clicked {desc!r} at {spot}."
    if action == "user_data":
        field = (args.get("field") or "name").strip().lower()
        return _remembered(field) or f"I don't have your {field} saved."
    return f"Unknown action {action!r}."


@register(
    "computer_control",
    "Direct mouse and keyboard control: type, click at coordinates, hotkeys, key "
    "presses, scrolling, pointer moves, clipboard, screenshots, focusing a window, "
    "and finding or clicking an on-screen element by describing it.",
    {
        "action": S("type | smart_type | click | double_click | right_click | move | hotkey | press | "
                    "scroll | copy | paste | clear_field | wait | screenshot | focus_window | "
                    "screen_find | screen_click | user_data"),
        "text": S("What to type (or paste, for long text)"),
        "x": I("Screen X"), "y": I("Screen Y"),
        "keys": S("Key combination, e.g. ctrl+shift+t"),
        "key": S("Single key, e.g. enter"),
        "direction": S("Which way to scroll or move: up, down, left or right"),
        "amount": I("Scroll steps (default 3)"),
        "seconds": N("Seconds to wait (max 30)"),
        "title": S("Part of the window title for focus_window"),
        "description": S("For screen_find/screen_click: how the thing looks or what it says, e.g. 'blue Send button'"),
        "field": S("For user_data: a saved identity field such as name, email, city"),
        "clear_first": B("smart_type: clear the field first (default true)"),
        "path": S("Where to save a screenshot"),
    },
    ["action"],
)
def computer_control(args: dict, ctx: ToolContext) -> str:
    action = (args.get("action") or "").strip().lower()
    if action == "left_click":
        action = "click"
    if not action:
        return "Which action?"
    ctx.log(f"[Computer] {action}")
    try:
        return _do(action, args)
    except Exception as err:
        return f"{action} failed: {err}"
