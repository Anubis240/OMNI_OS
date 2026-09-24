"""MainWindow — the Omni-OS desktop window.

Layout: an icon sidebar on the left; the rest is a stack showing either the
orb or one of the full panels (trader, settings, integrations, world). Over
the orb float a top bar, a status card (top left), the chat panel (right)
and the companion switcher (bottom) — positioned by hand in _place_floaters.
"""

from __future__ import annotations

import platform
import threading
import time
from pathlib import Path

import psutil
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolTip,
    QVBoxLayout, QWidget,
)

from core import settings_store
from core.app_paths import get_app_version
from gui import firstrun
from gui.chatlog import ChatLog
from gui.gauges import Gauge
from gui.orb import OrbView
from gui.pairing import PairingCard
from gui.sysinfo import sampler
from gui.theme import C, companion_color, load_saved_voice

SIDEBAR = 64
TOP_BAR = 56
CARD_W = 220
CHAT_W = 340
CONFIRM_WAIT = 120   # seconds a tool may wait on a confirmation; less than its 200 s limit


class _Question:
    """A yes/no asked from a worker thread and answered on the GUI thread."""
    def __init__(self, text: str):
        self.text = text
        self.answered = threading.Event()
        self.yes = False


def _icon_button(glyph: str, tip: str, size: int = 40) -> QPushButton:
    btn = QPushButton(glyph)
    btn.setFixedSize(size, size)
    btn.setToolTip(tip)
    btn.setFont(QFont("Segoe UI", 14))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _flat_style(color: str, hover: str = C.PRI) -> str:
    return (f"QPushButton {{ color: {color}; background: transparent; border: none; border-radius: 20px; }}"
            f"QPushButton:hover {{ background: {C.PANEL2_BG}; color: {hover}; }}")


def _card(parent: QWidget, width: int) -> QWidget:
    w = QWidget(parent)
    w.setFixedWidth(width)
    w.setObjectName("card")
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet(f"#card {{ background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 14px; }}")
    return w


def _text(text: str, size: int = 8, color: str = C.TEXT_MED, bold: bool = False) -> QLabel:
    lbl = QLabel(text)
    lbl.setFont(QFont("Segoe UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
    lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lbl


class MainWindow(QMainWindow):
    # Everything a worker thread asks of the window goes through a signal,
    # so Qt runs it on the GUI thread. (Building widgets off the GUI thread —
    # e.g. opening the trader panel by voice — was the likely cause of the
    # long freezes in Bug 11.)
    log_line = pyqtSignal(str)
    state_changed = pyqtSignal(str)
    open_trader_requested = pyqtSignal()
    question_asked = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OMNI-OS")
        self.setMinimumSize(820, 580)
        self.resize(980, 700)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move((screen.width() - 980) // 2, (screen.height() - 700) // 2)

        # Hooks the voice assistant fills in.
        self.on_text_command = None
        self.on_voice_change = None
        self.on_companions_changed = None
        self.on_remote_clicked = None
        self.on_trader_clicked = None
        self.on_always_listening_toggled = None

        self.mic_muted = False
        self.speech_muted = False
        self.always_listening = False
        self.current_file: str | None = None
        self.voice = load_saved_voice()
        self.ready = firstrun.has_api_key()
        self._launched_at = time.time()
        self._panels: dict[str, QWidget] = {}
        self._pairing: PairingCard | None = None
        self._key_prompt: firstrun.KeyPrompt | None = None
        self._hover_tips = False

        self._build()
        QApplication.instance().installEventFilter(self)

        self.log_line.connect(self.chat.post)
        self.state_changed.connect(self._apply_state)
        self.open_trader_requested.connect(self.show_trader)
        self.question_asked.connect(self._ask)

        for key, action in (("F4", self.toggle_mic), ("F5", self.toggle_speech), ("F11", self._toggle_fullscreen)):
            QShortcut(QKeySequence(key), self).activated.connect(action)

        sampler.start()
        ticker = QTimer(self)
        ticker.timeout.connect(self._tick)
        ticker.start(1000)
        self._tick()

        if not self.ready:
            self._show_key_prompt()

    # --- building ------------------------------------------------------------
    def _build(self) -> None:
        root = QWidget()
        root.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(root)
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._sidebar())

        self.orb = OrbView()
        self._tint_for_active_companion(animate=False)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stack.addWidget(self.orb)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.addSpacing(TOP_BAR)   # full panels start below the top bar, not under it
        column.addWidget(self.stack, 1)
        row.addLayout(column, 1)

        self._top = self._top_bar(root)
        self._status = self._status_card(root)
        self._chat = self._chat_card(root)
        self._switcher = self._companion_switcher(root)
        self._credit = _text("© KONDUX", 7, C.TEXT_DIM)
        self._credit.setParent(root)
        self._credit.adjustSize()
        self._place_floaters()
        self._refresh_trader_button()
        self._refresh_companion_name()

    def _sidebar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedWidth(SIDEBAR)
        bar.setStyleSheet(f"background: {C.PANEL_BG}; border-right: 1px solid {C.BORDER};")
        col = QVBoxLayout(bar)
        col.setContentsMargins(12, 14, 12, 12)
        col.setSpacing(8)

        home = _icon_button("Ω", "Home")
        home.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        home.setStyleSheet(_flat_style(C.PRI))
        home.clicked.connect(self.show_home)
        col.addWidget(home)
        col.addSpacing(8)

        self._mic_btn = _icon_button("🎙", "Mute microphone [F4]")
        self._mic_btn.clicked.connect(self.toggle_mic)
        self._speech_btn = _icon_button("🔊", "Mute speech [F5]")
        self._speech_btn.clicked.connect(self.toggle_speech)
        col.addWidget(self._mic_btn)
        col.addWidget(self._speech_btn)
        self._style_mute_buttons()
        col.addSpacing(6)

        self._trader_btn = _icon_button("◈", "Trader panel")
        self._trader_btn.setStyleSheet(_flat_style(C.ACC2))
        self._trader_btn.clicked.connect(lambda: self._toggle_panel("trader"))
        col.addWidget(self._trader_btn)
        for glyph, tip, action in (("⛓", "Remote control", self._open_pairing),
                                   ("▦", "Integrations", lambda: self._toggle_panel("integrations")),
                                   ("◎", "World", lambda: self._toggle_panel("world")),
                                   ("⚙", "Settings", lambda: self._toggle_panel("settings")),
                                   ("⛶", "Fullscreen [F11]", self._toggle_fullscreen)):
            btn = _icon_button(glyph, tip)
            btn.setStyleSheet(_flat_style(C.TEXT_MED))
            btn.clicked.connect(action)
            col.addWidget(btn)
        col.addStretch()
        return bar

    def _top_bar(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        bar.setStyleSheet("background: transparent;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 12, 20, 0)
        row.addWidget(_text("OMNI_OS", 13, C.PRI, bold=True))
        row.addStretch()
        self._listen_btn = QPushButton()
        self._listen_btn.setFixedHeight(32)
        self._listen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._listen_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._listen_btn.clicked.connect(self.toggle_listening)
        self._style_listen_button()
        row.addWidget(self._listen_btn)
        row.addStretch()
        self._state_pill = QLabel()
        self._state_pill.setFixedHeight(26)
        self._state_pill.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._state_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._state_pill)
        row.addSpacing(10)
        self._clock = _text("", 13, C.TEXT, bold=True)
        row.addWidget(self._clock)
        self._refresh_state_pill()
        return bar

    def _status_card(self, parent: QWidget) -> QWidget:
        card = _card(parent, CARD_W)
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(4)

        self._metrics = QWidget()
        m = QVBoxLayout(self._metrics)
        m.setContentsMargins(0, 0, 0, 0)
        m.setSpacing(2)
        m.addWidget(_text("SYSTEM", 8, C.TEXT_MED, bold=True))
        self._gauges = {name: Gauge(name, color) for name, color in
                        (("CPU", C.PRI), ("MEM", C.ACC2), ("NET", C.GREEN), ("GPU", C.ACC), ("TEMP", C.RED))}
        for gauge in self._gauges.values():
            m.addWidget(gauge)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER};")
        m.addWidget(line)
        self._uptime = _text("UP  00:00", 8, C.GREEN, bold=True)
        self._processes = _text("PROC  —")
        m.addWidget(self._uptime)
        m.addWidget(self._processes)
        m.addWidget(_text("OS  " + {"Windows": "WIN", "Darwin": "macOS"}.get(platform.system(), platform.system().upper()),
                          8, C.ACC2))
        version = get_app_version()
        if version:   # so testers can tell builds apart (GEMZ4US 2026-09-20)
            m.addWidget(_text(f"v{version}"))
        col.addWidget(self._metrics)

        # The trader panel mounts its config controls here (set_left_panel_extra).
        self._extra_box = QWidget()
        self._extra_layout = QVBoxLayout(self._extra_box)
        self._extra_layout.setContentsMargins(0, 0, 0, 0)
        self._extra_scroll = QScrollArea()
        self._extra_scroll.setWidgetResizable(True)
        self._extra_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._extra_scroll.setMaximumHeight(280)
        self._extra_scroll.setStyleSheet("background: transparent; border: none;")
        self._extra_scroll.setWidget(self._extra_box)
        self._extra_scroll.hide()
        col.addWidget(self._extra_scroll)
        return card

    def _chat_card(self, parent: QWidget) -> QWidget:
        card = _card(parent, CHAT_W)
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 12, 14, 10)
        col.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_text("●", 8, C.GREEN))
        self._chat_title = _text("OMNI", 9, C.TEXT, bold=True)
        head.addWidget(self._chat_title)
        head.addStretch()
        col.addLayout(head)
        self.chat = ChatLog()
        col.addWidget(self.chat, 1)

        entry = QHBoxLayout()
        attach = QPushButton("+")
        attach.setToolTip("Attach a file")
        attach.setFixedSize(30, 30)
        attach.setCursor(Qt.CursorShape.PointingHandCursor)
        attach.setStyleSheet(f"QPushButton {{ color: {C.TEXT_MED}; background: {C.PANEL2_BG}; border: none; border-radius: 15px; font-size: 16px; }}"
                             f"QPushButton:hover {{ color: {C.PRI}; }}")
        attach.clicked.connect(self._pick_file)
        entry.addWidget(attach)
        self._entry = QLineEdit()
        self._entry.setPlaceholderText("Message Omni…")
        self._entry.setFixedHeight(34)
        self._entry.setStyleSheet(f"QLineEdit {{ background: {C.PANEL2_BG}; color: {C.WHITE}; border: 1px solid {C.BORDER};"
                                  f" border-radius: 17px; padding: 3px 14px; }} QLineEdit:focus {{ border-color: {C.PRI}; }}")
        self._entry.returnPressed.connect(self._submit_text)
        entry.addWidget(self._entry, 1)
        send = QPushButton("↑")
        send.setFixedSize(34, 34)
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"QPushButton {{ background: {C.PRI}; color: #000; border: none; border-radius: 17px; font-weight: bold; }}"
                           f"QPushButton:hover {{ background: {C.ACC}; }}")
        send.clicked.connect(self._submit_text)
        entry.addWidget(send)
        col.addLayout(entry)
        note = _text("AI-generated · double-check anything important", 6, C.TEXT_DIM)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(note)
        return card

    def _companion_switcher(self, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        box.setFixedSize(230, 40)
        box.setStyleSheet("background: transparent;")
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        for glyph, step in (("‹", -1), (None, 0), ("›", 1)):
            if glyph is None:
                self._companion_name = _text("OMNI", 11, C.PRI, bold=True)
                self._companion_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._companion_name.setMinimumWidth(150)
                row.addWidget(self._companion_name)
                continue
            btn = _icon_button(glyph, "Previous companion" if step < 0 else "Next companion", 28)
            btn.setStyleSheet(_flat_style(C.TEXT_MED))
            btn.clicked.connect(lambda _=False, s=step: self._switch_companion(s))
            row.addWidget(btn)
        return box

    def _place_floaters(self) -> None:
        root = self.centralWidget()
        if root is None or not hasattr(self, "_credit"):
            return
        w, h = root.width(), root.height()
        self._top.setGeometry(SIDEBAR, 0, max(0, w - SIDEBAR), TOP_BAR)
        self._status.adjustSize()
        self._status.move(SIDEBAR + 16, TOP_BAR + 12)
        self._chat.resize(CHAT_W, max(240, h - TOP_BAR - 42))   # clears the credit line below
        self._chat.move(max(SIDEBAR + 16, w - CHAT_W - 16), TOP_BAR + 12)
        self._switcher.move(SIDEBAR + (w - SIDEBAR - self._switcher.width()) // 2, h - 66)
        self._credit.move(w - self._credit.width() - 14, h - 22)
        for overlay, size in ((self._pairing, (PairingCard.WIDTH, PairingCard.HEIGHT)),
                              (self._key_prompt, (firstrun.KeyPrompt.WIDTH, firstrun.KeyPrompt.HEIGHT))):
            if overlay is not None and overlay.isVisible():
                overlay.setGeometry((w - size[0]) // 2, (h - size[1]) // 2, *size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_floaters()

    # --- live readouts -----------------------------------------------------------
    def _tick(self) -> None:
        self._clock.setText(time.strftime("%H:%M:%S"))
        self._refresh_state_pill()
        r = sampler.latest
        self._gauges["CPU"].show_reading(r.cpu, f"{r.cpu:.0f}%")
        self._gauges["MEM"].show_reading(r.memory, f"{r.memory:.0f}%")
        net = r.network_mb_s
        self._gauges["NET"].show_reading(min(100, net * 10), f"{net * 1024:.0f} KB/s" if net < 1 else f"{net:.1f} MB/s")
        self._gauges["GPU"].show_reading(r.gpu, "n/a" if r.gpu is None else f"{r.gpu:.0f}%")
        self._gauges["TEMP"].show_reading(r.temperature, "n/a" if r.temperature is None else f"{r.temperature:.0f}°C")
        # This app's own uptime, not the computer's — testers use it to confirm a real restart.
        up = int(time.time() - self._launched_at)
        self._uptime.setText(f"UP  {up // 3600:02d}:{up % 3600 // 60:02d}")
        try:
            self._processes.setText(f"PROC  {len(psutil.pids())}")
        except Exception:
            pass

    def _refresh_state_pill(self) -> None:
        orb = self.orb
        if orb.muted:
            text, color = "MUTED", C.MUTED_C
        elif orb.speaking:
            text, color = "SPEAKING", C.ACC
        elif orb.state == "LISTENING":
            text, color = "LISTENING", C.GREEN
        else:
            text, color = orb.state, C.ACC2 if orb.state == "THINKING" else C.TEXT_MED
        self._state_pill.setText(text)
        self._state_pill.setStyleSheet(f"color: {color}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER};"
                                       f" border-radius: 13px; padding: 4px 14px;")

    def _apply_state(self, state: str) -> None:
        self.orb.state = state
        self.orb.speaking = state == "SPEAKING"
        self._refresh_state_pill()

    # --- mic / speech / listening ------------------------------------------------
    def _style_mute_buttons(self) -> None:
        for btn, muted, on_glyph in ((self._mic_btn, self.mic_muted, "🎙"), (self._speech_btn, self.speech_muted, "🔊")):
            btn.setText("🔇" if muted else on_glyph)   # same slashed icon for "off" on both (GEMZ4US Part 8)
            btn.setStyleSheet(
                f"QPushButton {{ color: {C.MUTED_C}; background: {C.PANEL2_BG}; border: 1px solid {C.MUTED_C}; border-radius: 20px; }}"
                if muted else _flat_style(C.TEXT_MED, C.GREEN))

    def toggle_mic(self) -> None:
        self.mic_muted = not self.mic_muted
        self.orb.muted = self.mic_muted
        self._style_mute_buttons()
        self._apply_state("MUTED" if self.mic_muted else "LISTENING")
        self.chat.post("SYS: Microphone muted." if self.mic_muted else "SYS: Microphone on.")

    def toggle_speech(self) -> None:
        self.speech_muted = not self.speech_muted
        self._style_mute_buttons()
        self.chat.post("SYS: Speech muted — Omni will answer in text only." if self.speech_muted
                       else "SYS: Speech on.")

    def _style_listen_button(self) -> None:
        on = self.always_listening
        self._listen_btn.setText("●  LISTENING" if on else "○  CLICK TO LISTEN")
        self._listen_btn.setStyleSheet(
            f"QPushButton {{ color: {C.PRI if on else C.TEXT_MED}; background: {C.PRI_GHO_BG if on else C.PANEL2_BG};"
            f" border: 1px solid {C.PRI if on else C.BORDER}; border-radius: 16px; padding: 4px 18px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; }}")

    def toggle_listening(self) -> None:
        self.always_listening = not self.always_listening
        self._style_listen_button()
        self.chat.post("SYS: Listening — click again to stop." if self.always_listening else "SYS: Listening stopped.")
        if self.on_always_listening_toggled:
            threading.Thread(target=self.on_always_listening_toggled, args=(self.always_listening,), daemon=True).start()

    # --- text and files -------------------------------------------------------
    def _submit_text(self) -> None:
        text = self._entry.text().strip()
        if not text:
            return
        self._entry.clear()
        self.chat.post(f"You: {text}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(text,), daemon=True).start()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file for Omni", str(Path.home()))
        if not path:
            return
        self.current_file = path
        info = Path(path)
        size_kb = info.stat().st_size / 1024
        size = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        self.chat.post(f"FILE: {info.name} ({size}) attached")
        if self.on_text_command:
            note = (f"[FILE ATTACHED] {path} ({size}). Let the user know you have {info.name} and ask what "
                    f"they'd like done with it; use file_processor for whatever they choose.")
            threading.Thread(target=self.on_text_command, args=(note,), daemon=True).start()

    # --- companions ---------------------------------------------------------------
    def _tint_for_active_companion(self, animate: bool) -> None:
        settings = settings_store.load_settings()
        active = settings.get("active_companion_id") or ""
        color = companion_color(active, settings.get("companions", [])) if active else C.PRI
        (self.orb.animate_companion_switch if animate else self.orb.set_tint)(color)

    def _refresh_companion_name(self) -> None:
        settings = settings_store.load_settings()
        active = settings.get("active_companion_id") or ""
        found = next((c for c in settings.get("companions", []) if c.get("id") == active), None)
        name = found["name"].upper() if found else "OMNI"
        self._companion_name.setText(name)
        self._chat_title.setText(name)

    def _switch_companion(self, step: int) -> None:
        settings = settings_store.load_settings()
        companions = settings.get("companions", [])
        ring = [""] + [c["id"] for c in companions]      # "" = the default Omni identity
        current = settings.get("active_companion_id") or ""
        target = ring[((ring.index(current) if current in ring else 0) + step) % len(ring)]
        if target == current:
            return   # nothing to switch to — don't log or reconnect for a no-op
        settings["active_companion_id"] = target
        settings_store.save_settings(settings)
        self._refresh_companion_name()
        self._tint_for_active_companion(animate=True)
        name = next((c["name"] for c in companions if c["id"] == target), "Omni (default)")
        self.chat.post(f"SYS: Switched to {name} — reconnecting now.")
        self._companions_changed()

    def _companions_changed(self) -> None:
        if self.on_companions_changed:
            self.on_companions_changed()

    # --- panels --------------------------------------------------------------------
    def _panel(self, name: str) -> QWidget:
        if name not in self._panels:
            if name == "trader":
                from trader_panel import TraderPanel
                panel = TraderPanel()
                panel.on_config_toggle = self._toggle_trader_config
            elif name == "settings":
                from settings_panel import SettingsPanel
                panel = SettingsPanel()
                panel.on_companions_changed = self._companions_changed
            elif name == "integrations":
                from integrations_panel import IntegrationsPanel
                panel = IntegrationsPanel()
                # a newly connected integration needs the live session to re-read its tools
                panel.on_integration_changed = self._companions_changed
            else:
                from world_panel import WorldPanel
                panel = WorldPanel()
                panel.on_companion_added = self._companions_changed
            self._panels[name] = panel
            self.stack.addWidget(panel)
        return self._panels[name]

    def _home_extras(self, visible: bool) -> None:
        self._chat.setVisible(visible)
        self._switcher.setVisible(visible)

    def _status_mode(self, mode: str) -> None:
        """'home': metrics; 'trader_config': the trader's config controls; 'hidden'."""
        self._status.setVisible(mode != "hidden")
        self._metrics.setVisible(mode == "home")
        self._extra_scroll.setVisible(mode == "trader_config")

    def _toggle_trader_config(self) -> None:
        showing = self._status.isVisible() and self._extra_scroll.isVisible()
        self._status_mode("hidden" if showing else "trader_config")

    def show_home(self) -> None:
        self.stack.setCurrentWidget(self.orb)
        self.set_left_panel_extra(None)
        self._home_extras(True)
        self._status_mode("home")
        self._refresh_trader_button()
        self._refresh_companion_name()

    def _show_panel(self, name: str) -> None:
        panel = self._panel(name)
        if name == "world":
            panel.refresh()
        elif name == "settings":
            panel.refresh_companions()   # World may have changed companions meanwhile
        self.stack.setCurrentWidget(panel)
        self.set_left_panel_extra(panel.left_panel_widget() if name == "trader" else None)
        self._home_extras(False)
        self._status_mode("hidden")

    def _toggle_panel(self, name: str) -> None:
        if self._panels.get(name) is not None and self.stack.currentWidget() is self._panels[name]:
            self.show_home()
        else:
            self._show_panel(name)

    def show_trader(self) -> None:
        """Open (never toggle closed) — for "open the trader" by voice."""
        self._show_panel("trader")

    def set_left_panel_extra(self, widget: QWidget | None) -> None:
        while self._extra_layout.count():
            old = self._extra_layout.takeAt(0).widget()
            if old is not None:
                old.setParent(None)
        if widget is not None:
            self._extra_layout.addWidget(widget)

    def _refresh_trader_button(self) -> None:
        self._trader_btn.setVisible(bool(settings_store.load_settings()["trader"]["enabled"]))

    @property
    def trader_panel(self):
        return self._panels.get("trader")

    # --- phone pairing ------------------------------------------------------------
    def _open_pairing(self) -> None:
        pairing = self.on_remote_clicked() if self.on_remote_clicked else None
        if not pairing:
            self.chat.post("SYS: Couldn't create a pairing key right now.")
            return
        if self._pairing is not None:
            self._pairing.dismiss()
        url, key, *rest = pairing
        card = PairingCard(url, key, rest[0] if rest else "", parent=self.centralWidget())
        card.request_new_key = self.on_remote_clicked
        card.closed.connect(lambda: setattr(self, "_pairing", None))
        self._pairing = card
        card.show()
        self._place_floaters()
        self.chat.post(f"SYS: Pairing key ready — {url}")

    def phone_connected(self) -> None:
        if self._pairing is not None and self._pairing.isVisible():
            self._pairing.mark_connected()

    def phone_disconnected(self) -> None:
        if self._pairing is not None and self._pairing.isVisible():
            self._pairing.mark_disconnected()

    # --- first run -------------------------------------------------------------
    def _show_key_prompt(self) -> None:
        self._key_prompt = firstrun.KeyPrompt(self.centralWidget())
        self._key_prompt.submitted.connect(self._key_saved)
        self._key_prompt.show()
        self._place_floaters()

    def _key_saved(self, _key: str) -> None:
        self._key_prompt.hide()
        self._key_prompt = None
        self.ready = True
        self.chat.post("SYS: Key saved — starting Omni-OS.")

    # --- confirmations from worker threads -----------------------------------------
    def _ask(self, question: _Question) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Confirm")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(question.text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        # Styled explicitly: Fusion + Windows dark mode leaves a stock box unreadable.
        box.setStyleSheet(f"QMessageBox {{ background: {C.PANEL_BG}; }} QLabel {{ color: {C.TEXT}; }}"
                          f"QPushButton {{ color: {C.TEXT}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER};"
                          f" border-radius: 6px; padding: 4px 14px; }}")
        question.yes = box.exec() == QMessageBox.StandardButton.Yes
        question.answered.set()

    def confirm(self, text: str, wait: float = CONFIRM_WAIT) -> bool:
        """Ask the user yes/no from any non-GUI thread; blocks that thread.
        No answer in time counts as no."""
        question = _Question(text)
        self.question_asked.emit(question)
        return question.answered.wait(wait) and question.yes

    # --- window chrome ---------------------------------------------------------
    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._hover_tips = True

    def eventFilter(self, obj, event):
        # Tooltips don't appear by themselves when the pointer arrives from
        # another window without a click (or right after re-activation), so
        # show them ourselves in that case (GEMZ4US C2, 2026-09-14…16).
        if (event.type() == QEvent.Type.Enter and (self._hover_tips or not self.isActiveWindow())
                and isinstance(obj, QWidget) and obj.toolTip() and self.isAncestorOf(obj)):
            QToolTip.showText(QCursor.pos(), obj.toolTip(), obj)
            self._hover_tips = False
        return super().eventFilter(obj, event)
