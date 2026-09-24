"""The transcript pane: lines appear with a quick type-on effect, coloured
by who they're from. Thread-safe — post() may be called from any thread."""

from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import QTextBrowser

from gui.theme import C

_CHARS_PER_TICK = 3
_TICK_MS = 12


def colour_for(line: str) -> str:
    """Colour by the line's own prefix. Only a genuine leading "ERR:" is an
    error — not any line that merely contains those letters (GEMZ4US
    2026-09-10: "erreur", "interrupt" were being shown as errors)."""
    head = line.split(":", 1)[0].strip().lower() if ":" in line[:24] else ""
    return {
        "you": C.WHITE, "[phone]": C.WHITE,
        "omni": C.PRI, "err": C.RED, "file": C.GREEN,
    }.get(head, C.ACC2)


class ChatLog(QTextBrowser):
    _incoming = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFont(QFont("Segoe UI", 9))
        self.setStyleSheet(f"QTextBrowser {{ background: transparent; border: none; color: {C.TEXT}; }}"
                           f"QScrollBar:vertical {{ background: {C.BG}; width: 7px; border: none; }}"
                           f"QScrollBar::handle:vertical {{ background: {C.BORDER}; border-radius: 3px; min-height: 24px; }}")
        self._waiting: deque[str] = deque()
        self._line = ""
        self._shown = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._type_more)
        self._incoming.connect(self._queue_line)

    def post(self, line: str) -> None:
        self._incoming.emit(line)

    # Old name, kept for panels that call it directly.
    append_log = post

    def _queue_line(self, line: str) -> None:
        self._waiting.append(line)
        if not self._timer.isActive() and not self._line:
            self._start_next()

    def _append_html(self, fragment: str) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(fragment)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _start_next(self) -> None:
        self._line, self._shown = "", 0
        if not self._waiting:
            return
        line = self._waiting.popleft()
        if "<a href=" in line:
            # Ready-made links (share_file) are shown at once, not typed out as markup.
            self._append_html(f'<span style="color:{C.ACC2}">{line}</span><br>')
            QTimer.singleShot(15, self._start_next)
            return
        self._line = line
        self._colour = colour_for(line)
        self._timer.start(_TICK_MS)

    def _type_more(self) -> None:
        chunk = self._line[self._shown:self._shown + _CHARS_PER_TICK]
        self._shown += len(chunk)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        style = QTextCharFormat()
        style.setForeground(QColor(self._colour))
        finished = self._shown >= len(self._line)
        cursor.insertText(chunk + ("\n" if finished else ""), style)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        if finished:
            self._timer.stop()
            QTimer.singleShot(15, self._start_next)
