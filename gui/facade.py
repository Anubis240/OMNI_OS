"""OmniUI — the only part of the window the voice assistant touches.

Every method is safe to call from any thread: anything that changes the
window goes through a Qt signal, which Qt delivers on the GUI thread.
"""

from __future__ import annotations

import sys
import time

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from gui.theme import C
from gui.window import MainWindow


class _Relay(QObject):
    phone_connected = pyqtSignal()
    phone_disconnected = pyqtSignal()


class _EventLoop:
    """`window.root.mainloop()` — starts the Qt event loop."""
    def __init__(self, app: QApplication):
        self._app = app

    def mainloop(self) -> None:
        self._app.exec()


class OmniUI:
    def __init__(self):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        # Fusion under Windows dark mode draws unreadable tooltips unless styled.
        self._app.setStyleSheet(f"QToolTip {{ color: {C.TEXT}; background-color: {C.PANEL_BG};"
                                f" border: 1px solid {C.BORDER_A}; padding: 4px 6px; }}")
        self._win = MainWindow()
        self._relay = _Relay()
        self._relay.phone_connected.connect(self._win.phone_connected)
        self._relay.phone_disconnected.connect(self._win.phone_disconnected)
        self._win.show()
        self.root = _EventLoop(self._app)

    # --- state the assistant reads -----------------------------------------
    @property
    def muted(self) -> bool:
        return self._win.mic_muted

    @property
    def speech_muted(self) -> bool:
        return self._win.speech_muted

    @property
    def always_listening(self) -> bool:
        return self._win.always_listening

    @property
    def current_file(self) -> str | None:
        return self._win.current_file

    @property
    def voice(self) -> str:
        return self._win.voice

    # --- hooks the assistant installs ------------------------------------------
    def _hook(name):  # noqa: N805 — tiny property factory used only in this class body
        return property(lambda self: getattr(self._win, name),
                        lambda self, fn: setattr(self._win, name, fn))

    on_text_command = _hook("on_text_command")
    on_voice_change = _hook("on_voice_change")
    on_companions_changed = _hook("on_companions_changed")
    on_remote_clicked = _hook("on_remote_clicked")
    on_trader_clicked = _hook("on_trader_clicked")
    on_always_listening_toggled = _hook("on_always_listening_toggled")
    del _hook

    # --- things the assistant does to the window -------------------------------
    def write_log(self, text: str) -> None:
        self._win.log_line.emit(text)

    def set_state(self, state: str) -> None:
        self._win.state_changed.emit(state)

    def open_trader_panel(self) -> None:
        self._win.open_trader_requested.emit()

    def notify_phone_connected(self) -> None:
        self._relay.phone_connected.emit()

    def notify_phone_disconnected(self) -> None:
        self._relay.phone_disconnected.emit()

    def confirm_action(self, message: str) -> bool:
        """Real yes/no dialog; blocks the calling (non-GUI) thread. Times out as 'no'."""
        return self._win.confirm(message)

    def wait_for_api_key(self) -> None:
        while not self._win.ready:
            time.sleep(0.1)

    # --- the phone dashboard's trader view ---------------------------------------
    def get_trader_state(self) -> dict | None:
        """Stats/positions/watchlist for the phone, or None until the trader
        panel has been opened once (its engine is created lazily)."""
        panel = self._win.trader_panel
        if panel is None:
            return None
        engine = panel.engine
        # Trending suggestions: serve the cache, refresh in the background —
        # this is polled every few seconds and discovery can back off for 45 s.
        engine.ensure_suggestions_refreshing()
        # Equity is a single fast RPC read, so always fresh.
        engine.refresh_live_equity()
        return {**engine.public_state(),
                "watchlist": engine.config.get("watchlist") or [],
                "suggestions": engine.cached_suggestions()}

    def run_trader_action(self, action: str, payload: dict) -> dict:
        """A trade-panel action requested from the phone (runs on a worker
        thread; the engine marshals its own UI updates)."""
        panel = self._win.trader_panel
        if panel is None:
            return {"ok": False, "message": "trader panel not open on the PC yet"}
        engine = panel.engine
        symbol = (payload.get("symbol") or "").strip().upper()
        entry = (payload.get("entry") or "").strip()
        if action in ("buy", "add_watch"):
            if not entry:
                return {"ok": False, "message": "missing entry (SYM:CHAIN:0xADDR)"}
            return engine.buy_one(entry) if action == "buy" else engine.add_watch(entry)
        if action == "sell":
            if payload.get("force"):
                return engine.sell_one(symbol, True)
            pct = payload.get("pct", 100)
            return engine.sell_one(symbol) if pct == 100 else engine.partial_sell(symbol, pct)
        if action == "take_profit":
            pct = payload.get("pct")
            return engine.partial_sell(symbol, pct) if pct is not None else {"ok": False, "message": "missing pct"}
        if action == "remove_watch":
            return engine.remove_watch(symbol)
        if action == "command":
            text = (payload.get("text") or "").strip()
            return engine.command(text) if text else {"ok": False, "message": "empty command"}
        return {"ok": False, "message": f"unknown action: {action}"}
