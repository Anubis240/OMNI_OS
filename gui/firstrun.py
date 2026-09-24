"""First launch: ask for the user's own Gemini API key."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from core.app_paths import get_data_dir, get_os_name
from gui.theme import C

KEY_PAGE = "https://aistudio.google.com/api-keys?project=gen-lang-client-0368720913"


def _keys_file():
    return get_data_dir() / "config" / "api_keys.json"


def has_api_key() -> bool:
    try:
        return bool(json.loads(_keys_file().read_text(encoding="utf-8")).get("gemini_api_key"))
    except (OSError, ValueError):
        return False


def store_api_key(key: str) -> None:
    """Save the key, keeping anything else already in the file."""
    path = _keys_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["gemini_api_key"] = key.strip()
    data.setdefault("os_system", get_os_name())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class KeyPrompt(QWidget):
    submitted = pyqtSignal(str)
    WIDTH, HEIGHT = 440, 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("keyPrompt")
        self.setStyleSheet(f"#keyPrompt {{ background: {C.PANEL_BG}; border: 1px solid {C.PRI}; border-radius: 16px; }}")
        col = QVBoxLayout(self)
        col.setContentsMargins(28, 22, 28, 22)
        col.setSpacing(10)

        title = QLabel("Welcome to Omni-OS")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent; border: none;")
        col.addWidget(title)
        intro = QLabel("Omni runs on your own Google Gemini API key. Paste it below — it's stored "
                       "only on this computer.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        col.addWidget(intro)

        self._field = QLineEdit()
        self._field.setEchoMode(QLineEdit.EchoMode.Password)
        self._field.setPlaceholderText("Gemini API key (starts with AIza…)")
        self._field.setFixedHeight(34)
        self._field.returnPressed.connect(self._submit)
        self._restyle(error=False)
        col.addWidget(self._field)

        link = QPushButton("Get a free key from Google AI Studio ↗")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.setStyleSheet(f"QPushButton {{ color: {C.ACC2}; background: transparent; border: none; text-align: left; }}"
                           f"QPushButton:hover {{ text-decoration: underline; }}")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KEY_PAGE)))
        col.addWidget(link)
        col.addStretch()

        go = QPushButton("Start Omni-OS")
        go.setFixedHeight(38)
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        go.setStyleSheet(f"QPushButton {{ background: {C.PRI}; color: #000; border: none; border-radius: 10px; }}"
                         f"QPushButton:hover {{ background: {C.ACC}; }}")
        go.clicked.connect(self._submit)
        col.addWidget(go)

    def _restyle(self, error: bool) -> None:
        edge = C.RED if error else C.BORDER
        self._field.setStyleSheet(f"QLineEdit {{ background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {edge};"
                                  f" border-radius: 8px; padding: 4px 10px; }} QLineEdit:focus {{ border-color: {C.PRI}; }}")

    def _submit(self) -> None:
        key = self._field.text().strip()
        if len(key) < 20:
            self._restyle(error=True)
            return
        store_api_key(key)
        self.submitted.emit(key)
