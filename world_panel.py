"""WorldPanel — the Zoey-OS-style "World" view: the active (lead) companion
and its sub-agents (claude_agent-backend companions), shown as a node graph
with live idle/running status.

Delegation itself happens through the delegate_to_agent tool (see
main.py::_delegate_to_agent / _run_delegation); this panel is purely the
visualization plus where new sub-agents get created. Swapped into the
center stack exactly like TraderPanel/SettingsPanel/IntegrationsPanel.
`from ui import C, COMPANION_HUES` is a deferred import for the same reason
those other panels do it.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

from core import settings_store


class WorldPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        from ui import C, COMPANION_HUES  # deferred — see module docstring
        self._C = C
        self._hues = COMPANION_HUES
        self.on_companion_added = None  # wired by MainWindow._ensure_world_panel() to trigger a live reconnect
        self._build_ui()

        self._status_tmr = QTimer(self)
        self._status_tmr.timeout.connect(self._canvas.refresh_statuses)
        self._status_tmr.start(1500)

    def _build_ui(self):
        C = self._C
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        title = QLabel("◎ WORLD")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(title)

        subtitle = QLabel("Your lead companion and its sub-agents, at a glance.")
        subtitle.setFont(QFont("Segoe UI", 8))
        subtitle.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(scroll, stretch=1)

        self._canvas = _GraphCanvas(C, self._hues)
        self._canvas.add_clicked.connect(self._open_add_dialog)
        self._canvas.edit_requested.connect(self._open_edit_dialog)
        self._canvas.delete_requested.connect(self._delete_companion)
        scroll.setWidget(self._canvas)

        self.refresh()

    def refresh(self):
        settings = settings_store.load_settings()
        active_id = settings.get("active_companion_id") or ""
        all_companions = settings["companions"]
        lead = next((c for c in all_companions if c.get("id") == active_id), None)
        lead_name = lead["name"] if lead else "Omni"
        sub_agents = [c for c in all_companions if c.get("backend") == "claude_agent"]
        self._canvas.set_data(lead_name, sub_agents, all_companions)

    def _open_add_dialog(self):
        dlg = _AddSubAgentDialog(self._C, None, self)
        if dlg.exec():
            self.refresh()
            if self.on_companion_added:
                self.on_companion_added()

    def _open_edit_dialog(self, companion_id: str):
        settings = settings_store.load_settings()
        companion = next((c for c in settings["companions"] if c["id"] == companion_id), None)
        if companion is None:
            return
        dlg = _AddSubAgentDialog(self._C, companion, self)
        if dlg.exec():
            from actions.claude_companion import forget_session
            forget_session(companion_id)  # identity changed — start its next turn fresh
            self.refresh()
            if self.on_companion_added:
                self.on_companion_added()

    def _delete_companion(self, companion_id: str):
        C = self._C
        settings = settings_store.load_settings()
        companion = next((c for c in settings["companions"] if c["id"] == companion_id), None)
        name = companion["name"] if companion else "this sub-agent"

        box = QMessageBox(self)
        box.setWindowTitle("Remove sub-agent")
        box.setText(f'Remove "{name}"? This deletes its identity and conversation history.')
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setStyleSheet(
            f"QMessageBox {{ background: {C.PANEL_BG}; }} QLabel {{ color: {C.TEXT}; background: transparent; }}"
        )
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        settings["companions"] = [c for c in settings["companions"] if c["id"] != companion_id]
        if settings.get("active_companion_id") == companion_id:
            settings["active_companion_id"] = ""
        settings_store.save_settings(settings)

        from actions.claude_companion import forget_session
        forget_session(companion_id)

        self.refresh()
        if self.on_companion_added:
            self.on_companion_added()


class _GraphCanvas(QWidget):
    add_clicked = pyqtSignal()
    edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    NODE_W, NODE_H = 190, 88
    LEAD_W, LEAD_H = 220, 92
    H_GAP = 24
    TOP_MARGIN = 24
    TRUNK_GAP = 56

    def __init__(self, C, hues, parent=None):
        super().__init__(parent)
        self._C = C
        self._hues = hues
        self.setStyleSheet("background: transparent;")
        self._lead_card: _NodeCard | None = None
        self._sub_cards: dict[str, _NodeCard] = {}  # companion id -> card
        # Positions a user has dragged a node to — kept for this panel's
        # lifetime (the app's whole run, since panels are created once and
        # reused) so opening/closing the World tab or adding another
        # sub-agent doesn't snap an already-arranged layout back to the grid.
        self._manual_positions: dict[str, tuple[int, int]] = {}

        self._add_btn = QPushButton("+", self)
        self._add_btn.setFixedSize(34, 34)
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._add_btn.setStyleSheet(f"""
            QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER}; border-radius: 17px; }}
            QPushButton:hover {{ border: 1px solid {C.PRI}; }}
        """)
        self._add_btn.setToolTip("Add a sub-agent")
        self._add_btn.clicked.connect(self.add_clicked)
        self._add_btn.hide()
        self.setMinimumHeight(320)

        # Marching-ants dash animation on connectors leading to a RUNNING
        # sub-agent — only ticks while something is actually running, so an
        # idle World view isn't burning cycles on an invisible animation.
        self._dash_phase = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._advance_animation)

    def set_data(self, lead_name: str, sub_agents: list[dict], all_companions: list[dict]):
        C = self._C
        count_txt = f"{len(sub_agents)} sub-agent{'s' if len(sub_agents) != 1 else ''}"
        if self._lead_card is None:
            self._lead_card = _NodeCard(lead_name, "lead companion", count_txt, C.PRI, C, None, self)
            self._lead_card.resize(self.LEAD_W, self.LEAD_H)
            self._lead_card.show()
        else:
            self._lead_card.update_content(lead_name, count_txt)

        from ui import companion_color

        new_ids = {comp["id"] for comp in sub_agents}
        for old_id in list(self._sub_cards):
            if old_id not in new_ids:
                self._sub_cards.pop(old_id).deleteLater()
                self._manual_positions.pop(old_id, None)

        for comp in sub_agents:
            cid = comp["id"]
            color = companion_color(cid, all_companions)
            subtitle = comp.get("specialty") or "sub-agent"
            card = self._sub_cards.get(cid)
            if card is None:
                card = _NodeCard(comp["name"], subtitle, "", color, C, cid, self)
                card.resize(self.NODE_W, self.NODE_H)
                card.dragged.connect(self._on_card_dragged)
                card.edit_clicked.connect(self.edit_requested)
                card.delete_clicked.connect(self.delete_requested)
                card.show()
                self._sub_cards[cid] = card
            else:
                card.update_content(comp["name"], subtitle)

        self._layout_nodes()
        self.refresh_statuses()

    def _on_card_dragged(self, companion_id: str, x: int, y: int):
        self._manual_positions[companion_id] = (x, y)
        self.update()

    def _layout_nodes(self):
        if self._lead_card is None:
            return
        W = max(self.width(), 700)
        self._lead_card.move((W - self.LEAD_W) // 2, self.TOP_MARGIN)

        cards = list(self._sub_cards.items())
        auto_cards = [(cid, c) for cid, c in cards if cid not in self._manual_positions]
        row_y = self.TOP_MARGIN + self.LEAD_H + self.TRUNK_GAP
        n = len(auto_cards)
        if n:
            total_w = n * self.NODE_W + (n - 1) * self.H_GAP
            start_x = max(20, (W - total_w) // 2)
            for i, (cid, card) in enumerate(auto_cards):
                card.move(start_x + i * (self.NODE_W + self.H_GAP), row_y)

        for cid, (x, y) in self._manual_positions.items():
            card = self._sub_cards.get(cid)
            if card:
                card.move(x, y)

        max_y = max([row_y] + [c.y() + c.height() for c in self._sub_cards.values()])
        btn_y = max_y + 20
        self._add_btn.move(W // 2 - 17, btn_y)
        self._add_btn.show()
        self.setMinimumHeight(btn_y + 60)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_nodes()

    def refresh_statuses(self):
        if not self._sub_cards:
            return
        from actions.claude_companion import get_status
        any_running = False
        for cid, card in self._sub_cards.items():
            status = get_status(cid)
            card.set_status(status)
            any_running = any_running or status == "running"

        if any_running and not self._anim_tmr.isActive():
            self._anim_tmr.start(50)
        elif not any_running and self._anim_tmr.isActive():
            self._anim_tmr.stop()
            self.update()

    def _advance_animation(self):
        self._dash_phase = (self._dash_phase + 1.2) % 1000
        for card in self._sub_cards.values():
            card.pulse(self._dash_phase)
        self.update()

    def _draw_grid(self, painter: QPainter):
        C = self._C
        spacing = 28
        color = QColor(C.BORDER)
        color.setAlpha(160)
        pen = QPen(color)
        pen.setWidth(1)
        painter.setPen(pen)
        w, h = self.width(), self.height()
        for x in range(0, w, spacing):
            painter.drawLine(x, 0, x, h)
        for y in range(0, h, spacing):
            painter.drawLine(0, y, w, y)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        self._draw_grid(painter)
        if not self._lead_card or not self._sub_cards:
            painter.end()
            return
        lead_bottom = QPoint(
            self._lead_card.x() + self._lead_card.width() // 2,
            self._lead_card.y() + self._lead_card.height(),
        )
        trunk_y = lead_bottom.y() + self.TRUNK_GAP // 2
        for card in self._sub_cards.values():
            pen = QPen(QColor(card.color))
            pen.setWidth(2 if card.is_running else 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            if card.is_running:
                pen.setDashOffset(self._dash_phase)
            painter.setPen(pen)
            top_center = QPoint(card.x() + card.width() // 2, card.y())
            painter.drawLine(lead_bottom, QPoint(lead_bottom.x(), trunk_y))
            painter.drawLine(QPoint(lead_bottom.x(), trunk_y), QPoint(top_center.x(), trunk_y))
            painter.drawLine(QPoint(top_center.x(), trunk_y), top_center)


class _NodeCard(QFrame):
    """companion_id is None for the (fixed, non-draggable) lead node; a
    real id makes the card draggable and emits `dragged` on release so the
    canvas can remember the manually-chosen position."""
    dragged = pyqtSignal(str, int, int)
    edit_clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, name: str, subtitle: str, extra: str, color: str, C, companion_id: str | None, parent=None):
        super().__init__(parent)
        self.color = color
        self.is_running = False
        self.companion_id = companion_id
        self._C = C
        self._drag_offset: QPoint | None = None
        if companion_id is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._apply_border(color)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._icon = QLabel(name[0].upper() if name else "?")
        self._icon.setFixedSize(28, 28)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._icon.setStyleSheet(f"background: {color}; color: #000000; border: none; border-radius: 14px;")
        top.addWidget(self._icon)

        # QLabel is itself a QFrame subclass, so the card's own `QFrame {
        # border: ... }` rule (_apply_border) cascades down and boxes every
        # label below unless each one explicitly opts out with border: none.
        name_col = QVBoxLayout()
        name_col.setSpacing(0)
        self._name_lbl = QLabel(name)
        self._name_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._name_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
        name_col.addWidget(self._name_lbl)
        self._sub_lbl = QLabel(subtitle)
        self._sub_lbl.setFont(QFont("Segoe UI", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        self._sub_lbl.setWordWrap(True)
        name_col.addWidget(self._sub_lbl)
        top.addLayout(name_col, stretch=1)

        if companion_id is not None:
            edit_btn = QPushButton("✎")
            del_btn = QPushButton("×")
            for btn, color, cb in (
                (edit_btn, C.TEXT_MED, lambda: self.edit_clicked.emit(self.companion_id)),
                (del_btn, C.RED, lambda: self.delete_clicked.emit(self.companion_id)),
            ):
                btn.setFixedSize(18, 18)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                btn.setStyleSheet(
                    f"QPushButton {{ color: {color}; background: transparent; border: none; }}"
                    f"QPushButton:hover {{ color: {C.TEXT}; }}"
                )
                btn.clicked.connect(cb)
                top.addWidget(btn)

        lay.addLayout(top)

        self._status_lbl = QLabel("○ IDLE")
        self._status_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._status_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        lay.addWidget(self._status_lbl)

        self._extra_lbl = None
        if extra:
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet(f"border: none; border-top: 1px solid {C.BORDER};")
            lay.addWidget(sep)
            self._extra_lbl = QLabel(extra)
            self._extra_lbl.setFont(QFont("Segoe UI", 7))
            self._extra_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
            lay.addWidget(self._extra_lbl)
        else:
            lay.addStretch()

    def _apply_border(self, color: str, width: int = 1):
        C = self._C
        self.setStyleSheet(
            f"QFrame {{ background: {C.PANEL2_BG}; border: {width}px solid {color}; border-radius: 12px; }}"
        )

    def update_content(self, name: str, subtitle_or_extra: str):
        self._name_lbl.setText(name)
        if self._extra_lbl is not None:
            self._extra_lbl.setText(subtitle_or_extra)
        else:
            self._sub_lbl.setText(subtitle_or_extra)

    def set_status(self, status: str):
        C = self._C
        self.is_running = status == "running"
        if self.is_running:
            self._status_lbl.setText("● RUNNING")
            self._status_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        else:
            self._status_lbl.setText("○ IDLE")
            self._status_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
            self._apply_border(self.color)

    def pulse(self, phase: float):
        """Breathing border glow while running — called on the canvas's
        fast animation timer, only for cards currently marked running."""
        if not self.is_running:
            return
        from ui import lerp_hex
        C = self._C
        t = 0.5 + 0.5 * math.sin(phase * 0.12)
        glow = lerp_hex(self.color, C.WHITE, t * 0.5)
        self._apply_border(glow, width=2)

    def mousePressEvent(self, event: QMouseEvent):
        if self.companion_id is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_offset is not None:
            new_pos = self.mapToParent(event.pos() - self._drag_offset)
            self.move(new_pos)
            if self.parent():
                self.parent().update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.dragged.emit(self.companion_id, self.x(), self.y())
        super().mouseReleaseEvent(event)


class _AddSubAgentDialog(QDialog):
    """companion is None when creating a new sub-agent, or an existing
    companion dict when editing one in place (pre-fills the fields and
    updates that entry on save instead of appending a new one)."""

    def __init__(self, C, companion: dict | None, parent=None):
        super().__init__(parent)
        self._C = C
        self._companion = companion
        self.setWindowTitle("Edit sub-agent" if companion else "Add a sub-agent")
        self.setFixedWidth(360)
        self.setStyleSheet(f"QDialog {{ background: {C.PANEL_BG}; }}")
        self._build_ui()

    def _style_input(self, inp: QLineEdit):
        C = self._C
        inp.setFont(QFont("Segoe UI", 9))
        inp.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px 6px;")

    def _labeled(self, lay, text: str):
        C = self._C
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(lbl)

    def _build_ui(self):
        C = self._C
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel("Edit sub-agent" if self._companion else "New sub-agent")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(title)

        note_text = "Runs on the Claude Agent SDK. The lead companion can delegate tasks to it by name via delegate_to_agent."
        if self._companion:
            note_text += " Saving resets its conversation — it starts fresh under the new identity next time it's given a task."
        note = QLabel(note_text)
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._labeled(lay, "Name")
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Forge")
        self._style_input(self._name)
        lay.addWidget(self._name)

        self._labeled(lay, "Specialty — one line, used for routing")
        self._specialty = QLineEdit()
        self._specialty.setPlaceholderText("e.g. coding, debugging, technical planning")
        self._style_input(self._specialty)
        lay.addWidget(self._specialty)

        self._labeled(lay, "System prompt — its identity and instructions")
        self._prompt = QTextEdit()
        self._prompt.setFixedHeight(90)
        self._prompt.setFont(QFont("Segoe UI", 9))
        self._prompt.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px;")
        lay.addWidget(self._prompt)

        if self._companion:
            self._name.setText(self._companion.get("name", ""))
            self._specialty.setText(self._companion.get("specialty", ""))
            self._prompt.setPlainText(self._companion.get("system_prompt", ""))

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont("Segoe UI", 8))
        self._status_lbl.setStyleSheet(f"color: {C.RED}; background: transparent;")
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

        btn = QPushButton("SAVE CHANGES" if self._companion else "+ CREATE")
        btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{ color: {C.GREEN}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 6px 12px; }}
            QPushButton:hover {{ border: 1px solid {C.GREEN}; }}
        """)
        btn.clicked.connect(self._on_submit)
        lay.addWidget(btn)

    def _on_submit(self):
        name = self._name.text().strip()
        specialty = self._specialty.text().strip()
        prompt = self._prompt.toPlainText().strip()
        if not name or not prompt:
            self._status_lbl.setText("Name and system prompt are both required.")
            return

        settings = settings_store.load_settings()
        if self._companion:
            for c in settings["companions"]:
                if c["id"] == self._companion["id"]:
                    c["name"] = name
                    c["specialty"] = specialty
                    c["system_prompt"] = prompt
                    break
        else:
            settings["companions"].append({
                "id": settings_store.new_id(),
                "name": name,
                "backend": "claude_agent",
                "model": "",
                "system_prompt": prompt,
                "specialty": specialty,
                "voice": "",
                "memory_namespace": settings_store.new_id(),
                "mcp_server_ids": [],
                "enabled": True,
            })
        settings_store.save_settings(settings)
        self.accept()
