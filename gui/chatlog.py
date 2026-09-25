"""The transcript pane. Each line is added whole the moment it arrives and
fades in over a fraction of a second, coloured by who it's from.
Thread-safe — post() may be called from any thread."""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QTextBrowser

from gui.theme import Tone

_FADE_SECONDS = 0.4
_FADE_STEP_MS = 30
_PANE_STYLE = (f"QTextBrowser {{ background: transparent; border: none; color: {Tone.INK}; }}"
               f"QScrollBar:vertical {{ background: {Tone.CANVAS}; width: 7px; border: none; }}"
               f"QScrollBar::handle:vertical {{ background: {Tone.EDGE}; border-radius: 3px; min-height: 24px; }}")


def colour_for(line: str) -> str:
    """Colour by the line's own prefix. Only a genuine leading "ERR:" is an
    error — not any line that merely contains those letters (GEMZ4US
    2026-09-10: "erreur", "interrupt" were being shown as errors)."""
    head = line.split(":", 1)[0].strip().lower() if ":" in line[:24] else ""
    return {
        "you": Tone.WHITE, "[phone]": Tone.WHITE,
        "omni": Tone.ACCENT, "err": Tone.ALERT, "file": Tone.OK,
    }.get(head, Tone.CYAN)


def _ink(colour: QColor, opacity: float) -> QTextCharFormat:
    shade = QColor(colour)
    shade.setAlphaF(max(0.0, min(1.0, opacity)))
    fmt = QTextCharFormat()
    fmt.setForeground(shade)
    return fmt


class _Fade:
    """One line's character range and when it started to appear."""
    __slots__ = ("start", "end", "colour", "began")

    def __init__(self, start: int, end: int, colour: QColor):
        self.start, self.end, self.colour, self.began = start, end, colour, time.monotonic()

    def opacity(self, now: float) -> float:
        t = min(1.0, (now - self.began) / _FADE_SECONDS)
        return t * (2 - t)          # ease-out


class ChatLog(QTextBrowser):
    _arrived = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(_PANE_STYLE)
        self._fades: list[_Fade] = []
        self._fade_clock = QTimer(self)
        self._fade_clock.setInterval(_FADE_STEP_MS)
        self._fade_clock.timeout.connect(self._advance_fades)
        self._arrived.connect(self._add_line)

    def post(self, line: str) -> None:
        self._arrived.emit(line)

    def _add_line(self, line: str) -> None:
        end = QTextCursor(self.document())
        end.movePosition(QTextCursor.MoveOperation.End)
        if "<a href=" in line:
            # Ready-made links (share_file) are markup; show them as-is, no fade.
            end.insertHtml(f'<span style="color:{Tone.CYAN}">{line}</span><br>')
        else:
            colour = QColor(colour_for(line))
            start = end.position()
            end.insertText(line + "\n", _ink(colour, 0.0))
            self._fades.append(_Fade(start, end.position(), colour))
            if not self._fade_clock.isActive():
                self._fade_clock.start()
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _advance_fades(self) -> None:
        now = time.monotonic()
        for fade in self._fades:
            span = QTextCursor(self.document())
            span.setPosition(fade.start)
            span.setPosition(fade.end, QTextCursor.MoveMode.KeepAnchor)
            span.mergeCharFormat(_ink(fade.colour, fade.opacity(now)))
        self._fades = [f for f in self._fades if f.opacity(now) < 1.0]
        if not self._fades:
            self._fade_clock.stop()
