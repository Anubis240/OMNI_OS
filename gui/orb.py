"""The orb: a slowly turning cloud of points in the active companion's
colour, lit from behind by a soft glow.

Motion is driven by time and by sound, not by random targets:
  - idle, it breathes on a slow sine wave;
  - while Omni talks, its size, glow and spin follow the loudness of the
    reply audio actually being played (Speaker reports it through
    set_voice_level), smoothed by a fast-attack / slow-release envelope;
  - with the mic off it dims to the alert colour and holds still-ish.
A companion switch cross-fades the colour and adds a short burst of spin."""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from gui.theme import Tone, lerp_hex, with_alpha

_POINTS = 900
_TILT = math.radians(16)        # the spin axis leans a little toward the viewer
_FADE = 0.8                     # seconds for a colour change
_FRAME_MS = 1000 // 60

_BREATH_PERIOD = 4.5            # seconds per idle breath
_ATTACK, _RELEASE = 0.05, 0.35  # envelope time constants (s): rise fast, fall slowly
_SETTLE = 0.6                   # time constant (s) for size/glow outside speech
_IDLE_SPIN, _TALK_SPIN = 6.0, 30.0   # degrees per second (talk spin scales with loudness)


def _cloud(n: int) -> np.ndarray:
    """n points spread over a sphere, each pulled in a little at random."""
    rng = np.random.default_rng(7)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * rng.uniform(0.86, 1.0, size=(n, 1))


def _approach(value: float, target: float, dt: float, tau: float) -> float:
    """Move `value` toward `target` as a first-order lag with time constant
    `tau`, independent of frame rate."""
    return target + (value - target) * math.exp(-dt / tau)


class OrbView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 240)
        self.speaking = False
        self.muted = False
        self.voice_level = 0.0          # 0..1, written by the audio side

        self._cloud = _cloud(_POINTS)
        self._born = self._last_frame = time.monotonic()
        self._envelope = 0.0
        self._scale, self._light = 1.0, 0.35
        self._angle = 0.0
        self._burst = 0.0               # extra degrees/second, decays after a companion switch
        self._color = self._color_from = self._color_to = Tone.ACCENT
        self._fade_started = 0.0

        clock = QTimer(self)
        clock.setTimerType(Qt.TimerType.PreciseTimer)
        clock.timeout.connect(self._frame)
        clock.start(_FRAME_MS)

    # --- colour -------------------------------------------------------------
    def set_tint(self, color: str) -> None:
        self._color = self._color_from = self._color_to = color

    def animate_companion_switch(self, color: str) -> None:
        self._color_from, self._color_to = self._color, color
        self._fade_started = time.monotonic()
        self._burst = 700.0

    # --- motion -------------------------------------------------------------
    def _frame(self) -> None:
        now = time.monotonic()
        dt = min(0.1, now - self._last_frame)
        self._last_frame = now

        heard = self.voice_level if self.speaking else 0.0
        self._envelope = _approach(self._envelope, heard, dt, _ATTACK if heard > self._envelope else _RELEASE)

        if self.muted:
            scale_to, light_to = 0.985, 0.12
        elif self.speaking:
            scale_to, light_to = 1.02 + 0.1 * self._envelope, 0.55 + 0.45 * self._envelope
        else:
            breath = math.sin(2 * math.pi * (now - self._born) / _BREATH_PERIOD)
            scale_to, light_to = 1.0 + 0.012 * breath, 0.34 + 0.06 * breath
        tau = _ATTACK if self.speaking and not self.muted else _SETTLE
        self._scale = _approach(self._scale, scale_to, dt, tau)
        self._light = _approach(self._light, light_to, dt, tau)

        spin = 0.0 if self.muted else _IDLE_SPIN + _TALK_SPIN * self._envelope
        self._angle = (self._angle + math.radians((spin + self._burst) * dt)) % (2 * math.pi)
        self._burst = _approach(self._burst, 0.0, dt, 0.25) if self._burst > 1.0 else 0.0

        if self._color != self._color_to:
            t = min(1.0, (now - self._fade_started) / _FADE)
            self._color = self._color_to if t >= 1.0 else lerp_hex(self._color_from, self._color_to, 1 - (1 - t) ** 3)
        self.update()

    # --- painting -------------------------------------------------------------
    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        centre, side = QPointF(w / 2, h / 2), min(w, h)
        tint = QColor(Tone.ALERT if self.muted else self._color)

        # Backdrop: black edges lifting to a faint warm centre.
        backdrop = QRadialGradient(centre, max(w, h) * 0.75)
        backdrop.setColorAt(0.0, with_alpha(Tone.ACCENT_WASH))
        backdrop.setColorAt(1.0, with_alpha(Tone.CANVAS))
        painter.fillRect(self.rect(), QBrush(backdrop))

        halo_r = side * 0.5
        halo = QRadialGradient(centre, halo_r)
        halo.setColorAt(0.3, QColor(tint.red(), tint.green(), tint.blue(), int(75 * self._light)))
        halo.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(centre, halo_r, halo_r)

        # Spin around the vertical axis, then lean the whole thing by _TILT.
        cos_a, sin_a = math.cos(self._angle), math.sin(self._angle)
        x, y, z = self._cloud[:, 0], self._cloud[:, 1], self._cloud[:, 2]
        xs, zs = x * cos_a + z * sin_a, -x * sin_a + z * cos_a
        ys, zs = y * math.cos(_TILT) - zs * math.sin(_TILT), y * math.sin(_TILT) + zs * math.cos(_TILT)
        radius = side * 0.29 * self._scale
        near = (zs + 1) / 2                                  # 0 far … 1 near
        brightness = 0.8 + 0.4 * self._envelope
        for i in np.argsort(zs):                             # far points first
            depth = near[i]
            painter.setBrush(QColor(tint.red(), tint.green(), tint.blue(), int(min(255, 35 + 200 * depth * brightness))))
            dot = 0.7 + 2.3 * depth
            painter.drawEllipse(QPointF(centre.x() + xs[i] * radius, centre.y() - ys[i] * radius), dot, dot)
