"""Trader engine — scan -> analyze -> Seraph-gate -> paper-trade loop.

Port of the JS trader's engine.js. Every safety invariant from that file
carries over unchanged: fail-closed Seraph gate, armed_live never
persisted, deterministic command parser is the only path that can move a
position, min-liquidity floor can only be raised. See engine.js's own
comments (preserved here) for the reasoning behind each one.

Unlike the JS version (an Electron main-process singleton talking to a
renderer over IPC), this is a plain class: the caller (trader_panel.py)
supplies an `emit` callback instead of an IPC channel, and drives
start()/stop() from Qt. cycle() runs on a background thread — never the
Qt main thread — same pattern main.py uses for other slow actions
(run_in_executor), just via a plain threading.Thread here since this
module has no asyncio dependency.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from random import random

from . import chains as chains_mod
from . import dexscreener
from . import live as live_mod
from . import marketdata as market
from . import memecoins
from . import trending
from .analysis import analyze

MIN_LIQUIDITY_FLOOR_USD = 100000

DEFAULT_CONFIG = {
    "mode": "paper",
    "strategy": "swing",
    "startingBalanceUsd": 100,
    "watchlist": [],
    "tradeSizeMinUsd": 3,
    "tradeSizeMaxUsd": 10,
    "maxOpenPositions": 10,
    "maxDailyTrades": 20,
    "takeProfitPct": 4,
    "stopLossPct": 2,
    "maxHoldHours": 24,
    "minSignalScore": 60,
    "minLiquidityUsd": MIN_LIQUIDITY_FLOOR_USD,
    "profitTargetUsd": 20,
    "maxDrawdownUsd": 50,
    "swapFeePct": 0.3,
    "slippagePct": 0.5,
    "gasUsd": 3,
    "intervalMinutes": 15,
    "candleTimeframe": "hour",
    "candleLimit": 100,
    "autoDiscoverTrending": True,
    "trendingLimit": 5,
    "chains": ["ethereum"],
    "autoDiscoverVolumeSpikes": True,
    "volumeSpikeMinRatio": 3,
    "volumeSpikeLimit": 5,
    "autoDiscoverMemeCoins": False,
    "memeCoinLimit": 5,
    "blockLevels": ["unknown", "high", "critical"],
    "blockScoreAbove": 70,
    "minNetProfitUsd": 0,
    "maxPriceImpactBps": 300,
}

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


HELP_TEXT = (
    "COMMANDS<br>"
    "buy SYM:0xADDR &mdash; buy a token; must pass Seraph's initial risk screen<br>"
    "buy SYM:0xADDR force &mdash; same, but skips the risk screen too (your own risk)<br>"
    "sell SYM / close SYM &mdash; pick how much to sell now (25/50/75/100%)<br>"
    "sell SYM force &mdash; sell 100% immediately, skipping the gate (your own risk)<br>"
    "sell all / close all &mdash; sell every open position<br>"
    "hold SYM &mdash; exempt a position from auto take-profit (stop-loss/max-hold still apply)<br>"
    "unhold SYM &mdash; release a held position back to automatic control<br>"
    "take profit SYM &mdash; pick a percentage (10/20/25/50%) to sell now, leaving the rest open<br>"
    "watch SYM:0xADDR &mdash; add a token to the watchlist (scanned + auto-tradeable)<br>"
    "remove SYM / unwatch SYM &mdash; drop a token from the watchlist<br>"
    "unwrap [CHAIN] &mdash; live mode only: converts wallet WETH back to native ETH (sell proceeds land as WETH, see help on why). Runs automatically after every live sell whenever the WETH is worth clearly more than its own gas cost &mdash; this is only needed for WETH left over from before, or if an auto-unwrap got skipped as not worth it yet<br>"
    "sync / sync positions &mdash; live mode only: re-check on-chain balances now and close/adjust any position sold or moved outside the app (also runs automatically every scan cycle)<br>"
    "scan / /scan &mdash; scan right now instead of waiting for the rest of the interval<br>"
    "help / /help &mdash; show this list<br>"
    "anything else &mdash; asks Seraph directly (read-only, cannot trade)<br>"
    "add \":CHAIN:\" before the address to buy/watch on another chain, e.g. buy PEPE:base:0x... &mdash; "
    f"supported: {', '.join(chains_mod.CHAINS.keys())} (Ethereum if omitted; paper mode only off-Ethereum, live trading stays Ethereum-only)"
)


class TraderEngine:
    def __init__(self, emit=None, mcp_tools=None, mcp_call=None, wallet_status=None):
        self._emit_cb = emit
        self.mcp_tools = mcp_tools
        self.mcp_call = mcp_call
        self.wallet_status = wallet_status

        self._dir = _base_dir() / "config" / "trader"
        self._dir.mkdir(parents=True, exist_ok=True)

        self.config: dict = self._load_config()
        self.state: dict = self._load_state()

        self.running = False
        self._cycle_busy = False
        self._thread: threading.Thread | None = None
        self._wake_event = threading.Event()
        self._lock = threading.RLock()

        # Live-armed state is DELIBERATELY in-memory only, never persisted.
        # Every app restart starts back in paper mode; arming requires a
        # fresh, explicit confirmation each session with a wallet connected
        # at that moment.
        self.armed_live = False

        self._risk_tool: dict | None = None
        self._risk_result_tool: dict | None = None

        # Not persisted — just avoids re-hitting trending.discover() on
        # every phone-dashboard poll (see trending_suggestions()).
        self._suggestions_cache: dict = {"at": 0.0, "items": []}
        self._suggestions_refresh_lock = threading.Lock()

        live_mod.init(mcp_call=mcp_call, server_id="seraph-kondux")

    # ---------- persistence ----------

    def _config_file(self) -> Path:
        return self._dir / "config.json"

    def _state_file(self) -> Path:
        return self._dir / "state.json"

    def _journal_file(self) -> Path:
        return self._dir / "journal.jsonl"

    def _load_config(self) -> dict:
        saved = {}
        try:
            saved = json.loads(self._config_file().read_text(encoding="utf-8"))
        except Exception:
            pass
        cfg = {**DEFAULT_CONFIG, **saved, "mode": "paper"}
        watchlist = []
        for w in cfg.get("watchlist") or []:
            chain = w.get("chain") if chains_mod.is_supported(w.get("chain", "")) else chains_mod.DEFAULT_CHAIN
            entry = {"symbol": w.get("symbol"), "address": w.get("address"), "chain": chain}
            if entry["symbol"] and _ADDR_RE.match(entry["address"] or ""):
                watchlist.append(entry)
        cfg["watchlist"] = watchlist or list(DEFAULT_CONFIG["watchlist"])
        cfg["chains"] = [c for c in (cfg.get("chains") or []) if chains_mod.is_supported(c)] or [chains_mod.DEFAULT_CHAIN]
        if cfg["tradeSizeMaxUsd"] < cfg["tradeSizeMinUsd"]:
            cfg["tradeSizeMaxUsd"] = cfg["tradeSizeMinUsd"]
        cfg["minLiquidityUsd"] = max(cfg.get("minLiquidityUsd") or 0, MIN_LIQUIDITY_FLOOR_USD)
        return cfg

    def _save_config(self):
        self._config_file().write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def _load_state(self) -> dict:
        saved = None
        try:
            saved = json.loads(self._state_file().read_text(encoding="utf-8"))
        except Exception:
            pass
        base = {
            "balanceUsd": self.config["startingBalanceUsd"],
            "startingBalanceUsd": self.config["startingBalanceUsd"],
            "realizedPnlUsd": 0,
            "positions": [],
            "livePositions": [],
            "liveRealizedPnlUsd": 0,
            "liveStartingEquityUsd": None,
            "lastLiveEquityUsd": None,
            "tradesToday": {"date": _today(), "count": 0},
            "halted": None,
            "lastEquityUsd": self.config["startingBalanceUsd"],
            "emptyWatchlistPrompted": False,
        }
        return {**base, **saved} if saved else base

    def _persist(self):
        self._state_file().write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _emit(self, event: dict):
        e = {"at": _now_iso(), **event}
        try:
            with self._journal_file().open("a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")
        except Exception:
            pass
        if self._emit_cb:
            try:
                self._emit_cb(e)
            except Exception:
                pass

    def _positions(self) -> list:
        return self.state["livePositions"] if self.armed_live else self.state["positions"]

    def _reconcile_live_positions(self):
        """Live positions are only ever updated when THIS app executes a
        buy/sell — a manual sell made outside the app (another wallet,
        Etherscan, MetaMask) leaves the ledger, and therefore the equity
        total, silently stale. Check each open live position's real
        on-chain balance and close/shrink it to match reality."""
        if not self.armed_live or not self.state["livePositions"]:
            return
        ws = (self.wallet_status() if self.wallet_status else None) or {"connected": False}
        if not ws.get("connected"):
            return
        changed = False
        for pos in list(self.state["livePositions"]):
            try:
                real_qty = live_mod.token_balance(pos.get("chain"), pos["address"], ws["address"])
            except Exception as err:
                self._emit({"type": "log", "text": f"reconcile {pos['symbol']} skipped: {err}"})
                continue
            if real_qty <= 0:
                self.state["livePositions"] = [p for p in self.state["livePositions"] if p is not pos]
                self._emit({"type": "log", "text": f"{pos['symbol']} position closed — on-chain balance is 0 (sold or moved outside the app)"})
                changed = True
            elif real_qty < pos["qty"] * 0.999:
                shortfall_pct = (1 - real_qty / pos["qty"]) * 100
                pos["costUsd"] *= real_qty / pos["qty"]
                pos["qty"] = real_qty
                self._emit({"type": "log", "text": f"{pos['symbol']} position reduced {shortfall_pct:.1f}% — on-chain balance is lower than the ledger recorded (partial external sell/transfer)"})
                changed = True
        if changed:
            # cycle() can return early (e.g. empty watchlist) before its own
            # final state emit/equity recompute, so this must self-announce
            # with a fresh equity figure or the UI panel and balance/equity
            # numbers are left showing the stale ledger.
            self.state["lastLiveEquityUsd"] = self._equity([])
            self._persist()
            self._emit({"type": "state", **self.public_state()})
        else:
            self._persist()

    # ---------- Seraph MCP risk gate (fail-closed) ----------

    @staticmethod
    def _parse_verdict(text: str) -> dict:
        level = None
        score = None
        try:
            j = json.loads(text)
            level = j.get("risk_level") or j.get("riskLevel") or j.get("level") or j.get("verdict")
            score = j.get("risk_score", j.get("riskScore", j.get("score")))
        except Exception:
            pass
        if level is None:
            m = re.search(r"\b(critical|high|medium|moderate|low)\b(?:\s+risk)?", text, re.I)
            if m:
                level = m.group(1)
        if score is None:
            m = re.search(r"(?:risk[\s_-]*score|score)\D{0,5}(\d{1,3})", text, re.I)
            if m:
                score = int(m.group(1))
        return {
            "level": str(level).lower().replace("moderate", "medium") if level else None,
            "score": score if isinstance(score, (int, float)) else None,
        }

    @staticmethod
    def _build_gate_args(schema: dict, token: dict) -> dict:
        c = chains_mod.resolve(token.get("chain"))
        known = {
            "chainId": c["chainId"], "chain_id": c["chainId"],
            "chain": c["name"].lower(), "network": c["name"].lower(), "blockchain": c["name"].lower(),
            "address": token["address"], "tokenAddress": token["address"], "token_address": token["address"],
            "contract": token["address"], "contractAddress": token["address"], "contract_address": token["address"],
            "token": token["address"], "symbol": token["symbol"], "tokenSymbol": token["symbol"], "ticker": token["symbol"],
        }
        props = (schema or {}).get("properties") or {}
        args = {k: known[k] for k in props if k in known}
        if not args:
            args = {"chainId": c["chainId"], "chain": c["name"].lower(), "address": token["address"], "symbol": token["symbol"]}
        return args

    def _poll_gate_result(self, text: str, budget_seconds: float = 40.0) -> str:
        """If `text` is an async job envelope, poll the "<tool>_result"
        counterpart until a terminal verdict, "failed", or the budget runs
        out — then return the last text seen (fail-closed if it's still
        pending: _parse_verdict won't find a level/score in a pending
        envelope, so the normal unparseable-verdict fail-closed path
        applies). Mirrors live.js's requireAllow() polling loop."""
        deadline = time.monotonic() + budget_seconds
        while True:
            try:
                job = json.loads(text)
            except Exception:
                return text
            status = job.get("status")
            request_id = job.get("requestId")
            if status not in ("pending", "running") or not request_id or not self._risk_result_tool:
                return text
            if status == "failed" or time.monotonic() >= deadline:
                return text
            wait_s = min(max((job.get("retryAfterMs") or 2000) / 1000, 1), 10)
            time.sleep(wait_s)
            res = self.mcp_call(self._risk_result_tool["name"], {"requestId": request_id})
            if not res.get("ok"):
                return text
            text = res.get("text") or text

    def _risk_check(self, token: dict) -> dict:
        try:
            if not self._risk_tool:
                res = self.mcp_tools()
                if not res.get("ok"):
                    raise RuntimeError(res.get("error") or "tools/list failed")
                tools = res.get("tools") or []
                t = next((t for t in tools if t["name"] == "guardian_check_token"), None)
                if not t:
                    t = next((t for t in tools if re.search(r"token.*(risk|safe|check)|(risk|safe|check).*token", t["name"], re.I)
                              and not re.search(r"pretrade", t["name"], re.I)), None)
                if not t:
                    t = next((t for t in tools if re.search(r"risk|scan|analy", t["name"], re.I)
                              and not re.search(r"pretrade", t["name"], re.I)), None)
                if not t:
                    raise RuntimeError("no token-risk tool on Seraph MCP")
                self._risk_tool = t
                # Seraph's risk tools can return an async job envelope
                # ({"status":"pending"|"running", requestId, retryAfterMs})
                # instead of an immediate verdict — same contract already
                # documented for guardian_pretrade_check (live.js). The
                # counterpart poll tool is "<name>_result" by convention.
                self._risk_result_tool = next((t2 for t2 in tools if t2["name"] == f"{t['name']}_result"), None)
                self._emit({"type": "log", "text": f"Seraph risk tool: {self._risk_tool['name']}"})
            res = self.mcp_call(self._risk_tool["name"], self._build_gate_args(self._risk_tool.get("schema"), token))
            if not res.get("ok"):
                raise RuntimeError(res.get("error") or "tools/call failed")
            text = self._poll_gate_result(res.get("text") or "")
            v = self._parse_verdict(text)
            if v["level"] is None and v["score"] is None:
                return {"approved": False, **v, "reason": "verdict unparseable (fail-closed)"}
            level_blocked = v["level"] and v["level"] in self.config["blockLevels"]
            score_blocked = v["score"] is not None and v["score"] > self.config["blockScoreAbove"]
            if level_blocked or score_blocked:
                return {"approved": False, **v, "reason": f"blocked (level={v['level']}, score={v['score']})"}
            return {"approved": True, **v, "reason": f"approved (level={v['level']}, score={v['score']})"}
        except Exception as err:
            self._risk_tool = None
            return {"approved": False, "level": None, "score": None, "reason": f"Seraph MCP unavailable: {err} (fail-closed)"}

    # ---------- execution ----------

    def _is_chain_live_enabled(self, chain_key: str) -> bool:
        return chains_mod.is_live_supported(chain_key) and chain_key in self.config["chains"]

    def _enabled_live_chains(self) -> list[str]:
        return [c for c in self.config["chains"] if chains_mod.is_live_supported(c)]

    def _pick_trade_size_usd(self) -> float:
        lo, hi = self.config["tradeSizeMinUsd"], self.config["tradeSizeMaxUsd"]
        if hi <= lo:
            return lo
        return round((lo + random() * (hi - lo)) * 100) / 100

    @staticmethod
    def _merge_position(lst: list, entry: dict):
        existing = next((p for p in lst if p["symbol"] == entry["symbol"]), None)
        if not existing:
            lst.append(entry)
            return
        total_qty = existing["qty"] + entry["qty"]
        existing["entryPriceUsd"] = (existing["qty"] * existing["entryPriceUsd"] + entry["qty"] * entry["entryPriceUsd"]) / total_qty
        existing["qty"] = total_qty
        existing["costUsd"] += entry["costUsd"]
        if entry.get("txHash"):
            existing["txHash"] = entry["txHash"]

    def _execute_buy(self, token: dict, price_usd: float, context: dict):
        trade_size_usd = self._pick_trade_size_usd()
        if self.armed_live:
            if not self._is_chain_live_enabled(token.get("chain")):
                raise RuntimeError(
                    f"live execution is not enabled for {chains_mod.resolve(token.get('chain'))['name']} — "
                    f"{token['symbol']} was skipped (chain must be both live-capable and checked on in chain config)"
                )
            self._emit({"type": "log", "text": f"LIVE: submitting buy for {token['symbol']} on {chains_mod.resolve(token.get('chain'))['name']}…"})
            result = live_mod.live_buy(token=token, trade_size_usd=trade_size_usd,
                                        max_price_impact_bps=self.config["maxPriceImpactBps"], bypass_gate=True)
            self._merge_position(self.state["livePositions"], {
                "symbol": token["symbol"], "address": token["address"], "chain": token.get("chain", chains_mod.DEFAULT_CHAIN),
                "qty": result["qty"], "entryPriceUsd": result["priceUsd"], "costUsd": result["costUsd"],
                "openedAt": _now_iso(), "txHash": result["txHash"],
            })
            self.state["tradesToday"]["count"] += 1
            self._emit({"type": "buy", "symbol": token["symbol"], "address": token["address"], "priceUsd": result["priceUsd"],
                         "qty": result["qty"], "costUsd": result["costUsd"], "txHash": result["txHash"], "live": True, **context})
            return

        pct = (self.config["swapFeePct"] + self.config["slippagePct"]) / 100
        received = trade_size_usd * (1 - pct)
        total_cost = trade_size_usd + self.config["gasUsd"]
        if self.state["balanceUsd"] < total_cost:
            raise RuntimeError("insufficient paper balance")
        qty = received / price_usd
        self.state["balanceUsd"] -= total_cost
        self._merge_position(self.state["positions"], {
            "symbol": token["symbol"], "address": token["address"], "chain": token.get("chain", chains_mod.DEFAULT_CHAIN),
            "qty": qty, "entryPriceUsd": price_usd, "costUsd": total_cost, "openedAt": _now_iso(),
        })
        self.state["tradesToday"]["count"] += 1
        self._emit({"type": "buy", "symbol": token["symbol"], "address": token["address"],
                     "chain": token.get("chain", chains_mod.DEFAULT_CHAIN), "priceUsd": price_usd, "qty": qty,
                     "costUsd": total_cost, **context})

    def _execute_sell(self, position: dict, price_usd: float, reason: str, fraction: float = 1, bypass_gate: bool = False):
        sell_qty = position["qty"] * fraction
        cost_basis_usd = position["costUsd"] * fraction
        if self.armed_live:
            self._emit({"type": "log", "text": f"LIVE: submitting {'' if fraction >= 1 else f'{round(fraction * 100)}% '}sell for {position['symbol']}…"})
            result = live_mod.live_sell(position=position, min_net_profit_usd=self.config["minNetProfitUsd"],
                                         qty=sell_qty, cost_basis_usd=cost_basis_usd, bypass_gate=bypass_gate)
            pnl = result["proceedsUsd"] - cost_basis_usd
            self.state["liveRealizedPnlUsd"] += pnl
            if fraction >= 1:
                self.state["livePositions"] = [p for p in self.state["livePositions"] if p is not position]
            else:
                position["qty"] -= sell_qty
                position["costUsd"] -= cost_basis_usd
            self.state["tradesToday"]["count"] += 1
            self._emit({"type": "sell", "symbol": position["symbol"], "priceUsd": price_usd, "qty": sell_qty,
                         "proceedsUsd": result["proceedsUsd"], "pnlUsd": round(pnl, 4), "reason": reason,
                         "txHash": result["txHash"], "live": True, "partial": fraction < 1})
            self._maybe_auto_unwrap(position.get("chain"))
            return

        pct = (self.config["swapFeePct"] + self.config["slippagePct"]) / 100
        proceeds = sell_qty * price_usd * (1 - pct) - self.config["gasUsd"]
        pnl = proceeds - cost_basis_usd
        self.state["balanceUsd"] += proceeds
        self.state["realizedPnlUsd"] += pnl
        if fraction >= 1:
            self.state["positions"] = [p for p in self.state["positions"] if p is not position]
        else:
            position["qty"] -= sell_qty
            position["costUsd"] -= cost_basis_usd
        self.state["tradesToday"]["count"] += 1
        self._emit({"type": "sell", "symbol": position["symbol"], "priceUsd": price_usd, "qty": sell_qty,
                     "proceedsUsd": proceeds, "pnlUsd": round(pnl, 4), "reason": reason, "partial": fraction < 1})

    def _maybe_auto_unwrap(self, chain: str | None):
        """Called after every successful live sell — proceeds always land
        as WETH (see live.py's live_sell comment), so this checks whether
        the wallet's current WETH balance clears its own gas cost by a
        safe margin and, if so, converts it straight back to native ETH.
        Best-effort: any failure here must never surface as if the SELL
        itself failed, since the sell already succeeded and is done."""
        try:
            est = live_mod.estimate_auto_unwrap(chain)
        except Exception as err:
            self._emit({"type": "log", "text": f"auto-unwrap check skipped: {err}"})
            return
        if not est.get("worthIt"):
            return
        try:
            result = live_mod.unwrap_weth(chain, est.get("amountWei"))
            self._emit({"type": "log", "text": f"auto-unwrapped {result['amountEth']:.6f} WETH → ETH on {chains_mod.resolve(chain)['name']}"})
        except Exception as err:
            self._emit({"type": "log", "text": f"auto-unwrap failed (WETH still sitting in wallet, unaffected): {err}"})

    def refresh_live_equity(self):
        """Recomputes and caches live equity from a fresh on-chain ETH
        balance read. Equity is otherwise only recomputed at specific
        trigger points (end of a scan cycle, or when position reconcile
        detects a change) — a wallet balance change made OUTSIDE the app
        (manual unwrap, external transfer in/out) touches no position, so
        nothing else notices it. Call this before returning state to any
        client (see the phone dashboard's get_trader_state)."""
        if not self.armed_live:
            return
        try:
            self.state["lastLiveEquityUsd"] = self._equity([])
        except Exception:
            pass

    def trending_suggestions(self, limit: int = 10, max_age_s: float = 60) -> list[dict]:
        """Top-movers across enabled chains, already excluding tokens
        already on the watchlist. BLOCKS on real network calls
        (trending.discover(), which itself retries on HTTP 429 with 15/30/45s
        backoff) whenever the cache is stale — safe from the desktop's
        empty-watchlist cycle prompt (already off the GUI thread), but NEVER
        call this on a request-serving thread with a client waiting on a
        timely response. Use ensure_suggestions_refreshing() + cached_suggestions()
        for that (see the phone dashboard's get_trader_state)."""
        now = time.monotonic()
        if now - self._suggestions_cache["at"] >= max_age_s:
            self._refresh_suggestions_cache(limit)
        return self.cached_suggestions(limit)

    def cached_suggestions(self, limit: int = 10) -> list[dict]:
        """Instant, network-free read of whatever's currently cached —
        always re-filtered against the CURRENT watchlist so a token just
        added via +WATCH doesn't linger in suggestions until the next
        refresh."""
        held = {w["address"].lower() for w in self.config["watchlist"]}
        items = self._suggestions_cache["items"]
        return [t for t in items if t["address"].lower() not in held][:limit]

    def ensure_suggestions_refreshing(self, limit: int = 10, max_age_s: float = 60):
        """Fire-and-forget: kicks off a background refresh if the cache is
        stale and nothing is already refreshing it, then returns immediately.
        Lets a request handler (e.g. the phone's /api/trader/state) offer
        fresh-ish suggestions on the NEXT poll without ever blocking on the
        real network call itself — trending.discover()'s 429 backoff alone
        can take up to 45s per chain, far past any reasonable request timeout."""
        now = time.monotonic()
        if now - self._suggestions_cache["at"] < max_age_s:
            return
        if not self._suggestions_refresh_lock.acquire(blocking=False):
            return  # already refreshing
        def _worker():
            try:
                self._refresh_suggestions_cache(limit)
            finally:
                self._suggestions_refresh_lock.release()
        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_suggestions_cache(self, limit: int = 10):
        per_chain = max(3, -(-limit // len(self.config["chains"])))
        items: list[dict] = []
        for c in self.config["chains"]:
            try:
                items.extend(trending.discover(c, per_chain, self.config["minLiquidityUsd"]))
            except Exception:
                pass
        items.sort(key=lambda t: t["chg1h"], reverse=True)
        self._suggestions_cache = {"at": time.monotonic(), "items": items}

    # ---------- the cycle ----------

    def _equity(self, snaps: list[dict]) -> float:
        if self.armed_live:
            ws = (self.wallet_status() if self.wallet_status else None) or {"connected": False}
            if not ws.get("connected"):
                return self.state.get("lastLiveEquityUsd") or 0
            try:
                eth_price_usd = live_mod.eth_usd_price()
                eth_bal_usd = live_mod.wallet_equity_usd_across_chains(self._enabled_live_chains(), ws["address"], eth_price_usd)
                open_usd = 0
                for pos in self.state["livePositions"]:
                    snap = next((s for s in snaps if s["symbol"] == pos["symbol"]), None)
                    open_usd += pos["qty"] * (snap["priceUsd"] if snap else pos["entryPriceUsd"])
                return eth_bal_usd + open_usd
            except Exception:
                return self.state.get("lastLiveEquityUsd") or 0
        open_usd = 0
        for pos in self.state["positions"]:
            snap = next((s for s in snaps if s["symbol"] == pos["symbol"]), None)
            open_usd += pos["qty"] * (snap["priceUsd"] if snap else pos["entryPriceUsd"])
        return self.state["balanceUsd"] + open_usd

    def _check_stops(self, snaps: list[dict]) -> str | None:
        eq = self._equity(snaps)
        start_ref = (self.state.get("liveStartingEquityUsd") if self.state.get("liveStartingEquityUsd") is not None else eq) \
            if self.armed_live else self.state["startingBalanceUsd"]
        pnl = eq - start_ref
        if pnl >= self.config["profitTargetUsd"]:
            return f"PROFIT TARGET HIT: +${pnl:.2f}"
        if pnl <= -self.config["maxDrawdownUsd"]:
            return f"MAX DRAWDOWN HIT: ${pnl:.2f}"
        return None

    def _cycle(self):
        if self.state["tradesToday"]["date"] != _today():
            self.state["tradesToday"] = {"date": _today(), "count": 0}

        self._reconcile_live_positions()

        if not self.config["watchlist"]:
            if not self.state["emptyWatchlistPrompted"]:
                self.state["emptyWatchlistPrompted"] = True
                self._emit({"type": "log", "text": "watchlist is empty — pick at least one token below before trading starts"})
                try:
                    top10 = self.trending_suggestions(10)
                    if top10:
                        self._emit({"type": "watchlist-empty", "candidates": [
                            {"symbol": t["symbol"], "address": t["address"], "chain": t["chain"], "chg1h": t["chg1h"]} for t in top10
                        ]})
                    else:
                        self._emit({"type": "log", "text": 'no trending tokens available right now — try again shortly, or use "watch SYM:0x..." in the command bar'})
                except Exception as err:
                    self._emit({"type": "log", "text": f"could not fetch trending tokens: {err}"})
            return
        self.state["emptyWatchlistPrompted"] = False

        self._emit({"type": "log", "text": "scanning watchlist…"})
        scan_list = [{**t, "source": "watchlist"} for t in self.config["watchlist"]]

        if self.config["autoDiscoverTrending"]:
            try:
                held = {t["address"].lower() for t in self.config["watchlist"]}
                lists = []
                for c in self.config["chains"]:
                    try:
                        lists.append(trending.discover(c, self.config["trendingLimit"], self.config["minLiquidityUsd"]))
                    except Exception as err:
                        self._emit({"type": "log", "text": f"trending discovery skipped for {chains_mod.resolve(c)['name']}: {err}"})
                        lists.append([])
                fresh = [t for lst in lists for t in lst if t["address"].lower() not in held]
                if fresh:
                    self._emit({"type": "trending", "candidates": [
                        {"symbol": t["symbol"], "address": t["address"], "chain": t["chain"], "chg1h": t["chg1h"]} for t in fresh
                    ]})
                    scan_list += [{"symbol": t["symbol"], "address": t["address"], "chain": t["chain"], "source": "trending"} for t in fresh]
            except Exception as err:
                self._emit({"type": "log", "text": f"trending discovery skipped: {err}"})

        if self.config["autoDiscoverVolumeSpikes"]:
            try:
                held = {t["address"].lower() for t in self.config["watchlist"]}
                enabled_chains = set(self.config["chains"])
                spikes = dexscreener.discover_spikes(limit=self.config["volumeSpikeLimit"],
                                                      min_ratio=self.config["volumeSpikeMinRatio"],
                                                      min_liquidity_usd=self.config["minLiquidityUsd"])
                tradeable, alert_only = [], []
                for s in spikes:
                    chain_key = chains_mod.by_dexscreener_id(s["chainId"])
                    if chain_key and chain_key in enabled_chains and s["address"].lower() not in held:
                        tradeable.append({**s, "chain": chain_key})
                    else:
                        alert_only.append(s)
                if tradeable:
                    self._emit({"type": "volume-spike", "candidates": [
                        {"symbol": s["symbol"], "address": s["address"], "chain": s["chain"], "ratio": s["ratio"], "priceChange1hPct": s["priceChange1hPct"]}
                        for s in tradeable
                    ]})
                    scan_list += [{"symbol": s["symbol"], "address": s["address"], "chain": s["chain"], "source": "volume-spike"} for s in tradeable]
                if alert_only:
                    self._emit({"type": "volume-spike-alert", "candidates": [
                        {"symbol": s["symbol"], "chainId": s["chainId"], "ratio": s["ratio"], "priceChange1hPct": s["priceChange1hPct"]}
                        for s in alert_only
                    ]})
            except Exception as err:
                self._emit({"type": "log", "text": f"volume-spike scan skipped: {err}"})

        if self.config["autoDiscoverMemeCoins"]:
            try:
                held = {t["address"].lower() for t in self.config["watchlist"]}
                found = memecoins.discover(limit=self.config["memeCoinLimit"])
                trend = [t for t in found["trending"] if t["address"].lower() not in held]
                fresh = [t for t in found["new"] if t["address"].lower() not in held]
                if trend:
                    self._emit({"type": "meme-coins", "category": "trending", "candidates": [
                        {"symbol": t["symbol"], "address": t["address"], "chg1h": t["chg1h"], "chg24h": t["chg24h"]} for t in trend
                    ]})
                    scan_list += [{"symbol": t["symbol"], "address": t["address"], "source": "meme"} for t in trend]
                if fresh:
                    self._emit({"type": "meme-coins", "category": "new", "candidates": [
                        {"symbol": t["symbol"], "address": t["address"], "chg1h": t["chg1h"], "liquidityUsd": t["liquidityUsd"]} for t in fresh
                    ]})
                    scan_list += [{"symbol": t["symbol"], "address": t["address"], "source": "meme"} for t in fresh]
            except Exception as err:
                self._emit({"type": "log", "text": f"meme-coin scan skipped: {err}"})

        snaps = []
        for token in scan_list:
            if not self.running:
                return
            try:
                snaps.append(market.snapshot(token, self.config["candleTimeframe"], self.config["candleLimit"]))
            except Exception as err:
                self._emit({"type": "log", "text": f"skip {token['symbol']}: {err}"})
            time.sleep(4)
        if not snaps:
            self._emit({"type": "log", "text": "no market data this cycle"})
            return

        # Exits first
        for pos in list(self._positions()):
            snap = next((s for s in snaps if s["symbol"] == pos["symbol"]), None)
            if not snap:
                continue
            move_pct = ((snap["priceUsd"] - pos["entryPriceUsd"]) / pos["entryPriceUsd"]) * 100
            held_hours = (time.time() - datetime.fromisoformat(pos["openedAt"]).timestamp()) / 3600
            reason = None
            if move_pct >= self.config["takeProfitPct"] and not pos.get("held"):
                reason = f"take-profit +{move_pct:.2f}%"
            elif move_pct <= -self.config["stopLossPct"]:
                reason = f"stop-loss {move_pct:.2f}%"
            elif held_hours >= self.config["maxHoldHours"]:
                reason = f"max hold {held_hours:.1f}h"
            if reason:
                try:
                    self._execute_sell(pos, snap["priceUsd"], reason)
                except Exception as err:
                    self._emit({"type": "log", "text": f"sell {pos['symbol']} failed: {err}"})

        stop = self._check_stops(snaps)
        if not stop:
            held = {p["symbol"] for p in self._positions()}
            candidates = [analyze(s, self.config["minLiquidityUsd"]) for s in snaps]
            candidates = [c for c in candidates if c["direction"] == "buy" and c["score"] >= self.config["minSignalScore"] and c["symbol"] not in held]
            candidates.sort(key=lambda c: c["score"], reverse=True)
            self._emit({"type": "scan", "candidates": [{"symbol": c["symbol"], "score": c["score"]} for c in candidates]})

            for cand in candidates:
                if not self.running:
                    return
                if len(self._positions()) >= self.config["maxOpenPositions"]:
                    break
                if self.state["tradesToday"]["count"] >= self.config["maxDailyTrades"]:
                    self._emit({"type": "log", "text": "daily trade cap reached"})
                    break
                snap = next(s for s in snaps if s["symbol"] == cand["symbol"])
                token = {"symbol": snap["symbol"], "address": snap["address"], "chain": snap.get("chain", chains_mod.DEFAULT_CHAIN)}
                if self.armed_live and not self._is_chain_live_enabled(token["chain"]):
                    self._emit({"type": "log", "text": f"live execution not enabled for {chains_mod.resolve(token['chain'])['name']} — skipping {token['symbol']}"})
                    continue
                verdict = self._risk_check(token)
                self._emit({"type": "gate", "symbol": cand["symbol"], "address": snap["address"], "chain": token["chain"],
                             "signalScore": cand["score"], "source": snap.get("source"), **verdict})
                if not verdict["approved"]:
                    continue
                try:
                    self._execute_buy(token, cand["priceUsd"], {
                        "signalScore": cand["score"], "riskLevel": verdict["level"], "riskScore": verdict["score"], "source": snap.get("source"),
                    })
                except Exception as err:
                    self._emit({"type": "log", "text": f"buy {cand['symbol']} failed: {err}"})
            stop = self._check_stops(snaps)

        final_equity = self._equity(snaps)
        if self.armed_live:
            self.state["lastLiveEquityUsd"] = final_equity
        else:
            self.state["lastEquityUsd"] = final_equity

        if stop:
            self.state["halted"] = {"reason": stop, "at": _now_iso()}
            self._emit({"type": "halt", "reason": stop})
            self.running = False
            if self.armed_live:
                self.armed_live = False
                self._emit({"type": "log", "text": "■ live mode disarmed (stop condition hit) — back to paper"})

        self._persist()
        self._emit({"type": "state", **self.public_state()})

    # ---------- background loop ----------

    def _loop(self):
        while self.running:
            self._cycle_busy = True
            try:
                self._cycle()
            except Exception as err:
                self._emit({"type": "log", "text": f"cycle error: {err}"})
            finally:
                self._cycle_busy = False
            if not self.running:
                break
            wait_seconds = max(5, self.config["intervalMinutes"]) * 60
            self._wake_event.wait(wait_seconds)
            self._wake_event.clear()

    def scan_now(self) -> dict:
        if not self.running:
            return {"ok": False, "message": "trader is not running — start it first"}
        if self._cycle_busy:
            return {"ok": False, "message": "a scan is already in progress"}
        self._wake_event.set()
        return {"ok": True, "message": "scan triggered now"}

    # ---------- public API ----------

    def public_state(self) -> dict:
        live = self.armed_live
        return {
            "running": self.running,
            "mode": "live" if live else "paper",
            "balanceUsd": None if live else self.state["balanceUsd"],
            "startingBalanceUsd": self.state["liveStartingEquityUsd"] if live else self.state["startingBalanceUsd"],
            "equityUsd": self.state["lastLiveEquityUsd"] if live else self.state["lastEquityUsd"],
            "realizedPnlUsd": self.state["liveRealizedPnlUsd"] if live else self.state["realizedPnlUsd"],
            "positions": self.state["livePositions"] if live else self.state["positions"],
            "tradesToday": self.state["tradesToday"]["count"],
            "halted": self.state["halted"],
        }

    def status(self) -> dict:
        return {"ok": True, **self.public_state(), "config": self.config}

    def start(self) -> dict:
        with self._lock:
            if self.running:
                return self.status()
            if self.state["halted"]:
                return {"ok": False, "error": "halted: " + self.state["halted"]["reason"] + " — reset to start over"}
            self.running = True
            self._emit({"type": "log", "text": f"trader started ({'LIVE' if self.armed_live else 'paper'} mode)"})
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            self.running = False
            self._wake_event.set()
            self._emit({"type": "log", "text": "trader stopped"})
            self._persist()
            return self.status()

    def arm_live(self) -> dict:
        if self.armed_live:
            return {"ok": True, **self.status()}
        ws = (self.wallet_status() if self.wallet_status else None) or {"connected": False}
        if not ws.get("connected"):
            return {"ok": False, "error": "connect a wallet before arming live mode"}
        try:
            eth_price_usd = live_mod.eth_usd_price()
            eth_bal_usd = live_mod.wallet_equity_usd_across_chains(self._enabled_live_chains(), ws["address"], eth_price_usd)
            open_usd = sum(p["qty"] * p["entryPriceUsd"] for p in self.state["livePositions"])
            start_equity = eth_bal_usd + open_usd
        except Exception as err:
            return {"ok": False, "error": f"could not read wallet balance: {err}"}
        self.state["liveStartingEquityUsd"] = start_equity
        self.state["lastLiveEquityUsd"] = start_equity
        self.armed_live = True
        self._persist()
        live_chain_names = ", ".join(chains_mod.resolve(c)["name"] for c in self._enabled_live_chains()) or "none"
        self._emit({"type": "log", "text": f"⚠ LIVE MODE ARMED — wallet {ws['address']} — chains: {live_chain_names} — starting equity ${start_equity:.2f} — real funds at risk"})
        return {"ok": True, **self.status()}

    def disarm_live(self, reason: str | None = None) -> dict:
        if not self.armed_live:
            return {"ok": True, **self.status()}
        self.armed_live = False
        self._emit({"type": "log", "text": "live mode disarmed" + (f" ({reason})" if reason else "") + " — back to paper"})
        return {"ok": True, **self.status()}

    def set_config(self, partial: dict) -> dict:
        allowed = set(DEFAULT_CONFIG.keys())
        for k, v in (partial or {}).items():
            if k in allowed and k != "mode":
                self.config[k] = v
        if self.config["tradeSizeMaxUsd"] < self.config["tradeSizeMinUsd"]:
            self.config["tradeSizeMaxUsd"] = self.config["tradeSizeMinUsd"]
        self.config["minLiquidityUsd"] = max(self.config.get("minLiquidityUsd") or 0, MIN_LIQUIDITY_FLOOR_USD)
        self._save_config()
        return self.status()

    def reset(self) -> dict:
        self.stop()
        if self.armed_live:
            self.disarm_live("ledger reset")
        try:
            self._state_file().unlink()
        except Exception:
            pass
        try:
            self._journal_file().unlink()
        except Exception:
            pass
        self.state = self._load_state()
        self.config["watchlist"] = []
        self._save_config()
        self._emit({"type": "log", "text": f"ledger reset (paper + live tracking), watchlist cleared — fresh paper balance ${self.state['balanceUsd']}"})
        return self.status()

    def journal_tail(self, n: int = 100) -> list[dict]:
        try:
            lines = [l for l in self._journal_file().read_text(encoding="utf-8").split("\n") if l]
            return [json.loads(l) for l in lines[-n:]]
        except Exception:
            return []

    # ---------- manual commands ----------

    @staticmethod
    def _parse_entry(entry: str) -> dict | None:
        parts = [s.strip() for s in entry.split(":")]
        if len(parts) == 2:
            symbol, address = parts
            chain_key = chains_mod.DEFAULT_CHAIN
        elif len(parts) == 3:
            symbol, chain_key, address = parts
            chain_key = chain_key.lower()
        else:
            return None
        symbol = (symbol or "").upper()
        if not symbol or not _ADDR_RE.match(address or "") or not chains_mod.is_supported(chain_key):
            return None
        return {"symbol": symbol, "chain": chain_key, "address": address}

    def sell_one(self, symbol: str, bypass_gate: bool = False) -> dict:
        pos = next((p for p in self._positions() if p["symbol"].upper() == symbol), None)
        if not pos:
            return {"ok": False, "message": f"no open position in {symbol}"}
        try:
            price_usd = market.current_price(pos["address"], pos.get("chain"))
            self._execute_sell(pos, price_usd, "manual sell", 1, bypass_gate)
            self._persist()
            self._emit({"type": "state", **self.public_state()})
            return {"ok": True, "message": f"sold {symbol} @ ${price_usd}" + (" — Seraph gate bypassed" if bypass_gate else "")}
        except Exception as err:
            return {"ok": False, "message": f"sell {symbol} failed: {err}"}

    def hold_one(self, symbol: str) -> dict:
        pos = next((p for p in self._positions() if p["symbol"] == symbol), None)
        if not pos:
            return {"ok": False, "message": f"no open position in {symbol}"}
        if pos.get("held"):
            return {"ok": False, "message": f"{symbol} is already held"}
        pos["held"] = True
        self._persist()
        self._emit({"type": "state", **self.public_state()})
        return {"ok": True, "message": f'{symbol} held — take-profit auto-sell disabled (stop-loss/max-hold still apply). "unhold {symbol}" to release.'}

    def unhold_one(self, symbol: str) -> dict:
        pos = next((p for p in self._positions() if p["symbol"] == symbol), None)
        if not pos:
            return {"ok": False, "message": f"no open position in {symbol}"}
        if not pos.get("held"):
            return {"ok": False, "message": f"{symbol} is not held"}
        pos["held"] = False
        self._persist()
        self._emit({"type": "state", **self.public_state()})
        return {"ok": True, "message": f"{symbol} released — back under automatic control"}

    def partial_sell(self, symbol_raw: str, pct) -> dict:
        symbol = (symbol_raw or "").upper()
        pos = next((p for p in self._positions() if p["symbol"] == symbol), None)
        if not pos:
            return {"ok": False, "message": f"no open position in {symbol}"}
        try:
            fraction = float(pct) / 100
        except (TypeError, ValueError):
            fraction = -1
        if not (0 < fraction <= 1):
            return {"ok": False, "message": f"invalid take-profit percentage: {pct}"}
        try:
            price_usd = market.current_price(pos["address"], pos.get("chain"))
            self._execute_sell(pos, price_usd, f"manual take-profit {pct}%", fraction)
            self._persist()
            self._emit({"type": "state", **self.public_state()})
            return {"ok": True, "message": f"took {pct}% profit on {symbol} @ ${price_usd}"}
        except Exception as err:
            return {"ok": False, "message": f"take profit on {symbol} failed: {err}"}

    def buy_one(self, text: str) -> dict:
        raw = (text or "").strip()
        m = re.match(r"^(.*?)\s+force$", raw, re.I)
        bypass_gate = bool(m)
        entry = m.group(1).strip() if m else raw

        parsed = self._parse_entry(entry)
        if not parsed:
            return {"ok": False, "message": f'use SYMBOL:0xADDRESS or SYMBOL:CHAIN:0xADDRESS[ force], got: "{raw}"'}
        symbol, chain, address = parsed["symbol"], parsed["chain"], parsed["address"]
        already_held = any(p["symbol"] == symbol for p in self._positions())
        if not already_held and len(self._positions()) >= self.config["maxOpenPositions"]:
            return {"ok": False, "message": f"at max open positions ({self.config['maxOpenPositions']}) — sell something first"}
        token = {"symbol": symbol, "chain": chain, "address": address}
        try:
            price_usd = market.current_price(address, chain)
            if bypass_gate:
                verdict = {"approved": True, "level": None, "score": None}
                self._emit({"type": "gate", "symbol": symbol, "address": address, "chain": chain, "source": "manual",
                             "approved": True, "bypassed": True, "reason": "BYPASSED — user explicitly overrode the Seraph gate for this buy"})
            else:
                verdict = self._risk_check(token)
                self._emit({"type": "gate", "symbol": symbol, "address": address, "chain": chain, "source": "manual", **verdict})
                if not verdict["approved"]:
                    return {"ok": False, "message": f"Seraph {verdict['reason']}"}
            self._execute_buy(token, price_usd, {"riskLevel": verdict["level"], "riskScore": verdict["score"], "source": "manual", "bypassGate": bypass_gate})
            self._persist()
            self._emit({"type": "state", **self.public_state()})
            verb = "topped up" if already_held else "bought"
            return {"ok": True, "message": f"{verb} {symbol} @ ${price_usd}" + (" — Seraph gate bypassed" if bypass_gate else "")}
        except Exception as err:
            return {"ok": False, "message": f"buy {symbol} failed: {err}"}

    def sync_positions(self) -> dict:
        if not self.armed_live:
            return {"ok": False, "message": "sync only applies in live mode — paper positions can't drift from an on-chain wallet"}
        before = {p["symbol"]: p["qty"] for p in self.state["livePositions"]}
        self._reconcile_live_positions()  # emits its own state update if anything changed
        after = {p["symbol"]: p["qty"] for p in self.state["livePositions"]}
        closed = [sym for sym in before if sym not in after]
        changed = [sym for sym in after if sym in before and after[sym] != before[sym]]
        if not closed and not changed:
            return {"ok": True, "message": "positions already match on-chain balances"}
        parts = []
        if closed:
            parts.append(f"closed: {', '.join(closed)}")
        if changed:
            parts.append(f"adjusted: {', '.join(changed)}")
        return {"ok": True, "message": "synced — " + "; ".join(parts)}

    def sell_all(self) -> dict:
        if not self._positions():
            return {"ok": False, "message": "no open positions"}
        results = []
        for pos in list(self._positions()):
            try:
                price_usd = market.current_price(pos["address"], pos.get("chain"))
                self._execute_sell(pos, price_usd, "manual sell all")
                results.append(f"{pos['symbol']}@${price_usd}")
            except Exception as err:
                results.append(f"{pos['symbol']} FAILED: {err}")
        self._persist()
        self._emit({"type": "state", **self.public_state()})
        return {"ok": True, "message": f"sold: {', '.join(results)}"}

    def unwrap_weth(self, chain_text: str | None = None) -> dict:
        """Live-mode only: converts wallet WETH back to native ETH via
        WETH9's own withdraw() — not a swap, so it never touches the
        Seraph gate. Sell proceeds land as WETH (see live.py's live_sell
        comment on why); this is the deliberate way back to ETH."""
        if not self.armed_live:
            return {"ok": False, "message": "unwrap only applies in live mode — arm live first"}
        chain = (chain_text or "").strip().lower() or chains_mod.DEFAULT_CHAIN
        if not chains_mod.is_supported(chain):
            return {"ok": False, "message": f"unknown chain: {chain}"}
        try:
            result = live_mod.unwrap_weth(chain)
        except Exception as err:
            return {"ok": False, "message": f"unwrap failed: {err}"}
        msg = f"unwrapped {result['amountEth']:.6f} WETH → ETH on {chains_mod.resolve(chain)['name']}"
        self._emit({"type": "log", "text": msg})
        return {"ok": True, "message": msg, "txHash": result["txHash"], "chain": chain}

    def add_watch(self, text: str) -> dict:
        entries = [s.strip() for s in re.split(r"[,\n]", text) if s.strip()]
        added, invalid = [], []
        for entry in entries:
            parsed = self._parse_entry(entry)
            if not parsed:
                invalid.append(entry)
                continue
            symbol, chain, address = parsed["symbol"], parsed["chain"], parsed["address"]
            idx = next((i for i, w in enumerate(self.config["watchlist"]) if w["symbol"] == symbol), -1)
            entry_dict = {"symbol": symbol, "chain": chain, "address": address}
            if idx >= 0:
                self.config["watchlist"][idx] = entry_dict
            else:
                self.config["watchlist"].append(entry_dict)
            added.append(symbol)
        if not added:
            return {"ok": False, "message": f'no valid entries — use SYMBOL:0xADDRESS or SYMBOL:CHAIN:0xADDRESS, got: "{text}"'}
        self._save_config()
        msg = f"watching: {', '.join(added)}"
        if invalid:
            msg += f" (skipped invalid: {', '.join(invalid)})"
        self._emit({"type": "watchlist", "watchlist": self.config["watchlist"]})
        return {"ok": True, "message": msg}

    def remove_watch(self, text: str) -> dict:
        symbols = [s.strip().upper() for s in re.split(r"[,\s]+", text) if s.strip()]
        if not symbols:
            return {"ok": False, "message": "no symbols given"}
        before = len(self.config["watchlist"])
        self.config["watchlist"] = [w for w in self.config["watchlist"] if w["symbol"] not in symbols]
        if len(self.config["watchlist"]) == before:
            return {"ok": False, "message": f"not found in watchlist: {', '.join(symbols)}"}
        self._save_config()
        self._emit({"type": "watchlist", "watchlist": self.config["watchlist"]})
        return {"ok": True, "message": f"removed from watchlist: {', '.join(symbols)} (existing positions unaffected)"}

    def command(self, text: str) -> dict:
        raw = (text or "").strip()
        if not raw:
            return {"ok": False, "message": "empty command"}
        lower = raw.lower()

        if re.match(r"^/?help$", lower):
            return {"ok": True, "message": HELP_TEXT}
        if re.match(r"^/?scan$", lower):
            return self.scan_now()
        if re.match(r"^(sell|close)\s+all$", lower):
            return self.sell_all()
        if re.match(r"^/?sync(?:\s+positions)?$", lower):
            return self.sync_positions()

        m = re.match(r"^unwrap(?:\s+([a-z0-9]+))?$", lower)
        if m:
            return self.unwrap_weth(m.group(1))

        m = re.match(r"^(?:sell|close)\s+([a-z0-9]+)(\s+force)?$", lower)
        if m:
            symbol = m.group(1).upper()
            if m.group(2):
                return self.sell_one(symbol, True)
            if not any(p["symbol"] == symbol for p in self._positions()):
                return {"ok": False, "message": f"no open position in {symbol}"}
            return {"ok": True, "sellPrompt": True, "symbol": symbol, "message": f"pick how much of {symbol} to sell:"}

        m = re.match(r"^hold\s+([a-z0-9]+)$", lower)
        if m:
            return self.hold_one(m.group(1).upper())

        m = re.match(r"^unhold\s+([a-z0-9]+)$", lower)
        if m:
            return self.unhold_one(m.group(1).upper())

        m = re.match(r"^take[\s-]?profit\s+([a-z0-9]+)$", lower)
        if m:
            symbol = m.group(1).upper()
            if not any(p["symbol"] == symbol for p in self._positions()):
                return {"ok": False, "message": f"no open position in {symbol}"}
            return {"ok": True, "takeProfitPrompt": True, "symbol": symbol, "message": f"pick a percentage of {symbol} to take profit on:"}

        m = re.match(r"^buy\s+(.+)$", raw, re.I)
        if m:
            return self.buy_one(m.group(1))

        m = re.match(r"^watch\s+(.+)$", raw, re.I)
        if m:
            return self.add_watch(m.group(1))

        m = re.match(r"^(?:unwatch|remove)\s+(.+)$", raw, re.I)
        if m:
            return self.remove_watch(m.group(1))

        return {
            "ok": False,
            "unrecognized": True,
            "message": "unrecognized command. Try: buy SYM:0x... · sell <SYMBOL> · sell all · watch SYM:0x... · remove <SYMBOL>",
        }
