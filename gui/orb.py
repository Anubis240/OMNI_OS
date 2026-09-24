"""The orb: a slowly turning cloud of points in the active companion's
colour. It breathes while idle, swells and quickens while speaking, and
greys out while muted. A companion switch cross-fades the colour and
gives it a short burst of spin."""

from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QTimer, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QWidget

from gui.theme import C, lerp_hex, qcol

_POINTS = 900
_TILT = math.radians(16)        # the spin axis leans a little toward the viewer
_FADE = 0.8                     # seconds for a colour change


def _cloud(n: int) -> np.ndarray:
    """n points spread over a sphere, each pulled in a little at random."""
    rng = np.random.default_rng(7)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * rng.uniform(0.86, 1.0, size=(n, 1))


class OrbView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.state = "STARTING"
        self.speaking = False
        self.muted = False

        self._cloud = _cloud(_POINTS)
        self._angle = 0.0
        self._extra_spin = 0.0
        self._size = 1.0
        self._glow = 0.35
        self._size_goal = 1.0
        self._glow_goal = 0.35
        self._next_goal_at = 0.0
        self._color = self._color_from = self._color_to = C.PRI
        self._fade_started = 0.0

        timer = QTimer(self)
        timer.timeout.connect(self._advance)
        timer.start(16)

    # --- colour -------------------------------------------------------------
    def set_tint(self, color: str) -> None:
        self._color = self._color_from = self._color_to = color

    def animate_companion_switch(self, color: str) -> None:
        self._color_from, self._color_to = self._color, color
        self._fade_started = time.monotonic()
        self._extra_spin = 12.0

    # --- animation ------------------------------------------------------------
    def _advance(self) -> None:
        now = time.monotonic()
        if now >= self._next_goal_at:
            if self.speaking:
                self._size_goal, self._glow_goal = np.random.uniform(1.05, 1.13), np.random.uniform(0.8, 1.0)
                self._next_goal_at = now + 0.12
            elif self.muted:
                self._size_goal, self._glow_goal = 0.99, 0.12
                self._next_goal_at = now + 0.5
            else:
                self._size_goal, self._glow_goal = np.random.uniform(1.0, 1.01), np.random.uniform(0.28, 0.4)
                self._next_goal_at = now + 0.5
        ease = 0.35 if self.speaking else 0.12
        self._size += (self._size_goal - self._size) * ease
        self._glow += (self._glow_goal - self._glow) * ease

        if self._color != self._color_to:
            t = min(1.0, (now - self._fade_started) / _FADE)
            self._color = lerp_hex(self._color_from, self._color_to, 1 - (1 - t) ** 3)
            if t >= 1.0:
                self._color = self._color_to

        self._angle += math.radians((0.32 if self.speaking else 0.11) + self._extra_spin)
        self._extra_spin = self._extra_spin * 0.9 if self._extra_spin > 0.02 else 0.0
        self.update()

    # --- painting -------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), qcol(C.BG))
        w, h = self.width(), self.height()
        cx, cy, side = w / 2, h / 2, min(w, h)

        painter.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(20, w, 40):
            for y in range(20, h, 40):
                painter.drawPoint(x, y)

        tint = QColor(C.MUTED_C if self.muted else self._color)
        glow = QRadialGradient(QPointF(cx, cy), side * 0.52)
        glow.setColorAt(0.35, QColor(tint.red(), tint.green(), tint.blue(), int(70 * self._glow)))
        glow.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(QPointF(cx, cy), side * 0.52, side * 0.52)

        # Spin around the vertical axis, then lean the whole thing by _TILT.
        cos_a, sin_a = math.cos(self._angle), math.sin(self._angle)
        x, y, z = self._cloud[:, 0], self._cloud[:, 1], self._cloud[:, 2]
        xs, zs = x * cos_a + z * sin_a, -x * sin_a + z * cos_a
        ys, zs = y * math.cos(_TILT) - zs * math.sin(_TILT), y * math.sin(_TILT) + zs * math.cos(_TILT)
        radius = side * 0.29 * self._size
        near = (zs + 1) / 2                                  # 0 far … 1 near
        order = np.argsort(zs)                               # far points first
        shimmer = 0.85 + 0.25 * math.sin(time.monotonic() * 3) if self.speaking else 1.0
        for i in order:
            depth = near[i]
            alpha = int(min(255, 35 + 200 * depth * shimmer))
            painter.setBrush(QColor(tint.red(), tint.green(), tint.blue(), alpha))
            dot = 0.7 + 2.3 * depth
            painter.drawEllipse(QPointF(cx + xs[i] * radius, cy - ys[i] * radius), dot, dot)
