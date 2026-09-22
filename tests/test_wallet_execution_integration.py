"""Real live guardian execution against local HTTPS, OAuth and P-256 signing."""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_seraph_as import (
    CAP_TX_WEI, FAKE_CHAINS, FakeSeraph, SELECTOR_APPROVE,
    SELECTOR_V3_EXACT_INPUT_SINGLE, SELECTOR_WETH_WITHDRAW,
)

from core import settings_store
from trader import live as live_mod
from trader.mcp_client import McpClient
from trader.seraph_auth import SeraphAuth


class WalletExecutionIntegrationTests(unittest.TestCase):
    ADDRESS = "0x" + "11" * 20
    TOKEN = "0x" + "22" * 20
    CHAIN_ID = 8453

    def setUp(self):
        self._env_snapshot = dict(os.environ)
        self.addCleanup(self._restore_environment)
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        settings_patch = patch.object(
            settings_store, "SETTINGS_PATH", Path(self.tmpdir.name) / "settings.json"
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.old_provider = live_mod._wallet_status_provider
        self.old_mcp_call = live_mod._mcp_call
        self.addCleanup(self._restore_live)
        self.fake = FakeSeraph()
        self.privy_bodies = []
        self.mcp_calls = []

        # Observe wire payloads without replacing the executor, transport or
        # signature verifier. The original HTTP handlers still process them.
        @self.fake.app.middleware("http")
        async def observe_wire(request, call_next):
            if request.method == "POST":
                if request.url.path.startswith("/v1/wallets/"):
                    self.privy_bodies.append(json.loads(await request.body()))
                elif request.url.path == "/mcp":
                    self.mcp_calls.append(json.loads(await request.body()))
            return await call_next(request)

        self.fake.start()
        self.addCleanup(self.fake.stop)  # Also covers a failed setUp.
        os.environ["REQUESTS_CA_BUNDLE"] = self.fake.ca_bundle
        os.environ.pop("CURL_CA_BUNDLE", None)
        self.session = self.fake.client_session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)
        self.auth = SeraphAuth(
            issuer=self.fake.base_url,
            resource=self.fake.base_url + "/api",
            api_keys_url=self.fake.base_url + "/api/desktop/api-keys",
            session=self.session,
            opener=lambda url: bool(self.session.get(url, timeout=10, allow_redirects=True)),
        )
        self.assertTrue(self.auth.login(timeout_s=20).ok)
        self.assertIn("wallet:execute", self.auth.status().api_key_scopes)
        self.assertTrue(self.auth.get_api_key().startswith("mcfw_"))
        self.client = McpClient(url=self.fake.mcp_url, api_key=self.auth.get_api_key())
        live_mod.init(lambda name, args: self.client.call(name, args))
        status = self._control("grant_signer", {"address": self.ADDRESS})
        live_mod.set_wallet_status_provider(lambda: {"connected": True, **status})
        self.chain = FAKE_CHAINS[self.CHAIN_ID]

    def tearDown(self):
        try:
            self._restore_live()
        finally:
            self.fake.stop()

    def _restore_live(self):
        live_mod.set_wallet_status_provider(self.old_provider)
        live_mod.init(self.old_mcp_call)

    def _restore_environment(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def _control(self, name, payload=None):
        response = self.session.post(
            self.fake.base_url + "/_test/" + name,
            json={} if payload is None else payload, timeout=10,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _word(value):
        return f"{int(value, 16) if isinstance(value, str) else value:064x}"

    def _swap(self, value=10**15, *, sell=False):
        token_in, token_out = (self.TOKEN, self.chain["weth"]) if sell else (
            self.chain["weth"], self.TOKEN
        )
        words = (token_in, token_out, 3000, self.ADDRESS, value, 1, 0)
        return {"to": self.chain["v3"], "value": "0" if sell else str(value),
                "data": SELECTOR_V3_EXACT_INPUT_SINGLE + "".join(map(self._word, words))}

    def _approve(self):
        # WETH is an ERC20 accepted by the fake Privy's destination policy.
        return {"to": self.chain["weth"], "value": "0",
                "data": SELECTOR_APPROVE + self._word(self.chain["v3"]) + self._word(10**15)}

    def _execute(self, tx=None, kind="swap", gate=None):
        result = live_mod._execute_via_guardian(
            "base", self._swap() if tx is None else tx, kind=kind, gate=gate
        )
        self.assertRegex(result, r"^0x[0-9a-fA-F]{64}$")
        return result

    def _tool(self, name, args):
        result = self.client.call(name, args)
        self.assertTrue(result["ok"], result)
        return json.loads(result["text"])

    def _gate(self):
        return live_mod.require_allow("base", self._swap(), self.ADDRESS)

    def test_wallet_status_reports_granted_signer(self):
        status = self._tool("guardian_wallet_status", {})
        self.assertEqual(status["address"], self.ADDRESS)
        self.assertIs(status["signerGranted"], True)

    def test_buy_v3_base_passes_gate_executor_and_privy(self):
        self._execute()
        stats = self.fake.stats()
        self.assertGreaterEqual(stats["gates"], 1)
        self.assertEqual(stats["executes"], 1)
        self.assertEqual(stats["privy_calls"], 1)

    def test_privy_receives_exact_recorded_gate_payload(self):
        tx_hash = self._execute()
        gates = self.fake.gates()
        self.assertEqual(len(gates), 1)
        rid, gate = next(iter(gates.items()))
        record = self.fake.executions()[rid]
        self.assertEqual(record["requestId"], rid)
        self.assertEqual(record["txHash"], tx_hash)
        self.assertEqual(len(self.privy_bodies), 1)
        body = self.privy_bodies[0]
        self.assertEqual(body["reference_id"], rid)
        self.assertEqual(body["params"]["transaction"], record["tx"])
        self.assertEqual(record["tx"], {
            "to": gate["to"], "from": gate["from"], "data": gate["callData"],
            "value": hex(int(gate["valueWei"])), "chain_id": gate["chainId"],
        })
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_approve_chain_router_executes(self):
        self._execute(self._approve(), kind="approve")
        self.assertEqual(next(iter(self.fake.gates().values()))["kind"], "approve")
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_withdraw_chain_weth_executes(self):
        self._execute({"to": self.chain["weth"], "value": "0",
                       "data": SELECTOR_WETH_WITHDRAW + self._word(10**15)}, kind="withdraw")
        self.assertEqual(next(iter(self.fake.gates().values()))["kind"], "withdraw")
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_blocked_gate_never_reaches_executor(self):
        self._control("set_gate_decision", {"decision": "block"})
        with self.assertRaisesRegex(RuntimeError, "BLOCK"):
            self._execute()
        self.assertEqual(self.fake.stats()["executes"], 0)

    def test_revoked_signer_is_refused(self):
        self._control("revoke_signer")
        with self.assertRaisesRegex(RuntimeError, "signer_not_granted"):
            self._execute()
        self.assertEqual(self.fake.stats()["privy_calls"], 0)

    def test_missing_wallet_scope_returns_403(self):
        def no_wallet_consent(url):
            parsed = urlsplit(url)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query.append(("_no_wallet_consent", "1"))
            return bool(self.session.get(
                urlunsplit(parsed._replace(query=urlencode(query))),
                timeout=10, allow_redirects=True,
            ))

        self.auth.opener = no_wallet_consent
        self.assertTrue(self.auth.login(timeout_s=20).ok)
        self.assertNotIn("wallet:execute", self.auth.status().api_key_scopes)
        self.client = McpClient(url=self.fake.mcp_url, api_key=self.auth.get_api_key())
        with self.assertRaisesRegex(RuntimeError, "403"):
            self._execute()
        self.assertEqual(self.fake.stats()["privy_calls"], 0)

    def test_transaction_cap_is_enforced(self):
        with self.assertRaisesRegex(RuntimeError, "cap_tx_exceeded"):
            self._execute(self._swap(CAP_TX_WEI + 1))
        self.assertEqual(self.fake.stats()["privy_calls"], 0)

    def test_twenty_executions_then_daily_cap(self):
        hashes = {self._execute() for _ in range(20)}
        self.assertEqual(len(hashes), 20)
        with self.assertRaisesRegex(RuntimeError, "cap_day_exceeded"):
            self._execute()
        self.assertEqual(self.fake.stats()["privy_calls"], 20)
        response = self.session.get(self.fake.base_url + "/_test/daily_spend",
                                    params={"address": self.ADDRESS}, timeout=10)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"spentWei": str(20 * 10**15), "txCount": 20})

    def test_request_id_replay_is_idempotent(self):
        self._gate()
        rid = next(iter(self.fake.gates()))
        first = self._tool("guardian_execute", {"requestId": rid})
        self.assertTrue(first["ok"])
        calls = self.fake.stats()["privy_calls"]
        second = self._tool("guardian_execute", {"requestId": rid})
        self.assertTrue(second["ok"])
        self.assertEqual(first["txHash"], second["txHash"])
        self.assertEqual(calls, 1)
        self.assertEqual(self.fake.stats()["privy_calls"], calls)

    def test_pending_execution_resumes_without_duplicate_privy_call(self):
        gate = self._gate()
        self._control("privy_delay", {"ms": 1000})
        first = self._tool("guardian_execute", {"requestId": gate["requestId"]})
        self.assertIs(first["ok"], False)
        self.assertEqual(first["error"], "execution_pending")
        self.assertEqual(self.fake.stats()["privy_calls"], 0)
        reserved = self.fake.executions()[gate["requestId"]]
        self._control("privy_delay", {"ms": 0})
        self._execute(gate=gate)
        record = self.fake.executions()[gate["requestId"]]
        self.assertEqual(record["idempotency_key"], reserved["idempotency_key"])
        self.assertEqual(record["status"], "submitted")
        self.assertEqual(len(self.fake.executions()), 1)
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_sell_approve_and_swap_use_distinct_gates(self):
        self._execute(self._approve(), kind="approve")
        self._execute(self._swap(sell=True))
        gates = self.fake.gates()
        self.assertEqual(len(gates), 2)
        self.assertEqual({gate["kind"] for gate in gates.values()}, {"approve", "swap"})
        self.assertEqual(set(gates), set(self.fake.executions()))
        self.assertEqual(self.fake.stats()["privy_calls"], 2)

    def test_expired_gate_is_refused(self):
        gate = self._gate()
        stored = self.fake.gates()[gate["requestId"]]
        past = int(time.time() * 1000) - 10000
        response = self.session.post(
            self.fake.base_url + "/api/internal/wallet/gate",
            headers={"X-Internal-Secret": "fake-internal-secret"}, timeout=10,
            json={"requestId": stored["requestId"], "orgId": stored["orgId"],
                  "userId": stored["userId"], "kind": stored["kind"],
                  "decision": "allow", "decidedAt": past - 1000, "expiresAt": past,
                  "payload": {"chainId": stored["chainId"], "to": stored["to"],
                              "from": stored["from"], "callData": stored["callData"],
                              "value": stored["valueWei"]}},
        )
        self.assertEqual(response.status_code, 204, response.text)
        with self.assertRaisesRegex(RuntimeError, "gate_expired"):
            self._execute(gate=gate)
        self.assertEqual(self.fake.stats()["privy_calls"], 0)

    def test_executor_ignores_transaction_fields_in_tool_input(self):
        gate = self._gate()
        result = self._tool("guardian_execute", {
            "requestId": gate["requestId"], "to": "0xdead" + "00" * 18,
            "value": "999", "data": "0xdeadbeef", "chainId": 1,
        })
        self.assertTrue(result["ok"])
        self.assertEqual(self._execute(gate=gate), result["txHash"])
        tx = self.privy_bodies[0]["params"]["transaction"]
        self.assertEqual(tx["to"], self.chain["v3"])
        self.assertEqual(tx["value"], hex(10**15))
        self.assertEqual(tx["data"], self._swap()["data"])
        self.assertEqual(tx["chain_id"], self.CHAIN_ID)
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_from_address_must_match_granted_wallet(self):
        live_mod.set_wallet_status_provider(lambda: {
            "connected": True, "address": self.TOKEN, "signerGranted": True,
        })
        with self.assertRaisesRegex(RuntimeError, "wallet_mismatch"):
            self._execute()
        self.assertEqual(self.fake.stats()["privy_calls"], 0)

    def test_pending_pretrade_is_polled_before_execution(self):
        self._control("pretrade_pending", {"enabled": True})
        self._execute()
        tools = [call["params"]["name"] for call in self.mcp_calls
                 if call.get("method") == "tools/call"]
        self.assertEqual(tools, ["guardian_pretrade_check", "guardian_pretrade_result",
                                 "guardian_execute"])
        self.assertEqual(next(iter(self.fake.gates().values()))["decision"], "allow")
        self.assertEqual(self.fake.stats()["privy_calls"], 1)

    def test_chain_id_stays_integer_through_privy_caip2(self):
        self._execute()
        gate = next(iter(self.fake.gates().values()))
        # Gates name this field chainId; the signed transaction uses chain_id.
        self.assertIs(type(gate["chainId"]), int)
        self.assertEqual(gate["chainId"], self.CHAIN_ID)
        body = self.privy_bodies[0]
        self.assertIs(type(body["params"]["transaction"]["chain_id"]), int)
        self.assertEqual(body["params"]["transaction"]["chain_id"], gate["chainId"])
        self.assertEqual(body["caip2"], f"eip155:{self.CHAIN_ID}")


if __name__ == "__main__":
    unittest.main()
