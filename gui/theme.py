"""The Omni palette and the few helpers every panel shares."""

from __future__ import annotations

import json

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QComboBox

from core.app_paths import get_data_dir


class Tone:
    """Omni's colours: near-black surfaces and one warm orange accent. Plain
    hex strings, so they work in style sheets and through with_alpha()."""
    CANVAS = "#000000"
    SURFACE = "#0a0a0c"
    RAISED = "#111114"
    EDGE = "#2a2a2e"
    EDGE_SOFT = "#1c1c1f"
    EDGE_HOT = "#ff8c42"
    ACCENT = "#ff8c42"
    ACCENT_DEEP = "#b35a1f"
    ACCENT_WASH = "#1a0f08"
    AMBER = "#e9c46a"
    CYAN = "#48cae4"
    OK = "#06d6a0"
    ALERT = "#ef476f"
    INK = "#f0f0f0"
    INK_SOFT = "#9e9e9e"    # ~7.4:1 on SURFACE — use for any text that matters
    INK_FAINT = "#575757"   # ~2.7:1 — placeholders and decoration only
    WHITE = "#ffffff"


# One accent per companion, assigned by position in the companion list.
COMPANION_HUES = ["#48cae4", "#e9c46a", "#06d6a0", "#b5179e", "#ef476f", "#ff8c42"]


def companion_color(companion_id: str, companions: list[dict]) -> str:
    """The same colour for a companion everywhere (orb tint, World view)."""
    for index, companion in enumerate(companions):
        if companion.get("id") == companion_id:
            return COMPANION_HUES[index % len(COMPANION_HUES)]
    return Tone.ACCENT


def with_alpha(hex_color: str, alpha: int = 255) -> QColor:
    """`hex_color` as a QColor with the given 0–255 opacity."""
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


def lerp_hex(start: str, end: str, t: float) -> str:
    """Colour `t` (0..1) of the way from `start` to `end`, as #rrggbb."""
    t = min(1.0, max(0.0, t))
    a, b = QColor(start), QColor(end)
    mix = [round(x + (y - x) * t) for x, y in ((a.red(), b.red()), (a.green(), b.green()), (a.blue(), b.blue()))]
    return "#" + "".join(f"{v:02x}" for v in mix)


class NoScrollComboBox(QComboBox):
    """A dropdown the mouse wheel can't change while scrolling past it
    (GEMZ4US #34): wheel events go to the surrounding scroll area instead."""

    def wheelEvent(self, event):
        event.ignore()


# --- voices -------------------------------------------------------------------

VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]
DEFAULT_VOICE = "Leda"


def _voice_file():
    return get_data_dir() / "config" / "voice.json"


def load_saved_voice() -> str:
    try:
        name = json.loads(_voice_file().read_text(encoding="utf-8")).get("voice")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_VOICE
    return name if name in VOICES else DEFAULT_VOICE


def save_voice(name: str) -> None:
    try:
        _voice_file().parent.mkdir(parents=True, exist_ok=True)
        _voice_file().write_text(json.dumps({"voice": name}), encoding="utf-8")
    except OSError:
        pass
