"""Settings contracts using only disposable files, never user settings."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from core import settings_store


class SettingsStoreTests(unittest.TestCase):
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
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["dashboard_port"], 8123)

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
