"""trader/live.py — check_buy_receipt's three outcomes (pending/reverted/
confirmed) and live_buy's translation of a _wait_for_receipt timeout into
BuyPendingError. Covers the root cause behind a real FORCE BUY that
confirmed on-chain but was reported to the user as failed, with the trade
never recorded in the ledger — see trader/engine.py's
_reconcile_pending_live_buys for the other half. No real RPC/network calls."""

import unittest
from unittest.mock import patch
from hexbytes import HexBytes
from web3 import Web3

from trader import live as live_mod

TOKEN_ADDRESS = "0x1111111111111111111111111111111111111111"
OWNER_ADDRESS = "0x2222222222222222222222222222222222222222"
OTHER_ADDRESS = "0x3333333333333333333333333333333333333333"
TX_HASH = "0x" + "ab" * 32
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)")


def _transfer_log(token_address: str, from_addr: str, to_addr: str, value: int) -> dict:
    return {
        "address": Web3.to_checksum_address(token_address),
        "topics": [
            TRANSFER_TOPIC,
            HexBytes(int(from_addr, 16).to_bytes(32, "big")),
            HexBytes(int(to_addr, 16).to_bytes(32, "big")),
        ],
        "data": HexBytes(value.to_bytes(32, "big")),
        "blockNumber": 1,
        "transactionHash": HexBytes(TX_HASH),
        "transactionIndex": 0,
        "blockHash": HexBytes("0x" + "22" * 32),
        "logIndex": 0,
        "removed": False,
    }


class CheckBuyReceiptTests(unittest.TestCase):
    def test_no_receipt_yet_is_pending(self):
        with patch.object(live_mod, "_get_receipt_or_none", return_value=None):
            result = live_mod.check_buy_receipt("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 10.0, 3000.0)
        self.assertEqual(result, {"status": "pending"})

    def test_reverted_status_zero(self):
        receipt = {"status": 0, "gasUsed": 21000, "gasPrice": 1_000_000_000, "logs": []}
        with patch.object(live_mod, "_get_receipt_or_none", return_value=receipt):
            result = live_mod.check_buy_receipt("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 10.0, 3000.0)
        self.assertEqual(result, {"status": "reverted"})

    def test_success_but_nothing_landed_in_our_wallet_is_treated_as_reverted(self):
        # Swap succeeded on-chain but the Transfer log's recipient isn't
        # our wallet (e.g. it went to a different address) — no position
        # should be opened for tokens we never actually received.
        receipt = {
            "status": 1, "gasUsed": 21000, "gasPrice": 1_000_000_000,
            "logs": [_transfer_log(TOKEN_ADDRESS, OTHER_ADDRESS, OTHER_ADDRESS, 500 * 10**18)],
        }
        with patch.object(live_mod, "_get_receipt_or_none", return_value=receipt):
            result = live_mod.check_buy_receipt("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 10.0, 3000.0)
        self.assertEqual(result, {"status": "reverted"})

    def test_confirmed_decodes_exact_qty_from_transfer_log(self):
        qty_wei = 23362 * 10**18  # matches the real STOCKER buy's ~23,362 tokens
        receipt = {
            "status": 1, "gasUsed": 118000, "effectiveGasPrice": 236_112_178,  # 0.236112178 Gwei, the real observed rate
            "logs": [_transfer_log(TOKEN_ADDRESS, OTHER_ADDRESS, OWNER_ADDRESS, qty_wei)],
        }
        with patch.object(live_mod, "_get_receipt_or_none", return_value=receipt), \
             patch.object(live_mod, "_with_rpc", return_value=18):
            result = live_mod.check_buy_receipt("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 7.87, 3000.0)
        self.assertEqual(result["status"], "confirmed")
        self.assertAlmostEqual(result["qty"], 23362.0, places=6)
        self.assertGreater(result["costUsd"], 7.87)  # trade size plus gas
        self.assertEqual(result["txHash"], TX_HASH)
        self.assertAlmostEqual(result["priceUsd"], result["costUsd"] / result["qty"], places=9)


class LiveBuyPendingTranslationTests(unittest.TestCase):
    """live_buy() must turn a plain '_wait_for_receipt timed out' RuntimeError
    into a BuyPendingError carrying the tx hash and trade context — that's
    what lets engine.py track it instead of the trade silently vanishing."""

    def test_timeout_becomes_buy_pending_error_with_context(self):
        token = {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS}
        with patch.object(live_mod.wallet, "status", return_value={"connected": True, "address": OWNER_ADDRESS}), \
             patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "best_quote", return_value={"amountOut": 23362 * 10**18, "dex": "v2"}), \
             patch.object(live_mod.wallet, "send_transaction", return_value=TX_HASH), \
             patch.object(live_mod, "_wait_for_receipt",
                          side_effect=RuntimeError(f"timed out waiting for confirmation (still pending): {TX_HASH}")):
            with self.assertRaises(live_mod.BuyPendingError) as ctx:
                live_mod.live_buy(token=token, trade_size_usd=7.87, bypass_gate=True)
        err = ctx.exception
        self.assertEqual(err.tx_hash, TX_HASH)
        self.assertEqual(err.token_address, TOKEN_ADDRESS)
        self.assertEqual(err.trade_size_usd, 7.87)
        self.assertEqual(err.eth_price_usd, 3000.0)

    def test_revert_is_not_reclassified_as_pending(self):
        # A genuine on-chain revert is a real, final failure — must NOT be
        # swallowed into the pending-tracking path.
        token = {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS}
        with patch.object(live_mod.wallet, "status", return_value={"connected": True, "address": OWNER_ADDRESS}), \
             patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "best_quote", return_value={"amountOut": 23362 * 10**18, "dex": "v2"}), \
             patch.object(live_mod.wallet, "send_transaction", return_value=TX_HASH), \
             patch.object(live_mod, "_wait_for_receipt",
                          side_effect=RuntimeError(f"transaction reverted on-chain: {TX_HASH}")):
            with self.assertRaises(RuntimeError) as ctx:
                live_mod.live_buy(token=token, trade_size_usd=7.87, bypass_gate=True)
        self.assertNotIsInstance(ctx.exception, live_mod.BuyPendingError)
        self.assertIn("reverted on-chain", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
