"""trader/wallet/local_wallet.py — send_transaction's gas price buffer.
A real LIVE FORCE BUY confirmed at an unusually low 0.236 Gwei (the RPC's
bare eth_gasPrice quote, unbuffered) but slowly enough to blow past
_wait_for_receipt's timeout and get reported as failed — see
tests/test_trader_live.py and tests/test_trader_engine.py for the other
half of that fix. No real RPC/network calls, no DPAPI: the module-level
wallet is set directly to a throwaway in-memory keypair."""

import unittest
from unittest.mock import MagicMock, patch

from eth_account import Account

from trader.wallet import local_wallet

RECIPIENT = "0x1111111111111111111111111111111111111111"


class GasPriceBufferTests(unittest.TestCase):
    def setUp(self):
        self._prev_wallet = local_wallet._wallet
        local_wallet._wallet = Account.create()

    def tearDown(self):
        local_wallet._wallet = self._prev_wallet

    def test_send_transaction_buffers_the_rpcs_gas_price_quote(self):
        fake_w3 = MagicMock()
        fake_w3.eth.gas_price = 236_112_178  # wei — the real observed rate that confirmed too slowly
        fake_w3.eth.get_transaction_count.return_value = 5
        fake_w3.eth.estimate_gas.return_value = 118000
        fake_w3.eth.send_raw_transaction.return_value = b"\xab" * 32

        captured = {}

        def fake_sign_transaction(tx, key):
            captured["tx"] = dict(tx)
            signed = MagicMock()
            signed.raw_transaction = b"\xcd" * 10
            return signed

        with patch.object(local_wallet, "_with_rpc", lambda chain, fn: fn(fake_w3)), \
             patch.object(local_wallet.Account, "sign_transaction", side_effect=fake_sign_transaction):
            local_wallet.send_transaction(RECIPIENT, "0xdead", 0, "ethereum")

        expected = 236_112_178 * (100 + local_wallet.GAS_PRICE_BUFFER_PCT) // 100
        self.assertEqual(captured["tx"]["gasPrice"], expected)
        self.assertGreater(captured["tx"]["gasPrice"], fake_w3.eth.gas_price)

    def test_buffer_is_a_meaningful_percentage_not_a_no_op(self):
        self.assertGreaterEqual(local_wallet.GAS_PRICE_BUFFER_PCT, 10)

    def test_on_gas_quote_callback_receives_both_prices(self):
        # GEMZ4US, Section F (2026-09-17): the exact margin figure wasn't
        # independently checkable from anything the app exposed.
        fake_w3 = MagicMock()
        fake_w3.eth.gas_price = 236_112_178
        fake_w3.eth.get_transaction_count.return_value = 5
        fake_w3.eth.estimate_gas.return_value = 118000
        fake_w3.eth.send_raw_transaction.return_value = b"\xab" * 32

        def fake_sign_transaction(tx, key):
            signed = MagicMock()
            signed.raw_transaction = b"\xcd" * 10
            return signed

        seen = {}
        with patch.object(local_wallet, "_with_rpc", lambda chain, fn: fn(fake_w3)), \
             patch.object(local_wallet.Account, "sign_transaction", side_effect=fake_sign_transaction):
            local_wallet.send_transaction(RECIPIENT, "0xdead", 0, "ethereum",
                                           on_gas_quote=lambda q, b: seen.update(quoted=q, buffered=b))

        self.assertEqual(seen["quoted"], 236_112_178)
        self.assertEqual(seen["buffered"], 236_112_178 * 130 // 100)

    def test_no_callback_is_a_no_op(self):
        # Default call shape (every existing caller) must still work unchanged.
        fake_w3 = MagicMock()
        fake_w3.eth.gas_price = 1_000_000_000
        fake_w3.eth.get_transaction_count.return_value = 5
        fake_w3.eth.estimate_gas.return_value = 21000
        fake_w3.eth.send_raw_transaction.return_value = b"\xab" * 32
        with patch.object(local_wallet, "_with_rpc", lambda chain, fn: fn(fake_w3)), \
             patch.object(local_wallet.Account, "sign_transaction",
                          side_effect=lambda tx, key: MagicMock(raw_transaction=b"\xcd" * 10)):
            result = local_wallet.send_transaction(RECIPIENT, "0xdead", 0, "ethereum")
        self.assertTrue(result.startswith("0x"))


if __name__ == "__main__":
    unittest.main()
