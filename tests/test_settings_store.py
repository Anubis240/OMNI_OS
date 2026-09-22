"""Settings contracts using only disposable files, never user settings."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
import time
from unittest.mock import patch

from core import settings_store


class SettingsStoreTests(unittest.TestCase):
    def test_default_settings_has_seraph_auth_block_with_all_fields(self):
        auth = settings_store.DEFAULT_SETTINGS["seraph_auth"]
        self.assertEqual(set(auth), {
            "api_key", "api_key_id", "api_key_prefix", "api_key_name",
            "api_key_created_at", "api_key_source", "api_key_scopes",
            "device_id", "client_id", "client_id_issued_at",
            "registered_redirect_uri", "access_token", "access_expires_at",
            "refresh_token", "refresh_issued_at", "scope", "subject", "org_id",
            "issuer", "authorization_endpoint", "token_endpoint",
            "registration_endpoint", "revocation_endpoint", "metadata_fetched_at",
        })
        self.assertIsInstance(auth["api_key_scopes"], list)
        self.assertEqual(auth["api_key_scopes"], [])
        for key, value in auth.items():
            if key != "api_key_scopes":
                self.assertIsNone(value, key)

    def test_legacy_file_gains_defaults(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"dashboard_port": 9001}), encoding="utf-8")
        loaded = settings_store.load_settings()
        self.assertEqual(loaded["seraph_auth"], settings_store.DEFAULT_SETTINGS["seraph_auth"])
        self.assertEqual(loaded["dashboard_port"], 9001)

    def test_update_settings_applies_and_persists(self):
        def mutate(settings):
            settings["dashboard_port"] = 9123
            return "ignored"

        saved = settings_store.update_settings(mutate)
        self.assertEqual(saved["dashboard_port"], 9123)
        self.assertEqual(settings_store.load_settings(), saved)

    def test_serializes_8_concurrent_writers(self):
        settings_store.save_settings({"integrations": {}})
        barrier = threading.Barrier(8)

        def write(index):
            barrier.wait(timeout=5)

            def increment(settings):
                counters = settings["integrations"]
                key = str(index)
                counters[key] = counters.get(key, 0) + 1
                # Release the GIL while holding the settings lock: without
                # serialization, concurrent snapshots lose other counters.
                time.sleep(0.02)

            settings_store.update_settings(increment)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(8)))
        self.assertEqual(settings_store.load_settings()["integrations"], {
            str(index): 1 for index in range(8)
        })

    def test_mutator_exception_leaves_file_intact(self):
        settings_store.save_settings({"dashboard_port": 9001})
        before = self.path.read_bytes()
        error = ValueError("mutation failed")

        def mutate(settings):
            settings["dashboard_port"] = 9123
            raise error

        with self.assertRaises(ValueError) as raised:
            settings_store.update_settings(mutate)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.path.read_bytes(), before)

    def test_reentrant(self):
        def mutate(settings):
            # A bounded acquisition makes a regression to Lock fail rather
            # than leaving a blocked thread behind in the test process.
            acquired = settings_store._SETTINGS_LOCK.acquire(timeout=1)
            self.assertTrue(acquired, "settings lock must be reentrant")
            try:
                settings["dashboard_port"] = 9123
            finally:
                settings_store._SETTINGS_LOCK.release()

        settings_store.update_settings(mutate)
        self.assertEqual(settings_store.load_settings()["dashboard_port"], 9123)

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "config" / "settings.json"
        self.enterContext(patch.object(settings_store, "SETTINGS_PATH", self.path))

    def test_missing_settings_return_independent_defaults_without_writing(self):
        first = settings_store.load_settings()
        self.assertEqual(first["dashboard_port"], 8000)
        self.assertFalse(first["trader"]["enabled"])
        self.assertEqual(first["companions"], [])
        first["api_keys"]["openai"] = "test-only"
        first["skills"].append({"name": "test"})

        second = settings_store.load_settings()
        self.assertEqual(second["api_keys"]["openai"], "")
        self.assertEqual(second["skills"], [])
        self.assertEqual(settings_store.DEFAULT_SETTINGS["api_keys"]["openai"], "")
        self.assertFalse(self.path.exists())

    def test_save_and_load_merge_partial_settings_and_ignore_unknown_keys(self):
        settings_store.save_settings({
            "api_keys": {"openai": "synthetic-token"},
            "claude_agent": {"enabled": True},
            "dashboard_port": 8123,
            "skills": [{"name": "Local skill", "enabled": True}],
            "unknown_setting": "ignored",
        })

        loaded = settings_store.load_settings()
        self.assertEqual(loaded["api_keys"], {
            "openai": "synthetic-token", "anthropic": "",
        })
        self.assertTrue(loaded["claude_agent"]["enabled"])
        self.assertEqual(loaded["claude_agent"]["cliPath"], "")
        self.assertEqual(loaded["dashboard_port"], 8123)
        self.assertEqual(loaded["skills"], [{"name": "Local skill", "enabled": True}])
        self.assertNotIn("unknown_setting", loaded)

        # 2026-09-12 security audit fix: settings.json is now DPAPI-encrypted
        # at rest (see core/settings_store.py's _ENC_MAGIC/_encrypt_bytes) —
        # the raw file is no longer plain JSON where DPAPI is available
        # (every real Windows install; not necessarily true on a non-Windows
        # CI runner, which is why this branches instead of assuming either).
        raw = self.path.read_bytes()
        if settings_store._dpapi_available():
            self.assertTrue(raw.startswith(settings_store._ENC_MAGIC))
            decrypted = json.loads(
                settings_store._decrypt_bytes(raw[len(settings_store._ENC_MAGIC):]).decode("utf-8")
            )
            self.assertEqual(decrypted["dashboard_port"], 8123)
        else:
            self.assertEqual(json.loads(raw.decode("utf-8"))["dashboard_port"], 8123)

    def test_legacy_plaintext_settings_still_load_and_get_encrypted_on_next_save(self):
        # Every install from before 2026-09-12 has a plain-JSON settings.json
        # on disk with no _ENC_MAGIC prefix — this must keep loading
        # correctly with no separate migration step, and then start getting
        # encrypted (where DPAPI is available) the next time anything saves.
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"dashboard_port": 9001}), encoding="utf-8")

        loaded = settings_store.load_settings()
        self.assertEqual(loaded["dashboard_port"], 9001)

        settings_store.save_settings(loaded)
        raw = self.path.read_bytes()
        if settings_store._dpapi_available():
            self.assertTrue(raw.startswith(settings_store._ENC_MAGIC))
        else:
            self.assertFalse(raw.startswith(settings_store._ENC_MAGIC))
        self.assertEqual(settings_store.load_settings()["dashboard_port"], 9001)

    def test_invalid_json_falls_back_to_defaults_without_overwriting_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{invalid json", encoding="utf-8")

        self.assertEqual(settings_store.load_settings(), settings_store.DEFAULT_SETTINGS)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{invalid json")

    def test_placeholders_resolve_known_keys_and_preserve_unknown_names(self):
        settings_store.save_settings({"custom_api_keys": [
            {"name": "LOCAL_TOKEN", "value": "synthetic-value"},
        ]})
        self.assertEqual(
            settings_store.resolve_key_placeholders("{{LOCAL_TOKEN}}/{{MISSING}}/{{LOCAL_TOKEN}}"),
            "synthetic-value/{{MISSING}}/synthetic-value",
        )
        self.assertEqual(settings_store.resolve_key_placeholders(""), "")
        self.assertEqual(settings_store.resolve_key_placeholders("plain text"), "plain text")
        self.assertEqual(
            settings_store.resolve_key_placeholders("{{LOCAL_TOKEN}}", {
                "custom_api_keys": [{"name": "LOCAL_TOKEN", "value": "explicit"}],
            }),
            "explicit",
        )
