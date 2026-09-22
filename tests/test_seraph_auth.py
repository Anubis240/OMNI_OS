"""Isolated OAuth/device-key regressions; only listener tests open sockets."""

import http.client
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from trader import seraph_auth as sa


KEY = "mcfw_test_device_key_secret_123456789"
ACCESS = "access-token-secret"
REFRESH = "refresh-token-secret"
CLIENT = "mcp_abcdefghijklmnop"
DEVICE = "device_abcdefghijklmnop"
REDIRECT = "http://127.0.0.1/callback"


def response(status, data=None):
    result = Mock(status_code=status)
    result.json.return_value = deepcopy(data)
    return result


class FakeListener:
    """No socket: expose callback results and the real cancellation event API."""

    def __init__(self, state):
        self.state = state
        self.redirect_uri = "http://127.0.0.1:49152/callback"
        self.event = threading.Event()
        self.code = "authorization-code-secret"
        self.error = None
        self.state_mismatch_seen = False
        self.completed = True
        self.stopped = False
        self.wait_hook = None

    def wait(self, timeout):
        if self.wait_hook:
            return self.wait_hook(timeout)
        return self.completed

    def stop(self):
        self.stopped = True


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.storage = {sa.SETTINGS_KEY: {}}
        self.storage_lock = threading.RLock()
        self.now = 100000.0
        self.session = Mock(spec=["get", "post", "delete"])
        self.metadata = {name: sa.SERAPH_ISSUER + path
                         for name, path in sa.DEFAULT_ENDPOINTS.items()}
        self.metadata["issuer"] = sa.SERAPH_ISSUER
        self.mint_data = {"key": KEY, "id": "key-id", "keyPrefix": KEY[:12],
                          "scopes": ["wallet:execute"], "mcpUrl": sa.SERAPH_MCP_URL}
        self.session.get.side_effect = self.get_response
        self.session.post.side_effect = self.post_response
        self.session.delete.return_value = response(204)
        self.load_mock = self.start_patch("settings_store.load_settings", side_effect=self.load)
        self.update_mock = self.start_patch("settings_store.update_settings", side_effect=self.update)
        self.start_patch("settings_store._dpapi_available", return_value=True)
        # Fail closed if a production path ignores the injected session.
        for method in ("get", "post", "delete"):
            self.start_patch("requests." + method, side_effect=AssertionError("real network forbidden"))
        self.listener_factory = self.start_patch("_LoopbackListener", side_effect=self.make_listener)
        self.opener = Mock(return_value=True)
        self.auth = self.new_auth()

    def start_patch(self, name, **kwargs):
        patcher = patch("trader.seraph_auth." + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def new_auth(self):
        return sa.SeraphAuth(session=self.session, opener=self.opener, clock=lambda: self.now)

    @property
    def block(self):
        return self.storage[sa.SETTINGS_KEY]

    def load(self):
        with self.storage_lock:
            return deepcopy(self.storage)

    def update(self, mutate):
        with self.storage_lock:
            mutate(self.storage)

    def make_listener(self, state):
        self.listener = FakeListener(state)
        return self.listener

    def get_response(self, url, **kwargs):
        if url.endswith("/.well-known/oauth-authorization-server"):
            return response(200, self.metadata)
        if url.endswith(sa.CLIENT_INFO_PATH):
            return response(200, {})
        raise AssertionError("Unexpected GET: " + url)

    def post_response(self, url, **kwargs):
        if url.endswith("/register"):
            return response(201, {"client_id": CLIENT})
        if url.endswith("/token"):
            return response(200, {"access_token": ACCESS, "refresh_token": REFRESH,
                                  "expires_in": 900, "scope": sa.SERAPH_SCOPES})
        if url == self.auth.api_keys_url:
            return response(201, self.mint_data)
        if url.endswith("/revoke"):
            return response(200, {})
        raise AssertionError("Unexpected POST: " + url)

    def calls_to(self, suffix):
        return [call for call in self.session.post.call_args_list
                if call.args[0].endswith(suffix)]

    def seed(self):
        self.block.update(self.metadata, metadata_fetched_at=self.now,
                          client_id=CLIENT, device_id=DEVICE, registered_redirect_uri=REDIRECT,
                          api_key=KEY, api_key_id="key-id", api_key_prefix=KEY[:12],
                          api_key_source="oauth", access_token=ACCESS,
                          access_expires_at=self.now + 900, refresh_token=REFRESH)

    def configure_callback(self, **fields):
        def opening(url):
            for name, value in fields.items():
                setattr(self.listener, name, value)
            return True
        self.opener.side_effect = opening

    def assert_no_credentials(self):
        for field in ("api_key", "access_token", "refresh_token"):
            self.assertFalse(self.block.get(field), field)


class PkceTests(AuthTestCase):
    def test_rfc7636_vector_and_generated_pair(self):
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        self.assertEqual(sa._pkce_challenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        verifier, challenge = self.auth._pkce_pair()
        self.assertEqual((len(verifier), len(challenge)), (86, 43))
        self.assertEqual(challenge, sa._pkce_challenge(verifier))
        self.assertNotIn("=", verifier + challenge)

    def test_new_states_are_long_and_distinct(self):
        states = [self.auth._new_state() for _ in range(10)]
        self.assertEqual(len(set(states)), 10)
        self.assertTrue(all(len(state) >= 32 for state in states))


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.state = "correct-state-secret-abcdefghijklmnop"
        self.code = "callback-code-secret-abcdefghijklmnop"
        self.listener = sa._LoopbackListener(self.state)
        self.addCleanup(self.listener.stop)

    def request(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.listener.port, timeout=2)
        try:
            connection.request("GET", path)
            reply = connection.getresponse()
            return reply.status, reply.read().decode()
        finally:
            connection.close()

    def callback(self, **kwargs):
        return self.request("/callback?" + urlencode(kwargs))

    def test_other_path_returns_404(self):
        self.assertEqual(self.request("/other-path")[0], 404)
        self.assertFalse(self.listener.event.is_set())

    def test_bad_state_does_not_end_wait(self):
        self.assertEqual(self.callback(code=self.code, state="wrong")[0], 400)
        self.assertTrue(self.listener.state_mismatch_seen)
        self.assertFalse(self.listener.event.is_set())

    def test_valid_callback_html_does_not_echo_secrets(self):
        status, body = self.callback(code=self.code, state=self.state)
        self.assertEqual(status, 200)
        self.assertIn("<html>", body)
        self.assertNotIn(self.code, body)
        self.assertNotIn(self.state, body)
        self.assertTrue(self.listener.wait(1))
        self.assertEqual(self.listener.code, self.code)

    def test_second_callback_is_gone(self):
        # Missing code is malformed and does not consume the one-shot callback.
        self.assertEqual(self.callback(state=self.state)[0], 400)
        self.assertFalse(self.listener.event.is_set())
        self.assertEqual(self.callback(code=self.code, state=self.state)[0], 200)
        self.assertEqual(self.callback(code="second", state=self.state)[0], 410)
        self.assertEqual(self.listener.code, self.code)


class MetadataTests(AuthTestCase):
    def test_valid_metadata_is_cached(self):
        self.assertEqual(self.auth._discover_metadata(), self.metadata)
        self.assertEqual(self.auth._discover_metadata(), self.metadata)
        self.session.get.assert_called_once()

    def test_cache_expires_at_ttl(self):
        self.auth._discover_metadata()
        self.now += sa.METADATA_TTL_S
        self.auth._discover_metadata()
        self.assertEqual(self.session.get.call_count, 2)

    def test_wrong_issuer_falls_back(self):
        expected = deepcopy(self.metadata)
        self.metadata["issuer"] = "https://evil.example"
        self.assertEqual(self.auth._discover_metadata(), expected)
        self.assertNotIn("metadata_fetched_at", self.block)

    def test_cross_origin_endpoint_falls_back(self):
        self.metadata["token_endpoint"] = "https://evil.example/token"
        result = self.auth._discover_metadata()
        self.assertEqual(result["token_endpoint"], sa.SERAPH_ISSUER + "/token")
        self.assertNotIn("metadata_fetched_at", self.block)

    def test_network_failure_uses_fallback_authorize_url(self):
        self.session.get.side_effect = requests.ConnectionError("offline")
        url = self.auth._build_authorize_url(client_id=CLIENT, redirect_uri=REDIRECT,
                                             state="state", challenge="challenge")
        self.assertTrue(url.startswith(sa.SERAPH_ISSUER + "/authorize?"))


class ClientTests(AuthTestCase):
    def test_missing_client_registers_and_persists(self):
        self.assertEqual(self.auth._ensure_client(REDIRECT), CLIENT)
        self.assertEqual(self.block["client_id"], CLIENT)
        call = self.calls_to("/register")[0]
        self.assertEqual(call.kwargs["json"]["redirect_uris"], [REDIRECT])
        self.assertEqual(call.kwargs["json"]["client_name"], sa.CLIENT_NAME)

    def test_existing_client_is_probed_not_registered(self):
        self.seed()
        self.assertEqual(self.auth._ensure_client(REDIRECT), CLIENT)
        self.session.get.assert_called_once()
        self.assertEqual(self.session.get.call_args.args[0], sa.SERAPH_ISSUER + "/client-info")
        self.session.post.assert_not_called()

    def test_missing_remote_client_reregisters_once(self):
        self.seed()
        self.session.get.return_value = response(404)
        self.session.get.side_effect = None
        self.assertEqual(self.auth._ensure_client(REDIRECT), CLIENT)
        self.assertEqual(len(self.calls_to("/register")), 1)

    def test_registration_cap_under_two_attempts_in_one_login(self):
        self.session.post.side_effect = lambda *args, **kwargs: response(500)
        # Login normally stops at the first failure; inject a second ensure
        # attempt in the same flow to exercise the registration guard itself.
        ensure = self.auth._ensure_client
        def twice(redirect):
            self.assertIsNone(ensure(redirect))
            return ensure(redirect)
        with patch.object(self.auth, "_ensure_client", side_effect=twice):
            result = self.auth.login()
        self.assertEqual(result.error, "registration_failed")
        self.assertEqual(len(self.calls_to("/register")), 1)


class LoginTests(AuthTestCase):
    def test_complete_login(self):
        result = self.auth.login()
        self.assertTrue(result.ok)
        self.assertEqual(result.status.mode, "api_key_oauth")
        self.assertEqual(self.auth.get_api_key(), KEY)
        self.assertTrue(self.auth.is_logged_in())
        self.assertTrue(self.listener.stopped)
        self.assertEqual([call.args[0] for call in self.session.post.call_args_list],
                         [self.metadata["registration_endpoint"], self.metadata["token_endpoint"],
                          self.auth.api_keys_url])

    def test_progress_messages_exact_order(self):
        messages = []
        self.assertTrue(self.auth.login(on_status=messages.append).ok)
        self.assertEqual(messages, ["Checking Seraph configuration…",
                                   "Opening your browser to sign in…",
                                   "Waiting for browser sign-in…", "Completing sign-in…",
                                   "Creating your Seraph API key…", "Signed in"])

    def test_token_exchange_resource_verifier_and_redirect(self):
        self.assertTrue(self.auth.login().ok)
        data = self.calls_to("/token")[0].kwargs["data"]
        query = parse_qs(urlsplit(self.opener.call_args.args[0]).query)
        self.assertEqual(data["resource"], sa.SERAPH_API_RESOURCE)
        self.assertEqual(data["redirect_uri"], self.listener.redirect_uri)
        self.assertEqual(data["code"], self.listener.code)
        self.assertEqual(sa._pkce_challenge(data["code_verifier"]), query["code_challenge"][0])

    def test_authorize_scope_and_resource(self):
        self.assertTrue(self.auth.login().ok)
        query = parse_qs(urlsplit(self.opener.call_args.args[0]).query)
        self.assertEqual(query["scope"], ["api-keys:write wallet:execute offline_access"])
        self.assertEqual(query["resource"], [sa.SERAPH_API_RESOURCE])
        self.assertEqual(query["state"], [self.listener.state])

    def test_access_denied(self):
        self.configure_callback(error="access_denied", code=None)
        self.assertEqual(self.auth.login().error, "access_denied")
        self.assertFalse(self.calls_to("/token"))

    def test_completed_callback_without_code_fails(self):
        self.configure_callback(code=None)
        self.assertEqual(self.auth.login().error, "token_exchange_failed")
        self.assert_no_credentials()

    def test_listener_timeout(self):
        self.configure_callback(completed=False)
        self.assertEqual(self.auth.login(timeout_s=0).error, "timeout")

    def test_timeout_after_mismatched_state(self):
        self.configure_callback(completed=False, state_mismatch_seen=True)
        self.assertEqual(self.auth.login(timeout_s=0).error, "state_mismatch")

    def test_cancellation_during_wait_stops_listener(self):
        waiting = threading.Event()
        def wait(timeout):
            waiting.set()
            return self.listener.event.wait(timeout)
        self.configure_callback(wait_hook=wait)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.auth.login, timeout_s=2)
            self.assertTrue(waiting.wait(1))
            self.auth.cancel_login()
            result = pending.result(timeout=3)
        self.assertEqual(result.error, "cancelled")
        self.assertTrue(self.listener.stopped)
        self.assertIsNone(self.auth._active_listener)
        self.assert_no_credentials()

    def test_browser_failure_includes_authorize_url(self):
        self.opener.return_value = False
        result = self.auth.login()
        self.assertEqual(result.error, "browser_open_failed")
        self.assertIn(self.opener.call_args.args[0], result.error_description)
        self.assertTrue(self.listener.stopped)

    def test_invalid_client_clears_client_but_preserves_device(self):
        self.seed()
        self.session.post.side_effect = lambda *args, **kwargs: response(400, {"error": "invalid_client"})
        self.assertEqual(self.auth.login().error, "invalid_client")
        self.assertIsNone(self.block["client_id"])
        self.assertEqual(self.block["device_id"], DEVICE)

    def test_token_network_failure_does_not_persist_key(self):
        original = self.post_response
        def post(url, **kwargs):
            if url.endswith("/token"):
                raise requests.ConnectionError("offline")
            return original(url, **kwargs)
        self.session.post.side_effect = post
        self.assertEqual(self.auth.login().error, "network_error")
        self.assert_no_credentials()


class MintTests(AuthTestCase):
    def test_mint_payload_and_bearer(self):
        self.assertTrue(self.auth.login().ok)
        call = self.calls_to(sa.SERAPH_API_KEYS_PATH)[0]
        self.assertEqual(call.kwargs["json"], {"name": sa.CLIENT_NAME,
                         "device_id": self.block["device_id"], "replace_existing": True})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer " + ACCESS)

    def test_mint_persists_key_fields(self):
        self.assertTrue(self.auth.login().ok)
        for name, expected in {"api_key": KEY, "api_key_id": "key-id",
                               "api_key_prefix": KEY[:12], "api_key_scopes": ["wallet:execute"],
                               "api_key_source": "oauth"}.items():
            self.assertEqual(self.block[name], expected)

    def fail_mint(self, status, data=None):
        original = self.post_response
        self.session.post.side_effect = lambda url, **kwargs: (
            response(status, data) if url == self.auth.api_keys_url else original(url, **kwargs))
        result = self.auth.login()
        self.assertFalse(result.ok)
        self.assert_no_credentials()
        return result

    def test_forbidden_mint_persists_no_credentials(self):
        self.assertEqual(self.fail_mint(403).error, "insufficient_scope")

    def test_server_failure_persists_no_credentials(self):
        self.assertEqual(self.fail_mint(500).error, "api_key_mint_failed")

    def test_missing_key_persists_no_credentials(self):
        self.assertEqual(self.fail_mint(201, {"id": "no-key"}).error, "api_key_mint_failed")

    def test_mcp_url_mismatch_warns_without_changing_configuration(self):
        self.mint_data["mcpUrl"] = "https://evil.example/mcp"
        with self.assertLogs(sa.logger, level="WARNING") as logs:
            self.assertTrue(self.auth.login().ok)
        self.assertTrue(any("mint_mcp_url_mismatch" in line for line in logs.output))
        self.assertEqual(sa.SERAPH_MCP_URL, "https://seraph.kondux.io/mcp")
        self.assertNotIn("mcpUrl", self.block)
        self.assertEqual(self.auth.api_keys_url, sa.SERAPH_CONTROL_PLANE_URL + sa.SERAPH_API_KEYS_PATH)

    def test_two_logins_reuse_device_id(self):
        self.assertTrue(self.auth.login().ok)
        device = self.block["device_id"]
        self.assertTrue(self.auth.login().ok)
        self.assertEqual(self.block["device_id"], device)
        self.assertEqual([call.kwargs["json"]["device_id"]
                          for call in self.calls_to(sa.SERAPH_API_KEYS_PATH)], [device, device])


class RefreshTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.seed()

    def test_fresh_access_token_avoids_network(self):
        self.assertEqual(self.auth._get_access_token(), ACCESS)
        self.assertEqual(self.session.mock_calls, [])

    def test_expiry_within_skew_refreshes(self):
        self.block["access_expires_at"] = self.now + sa.REFRESH_SKEW_S
        self.assertEqual(self.auth._get_access_token(), ACCESS)
        self.assertEqual(len(self.calls_to("/token")), 1)

    def test_refresh_resource_is_api_not_mcp(self):
        self.auth._get_access_token(force_refresh=True)
        data = self.calls_to("/token")[0].kwargs["data"]
        self.assertEqual(data["resource"], sa.SERAPH_API_RESOURCE)
        self.assertNotEqual(data["resource"], sa.SERAPH_MCP_URL)
        self.assertEqual(data["grant_type"], "refresh_token")

    def test_refresh_rotates_refresh_token(self):
        self.session.post.side_effect = lambda *args, **kwargs: response(
            200, {"access_token": "rotated-access", "refresh_token": "rotated-refresh"})
        self.assertEqual(self.auth._get_access_token(force_refresh=True), "rotated-access")
        self.assertEqual(self.block["refresh_token"], "rotated-refresh")
        self.assertEqual(self.block["access_expires_at"], self.now + sa.DEFAULT_EXPIRES_IN_S)

    def test_invalid_grant_clears_only_tokens(self):
        self.session.post.side_effect = lambda *args, **kwargs: response(400, {"error": "invalid_grant"})
        self.assertIsNone(self.auth._get_access_token(force_refresh=True))
        for field in sa._TOKEN_FIELDS:
            self.assertIsNone(self.block[field])
        self.assertEqual(self.auth.get_api_key(), KEY)
        self.assertEqual(self.block["client_id"], CLIENT)

    def test_refresh_network_failure_preserves_storage(self):
        before = deepcopy(self.storage)
        self.session.post.side_effect = requests.ConnectionError("offline")
        self.assertIsNone(self.auth._get_access_token(force_refresh=True))
        self.assertEqual(self.storage, before)

    def test_forced_refresh_deduplicates(self):
        self.assertEqual(self.auth._get_access_token(force_refresh=True), ACCESS)
        self.now += sa.REFRESH_DEDUP_S - 1
        self.assertEqual(self.auth._get_access_token(force_refresh=True), ACCESS)
        self.assertEqual(len(self.calls_to("/token")), 1)

    def test_remint_persists_new_key(self):
        self.mint_data["key"] = "mcfw_new_device_key_123456789"
        self.assertEqual(self.auth.remint_api_key(), self.mint_data["key"])
        self.assertEqual(self.auth.get_api_key(), self.mint_data["key"])

    def test_remint_deduplicates(self):
        self.assertEqual(self.auth.remint_api_key(), KEY)
        self.now += sa.REMINT_DEDUP_S - 1
        self.assertEqual(self.auth.remint_api_key(), KEY)
        self.assertEqual(len(self.calls_to(sa.SERAPH_API_KEYS_PATH)), 1)

    def test_remint_401_retries_once_preserving_old_key_on_failure(self):
        original = self.post_response
        self.session.post.side_effect = lambda url, **kwargs: (
            response(401, {}) if url == self.auth.api_keys_url else original(url, **kwargs))
        with patch.object(self.auth, "_get_access_token", wraps=self.auth._get_access_token) as access:
            self.assertIsNone(self.auth.remint_api_key())
        self.assertEqual(access.call_count, 2)
        self.assertEqual(access.call_args.kwargs, {"force_refresh": True})
        self.assertEqual(len(self.calls_to(sa.SERAPH_API_KEYS_PATH)), 2)
        self.assertEqual(len(self.calls_to("/token")), 1)
        self.assertEqual(self.auth.status().last_error, "remint_failed")
        self.assertEqual(self.auth.get_api_key(), KEY)


class DisconnectAndStatusTests(AuthTestCase):
    def test_disconnect_deletes_then_revokes_then_clears(self):
        self.seed()
        order = []
        def delete(url, **kwargs):
            self.assertEqual(self.auth.get_api_key(), KEY)
            order.append("delete")
            return response(204)
        def revoke(url, **kwargs):
            self.assertEqual(self.auth.get_api_key(), KEY)
            order.append("revoke")
            return response(200)
        self.session.delete.side_effect = delete
        self.session.post.side_effect = revoke
        self.auth.disconnect_device()
        self.assertEqual(order, ["delete", "revoke"])
        self.session.delete.assert_called_once_with(
            self.auth.api_keys_url + "/key-id", params={"device_id": DEVICE},
            headers={"Authorization": "Bearer " + ACCESS}, timeout=sa.DISCONNECT_TIMEOUT_S)
        self.session.post.assert_called_once_with(
            self.metadata["revocation_endpoint"], data={"token": REFRESH, "client_id": CLIENT,
            "token_type_hint": "refresh_token"}, timeout=sa.REVOKE_TIMEOUT_S)
        self.assert_no_credentials()

    def test_disconnect_offline_still_clears(self):
        self.seed()
        self.session.delete.side_effect = requests.ConnectionError("offline")
        self.session.post.side_effect = requests.ConnectionError("offline")
        self.auth.disconnect_device()
        self.session.delete.assert_called_once()
        self.session.post.assert_called_once()
        self.assert_no_credentials()

    def test_disconnect_without_valid_token_skips_delete_and_login(self):
        self.seed()
        self.block.update(access_expires_at=self.now - 1, refresh_token=None)
        with patch.object(self.auth, "login", side_effect=AssertionError("must not login")):
            self.auth.disconnect_device()
        self.session.delete.assert_not_called()
        self.session.post.assert_not_called()
        self.assert_no_credentials()

    def test_disconnect_preserves_registration_device_and_metadata(self):
        self.seed()
        names = ("client_id", "device_id", "registered_redirect_uri", "metadata_fetched_at",
                 *self.metadata)
        before = {name: self.block[name] for name in names}
        self.auth.disconnect_device()
        self.assertEqual({name: self.block[name] for name in names}, before)

    def test_logout_is_not_exposed(self):
        self.assertFalse(hasattr(sa.SeraphAuth, "logout"))

    def test_manual_key_clears_tokens(self):
        self.seed()
        self.auth.set_manual_api_key(KEY)
        self.assertEqual(self.auth.status().mode, "api_key_manual")
        self.assertFalse(self.auth.is_logged_in())
        for field in sa._TOKEN_FIELDS:
            self.assertIsNone(self.block[field])

    def test_unrecognized_manual_prefix_warns_but_is_accepted(self):
        with self.assertLogs(sa.logger, level="WARNING") as logs:
            self.auth.set_manual_api_key("unusual-secret-key")
        self.assertIn("manual_api_key_unrecognized_prefix", "\n".join(logs.output))
        self.assertEqual(self.auth.get_api_key(), "unusual-secret-key")

    def test_status_modes_and_prefix_only(self):
        # Legacy is a display type, not a mode produced by this implementation.
        for source, expected in (("oauth", "api_key_oauth"), ("manual", "api_key_manual"),
                                 ("legacy", "none"), (None, "none")):
            with self.subTest(source=source):
                self.block.update(api_key=KEY, api_key_source=source, api_key_prefix=KEY[:12])
                status = self.auth.status()
                self.assertEqual(status.mode, expected)
                self.assertEqual(len(status.api_key_prefix), 12)
                self.assertNotIn(KEY, repr(asdict(status)))
        self.block.clear()
        self.assertEqual(self.auth.status().mode, "none")
        self.assertIsNone(self.auth.status().api_key_prefix)

    def test_abbreviated_subject(self):
        self.assertEqual(sa.abbreviate_subject("did:privy:cmp1fe7sm004v0cjmn1i9rwyc"), "did:privy:cm…9rwyc")
        self.assertEqual(sa.abbreviate_subject(None), "—")

    def test_format_identity_for_supported_and_legacy_display_modes(self):
        self.seed()
        status = replace(self.auth.status(), subject="alice")
        prefix = "key " + KEY[:12]
        for mode, expected in (("api_key_oauth", prefix + " (signed in as alice)"),
                               ("api_key_manual", prefix + " (manual)"),
                               ("api_key_legacy", prefix + " (legacy)"),
                               ("none", "not connected")):
            with self.subTest(mode=mode):
                self.assertEqual(sa.format_identity(replace(status, mode=mode)), expected)


class SecurityTests(AuthTestCase):
    def test_repr_never_contains_credentials(self):
        self.seed()
        for secret in (KEY, ACCESS, REFRESH):
            self.assertNotIn(secret, repr(self.auth))

    def test_login_logs_never_contain_credentials(self):
        with self.assertLogs(sa.logger, level="DEBUG") as logs:
            self.assertTrue(self.auth.login().ok)
        text = "\n".join(logs.output)
        for secret in (KEY, ACCESS, REFRESH):
            self.assertNotIn(secret, text)

    def test_failed_mint_never_persists_access_token(self):
        original = self.post_response
        self.session.post.side_effect = lambda url, **kwargs: (
            response(500, {}) if url == self.auth.api_keys_url else original(url, **kwargs))
        self.assertEqual(self.auth.login().error, "api_key_mint_failed")
        self.assertNotIn("access_token", self.block)
        self.assert_no_credentials()

    def test_key_and_status_are_pure_reads(self):
        self.seed()
        before = deepcopy(self.storage)
        for _ in range(3):
            self.assertEqual(self.auth.get_api_key(), KEY)
            self.assertEqual(self.auth.status().mode, "api_key_oauth")
        self.assertEqual(self.session.mock_calls, [])
        self.update_mock.assert_not_called()
        self.assertEqual(self.storage, before)

    def test_login_persistence_roundtrip_to_new_instance(self):
        self.assertTrue(self.auth.login().ok)
        other = self.new_auth()
        self.assertEqual(other.get_api_key(), KEY)
        self.assertEqual(other.status().mode, "api_key_oauth")

    def test_five_concurrent_remints_issue_one_key(self):
        self.seed()
        barrier = threading.Barrier(5)
        def remint():
            barrier.wait(timeout=3)
            return self.auth.remint_api_key()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: remint(), range(5)))
        self.assertEqual(results, [KEY] * 5)
        self.assertEqual(len(self.calls_to(sa.SERAPH_API_KEYS_PATH)), 1)


if __name__ == "__main__":
    unittest.main()
