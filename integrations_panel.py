"""IntegrationsPanel — the Zoey-OS-style "connect your tools" grid.

Swapped into the center stack exactly like TraderPanel/SettingsPanel (see
MainWindow._toggle_integrations_panel()). Reads/writes
core.settings_store.INTEGRATION_CATALOG + settings["integrations"]; the
actual API calls live in actions/integrations/. `from ui import C` is a
deferred import for the same reason trader_panel.py/settings_panel.py do it.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core import settings_store

# Catalog help strings lead with a bare domain(/path) — e.g. "github.com/
# settings/tokens — classic PAT..." — turn just that leading URL into a
# clickable link; leave the rest (and help text with no URL, like Ollama's
# or Confluence's) as plain text.
_LEADING_URL_RE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}(?:/[^\s]*)?)")


def _linkify_help(text: str, link_color: str) -> str:
    m = _LEADING_URL_RE.match(text)
    if not m:
        return text
    url = m.group(1)
    rest = text[len(url):]
    href = url if url.startswith("http") else f"https://{url}"
    return f'<a href="{href}" style="color:{link_color};">{url}</a>{rest}'


# Real per-service brand marks (assets/integration_icons/<catalog id>.svg),
# composited onto a circular badge at render time and cached — catalog ids
# with no matching file (there shouldn't be any, but new entries could lag
# the icon set) fall through to _build_card's letter-badge fallback.
_ICONS_DIR = Path(__file__).resolve().parent / "assets" / "integration_icons"
_icon_cache: dict[str, QPixmap] = {}


def _icon_badge_pixmap(catalog_id: str, size: int, badge_color: str) -> QPixmap | None:
    key = f"{catalog_id}:{size}:{badge_color}"
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached

    svg_path = _ICONS_DIR / f"{catalog_id}.svg"
    if not svg_path.exists():
        return None
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return None

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    circle = QPainterPath()
    circle.addEllipse(0.0, 0.0, float(size), float(size))
    painter.setClipPath(circle)
    painter.fillRect(0, 0, size, size, QColor(badge_color))

    pad = size * 0.22
    glyph_rect = QRectF(pad, pad, size - 2 * pad, size - 2 * pad)
    renderer.render(painter, glyph_rect)
    painter.end()

    _icon_cache[key] = pm
    return pm


class IntegrationsPanel(QWidget):
    _status_sig = pyqtSignal(str, bool)  # (message, is_error)

    def __init__(self, parent=None):
        super().__init__(parent)
        from ui import C  # deferred — see module docstring
        self._C = C
        self.settings = settings_store.load_settings()
        self._search = ""
        self._category = "All Categories"
        self._cards: list[tuple[dict, QWidget]] = []

        self._status_sig.connect(self._show_status)
        self._build_ui()

    # ---------- layout ----------

    def _build_ui(self):
        C = self._C
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        title = QLabel("◈ INTEGRATIONS")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(title)

        subtitle = QLabel("Connect your tools — Omni will handle the rest.")
        subtitle.setFont(QFont("Segoe UI", 8))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(subtitle)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont("Segoe UI", 8))
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search integrations…")
        self._search_input.setFont(QFont("Segoe UI", 9))
        self._search_input.setStyleSheet(
            f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 15px; padding: 6px 12px;"
        )
        self._search_input.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self._search_input, stretch=1)

        self._category_combo = QComboBox()
        categories = ["All Categories"] + sorted({e["category"] for e in settings_store.INTEGRATION_CATALOG})
        self._category_combo.addItems(categories)
        self._category_combo.setFont(QFont("Segoe UI", 9))
        self._category_combo.setStyleSheet(
            f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 1px; padding: 5px 10px;"
        )
        self._category_combo.currentTextChanged.connect(self._on_filter_changed)
        bar.addWidget(self._category_combo)

        outer.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(content)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(0, 4, 0, 4)
        scroll.setWidget(content)

        self._populate_grid()

    def _populate_grid(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards.clear()

        entries = [
            e for e in settings_store.INTEGRATION_CATALOG
            if self._search in e["name"].lower()
            and (self._category == "All Categories" or e["category"] == self._category)
        ]

        cols = 4
        for i, entry in enumerate(entries):
            card = self._build_card(entry)
            self._grid.addWidget(card, i // cols, i % cols)
            self._cards.append((entry, card))

    def _on_filter_changed(self):
        self._search = self._search_input.text().strip().lower()
        self._category = self._category_combo.currentText()
        self._populate_grid()

    # ---------- cards ----------

    def _is_connected(self, entry: dict) -> bool:
        conn_id = entry.get("family", entry["id"])
        saved = self.settings.get("integrations", {}).get(conn_id)
        return bool(saved and saved.get("enabled"))

    def _build_card(self, entry: dict) -> QWidget:
        C = self._C
        implemented = entry.get("implemented", False)
        connected = implemented and self._is_connected(entry)

        card = QFrame()
        card.setFixedSize(150, 92)
        border = C.GREEN if connected else "transparent"
        card.setStyleSheet(f"""
            QFrame {{ background: {C.PANEL2_BG}; border: 1px solid {border}; border-radius: 10px; }}
            QFrame:hover {{ border: 1px solid {C.PRI if implemented else "transparent"}; }}
        """)
        if implemented:
            card.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(4)

        icon = QLabel()
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # QLabel is itself a QFrame subclass, so the card's own `QFrame {
        # border: ... }` rule cascades down and boxes every label below
        # unless each one explicitly opts out with border: none.
        badge = _icon_badge_pixmap(entry["id"], 30, C.WHITE)
        if badge is not None:
            icon.setPixmap(badge)
            icon.setStyleSheet("background: transparent; border: none;")
        else:
            icon.setText(entry["name"][0].upper())
            icon.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            icon_bg = C.PRI if connected else C.BORDER_A
            icon.setStyleSheet(f"background: {icon_bg}; color: {C.DARK if connected else C.TEXT_MED}; border: none; border-radius: 15px;")
        lay.addWidget(icon)

        name = QLabel(entry["name"])
        name.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        name.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        name.setWordWrap(True)
        lay.addWidget(name)

        if not implemented:
            status = "Coming soon"
            color = C.TEXT_DIM
        elif connected:
            status = "Connected"
            color = C.GREEN
        else:
            status = "Not connected"
            color = C.TEXT_DIM
        status_lbl = QLabel(status)
        status_lbl.setFont(QFont("Segoe UI", 7))
        status_lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        lay.addWidget(status_lbl)
        lay.addStretch()

        if implemented:
            card.mousePressEvent = lambda ev, e=entry: self._open_connect_dialog(e)

        return card

    def _open_connect_dialog(self, entry: dict):
        dlg = _ConnectDialog(entry, self.settings, self._C, self)
        dlg.saved.connect(self._on_dialog_saved)
        dlg.exec()

    def _on_dialog_saved(self, message: str, is_error: bool):
        self.settings = settings_store.load_settings()
        self._populate_grid()
        self._status_sig.emit(message, is_error)

    def _show_status(self, message: str, is_error: bool):
        C = self._C
        self._status_lbl.setText(message)
        self._status_lbl.setStyleSheet(f"color: {C.RED if is_error else C.GREEN}; background: transparent;")


class _ConnectDialog(QDialog):
    saved = pyqtSignal(str, bool)

    def __init__(self, entry: dict, settings: dict, C, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._settings = settings
        self._C = C
        self._inputs: dict[str, QLineEdit] = {}
        self.setWindowTitle(f"Connect {entry['name']}")
        self.setFixedWidth(360)
        self.setStyleSheet(f"QDialog {{ background: {C.PANEL_BG}; }}")
        self._build_ui()

    def _build_ui(self):
        C = self._C
        entry = self._entry
        conn_id = entry.get("family", entry["id"])
        saved = self._settings.get("integrations", {}).get(conn_id, {})

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        badge = _icon_badge_pixmap(entry["id"], 28, C.WHITE)
        if badge is not None:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(badge)
            icon_lbl.setFixedSize(28, 28)
            title_row.addWidget(icon_lbl)
        title = QLabel(entry["name"])
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        lay.addLayout(title_row)

        if entry.get("help"):
            help_lbl = QLabel(_linkify_help(entry["help"], C.ACC2))
            help_lbl.setFont(QFont("Segoe UI", 8))
            help_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            help_lbl.setWordWrap(True)
            help_lbl.setTextFormat(Qt.TextFormat.RichText)
            help_lbl.setOpenExternalLinks(True)
            help_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            lay.addWidget(help_lbl)

        for field in entry.get("fields", []):
            lbl = QLabel(field["label"])
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(lbl)

            inp = QLineEdit()
            inp.setFont(QFont("Segoe UI", 9))
            inp.setStyleSheet(
                f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; "
                f"border-radius: 1px; padding: 5px 6px;"
            )
            if field.get("secret"):
                inp.setEchoMode(QLineEdit.EchoMode.Password)
                if saved.get(field["key"]):
                    inp.setPlaceholderText("(saved — leave blank to keep)")
            elif saved.get(field["key"]):
                inp.setText(str(saved[field["key"]]))
            elif field.get("default"):
                inp.setText(field["default"])
            lay.addWidget(inp)
            self._inputs[field["key"]] = inp

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont("Segoe UI", 8))
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        save_btn = self._button("SAVE", self._on_save)
        btn_row.addWidget(save_btn)

        if entry.get("connect_action"):
            connect_btn = self._button("CONNECT", self._on_connect, color=C.GREEN)
            btn_row.addWidget(connect_btn)

        if saved.get("enabled"):
            disc_btn = self._button("DISCONNECT", self._on_disconnect, color=C.RED)
            btn_row.addWidget(disc_btn)

        lay.addLayout(btn_row)

    def _button(self, label: str, cb, color: str | None = None) -> QPushButton:
        C = self._C
        btn = QPushButton(label)
        btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        c = color or C.PRI
        btn.setStyleSheet(f"""
            QPushButton {{ color: {c}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 6px 12px; }}
            QPushButton:hover {{ border: 1px solid {c}; }}
        """)
        btn.clicked.connect(cb)
        return btn

    def _collect_values(self) -> dict:
        conn_id = self._entry.get("family", self._entry["id"])
        existing = self._settings.get("integrations", {}).get(conn_id, {})
        values = dict(existing)
        for field in self._entry.get("fields", []):
            text = self._inputs[field["key"]].text().strip()
            if text:
                values[field["key"]] = text
            elif field.get("secret") and field["key"] in existing:
                pass  # keep the previously saved secret
            elif field.get("default") and field["key"] not in values:
                values[field["key"]] = field["default"]
        return values

    def _on_save(self):
        conn_id = self._entry.get("family", self._entry["id"])
        values = self._collect_values()
        values["enabled"] = True
        settings = settings_store.load_settings()
        settings.setdefault("integrations", {})[conn_id] = values
        settings_store.save_settings(settings)
        self._settings = settings
        self.saved.emit(f"{self._entry['name']} saved.", False)
        if not self._entry.get("connect_action"):
            self.accept()
        else:
            self._status_lbl.setText("Saved. Click Connect to finish linking your account.")
            self._status_lbl.setStyleSheet(f"color: {self._C.GREEN}; background: transparent;")

    def _on_connect(self):
        conn_id = self._entry.get("family", self._entry["id"])
        values = self._collect_values()
        values["enabled"] = True
        settings = settings_store.load_settings()
        settings.setdefault("integrations", {})[conn_id] = values
        settings_store.save_settings(settings)

        self._status_lbl.setText("Opening your browser to complete sign-in…")
        self._status_lbl.setStyleSheet(f"color: {self._C.ACC2}; background: transparent;")

        from actions.integrations.connectors import CONNECT_ACTIONS
        action = CONNECT_ACTIONS[self._entry["connect_action"]]

        def _run():
            result = action()
            self.saved.emit(result, "failed" in result.lower() or "error" in result.lower())

        threading.Thread(target=_run, daemon=True).start()
        self.accept()

    def _on_disconnect(self):
        conn_id = self._entry.get("family", self._entry["id"])
        settings = settings_store.load_settings()
        settings.get("integrations", {}).pop(conn_id, None)
        settings_store.save_settings(settings)
        self.saved.emit(f"{self._entry['name']} disconnected.", False)
        self.accept()
