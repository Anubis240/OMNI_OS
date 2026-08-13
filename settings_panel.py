"""SettingsPanel — lets a user add their own MCP servers, OpenAI/Anthropic
API keys (for those servers/skills to use — Omni's own core model stays
Gemini unless a companion below is set to a Claude backend), skill prompt
add-ons, additional companions, the optional trader panel, and optionally
point the "delegate to Claude" tool at their own Claude Code CLI install
instead of the hardcoded developer-machine paths it originally shipped
with.

Swapped into the center stack exactly like TraderPanel (see
MainWindow.open_settings_panel()) — same mechanic, own file, no cross-
import between the two panels. `from ui import C` is a deferred import for
the same reason trader_panel.py does it: avoids a circular import at
module-load time since ui.py only imports this lazily, on first click.
"""

from __future__ import annotations

import subprocess

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from core import settings_store


class SettingsPanel(QWidget):
    _status_sig = pyqtSignal(str, bool)  # (message, is_error)

    def __init__(self, parent=None):
        super().__init__(parent)
        from ui import C  # deferred — see module docstring
        self._C = C
        self.settings = settings_store.load_settings()

        self._mcp_rows: dict[str, dict] = {}
        self._skill_rows: dict[str, dict] = {}
        self._companion_mcp_checks: dict[str, QCheckBox] = {}  # server id -> checkbox, in the add-companion form

        self._status_sig.connect(self._show_status)
        self._build_ui()

    # ---------- layout ----------

    def _build_ui(self):
        C = self._C
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        title = QLabel("⚙ SETTINGS")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        outer.addWidget(title)

        self._status_lbl = QLabel("")
        self._status_lbl.setFont(QFont("Segoe UI", 8))
        self._status_lbl.setWordWrap(True)
        outer.addWidget(self._status_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(scroll, stretch=1)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(18)
        scroll.setWidget(content)

        col.addWidget(self._build_companions_section())
        col.addWidget(self._build_trader_section())
        col.addWidget(self._build_claude_section())
        col.addWidget(self._build_api_keys_section())
        col.addWidget(self._build_mcp_section())
        col.addWidget(self._build_skills_section())
        col.addStretch()

    def _section(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        C = self._C
        wrap = QWidget()
        wrap.setStyleSheet(f"background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 2px;")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        hdr = QLabel(title)
        hdr.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        return wrap, lay

    def _labeled_input(self, lay: QVBoxLayout, label: str, placeholder: str = "",
                        password: bool = False) -> QLineEdit:
        C = self._C
        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(lbl)
        inp = QLineEdit()
        inp.setPlaceholderText(placeholder)
        inp.setFont(QFont("Segoe UI", 9))
        inp.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px 6px;")
        if password:
            inp.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addWidget(inp)
        return inp

    def _make_button(self, label: str, cb, color: str | None = None) -> QPushButton:
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

    # ---------- Trader panel ----------

    def _build_trader_section(self) -> QWidget:
        C = self._C
        wrap, lay = self._section("TRADER PANEL")

        note = QLabel(
            "Optional built-in crypto trading panel, guarded by pre-trade risk checks. "
            "Off by default. Trades only ever go through commands typed directly into "
            "the trader panel's own command bar — never by voice."
        )
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._trader_enabled = QCheckBox("Enable trader panel")
        self._trader_enabled.setFont(QFont("Segoe UI", 9))
        self._trader_enabled.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._trader_enabled.setChecked(bool(self.settings["trader"].get("enabled")))
        lay.addWidget(self._trader_enabled)

        lay.addWidget(self._make_button("SAVE", self._on_save_trader))
        return wrap

    def _on_save_trader(self):
        self.settings["trader"] = {"enabled": self._trader_enabled.isChecked()}
        self._persist("Trader panel setting saved.")

    # ---------- Companions ----------

    def _build_companions_section(self) -> QWidget:
        wrap, lay = self._section("COMPANIONS")
        C = self._C
        note = QLabel(
            "Add alternate personas with their own name and instructions. Only one is "
            "active at a time. A Voice companion drives the live voice session (takes "
            "effect on the next reconnect); a Text companion answers typed messages "
            "through its own Claude conversation instead — voice keeps using Omni's "
            "default identity either way, since there's no realtime voice API on the "
            "Claude side. With no companion active, Omni's default identity is used."
        )
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._companions_list_layout = QVBoxLayout()
        self._companions_list_layout.setSpacing(6)
        lay.addLayout(self._companions_list_layout)
        self._refresh_companions_list()

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        lay.addWidget(sep)

        add_lbl = QLabel("Add a companion")
        add_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        add_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(add_lbl)

        self._new_companion_name = self._labeled_input(lay, "Name", "e.g. Nova")

        backend_lbl = QLabel("Interaction mode")
        backend_lbl.setFont(QFont("Segoe UI", 8))
        backend_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(backend_lbl)
        self._new_companion_backend = QComboBox()
        # display text -> backend id. Voice uses the live Gemini session (voice
        # field below applies); Text uses its own turn-based Claude conversation
        # (typed input only — see main.py::_on_text_command) and ignores voice.
        self._new_companion_backend.addItem("Voice (real-time)", userData="gemini_live")
        self._new_companion_backend.addItem("Text (Claude-powered)", userData="claude_agent")
        self._new_companion_backend.setFont(QFont("Segoe UI", 9))
        self._new_companion_backend.setStyleSheet(
            f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px 6px;"
        )
        lay.addWidget(self._new_companion_backend)

        from ui import VOICES  # deferred — see module docstring
        voice_lbl = QLabel("Voice (used only for the Voice interaction mode)")
        voice_lbl.setFont(QFont("Segoe UI", 8))
        voice_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(voice_lbl)
        self._new_companion_voice = QComboBox()
        self._new_companion_voice.addItems(VOICES)
        self._new_companion_voice.setFont(QFont("Segoe UI", 9))
        self._new_companion_voice.setStyleSheet(
            f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px 6px;"
        )
        lay.addWidget(self._new_companion_voice)

        prompt_lbl = QLabel("System prompt — defines this companion's identity and behavior")
        prompt_lbl.setFont(QFont("Segoe UI", 8))
        prompt_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(prompt_lbl)
        self._new_companion_prompt = QTextEdit()
        self._new_companion_prompt.setFont(QFont("Segoe UI", 9))
        self._new_companion_prompt.setFixedHeight(100)
        self._new_companion_prompt.setStyleSheet(
            f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px;"
        )
        lay.addWidget(self._new_companion_prompt)

        # Snapshot of MCP servers at panel-build time — a server added later
        # via the MCP section below won't appear here until the app restarts
        # (settings panel is built once and kept alive for the app's lifetime).
        self._companion_mcp_checks = {}
        servers = self.settings["mcp_servers"]
        if servers:
            mcp_lbl = QLabel("MCP tools this companion can use")
            mcp_lbl.setFont(QFont("Segoe UI", 8))
            mcp_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(mcp_lbl)
            for server in servers:
                cb = QCheckBox(server["name"])
                cb.setFont(QFont("Segoe UI", 8))
                cb.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                lay.addWidget(cb)
                self._companion_mcp_checks[server["id"]] = cb

        lay.addWidget(self._make_button("+ ADD COMPANION", self._on_add_companion, C.GREEN))
        return wrap

    def _refresh_companions_list(self):
        while self._companions_list_layout.count():
            item = self._companions_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        C = self._C
        companions = self.settings["companions"]
        active_id = self.settings.get("active_companion_id") or ""

        # "Omni (default)" is always selectable — it's not a real companion
        # entry, just active_companion_id == "" (see _resolve_active_companion).
        default_row = QWidget()
        default_row.setStyleSheet("background: transparent;")
        drlay = QHBoxLayout(default_row)
        drlay.setContentsMargins(0, 0, 0, 0)
        is_default_active = not active_id
        default_lbl = QLabel(("★ " if is_default_active else "") + "Omni (default)")
        default_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold if is_default_active else QFont.Weight.Normal))
        default_lbl.setStyleSheet(f"color: {C.PRI if is_default_active else C.TEXT}; background: transparent;")
        drlay.addWidget(default_lbl, stretch=1)
        if not is_default_active:
            drlay.addWidget(self._make_button("SET ACTIVE", self._on_use_default_companion))
        self._companions_list_layout.addWidget(default_row)

        if not companions:
            empty = QLabel("No additional companions added yet.")
            empty.setFont(QFont("Segoe UI", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._companions_list_layout.addWidget(empty)
            return

        from ui import COMPANION_HUES  # deferred — see module docstring

        for i, comp in enumerate(companions):
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            is_active = comp["id"] == active_id
            hue = COMPANION_HUES[i % len(COMPANION_HUES)]
            dot = QLabel("●")
            dot.setFont(QFont("Segoe UI", 8))
            dot.setStyleSheet(f"color: {hue}; background: transparent;")
            dot.setFixedWidth(14)
            rlay.addWidget(dot)
            backend_label = {"gemini_live": "Voice", "claude_agent": "Text (Claude)"}.get(comp.get("backend"), comp.get("backend", "?"))
            lbl = QLabel(("★ " if is_active else "") + f"{comp['name']}  —  {backend_label}")
            lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold if is_active else QFont.Weight.Normal))
            lbl.setStyleSheet(f"color: {C.PRI if is_active else C.TEXT}; background: transparent;")
            lbl.setWordWrap(True)
            rlay.addWidget(lbl, stretch=1)
            if not is_active:
                set_active_btn = self._make_button(
                    "SET ACTIVE", lambda _c=False, cid=comp["id"]: self._on_set_active_companion(cid)
                )
                rlay.addWidget(set_active_btn)
            remove_btn = self._make_button(
                "REMOVE", lambda _c=False, cid=comp["id"]: self._on_remove_companion(cid), C.RED
            )
            rlay.addWidget(remove_btn)
            self._companions_list_layout.addWidget(row)

    def _on_set_active_companion(self, companion_id: str):
        self.settings["active_companion_id"] = companion_id
        self._refresh_companions_list()
        self._persist("Active companion changed — takes effect on next reconnect.")

    def _on_use_default_companion(self):
        self.settings["active_companion_id"] = ""
        self._refresh_companions_list()
        self._persist("Switched back to the default Omni companion.")

    def _on_add_companion(self):
        name = self._new_companion_name.text().strip()
        prompt = self._new_companion_prompt.toPlainText().strip()
        if not name or not prompt:
            self._status_sig.emit("Companions need both a name and a system prompt.", True)
            return
        companion_id = settings_store.new_id()
        self.settings["companions"].append({
            "id": companion_id,
            "name": name,
            "backend": self._new_companion_backend.currentData(),
            "model": "",
            "system_prompt": prompt,
            "voice": self._new_companion_voice.currentText(),
            "memory_namespace": companion_id,
            "mcp_server_ids": [sid for sid, cb in self._companion_mcp_checks.items() if cb.isChecked()],
            "enabled": True,
        })
        self._new_companion_name.clear()
        self._new_companion_prompt.clear()
        for cb in self._companion_mcp_checks.values():
            cb.setChecked(False)
        self._refresh_companions_list()
        self._persist(f"Added companion \"{name}\".")

    def _on_remove_companion(self, companion_id: str):
        self.settings["companions"] = [c for c in self.settings["companions"] if c["id"] != companion_id]
        if self.settings.get("active_companion_id") == companion_id:
            self.settings["active_companion_id"] = ""
        from actions.claude_companion import forget_session
        forget_session(companion_id)
        self._refresh_companions_list()
        self._persist("Companion removed.")

    # ---------- Claude Code delegation ----------

    def _build_claude_section(self) -> QWidget:
        C = self._C
        wrap, lay = self._section("CLAUDE CODE DELEGATION")

        note = QLabel(
            "Lets Omni hand off coding/project work to a local Claude Code CLI "
            "session, scoped to a folder of your choice. Off by default — the "
            "\"delegate to Claude\" tool just declines politely until this is set up."
        )
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        ca = self.settings["claude_agent"]
        self._claude_enabled = QCheckBox("Enable Claude Code delegation")
        self._claude_enabled.setFont(QFont("Segoe UI", 9))
        self._claude_enabled.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._claude_enabled.setChecked(bool(ca.get("enabled")))
        lay.addWidget(self._claude_enabled)

        self._claude_cli = self._labeled_input(lay, "Claude CLI path", r"C:\Users\you\AppData\Roaming\npm\claude.cmd")
        self._claude_cli.setText(ca.get("cliPath", ""))
        browse_cli = self._make_button("BROWSE…", self._on_browse_cli)
        lay.addWidget(browse_cli)

        self._claude_vault = self._labeled_input(lay, "Working directory (vault/project root)")
        self._claude_vault.setText(ca.get("vaultDir", ""))
        browse_vault = self._make_button("BROWSE…", lambda: self._browse_dir(self._claude_vault))
        lay.addWidget(browse_vault)

        self._claude_extra = self._labeled_input(lay, "Extra allowed directory (optional)")
        self._claude_extra.setText(ca.get("extraDir", ""))
        browse_extra = self._make_button("BROWSE…", lambda: self._browse_dir(self._claude_extra))
        lay.addWidget(browse_extra)

        lay.addWidget(self._make_button("SAVE", self._on_save_claude))
        return wrap

    def _on_browse_cli(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Claude CLI", "", "Executables (*.cmd *.exe);;All files (*)")
        if path:
            self._claude_cli.setText(path)

    def _browse_dir(self, target: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "Select directory")
        if path:
            target.setText(path)

    def _on_save_claude(self):
        cli = self._claude_cli.text().strip()
        enabled = self._claude_enabled.isChecked()
        if enabled and not cli:
            self._status_sig.emit("Set a Claude CLI path before enabling delegation.", True)
            return
        self.settings["claude_agent"] = {
            "enabled": enabled, "cliPath": cli,
            "vaultDir": self._claude_vault.text().strip(),
            "extraDir": self._claude_extra.text().strip(),
        }
        self._persist("Claude Code delegation settings saved.")

    # ---------- API keys ----------

    def _build_api_keys_section(self) -> QWidget:
        wrap, lay = self._section("API KEYS")
        C = self._C
        note = QLabel(
            "For custom MCP servers or skills you add below that need their own key, "
            "or a companion set to the Claude backend below."
        )
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        keys = self.settings["api_keys"]
        self._openai_key = self._labeled_input(lay, "OpenAI API key", "sk-...", password=True)
        self._openai_key.setText(keys.get("openai", ""))
        self._anthropic_key = self._labeled_input(lay, "Anthropic API key", "sk-ant-...", password=True)
        self._anthropic_key.setText(keys.get("anthropic", ""))
        lay.addWidget(self._make_button("SAVE", self._on_save_api_keys))

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        lay.addWidget(sep)

        custom_lbl = QLabel(
            "Custom keys — reference one anywhere in a Skill's instructions or an MCP "
            "server's API Key field as {{NAME}} and it's substituted in automatically."
        )
        custom_lbl.setFont(QFont("Segoe UI", 8))
        custom_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        custom_lbl.setWordWrap(True)
        lay.addWidget(custom_lbl)

        self._custom_keys_list_layout = QVBoxLayout()
        self._custom_keys_list_layout.setSpacing(6)
        lay.addLayout(self._custom_keys_list_layout)
        self._refresh_custom_keys_list()

        self._new_custom_key_name = self._labeled_input(lay, "Name", "e.g. PERPLEXITY_KEY")
        self._new_custom_key_value = self._labeled_input(lay, "Value", "", password=True)
        lay.addWidget(self._make_button("+ ADD KEY", self._on_add_custom_key, C.GREEN))
        return wrap

    def _on_save_api_keys(self):
        self.settings["api_keys"] = {
            "openai": self._openai_key.text().strip(),
            "anthropic": self._anthropic_key.text().strip(),
        }
        self._persist("API keys saved.")

    def _refresh_custom_keys_list(self):
        while self._custom_keys_list_layout.count():
            item = self._custom_keys_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        C = self._C
        custom_keys = self.settings.get("custom_api_keys", [])
        if not custom_keys:
            empty = QLabel("No custom keys added.")
            empty.setFont(QFont("Segoe UI", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._custom_keys_list_layout.addWidget(empty)
            return
        for key in custom_keys:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            name_lbl = QLabel(f"{key['name']} — ••••••••")
            name_lbl.setFont(QFont("Segoe UI", 8))
            name_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            rlay.addWidget(name_lbl, stretch=1)
            remove_btn = self._make_button("REMOVE", lambda _c=False, kid=key["id"]: self._on_remove_custom_key(kid), C.RED)
            rlay.addWidget(remove_btn)
            self._custom_keys_list_layout.addWidget(row)

    def _on_add_custom_key(self):
        name = self._new_custom_key_name.text().strip()
        value = self._new_custom_key_value.text().strip()
        if not name or not value:
            self._status_sig.emit("Custom keys need both a name and a value.", True)
            return
        self.settings.setdefault("custom_api_keys", []).append({
            "id": settings_store.new_id(), "name": name, "value": value,
        })
        self._new_custom_key_name.clear(); self._new_custom_key_value.clear()
        self._refresh_custom_keys_list()
        self._persist(f"Added key \"{name}\".")

    def _on_remove_custom_key(self, key_id: str):
        self.settings["custom_api_keys"] = [k for k in self.settings.get("custom_api_keys", []) if k["id"] != key_id]
        self._refresh_custom_keys_list()
        self._persist("Key removed.")

    # ---------- MCP servers ----------

    def _build_mcp_section(self) -> QWidget:
        wrap, lay = self._section("MCP SERVERS")
        C = self._C
        note = QLabel("Tools from servers added here become available for Omni to call, alongside its built-in tools.")
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._mcp_list_layout = QVBoxLayout()
        self._mcp_list_layout.setSpacing(6)
        lay.addLayout(self._mcp_list_layout)
        self._refresh_mcp_list()

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        lay.addWidget(sep)

        add_lbl = QLabel("Add a server")
        add_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        add_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(add_lbl)

        self._new_mcp_name = self._labeled_input(lay, "Name", "my-server")
        self._new_mcp_url = self._labeled_input(lay, "URL", "https://example.com/mcp")
        self._new_mcp_key = self._labeled_input(lay, "API key (optional)", "", password=True)
        lay.addWidget(self._make_button("+ ADD SERVER", self._on_add_mcp_server, C.GREEN))
        return wrap

    def _refresh_mcp_list(self):
        while self._mcp_list_layout.count():
            item = self._mcp_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        C = self._C
        servers = self.settings["mcp_servers"]
        if not servers:
            empty = QLabel("No custom MCP servers configured.")
            empty.setFont(QFont("Segoe UI", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._mcp_list_layout.addWidget(empty)
            return
        for server in servers:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"{server['name']}  —  {server['url']}")
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lbl.setWordWrap(True)
            rlay.addWidget(lbl, stretch=1)
            remove_btn = self._make_button("REMOVE", lambda _c=False, sid=server["id"]: self._on_remove_mcp_server(sid), C.RED)
            rlay.addWidget(remove_btn)
            self._mcp_list_layout.addWidget(row)

    def _on_add_mcp_server(self):
        name = self._new_mcp_name.text().strip()
        url = self._new_mcp_url.text().strip()
        if not name or not url:
            self._status_sig.emit("Name and URL are both required to add a server.", True)
            return
        self.settings["mcp_servers"].append({
            "id": settings_store.new_id(), "name": name, "url": url,
            "apiKey": self._new_mcp_key.text().strip(),
        })
        self._new_mcp_name.clear(); self._new_mcp_url.clear(); self._new_mcp_key.clear()
        self._refresh_mcp_list()
        self._persist(f"Added MCP server \"{name}\".")

    def _on_remove_mcp_server(self, server_id: str):
        self.settings["mcp_servers"] = [s for s in self.settings["mcp_servers"] if s["id"] != server_id]
        self._refresh_mcp_list()
        self._persist("Server removed.")

    # ---------- Skills ----------

    def _build_skills_section(self) -> QWidget:
        wrap, lay = self._section("SKILLS")
        C = self._C
        note = QLabel("Named blocks of extra instructions, appended to the active companion's system prompt when enabled.")
        note.setFont(QFont("Segoe UI", 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._skills_list_layout = QVBoxLayout()
        self._skills_list_layout.setSpacing(6)
        lay.addLayout(self._skills_list_layout)
        self._refresh_skills_list()

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        lay.addWidget(sep)

        add_lbl = QLabel("Add a skill")
        add_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        add_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(add_lbl)

        self._new_skill_name = self._labeled_input(lay, "Name", "e.g. house-style")
        content_lbl = QLabel("Instructions")
        content_lbl.setFont(QFont("Segoe UI", 8))
        content_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(content_lbl)
        self._new_skill_content = QTextEdit()
        self._new_skill_content.setFont(QFont("Segoe UI", 9))
        self._new_skill_content.setFixedHeight(80)
        self._new_skill_content.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px;")
        lay.addWidget(self._new_skill_content)
        lay.addWidget(self._make_button("+ ADD SKILL", self._on_add_skill, C.GREEN))
        return wrap

    def _refresh_skills_list(self):
        while self._skills_list_layout.count():
            item = self._skills_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        C = self._C
        skills = self.settings["skills"]
        if not skills:
            empty = QLabel("No skills added.")
            empty.setFont(QFont("Segoe UI", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._skills_list_layout.addWidget(empty)
            return
        for skill in skills:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox(skill["name"])
            cb.setFont(QFont("Segoe UI", 8))
            cb.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            cb.setChecked(bool(skill.get("enabled")))
            cb.toggled.connect(lambda checked, sid=skill["id"]: self._on_toggle_skill(sid, checked))
            rlay.addWidget(cb, stretch=1)
            remove_btn = self._make_button("REMOVE", lambda _c=False, sid=skill["id"]: self._on_remove_skill(sid), C.RED)
            rlay.addWidget(remove_btn)
            self._skills_list_layout.addWidget(row)

    def _on_add_skill(self):
        name = self._new_skill_name.text().strip()
        content = self._new_skill_content.toPlainText().strip()
        if not name or not content:
            self._status_sig.emit("Skills need both a name and instructions.", True)
            return
        self.settings["skills"].append({
            "id": settings_store.new_id(), "name": name, "content": content, "enabled": True,
        })
        self._new_skill_name.clear(); self._new_skill_content.clear()
        self._refresh_skills_list()
        self._persist(f"Added skill \"{name}\".")

    def _on_toggle_skill(self, skill_id: str, checked: bool):
        for skill in self.settings["skills"]:
            if skill["id"] == skill_id:
                skill["enabled"] = checked
        self._persist(None)

    def _on_remove_skill(self, skill_id: str):
        self.settings["skills"] = [s for s in self.settings["skills"] if s["id"] != skill_id]
        self._refresh_skills_list()
        self._persist("Skill removed.")

    # ---------- persistence ----------

    def _persist(self, message: str | None):
        settings_store.save_settings(self.settings)
        if message:
            self._status_sig.emit(message, False)

    def _show_status(self, message: str, is_error: bool):
        C = self._C
        self._status_lbl.setText(message)
        self._status_lbl.setStyleSheet(f"color: {C.RED if is_error else C.GREEN}; background: transparent;")
