"""trader_panel.py's TraderPanel._format_event / _feed_timestamp.

GEMZ4US, Finding #27 (2026-09-17), confirmed still partial on
2026-09-18: an earlier fix added a timestamp inside _format_event, but
that only covers lines built from an engine event — echoed commands,
local status text ("SYS: checking with Seraph…"), and command results
("OK: bought...") are appended directly as plain text from UI code and
never touched _format_event, so they stayed unstamped. Timestamping
moved to a single _feed_timestamp() helper called at every feed-line
append point (_append_feed_text, _append_feed_line_with_tx,
_append_gate_feed_line) instead — this file tests the two pure,
QApplication-free pieces of that: _format_event now returns body text
only (no timestamp), and _feed_timestamp's own format."""

import re
import unittest

from trader_panel import TraderPanel


class FormatEventBodyOnlyTests(unittest.TestCase):
    """_format_event must NOT add its own timestamp any more — that would
    double it up with the one _append_feed_text/etc. now add at append
    time — regardless of whether "at" is present, missing, or malformed."""

    def test_no_prefix_when_at_is_present(self):
        text = TraderPanel._format_event({"type": "log", "text": "hello", "at": "2026-09-17T22:13:23+00:00"})
        self.assertEqual(text, "SYS: hello")

    def test_no_prefix_when_at_is_missing(self):
        self.assertEqual(TraderPanel._format_event({"type": "log", "text": "hello"}), "SYS: hello")

    def test_body_formatting_still_works(self):
        event = {"type": "buy", "symbol": "LIT", "qty": 0.807, "priceUsd": 8.54, "costUsd": 6.89}
        text = TraderPanel._format_event(event)
        self.assertIn("BUY LIT qty=0.8070", text)
        self.assertIn("cost=$6.89", text)


class FeedTimestampTests(unittest.TestCase):
    def test_format_is_bracketed_hh_mm_ss_with_trailing_space(self):
        ts = TraderPanel._feed_timestamp()
        self.assertRegex(ts, r"^\[\d{2}:\d{2}:\d{2}\] $")

    def test_composes_cleanly_with_any_line_text(self):
        line = TraderPanel._feed_timestamp() + "SYS: checking with Seraph…"
        self.assertTrue(re.match(r"^\[\d{2}:\d{2}:\d{2}\] SYS: checking with Seraph…$", line))


if __name__ == "__main__":
    unittest.main()
