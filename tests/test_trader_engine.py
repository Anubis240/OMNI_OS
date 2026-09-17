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


if __name__ == "__main__":
    unittest.main()
