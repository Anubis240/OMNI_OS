"""trader/engine.py — _reconcile_pending_live_buys and sync_positions'
handling of a LIVE buy that timed out waiting for confirmation
(live.BuyPendingError). Covers the ledger-tracking half of the fix for a
real FORCE BUY that confirmed on-chain but was reported as failed, with
the trade never recorded anywhere — see tests/test_trader_live.py for the
matching live.py-level coverage. No real RPC/network calls: TraderEngine
is pointed at a temp directory and live_mod.check_buy_receipt is mocked."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trader import engine as engine_mod
from trader import live as live_mod

OWNER_ADDRESS = "0x2222222222222222222222222222222222222222"
TOKEN_ADDRESS = "0x1111111111111111111111111111111111111111"
TX_HASH = "0x" + "ab" * 32


def _pending_entry(**overrides):
    entry = {
        "symbol": "STOCKER", "address": TOKEN_ADDRESS, "chain": "ethereum",
        "txHash": TX_HASH, "tradeSizeUsd": 7.87, "ethPriceUsd": 3000.0,
        "submittedAt": "2026-09-16T17:55:11+00:00", "context": {"source": "manual"},
    }
    entry.update(overrides)
    return entry


class ReconcilePendingLiveBuysTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})
        self.engine.armed_live = False  # pending buys must resolve even if live mode isn't currently armed (e.g. after a restart)

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_confirmed_pending_buy_is_merged_into_live_positions(self):
        self.engine.state["pendingLiveBuys"] = [_pending_entry()]
        outcome = {"status": "confirmed", "qty": 23362.0, "priceUsd": 8.0 / 23362.0, "costUsd": 8.0, "txHash": TX_HASH}
        with patch.object(live_mod, "check_buy_receipt", return_value=outcome):
            self.engine._reconcile_pending_live_buys()

        self.assertEqual(self.engine.state["pendingLiveBuys"], [])
        self.assertEqual(len(self.engine.state["livePositions"]), 1)
        pos = self.engine.state["livePositions"][0]
        self.assertEqual(pos["symbol"], "STOCKER")
        self.assertAlmostEqual(pos["qty"], 23362.0)
        self.assertEqual(pos["txHash"], TX_HASH)
        self.assertEqual(self.engine.state["tradesToday"]["count"], 1)

    def test_reverted_pending_buy_is_dropped_with_no_position(self):
        self.engine.state["pendingLiveBuys"] = [_pending_entry()]
        with patch.object(live_mod, "check_buy_receipt", return_value={"status": "reverted"}):
            self.engine._reconcile_pending_live_buys()

        self.assertEqual(self.engine.state["pendingLiveBuys"], [])
        self.assertEqual(self.engine.state["livePositions"], [])
        self.assertEqual(self.engine.state["tradesToday"]["count"], 0)

    def test_still_pending_stays_untouched(self):
        entry = _pending_entry()
        self.engine.state["pendingLiveBuys"] = [entry]
        with patch.object(live_mod, "check_buy_receipt", return_value={"status": "pending"}):
            self.engine._reconcile_pending_live_buys()

        self.assertEqual(self.engine.state["pendingLiveBuys"], [entry])
        self.assertEqual(self.engine.state["livePositions"], [])

    def test_disconnected_wallet_is_a_no_op(self):
        self.engine.wallet_status = lambda: {"connected": False}
        entry = _pending_entry()
        self.engine.state["pendingLiveBuys"] = [entry]
        with patch.object(live_mod, "check_buy_receipt") as mock_check:
            self.engine._reconcile_pending_live_buys()
        mock_check.assert_not_called()
        self.assertEqual(self.engine.state["pendingLiveBuys"], [entry])

    def test_sync_positions_reports_resolved_pending_count(self):
        self.engine.armed_live = True
        self.engine.state["pendingLiveBuys"] = [_pending_entry()]
        outcome = {"status": "confirmed", "qty": 23362.0, "priceUsd": 8.0 / 23362.0, "costUsd": 8.0, "txHash": TX_HASH}
        with patch.object(live_mod, "check_buy_receipt", return_value=outcome):
            result = self.engine.sync_positions()

        self.assertTrue(result["ok"])
        self.assertIn("resolved 1 pending buy(s)", result["message"])


class ParseAdoptEntryTests(unittest.TestCase):
    """2/3/4-part parsing, including the 3-part ambiguity between
    SYMBOL:CHAIN:ADDR and SYMBOL:ADDR:TXHASH."""

    def test_two_part_defaults_to_ethereum_no_tx_hash(self):
        result = engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:{TOKEN_ADDRESS}")
        self.assertEqual(result, {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS, "txHash": None})

    def test_three_part_with_chain_name(self):
        result = engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:base:{TOKEN_ADDRESS}")
        self.assertEqual(result["chain"], "base")
        self.assertIsNone(result["txHash"])

    def test_three_part_with_tx_hash_defaults_to_ethereum(self):
        result = engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")
        self.assertEqual(result, {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS, "txHash": TX_HASH})

    def test_four_part_with_chain_and_tx_hash(self):
        result = engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:base:{TOKEN_ADDRESS}:{TX_HASH}")
        self.assertEqual(result, {"symbol": "STOCKER", "chain": "base", "address": TOKEN_ADDRESS, "txHash": TX_HASH})

    def test_invalid_third_part_is_rejected(self):
        # Neither a valid chain+addr nor an addr+txhash combination.
        self.assertIsNone(engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:{TOKEN_ADDRESS}:notahash"))

    def test_unknown_chain_is_rejected(self):
        self.assertIsNone(engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:atlantis:{TOKEN_ADDRESS}"))

    def test_bad_address_is_rejected(self):
        self.assertIsNone(engine_mod.TraderEngine._parse_adopt_entry("STOCKER:0xnotanaddress"))

    def test_too_many_parts_is_rejected(self):
        self.assertIsNone(engine_mod.TraderEngine._parse_adopt_entry(f"STOCKER:base:{TOKEN_ADDRESS}:{TX_HASH}:extra"))


class AdoptOneTests(unittest.TestCase):
    """engine.adopt_one — the standing command GEMZ4US asked for so a real
    on-chain fill that fell through a timeout/RPC gap can be recorded in
    the ledger, with or without a tx hash for the cost basis."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})
        self.engine.armed_live = True

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_rejected_outside_live_mode(self):
        self.engine.armed_live = False
        result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")
        self.assertFalse(result["ok"])
        self.assertIn("live mode", result["message"])

    def test_rejected_when_wallet_disconnected(self):
        self.engine.wallet_status = lambda: {"connected": False}
        result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")
        self.assertFalse(result["ok"])
        self.assertIn("connect a wallet", result["message"])

    def test_rejected_on_malformed_entry(self):
        result = self.engine.adopt_one("not a valid entry")
        self.assertFalse(result["ok"])
        self.assertIn("use SYMBOL", result["message"])

    def test_wallet_address_used_as_token_address_is_rejected_with_zero_rpc_calls(self):
        # GEMZ4US, 2026-09-18 (Section A): 3/3 real attempts failed with a
        # generic web3 error, confirmed to mean the given address had no
        # contract code — almost certainly his wallet address used where
        # the token's contract address belongs. Caught here before any
        # network call at all.
        with patch.object(live_mod, "adopt_from_tx") as mock_adopt, \
             patch.object(live_mod, "token_balance") as mock_balance:
            result = self.engine.adopt_one(f"STOCKER:{OWNER_ADDRESS}:{TX_HASH}")
        mock_adopt.assert_not_called()
        mock_balance.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("your wallet address, not a contract", result["message"])

    def test_with_tx_hash_merges_exact_qty_and_cost(self):
        info = {"qty": 23362.0, "priceUsd": 8.0 / 23362.0, "costUsd": 8.0, "txHash": TX_HASH}
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", return_value=info):
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        self.assertIn("exact", result["message"])
        self.assertEqual(len(self.engine.state["livePositions"]), 1)
        pos = self.engine.state["livePositions"][0]
        self.assertAlmostEqual(pos["qty"], 23362.0)
        self.assertAlmostEqual(pos["costUsd"], 8.0)
        self.assertEqual(pos["txHash"], TX_HASH)
        # Adopting a historical fill isn't a fresh trading decision — must
        # not count against the daily trade cap.
        self.assertEqual(self.engine.state["tradesToday"]["count"], 0)

    def test_stop_loss_warning_when_adopted_position_already_underwater(self):
        # GEMZ4US, 2026-09-19 (Item A): UNI/LIT adopted at a gas-inclusive
        # entry that already sat ~11%/~5.5% below current market — past
        # the default 2% stop-loss the moment they were adopted, meaning
        # starting the trader would have sold them for real immediately.
        entry_price = 9.554533
        info = {"qty": 0.217686, "priceUsd": entry_price, "costUsd": 2.08, "txHash": TX_HASH}
        current_market_price = 8.50  # ~-11% vs entry, well past the 2% default stop-loss
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", return_value=info), \
             patch.object(engine_mod.market, "current_price", return_value=current_market_price):
            result = self.engine.adopt_one(f"UNI:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        self.assertIn("past your configured stop-loss", result["message"])
        self.assertIn("11.", result["message"])  # ~-11.03%
        entries = self.engine.journal_tail(10)
        self.assertTrue(any("past its stop-loss threshold" in e.get("text", "") for e in entries))

    def test_take_profit_warning_when_adopted_position_already_up_and_not_held(self):
        entry_price = 1.0
        info = {"qty": 100.0, "priceUsd": entry_price, "costUsd": 100.0, "txHash": TX_HASH}
        current_market_price = 1.10  # +10%, past the default 4% take-profit
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", return_value=info), \
             patch.object(engine_mod.market, "current_price", return_value=current_market_price):
            result = self.engine.adopt_one(f"UNI:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        self.assertIn("past your configured take-profit", result["message"])
        entries = self.engine.journal_tail(10)
        self.assertTrue(any("past its take-profit threshold" in e.get("text", "") for e in entries))

    def test_no_warning_when_price_is_within_thresholds(self):
        entry_price = 1.0
        info = {"qty": 100.0, "priceUsd": entry_price, "costUsd": 100.0, "txHash": TX_HASH}
        current_market_price = 1.005  # +0.5% — inside both default 4%/2% thresholds
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", return_value=info), \
             patch.object(engine_mod.market, "current_price", return_value=current_market_price):
            result = self.engine.adopt_one(f"UNI:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        self.assertNotIn("past your configured", result["message"])

    def test_warning_is_best_effort_and_does_not_break_adopt_if_price_lookup_fails(self):
        info = {"qty": 100.0, "priceUsd": 1.0, "costUsd": 100.0, "txHash": TX_HASH}
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", return_value=info), \
             patch.object(engine_mod.market, "current_price", side_effect=RuntimeError("no pools")):
            result = self.engine.adopt_one(f"UNI:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        self.assertNotIn("past your configured", result["message"])
        self.assertEqual(len(self.engine.state["livePositions"]), 1)

    def test_same_tx_hash_twice_is_rejected(self):
        self.engine.state["livePositions"] = [{
            "symbol": "STOCKER", "address": TOKEN_ADDRESS, "chain": "ethereum",
            "qty": 23362.0, "entryPriceUsd": 0.0003, "costUsd": 8.0,
            "openedAt": "2026-09-16T17:55:11+00:00", "txHash": TX_HASH,
        }]
        with patch.object(live_mod, "adopt_from_tx") as mock_adopt:
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")
        mock_adopt.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("already recorded", result["message"])

    def test_without_tx_hash_uses_balance_delta_and_market_price(self):
        with patch.object(live_mod, "token_balance", return_value=100.0), \
             patch.object(engine_mod.market, "current_price", return_value=2.0):
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}")

        self.assertTrue(result["ok"])
        self.assertIn("approximate", result["message"])
        pos = self.engine.state["livePositions"][0]
        self.assertAlmostEqual(pos["qty"], 100.0)
        self.assertAlmostEqual(pos["costUsd"], 200.0)

    def test_without_tx_hash_only_adopts_the_untracked_delta(self):
        self.engine.state["livePositions"] = [{
            "symbol": "STOCKER", "address": TOKEN_ADDRESS, "chain": "ethereum",
            "qty": 60.0, "entryPriceUsd": 1.0, "costUsd": 60.0,
            "openedAt": "2026-09-16T17:55:11+00:00", "txHash": "",
        }]
        with patch.object(live_mod, "token_balance", return_value=100.0), \
             patch.object(engine_mod.market, "current_price", return_value=2.0):
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}")

        self.assertTrue(result["ok"])
        pos = self.engine.state["livePositions"][0]
        self.assertAlmostEqual(pos["qty"], 100.0)  # 60 already tracked + 40 newly adopted
        self.assertAlmostEqual(pos["costUsd"], 60.0 + 40.0 * 2.0)

    def test_without_tx_hash_rejected_when_ledger_already_matches(self):
        self.engine.state["livePositions"] = [{
            "symbol": "STOCKER", "address": TOKEN_ADDRESS, "chain": "ethereum",
            "qty": 100.0, "entryPriceUsd": 1.0, "costUsd": 100.0,
            "openedAt": "2026-09-16T17:55:11+00:00", "txHash": "",
        }]
        with patch.object(live_mod, "token_balance", return_value=100.0):
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}")
        self.assertFalse(result["ok"])
        self.assertIn("already fully accounted for", result["message"])

    def test_tx_lookup_failure_is_a_clean_error_not_a_raise(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "adopt_from_tx", side_effect=RuntimeError("all RPC endpoints failed")):
            result = self.engine.adopt_one(f"STOCKER:{TOKEN_ADDRESS}:{TX_HASH}")
        self.assertFalse(result["ok"])
        self.assertIn("adopt STOCKER failed", result["message"])


class PaperBuyCostBasisTests(unittest.TestCase):
    """GEMZ4US, Finding #25 (2026-09-17): a PAPER FORCE BUY showed
    qty=0.8070 entry=$4.781742 cost=$6.89, where qty*entry ($3.86) didn't
    match cost ($6.89) at all — entryPriceUsd was the raw market price
    while qty and costUsd came from two other, unrelated formulas.
    entryPriceUsd must now be cost-inclusive (total_cost / qty), matching
    how LIVE already derives its own priceUsd."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_qty_times_entry_price_equals_cost_reproducing_the_real_report(self):
        # Exact numbers from the report: trade_size_usd=3.89 (DEFAULT_CONFIG's
        # gasUsd=3, swapFeePct=0.3, slippagePct=0.5), price_usd=4.781742,
        # which reproduced his qty=0.8070/cost=6.89 exactly when solved
        # backwards from the reported figures.
        self.engine.config["tradeSizeMinUsd"] = 3.89
        self.engine.config["tradeSizeMaxUsd"] = 3.89
        token = {"symbol": "LIT", "address": TOKEN_ADDRESS, "chain": "ethereum"}
        self.engine._execute_buy(token, 4.781742, {"source": "manual"})

        pos = self.engine.state["positions"][0]
        self.assertAlmostEqual(pos["qty"], 0.8070, places=4)
        self.assertAlmostEqual(pos["costUsd"], 6.89, places=2)
        self.assertAlmostEqual(pos["qty"] * pos["entryPriceUsd"], pos["costUsd"], places=9)


class GasQuoteLogLineTests(unittest.TestCase):
    """GEMZ4US, Section F (2026-09-17): after locally verifying the gas
    margin fix, the exact 30% figure wasn't independently checkable from
    anything the app exposed."""

    def test_formats_quote_and_signed_price_in_gwei(self):
        text = engine_mod._gas_quote_log_line({"gasQuoteWei": 236_112_178, "gasSignedWei": 306_945_831})
        self.assertIn("Gas quote (RPC): 0.236112 Gwei", text)
        self.assertIn("margin 30% applied", text)
        self.assertIn("signing at 0.306946 Gwei", text)

    def test_missing_data_returns_none(self):
        self.assertIsNone(engine_mod._gas_quote_log_line({"gasQuoteWei": None, "gasSignedWei": None}))
        self.assertIsNone(engine_mod._gas_quote_log_line({}))


class RouteLogLineTests(unittest.TestCase):
    """Item E (GEMZ4US, 2026-09-21): a real sell routed through a $51-
    liquidity V2 pool at ~15% worse than a $67K pool, only discoverable
    afterward on Etherscan -- neither the DEX used nor the simulated
    price impact was ever surfaced anywhere."""

    def test_formats_dex_and_price_impact(self):
        text = engine_mod._route_log_line({"dex": "v2", "priceImpactBps": 1450})
        self.assertEqual(text, "Routed via Uniswap V2 — 14.50% simulated price impact")

    def test_formats_dex_without_price_impact(self):
        # bypass_gate=True (e.g. FORCE) never computes a gate, so no
        # price-impact figure is available -- the DEX alone is still useful.
        text = engine_mod._route_log_line({"dex": "v3", "priceImpactBps": None})
        self.assertEqual(text, "Routed via Uniswap V3")

    def test_missing_dex_returns_none(self):
        self.assertIsNone(engine_mod._route_log_line({}))
        self.assertIsNone(engine_mod._route_log_line({"dex": None, "priceImpactBps": 100}))


class ClearHaltTests(unittest.TestCase):
    """GEMZ4US, Finding #26 (2026-09-17): the only documented way out of a
    Max Drawdown HALT was RESET LEDGER, which also wipes trade history,
    P&L, and the watchlist. clear_halt() ("resume" command) clears just
    the halt gate and rebases the drawdown baseline, leaving everything
    else untouched."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_not_halted_is_a_no_op(self):
        result = self.engine.clear_halt()
        self.assertFalse(result["ok"])
        self.assertIn("not halted", result["message"])

    def test_clears_halt_and_rebases_paper_baseline_without_wiping_state(self):
        self.engine.state["halted"] = {"reason": "MAX DRAWDOWN HIT: $-7.66", "at": "2026-09-17T22:00:00+00:00"}
        self.engine.state["balanceUsd"] = 88.48
        self.engine.config["watchlist"] = [{"symbol": "LIT", "chain": "ethereum", "address": TOKEN_ADDRESS}]
        self.engine.state["positions"] = [{
            "symbol": "LIT", "address": TOKEN_ADDRESS, "chain": "ethereum",
            "qty": 0.8070, "entryPriceUsd": 4.78, "costUsd": 6.89, "openedAt": "2026-09-17T21:00:00+00:00",
        }]

        result = self.engine.clear_halt()

        self.assertTrue(result["ok"])
        self.assertIsNone(self.engine.state["halted"])
        # Trade history, watchlist, and open positions are untouched.
        self.assertEqual(len(self.engine.config["watchlist"]), 1)
        self.assertEqual(len(self.engine.state["positions"]), 1)
        # Baseline rebased to current equity (balance + open position value).
        self.assertAlmostEqual(self.engine.state["startingBalanceUsd"], self.engine._equity([]))

    def test_start_succeeds_after_clearing_the_halt(self):
        # start() spawns a real background thread whose first _cycle() can
        # make real network calls (trending_suggestions on an empty
        # watchlist) — mock threading.Thread so this stays a unit test of
        # the halted-gate check, not an integration test of the scan loop.
        self.engine.state["halted"] = {"reason": "MAX DRAWDOWN HIT: $-7.66", "at": "2026-09-17T22:00:00+00:00"}
        blocked = self.engine.start()
        self.assertFalse(blocked["ok"])
        self.engine.running = False
        self.engine.clear_halt()
        with patch.object(engine_mod.threading, "Thread") as mock_thread:
            resumed = self.engine.start()
        self.assertTrue(resumed["ok"])
        mock_thread.assert_called_once()
        self.engine.running = False

    def test_resume_command_routes_to_clear_halt(self):
        self.engine.state["halted"] = {"reason": "MAX DRAWDOWN HIT: $-7.66", "at": "2026-09-17T22:00:00+00:00"}
        result = self.engine.command("resume")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.engine.state["halted"])


class LiveBalanceTests(unittest.TestCase):
    """GEMZ4US, Finding #41: BALANCE showed "-" in LIVE mode (public_state
    hardcoded balanceUsd to None whenever armed_live) while EQUITY worked
    fine. There is a real LIVE analog of PAPER's balanceUsd — the wallet's
    own uninvested ETH, in USD — mirroring PAPER's equity = balanceUsd +
    open positions. It's cached on self.state (not fetched fresh in
    public_state(), which must stay network-call-free for UI polling)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_arm_live_caches_wallet_balance(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            result = self.engine.arm_live()

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(self.engine.state["lastLiveBalanceUsd"], 42.5)

    def test_public_state_reports_live_balance_instead_of_none(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.arm_live()

        state = self.engine.public_state()
        self.assertEqual(state["mode"], "live")
        self.assertAlmostEqual(state["balanceUsd"], 42.5)

    def test_equity_refreshes_cached_balance_on_every_call(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.arm_live()

        with patch.object(live_mod, "eth_usd_price", return_value=3200.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=55.0):
            self.engine._equity([])

        self.assertAlmostEqual(self.engine.state["lastLiveBalanceUsd"], 55.0)
        self.assertAlmostEqual(self.engine.public_state()["balanceUsd"], 55.0)

    def test_falls_back_to_last_cached_value_when_wallet_disconnected(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.arm_live()

        self.engine.wallet_status = lambda: {"connected": False}
        self.engine._equity([])

        # Wallet unreachable mid-session: keep showing the last known
        # balance rather than reverting to "-", same fallback lastLiveEquityUsd already gets.
        self.assertAlmostEqual(self.engine.public_state()["balanceUsd"], 42.5)

    def test_paper_mode_balance_is_unaffected(self):
        self.engine.state["balanceUsd"] = 94.0
        state = self.engine.public_state()
        self.assertEqual(state["mode"], "paper")
        self.assertAlmostEqual(state["balanceUsd"], 94.0)


class RefreshLiveEquityBalanceChangeLogTests(unittest.TestCase):
    """GEMZ4US, Item D (2026-09-21): BALANCE changes made outside the
    app's own actions (a deposit, an external transfer) were picked up
    by refresh_live_equity() already, but silently -- no sign anything
    had happened. Also verifies the new periodic desktop-panel timer's
    target (refresh_live_equity itself is called every few seconds by
    the phone dashboard already, and now every 30s by the desktop panel
    regardless of running state -- see trader_panel.py)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.events = []
        self.engine = engine_mod.TraderEngine(
            emit=self.events.append,
            wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS},
        )

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_logs_when_balance_actually_changes(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.arm_live()
        self.events.clear()

        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=47.5):
            self.engine.refresh_live_equity()

        balance_logs = [e for e in self.events if e.get("type") == "log" and "BALANCE updated" in e.get("text", "")]
        self.assertEqual(len(balance_logs), 1)
        self.assertIn("$47.50", balance_logs[0]["text"])
        self.assertIn("$42.50", balance_logs[0]["text"])

    def test_does_not_log_when_balance_is_unchanged(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.arm_live()
        self.events.clear()

        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.refresh_live_equity()

        balance_logs = [e for e in self.events if e.get("type") == "log" and "BALANCE updated" in e.get("text", "")]
        self.assertEqual(balance_logs, [])

    def test_does_not_log_on_the_very_first_call(self):
        # No previous balance cached yet -- nothing to compare against.
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=42.5):
            self.engine.armed_live = True
            self.engine.refresh_live_equity()

        balance_logs = [e for e in self.events if e.get("type") == "log" and "BALANCE updated" in e.get("text", "")]
        self.assertEqual(balance_logs, [])

    def test_is_a_no_op_in_paper_mode(self):
        self.engine.armed_live = False
        self.engine.refresh_live_equity()
        self.assertEqual(self.events, [])


class PositionMarkPriceTests(unittest.TestCase):
    """Item J (GEMZ4US, 2026-09-20): EQUITY valued every adopted position
    at cost, never market, because _equity()'s per-position fallback
    always used entryPriceUsd whenever no scan snapshot existed for that
    symbol -- true for every call except the scan loop itself. Fixed via
    an opt-in fetch_missing_prices flag: on for the low-frequency,
    deliberate call sites (end of a scan cycle, adopt, clear_halt, sync),
    off for refresh_live_equity() (polled every few seconds by the phone
    dashboard -- a live per-position fetch there would hammer the price
    API on every poll)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})
        self.pos = {"symbol": "LINK", "address": TOKEN_ADDRESS, "chain": "ethereum", "entryPriceUsd": 10.0}

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_uses_the_snap_price_when_available_regardless_of_the_flag(self):
        snaps = [{"symbol": "LINK", "priceUsd": 12.0}]
        with patch.object(engine_mod.market, "current_price") as mock_price:
            price = self.engine._position_mark_price(self.pos, snaps, fetch_missing_prices=True)
        self.assertAlmostEqual(price, 12.0)
        mock_price.assert_not_called()  # a snap already answers it — no network call needed

    def test_falls_back_to_cost_when_flag_is_off(self):
        with patch.object(engine_mod.market, "current_price") as mock_price:
            price = self.engine._position_mark_price(self.pos, [], fetch_missing_prices=False)
        self.assertAlmostEqual(price, 10.0)
        mock_price.assert_not_called()

    def test_fetches_live_price_when_flag_is_on_and_no_snap(self):
        with patch.object(engine_mod.market, "current_price", return_value=8.5) as mock_price:
            price = self.engine._position_mark_price(self.pos, [], fetch_missing_prices=True)
        self.assertAlmostEqual(price, 8.5)
        mock_price.assert_called_once_with(TOKEN_ADDRESS, "ethereum")

    def test_falls_back_to_cost_if_the_live_fetch_itself_fails(self):
        with patch.object(engine_mod.market, "current_price", side_effect=RuntimeError("boom")):
            price = self.engine._position_mark_price(self.pos, [], fetch_missing_prices=True)
        self.assertAlmostEqual(price, 10.0)

    def test_adopt_one_equity_reflects_market_not_cost(self):
        # Same scenario GEMZ4US reported: LINK adopted at market ($12.55),
        # itself now worth less ($10) -- EQUITY must move with the market
        # price, not silently freeze at the adopted cost basis.
        self.engine.armed_live = True
        info = {"qty": 0.259318, "priceUsd": 12.550213, "costUsd": 3.26, "txHash": TX_HASH}
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=2.19), \
             patch.object(live_mod, "adopt_from_tx", return_value=info), \
             patch.object(engine_mod.market, "current_price", return_value=10.0):
            result = self.engine.adopt_one(f"LINK:{TOKEN_ADDRESS}:{TX_HASH}")

        self.assertTrue(result["ok"])
        expected_equity = 2.19 + 0.259318 * 10.0  # balance + market value, not cost ($3.26)
        self.assertAlmostEqual(self.engine.state["lastLiveEquityUsd"], expected_equity, places=4)


class EquityRefreshOnLowFrequencyActionsTests(unittest.TestCase):
    """GEMZ4US, 2026-09-21 (Item C): "2 ARMs, one sync, one interrupted
    scan" all still showed EQUITY = BALANCE + cost, even after the
    2026-09-20 EQUITY-at-cost fix. Traced further: arm_live() had its OWN
    separate, cost-only open_usd calculation that never went through
    _equity()/_position_mark_price at all; sync_positions() only
    refreshed EQUITY when a reconcile found something to change, leaving
    the common "already matches" case exactly as stale as before; and
    stop() never recomputed EQUITY either, so a scan interrupted by
    stopping mid-cycle never reached _cycle()'s own end-of-scan refresh."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})
        self.engine.state["livePositions"] = [
            {"symbol": "LIT", "address": TOKEN_ADDRESS, "chain": "ethereum",
             "qty": 2.0, "entryPriceUsd": 10.0, "costUsd": 20.0, "openedAt": engine_mod._now_iso()},
        ]

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_arm_live_values_open_positions_at_market_not_cost(self):
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=5.0), \
             patch.object(engine_mod.market, "current_price", return_value=7.0) as mock_price:
            result = self.engine.arm_live()

        self.assertTrue(result["ok"])
        expected = 5.0 + 2.0 * 7.0  # balance + market value, not cost (2.0 * 10.0 = 20.0)
        self.assertAlmostEqual(self.engine.state["lastLiveEquityUsd"], expected)
        self.assertAlmostEqual(self.engine.state["liveStartingEquityUsd"], expected)
        mock_price.assert_any_call(TOKEN_ADDRESS, "ethereum")

    def test_sync_refreshes_equity_even_when_nothing_changed(self):
        self.engine.armed_live = True
        self.engine.state["lastLiveEquityUsd"] = 999.0  # stale, deliberately wrong
        with patch.object(live_mod, "token_balance", return_value=2.0), \
             patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=5.0), \
             patch.object(engine_mod.market, "current_price", return_value=7.0):
            result = self.engine.sync_positions()

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "positions already match on-chain balances")
        expected = 5.0 + 2.0 * 7.0
        self.assertAlmostEqual(self.engine.state["lastLiveEquityUsd"], expected)

    def test_stop_refreshes_live_equity(self):
        self.engine.armed_live = True
        self.engine.state["lastLiveEquityUsd"] = 999.0  # stale, deliberately wrong
        self.engine.running = True
        with patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "wallet_equity_usd_across_chains", return_value=5.0), \
             patch.object(engine_mod.market, "current_price", return_value=7.0):
            self.engine.stop()

        expected = 5.0 + 2.0 * 7.0
        self.assertAlmostEqual(self.engine.state["lastLiveEquityUsd"], expected)

    def test_stop_refreshes_paper_equity(self):
        self.engine.armed_live = False
        self.engine.running = True
        self.engine.state["positions"] = [
            {"symbol": "LIT", "address": TOKEN_ADDRESS, "chain": "ethereum",
             "qty": 2.0, "entryPriceUsd": 10.0, "costUsd": 20.0, "openedAt": engine_mod._now_iso()},
        ]
        self.engine.state["balanceUsd"] = 5.0
        self.engine.state["lastEquityUsd"] = 999.0  # stale, deliberately wrong
        with patch.object(engine_mod.market, "current_price", return_value=7.0):
            self.engine.stop()

        expected = 5.0 + 2.0 * 7.0
        self.assertAlmostEqual(self.engine.state["lastEquityUsd"], expected)


class ScanCadenceTests(unittest.TestCase):
    """GEMZ4US, Item C (2026-09-20): confirmed by exact timestamps across
    three consecutive scans that the real period between scan starts was
    cycle_duration + intervalMinutes, not just intervalMinutes -- _loop()
    only started its wait after _cycle() had already finished. Subtracting
    the cycle's own elapsed time makes scans start at the configured
    cadence instead."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_wait_is_reduced_by_the_cycles_own_duration(self):
        self.engine.config["intervalMinutes"] = 5  # 300s
        self.engine.running = True
        captured = {}

        def fake_wait(seconds):
            captured["seconds"] = seconds
            self.engine.running = False  # stop after this one iteration

        with patch.object(engine_mod.time, "monotonic", side_effect=[100.0, 145.0]), \
             patch.object(self.engine, "_cycle", return_value=None), \
             patch.object(self.engine._wake_event, "wait", side_effect=fake_wait):
            self.engine._loop()

        self.assertAlmostEqual(captured["seconds"], 300 - 45)

    def test_wait_never_goes_negative_when_the_cycle_outruns_the_interval(self):
        self.engine.config["intervalMinutes"] = 5  # 300s
        self.engine.running = True
        captured = {}

        def fake_wait(seconds):
            captured["seconds"] = seconds
            self.engine.running = False

        # Cycle took 400s -- longer than the configured 300s interval.
        with patch.object(engine_mod.time, "monotonic", side_effect=[100.0, 500.0]), \
             patch.object(self.engine, "_cycle", return_value=None), \
             patch.object(self.engine._wake_event, "wait", side_effect=fake_wait):
            self.engine._loop()

        self.assertEqual(captured["seconds"], 0)


class ExitsCoverOffWatchlistPositionsTests(unittest.TestCase):
    """Investigating Item J (GEMZ4US, 2026-09-20): a position not on the
    watchlist never appeared in a scan cycle's snaps, so _cycle()'s exits
    loop silently `continue`d past it -- no stop-loss, no take-profit, no
    max-hold, ever, for as long as it stayed off the watchlist. Every
    position opened via `adopt` is in exactly this state, since
    adopt_one() never adds one to the watchlist -- directly contradicting
    its own warning that "starting the trader will sell this on the next
    scan". Fixed by fetching the position's price directly instead of
    skipping it. No real network calls: market.snapshot/current_price and
    live_mod.token_balance are mocked; analyze() is mocked to never
    propose a buy, since this test isn't exercising that path."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine(wallet_status=lambda: {"connected": True, "address": OWNER_ADDRESS})
        self.engine.armed_live = True
        self.engine.running = True
        for flag in ("autoDiscoverTrending", "autoDiscoverVolumeSpikes", "autoDiscoverMemeCoins"):
            self.engine.config[flag] = False
        self.engine.config["watchlist"] = [{"symbol": "WATCHED", "chain": "ethereum", "address": "0x" + "11" * 20}]
        self.engine.config["stopLossPct"] = 2
        self.engine.config["takeProfitPct"] = 1000
        self.engine.config["maxHoldHours"] = 999
        self.engine.state["livePositions"] = [
            {"symbol": "WATCHED", "address": "0x" + "11" * 20, "chain": "ethereum",
             "qty": 10, "entryPriceUsd": 1.0, "costUsd": 10.0, "openedAt": engine_mod._now_iso()},
            {"symbol": "ADOPTED", "address": "0x" + "22" * 20, "chain": "ethereum",
             "qty": 5, "entryPriceUsd": 10.0, "costUsd": 50.0, "openedAt": engine_mod._now_iso()},
        ]

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_cycle_with(self, adopted_price_usd):
        watched_snap = {"symbol": "WATCHED", "address": "0x" + "11" * 20, "chain": "ethereum", "priceUsd": 1.0, "candles": []}
        sold = []

        def fake_execute_sell(pos, price_usd, reason, *a, **kw):
            sold.append((pos["symbol"], price_usd, reason))

        with patch.object(live_mod, "token_balance", side_effect=lambda chain, addr, owner: (
            10 if addr == "0x" + "11" * 20 else 5
        )), \
             patch.object(engine_mod.market, "snapshot", return_value=watched_snap), \
             patch.object(engine_mod.market, "current_price", return_value=adopted_price_usd) as mock_price, \
             patch.object(engine_mod, "analyze", return_value={"symbol": "WATCHED", "direction": "sell", "score": 0}), \
             patch.object(self.engine, "_execute_sell", side_effect=fake_execute_sell):
            self.engine._cycle()
        return sold, mock_price

    def test_off_watchlist_position_past_stop_loss_is_still_sold(self):
        # Entry $10, now $5 -- -50%, well past the 2% configured stop-loss.
        sold, mock_price = self._run_cycle_with(adopted_price_usd=5.0)

        adopted_sales = [s for s in sold if s[0] == "ADOPTED"]
        self.assertEqual(len(adopted_sales), 1, "adopt_one()'s own warning promises exactly this")
        _, price_usd, reason = adopted_sales[0]
        self.assertAlmostEqual(price_usd, 5.0)
        self.assertIn("stop-loss", reason)
        mock_price.assert_any_call("0x" + "22" * 20, "ethereum")

    def test_off_watchlist_position_within_bounds_is_not_sold(self):
        # Entry $10, now $9.90 -- -1%, inside the 2% configured stop-loss.
        sold, _ = self._run_cycle_with(adopted_price_usd=9.90)

        self.assertEqual([s for s in sold if s[0] == "ADOPTED"], [])


if __name__ == "__main__":
    unittest.main()
