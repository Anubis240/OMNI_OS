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
import time
import unittest
from pathlib import Path

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


class SeraphStatusTextTests(unittest.TestCase):
    def test_oauth_mode_shows_prefix_and_signed_in(self):
        text = TraderPanel._seraph_status_text({
            "mode": "api_key_oauth", "api_key_prefix": "mcfw_3fa9c1e", "needs_login": False,
        })
        self.assertIn("mcfw_3fa9c1e", text)
        self.assertIn("signed in", text)

    def test_manual_mode_labelled_manual(self):
        self.assertIn("manual", TraderPanel._seraph_status_text({"mode": "api_key_manual"}))

    def test_legacy_mode_labelled_legacy(self):
        self.assertIn("legacy", TraderPanel._seraph_status_text({"mode": "api_key_legacy"}))

    def test_none_mode_asks_for_sign_in(self):
        self.assertIn("not connected to Seraph", TraderPanel._seraph_status_text({"mode": "none"}))

    def test_needs_login_wins_over_mode(self):
        text = TraderPanel._seraph_status_text({"mode": "api_key_oauth", "needs_login": True})
        self.assertIn("session expired", text)
        self.assertNotIn("signed in", text)

    def test_unauthorized_appends_key_rejected(self):
        text = TraderPanel._seraph_status_text({"mode": "api_key_oauth", "last_error": "unauthorized"})
        self.assertTrue(text.endswith("— key rejected"))

    def test_never_contains_a_full_api_key(self):
        text = TraderPanel._seraph_status_text({
            "mode": "api_key_oauth", "api_key_prefix": "mcfw_3fa9c1e",
        })
        self.assertNotIn("mcfw_3fa9c1e0000000000deadbeef", text)
        self.assertNotRegex(text, r"[A-Za-z0-9]{40,}")


class SeraphLoginErrorTextTests(unittest.TestCase):
    def test_every_documented_error_code_has_a_message(self):
        default = TraderPanel._seraph_login_error_text(None)
        codes = (
            "access_denied", "timeout", "cancelled", "state_mismatch", "invalid_client",
            "registration_failed", "metadata_error", "token_exchange_failed",
            "api_key_mint_failed", "insufficient_scope", "network_error", "browser_open_failed",
        )
        for code in codes:
            with self.subTest(code=code):
                text = TraderPanel._seraph_login_error_text(code)
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip())
                self.assertNotEqual(text, default)
                self.assertNotEqual(text, code)

    def test_unknown_code_falls_back_to_a_generic_message(self):
        text = TraderPanel._seraph_login_error_text("totally_unknown")
        self.assertEqual(text, "Sign-in failed. Try again.")
        self.assertEqual(text, TraderPanel._seraph_login_error_text(None))
        self.assertTrue(text.strip())

    def test_description_is_appended_when_present(self):
        text = TraderPanel._seraph_login_error_text("timeout", "took too long")
        self.assertIn("took too long", text)

    def test_none_error_still_returns_text(self):
        text = TraderPanel._seraph_login_error_text(None, None)
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip())


class WalletBlockTextTests(unittest.TestCase):
    address = "0xAb12000000000000000000000000000000009f3E"
    external = "0xCd34000000000000000000000000000000005678"

    def test_no_wallet_asks_for_login(self):
        result = TraderPanel._wallet_block_text({})
        self.assertIn("não disponível", result["embedded_line"])
        self.assertIsNone(result["external_line"])
        self.assertIs(result["live_allowed"], False)

    def test_signer_granted_allows_live(self):
        result = TraderPanel._wallet_block_text({"address": self.address, "signerGranted": True})
        self.assertIs(result["live_allowed"], True)
        self.assertIn("autorizado", result["signer_line"])

    def test_signer_missing_blocks_live(self):
        result = TraderPanel._wallet_block_text({"address": self.address, "signerGranted": False})
        self.assertIs(result["live_allowed"], False)
        self.assertIn("não autorizado", result["signer_line"])

    def test_signer_granted_without_address_blocks_live(self):
        result = TraderPanel._wallet_block_text({"signerGranted": True, "address": None})
        self.assertIs(result["live_allowed"], False)

    def test_addresses_are_abbreviated_in_the_display_lines(self):
        result = TraderPanel._wallet_block_text({
            "address": self.address, "linkedExternalAddress": self.external,
        })
        for key, address in (("embedded_line", self.address), ("external_line", self.external)):
            with self.subTest(key=key):
                self.assertIn("…", result[key])
                self.assertNotIn(address, result[key])

    def test_copy_contains_both_addresses_and_the_word_nao_when_external_exists(self):
        result = TraderPanel._wallet_block_text({
            "address": self.address, "linkedExternalAddress": self.external,
        })
        self.assertIn(self.address, result["copy"])
        self.assertIn(self.external, result["copy"])
        self.assertRegex(result["copy"], r"\bnão\b")

    def test_copy_without_external_mentions_only_the_seraph_wallet(self):
        result = TraderPanel._wallet_block_text({"address": self.address})
        self.assertIn(self.address, result["copy"])
        self.assertNotIn("externa", result["copy"])
        self.assertIsNone(result["external_line"])

    def test_returned_keys_are_exactly_the_contract(self):
        self.assertEqual(set(TraderPanel._wallet_block_text({})), {
            "embedded_line", "signer_line", "external_line", "copy", "live_allowed",
        })


class AbbreviateAddressTests(unittest.TestCase):
    def test_abbreviates_a_full_address(self):
        text = TraderPanel._abbreviate_address("0xAb12000000000000000000000000000000009f3E")
        self.assertTrue(text.startswith("0xAb12"))
        self.assertTrue(text.endswith("9f3E"))
        self.assertIn("…", text)

    def test_none_and_short_values_return_em_dash(self):
        for address in (None, "", "0xAb12"):
            with self.subTest(address=address):
                self.assertEqual(TraderPanel._abbreviate_address(address), "—")


class WalletStatusProviderTests(unittest.TestCase):
    def _provider_result(self, cache):
        class _Fake:
            # An empty cache refreshes even with a recent timestamp. Use the
            # real refresh method, but do not execute its background work.
            _refresh_wallet_block = TraderPanel._refresh_wallet_block

        fake = _Fake()
        fake._wallet_cache = cache
        fake._wallet_cache_at = time.time()
        fake._background = lambda *a, **k: None
        return TraderPanel._wallet_status_provider(fake)

    def test_empty_cache_reports_disconnected(self):
        self.assertEqual(self._provider_result({}), {
            "connected": False, "address": None, "signerGranted": False,
        })

    def test_exposes_only_the_embedded_address_to_the_engine(self):
        embedded = "0xAb12000000000000000000000000000000009f3E"
        external = "0xCd34000000000000000000000000000000005678"
        result = self._provider_result({
            "address": embedded, "signerGranted": True, "linkedExternalAddress": external,
        })
        self.assertEqual(set(result), {"connected", "address", "signerGranted"})
        self.assertEqual(result["address"], embedded)
        self.assertIs(result["connected"], True)
        self.assertIs(result["signerGranted"], True)
        self.assertNotIn(external, result.values())

    def test_signer_granted_without_address_is_not_connected(self):
        result = self._provider_result({"signerGranted": True, "address": None})
        self.assertIs(result["connected"], False)


class PanelSourceInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent.parent / "trader_panel.py").read_text(encoding="utf-8")

    def test_no_sign_out_or_skip_strings(self):
        for text in ("Sign out", "Skip for now", "I OWN THIS RISK"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.source)

    def test_no_local_wallet_import(self):
        for text in ("trader.wallet", "local_wallet"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.source)

    def test_no_private_key_ui_strings(self):
        for text in ("recovery phrase", "private key", "mnemonic"):
            with self.subTest(text=text):
                self.assertNotIn(text, self.source.lower())

    def test_disconnect_button_label_present(self):
        self.assertIn("DESCONECTAR ESTE DISPOSITIVO", self.source)


if __name__ == "__main__":
    unittest.main()
