"""A compact labelled level meter for the status card."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget

from gui.theme import C, qcol


class Gauge(QWidget):
    """Name on the left, reading on the right, a thin fill line beneath.
    The line turns amber above 70% and red above 90%."""

    def __init__(self, name: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._name, self._color = name, color
        self._level: float | None = None
        self._reading = "—"
        self.setFixedHeight(30)
        self.setMinimumWidth(90)

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
        color = C.RED if level > 90 else C.ACC if level > 70 else self._color

        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.setPen(qcol(C.TEXT_MED))
        painter.drawText(QRectF(2, 0, w / 2, h - 8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._name)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.setPen(qcol(color if self._level is not None else C.TEXT_DIM))
        painter.drawText(QRectF(w / 2, 0, w / 2 - 2, h - 8), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._reading)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcol(C.BORDER_A))
        painter.drawRoundedRect(QRectF(2, h - 5, w - 4, 3), 1.5, 1.5)
        if level > 0:
            painter.setBrush(qcol(color))
            painter.drawRoundedRect(QRectF(2, h - 5, (w - 4) * level / 100, 3), 1.5, 1.5)
