"""Phone pairing card, floating over the window (drag it anywhere — GEMZ4US #37).

Landscape layout: a status line on top; below it the QR code on the left
and the steps on the right, with the 6-character code shown as separate
character tiles for typing by hand. A thin bar drains as the code ages and a
fresh code replaces it automatically when it runs out. Once a phone links,
the pairing part folds away to a one-line "linked" view; if the phone
leaves, it comes back with a new code."""

from __future__ import annotations

import io
import time

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                             QVBoxLayout, QWidget)

from gui.theme import Tone

CODE_LIFETIME = 600   # seconds; matches dashboard.server.CODE_LIFETIME
_QR_SIDE = 164


def _qr_pixmap(text: str) -> QPixmap | None:
    try:
        import qrcode
    except ImportError:
        return None
    buf = io.BytesIO()
    qrcode.make(text, box_size=6, border=2).save(buf, format="PNG")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue())
    return pix.scaled(_QR_SIDE, _QR_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def _text(text: str, colour: str, size: int = 9, bold: bool = False) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setFont(QFont("Segoe UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
    label.setStyleSheet(f"color: {colour}; background: transparent; border: none;")
    return label


class PairingCard(QWidget):
    closed = Signal()
    WIDTH, HEIGHT = 540, 272

    def __init__(self, url: str, code: str, qr_link: str, parent=None):
        super().__init__(parent)
        self.setObjectName("pairingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"#pairingCard {{ background: {Tone.SURFACE}; border: 1px solid {Tone.EDGE}; border-radius: 14px; }}")
        self.request_new_key = None    # set by the window: () -> (url, code, qr_link) | None
        self._grab: QPoint | None = None
        self._issued_at = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 12, 14)
        outer.setSpacing(10)
        outer.addLayout(self._status_line())
        self._pairing_part = self._pairing_section()
        self._linked_part = self._linked_section()
        outer.addWidget(self._pairing_part)
        outer.addWidget(self._linked_part)
        outer.addStretch(1)
        self._linked_part.hide()

        self._ager = QTimer(self)
        self._ager.setInterval(1000)
        self._ager.timeout.connect(self._age_code)
        self._set_status("Waiting for your phone", Tone.AMBER)
        self._load(url, code, qr_link)

    # --- layout -----------------------------------------------------------------
    def _status_line(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self._dot = _text("●", Tone.AMBER, 11)
        self._status = _text("", Tone.INK, 10, bold=True)
        close = QPushButton("✕")
        close.setToolTip("Closes this card only — a linked phone stays linked.")
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"QPushButton {{ color: {Tone.INK_SOFT}; background: transparent; border: none; font-size: 13px; }}"
                            f"QPushButton:hover {{ color: {Tone.ACCENT}; }}")
        close.clicked.connect(self.dismiss)
        row.addWidget(self._dot)
        row.addWidget(self._status, 1)
        row.addWidget(close)
        return row

    def _pairing_section(self) -> QWidget:
        part = QWidget()
        grid = QGridLayout(part)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        self._qr = QLabel()
        self._qr.setFixedSize(_QR_SIDE + 8, _QR_SIDE + 8)
        self._qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self._qr, 0, 0, 3, 1)

        steps = QVBoxLayout()
        steps.setSpacing(6)
        steps.addWidget(_text("1   Put the phone on the same Wi-Fi as this PC.", Tone.INK_SOFT, 9))
        steps.addWidget(_text("2   Scan the square with the phone's camera.", Tone.INK_SOFT, 9))
        steps.addWidget(_text("     No camera? Open this address and enter the code:", Tone.INK_FAINT, 8))
        self._address = _text("", Tone.CYAN, 9)
        self._address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        steps.addWidget(self._address)
        tiles = QHBoxLayout()
        tiles.setSpacing(5)
        self._tiles = []
        for _ in range(6):
            tile = QLabel()
            tile.setFixedSize(30, 38)
            tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tile.setFont(QFont("Consolas", 16, QFont.Weight.Bold))
            tile.setStyleSheet(f"color: {Tone.ACCENT}; background: {Tone.RAISED}; border: 1px solid {Tone.EDGE}; border-radius: 6px;")
            tiles.addWidget(tile)
            self._tiles.append(tile)
        tiles.addStretch(1)
        steps.addLayout(tiles)
        grid.addLayout(steps, 0, 1)

        self._age_bar = QProgressBar()
        self._age_bar.setRange(0, CODE_LIFETIME)
        self._age_bar.setTextVisible(False)
        self._age_bar.setFixedHeight(4)
        self._age_bar.setStyleSheet(f"QProgressBar {{ background: {Tone.RAISED}; border: none; border-radius: 2px; }}"
                                    f"QProgressBar::chunk {{ background: {Tone.ACCENT_DEEP}; border-radius: 2px; }}")
        grid.addWidget(self._age_bar, 1, 1)
        fresh = self._button("Fresh code", self._renew)
        fresh.setToolTip("Replaces the code now; the old one stops working at once.")
        grid.addWidget(fresh, 2, 1, Qt.AlignmentFlag.AlignLeft)
        return part

    def _linked_section(self) -> QWidget:
        part = QFrame()
        row = QHBoxLayout(part)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(_text("Your phone is linked. You can close this card; the phone stays connected.",
                            Tone.INK_SOFT, 9), 1)
        row.addWidget(self._button("Pair another phone", self._pair_another))
        return part

    @staticmethod
    def _button(caption: str, on_click) -> QPushButton:
        button = QPushButton(caption)
        button.setFixedHeight(28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        button.setStyleSheet(f"QPushButton {{ color: {Tone.INK}; background: {Tone.RAISED}; border: 1px solid {Tone.EDGE}; "
                             f"border-radius: 7px; padding: 0 14px; }}"
                             f"QPushButton:hover {{ border-color: {Tone.ACCENT}; color: {Tone.ACCENT}; }}")
        button.clicked.connect(on_click)
        return button

    # --- state ----------------------------------------------------------------------
    def _set_status(self, text: str, colour: str) -> None:
        self._status.setText(text)
        self._dot.setStyleSheet(f"color: {colour}; background: transparent; border: none;")

    def _load(self, url: str, code: str, qr_link: str) -> None:
        self._address.setText(url)
        for tile, char in zip(self._tiles, code.ljust(6)):
            tile.setText(char)
        pix = _qr_pixmap(qr_link or url)
        if pix is None:
            self._qr.setText("QR code needs\nthe qrcode package")
            self._qr.setStyleSheet(f"color: {Tone.INK_SOFT}; background: {Tone.RAISED}; border-radius: 8px;")
        else:
            self._qr.setPixmap(pix)
            self._qr.setStyleSheet("background: #ffffff; border-radius: 8px;")
        self._issued_at = time.time()
        self._ager.start()
        self._age_code()

    def _age_code(self) -> None:
        left = CODE_LIFETIME - (time.time() - self._issued_at)
        if left <= 0:
            self._renew()
            return
        self._age_bar.setValue(int(left))

    def _renew(self) -> None:
        fresh = self.request_new_key() if self.request_new_key else None
        if fresh:
            url, code, *rest = fresh
            self._load(url, code, rest[0] if rest else "")
        else:
            self._ager.stop()
            self._set_status("Couldn't make a new code — close this and try again", Tone.ALERT)

    def _pair_another(self) -> None:
        self._linked_part.hide()
        self._pairing_part.show()
        self._set_status("Waiting for another phone", Tone.AMBER)
        self._renew()

    def mark_connected(self) -> None:
        self._ager.stop()
        self._pairing_part.hide()
        self._linked_part.show()
        self._set_status("Phone linked", Tone.OK)

    def mark_disconnected(self) -> None:
        """The phone really left — say so and offer a new code straight away."""
        self._linked_part.hide()
        self._pairing_part.show()
        self._set_status("Your phone left — scan again to reconnect", Tone.ALERT)
        self._renew()

    def dismiss(self) -> None:
        self._ager.stop()
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
