"""TraderPanel — the native trader UI, swapped in over the HUD in place of
the old separate Electron window (see MainWindow.open_trader_panel()).

Phase 1: paper-mode trading only, using trader/engine.py's TraderEngine.
Reuses the app's existing Leda palette (class C in ui.py) directly — no
new theme, no background video. `from ui import C, qcol` is a deferred
import (done inside __init__, not at module load time) so this module can
be imported by ui.py without a circular-import at load time; ui.py itself
only imports TraderPanel lazily, on first click of the TRADER button.
"""

from __future__ import annotations

import re
import threading
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QTextBrowser,
    QVBoxLayout, QWidget,
)

from trader import chains as chains_mod
from trader import mcp_client
from trader.engine import TraderEngine
from trader.wallet import local_wallet

_FEED_MAX_ITEMS = 300
_PRIVATE_KEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
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


def _looks_like_private_key(raw: str) -> bool:
    return bool(_PRIVATE_KEY_RE.match(raw.strip()))

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
    """First-use popup asking for the Seraph MCP API key, shown over the
    trader panel when no key was found (neither in the Seraph Guardian
    app's settings nor previously saved by save_seraph_api_key). Only
    appears once — mcp_client.save_seraph_api_key persists the key so
    later sessions never hit this again."""

    done = pyqtSignal(str)

    def __init__(self, C, parent=None):
        super().__init__(parent)
        self._C = C
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            McpKeySetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI):
            w = QLabel(txt)
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            w.setFont(QFont("Courier New", font_size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  SERAPH MCP KEY REQUIRED", 12, True))
        layout.addWidget(_lbl("The trader needs a Seraph API key to gate and execute trades.", 8, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Seraph API key…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)

        get_key_btn = QPushButton("Get a Seraph API key ↗")
        get_key_btn.setFont(QFont("Courier New", 8))
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
        layout.addWidget(get_key_btn)
        layout.addSpacing(10)

        submit_btn = QPushButton("▸  SAVE KEY")
        submit_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        submit_btn.setFixedHeight(36)
        submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        submit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO_BG}; border: 1px solid {C.PRI};
            }}
        """)
        submit_btn.clicked.connect(self._submit)
        layout.addWidget(submit_btn)

        skip_btn = QPushButton("Skip for now")
        skip_btn.setFont(QFont("Courier New", 8))
        skip_btn.setFixedHeight(20)
        skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        skip_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: none; }}
            QPushButton:hover {{ color: {C.TEXT}; }}
        """)
        skip_btn.clicked.connect(self.hide)
        layout.addWidget(skip_btn)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {self._C.RED}; }}"
            )
            return
        self.done.emit(key)


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

        self.engine = TraderEngine(
            emit=self._on_engine_event,
            mcp_tools=mcp_client.mcp_tools,
            # engine.py calls self.mcp_call(name, args) — a 2-arg contract
            # (it always targets the one built-in Seraph server) — while
            # mcp_client.mcp_call(server_id, name, args) is a 3-arg,
            # multi-server-capable function. Bind server_id here so the
            # arities actually match.
            mcp_call=lambda name, args: mcp_client.mcp_call(mcp_client.SERAPH_SERVER_ID, name, args),
            wallet_status=local_wallet.status,
        )
        self._event_sig.connect(self._handle_event)
        self._run_on_gui_sig.connect(lambda fn: fn())
        self._config_inputs: dict[str, QLineEdit] = {}
        self._chain_checks: dict[str, QCheckBox] = {}

        self._build_ui()
        self._refresh_stats()
        self._refresh_positions()
        self._refresh_watchlist()
        self._load_config_into_ui()

        self._mcp_key_overlay: "McpKeySetupOverlay | None" = None
        if not mcp_client.get_default_client().api_key:
            self._show_mcp_key_setup()

    # ---------- Seraph MCP key first-use setup ----------

    def _show_mcp_key_setup(self):
        ov = McpKeySetupOverlay(self._C, self)
        ov.done.connect(self._on_mcp_key_submitted)
        self._position_mcp_key_overlay(ov)
        ov.show()
        ov.raise_()
        self._mcp_key_overlay = ov

    def _position_mcp_key_overlay(self, ov: "McpKeySetupOverlay"):
        ow, oh = 440, 260
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
            return f"BUY{live} {event.get('symbol')} qty={event.get('qty', 0):.4f} @ ${event.get('priceUsd', 0):.6f}{tx_part}"
        if etype == "sell":
            live = " (LIVE)" if event.get("live") else ""
            pnl = event.get("pnlUsd", 0)
            sign = "+" if pnl >= 0 else ""
            tx = event.get("txHash")
            tx_part = f" — tx {_full_tx_hash(tx)[:10]}…" if tx else ""
            return f"SELL{live} {event.get('symbol')} pnl={sign}${pnl:.2f} ({event.get('reason', '')}){tx_part}"
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
        C = self._C
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("◆ SERAPH TRADER")
        title.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        header.addWidget(title)
        header.addStretch()

        self._mode_lbl = QLabel("PAPER")
        self._mode_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._mode_lbl.setStyleSheet(f"color: {C.ACC2}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 8px;")
        header.addWidget(self._mode_lbl)

        self._key_warn_lbl = None
        if not mcp_client.get_default_client().api_key:
            self._key_warn_lbl = QLabel("⚠ no Seraph API key found — trades will fail closed")
            self._key_warn_lbl.setFont(QFont("Courier New", 8))
            self._key_warn_lbl.setStyleSheet(f"color: {C.RED}; background: transparent;")
            header.addWidget(self._key_warn_lbl)

        self._start_btn = self._make_button("▶ START", self._on_start_stop)
        header.addWidget(self._start_btn)
        scan_btn = self._make_button("⟳ SCAN NOW", lambda: self._run_command("/scan"))
        header.addWidget(scan_btn)
        root.addLayout(header)

        # Stats row
        stats = QHBoxLayout()
        self._stat_labels: dict[str, QLabel] = {}
        for key, label in [("balance", "BALANCE"), ("equity", "EQUITY"), ("pnl", "REALIZED P&L"),
                            ("trades", "TRADES TODAY"), ("positions", "OPEN POSITIONS")]:
            box = QVBoxLayout()
            lbl = QLabel(label)
            lbl.setFont(QFont("Courier New", 7))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            val = QLabel("—")
            val.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
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
        pos_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
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
        watch_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
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
        feed_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
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
        root.addWidget(self._feed_scroll, stretch=1)

        # Command bar
        cmd_row = QHBoxLayout()
        self._cmd_input = QLineEdit()
        self._cmd_input.setPlaceholderText("buy SYM:0x... · sell SYM · watch SYM:0x... · help")
        self._cmd_input.setFont(QFont("Courier New", 9))
        self._cmd_input.setStyleSheet(
            f"background: {C.PANEL_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 6px;"
        )
        self._cmd_input.returnPressed.connect(self._on_command_submit)
        cmd_row.addWidget(self._cmd_input, stretch=1)
        cmd_row.addWidget(self._make_button("SEND", self._on_command_submit))
        root.addLayout(cmd_row)

        # Config lives in the main window's left sidebar (that otherwise-
        # empty stretch region below the sys-monitor bars), not down here —
        # see left_panel_widget() / MainWindow.set_left_panel_extra().
        self._left_config_widget = self._build_left_config_panel()

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
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        col.addWidget(hdr)

        for key, label in _CONFIG_FIELDS:
            lbl = QLabel(label)
            lbl.setFont(QFont("Courier New", 7))
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            col.addWidget(lbl)
            inp = QLineEdit()
            inp.setFont(QFont("Courier New", 8))
            inp.setStyleSheet(f"background: {C.PANEL_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 4px;")
            self._config_inputs[key] = inp
            col.addWidget(inp)

        chains_lbl = QLabel("Chains (paper scan)")
        chains_lbl.setFont(QFont("Courier New", 7))
        chains_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; margin-top: 4px;")
        col.addWidget(chains_lbl)
        for key, info in chains_mod.CHAINS.items():
            cb = QCheckBox(info["name"])
            cb.setFont(QFont("Courier New", 7))
            cb.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            self._chain_checks[key] = cb
            col.addWidget(cb)

        save_btn = self._make_button("SAVE CONFIG", self._on_save_config)
        col.addWidget(save_btn)
        reset_btn = self._make_button("RESET LEDGER", self._on_reset)
        col.addWidget(reset_btn)

        return wrap

    def _build_wallet_row(self) -> QWidget:
        """App-managed wallet dock + live-arm control. Local wallet only
        (WalletConnect dropped for this Python version — see the project
        plan). Two separate typed confirmations, matching the JS app's
        safety UX: "I OWN THIS RISK" gates holding real key material on
        this device at all (create/import/export); "LIVE" gates arming
        live trading once a wallet is already connected. Neither is a
        one-time toggle — both are re-checked at the moment of the click."""
        C = self._C
        wrap = QWidget()
        wrap.setStyleSheet(f"background: {C.PANEL_BG}; border: 1px solid {C.BORDER}; border-radius: 4px;")
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        row1 = QHBoxLayout()
        wallet_hdr = QLabel("▸ WALLET (app-managed, local signing — real funds, no per-trade approval)")
        wallet_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        wallet_hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        row1.addWidget(wallet_hdr)
        row1.addStretch()
        self._wallet_status_lbl = QLabel("not connected")
        self._wallet_status_lbl.setFont(QFont("Courier New", 8))
        self._wallet_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        row1.addWidget(self._wallet_status_lbl)
        outer.addLayout(row1)

        row2 = QHBoxLayout()
        self._risk_ack_input = QLineEdit()
        self._risk_ack_input.setPlaceholderText('type "I OWN THIS RISK" to enable create/import/export below')
        self._risk_ack_input.setFont(QFont("Courier New", 8))
        self._risk_ack_input.setFixedWidth(260)
        self._risk_ack_input.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 5px;")
        row2.addWidget(self._risk_ack_input)
        row2.addWidget(self._make_button("CREATE", self._on_wallet_create))
        self._import_input = QLineEdit()
        self._import_input.setPlaceholderText("private key or recovery phrase to import")
        self._import_input.setFont(QFont("Courier New", 8))
        # Masked (password-style) — this field holds real key material;
        # never echo it in cleartext on screen.
        self._import_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._import_input.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 5px;")
        row2.addWidget(self._import_input, stretch=1)
        row2.addWidget(self._make_button("IMPORT", self._on_wallet_import))
        row2.addWidget(self._make_button("EXPORT", self._on_wallet_export))
        outer.addLayout(row2)

        row3 = QHBoxLayout()
        self._wallet_lock_btn = self._make_button("UNLOCK WALLET", self._on_wallet_lock_unlock)
        row3.addWidget(self._wallet_lock_btn)
        row3.addWidget(self._make_button("REMOVE", self._on_wallet_remove))
        row3.addSpacing(20)

        live_hdr = QLabel("▸ LIVE MODE")
        live_hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        live_hdr.setStyleSheet(f"color: {C.RED}; background: transparent;")
        row3.addWidget(live_hdr)
        self._live_confirm_input = QLineEdit()
        self._live_confirm_input.setPlaceholderText('type "LIVE" to arm')
        self._live_confirm_input.setFont(QFont("Courier New", 8))
        self._live_confirm_input.setFixedWidth(140)
        self._live_confirm_input.setStyleSheet(f"background: {C.PANEL2_BG}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 5px;")
        row3.addWidget(self._live_confirm_input)
        self._arm_btn = self._make_button("⚠ ARM LIVE", self._on_arm_live)
        row3.addWidget(self._arm_btn)
        self._disarm_btn = self._make_button("DISARM", self._on_disarm_live)
        row3.addWidget(self._disarm_btn)
        row3.addStretch()
        outer.addLayout(row3)

        self._refresh_wallet_status()
        return wrap

    def _refresh_wallet_status(self):
        C = self._C
        st = local_wallet.status()
        if st["connected"]:
            addr = st["address"]
            short = addr[:6] + "…" + addr[-4:]
            self._wallet_status_lbl.setText(f"UNLOCKED — {short}")
            self._wallet_status_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
            self._wallet_lock_btn.setText("LOCK WALLET")
        elif st["exists"]:
            self._wallet_status_lbl.setText("locked (stored wallet exists)")
            self._wallet_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._wallet_lock_btn.setText("UNLOCK WALLET")
        else:
            self._wallet_status_lbl.setText("not connected")
            self._wallet_status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._wallet_lock_btn.setText("UNLOCK WALLET")

    def _risk_acknowledged(self) -> bool:
        ok = self._risk_ack_input.text().strip() == "I OWN THIS RISK"
        if not ok:
            self._append_feed_text('SYS: type "I OWN THIS RISK" in the field first — this stores real key material on this device.')
        return ok

    def _on_wallet_create(self):
        if not self._risk_acknowledged():
            return
        try:
            result = local_wallet.create()
        except Exception as err:
            self._append_feed_text(f"SYS: could not create wallet: {err}")
            return
        self._risk_ack_input.clear()
        self._refresh_wallet_status()
        self._append_feed_text(f"OK: wallet created — {result['address']}")
        self._append_feed_text(f"⚠ RECOVERY PHRASE (shown once, write it down): {result['mnemonic']}")

    def _on_wallet_import(self):
        if not self._risk_acknowledged():
            return
        raw = self._import_input.text().strip()
        if not raw:
            self._append_feed_text("SYS: enter a private key or recovery phrase to import")
            return
        try:
            if _looks_like_private_key(raw):
                result = local_wallet.import_private_key(raw)
            else:
                result = local_wallet.import_mnemonic(raw)
        except Exception as err:
            self._append_feed_text(f"SYS: import failed: {err}")
            return
        self._import_input.clear()
        self._risk_ack_input.clear()
        self._refresh_wallet_status()
        self._append_feed_text(f"OK: wallet imported — {result['address']}")

    def _on_wallet_export(self):
        if not self._risk_acknowledged():
            return
        try:
            secret = local_wallet.export_secret()
        except Exception as err:
            self._append_feed_text(f"SYS: export failed: {err}")
            return
        self._risk_ack_input.clear()
        self._append_feed_text(f"⚠ EXPORTED {secret['type']} (shown once): {secret['value']}")

    def _on_wallet_lock_unlock(self):
        st = local_wallet.status()
        try:
            if st["connected"]:
                local_wallet.lock()
                if self.engine.armed_live:
                    self.engine.disarm_live("wallet locked")
                self._append_feed_text("SYS: wallet locked")
            else:
                result = local_wallet.unlock()
                self._append_feed_text(f"OK: wallet unlocked — {result['address']}")
        except Exception as err:
            self._append_feed_text(f"SYS: {err}")
        self._refresh_wallet_status()
        self._refresh_stats()

    def _on_wallet_remove(self):
        if not self._risk_acknowledged():
            return
        if self.engine.armed_live:
            self.engine.disarm_live("wallet removed")
        local_wallet.remove()
        self._risk_ack_input.clear()
        self._refresh_wallet_status()
        self._refresh_stats()
        self._append_feed_text("SYS: wallet removed from this device")

    def _on_arm_live(self):
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

    def _panel_style(self) -> str:
        C = self._C
        return f"""
            QTextBrowser, QScrollArea {{
                background: {C.PANEL_BG}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 20px; }}
        """

    def _make_button(self, text: str, cb) -> QPushButton:
        C = self._C
        btn = QPushButton(text)
        btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {C.ACC2}; background: {C.PANEL2_BG};
                border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 5px 10px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border: 1px solid {C.BORDER_B}; }}
        """)
        btn.clicked.connect(cb)
        return btn

    # ---------- feed rendering ----------

    def _append_feed_text(self, text: str):
        C = self._C
        lbl = QLabel(text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
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
        lbl = QLabel(text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl, stretch=1)

        def _tx_button(label: str, cb):
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(64)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
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
        lbl = QLabel(text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl, stretch=1)

        url = chains_mod.dexscreener_url(event.get("chain") or chains_mod.DEFAULT_CHAIN, event.get("address") or "")
        if url:
            btn = QPushButton("VIEW")
            btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedWidth(64)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
                QPushButton:hover {{ border: 1px solid {C.BORDER_B}; }}
            """)
            btn.clicked.connect(lambda: webbrowser.open(url))
            lay.addWidget(btn)

        symbol, address, chain = event.get("symbol"), event.get("address"), event.get("chain")
        if not event.get("approved") and symbol and address:
            force_btn = QPushButton(f"⚠ FORCE BUY {symbol}")
            force_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            force_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            force_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 3px 8px; }}
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
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(lbl)
        for pct in percents:
            btn = QPushButton(f"{pct}%")
            btn.setFont(QFont("Courier New", 8))
            btn.setFixedWidth(48)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
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
            lbl.setFont(QFont("Courier New", 8))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl)
            if address:
                btn = QPushButton("+WATCH")
                btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedWidth(64)
                btn.setStyleSheet(f"""
                    QPushButton {{ color: {C.GREEN}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
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
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(lbl)
        btn = QPushButton(f"⚠ FORCE SELL {symbol}")
        btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; padding: 3px 8px; }}
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
        if not positions:
            empty_lbl = QLabel("no open positions")
            empty_lbl.setFont(QFont("Courier New", 9))
            empty_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._positions_layout.insertWidget(0, empty_lbl)
            return

        for i, p in enumerate(positions):
            held = " [HELD]" if p.get("held") else ""
            symbol = p["symbol"]
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"{symbol:<8} qty={p['qty']:.4f}  entry=${p['entryPriceUsd']:.6f}  cost=${p['costUsd']:.2f}{held}")
            lbl.setFont(QFont("Courier New", 9))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl, stretch=1)

            def _pos_btn(label: str, color: str, cb):
                btn = QPushButton(label)
                btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFixedWidth(88)
                btn.setStyleSheet(f"""
                    QPushButton {{ color: {color}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
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
        self._append_feed_text(f"&gt; sell {symbol}")
        self._add_percent_picker(f"pick how much of {symbol} to sell:", symbol, [25, 50, 75, 100],
                                  lambda sym, pct: self._pick_and_run(lambda: self.engine.sell_one(sym) if pct == 100 else self.engine.partial_sell(sym, pct)))

    def _on_position_take_profit_click(self, symbol: str):
        if not any(p["symbol"] == symbol for p in self.engine._positions()):
            return
        self._append_feed_text(f"&gt; take profit {symbol}")
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
            empty_lbl.setFont(QFont("Courier New", 8))
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
            lbl.setFont(QFont("Courier New", 9))
            lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            lay.addWidget(lbl, stretch=1)
            buy_btn = QPushButton("BUY")
            buy_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            buy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            buy_btn.setFixedWidth(48)
            buy_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.GREEN}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
                QPushButton:hover {{ border: 1px solid {C.GREEN}; }}
            """)
            entry = f"{w['symbol']}:{chain}:{addr}"
            buy_btn.clicked.connect(lambda _checked=False, e=entry: self._on_watchlist_buy_click(e))
            lay.addWidget(buy_btn)

            remove_btn = QPushButton("REMOVE")
            remove_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            remove_btn.setFixedWidth(60)
            remove_btn.setStyleSheet(f"""
                QPushButton {{ color: {C.RED}; background: {C.PANEL2_BG}; border: 1px solid {C.BORDER_A}; border-radius: 3px; }}
                QPushButton:hover {{ border: 1px solid {C.RED}; }}
            """)
            remove_btn.clicked.connect(lambda _checked=False, sym=w["symbol"]: self._on_watchlist_remove_click(sym))
            lay.addWidget(remove_btn)

            self._watchlist_layout.insertWidget(i, row)

    def _on_watchlist_buy_click(self, entry: str):
        # Same gated path a typed "buy SYM:0xADDR" command uses — real
        # Seraph screen, no bypass — just a shortcut for typing it.
        self._append_feed_text(f"&gt; buy {entry}")
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
        self._append_feed_text(f"&gt; {text}")
        self._run_command(text)

    def _run_command(self, text: str):
        # engine.command() can reach real network calls (market price,
        # Seraph gate, and — for buy/sell while armed live — real RPC +
        # on-chain confirmation), so it always runs in the background, never
        # directly from this click handler (see _background's docstring for
        # the freeze this fixes). "checking with Seraph" gives immediate
        # feedback before the engine's own gate/submit log lines start
        # streaming in.
        if re.match(r"^buy\s", text, re.I):
            pending = "SYS: checking with Seraph…"
        elif re.match(r"^unwrap\b", text, re.I):
            pending = "SYS: unwrapping WETH…"
        else:
            pending = None
        self._background(lambda: self.engine.command(text), self._handle_command_result, pending_text=pending)

    def _handle_command_result(self, result: dict):
        if result.get("unrecognized"):
            self._append_feed_text(
                "SYS: not a trade command — ask Seraph directly for general questions (this command bar can never place a trade)."
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
        for key, inp in self._config_inputs.items():
            raw = inp.text().strip()
            if raw == "":
                continue
            try:
                partial[key] = float(raw) if "." in raw else int(raw)
            except ValueError:
                pass
        partial["chains"] = [k for k, cb in self._chain_checks.items() if cb.isChecked()] or ["ethereum"]
        self.engine.set_config(partial)
        self._load_config_into_ui()
        self._append_feed_text("SYS: config saved")

    def _on_reset(self):
        self.engine.reset()
        self._refresh_stats()
        self._refresh_positions()
        self._refresh_watchlist()
        self._load_config_into_ui()
        self._append_feed_text("SYS: ledger reset")
