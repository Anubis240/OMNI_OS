"""trader_panel.py's TraderPanel._format_event — GEMZ4US, Finding #27
(2026-09-17): no Event Feed line carried its own timestamp, making exact
timing analysis (e.g. the Max Drawdown cadence question) rely on reading
the app's separate global clock at screenshot time. engine._emit already
stamps every event with "at" (UTC ISO); this only tests that it's now
shown. Pure static-method formatting logic — no QApplication needed."""

import unittest

from trader_panel import TraderPanel


class FormatEventTimestampTests(unittest.TestCase):
    def test_prefixes_local_time_when_at_is_present(self):
        text = TraderPanel._format_event({"type": "log", "text": "hello", "at": "2026-09-17T22:13:23+00:00"})
        self.assertRegex(text, r"^\[\d{2}:\d{2}:\d{2}\] SYS: hello$")

    def test_no_prefix_when_at_is_missing(self):
        self.assertEqual(TraderPanel._format_event({"type": "log", "text": "hello"}), "SYS: hello")

    def test_no_prefix_when_at_is_malformed(self):
        self.assertEqual(
            TraderPanel._format_event({"type": "log", "text": "hello", "at": "not-a-timestamp"}),
            "SYS: hello",
        )

    def test_body_formatting_is_unaffected_by_the_timestamp_wrapper(self):
        event = {"type": "buy", "symbol": "LIT", "qty": 0.807, "priceUsd": 8.54, "costUsd": 6.89, "at": "2026-09-17T22:13:23+00:00"}
        text = TraderPanel._format_event(event)
        self.assertIn("BUY LIT qty=0.8070", text)
        self.assertIn("cost=$6.89", text)


if __name__ == "__main__":
    unittest.main()
