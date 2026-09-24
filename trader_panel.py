"""TraderPanel — the native trader UI, swapped in over the HUD in place of
the old separate Electron window (see MainWindow.open_trader_panel()).

Phase 1: paper-mode trading only, using trader/engine.py's TraderEngine.
Reuses the app's existing color palette (class C in ui.py) directly — no
new theme, no background video. `from ui import C, qcol` is a deferred
import (done inside __init__, not at module load time) so this module can
be imported by ui.py without a circular-import at load time; ui.py itself
only imports TraderPanel lazily, on first click of the TRADER button.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import webbrowser
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QTextBrowser,
    QVBoxLayout, QWidget,
)

from trader import chains as chains_mod
from trader import mcp_client
from trader.engine import TraderEngine
from trader.seraph_auth import SERAPH_WALLET_CONSOLE_URL, get_default_auth, format_identity

_FEED_MAX_ITEMS = 300
_FORCE_SELL_HINT_RE = re.compile(r"sell\s+(\S+)\s+force", re.I)

# Only chains with a well-established, unambiguous block explorer — no
# guessing at domains for the newer/exotic chains (Unichain, World Chain,
# Soneium, Robinhood Chain, Ink); the VIEW button just doesn't show for
# those rather than risk a wrong link.
CHAIN_EXPLORERS = {
    "ethereum": "https://etherscan.io/tx/",
    "optimism": "https://optimistic.etherscan.io/tx/",
    "base": "https://basescan.org/tx/",
    "arbitrum": "https://arbiscan.io/tx/",
    "polygon": "https://polygonscan.com/tx/",
}


def _full_tx_hash(tx_hash: str) -> str:
    return tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash


_CONFIG_FIELDS = [
    ("tradeSizeMinUsd", "Trade size min $"),
    ("tradeSizeMaxUsd", "Trade size max $"),
    ("maxOpenPositions", "Max open positions"),
    ("maxDailyTrades", "Max daily trades"),
    ("takeProfitPct", "Take profit %"),
    ("stopLossPct", "Stop loss %"),
    ("maxHoldHours", "Max hold (hours)"),
    ("minSignalScore", "Min signal score"),
    ("minLiquidityUsd", "Min liquidity $"),
    ("profitTargetUsd", "Profit target $"),
    ("maxDrawdownUsd", "Max drawdown $"),
    ("intervalMinutes", "Scan interval (min)"),
]


class McpKeySetupOverlay(QWidget):
    """Connect to Seraph through browser login or a manually supplied key."""

    sign_in_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    done = pyqtSignal(str)

    def __init__(self, C, parent=None):
        super().__init__(parent)
        self._C = C
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            McpKeySetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 1px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI):
            w = QLabel(txt)
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  CONNECT TO SERAPH", 12, True))
        subtitle = _lbl("Sign in with your Seraph account to use the trader. We'll create your Seraph API key automatically — nothing to copy or paste.", 8, color=C.PRI_DIM)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        self._sign_in_btn = QPushButton("▸  SIGN IN WITH SERAPH")
        self._sign_in_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._sign_in_btn.setFixedHeight(36)
        self._sign_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sign_in_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 1px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO_BG}; border: 1px solid {C.PRI};
            }}
        """)
        self._sign_in_btn.clicked.connect(self.sign_in_requested.emit)
        layout.addWidget(self._sign_in_btn)

        self._status_lbl = _lbl("", 8, color=C.TEXT_DIM)
        self._status_lbl.setWordWrap(True)
        layout.addWidget(self._status_lbl)
        self._status_lbl.hide()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFont(QFont("Segoe UI", 8))
        self._cancel_btn.setFixedHeight(20)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; }}
            QPushButton:hover {{ color: {C.TEXT}; }}
        """)
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(self._cancel_btn)
        self._cancel_btn.hide()

        self._storage_warn_lbl = _lbl("⚠ Secure storage unavailable on this device — your key will be stored unencrypted.", 8, color=C.RED)
        self._storage_warn_lbl.setWordWrap(True)
        layout.addWidget(self._storage_warn_lbl)
        self._storage_warn_lbl.hide()

        self._manual_link = QPushButton("Use an API key instead")
        self._manual_link.setFont(QFont("Segoe UI", 8))
        self._manual_link.setFixedHeight(22)
        self._manual_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manual_link.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: none; text-align: left; padding: 2px 0;
            }}
            QPushButton:hover {{ color: {C.PRI}; text-decoration: underline; }}
        """)
        self._manual_link.clicked.connect(
            lambda: self._manual_widget.setVisible(self._manual_widget.isHidden())
        )
        layout.addWidget(self._manual_link)
        self._manual_widget = QWidget()
        manual_layout = QVBoxLayout(self._manual_widget)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._manual_widget)
        self._manual_widget.hide()

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Seraph API key…")
        self._key_input.setFont(QFont("Segoe UI", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 1px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        manual_layout.addWidget(self._key_input)

        get_key_btn = QPushButton("Get a Seraph API key ↗")
        get_key_btn.setFont(QFont("Segoe UI", 8))
        get_key_btn.setFixedHeight(22)
        get_key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: none; text-align: left; padding: 2px 0;
            }}
            QPushButton:hover {{ color: {C.PRI}; text-decoration: underline; }}
        """)
        get_key_btn.clicked.connect(lambda: webbrowser.open(mcp_client.SERAPH_KEY_SIGNUP_URL))

        submit_btn = QPushButton("▸  SAVE KEY")
        submit_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        submit_btn.setFixedHeight(36)
        submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        submit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 1px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO_BG}; border: 1px solid {C.PRI};
            }}
        """)
        submit_btn.clicked.connect(self._submit)
        manual_layout.addWidget(submit_btn)
        manual_layout.addWidget(get_key_btn)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {self._C.RED}; }}"
            )
            return
        self.done.emit(key)

    def set_status(self, text: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setVisible(bool(text))

    def set_waiting(self, waiting: bool) -> None:
        self._sign_in_btn.setEnabled(not waiting)
        self._cancel_btn.setVisible(waiting)

    def set_storage_warning(self, insecure: bool) -> None:
        self._storage_warn_lbl.setVisible(insecure)


class TraderPanel(QWidget):
    _event_sig = pyqtSignal(object)
    # Carries a zero-arg callable — lets any background thread schedule a
    # callback to run safely on the Qt GUI thread. Required for anything
    # that might hit live-mode network calls (RPC, Seraph gate, on-chain
    # confirmation wait): a real bug found via live testing was a manual
    # live buy blocking the entire UI for ~1 minute because it ran
    # synchronously from a button-click handler.
    _run_on_gui_sig = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        from ui import C, qcol  # deferred — see module docstring
        self._C = C
        self._qcol = qcol
        self._wallet_cache: dict = {}
        self._wallet_cache_at: float = 0.0
        self._wallet_last_logged_error: str | None = None

        self.engine = TraderEngine(
            emit=self._on_engine_event,
            mcp_tools=mcp_client.mcp_tools,
            # engine.py calls self.mcp_call(name, args) — a 2-arg contract
            # (it always targets the one built-in Seraph server) — while
            # mcp_client.mcp_call(server_id, name, args) is a 3-arg,
            # multi-server-capable function. Bind server_id here so the
            # arities actually match.
            mcp_call=lambda name, args: mcp_client.mcp_call(mcp_client.SERAPH_SERVER_ID, name, args),
            wallet_status=self._wallet_status_provider,
        )
        self._event_sig.connect(self._handle_event)
        self._run_on_gui_sig.connect(lambda fn: fn())
        self._config_inputs: dict[str, QLineEdit] = {}
        self._chain_checks: dict[str, QCheckBox] = {}
        self._login_in_flight = False
        self._needs_login_overlay_shown = False
        self._mcp_key_overlay: "McpKeySetupOverlay | None" = None

        self._build_ui()
        self._refresh_stats()
        self._refresh_positions()
        self._refresh_watchlist()
        self._load_config_into_ui()

        # GEMZ4US, Item D (2026-09-21): BALANCE only ever updated at ARM/
        # sync/adopt/etc, or passively at the end of a scan cycle — a real
        # on-chain deposit was invisible for 4.5+ minutes with the trader
        # stopped, since nothing periodic ever re-reads it in that state.
        # refresh_live_equity() is a no-op in PAPER mode and a single fast
        # RPC call in LIVE (not the trending/price APIs' own rate-limited
        # backoff), so a modest interval here is safe regardless of
        # whether the trader is running — the phone dashboard already
        # polls this same call every few seconds with no issue.
        self._balance_refresh_tmr = QTimer(self)
        self._balance_refresh_tmr.timeout.connect(self._periodic_balance_refresh)
        self._balance_refresh_tmr.start(30_000)

        if not mcp_client.has_credentials():
            self._set_panel_enabled(False)
            self._show_mcp_key_setup()

    # ---------- Seraph MCP key first-use setup ----------

    def _show_mcp_key_setup(self):
        if self._mcp_key_overlay is not None:
            self._mcp_key_overlay.show()
            self._mcp_key_overlay.raise_()
            return
        ov = McpKeySetupOverlay(self._C, self)
        try:
            ov.set_storage_warning(not mcp_client.credential_status().get("secure_storage", True))
        except Exception:
            logging.getLogger(__name__).warning("Unable to read Seraph secure storage status")
        ov.sign_in_requested.connect(self._start_seraph_login)
        ov.cancel_requested.connect(self._cancel_seraph_login)
        ov.done.connect(self._on_mcp_key_submitted)
        self._position_mcp_key_overlay(ov)
        ov.show()
        ov.raise_()
        self._mcp_key_overlay = ov

    def _position_mcp_key_overlay(self, ov: "McpKeySetupOverlay"):
        ow, oh = 440, 380
        ov.setGeometry(
            (self.width()  - ow) // 2,
            (self.height() - oh) // 2,
            ow, oh,
        )

    def _on_mcp_key_submitted(self, key: str):
        mcp_client.save_seraph_api_key(key)
        if self._mcp_key_overlay:
            self._mcp_key_overlay.hide()
            self._mcp_key_overlay = None
        if self._key_warn_lbl:
            self._key_warn_lbl.hide()
        self._set_panel_enabled(True)
        self._refresh_seraph_status()
        self._refresh_seraph_account_row()

    def _start_seraph_login(self) -> None:
        if self._login_in_flight:
            return
        self._login_in_flight = True
        if self._mcp_key_overlay is not None:
            self._mcp_key_overlay.set_waiting(True)
            self._mcp_key_overlay.set_status("Starting…")

        def on_status(text):
            self._run_on_gui_sig.emit(lambda t=text: self._mcp_key_overlay and self._mcp_key_overlay.set_status(t))

        self._background(lambda: get_default_auth().login(on_status=on_status), self._on_seraph_login_done)

    def _cancel_seraph_login(self) -> None:
        try:
            get_default_auth().cancel_login()
        except Exception:
            logging.getLogger(__name__).warning("Unable to cancel Seraph login")
        if self._mcp_key_overlay is not None:
            self._mcp_key_overlay.set_status("Cancelling…")

    def _on_seraph_login_done(self, result) -> None:
        self._login_in_flight = False
        if getattr(result, "ok", False):
            if self._mcp_key_overlay is not None:
                self._mcp_key_overlay.hide()
                self._mcp_key_overlay = None
            self._set_panel_enabled(True)
            if self._key_warn_lbl:
                self._key_warn_lbl.hide()
            self._append_feed_text("OK: connected to Seraph.")
            self._refresh_seraph_status()
            self._refresh_seraph_account_row()
            self._refresh_wallet_block()
        else:
            message = TraderPanel._seraph_login_error_text(
                getattr(result, "error", None), getattr(result, "error_description", None)
            )
            if self._mcp_key_overlay is not None:
                self._mcp_key_overlay.set_waiting(False)
                self._mcp_key_overlay.set_status(message)
            self._append_feed_text("SYS: " + message)

    def _set_panel_enabled(self, enabled: bool) -> None:
        for widget in self._gated_widgets:
            widget.setEnabled(enabled)

    def _refresh_seraph_status(self) -> None:
        try:
            status = mcp_client.credential_status()
        except Exception:
            return
        self._seraph_status_lbl.setText(TraderPanel._seraph_status_text(status))
        if status.get("needs_login") and not self._needs_login_overlay_shown:
            self._needs_login_overlay_shown = True
            self._set_panel_enabled(False)
            self._show_mcp_key_setup()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._mcp_key_overlay is not None and self._mcp_key_overlay.isVisible():
            self._position_mcp_key_overlay(self._mcp_key_overlay)

    # ---------- engine event bridge (background thread -> Qt thread) ----------

    def _on_engine_event(self, event: dict):
        self._event_sig.emit(event)

    def _handle_event(self, event: dict):
        etype = event.get("type")
        if etype == "state":
            # Silent sync event, fired after every state-changing action —
            # applies to stats/positions only, never rendered as a feed line
            # (the buy/sell/gate event that triggered it already logged).
            self._refresh_stats(event)
            self._refresh_positions()
        elif etype in ("sellPrompt", "takeProfitPrompt"):
            pass  # handled inline from command() return value, not the event stream
        elif etype == "watchlist":
            # Silent sync event, fired whenever the watchlist changes from
            # any source — including the phone dashboard, which calls
            # add_watch/remove_watch directly and never touches this UI.
            self._refresh_watchlist()
        elif etype in ("buy", "sell") and event.get("txHash"):
            self._append_feed_line_with_tx(self._format_event(event), event["txHash"], event.get("chain"))
            self._refresh_stats()
            self._refresh_positions()
        elif etype == "gate":
            self._append_gate_feed_line(event)
        elif etype in ("watchlist-empty", "trending", "volume-spike"):
            # These carry a real address+chain per candidate — worth adding
            # to the watchlist, so each gets a clickable +WATCH button
            # instead of just a symbol name the user has no address for
            # (matches the JS app's feed, which did the same).
            header = {
                "watchlist-empty": "Watchlist empty — top trending right now (click +WATCH to add):",
                "trending": "TRENDING (click +WATCH to add):",
                "volume-spike": "VOLUME SPIKE (click +WATCH to add):",
            }[etype]
            self._append_candidate_list(header, event.get("candidates", []))
        else:
            text = self._format_event(event)
            self._append_feed_text(text)
            if etype == "log":
                self._offer_force_sell_if_hinted(event.get("text", ""))
            if etype in ("buy", "sell", "halt"):
                self._refresh_stats()
                self._refresh_positions()

    # ---------- background execution (never block the Qt GUI thread) ----------

    def _background(self, work_fn, then_fn, pending_text: str | None = None):
        """Runs work_fn() on a worker thread; then_fn(result) runs back on
        the Qt GUI thread once it's done. Anything that might reach a
        live-mode network call (RPC, Seraph gate, on-chain confirmation
        wait) MUST go through this — calling such a thing directly from a
        button-click handler blocks the entire UI for as long as it takes
        (found live: a manual live buy froze the app for ~1 minute)."""
        if pending_text:
            self._append_feed_text(pending_text)

        def _worker():
            try:
                result = work_fn()
            except Exception as err:
                result = {"ok": False, "message": str(err)}
            self._run_on_gui_sig.emit(lambda: then_fn(result))

        threading.Thread(target=_worker, daemon=True).start()

    def _periodic_balance_refresh(self):
        # See the QTimer set up in __init__ (Item D). Off the GUI thread
        # like every other engine call that can touch the network.
        self._background(self.engine.refresh_live_equity, lambda _result: self._refresh_stats())

    @staticmethod
    def _feed_timestamp() -> str:
        # GEMZ4US, Finding #27 (2026-09-17), confirmed still partial on
        # 2026-09-18: an earlier fix added a timestamp inside
        # _format_event, but that only covers lines built from an engine
        # event — echoed commands ("> buy ..."), local status text
        # ("SYS: checking with Seraph…", "SYS: config saved"), and command
        # results ("OK: bought...") are appended directly as plain text
        # from UI code and never touched _format_event, so they stayed
        # unstamped. Moved to a single stamp-at-append-time helper used by
        # every feed-line entry point below, so it's genuinely "every
        # line" this time — using wall-clock time at append rather than
        # each event's own "at" field, since that's the one thing every
        # entry point actually has in common.
        return datetime.now().astimezone().strftime("[%H:%M:%S] ")

    @staticmethod
    def _format_event(event: dict) -> str:
        etype = event.get("type")
        if etype == "log":
            return f"SYS: {event.get('text', '')}"
        if etype == "gate":
            verdict = "APPROVED" if event.get("approved") else "BLOCKED"
            return f"GATE [{verdict}] {event.get('symbol')} — {event.get('reason', '')}"
        if etype == "buy":
            live = " (LIVE)" if event.get("live") else ""
            tx = event.get("txHash")
            tx_part = f" — tx {_full_tx_hash(tx)[:10]}…" if tx else ""
            # cost includes swap fee/slippage/gas on top of qty*price (see
            # engine._execute_buy) — shown explicitly so "why doesn't cost
            # match qty*price" isn't left for the reader to reverse-engineer
            # from a balance delta (found live: a tester spent real effort
            # inferring this from Balance before/after instead).
            cost = event.get("costUsd")
            cost_part = f" cost=${cost:.2f}" if cost is not None else ""
            return f"BUY{live} {event.get('symbol')} qty={event.get('qty', 0):.4f} @ ${event.get('priceUsd', 0):.6f}{cost_part}{tx_part}"
        if etype == "sell":
            live = " (LIVE)" if event.get("live") else ""
            pnl = event.get("pnlUsd", 0)
            sign = "+" if pnl >= 0 else ""
            tx = event.get("txHash")
            tx_part = f" — tx {_full_tx_hash(tx)[:10]}…" if tx else ""
            proceeds = event.get("proceedsUsd")
            proceeds_part = f" proceeds=${proceeds:.2f}" if proceeds is not None else ""
            return f"SELL{live} {event.get('symbol')} pnl={sign}${pnl:.2f}{proceeds_part} ({event.get('reason', '')}){tx_part}"
        if etype == "scan":
            n = len(event.get("candidates", []))
            return f"SCAN: {n} candidate(s) passed signal threshold"
        if etype == "volume-spike-alert":
            syms = ", ".join(c["symbol"] for c in event.get("candidates", []))
            return f"VOLUME SPIKE (alert only, chain not enabled): {syms}"
        if etype == "halt":
            return f"■ HALTED: {event.get('reason', '')}"
        return str(event)

    # ---------- UI ----------

    def _build_ui(self):
        self._gated_widgets: list[QWidget] = []
        C = self._C
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("◆ OMNI TRADER")
        title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        header.addWidget(title)
        header.addStretch()

        self._mode_lbl = QLabel("PAPER")
        self._mode_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._mode_lbl.setStyleSheet(f"color: {C.ACC2}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER}; border-radius: 1px; padding: 3px 8px;")
        header.addWidget(self._mode_lbl)

        self._key_warn_lbl = None
        if not mcp_client.has_credentials():
            self._key_warn_lbl = QLabel("⚠ no Seraph API key found — trades will fail closed")
            self._key_warn_lbl.setFont(QFont("Segoe UI", 8))
            self._key_warn_lbl.setStyleSheet(f"color: {C.RED}; background: transparent;")
            header.addWidget(self._key_warn_lbl)

        self._seraph_status_lbl = QLabel("")
        self._seraph_status_lbl.setFont(QFont("Segoe UI", 8))
        self._seraph_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        header.addWidget(self._seraph_status_lbl)

        # Toggled via MainWindow, which owns the actual config panel widget
        # (a floating overlay over the orb/trader view — see
        # left_panel_widget() / MainWindow._toggle_trader_config()). Not
        # shown by default: it used to float permanently and covered this
        # panel's own wallet/live-mode controls underneath it.
        self.on_config_toggle = None
        config_btn = self._make_button("⚙ CONFIG", lambda: self.on_config_toggle and self.on_config_toggle())
        header.addWidget(config_btn)
        self._start_btn = self._make_button("▶ START", self._on_start_stop)
        header.addWidget(self._start_btn)
        scan_btn = self._make_button("⟳ SCAN NOW", lambda: self._run_command("/scan"))
        header.addWidget(scan_btn)
        self._gated_widgets.extend([config_btn, self._start_btn, scan_btn])
        root.addLayout(header)

        # Stats row
        stats = QHBoxLayout()
        self._stat_labels: dict[str, QLabel] = {}
        for key, label in [("balance", "BALANCE"), ("equity", "EQUITY"), ("pnl", "REALIZED P&L"),
                            ("trades", "TRADES TODAY"), ("positions", "OPEN POSITIONS")]:
            box = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setFont(QFont("Segoe UI", 7))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            val = QLabel("—")
            val.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            val.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            box.addWidget(lbl)
            box.addWidget(val)
            stats.addLayout(box)
            self._stat_labels[key] = val
        stats.addStretch()
        root.addLayout(stats)

        root.addWidget(self._build_wallet_row())

        # Middle: positions + feed side by side
        mid = QHBoxLayout()
        mid.setSpacing(8)

        pos_col = QVBoxLayout()
        pos_hdr = QLabel("▸ OPEN POSITIONS")
        pos_hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        pos_hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        pos_col.addWidget(pos_hdr)
        self._positions_scroll = QScrollArea()
        self._positions_scroll.setWidgetResizable(True)
        self._positions_scroll.setFixedHeight(120)
        self._positions_scroll.setStyleSheet(self._panel_style())
        positions_inner = QWidget()
        positions_inner.setStyleSheet("background: transparent;")
        self._positions_layout = QVBoxLayout(positions_inner)
        self._positions_layout.setContentsMargins(6, 6, 6, 6)
        self._positions_layout.setSpacing(2)
        self._positions_layout.addStretch()
        self._positions_scroll.setWidget(positions_inner)
        pos_col.addWidget(self._positions_scroll)
        mid.addLayout(pos_col, stretch=1)

        watch_col = QVBoxLayout()
        watch_hdr = QLabel("▸ WATCHLIST")
        watch_hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        watch_hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        watch_col.addWidget(watch_hdr)
        self._watchlist_scroll = QScrollArea()
        self._watchlist_scroll.setWidgetResizable(True)
        self._watchlist_scroll.setFixedHeight(120)
        self._watchlist_scroll.setStyleSheet(self._panel_style())
        watchlist_inner = QWidget()
        watchlist_inner.setStyleSheet("background: transparent;")
        self._watchlist_layout = QVBoxLayout(watchlist_inner)
        self._watchlist_layout.setContentsMargins(6, 6, 6, 6)
        self._watchlist_layout.setSpacing(2)
        self._watchlist_layout.addStretch()
        self._watchlist_scroll.setWidget(watchlist_inner)
        watch_col.addWidget(self._watchlist_scroll)
        mid.addLayout(watch_col, stretch=1)

        root.addLayout(mid)

        feed_hdr = QLabel("▸ EVENT FEED")
        feed_hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        feed_hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        root.addWidget(feed_hdr)

        self._feed_scroll = QScrollArea()
        self._feed_scroll.setWidgetResizable(True)
        self._feed_scroll.setStyleSheet(self._panel_style())
        feed_inner = QWidget()
        feed_inner.setStyleSheet("background: transparent;")
        self._feed_layout = QVBoxLayout(feed_inner)
        self._feed_layout.setContentsMargins(6, 6, 6, 6)
        self._feed_layout.setSpacing(3)
        self._feed_layout.addStretch()
        self._feed_scroll.setWidget(feed_inner)
        # GEMZ4US, Finding #27 (2026-09-17): the previous approach (a
        # QTimer.singleShot(0, ...) fired once per append, still kept
        # below as a fallback) intermittently missed — confirmed
        # independent of any screenshot artifact, e.g. 4 lines added by one
        # click not scrolling into view until a manual scroll. Reacting to
        # the scrollbar's own rangeChanged is the reliable Qt idiom: it
        # fires exactly when Qt recomputes the scrollable area after new
        # content is laid out, rather than hoping a fixed-delay timer lands
        # at the right moment relative to that layout pass.
        self._feed_scroll.verticalScrollBar().rangeChanged.connect(
            lambda _lo, hi: self._feed_scroll.verticalScrollBar().setValue(hi)
        )
        root.addWidget(self._feed_scroll, stretch=1)

        # Command bar
        cmd_row = QHBoxLayout()
        self._cmd_input = QLineEdit()
        self._gated_widgets.append(self._cmd_input)
        self._cmd_input.setPlaceholderText("buy SYM:0x... · sell SYM · watch SYM:0x... · help")
        self._cmd_input.setFont(QFont("Segoe UI", 9))
        self._cmd_input.setStyleSheet(
            f"background: {C.PANEL_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 1px; padding: 6px;"
        )
        self._cmd_input.returnPressed.connect(self._on_command_submit)
        cmd_row.addWidget(self._cmd_input, stretch=1)
        cmd_row.addWidget(self._make_button("SEND", self._on_command_submit))
        root.addLayout(cmd_row)

        # Config lives in the main window's left sidebar (that otherwise-
        # empty stretch region below the sys-monitor bars), not down here —
        # see left_panel_widget() / MainWindow.set_left_panel_extra().
        self._left_config_widget = self._build_left_config_panel()
        self._seraph_timer = QTimer(self)
        self._seraph_timer.timeout.connect(self._refresh_seraph_status)
        self._seraph_timer.start(30000)
        self._refresh_seraph_status()

    def left_panel_widget(self) -> QWidget:
        """Mounted into the main window's left sidebar while this panel is
        the active center view — see MainWindow.set_left_panel_extra()."""
        return self._left_config_widget

    def _build_left_config_panel(self) -> QWidget:
        """Compact, single-column config panel sized for the ~148px-wide
        left sidebar — a from-scratch layout, not a narrowed copy of a wide
        grid, since a 3-column field grid simply doesn't fit that width."""
        C = self._C
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        hdr = QLabel("▸ TRADER CONFIG")
        hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        col.addWidget(hdr)

        for key, label in _CONFIG_FIELDS:
            lbl = QLabel(label)
            # GEMZ4US, 2026-09-18 (Part 4/C): field labels here (and the
            # equivalent Settings submenu labels, same fix applied there)
            # were TEXT_DIM at 7pt — "difficult to read at a glance even
            # at close viewing distance." Bumped to TEXT_MED (the
            # already-established, more-legible mid-gray used elsewhere
            # for this exact purpose) at 8pt.
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            col.addWidget(lbl)
            inp = QLineEdit()
            inp.setFont(QFont("Segoe UI", 8))
            inp.setStyleSheet(f"background: {C.PANEL_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 1px; padding: 3px 4px;")
            self._config_inputs[key] = inp
            col.addWidget(inp)

        chains_lbl = QLabel("Chains (paper scan)")
        chains_lbl.setFont(QFont("Segoe UI", 8))
        chains_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; margin-top: 4px;")
        col.addWidget(chains_lbl)
        for key, info in chains_mod.CHAINS.items():
            cb = QCheckBox(info["name"])
            # GEMZ4US, Item E (2026-09-20): still 7pt after the field-label
            # contrast fix above (Part 4/C) was applied — same colour as
            # the input values already, just smaller. Matched to 8pt.
            cb.setFont(QFont("Segoe UI", 8))
            cb.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            self._chain_checks[key] = cb
            col.addWidget(cb)

        save_btn = self._make_button("SAVE CONFIG", self._on_save_config)
        col.addWidget(save_btn)
        reset_btn = self._make_button("RESET LEDGER", self._on_reset)
        col.addWidget(reset_btn)

        # A bad/invalid key entered on first setup (McpKeySetupOverlay) had
        # no way to be corrected afterward — that overlay only ever shows
        # once, before any key is saved, and neither this config panel nor
        # Settings had a key field at all. save_seraph_api_key() already
        # persists + live-applies with no restart needed (mcp_client.py);
        # this was purely a missing UI affordance to reach it again.
        self._seraph_identity_lbl = QLabel("Seraph account: —")
        self._seraph_identity_lbl.setFont(QFont("Segoe UI", 7))
        self._seraph_identity_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        col.addWidget(self._seraph_identity_lbl)
        self._seraph_account_btn = self._make_button("…", self._on_seraph_account_button)
        col.addWidget(self._seraph_account_btn)

        key_lbl = QLabel("Seraph API key")
        key_lbl.setFont(QFont("Segoe UI", 7))
        key_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; margin-top: 4px;")
        col.addWidget(key_lbl)
        self._seraph_key_input = QLineEdit()
        self._seraph_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._seraph_key_input.setPlaceholderText("Enter to replace saved key…")
        self._seraph_key_input.setFont(QFont("Segoe UI", 8))
        self._seraph_key_input.setStyleSheet(f"background: {C.PANEL_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 1px; padding: 3px 4px;")
        col.addWidget(self._seraph_key_input)
        key_save_btn = self._make_button("SAVE KEY", self._on_save_seraph_key)
        col.addWidget(key_save_btn)

        self._refresh_seraph_account_row()
        return wrap

    def _refresh_seraph_account_row(self) -> None:
        # The left config panel is built lazily (left_panel_widget), so these
        # widgets may not exist yet when a login/disconnect finishes. Refreshing
        # is best-effort: _build_left_config_panel calls this itself on create.
        if getattr(self, "_seraph_identity_lbl", None) is None:
            return
        try:
            status = mcp_client.credential_status()
        except Exception:
            return
        try:
            identity = "Seraph account: " + format_identity(status)
        except Exception:
            identity = "Seraph account: —"
        self._seraph_identity_lbl.setText(identity)
        self._seraph_account_btn.setText(
            "DISCONNECT THIS DEVICE" if mcp_client.has_credentials() else "SIGN IN"
        )

    def _on_seraph_account_button(self) -> None:
        if not mcp_client.has_credentials():
            self._show_mcp_key_setup()
            return
        if not self._confirm_warning(
            "Disconnect this device?",
            "This removes the Seraph key from this device and revokes it on the server.",
        ):
            return
        self._background(self._disconnect_device_work, self._on_disconnect_device_done,
                         pending_text="SYS: disconnecting this device…")

    def _disconnect_device_work(self) -> dict:
        try:
            get_default_auth().disconnect_device()
        except Exception:
            logging.getLogger(__name__).warning("Unable to complete Seraph device disconnect")
        try:
            mcp_client.get_default_client().invalidate()
        except Exception:
            logging.getLogger(__name__).warning("Unable to invalidate Seraph client")
        return {"ok": True}

    def _on_disconnect_device_done(self, _result) -> None:
        self._append_feed_text("OK: device disconnected from Seraph.")
        self._refresh_seraph_status()
        self._refresh_seraph_account_row()
        self._set_panel_enabled(False)
        self._needs_login_overlay_shown = False
        self._show_mcp_key_setup()

    def _on_save_seraph_key(self):
        key = self._seraph_key_input.text().strip()
        if not key:
            return
        mcp_client.save_seraph_api_key(key)
        self._seraph_key_input.clear()
        if self._key_warn_lbl:
            self._key_warn_lbl.hide()
        self._append_feed_text("SYS: Seraph API key updated — takes effect immediately, no restart needed.")

    def _build_wallet_row(self) -> QWidget:
        """Seraph wallet block. Read-only by construction: this device never
        holds key material, so there is nothing here to create, import,
        export, lock or remove. Trades are signed server-side by the Seraph
        wallet, gated by the signer grant the user makes in the console."""
        C = self._C
        wrap = QWidget()
        wrap.setStyleSheet(f"background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 1px;")
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        wallet_hdr = QLabel("▸ SERAPH WALLET (custodial — server-side signing, no keys on this device)")
        wallet_hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        wallet_hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        outer.addWidget(wallet_hdr)
        text = TraderPanel._wallet_block_text(self._wallet_cache)
        self._wallet_embedded_lbl = QLabel(text["embedded_line"])
        self._wallet_signer_lbl = QLabel(text["signer_line"])
        self._wallet_external_lbl = QLabel(text["external_line"] or "")
        self._wallet_copy_lbl = QLabel(text["copy"])
        for label in (self._wallet_embedded_lbl, self._wallet_signer_lbl,
                      self._wallet_external_lbl, self._wallet_copy_lbl):
            label.setFont(QFont("Segoe UI", 8))
            label.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            outer.addWidget(label)
        self._wallet_external_lbl.setVisible(text["external_line"] is not None)
        self._wallet_copy_lbl.setWordWrap(True)

        row2 = QHBoxLayout()
        row2.addWidget(self._make_button("COPY ADDRESS", self._on_copy_wallet_address))
        row2.addWidget(self._make_button("DEPOSIT", self._on_show_deposit))
        row2.addWidget(self._make_button("WITHDRAW ↗", lambda: webbrowser.open(SERAPH_WALLET_CONSOLE_URL)))
        row2.addWidget(self._make_button("MANAGE IN CONSOLE ↗", lambda: webbrowser.open(SERAPH_WALLET_CONSOLE_URL)))
        row2.addStretch()
        outer.addLayout(row2)

        row3 = QHBoxLayout()
        live_hdr = QLabel("▸ LIVE MODE")
        live_hdr.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        live_hdr.setStyleSheet(f"color: {C.RED}; background: transparent;")
        row3.addWidget(live_hdr)
        self._live_confirm_input = QLineEdit()
        self._live_confirm_input.setPlaceholderText('type "LIVE" to arm')
        self._live_confirm_input.setFont(QFont("Segoe UI", 8))
        self._live_confirm_input.setFixedWidth(140)
        self._live_confirm_input.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 1px; padding: 3px 5px;")
        row3.addWidget(self._live_confirm_input)
        self._arm_btn = self._make_button("⚠ ARM LIVE", self._on_arm_live)
        row3.addWidget(self._arm_btn)
        self._disarm_btn = self._make_button("DISARM", self._on_disarm_live)
        row3.addWidget(self._disarm_btn)
        row3.addStretch()
        outer.addLayout(row3)

        self._refresh_wallet_block()
        return wrap

    def _refresh_wallet_block(self) -> None:
        """Refresh the wallet block from guardian_wallet_status, off the GUI
        thread. Never called synchronously from a paint or click path."""
        def work_fn():
            # GEMZ4US, 2026-09-24 (v1.12.3 retest): v1.12.3's has_credentials()
            # check still never fired, because it ran in then_fn — AFTER the
            # call already failed. On a 401 that fails to recover,
            # _call_with_auth_retry calls auth.invalidate_session(), which
            # wipes the persisted api_key field (_clear_session in
            # seraph_auth.py) before returning. By the time then_fn checked
            # has_credentials(), the credential the call itself had just
            # deleted was already gone — so the check always saw "no
            # credential" regardless of whether one existed going in. Capture
            # it BEFORE the call, on this same worker thread, so then_fn can
            # tell "never had one" apart from "had one, this call wiped it."
            had_credential = mcp_client.has_credentials()
            try:
                result = mcp_client.mcp_call(mcp_client.SERAPH_SERVER_ID, "guardian_wallet_status", {})
            except Exception as err:
                # Defensive: _background's own except-branch would otherwise
                # substitute a differently-shaped {"ok": False, "message": ...}
                # dict here, which then_fn's tuple-unpack below would silently
                # misinterpret (2 dict keys unpack as if they were the tuple's
                # two elements) instead of erroring loudly. Keep the shape
                # then_fn actually expects even on an unexpected exception.
                result = {"ok": False, "error": f"unexpected: {err}"}
            return result, had_credential

        def then_fn(work_result):
            result, has_local_credential = work_result
            # GEMZ4US, 2026-09-24 (v1.12.4 retest): four straight fixes to
            # this method's error branch changed nothing, which — since
            # every one of those fixes is gated on `error` being truthy —
            # is only possible if `error` was never truthy to begin with.
            # Stop guessing at the failure shape and log the raw response
            # unconditionally, so the next retest shows ground truth instead
            # of another blind hypothesis: either guardian_wallet_status is
            # genuinely succeeding with an empty/no-wallet payload (a Seraph-
            # side data question, not fixable here), or it's failing with an
            # error string none of v1.12.2-v1.12.4 anticipated.
            if result.get("ok"):
                self._append_feed_text(
                    f"SYS: [diag] guardian_wallet_status ok=True text={result.get('text', '')[:300]!r}"
                )
            else:
                self._append_feed_text(
                    f"SYS: [diag] guardian_wallet_status ok=False error={result.get('error')!r}"
                )
            error = None if result.get("ok") else (result.get("error") or "unknown error")
            rejected_despite_credential = (
                error in ("seraph_login_required", "seraph_unauthorized") and has_local_credential
            )
            if error and (error not in ("seraph_login_required", "seraph_unauthorized")
                          or rejected_despite_credential):
                self._wallet_cache = {}
                self._wallet_cache_at = time.time()
                if rejected_despite_credential:
                    # The call itself was rejected as unauthorized and gave up
                    # recovering (see the comment above) — this can invalidate
                    # the local session as a side effect, so "sign in again"
                    # is the actually-correct next step here, unlike the
                    # generic network/backend-error case below.
                    copy = (
                        "Seraph rejected the wallet status check even though you have a device key "
                        f"({error}) — your session may have just been invalidated. Try signing in again."
                    )
                else:
                    copy = (
                        f"Could not reach Seraph to check your wallet status ({error}). "
                        "This is separate from being signed in — your sign-in may still be valid."
                    )
                self._wallet_embedded_lbl.setText(f"Seraph Wallet: could not check status — {error}")
                self._wallet_signer_lbl.setText("Signer: unknown — status check failed")
                self._wallet_signer_lbl.setStyleSheet(f"color: {self._C.TEXT_DIM}; background: transparent;")
                self._wallet_external_lbl.setText("")
                self._wallet_external_lbl.setVisible(False)
                self._wallet_copy_lbl.setText(copy)
                if error != self._wallet_last_logged_error:
                    self._wallet_last_logged_error = error
                    self._append_feed_text(f"SYS: Seraph Wallet status check failed — {error}")
                return
            self._wallet_last_logged_error = None
            payload = {}
            if result.get("ok"):
                try:
                    payload = json.loads(result["text"])
                except (ValueError, TypeError, KeyError):
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}
            self._wallet_cache = payload
            self._wallet_cache_at = time.time()
            text = TraderPanel._wallet_block_text(payload)
            self._wallet_embedded_lbl.setText(text["embedded_line"])
            self._wallet_signer_lbl.setText(text["signer_line"])
            C = self._C
            color = C.GREEN if payload.get("signerGranted") else C.TEXT_DIM
            self._wallet_signer_lbl.setStyleSheet(f"color: {color}; background: transparent;")
            self._wallet_external_lbl.setText(text["external_line"] or "")
            self._wallet_external_lbl.setVisible(text["external_line"] is not None)
            self._wallet_copy_lbl.setText(text["copy"])

        self._background(work_fn, then_fn, pending_text=None)

    def _wallet_status_provider(self) -> dict:
        """What the engine sees. NON-BLOCKING by contract: it answers from the
        30s cache and schedules a refresh when stale, never waiting on the
        network. The engine calls this from paths that must not stall, and an
        empty cache answers "not connected", which fails closed."""
        cache = self._wallet_cache
        if not cache or time.time() - self._wallet_cache_at >= 30:
            self._refresh_wallet_block()
        return {
            "connected": bool(cache.get("signerGranted")) and bool(cache.get("address")),
            "address": cache.get("address") or None,
            "signerGranted": bool(cache.get("signerGranted")),
        }

    def _on_copy_wallet_address(self) -> None:
        address = self._wallet_cache.get("address")
        if not address:
            self._append_feed_text("SYS: no Seraph Wallet address — sign in")
            return
        QApplication.clipboard().setText(address)

    def _on_show_deposit(self) -> None:
        address = self._wallet_cache.get("address")
        if not address:
            self._append_feed_text("SYS: no Seraph Wallet address — sign in")
            return
        names = {str(info["chainId"]): info["name"] for info in chains_mod.CHAINS.values()}
        networks = ", ".join(names.get(str(chain), str(chain))
                             for chain in (self._wallet_cache.get("chains") or []))
        self._append_feed_text(
            f"SYS: deposit to the Seraph Wallet: {address} — supported networks: {networks or 'not reported'}"
        )

    def _confirm_warning(self, title: str, message: str) -> bool:
        """Confirm an action using the styled warning dialog, defaulting to No."""
        C = self._C
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setStyleSheet(
            f"QMessageBox {{ background: {C.PANEL_BG}; }} "
            f"QLabel {{ color: {C.TEXT}; background: transparent; }} "
            f"QPushButton {{ color: {C.TEXT}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 4px; padding: 4px 14px; }}"
        )
        return box.exec() == QMessageBox.StandardButton.Yes

    def _on_arm_live(self):
        if not TraderPanel._wallet_block_text(self._wallet_cache)["live_allowed"]:
            self._append_feed_text("SYS: authorize the Seraph Wallet in the console before trading live")
            return
        if self._live_confirm_input.text().strip() != "LIVE":
            self._append_feed_text('SYS: type "LIVE" in the field first to confirm arming real-money trading.')
            return
        self._live_confirm_input.clear()
        self._background(self.engine.arm_live, self._handle_arm_live_result,
                          pending_text="SYS: reading wallet balance across enabled chains…")

    def _handle_arm_live_result(self, result: dict):
        if not result.get("ok"):
            self._append_feed_text(f"SYS: {result.get('error')}")
        self._refresh_stats()
        self._refresh_positions()

    def _on_disarm_live(self):
        self.engine.disarm_live("user requested")
        self._refresh_stats()
        self._refresh_positions()

    @staticmethod
    def _abbreviate_address(address: str | None) -> str:
        """0x-prefixed address shortened for display: first 6 chars, ellipsis,
        last 4. Anything that is not a plausible address renders as an em dash
        rather than a truncated fragment the user might mistake for real."""
        if not isinstance(address, str) or len(address) < 12:
            return "—"
        return f"{address[:6]}…{address[-4:]}"

    @staticmethod
    def _seraph_status_text(status: dict) -> str:
        """Header line describing the Seraph credential. Never renders the key
        itself — only the 12-character prefix the backend already treats as
        public."""
        mode = status.get("mode")
        if status.get("needs_login"):
            base = "Seraph session expired — sign in again"
        elif mode is None or mode == "none":
            base = "not connected to Seraph — sign in to use the trader"
        else:
            prefix = status.get("api_key_prefix") or "—"
            label = {
                "api_key_oauth": "signed in",
                "api_key_manual": "manual",
                "api_key_legacy": "legacy",
                "api_key_explicit": "configured",
            }.get(mode, "unknown")
            base = f"Seraph: key {prefix} ({label})"
        if status.get("last_error") == "unauthorized":
            base += " — key rejected"
        return base

    @staticmethod
    def _seraph_login_error_text(error: str | None, error_description: str | None = None) -> str:
        """Human-readable message for every AuthResult.error code. The raw code
        is never shown on its own: an unmapped code still gets a sentence."""
        message = {
            "access_denied": "Sign-in was denied. Nothing was changed.",
            "timeout": "Sign-in timed out. Try again.",
            "cancelled": "Sign-in was cancelled.",
            "state_mismatch": "Sign-in could not be verified. Try again.",
            "network_error": "Could not reach Seraph. Check your connection and try again.",
            "metadata_error": "Could not reach Seraph. Check your connection and try again.",
            "invalid_client": "This device needs to register with Seraph again. Try signing in once more.",
            "registration_failed": "This device needs to register with Seraph again. Try signing in once more.",
            "token_exchange_failed": "Seraph refused the sign-in. Try again.",
            "api_key_mint_failed": "Signed in, but the Seraph API key could not be created. Try again.",
            "insufficient_scope": "Your Seraph account is missing permissions for this device. Contact support.",
            "browser_open_failed": "Could not open your browser. Copy the sign-in link and open it manually.",
        }.get(error, "Sign-in failed. Try again.")
        if isinstance(error_description, str) and error_description:
            return f"{message} ({error_description})"
        return message

    @staticmethod
    def _wallet_block_text(wallet: dict) -> dict:
        """Pure rendering of the Seraph wallet block from a guardian_wallet_status
        payload. Kept free of Qt so the copy that tells the user WHICH wallet
        actually trades can be asserted in a test — depositing into the wrong
        one is a silent, unrecoverable user error."""
        address = wallet.get("address")
        external = wallet.get("linkedExternalAddress")
        signer_granted = wallet.get("signerGranted")
        embedded_line = "Seraph Wallet: not available — sign in"
        if isinstance(address, str) and address:
            embedded_line = f"Seraph Wallet: {TraderPanel._abbreviate_address(address)}"
        external_line = None
        if isinstance(external, str) and external:
            external_line = f"External wallet (login): {TraderPanel._abbreviate_address(external)}"
        # A truncated address in a deposit instruction is useless and dangerous: the user could copy the wrong fragment.
        if not address:
            copy = "Sign in to Seraph to see your Seraph Wallet."
        elif external:
            copy = f"Your trades use the Seraph Wallet ({address}), not your external wallet ({external}). Deposit funds into the Seraph Wallet to trade."
        else:
            copy = f"Your trades use the Seraph Wallet ({address}). Deposit funds into it to trade."
        return {
            "embedded_line": embedded_line,
            "signer_line": "Signer: authorized" if signer_granted else "Signer: not authorized — authorize in the console",
            "external_line": external_line,
            "copy": copy,
            "live_allowed": bool(signer_granted) and bool(address),
        }

    def _panel_style(self) -> str:
        C = self._C
        return f"""
            QTextBrowser, QScrollArea {{
                background: {C.PANEL_BG}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 1px;
            }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 1px; min-height: 20px; }}
        """

    def _make_button(self, text: str, cb) -> QPushButton:
        C = self._C
        btn = QPushButton(text)
        btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.ACC2}; background: {C.PANEL2_BG};
                border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 5px 10px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
        """)
        btn.clicked.connect(cb)
        return btn

    # ---------- feed rendering ----------

    def _append_feed_text(self, text: str):
        C = self._C
        lbl = QLabel(self._feed_timestamp() + text)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
        # Finding #40 (GEMZ4US): Event Feed log text couldn't be selected
        # or copied — a plain QLabel isn't mouse-selectable by default.
        # Not TextSelectableByKeyboard too: that would pull every one of
        # these labels into the tab-focus chain, which for a scrolling log
        # of arbitrarily many lines is worse than the bug it'd fix.
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, lbl)
        self._trim_feed()
        self._scroll_feed_to_bottom()

    def _append_feed_widget(self, widget: QWidget):
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, widget)
        self._trim_feed()
        self._scroll_feed_to_bottom()

    def _trim_feed(self):
        while self._feed_layout.count() - 1 > _FEED_MAX_ITEMS:
            item = self._feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _append_feed_line_with_tx(self, text: str, tx_hash: str, chain: str | None = None):
        """A feed line for a real on-chain buy/sell — text plus COPY (tx
        hash to clipboard) and, for chains with a known block explorer,
        VIEW (opens it in the default browser)."""
        C = self._C
        full_hash = _full_tx_hash(tx_hash)
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(self._feed_timestamp() + text)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
        # Finding #40 (GEMZ4US): Event Feed log text couldn't be selected
        # or copied — a plain QLabel isn't mouse-selectable by default.
        # Not TextSelectableByKeyboard too: that would pull every one of
        # these labels into the tab-focus chain, which for a scrolling log
        # of arbitrarily many lines is worse than the bug it'd fix.
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lbl, stretch=1)

        def _tx_button(label: str, cb):
            btn = QPushButton(label)
            btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(64)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                QPushButton:hover {{ border: 1px solid {C.BORDER_B}; }}
            """)
            btn.clicked.connect(cb)
            return btn

        lay.addWidget(_tx_button("COPY TX", lambda: QApplication.clipboard().setText(full_hash)))
        explorer = CHAIN_EXPLORERS.get(chain or chains_mod.DEFAULT_CHAIN)
        if explorer:
            lay.addWidget(_tx_button("VIEW", lambda: webbrowser.open(explorer + full_hash)))
        self._append_feed_widget(row)

    def _append_gate_feed_line(self, event: dict):
        """A Seraph gate verdict (approve/block/unknown) — text plus a VIEW
        button to DexTools-style token charting (DexScreener; resolves a
        bare token address to its most-liquid pair automatically) so the
        user can inspect the token themselves, especially useful for a
        BLOCKED/unknown verdict where Seraph itself couldn't say much."""
        C = self._C
        text = self._format_event(event)
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(self._feed_timestamp() + text)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
        # Finding #40 (GEMZ4US): Event Feed log text couldn't be selected
        # or copied — a plain QLabel isn't mouse-selectable by default.
        # Not TextSelectableByKeyboard too: that would pull every one of
        # these labels into the tab-focus chain, which for a scrolling log
        # of arbitrarily many lines is worse than the bug it'd fix.
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(lbl, stretch=1)

        url = chains_mod.dexscreener_url(event.get("chain") or chains_mod.DEFAULT_CHAIN, event.get("address") or "")
        if url:
            btn = QPushButton("VIEW")
            btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(64)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                QPushButton:hover {{ border: 1px solid {C.BORDER_B}; }}
            """)
            btn.clicked.connect(lambda: webbrowser.open(url))
            lay.addWidget(btn)

        symbol, address, chain = event.get("symbol"), event.get("address"), event.get("chain")
        if not event.get("approved") and symbol and address:
            force_btn = QPushButton(f"⚠ FORCE BUY {symbol}")
            force_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            force_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            force_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 3px 8px; }}
                QPushButton:hover {{ border: 1px solid {C.RED}; }}
            """)
            entry = f"{symbol}:{chain or chains_mod.DEFAULT_CHAIN}:{address} force"
            force_btn.clicked.connect(lambda: self._pick_and_run(lambda: self.engine.buy_one(entry)))
            lay.addWidget(force_btn)
        self._append_feed_widget(row)

    def _scroll_feed_to_bottom(self):
        QTimer.singleShot(0, self._do_scroll_feed_to_bottom)

    def _do_scroll_feed_to_bottom(self):
        bar = self._feed_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _add_percent_picker(self, label: str, symbol: str, percents: list[int], on_pick):
        C = self._C
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(lbl)
        for pct in percents:
            btn = QPushButton(f"{pct}%")
            btn.setFont(QFont("Segoe UI", 8))
            btn.setFixedWidth(48)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                QPushButton:hover {{ border: 1px solid {C.BORDER_B}; }}
            """)
            btn.clicked.connect(lambda _checked=False, p=pct: on_pick(symbol, p))
            lay.addWidget(btn)
        lay.addStretch()
        self._append_feed_widget(row)

    def _append_candidate_list(self, header: str, candidates: list[dict]):
        """Renders a discovery event (empty-watchlist prompt, trending,
        volume-spike) as one row per candidate with a real +WATCH button —
        the address is known (it's what the discovery API returned) but
        never shown to the user otherwise, so there'd be no way to actually
        act on a plain symbol name without this."""
        self._append_feed_text(header)
        C = self._C
        for c in candidates:
            symbol = c.get("symbol", "?")
            address = c.get("address")
            chain = c.get("chain") or chains_mod.DEFAULT_CHAIN
            chg = c.get("chg1h")
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(16, 0, 0, 0)
            chg_text = f"  {chg:+.1f}%/1h" if isinstance(chg, (int, float)) else ""
            chain_name = chains_mod.resolve(chain)["name"]
            addr_short = f"{address[:6]}…{address[-4:]}" if address else "?"
            lbl = QLabel(f"{symbol:<8} {chain_name:<12} {addr_short}{chg_text}")
            lbl.setFont(QFont("Segoe UI", 8))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl)
            if address:
                btn = QPushButton("+WATCH")
                btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedWidth(64)
                btn.setStyleSheet(f"""
                    QPushButton {{ color: {C.GREEN}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                    QPushButton:hover {{ border: 1px solid {C.GREEN}; }}
                """)
                entry = f"{symbol}:{chain}:{address}"
                btn.clicked.connect(lambda _checked=False, e=entry: self._on_watch_click(e))
                lay.addWidget(btn)
            lay.addStretch()
            self._append_feed_widget(row)

    def _on_watch_click(self, entry: str):
        result = self.engine.add_watch(entry)
        self._append_feed_text(("OK: " if result.get("ok") else "SYS: ") + result.get("message", ""))
        self._refresh_watchlist()

    def _offer_force_sell_if_hinted(self, message: str):
        """A blocked live sell (net-profit check, or a Seraph gate refusal)
        already spells out the "sell SYM force" escape hatch in its error
        text (see live.py's requireAllow/net-profit-check messages) — this
        turns that into a one-click button instead of making the user type
        it. Clicking still routes through _pick_and_run (background thread,
        real trade), same as every other execution path — this is a
        shortcut for typing the command, not a new bypass."""
        m = _FORCE_SELL_HINT_RE.search(message)
        if not m:
            return
        symbol = m.group(1)
        C = self._C
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"Sell {symbol} anyway, skipping the safety checks?")
        lbl.setFont(QFont("Segoe UI", 8))
        lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(lbl)
        btn = QPushButton(f"⚠ FORCE SELL {symbol}")
        btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; padding: 3px 8px; }}
            QPushButton:hover {{ border: 1px solid {C.RED}; }}
        """)
        btn.clicked.connect(lambda: self._pick_and_run(lambda: self.engine.sell_one(symbol, True)))
        lay.addWidget(btn)
        lay.addStretch()
        self._append_feed_widget(row)

    # ---------- stats/positions ----------

    def _refresh_stats(self, state: dict | None = None):
        state = state or self.engine.public_state()
        C = self._C
        self._mode_lbl.setText(state["mode"].upper())
        bal = state.get("balanceUsd")
        self._stat_labels["balance"].setText("—" if bal is None else f"${bal:.2f}")
        eq = state.get("equityUsd")
        self._stat_labels["equity"].setText("—" if eq is None else f"${eq:.2f}")
        pnl = state.get("realizedPnlUsd") or 0
        pnl_lbl = self._stat_labels["pnl"]
        pnl_lbl.setText(f"{'+' if pnl >= 0 else ''}${pnl:.2f}")
        pnl_lbl.setStyleSheet(f"color: {C.GREEN if pnl >= 0 else C.RED}; background: transparent;")
        cfg = self.engine.config
        self._stat_labels["trades"].setText(f"{state.get('tradesToday', 0)}/{cfg['maxDailyTrades']}")
        positions = state.get("positions") or []
        self._stat_labels["positions"].setText(f"{len(positions)}/{cfg['maxOpenPositions']}")
        running = state.get("running")
        self._start_btn.setText("■ STOP" if running else "▶ START")

    def _refresh_positions(self):
        C = self._C
        while self._positions_layout.count() - 1 > 0:
            item = self._positions_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        positions = self.engine._positions()
        # GEMZ4US, Finding #24 (2026-09-17): a real, currently-open LIVE
        # position vanished from this panel entirely once switched back to
        # PAPER mode — _positions() only ever returns one list or the
        # other, keyed on armed_live. The position stayed fully open
        # on-chain the whole time (funds safe, verified on Etherscan); the
        # app just stopped showing it, with no indicator anywhere. Listed
        # here too, badged and read-only, so a real position is never
        # invisible just because the mode toggle is elsewhere.
        live_elsewhere = [] if self.engine.armed_live else (self.engine.state.get("livePositions") or [])
        if not positions and not live_elsewhere:
            empty_lbl = QLabel("no open positions")
            empty_lbl.setFont(QFont("Segoe UI", 9))
            empty_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._positions_layout.insertWidget(0, empty_lbl)
            return

        for p in live_elsewhere:
            held = " [HELD]" if p.get("held") else ""
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"[LIVE] {p['symbol']:<8} qty={p['qty']:.4f}  entry=${p['entryPriceUsd']:.6f}  cost=${p['costUsd']:.2f}{held}")
            lbl.setFont(QFont("Segoe UI", 9))
            lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
            lay.addWidget(lbl, stretch=1)
            note = QLabel("switch to LIVE mode to manage")
            note.setFont(QFont("Segoe UI", 7))
            note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(note)
            self._positions_layout.insertWidget(self._positions_layout.count() - 1, row)

        for i, p in enumerate(positions):
            held = " [HELD]" if p.get("held") else ""
            symbol = p["symbol"]
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"{symbol:<8} qty={p['qty']:.4f}  entry=${p['entryPriceUsd']:.6f}  cost=${p['costUsd']:.2f}{held}")
            lbl.setFont(QFont("Segoe UI", 9))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl, stretch=1)

            def _pos_btn(label: str, color: str, cb):
                btn = QPushButton(label)
                btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedWidth(88)
                btn.setStyleSheet(f"""
                    QPushButton {{ color: {color}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                    QPushButton:hover {{ border: 1px solid {color}; }}
                """)
                btn.clicked.connect(cb)
                return btn

            lay.addWidget(_pos_btn("TAKE PROFIT", C.ACC, lambda _c=False, s=symbol: self._on_position_take_profit_click(s)))
            lay.addWidget(_pos_btn("SELL", C.RED, lambda _c=False, s=symbol: self._on_position_sell_click(s)))
            self._positions_layout.insertWidget(i, row)

    def _on_position_sell_click(self, symbol: str):
        if not any(p["symbol"] == symbol for p in self.engine._positions()):
            return
        self._append_feed_text(f"> sell {symbol}")
        self._add_percent_picker(f"pick how much of {symbol} to sell:", symbol, [25, 50, 75, 100],
                                  lambda sym, pct: self._pick_and_run(lambda: self.engine.sell_one(sym) if pct == 100 else self.engine.partial_sell(sym, pct)))

    def _on_position_take_profit_click(self, symbol: str):
        if not any(p["symbol"] == symbol for p in self.engine._positions()):
            return
        self._append_feed_text(f"> take profit {symbol}")
        self._add_percent_picker(f"pick a percentage of {symbol} to take profit on:", symbol, [10, 20, 25, 50],
                                  lambda sym, pct: self._pick_and_run(lambda: self.engine.partial_sell(sym, pct)))

    def _refresh_watchlist(self):
        C = self._C
        while self._watchlist_layout.count() - 1 > 0:
            item = self._watchlist_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        watchlist = self.engine.config.get("watchlist") or []
        if not watchlist:
            empty_lbl = QLabel("watchlist empty — see feed for +WATCH suggestions, or use 'watch SYM:0xADDR'")
            empty_lbl.setFont(QFont("Segoe UI", 8))
            empty_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            empty_lbl.setWordWrap(True)
            self._watchlist_layout.insertWidget(0, empty_lbl)
            return

        for i, w in enumerate(watchlist):
            chain = w.get("chain") or chains_mod.DEFAULT_CHAIN
            chain_name = chains_mod.resolve(chain)["name"]
            addr = w.get("address", "")
            short = f"{addr[:6]}…{addr[-4:]}" if addr else "?"
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"{w['symbol']:<8} {chain_name:<12} {short}")
            lbl.setFont(QFont("Segoe UI", 9))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl, stretch=1)
            buy_btn = QPushButton("BUY")
            buy_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            buy_btn.setFixedWidth(48)
            buy_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.GREEN}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                QPushButton:hover {{ border: 1px solid {C.GREEN}; }}
            """)
            entry = f"{w['symbol']}:{chain}:{addr}"
            buy_btn.clicked.connect(lambda _checked=False, e=entry: self._on_watchlist_buy_click(e))
            lay.addWidget(buy_btn)

            remove_btn = QPushButton("REMOVE")
            remove_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.setFixedWidth(60)
            remove_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 1px; }}
                QPushButton:hover {{ border: 1px solid {C.RED}; }}
            """)
            remove_btn.clicked.connect(lambda _checked=False, sym=w["symbol"]: self._on_watchlist_remove_click(sym))
            lay.addWidget(remove_btn)

            self._watchlist_layout.insertWidget(i, row)

    def _on_watchlist_buy_click(self, entry: str):
        # Same gated path a typed "buy SYM:0xADDR" command uses — real
        # Seraph screen, no bypass — just a shortcut for typing it.
        self._append_feed_text(f"> buy {entry}")
        self._run_command(f"buy {entry}")

    def _on_watchlist_remove_click(self, symbol: str):
        result = self.engine.remove_watch(symbol)
        self._append_feed_text(("OK: " if result.get("ok") else "SYS: ") + result.get("message", ""))
        self._refresh_watchlist()

    # ---------- actions ----------

    def _on_start_stop(self):
        if self.engine.running:
            self.engine.stop()
        else:
            result = self.engine.start()
            if not result.get("ok", True):
                self._append_feed_text(f"SYS: {result.get('error')}")
        self._refresh_stats()

    def _on_command_submit(self):
        text = self._cmd_input.text().strip()
        if not text:
            return
        self._cmd_input.clear()
        self._append_feed_text(f"> {text}")
        self._run_command(text)

    def _run_command(self, text: str):
        # engine.command() can reach real network calls (market price,
        # Seraph gate, and — for buy/sell while armed live — real RPC +
        # on-chain confirmation), so it always runs in the background, never
        # directly from this click handler (see _background's docstring for
        # the freeze this fixes). "checking with Seraph" gives immediate
        # feedback before the engine's own gate/submit log lines start
        # streaming in.
        if re.match(r"^buy\s", text, re.I) and not re.search(r"\bforce\b", text, re.I):
            # "force" skips the risk screen entirely (see engine.py's
            # bypass_gate path) — showing "checking with Seraph" for it was
            # misleading, since no check is actually happening.
            pending = "SYS: checking with Seraph…"
        elif re.match(r"^unwrap\b", text, re.I):
            pending = "SYS: unwrapping WETH…"
        else:
            pending = None
        self._background(lambda: self.engine.command(text), self._handle_command_result, pending_text=pending)

    def _handle_command_result(self, result: dict):
        if result.get("unrecognized"):
            # GEMZ4US, Item F (2026-09-20): "ask Seraph directly" sent the
            # user looking for a way to talk to Seraph, which has no chat
            # interface at all — it's the backend risk/pricing service
            # Omni itself consults, not a conversational partner. Omni, in
            # the main chat, is who can actually answer a general question.
            self._append_feed_text(
                "SYS: not a trade command — ask Omni directly in the main chat for general questions (this command bar can never place a trade)."
            )
            return
        if result.get("sellPrompt"):
            self._add_percent_picker(result["message"], result["symbol"], [25, 50, 75, 100],
                                      lambda sym, pct: self._pick_and_run(lambda: self.engine.sell_one(sym) if pct == 100 else self.engine.partial_sell(sym, pct)))
            return
        if result.get("takeProfitPrompt"):
            self._add_percent_picker(result["message"], result["symbol"], [10, 20, 25, 50],
                                      lambda sym, pct: self._pick_and_run(lambda: self.engine.partial_sell(sym, pct)))
            return
        self._append_command_result(result)

    def _pick_and_run(self, fn):
        self._background(fn, self._handle_pick_result, pending_text="SYS: checking with Seraph…")

    def _handle_pick_result(self, result: dict):
        self._append_command_result(result)

    def _append_command_result(self, result: dict):
        """Shared tail for any engine call that returns {ok, message[,
        txHash, chain]} — renders with COPY/VIEW buttons when there's a
        real transaction (e.g. unwrap), offers a FORCE SELL button when the
        failure message hints at one, then refreshes stats/positions."""
        message = result.get("message", "")
        prefix = "SYS: " if not result.get("ok") else "OK: "
        if result.get("ok") and result.get("txHash"):
            self._append_feed_line_with_tx(prefix + message, result["txHash"], result.get("chain"))
        else:
            self._append_feed_text(prefix + message)
            if not result.get("ok"):
                self._offer_force_sell_if_hinted(message)
        self._refresh_stats()
        self._refresh_positions()
        self._refresh_watchlist()

    def _load_config_into_ui(self):
        cfg = self.engine.config
        for key, inp in self._config_inputs.items():
            inp.setText(str(cfg.get(key, "")))
        for key, cb in self._chain_checks.items():
            cb.setChecked(key in cfg.get("chains", []))

    def _on_save_config(self):
        partial = {}
        rejected = []  # GEMZ4US, Item A (2026-09-20): see the notice below
        field_labels = dict(_CONFIG_FIELDS)
        for key, inp in self._config_inputs.items():
            raw = inp.text().strip()
            if raw == "":
                continue
            try:
                partial[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                rejected.append((key, raw))
        partial["chains"] = [k for k, cb in self._chain_checks.items() if cb.isChecked()] or ["ethereum"]
        self.engine.set_config(partial)
        # GEMZ4US, 2026-09-19 (Item C): the $100,000 Min liquidity $ floor
        # clamps silently at save — confirmed correct (nothing is lost;
        # any value under the floor snaps to it), but the Event Feed only
        # ever showed "config saved" with no indication a value had been
        # raised, and the panel displays what was typed until save, not
        # what will actually be applied.
        requested_liq = partial.get("minLiquidityUsd")
        if requested_liq is not None and self.engine.config["minLiquidityUsd"] != requested_liq:
            # 2026-09-20 (Item B): a forced ",.0f" here printed "entered
            # 50,000" for a typed "50000" with no comma at all — a reader
            # could mistake it for the comma-input case just below, which
            # is silently ignored rather than clamped. Plain formatting so
            # the two cases can no longer look identical.
            self._append_feed_text(
                f"SYS: Min liquidity $ raised to the ${self.engine.config['minLiquidityUsd']:,.0f} minimum "
                f"(entered {requested_liq:.0f})"
            )
        # GEMZ4US, Item J (2026-09-23): same clamp notice for the 5-minute
        # scan-interval floor as minLiquidityUsd already gets above — they
        # asked for consistency after confirming by exact scan-start
        # timestamps that a lower value was accepted with no rejection but
        # silently never honored.
        requested_interval = partial.get("intervalMinutes")
        if requested_interval is not None and self.engine.config["intervalMinutes"] != requested_interval:
            self._append_feed_text(
                f"SYS: Scan interval raised to the {self.engine.config['intervalMinutes']:.0f}-minute minimum "
                f"(entered {requested_interval:.0f})"
            )
        # GEMZ4US, Item A (2026-09-20): every Config field silently ignored
        # unparseable input (a comma, a "K" suffix, ...) and kept its
        # previous value, with nothing in the Event Feed to say so — asked
        # directly whether a rejection notice was wanted; answer was yes.
        for key, raw in rejected:
            # GEMZ4US, Item O (2026-09-21): asked for a hint of the expected
            # format, since "1K" and "50,000" are both rejected outright
            # (no K-suffix or thousands-separator parsing).
            self._append_feed_text(
                f'SYS: {field_labels.get(key, key)} — could not read "{raw}" (digits only, e.g. 50000), '
                f'kept previous value {self.engine.config.get(key)}'
            )
        self._load_config_into_ui()
        # GEMZ4US, Item O (2026-09-21): "config saved" logged unconditionally
        # right after a rejection notice, reading as though every field
        # (including the rejected one) had just been saved — it hadn't;
        # only the rest of the config was.
        if rejected:
            self._append_feed_text(f"SYS: config saved (except the {len(rejected)} rejected field(s) above)")
        else:
            self._append_feed_text("SYS: config saved")

    def _on_reset(self):
        # Flagged 2026-09-07: this button sits directly next to SAVE CONFIG
        # and used to fire on a single click, wiping the persisted trade
        # ledger/P&L history with no way back. A confirmation dialog guards
        # this irreversible action; resetting the paper ledger risks no funds.
        #
        # Bug found by GEMZ4US 2026-09-08 (shipped in v1.10.3, fixed here):
        # the plain QMessageBox.question() convenience call renders with
        # every piece of app-supplied text invisible — message body AND
        # both button labels — leaving only the OS-drawn title bar legible.
        # Root cause: this app runs QApplication.setStyle("Fusion") (see
        # ui.py's JarvisUI.__init__), and on a Windows box with the OS set
        # to dark mode, Qt6's Fusion style picks up a dark-mode-derived
        # default QPalette for text color while a stock QMessageBox's own
        # box/button faces don't follow along the same way — light text on
        # a light face. world_panel.py's own "Remove sub-agent" confirm
        # dialog already worked around exactly this by overriding QLabel's
        # color explicitly; that fix was incomplete (never covered
        # QPushButton, so button labels alone stayed invisible there too —
        # fixed alongside this one). Building the box manually (instead of
        # the one-line .question() helper) so a stylesheet can be attached
        # before .exec().
        C = self._C
        box = QMessageBox(self)
        box.setWindowTitle("Reset ledger?")
        text = ("This permanently wipes the trade history, P&L, and watchlist, and stops "
                "the trader if it's running — there's no undo.")
        # GEMZ4US, Finding #29 (2026-09-17): this dialog used to say nothing
        # about LIVE — reset() (see engine.py) wipes livePositions along
        # with the paper ledger unconditionally, so a real, currently-open
        # on-chain position silently stopped being tracked with no warning
        # specific to it. Funds are never at risk (nothing is sold or
        # moved), but the app's own display goes wrong until the position
        # is brought back with the "adopt" command.
        live_positions = self.engine.state.get("livePositions") or []
        if live_positions:
            symbols = ", ".join(p["symbol"] for p in live_positions)
            text += (
                f"\n\n⚠ You have {len(live_positions)} open LIVE position(s) ({symbols}). "
                "Resetting stops tracking them here — your funds stay exactly where they are "
                "on-chain, nothing is sold or moved, but this app will no longer show them. "
                "Use the \"adopt\" command afterward to bring each one back into the ledger."
            )
        text += "\n\nReset the ledger?"
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setStyleSheet(
            f"QMessageBox {{ background: {C.PANEL_BG}; }} "
            f"QLabel {{ color: {C.TEXT}; background: transparent; }} "
            f"QPushButton {{ color: {C.TEXT}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; "
            f"border-radius: 4px; padding: 4px 14px; }}"
        )
        reply = box.exec()
        if reply != QMessageBox.StandardButton.Yes:
            return
        # engine.reset() already emits its own detailed "ledger reset..."
        # log line through the normal event bridge (_on_engine_event ->
        # _handle_event -> _append_feed_text) — a second, bare "SYS: ledger
        # reset" appended manually here used to duplicate it (GEMZ4US,
        # Finding #26, 2026-09-17: two "ledger reset" lines in the feed).
        self.engine.reset()
        self._refresh_stats()
        self._refresh_positions()
        self._refresh_watchlist()
        self._load_config_into_ui()
