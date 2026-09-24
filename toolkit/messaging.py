"""send_message — send a chat message through a desktop messaging app.

Drives the app's own UI: launch it, jump to the contact with the app's
quick-switch/search shortcut, then paste the text and press Enter. Every
send needs the user's explicit OK in a real dialog first (security audit
2026-09-12, Critical #1): the model can be steered into calling this by
content it read, so a model-set "confirmed" flag is not enough.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from toolkit import apps
from toolkit.base import ToolContext, register, S

_MOD = "command" if sys.platform == "darwin" else "ctrl"


@dataclass(frozen=True)
class _Profile:
    app: str            # name passed to the app launcher
    find: tuple         # hotkey that focuses contact search / quick switcher
    settle: float = 3.0  # seconds for the window to come up


_PROFILES = {
    "whatsapp": _Profile("WhatsApp", (_MOD, "f")),
    "telegram": _Profile("Telegram", (_MOD, "f")),
    "signal": _Profile("Signal", (_MOD, "f")),
    "discord": _Profile("Discord", (_MOD, "k")),
    "slack": _Profile("Slack", (_MOD, "k")),
    "teams": _Profile("Microsoft Teams", (_MOD, "e")),
}

_NICKNAMES = {"wa": "whatsapp", "wp": "whatsapp", "tg": "telegram", "ms teams": "teams"}


def _profile_for(platform: str) -> _Profile | None:
    key = platform.lower().strip()
    key = _NICKNAMES.get(key, key)
    for name, profile in _PROFILES.items():
        if name in key:
            return profile
    return None


def _paste(text: str) -> None:
    import pyautogui
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey(_MOD, "v")


def deliver(profile: _Profile, contact: str, text: str) -> None:
    import pyautogui
    if not apps.launch(profile.app):
        raise RuntimeError(f"{profile.app} doesn't seem to be installed")
    time.sleep(profile.settle)
    pyautogui.hotkey(*profile.find)
    time.sleep(0.6)
    pyautogui.hotkey(_MOD, "a")
    _paste(contact)
    time.sleep(1.2)          # let the search results populate
    pyautogui.press("enter")  # open the top match
    time.sleep(1.0)
    _paste(text)
    time.sleep(0.2)
    pyautogui.press("enter")


@register(
    "send_message",
    "Sends a text message to a contact through a desktop messaging app "
    "(WhatsApp, Telegram, Signal, Discord, Slack or Teams). The user is always "
    "asked to confirm before anything is sent.",
    {
        "receiver": S("Contact or channel name as it appears in the app"),
        "message_text": S("The message to send"),
        "platform": S("WhatsApp, Telegram, Signal, Discord, Slack or Teams"),
    },
    ["receiver", "message_text", "platform"],
)
def send_message(args: dict, ctx: ToolContext) -> str:
    contact = (args.get("receiver") or "").strip()
    text = (args.get("message_text") or "").strip()
    platform = (args.get("platform") or "WhatsApp").strip()
    if not contact or not text:
        return "I need both who to send it to and what to say."
    profile = _profile_for(platform)
    if profile is None:
        return (f"I can't send through {platform} yet — supported apps are "
                f"{', '.join(p.app for p in _PROFILES.values())}.")

    confirm = getattr(ctx.ui, "confirm_action", None)
    shown = text if len(text) <= 80 else text[:80] + "…"
    if not (callable(confirm) and confirm(f"Send this {profile.app} message to {contact}?\n\n\"{shown}\"")):
        return "Not sent — the user didn't confirm."

    ctx.log(f"[msg] {profile.app} → {contact}")
    try:
        deliver(profile, contact, text)
    except Exception as err:
        return f"Sending failed: {err}"
    return f"Sent to {contact} on {profile.app}."
