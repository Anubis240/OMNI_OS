"""Phone pairing card: a QR code that logs the phone straight in, plus the
address and a short key to type in by hand. Floats over the window and can
be dragged out of the way (GEMZ4US #37)."""

from __future__ import annotations

import io
import time

from PyQt6.QtCore import QPoint, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gui.theme import Tone

KEY_LIFETIME = 600   # seconds


def _label(text: str, size: int = 9, color: str = Tone.INK_SOFT, bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lbl.setFont(QFont("Segoe UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
    lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lbl


def _qr_pixmap(text: str, side: int) -> QPixmap | None:
    try:
        import qrcode
    except ImportError:
        return None
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue())
    return pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class PairingCard(QWidget):
    closed = pyqtSignal()
    WIDTH, HEIGHT = 380, 430

    def __init__(self, url: str, key: str, login_url: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("pairingCard")
        self.setStyleSheet(f"#pairingCard {{ background: {Tone.SURFACE}; border: 1px solid {Tone.ACCENT}; border-radius: 16px; }}")
        self.request_new_key = None    # set by the window: () -> (url, key, login_url) | None
        self._grab: QPoint | None = None
        self._expires = 0.0

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 16, 22, 16)
        col.setSpacing(6)
        col.addWidget(_label("CONNECT YOUR PHONE", 11, Tone.ACCENT, bold=True))
        self._qr = QLabel()
        self._qr.setFixedSize(180, 180)
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._qr)
        row.addStretch()
        col.addLayout(row)
        col.addWidget(_label("Point your phone's camera at the code.", 8))
        col.addWidget(_label("…or open this address and type the key:", 8, Tone.INK_FAINT))
        self._address = _label("", 8, Tone.CYAN)
        self._address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self._address)
        self._key = _label("", 26, Tone.AMBER, bold=True)
        col.addWidget(self._key)
        self._status = _label("", 8)
        col.addWidget(self._status)

        buttons = QHBoxLayout()
        for text, action, tip in (("NEW KEY", self._renew, "Make a fresh key and code"),
                                  ("CLOSE", self.dismiss, "Closes this card only — a connected phone stays connected.")):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            btn.setStyleSheet(f"QPushButton {{ color: {Tone.INK}; background: {Tone.RAISED}; border: 1px solid {Tone.EDGE}; border-radius: 8px; }}"
                              f"QPushButton:hover {{ border-color: {Tone.ACCENT}; color: {Tone.ACCENT}; }}")
            btn.clicked.connect(action)
            buttons.addWidget(btn)
        col.addLayout(buttons)

        self._countdown = QTimer(self)
        self._countdown.timeout.connect(self._tick)
        self._show_key(url, key, login_url)

    def _show_key(self, url: str, key: str, login_url: str) -> None:
        self._address.setText(url)
        self._key.setText(key)
        self._key.setStyleSheet(f"color: {Tone.AMBER}; background: transparent; border: none; letter-spacing: 8px;")
        pix = _qr_pixmap(login_url or url, 176)
        if pix is None:
            self._qr.setText("QR unavailable\n(install qrcode)")
            self._qr.setStyleSheet(f"color: {Tone.INK_SOFT}; background: {Tone.RAISED}; border-radius: 10px;")
        else:
            self._qr.setPixmap(pix)
            self._qr.setStyleSheet("background: white; border-radius: 10px;")
        self._expires = time.time() + KEY_LIFETIME
        self._countdown.start(1000)
        self._tick()

    def _tick(self) -> None:
        left = int(self._expires - time.time())
        if left <= 0:
            self.dismiss()
            return
        self._status.setText(f"Key valid for {left // 60}:{left % 60:02d}")
        self._status.setStyleSheet(f"color: {Tone.INK_SOFT}; background: transparent; border: none;")

    def _renew(self) -> None:
        fresh = self.request_new_key() if self.request_new_key else None
        if fresh:
            url, key, *rest = fresh
            self._show_key(url, key, rest[0] if rest else "")

    def _status_banner(self, word: str, detail: str, color: str, mark: str) -> None:
        self._countdown.stop()
        self._key.setText(word)
        self._key.setStyleSheet(f"color: {color}; background: transparent; border: none; letter-spacing: 3px;")
        self._qr.clear()
        self._qr.setText(mark)
        self._qr.setFont(QFont("Segoe UI", 56, QFont.Weight.Bold))
        self._qr.setStyleSheet(f"color: {color}; background: {Tone.RAISED}; border-radius: 10px;")
        self._status.setText(detail)
        self._status.setStyleSheet(f"color: {color}; background: transparent; border: none;")

    def mark_connected(self) -> None:
        self._status_banner("CONNECTED", "Phone connected — Omni is ready.", Tone.OK, "✓")

    def mark_disconnected(self) -> None:
        """The phone's connection really dropped — say so rather than keep
        showing a stale 'connected' state."""
        self._status_banner("DISCONNECTED", "The phone disconnected — press NEW KEY to pair again.", Tone.ALERT, "✕")

    def dismiss(self) -> None:
        self._countdown.stop()
        self.hide()
        self.closed.emit()

    # Drag the card by its background.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._grab is not None:
            self.move(self.mapToParent(event.position().toPoint() - self._grab))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._grab = None
        super().mouseReleaseEvent(event)
