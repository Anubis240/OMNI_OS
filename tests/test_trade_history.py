"""trader/engine.py's persistent trade history (trade_history.jsonl) and
trader_panel.py's TRADE HISTORY dialog.

GEMZ4US's suggestion (2026-09-23, again 2026-09-27): there was no list of
closed trades to audit realized P&L against, and RESET LEDGER, restarts
and updates lost the record (journal.jsonl is deleted by a reset and only
its last lines were ever read). Every sell the app makes is now appended
to a file that RESET LEDGER never deletes."""

import csv
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trader import engine as engine_mod
from trader import live as live_mod

TOKEN = "0x1111111111111111111111111111111111111111"


def _position(symbol="PEPE", qty=100.0, cost=10.0, **extra):
    return {"symbol": symbol, "address": TOKEN, "chain": "ethereum", "qty": qty, "costUsd": cost,
            "entryPriceUsd": cost / qty, "openedAt": "2026-09-27T10:00:00+00:00", **extra}


class TradeHistoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine()
        self.engine.armed_live = False

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _paper_sell(self, position, price, reason="take-profit", fraction=1):
        self.engine.state["positions"].append(position)
        self.engine._execute_sell(position, price, reason, fraction)

    def test_paper_sell_is_recorded(self):
        self._paper_sell(_position(), 0.2)
        (record,) = self.engine.trade_history()
        self.assertEqual(record["type"], "sell")
        self.assertEqual(record["mode"], "paper")
        self.assertEqual(record["symbol"], "PEPE")
        self.assertAlmostEqual(record["qty"], 100.0)
        self.assertAlmostEqual(record["costUsd"], 10.0)
        self.assertAlmostEqual(record["pnlUsd"], self.engine.state["realizedPnlUsd"])
        self.assertEqual(record["openedAt"], "2026-09-27T10:00:00+00:00")
        self.assertFalse(record["partial"])

    def test_partial_sell_records_the_sold_part(self):
        self._paper_sell(_position(), 0.2, fraction=0.25)
        (record,) = self.engine.trade_history()
        self.assertTrue(record["partial"])
        self.assertAlmostEqual(record["qty"], 25.0)
        self.assertAlmostEqual(record["costUsd"], 2.5)

    def test_live_sell_is_recorded_with_its_tx(self):
        self.engine.armed_live = True
        position = _position(symbol="UNI")
        self.engine.state["livePositions"].append(position)
        result = {"proceedsUsd": 12.5, "txHash": "0x" + "cd" * 32}
        with patch.object(live_mod, "live_sell", return_value=result), \
                patch.object(self.engine, "_maybe_auto_unwrap"):
            self.engine._execute_sell(position, 0.13, "manual")
        (record,) = self.engine.trade_history()
        self.assertEqual(record["mode"], "live")
        self.assertEqual(record["txHash"], result["txHash"])
        self.assertAlmostEqual(record["pnlUsd"], 2.5)

    def test_history_survives_reset_with_a_marker(self):
        self._paper_sell(_position(), 0.2)
        self.engine.state["detachedLivePositions"] = [_position(symbol="LINK", qty=0.35, cost=5.0)]
        self.engine.reset()
        self.assertFalse(any(e.get("type") == "sell" for e in self.engine.journal_tail()))   # journal wiped
        sell, reset = self.engine.trade_history()
        self.assertEqual(sell["type"], "sell")
        self.assertEqual(reset["type"], "reset")
        self.assertEqual(reset["detachedLivePositions"][0]["symbol"], "LINK")
        self.assertAlmostEqual(reset["detachedLivePositions"][0]["qty"], 0.35)

    def test_totals_since_reset_match_realized_pnl(self):
        self._paper_sell(_position(), 0.2)
        self.engine.reset()
        self._paper_sell(_position(symbol="DOGE"), 0.05, reason="stop-loss")
        totals = self.engine.history_totals()["paper"]
        self.assertEqual(totals["trades"], 2)
        self.assertEqual(totals["sinceResetTrades"], 1)
        self.assertAlmostEqual(totals["sinceResetPnlUsd"], self.engine.state["realizedPnlUsd"])

    def test_history_command(self):
        self.assertIn("no closed trades", self.engine.command("history")["message"])
        self._paper_sell(_position(), 0.2)
        message = self.engine.command("history")["message"]
        self.assertIn("PAPER: 1 closed", message)
        self.assertIn("PEPE", message)

    def test_csv_export(self):
        self._paper_sell(_position(), 0.2)
        self.engine.reset()
        path = self._tmp / "out.csv"
        self.assertEqual(self.engine.export_history_csv(path), 1)
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], list(engine_mod.TraderEngine.HISTORY_COLUMNS))
        self.assertEqual(rows[1][1:3], ["paper", "PEPE"])
        self.assertEqual(rows[2][1], "reset")

    def test_a_torn_last_line_is_skipped(self):
        self._paper_sell(_position(), 0.2)
        with self.engine._history_file().open("a", encoding="utf-8") as f:
            f.write('{"type": "sell", "sym')
        self.assertEqual(len(self.engine.trade_history()), 1)


class PriceFormatTests(unittest.TestCase):
    def test_plain_decimals(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from trader_panel import _price
        self.assertEqual(_price(7.5e-05), "$0.000075")
        self.assertEqual(_price(0.78022), "$0.78022")
        self.assertEqual(_price(8.54), "$8.54")
        self.assertEqual(_price(1234.5678), "$1,234.5678")
        self.assertEqual(_price(None), "")


class TradeHistoryDialogTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        self._tmp = Path(tempfile.mkdtemp())
        self._patcher = patch.object(engine_mod, "get_data_dir", return_value=self._tmp)
        self._patcher.start()
        self.engine = engine_mod.TraderEngine()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dialog_lists_trades_newest_first_with_resets(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui import C
        from trader_panel import TradeHistoryDialog

        position = _position()
        self.engine.state["positions"].append(position)
        self.engine._execute_sell(position, 0.2, "take-profit")
        self.engine.reset()
        dialog = TradeHistoryDialog(self.engine, C)
        table = dialog.findChild(__import__("PyQt6.QtWidgets", fromlist=["QTableWidget"]).QTableWidget)
        self.assertEqual(table.rowCount(), 2)
        self.assertIn("RESET LEDGER", table.item(0, 1).text())
        self.assertEqual(table.item(1, 2).text(), "PEPE")
        self.assertIn("PAPER: 1 closed", dialog._totals.text())
        dialog.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
