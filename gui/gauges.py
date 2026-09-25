"""A compact labelled level meter for the status card."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget

from gui.theme import Tone, with_alpha


class Gauge(QWidget):
    """Name on the left, reading on the right, a thin fill line beneath.
    The line turns amber above 70% and red above 90%."""

    def __init__(self, name: str, color: str = Tone.ACCENT, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setMinimumWidth(90)
        self._name, self._color = name, color
        self.show_reading(None, "—")

    def show_reading(self, level: float | None, reading: str) -> None:
        """`level` 0–100, or None when unknown (shown as an empty line)."""
        self._level = None if level is None else min(100.0, max(0.0, level))
        self._reading = reading
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        level = self._level or 0.0
        color = Tone.ALERT if level > 90 else Tone.AMBER if level > 70 else self._color

        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.setPen(with_alpha(Tone.INK_SOFT))
        painter.drawText(QRectF(2, 0, w / 2, h - 8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._name)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(with_alpha(color if self._level is not None else Tone.INK_FAINT))
        painter.drawText(QRectF(w / 2, 0, w / 2 - 2, h - 8), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._reading)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(Tone.EDGE_SOFT))
        painter.drawRoundedRect(QRectF(2, h - 5, w - 4, 3), 1.5, 1.5)
        if level > 0:
            painter.setBrush(with_alpha(color))
            painter.drawRoundedRect(QRectF(2, h - 5, (w - 4) * level / 100, 3), 1.5, 1.5)
