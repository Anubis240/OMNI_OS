import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import trader.mcp_client as mc


def _response(status=200, payload=None, session_id="sess-1", ctype="application/json"):
    res = MagicMock()
    res.status_code = status
    res.headers = {"content-type": ctype, "mcp-session-id": session_id}
    res.json.return_value = payload if payload is not None else {
        "jsonrpc": "2.0", "id": 1, "result": {}
    }
    res.text = json.dumps(res.json.return_value)
    res.raise_for_status.return_value = None
    return res


def _status(mode="api_key_oauth", **kw):
    st = MagicMock()
    st.mode = mode
    st.api_key_prefix = kw.get("api_key_prefix", "mcfw_3fa9c1e")
    st.subject = kw.get("subject", "did:privy:abc")
    st.org_id = kw.get("org_id", "org_1")
    st.api_key_created_at = kw.get("api_key_created_at", 1.0)
    st.api_key_scopes = kw.get("api_key_scopes", ("mcp", "wallet:execute"))
    st.needs_login = kw.get("needs_login", False)
    st.secure_storage = kw.get("secure_storage", True)
    st.last_error = kw.get("last_error", None)
    return st


def _auth(api_key="mcfw_minted", mode="api_key_oauth", remint="mcfw_fresh"):
    auth = MagicMock()
    auth.get_api_key.return_value = api_key
    auth.status.return_value = _status(mode)
    auth.remint_api_key.return_value = remint
    return auth


class McpClientCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        mc._default_client = None
        self.addCleanup(lambda: setattr(mc, "_default_client", None))
        self.auth = _auth()
        self.enterContext(patch.object(mc, "get_default_auth", return_value=self.auth))
        self.requests = self.enterContext(patch.object(mc, "requests"))
        self.requests.post.return_value = _response()
        self.legacy = self.enterContext(
            patch.object(mc, "_load_legacy_api_key", return_value=("", "none"))
        )
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "api_keys.json"
        self.enterContext(patch.object(mc, "_app_api_keys_path", return_value=self.path))

    def test_precedence_explicit_over_minted(self):
        self.assertEqual(mc.McpClient(api_key="ctor-key")._resolve_credential(),
                         ("ctor-key", "api_key_explicit", "ctor"))
        self.auth.get_api_key.assert_not_called()

    def test_precedence_minted_over_legacy(self):
        self.legacy.return_value = ("legacy", "api_keys_json")
        self.assertEqual(mc.McpClient()._resolve_credential(),
                         ("mcfw_minted", "api_key_oauth", "oauth"))
        self.legacy.assert_not_called()

    def test_precedence_manual_mode(self):
        self.auth.get_api_key.return_value = "mcfw_manual"
        self.auth.status.return_value = _status("api_key_manual")
        self.assertEqual(mc.McpClient()._resolve_credential(),
                         ("mcfw_manual", "api_key_manual", "manual"))

    def test_precedence_legacy_when_no_minted_key(self):
        self.auth.get_api_key.return_value = None
        self.legacy.return_value = ("legacy-key", "guardian_settings")
        self.assertEqual(mc.McpClient()._resolve_credential(),
                         ("legacy-key", "api_key_legacy", "guardian_settings"))

    def test_precedence_none(self):
        self.auth.get_api_key.return_value = None
        self.assertEqual(mc.McpClient()._resolve_credential(), ("", "none", "none"))

    def test_sent_as_bearer(self):
        self.auth.get_api_key.return_value = "mcfw_abc"
        self.assertEqual(mc.McpClient().call("x"),
                         {"ok": True, "text": "", "isError": False})
        self.assertEqual(self.requests.post.call_count, 3)
        for request in self.requests.post.call_args_list:
            self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer mcfw_abc")

    def test_kw_compat_mcp_registry(self):
        client = mc.McpClient(url="http://x", api_key="k")
        self.assertEqual(client.url, "http://x")
        self.assertEqual(client._resolve_credential(), ("k", "api_key_explicit", "ctor"))

    def test_path_uses_get_data_dir(self):
        # setUp replaces the path resolver to isolate all other tests from real files.
        with patch.object(mc, "_app_api_keys_path", _REAL_APP_API_KEYS_PATH):
            with patch.object(mc, "get_data_dir", return_value=self.path.parent) as data_dir:
                result = mc._app_api_keys_path()
        self.assertEqual(result, self.path.parent / "config" / "api_keys.json")
        self.assertNotIn("trader", result.parts)
        data_dir.assert_called_once_with()

    def test_no_credentials_fails_closed_without_network(self):
        self.auth.get_api_key.return_value = None
        self.assertEqual(mc.McpClient().call("x"),
                         {"ok": False, "error": "seraph_login_required"})
        self.requests.post.assert_not_called()

    def _successful_retry(self):
        self.requests.post.side_effect = [_response(401), _response(), _response(), _response()]
        client = mc.McpClient()
        result = client.call("x")
        self.assertEqual(result, {"ok": True, "text": "", "isError": False})
        self.auth.remint_api_key.assert_called_once_with()
        self.auth.invalidate_session.assert_not_called()
        self.assertEqual(self.requests.post.call_count, 4)
        return client, self.requests.post.call_args_list

    def test_401_oauth_remint_once_retry_once_success(self):
        _, calls = self._successful_retry()
        self.assertEqual(calls[0].kwargs["headers"]["Authorization"], "Bearer mcfw_minted")
        for request in calls[1:]:
            self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer mcfw_fresh")

    def test_401_twice_invalidates_session(self):
        self.requests.post.return_value = _response(401)
        client = mc.McpClient()
        self.assertEqual(client.call("x"), {"ok": False, "error": "seraph_login_required"})
        self.auth.remint_api_key.assert_called_once_with()
        self.auth.invalidate_session.assert_called_once_with()
        self.assertEqual(self.requests.post.call_count, 2)
        self.assertIsNone(client._session)
        self.assertIsNone(client._tools)

    def test_401_remint_none_requires_login(self):
        self.requests.post.return_value = _response(401)
        self.auth.remint_api_key.return_value = None
        self.assertEqual(mc.McpClient().call("x"),
                         {"ok": False, "error": "seraph_login_required"})
        self.auth.remint_api_key.assert_called_once_with()
        self.auth.invalidate_session.assert_called_once_with()
        self.requests.post.assert_called_once()

    def test_401_manual_keeps_key_and_reports_unauthorized(self):
        self.auth.status.return_value = _status("api_key_manual")
        self.requests.post.return_value = _response(401)
        callback = MagicMock()
        self.assertEqual(mc.McpClient(on_unauthorized=callback).call("x"),
                         {"ok": False, "error": "seraph_unauthorized"})
        self.auth.remint_api_key.assert_not_called()
        self.auth.invalidate_session.assert_not_called()
        self.auth.set_manual_api_key.assert_not_called()
        callback.assert_called_once_with()
        self.requests.post.assert_called_once()

    def test_401_legacy_no_retry(self):
        self.auth.get_api_key.return_value = None
        self.legacy.return_value = ("legacy", "api_keys_json")
        self.requests.post.return_value = _response(401)
        self.assertEqual(mc.McpClient().call("x"),
                         {"ok": False, "error": "seraph_unauthorized"})
        self.auth.remint_api_key.assert_not_called()
        self.requests.post.assert_called_once()

    def test_401_explicit_no_retry(self):
        self.requests.post.return_value = _response(401)
        self.assertEqual(mc.McpClient(api_key="ctor-key").call("x"),
                         {"ok": False, "error": "seraph_unauthorized"})
        self.auth.remint_api_key.assert_not_called()
        self.requests.post.assert_called_once()

    def test_retry_reinitializes_session(self):
        client, calls = self._successful_retry()
        self.assertEqual([request.kwargs["json"]["method"] for request in calls],
                         ["initialize", "initialize", "notifications/initialized", "tools/call"])
        self.assertNotIn("Mcp-Session-Id", calls[1].kwargs["headers"])
        self.assertEqual(client._session, "sess-1")

    def test_non_401_error_returns_ok_false(self):
        self.requests.post.side_effect = RuntimeError("boom")
        self.assertEqual(mc.McpClient().call("x"), {"ok": False, "error": "boom"})
        self.auth.remint_api_key.assert_not_called()

    def test_credential_mode_five_values(self):
        cases = [
            ("ctor-key", "minted", "api_key_oauth", ("", "none"), "api_key_explicit"),
            (None, "minted", "api_key_oauth", ("", "none"), "api_key_oauth"),
            (None, "manual", "api_key_manual", ("", "none"), "api_key_manual"),
            (None, None, "none", ("legacy", "api_keys_json"), "api_key_legacy"),
            (None, None, "none", ("", "none"), "none"),
        ]
        for explicit, minted, mode, legacy, expected in cases:
            with self.subTest(mode=expected):
                self.auth.get_api_key.return_value = minted
                self.auth.status.return_value = _status(mode)
                self.legacy.return_value = legacy
                self.assertEqual(mc.McpClient(api_key=explicit).credential_mode(), expected)

    def test_credential_status_shape(self):
        result = mc.credential_status()
        self.assertEqual(set(result), {
            "mode", "api_key_prefix", "subject", "org_id", "api_key_created_at",
            "api_key_scopes", "needs_login", "secure_storage", "source", "last_error",
        })
        self.assertIsInstance(result["api_key_scopes"], list)
        self.assertEqual(result, {
            "mode": "api_key_oauth", "api_key_prefix": "mcfw_3fa9c1e",
            "subject": "did:privy:abc", "org_id": "org_1", "api_key_created_at": 1.0,
            "api_key_scopes": ["mcp", "wallet:execute"], "needs_login": False,
            "secure_storage": True, "source": "oauth", "last_error": None,
        })
        self.requests.post.assert_not_called()

    def test_invalidate_resets_session_and_tools(self):
        self.requests.post.return_value = _response(payload={
            "jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x"}]}
        })
        client = mc.McpClient()
        expected = [{"name": "x", "description": "", "schema": {}}]
        self.assertEqual(client.tools(), {"ok": True, "tools": expected})
        self.assertEqual(client._session, "sess-1")
        self.assertEqual(client._tools, expected)
        self.assertGreater(client._tools_at, 0.0)
        client.invalidate()
        self.assertIsNone(client._session)
        self.assertIsNone(client._tools)
        self.assertEqual(client._tools_at, 0.0)

    def test_save_seraph_api_key_stores_manual_and_writes_no_json(self):
        self.assertFalse(self.path.exists())
        mc.save_seraph_api_key("mcfw_x")
        self.auth.set_manual_api_key.assert_called_once_with("mcfw_x")
        self.assertFalse(self.path.exists())

    def _legacy_file(self):
        self.path.write_text(json.dumps({"seraph_mcp_api_key": "old", "other": "keep"}),
                             encoding="utf-8")
        return self.path.read_bytes()

    def test_migrate_moves_entry_preserving_others(self):
        self._legacy_file()
        self.auth.get_api_key.return_value = None
        self.assertIs(mc.migrate_legacy_api_key(), True)
        self.auth.set_manual_api_key.assert_called_once_with("old")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"other": "keep"})

    def test_migrate_noop_when_auth_already_has_key(self):
        original = self._legacy_file()
        self.assertIs(mc.migrate_legacy_api_key(), False)
        self.assertEqual(self.path.read_bytes(), original)
        self.auth.set_manual_api_key.assert_not_called()

    def test_migrate_write_failure_keeps_json_readable(self):
        original = self._legacy_file()
        self.auth.get_api_key.return_value = None
        with patch.object(Path, "write_text", side_effect=OSError("read-only")) as write:
            with self.assertLogs(mc.logger, level="WARNING"):
                self.assertIs(mc.migrate_legacy_api_key(), True)
        write.assert_called_once()
        self.auth.set_manual_api_key.assert_called_once_with("old")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"seraph_mcp_api_key": "old", "other": "keep"})

    def test_key_provider_called_once_per_request(self):
        provider = MagicMock(return_value="mcfw_k")
        self.assertEqual(mc.McpClient(key_provider=provider).call("x"),
                         {"ok": True, "text": "", "isError": False})
        provider.assert_called_once_with()
        self.assertEqual(self.requests.post.call_count, 3)
        for request in self.requests.post.call_args_list:
            self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer mcfw_k")

    def test_empty_api_key_is_not_explicit(self):
        self.assertEqual(mc.McpClient(api_key="")._resolve_credential(),
                         ("mcfw_minted", "api_key_oauth", "oauth"))

    def _error_result(self, error):
        self.auth.get_api_key.return_value = "mcfw_SECRET_VALUE"
        self.auth.remint_api_key.return_value = None
        self.auth.status.return_value = _status(
            "api_key_manual" if error == "seraph_unauthorized" else "api_key_oauth"
        )
        self.requests.post.side_effect = RuntimeError("boom") if error == "boom" else None
        self.requests.post.return_value = _response(401)
        return mc.McpClient().call("x")

    def test_error_shape_matches_engine_contract(self):
        for error in ("seraph_login_required", "seraph_unauthorized", "boom"):
            with self.subTest(error=error):
                result = self._error_result(error)
                self.assertEqual(set(result), {"ok", "error"})
                self.assertIs(result["ok"], False)
                self.assertEqual(result["error"], error)

    def test_error_strings_never_contain_bearer(self):
        # Transport exceptions are not sanitized: this checks that the client
        # does not append its credential to an otherwise safe error message.
        for error in ("seraph_login_required", "seraph_unauthorized", "boom"):
            with self.subTest(error=error):
                result = self._error_result(error)
                self.assertEqual(result, {"ok": False, "error": error})
                for sensitive in ("mcfw_SECRET_VALUE", "Bearer", "Authorization"):
                    self.assertNotIn(sensitive, str(result))


_REAL_APP_API_KEYS_PATH = mc._app_api_keys_path


if __name__ == "__main__":
    unittest.main()
