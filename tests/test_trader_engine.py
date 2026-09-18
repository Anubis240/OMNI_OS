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


if __name__ == "__main__":
    unittest.main()
