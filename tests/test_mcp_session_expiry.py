"""An expired MCP session (410, or 404 for a sent session id) must be
renewed once instead of leaving the trade gate fail-closed."""

import linecache
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_seraph_as import FakeSeraph

from core import settings_store
from trader import engine as engine_mod
from trader import live as live_mod
import trader.mcp_client as mc
from trader.mcp_client import McpClient
from trader.seraph_auth import SeraphAuth

EXPIRED = {"ok": False, "error": "seraph_session_expired"}


class McpSessionExpiryTests(unittest.TestCase):
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
        self.fake = FakeSeraph()
        self.fake.start()
        self.addCleanup(self.fake.stop)
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
        patcher = patch.object(mc, "get_default_auth", return_value=self.auth)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertIs(self.auth.login(timeout_s=20).ok, True)
        self.client = McpClient(url=self.fake.mcp_url, api_key=None,
                                key_provider=self.auth.get_api_key,
                                on_unauthorized=self.auth.remint_api_key)

    def _restore_environment(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def _control(self, name, payload=None):
        response = self.session.post(self.fake.base_url + "/_test/" + name,
                                     json={} if payload is None else payload, timeout=10)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _price(self):
        return self.client.call("crypto_get_price", {"asset": "ETH"})

    def _warm(self):
        self.assertIs(self._price()["ok"], True)
        self.assertIsNotNone(self.client._session)
        return self.fake.stats()

    def _delta(self, before, key):
        return self.fake.stats()[key] - before[key]

    def test_410_reinitializes_and_retries_once(self):
        before = self._warm()
        old_session = self.client._session
        self.assertEqual(self._control("expire_mcp_sessions"), {"expired": 1})
        self.assertIs(self._price()["ok"], True)
        self.assertEqual(self._delta(before, "mcp_410s"), 1)
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)
        self.assertNotEqual(self.client._session, old_session)

    def test_404_with_session_reinitializes_and_retries_once(self):
        before = self._warm()
        self._control("forget_mcp_sessions", {"sessionId": self.client._session})
        self.assertIs(self._price()["ok"], True)
        self.assertEqual(self._delta(before, "mcp_404s"), 1)
        self.assertEqual(self._delta(before, "mcp_410s"), 0)
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)

    def test_410_twice_returns_clear_error_without_looping(self):
        before = self._warm()
        self._control("expire_mcp_sessions")
        self._control("expire_next_mcp_sessions", {"count": 1})
        self.assertEqual(self._price(), EXPIRED)
        # tools/call 410, initialize, notification 410, tools/call 410: no more.
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)
        self.assertEqual(self._delta(before, "mcp_requests"), 4)
        self.assertEqual(self._delta(before, "mcp_410s"), 3)
        self.assertIsNone(self.client._session)
        # The next call starts a fresh session instead of staying fail-closed.
        self.assertIs(self._price()["ok"], True)
        self.assertEqual(self._delta(before, "mcp_initializes"), 2)

    def test_410_after_401_remint_gets_its_single_session_retry(self):
        before = self._warm()
        self._control("revoke_api_key", {"prefix": self.auth.status().api_key_prefix})
        self._control("expire_next_mcp_sessions", {"count": 1})
        self.assertIs(self._price()["ok"], True)
        self.assertEqual(self._delta(before, "mcp_401s"), 1)
        self.assertEqual(self._delta(before, "mints"), 1)
        self.assertEqual(self._delta(before, "mcp_initializes"), 2)
        self.assertEqual(self._delta(before, "mcp_410s"), 2)

    def test_410_during_tools_list(self):
        before = self._warm()
        self._control("expire_mcp_sessions")
        result = self.client.tools()
        self.assertIs(result["ok"], True)
        self.assertEqual(len(result["tools"]), 5)
        self.assertEqual(self._delta(before, "mcp_410s"), 1)
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)

    def test_tools_cache_invalidated_on_renewal(self):
        self.assertEqual(len(self.client.tools()["tools"]), 5)
        self._control("check_token_tool", {"enabled": True})
        self.assertEqual(len(self.client.tools()["tools"]), 5)  # still cached
        self._control("expire_mcp_sessions")
        self.assertIs(self._price()["ok"], True)
        self.assertIsNone(self.client._tools)
        self.assertEqual(len(self.client.tools()["tools"]), 6)

    def test_concurrent_expiry_reinitializes_once(self):
        before = self._warm()
        stale = self.client._session
        self._control("expire_mcp_sessions")
        barrier = threading.Barrier(2, timeout=10)
        real_post = mc.requests.post

        def post(url, headers=None, json=None, timeout=None):
            # Hold both threads until each has built its request on the stale id.
            if json.get("method") == "tools/call" and headers.get("Mcp-Session-Id") == stale:
                barrier.wait()
            return real_post(url, headers=headers, json=json, timeout=timeout)

        results = []
        with patch.object(mc.requests, "post", side_effect=post):
            threads = [threading.Thread(target=lambda: results.append(self._price()))
                       for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual([r["ok"] for r in results], [True, True])
        self.assertEqual(self._delta(before, "mcp_410s"), 2)
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)

    def test_engine_gate_and_wallet_status_recover_after_expiry(self):
        self._control("check_token_tool", {"enabled": True})
        data_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, data_dir, True)
        old_mcp_call = live_mod._mcp_call
        self.addCleanup(live_mod.init, old_mcp_call)
        with patch.object(engine_mod, "get_data_dir", return_value=data_dir):
            engine = engine_mod.TraderEngine(
                mcp_tools=self.client.tools,
                mcp_call=lambda name, args: self.client.call(name, args))
            token = {"address": "0x" + "22" * 20, "symbol": "TKN", "chain": "base"}
            self.assertIs(engine._risk_check(token)["approved"], True)
            self.assertEqual(engine._risk_tool["name"], "guardian_check_token")
            self._control("expire_mcp_sessions")
            verdict = engine._risk_check(token)
        self.assertIs(verdict["approved"], True, verdict)
        self.assertNotIn("fail-closed", verdict["reason"])
        self._control("expire_mcp_sessions")
        with patch.object(mc, "_default_client", self.client):
            status = mc.mcp_call(mc.SERAPH_SERVER_ID, "guardian_wallet_status", {})
        self.assertIs(status["ok"], True, status)
        self.assertEqual(self.fake.stats()["mcp_410s"], 2)

    def test_no_retry_on_500(self):
        before = self._warm()
        self._control("fail_next_mcp", {"status": 500, "count": 1})
        result = self._price()
        self.assertIs(result["ok"], False)
        self.assertNotEqual(result, EXPIRED)
        self.assertEqual(self._delta(before, "mcp_requests"), 1)
        self.assertEqual(self._delta(before, "mcp_forced_failures"), 1)
        self.assertEqual(self._delta(before, "mcp_initializes"), 0)

    def test_no_retry_on_500_after_401_remint(self):
        before = self._warm()
        self._control("revoke_api_key", {"prefix": self.auth.status().api_key_prefix})
        self._control("fail_next_mcp", {"status": 500, "count": 1})
        result = self._price()
        self.assertIs(result["ok"], False)
        self.assertNotEqual(result, EXPIRED)
        self.assertEqual(self._delta(before, "mcp_401s"), 1)
        self.assertEqual(self._delta(before, "mcp_forced_failures"), 1)
        self.assertEqual(self._delta(before, "mcp_initializes"), 0)
        self.assertEqual(self._delta(before, "mcp_tool_executions"), 0)
        self.assertIsNone(self.client._session)

    def test_logs_never_contain_credentials_or_session_ids(self):
        self._warm()
        secrets_seen = {self.auth.get_api_key(), self.client._session}
        self._control("expire_mcp_sessions")
        with self.assertLogs(mc.logger, level=logging.DEBUG) as logs:
            self.assertIs(self._price()["ok"], True)
            secrets_seen.add(self.client._session)
            self._control("expire_mcp_sessions")
            self._control("expire_next_mcp_sessions", {"count": 1})
            self.assertEqual(self._price(), EXPIRED)
        text = "\n".join(logs.output)
        self.assertIn("mcp_session_expired", text)
        for secret in secrets_seen:
            self.assertNotIn(secret, text)
        for marker in ("Bearer", "mcfw_", "Authorization"):
            self.assertNotIn(marker, text)

    def test_fake_rejects_sessionless_non_initialize_with_400(self):
        response = self.session.post(
            self.fake.mcp_url, timeout=10,
            headers={"Authorization": "Bearer " + self.auth.get_api_key()},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "crypto_get_price", "arguments": {}}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.fake.stats()["mcp_tool_executions"], 0)

    def test_renewal_between_ready_and_post_never_sends_sessionless(self):
        # B passes _ensure_ready on S1, then A drops S1 for renewal
        # and holds the lock while re-initializing; B's tools/call must still
        # carry S1 (-> 410 -> reuse A's session), never go out sessionless.
        before = self._warm()
        self._control("expire_mcp_sessions")
        real_ensure, real_drop = self.client._ensure_ready, self.client._drop_stale_session
        b_ready, dropped = threading.Event(), threading.Event()

        def ensure(secret):
            session = real_ensure(secret)
            if threading.current_thread().name == "B" and not b_ready.is_set():
                b_ready.set()
                self.assertTrue(dropped.wait(10))
            return session

        def drop(stale):
            cleared = real_drop(stale)
            if cleared and not dropped.is_set():
                dropped.set()
                time.sleep(0.3)  # keep the lock (and an empty session) while B posts
            return cleared

        results = {}

        def run(name):
            results[name] = self._price()

        with patch.object(self.client, "_ensure_ready", side_effect=ensure), \
                patch.object(self.client, "_drop_stale_session", side_effect=drop):
            b = threading.Thread(target=run, args=("B",), name="B")
            b.start()
            self.assertTrue(b_ready.wait(10))
            a = threading.Thread(target=run, args=("A",), name="A")
            a.start()
            a.join(30)
            b.join(30)
        self.assertEqual(results["A"]["ok"], True, results["A"])
        self.assertEqual(results["B"]["ok"], True, results["B"])
        self.assertEqual(self._delta(before, "mcp_400s"), 0)
        self.assertEqual(self._delta(before, "mcp_410s"), 2)
        self.assertEqual(self._delta(before, "mcp_initializes"), 1)
        self.assertEqual(self._delta(before, "mcp_tool_executions"), 2)

    def test_tools_cache_hit_survives_concurrent_invalidate(self):
        # An invalidate() landing between the cache check and the
        # return must not yield {"ok": True, "tools": None}.
        self.assertEqual(len(self.client.tools()["tools"]), 5)
        fired = []

        def local(frame, event, arg):
            if (event == "line" and not fired
                    and 'return {"ok": True, "tools":' in linecache.getline(frame.f_code.co_filename, frame.f_lineno)):
                fired.append(True)
                self.client._drop_stale_session(self.client._session)
            return local

        def tracer(frame, event, arg):
            if frame.f_code.co_filename == mc.__file__ and frame.f_code.co_name == "op":
                return local
            return None

        previous = sys.gettrace()
        sys.settrace(tracer)
        try:
            result = self.client.tools()
        finally:
            sys.settrace(previous)
        self.assertEqual(fired, [True])
        self.assertIsNone(self.client._tools)
        self.assertIs(result["ok"], True)
        self.assertEqual(len(result["tools"]), 5)


if __name__ == "__main__":
    unittest.main()
