"""trader/live.py — check_buy_receipt's three outcomes (pending/reverted/
confirmed) and live_buy's translation of a _wait_for_receipt timeout into
BuyPendingError. Covers the root cause behind a real FORCE BUY that
confirmed on-chain but was reported to the user as failed, with the trade
never recorded in the ledger — see trader/engine.py's
_reconcile_pending_live_buys for the other half. No real RPC/network calls."""

import json
import unittest
from unittest.mock import MagicMock, patch
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


class _FakeMcp:
    """Wire-shaped Seraph responses; unexpected tools must never reach a network."""

    def __init__(self, verdict="allow", request_ids=None, execute_responses=None,
                 pending_gate=False, execute_error=None):
        self.calls = []
        self.verdict = verdict
        self.request_ids = iter(request_ids or ["request-1", "request-2", "request-3"])
        self.execute_responses = list(execute_responses or [{"ok": True, "txHash": TX_HASH}])
        self.pending_gate = pending_gate
        self.execute_error = execute_error

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "guardian_pretrade_check":
            request_id = next(self.request_ids)
            payload = {"decision": self.verdict, "requestId": request_id}
            if self.pending_gate:
                payload = {"status": "pending", "requestId": request_id, "retryAfterMs": 1}
        elif name == "guardian_pretrade_result":
            payload = {"decision": self.verdict, "requestId": args["requestId"]}
        elif name == "guardian_execute":
            if self.execute_error:
                return {"ok": False, "error": self.execute_error}
            payload = self.execute_responses[0]
            if len(self.execute_responses) > 1:
                self.execute_responses.pop(0)
        else:
            raise AssertionError(f"unexpected MCP tool: {name}")
        return {"ok": True, "text": json.dumps(payload)}


class _WalletStatusTests(unittest.TestCase):
    def setUp(self):
        self.previous_provider = live_mod._wallet_status_provider
        live_mod.set_wallet_status_provider(lambda: {
            "connected": True, "address": OWNER_ADDRESS, "signerGranted": True,
        })

    def tearDown(self):
        # Authorization belongs to each test, never to the next test's wallet.
        live_mod.set_wallet_status_provider(self.previous_provider)


class LiveBuyPendingTranslationTests(_WalletStatusTests):
    """live_buy() must turn a plain '_wait_for_receipt timed out' RuntimeError
    into a BuyPendingError carrying the tx hash and trade context — that's
    what lets engine.py track it instead of the trade silently vanishing."""

    def test_timeout_becomes_buy_pending_error_with_context(self):
        token = {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS}
        with patch.object(live_mod, "_mcp_call", _FakeMcp()), \
             patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "best_quote", return_value={"amountOut": 23362 * 10**18, "dex": "v2"}), \
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
        with patch.object(live_mod, "_mcp_call", _FakeMcp()), \
             patch.object(live_mod, "eth_usd_price", return_value=3000.0), \
             patch.object(live_mod, "best_quote", return_value={"amountOut": 23362 * 10**18, "dex": "v2"}), \
             patch.object(live_mod, "_wait_for_receipt",
                          side_effect=RuntimeError(f"transaction reverted on-chain: {TX_HASH}")):
            with self.assertRaises(RuntimeError) as ctx:
                live_mod.live_buy(token=token, trade_size_usd=7.87, bypass_gate=True)
        self.assertNotIsInstance(ctx.exception, live_mod.BuyPendingError)
        self.assertIn("reverted on-chain", str(ctx.exception))


class ExecuteViaGuardianTests(_WalletStatusTests):
    def setUp(self):
        super().setUp()
        self.fake = _FakeMcp()
        self.token = {"symbol": "STOCKER", "chain": "ethereum", "address": TOKEN_ADDRESS}
        self.tx = {"to": TOKEN_ADDRESS, "data": "0x1234", "value": 123}
        self.w3 = MagicMock()
        functions = self.w3.eth.contract.return_value.functions
        functions.decimals.return_value.call.return_value = 18
        functions.balanceOf.return_value.call.return_value = 10**18
        functions.allowance.return_value.call.return_value = 0
        self.enterContext(patch.object(live_mod, "_mcp_call", self.fake))
        self.enterContext(patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(self.w3)))
        self.enterContext(patch.object(live_mod, "eth_usd_price", return_value=3000.0))
        self.enterContext(patch.object(live_mod, "best_quote", return_value={
            "amountOut": 10**18, "dex": "v3", "fee": 3000, "gasEstimate": 170000,
        }))
        self.sleep = self.enterContext(patch.object(live_mod.time, "sleep"))
        self.receipt = {"status": 1, "gasUsed": 21000, "gasPrice": 1_000_000_000}
        self.enterContext(patch.object(live_mod, "_get_receipt_or_none", side_effect=self._receipt))

    def _receipt(self, chain, tx_hash):
        self.fake.calls.append(("receipt", {"txHash": tx_hash}))
        return self.receipt

    def _execute(self):
        return live_mod._execute_via_guardian("ethereum", self.tx, kind="swap")

    def _calls(self, name):
        return [args for tool, args in self.fake.calls if tool == name]

    def _assert_single_gate_and_execute(self):
        # The signer must consume the exact authorization returned by the gate.
        self.assertEqual([name for name, _ in self.fake.calls],
                         ["guardian_pretrade_check", "guardian_execute", "receipt"])
        self.assertEqual(self._calls("guardian_execute"), [{"requestId": "request-1"}])

    def _sell(self, **kwargs):
        return live_mod.live_sell(dict(self.token, qty=1.0, costUsd=10.0), **kwargs)

    def test_approve_gates_then_executes_with_the_returned_request_id(self):
        live_mod._ensure_allowance("ethereum", TOKEN_ADDRESS, OWNER_ADDRESS, OTHER_ADDRESS, 10**18)
        self._assert_single_gate_and_execute()

    def test_buy_gates_then_executes_with_the_returned_request_id(self):
        result = live_mod.live_buy(self.token, 7.87)
        self.assertEqual(result["txHash"], TX_HASH)
        self._assert_single_gate_and_execute()

    def test_unwrap_gates_then_executes_with_the_returned_request_id(self):
        result = live_mod.unwrap_weth("ethereum", amount_wei=10**18)
        self.assertEqual(result["txHash"], TX_HASH)
        self._assert_single_gate_and_execute()

    def test_execute_never_receives_transaction_fields(self):
        # Sending calldata again would let the client replace the custodial payload.
        self.fake.request_ids = iter(f"request-{i}" for i in range(5))
        live_mod.live_buy(self.token, 7.87)
        live_mod.unwrap_weth("ethereum", amount_wei=10**18)
        self._sell(bypass_gate=True)
        executes = self._calls("guardian_execute")
        self.assertEqual(len(executes), 4)
        for args in executes:
            self.assertEqual(set(args), {"requestId"})
            self.assertTrue(set(args).isdisjoint({"to", "data", "value", "chainId", "from"}))

    def test_sell_with_bypass_takes_two_gates_and_two_executes_with_distinct_request_ids(self):
        # Force bypasses profit simulation, not custodial authorization of either tx.
        self._sell(bypass_gate=True)
        self.assertEqual(len(self._calls("guardian_pretrade_check")), 2)
        self.assertEqual(self._calls("guardian_execute"),
                         [{"requestId": "request-1"}, {"requestId": "request-2"}])
        self.assertEqual([name for name, _ in self.fake.calls], [
            "guardian_pretrade_check", "guardian_execute", "receipt",
            "guardian_pretrade_check", "guardian_execute", "receipt",
        ])
        self.assertEqual(self._calls("receipt")[0], {"txHash": TX_HASH})

    def test_sell_without_bypass_takes_three_gates_because_the_profit_check_gates_too(self):
        # The first gate feeds min-net-profit simulation (the "sell X force"
        # message), the second authorizes approve, and the third authorizes swap.
        # The swap gate is deliberately fresh: waiting for the approve receipt
        # can exceed the gate's 180 s window. Lock down the common path's real
        # cost so any future gate consolidation becomes an explicit change.
        self._sell()
        self.assertEqual(len(self._calls("guardian_pretrade_check")), 3)
        self.assertEqual(self._calls("guardian_execute"),
                         [{"requestId": "request-2"}, {"requestId": "request-3"}])
        self.assertEqual([name for name, _ in self.fake.calls], [
            "guardian_pretrade_check", "guardian_pretrade_check", "guardian_execute",
            "receipt", "guardian_pretrade_check", "guardian_execute", "receipt",
        ])

    def test_gate_block_raises_and_never_executes(self):
        # Anything short of allow must fail closed before requesting a signature.
        for verdict in ("block", "warn", "unknown"):
            with self.subTest(verdict=verdict):
                fake = _FakeMcp(verdict=verdict)
                with patch.object(live_mod, "_mcp_call", fake):
                    with self.assertRaises(RuntimeError):
                        self._execute()
                self.assertEqual([name for name, _ in fake.calls], ["guardian_pretrade_check"])

    def test_executor_error_code_surfaces_in_the_exception(self):
        # Custodial refusals must remain actionable, not masquerade as a broadcast.
        for code in ("signer_not_granted", "cap_tx_exceeded", "wallet_mismatch", "calldata_not_allowed"):
            with self.subTest(code=code):
                fake = _FakeMcp(execute_responses=[{"ok": False, "error": code, "message": "refused"}])
                with patch.object(live_mod, "_mcp_call", fake):
                    with self.assertRaisesRegex(RuntimeError, code):
                        self._execute()

    def test_execution_pending_is_retried_then_raises(self):
        # Retry only the same authorization: a new gate could authorize a second spend.
        self.fake.execute_responses = [{"ok": False, "error": "execution_pending", "message": "waiting"}]
        with self.assertRaisesRegex(RuntimeError, "execution_pending"):
            self._execute()
        self.assertEqual(self._calls("guardian_execute"),
                         [{"requestId": "request-1"}] * live_mod.EXECUTION_PENDING_RETRIES)
        self.assertEqual(len(self._calls("guardian_pretrade_check")), 1)
        self.assertEqual(self.sleep.call_count, live_mod.EXECUTION_PENDING_RETRIES - 1)

    def test_execution_pending_then_success_returns_the_hash(self):
        # A pending signer response is not a rejection or a new transaction.
        self.fake.execute_responses = [
            {"ok": False, "error": "execution_pending", "message": "waiting"},
            {"ok": True, "txHash": TX_HASH},
        ]
        self.assertEqual(self._execute(), TX_HASH)
        self.assertEqual(self._calls("guardian_execute"), [{"requestId": "request-1"}] * 2)
        self.sleep.assert_called_once_with(live_mod.EXECUTION_PENDING_SLEEP_S)

    def test_no_wallet_address_raises_before_any_mcp_call(self):
        # No authorized custodial wallet means no request may reach the signer.
        live_mod.set_wallet_status_provider(lambda: {
            "connected": False, "address": None, "signerGranted": False,
        })
        with self.assertRaisesRegex(RuntimeError, "wallet not authorized"):
            self._execute()
        self.assertEqual(self.fake.calls, [])

    def test_pending_gate_is_polled_then_executed(self):
        # Pending is not permission: wait for the decision on that same request.
        self.fake.pending_gate = True
        self.assertEqual(self._execute(), TX_HASH)
        self.assertEqual([name for name, _ in self.fake.calls],
                         ["guardian_pretrade_check", "guardian_pretrade_result", "guardian_execute"])
        self.assertEqual(self._calls("guardian_pretrade_result"), [{"requestId": "request-1"}])
        self.assertEqual(self._calls("guardian_execute"), [{"requestId": "request-1"}])
        self.sleep.assert_called_once_with(0.001)

    def test_revert_still_raises_after_execution(self):
        # A custodial broadcast hash does not prove on-chain success.
        self.receipt = {"status": 0}
        with self.assertRaisesRegex(RuntimeError, "reverted on-chain") as ctx:
            live_mod.live_buy(self.token, 7.87)
        self.assertNotIsInstance(ctx.exception, live_mod.BuyPendingError)
        self._assert_single_gate_and_execute()

    def test_chain_id_is_int_and_value_is_decimal_string_in_the_gate(self):
        # The authorization must bind an unambiguous chain and wei amount.
        self._execute()
        args, = self._calls("guardian_pretrade_check")
        self.assertIs(type(args["chainId"]), int)
        self.assertIsInstance(args["value"], str)
        self.assertRegex(args["value"], r"^[0-9]+$")
        self.assertEqual(args["value"], "123")

    def test_executor_unavailable_when_mcp_call_is_not_ok(self):
        # Transport failure cannot be reported as a successfully signed trade.
        self.fake.execute_error = "executor offline"
        with self.assertRaisesRegex(RuntimeError, "executor unavailable.*executor offline"):
            self._execute()
        self.assertEqual(self._calls("guardian_execute"), [{"requestId": "request-1"}])


class AdoptFromTxTests(unittest.TestCase):
    """live.adopt_from_tx — cost basis for engine.py's `adopt` command,
    covering GEMZ4US's real 16 sept. STOCKER fill that predated
    BuyPendingError and was never recorded anywhere. Unlike
    check_buy_receipt, there's no stored trade_size_usd to trust, so cost
    comes from the tx's own ETH value + gas, not a re-derived quote."""

    def _fake_w3(self, decimals=18, tx_value_wei=0):
        w3 = MagicMock()
        w3.eth.contract.return_value.functions.decimals.return_value.call.return_value = decimals
        w3.eth.get_transaction.return_value = {"value": tx_value_wei}
        return w3

    def test_no_receipt_returns_none(self):
        with patch.object(live_mod, "_require_contract"), \
             patch.object(live_mod, "_get_receipt_or_none", return_value=None):
            self.assertIsNone(live_mod.adopt_from_tx("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 3000.0))

    def test_reverted_returns_none(self):
        receipt = {"status": 0, "gasUsed": 21000, "gasPrice": 1_000_000_000, "logs": []}
        with patch.object(live_mod, "_require_contract"), \
             patch.object(live_mod, "_get_receipt_or_none", return_value=receipt):
            self.assertIsNone(live_mod.adopt_from_tx("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 3000.0))

    def test_nothing_delivered_to_wallet_returns_none(self):
        receipt = {
            "status": 1, "gasUsed": 21000, "gasPrice": 1_000_000_000,
            "logs": [_transfer_log(TOKEN_ADDRESS, OTHER_ADDRESS, OTHER_ADDRESS, 500 * 10**18)],
        }
        with patch.object(live_mod, "_require_contract"), \
             patch.object(live_mod, "_get_receipt_or_none", return_value=receipt):
            self.assertIsNone(live_mod.adopt_from_tx("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 3000.0))

    def test_confirmed_cost_basis_from_tx_value_plus_gas(self):
        # Mirrors the real STOCKER fill: 0.003203657 ETH for 23,362.65
        # tokens, 118000 gas at 0.236112178 Gwei.
        qty_wei = 23362 * 10**18
        tx_value_wei = 3_203_657_000_000_000  # 0.003203657 ETH
        receipt = {
            "status": 1, "gasUsed": 118000, "effectiveGasPrice": 236_112_178,
            "logs": [_transfer_log(TOKEN_ADDRESS, OTHER_ADDRESS, OWNER_ADDRESS, qty_wei)],
        }
        fake_w3 = self._fake_w3(decimals=18, tx_value_wei=tx_value_wei)
        with patch.object(live_mod, "_get_receipt_or_none", return_value=receipt), \
             patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            result = live_mod.adopt_from_tx("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 3000.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["qty"], 23362.0, places=6)
        expected_eth_usd = 0.003203657 * 3000.0
        self.assertAlmostEqual(result["costUsd"], expected_eth_usd + (118000 * 236_112_178 / 1e18) * 3000.0, places=6)
        self.assertEqual(result["txHash"], TX_HASH)
        self.assertAlmostEqual(result["priceUsd"], result["costUsd"] / result["qty"], places=9)

    def test_get_transaction_failure_returns_none(self):
        # Qty decoding (decimals lookup) succeeds; the later, separate
        # get_transaction call (for cost basis) fails — must still return
        # None cleanly, not raise.
        receipt = {
            "status": 1, "gasUsed": 21000, "gasPrice": 1_000_000_000,
            "logs": [_transfer_log(TOKEN_ADDRESS, OTHER_ADDRESS, OWNER_ADDRESS, 100 * 10**18)],
        }
        fake_w3 = MagicMock()
        fake_w3.eth.contract.return_value.functions.decimals.return_value.call.return_value = 18
        fake_w3.eth.get_transaction.side_effect = RuntimeError("all RPC endpoints failed")
        with patch.object(live_mod, "_get_receipt_or_none", return_value=receipt), \
             patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            self.assertIsNone(live_mod.adopt_from_tx("ethereum", TX_HASH, TOKEN_ADDRESS, OWNER_ADDRESS, 3000.0))


class RequireContractTests(unittest.TestCase):
    """live._require_contract — GEMZ4US, 2026-09-18 (Section A): adopt
    failed 3/3 times with web3's generic 'is contract deployed correctly
    and chain synced?' error. Confirmed against web3.py's own source
    (contract/utils.py) that this fires specifically when a call returns
    empty data AND the target has no contract bytecode — almost certainly
    a wallet address used where the token's contract address belongs."""

    def test_passes_silently_when_address_has_code(self):
        fake_w3 = MagicMock()
        fake_w3.eth.get_code.return_value = b"\x60\x80\x60\x40"  # some non-empty bytecode
        with patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            live_mod._require_contract("ethereum", TOKEN_ADDRESS, OWNER_ADDRESS)  # must not raise

    def test_no_code_matching_owner_gives_wallet_specific_message(self):
        fake_w3 = MagicMock()
        fake_w3.eth.get_code.return_value = b""
        with patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            with self.assertRaises(RuntimeError) as ctx:
                live_mod._require_contract("ethereum", OWNER_ADDRESS, OWNER_ADDRESS)
        self.assertIn("your wallet address, not a contract", str(ctx.exception))

    def test_no_code_not_matching_owner_gives_generic_message(self):
        fake_w3 = MagicMock()
        fake_w3.eth.get_code.return_value = b""
        with patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            with self.assertRaises(RuntimeError) as ctx:
                live_mod._require_contract("ethereum", TOKEN_ADDRESS, OWNER_ADDRESS)
        msg = str(ctx.exception)
        self.assertIn("has no contract code", msg)
        self.assertNotIn("your wallet address", msg)

    def test_no_code_without_owner_arg_gives_generic_message(self):
        fake_w3 = MagicMock()
        fake_w3.eth.get_code.return_value = b""
        with patch.object(live_mod, "_with_rpc", lambda chain, fn: fn(fake_w3)):
            with self.assertRaises(RuntimeError) as ctx:
                live_mod._require_contract("ethereum", TOKEN_ADDRESS)
        self.assertIn("has no contract code", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
