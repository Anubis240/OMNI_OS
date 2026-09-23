"""Desktop OAuth integration against an isolated local HTTPS Seraph server."""

import base64
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_seraph_as import FakeSeraph

from core import settings_store
from trader.seraph_auth import SeraphAuth
from trader.mcp_client import McpClient


class SeraphOAuthIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._env_snapshot = dict(os.environ)
        self.addCleanup(self._restore_environment)
        self.tmpdir = TemporaryDirectory()
        self.fake = FakeSeraph()
        self.fake.start()
        self.fake_stopped = False
        os.environ["REQUESTS_CA_BUNDLE"] = self.fake.ca_bundle
        os.environ.pop("CURL_CA_BUNDLE", None)
        self.settings_patch = patch.object(
            settings_store, "SETTINGS_PATH", Path(self.tmpdir.name) / "settings.json"
        )
        self.settings_patch.start()
        self._auth_instance = self._build_auth()
        patcher = patch("trader.mcp_client.get_default_auth", return_value=self._auth_instance)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        try:
            self.settings_patch.stop()
            if not self.fake_stopped:
                self.fake.stop()
        finally:
            self.tmpdir.cleanup()

    def _restore_environment(self):
        os.environ.clear()
        os.environ.update(self._env_snapshot)

    def _build_auth(self):
        session = self.fake.client_session()
        # Trust this fake's specific self-signed certificate, not CA variables
        # left in the process by another suite module (or environment proxies).
        session.trust_env = False
        self.addCleanup(session.close)
        return SeraphAuth(
            issuer=self.fake.base_url,
            resource=self.fake.base_url + "/api",
            api_keys_url=self.fake.base_url + "/api/desktop/api-keys",
            session=session,
            opener=lambda url: bool(session.get(url, timeout=10, allow_redirects=True)),
        )

    def _mcp(self, auth):
        os.environ["REQUESTS_CA_BUNDLE"] = self.fake.ca_bundle
        return McpClient(
            url=self.fake.mcp_url,
            api_key=None,
            key_provider=auth.get_api_key,
            on_unauthorized=auth.remint_api_key,
        )

    def _control(self, name, payload=None):
        with self.fake.client_session() as session:
            session.trust_env = False
            response = session.post(
                self.fake.base_url + "/_test/" + name,
                json={} if payload is None else payload,
                timeout=10,
            )
            self.assertEqual(response.status_code, 200)
            return response.json()

    def _login(self):
        auth = self._auth_instance
        self.assertIs(auth.login(timeout_s=20).ok, True)
        return auth

    def test_full_login_mints_a_key_with_mcp_and_wallet_execute_scopes(self):
        auth = self._login()
        status = auth.status()
        self.assertEqual(status.mode, "api_key_oauth")
        self.assertTrue(status.api_key_prefix.startswith("mcfw_"))
        self.assertEqual(len(status.api_key_prefix), 12)
        self.assertEqual(status.api_key_scopes, ("mcp", "wallet:execute"))
        self.assertEqual(self.fake.stats()["mints"], 1)
        self.assertTrue(settings_store.load_settings()["seraph_auth"]["api_key"].startswith("mcfw_"))

    def test_token_is_requested_with_the_api_resource_and_both_scopes(self):
        self._login()
        token = settings_store.load_settings()["seraph_auth"]["access_token"]
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        self.assertEqual(payload["aud"], self.fake.base_url + "/api")
        self.assertIn("api-keys:write", payload["scope"].split())
        self.assertIn("wallet:execute", payload["scope"].split())

    def test_mcp_tools_call_uses_the_minted_key_and_no_jwt_reaches_mcp(self):
        client = self._mcp(self._login())
        result = client.tools()
        self.assertIs(result["ok"], True)
        self.assertEqual(len(result["tools"]), 5)
        self.assertIs(client.call("crypto_get_price", {"asset": "ETH"})["ok"], True)
        self.assertEqual(self.fake.stats()["mcp_401s"], 0)

    def test_revoked_key_triggers_a_single_remint_and_the_retry_succeeds(self):
        auth = self._login()
        self._control("revoke_api_key", {"prefix": auth.status().api_key_prefix})
        result = self._mcp(auth).call("crypto_get_price", {"asset": "ETH"})
        self.assertIs(result["ok"], True)
        self.assertEqual(self.fake.stats()["mints"], 2)

    def test_expired_access_token_refreshes_before_minting(self):
        auth = self._login()
        old_key = auth.get_api_key()
        self._control("expire_access")
        new_key = auth.remint_api_key()
        self.assertIsNotNone(new_key)
        self.assertNotEqual(new_key, old_key)
        self.assertEqual(auth.get_api_key(), new_key)
        self.assertGreaterEqual(self.fake.stats()["refreshes"], 1)
        self.assertEqual(self.fake.stats()["mints"], 2)

    def test_revoked_refresh_family_fails_the_remint_but_keeps_the_local_key(self):
        auth = self._login()
        key = auth.get_api_key()
        self._control("revoke_family")
        self._control("expire_access")
        self.assertIsNone(auth.remint_api_key())
        # Key and refresh token are independent credentials: destroying a working
        # key because refresh died would be destructive. needs_login becomes True
        # only when McpClient calls invalidate_session() after a 401 survives
        # re-mint, covered by test_a_401_that_survives_the_remint_invalidates_the_session.
        self.assertEqual(auth.get_api_key(), key)
        self.assertIs(auth.status().needs_login, False)
        self.assertEqual(auth.status().last_error, "remint_failed")

    def test_a_401_that_survives_the_remint_invalidates_the_session(self):
        auth = self._login()
        self._control("revoke_api_key", {"prefix": auth.status().api_key_prefix})
        self._control("mint_failure", {"status": 500})
        result = self._mcp(auth).call("crypto_get_price", {"asset": "ETH"})
        self.assertIs(result["ok"], False)
        self.assertEqual(result["error"], "seraph_login_required")
        self.assertIs(auth.status().needs_login, True)
        self.assertIsNone(auth.get_api_key())

    def test_mint_failure_keeps_the_existing_key(self):
        auth = self._login()
        key = auth.get_api_key()
        self._control("mint_failure", {"status": 503})
        self.assertIsNone(auth.remint_api_key())
        self.assertEqual(auth.get_api_key(), key)
        self.assertEqual(auth.status().mode, "api_key_oauth")

    def test_disconnect_device_deletes_the_remote_key_and_clears_local_state(self):
        auth = self._login()
        client = self._mcp(auth)
        auth.disconnect_device()
        self.assertEqual(self.fake.stats()["deletes"], 1)
        self.assertEqual(self.fake.active_key_count(), 0)
        self.assertEqual(auth.status().mode, "none")
        self.assertIsNone(auth.get_api_key())
        result = client.call("crypto_get_price", {"asset": "ETH"})
        self.assertEqual(result["error"], "seraph_login_required")

    def test_disconnect_device_clears_local_state_even_when_the_server_is_unreachable(self):
        auth = self._login()
        self.fake.stop()
        self.fake_stopped = True
        auth.disconnect_device()
        self.assertEqual(auth.status().mode, "none")

    def test_disconnect_keeps_the_client_id_and_device_id(self):
        auth = self._login()
        before = settings_store.load_settings()["seraph_auth"]
        auth.disconnect_device()
        after = settings_store.load_settings()["seraph_auth"]
        self.assertEqual(after["client_id"], before["client_id"])
        self.assertEqual(after["device_id"], before["device_id"])

    def test_cancelled_consent_returns_access_denied(self):
        session = self.fake.client_session()
        session.trust_env = False
        self.addCleanup(session.close)
        def deny_consent(url):
            parsed = urlsplit(url)
            query = parse_qsl(parsed.query, keep_blank_values=True)
            query.append(("_deny", "1"))
            denied_url = urlunsplit(parsed._replace(query=urlencode(query)))
            return bool(session.get(denied_url, timeout=10, allow_redirects=True))

        auth = self._auth_instance
        auth.opener = deny_consent
        result = auth.login(timeout_s=20)
        self.assertIs(result.ok, False)
        self.assertEqual(result.error, "access_denied")
        self.assertEqual(self.fake.stats()["mints"], 0)

    def test_second_login_reuses_the_client_id_and_rotates_the_key(self):
        auth = self._login()
        client_id = settings_store.load_settings()["seraph_auth"]["client_id"]
        key = auth.get_api_key()
        self.assertIs(auth.login(timeout_s=20).ok, True)
        self.assertEqual(settings_store.load_settings()["seraph_auth"]["client_id"], client_id)
        self.assertNotEqual(auth.get_api_key(), key)
        self.assertEqual(self.fake.stats()["registrations"], 1)
        self.assertEqual(self.fake.stats()["mints"], 2)
        self.assertEqual(self.fake.active_key_count(), 1)

    def test_manual_key_path_never_mints(self):
        auth = self._auth_instance
        auth.set_manual_api_key("mcfw_" + "ab" * 32)
        self.assertEqual(auth.status().mode, "api_key_manual")
        self.assertEqual(self.fake.stats()["mints"], 0)
        block = settings_store.load_settings()["seraph_auth"]
        for field in ("access_token", "access_expires_at", "refresh_token", "refresh_issued_at", "scope"):
            with self.subTest(field=field):
                self.assertIsNone(block[field])


if __name__ == "__main__":
    unittest.main()
