"""Memory persistence is redirected for BOTH default and namespaced paths."""

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from memory import memory_manager


class MemoryManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.path = self.base / "memory" / "long_term.json"
        self.enterContext(patch.object(memory_manager, "BASE_DIR", self.base))
        self.enterContext(patch.object(memory_manager, "MEMORY_PATH", self.path))
        clock = self.enterContext(patch.object(memory_manager, "datetime"))
        clock.now.return_value = datetime(2025, 1, 2)
        self.enterContext(redirect_stdout(StringIO()))

    def test_missing_and_invalid_memory_return_fresh_empty_categories(self):
        expected = {
            "identity": {}, "preferences": {}, "projects": {},
            "relationships": {}, "wishes": {}, "notes": {},
        }
        loaded = memory_manager.load_memory()
        self.assertEqual(loaded, expected)
        loaded["notes"]["local"] = "not persisted"
        self.assertEqual(memory_manager.load_memory(), expected)
        self.assertFalse(self.path.exists())

        self.path.parent.mkdir(parents=True)
        for content in ("{invalid json", "[]", "null"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                self.assertEqual(memory_manager.load_memory(), expected)
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_update_merges_truncates_and_preserves_unchanged_entries(self):
        memory_manager.save_memory({"identity": {
            "name": {"value": "Ana", "updated": "2024-01-01"},
        }})
        loaded = memory_manager.update_memory({
            "identity": {"name": "Ana", "city": "S\u00e3o Paulo"},
            "notes": {"long": "x" * 400, "blank": "  ", "missing": None},
            "preferences": {"count": 0, "enabled": False},
        })

        self.assertEqual(loaded["identity"]["name"]["updated"], "2024-01-01")
        self.assertEqual(loaded["identity"]["city"], {
            "value": "S\u00e3o Paulo", "updated": "2025-01-02",
        })
        self.assertEqual(loaded["notes"]["long"]["value"], "x" * 380 + "\u2026")
        self.assertNotIn("blank", loaded["notes"])
        self.assertNotIn("missing", loaded["notes"])
        self.assertEqual(loaded["preferences"]["count"]["value"], "0")
        self.assertEqual(loaded["preferences"]["enabled"]["value"], "False")
        self.assertEqual(loaded["projects"], {})
        self.assertEqual(memory_manager.load_memory(), loaded)

    def test_namespaced_memory_is_separate_from_default_and_other_companions(self):
        for namespace, name in ((None, "Default"), ("alpha", "Alice"), ("beta", "Bob")):
            memory_manager.update_memory({"identity": {"name": name}}, namespace)

        for namespace, name in ((None, "Default"), ("alpha", "Alice"), ("beta", "Bob")):
            with self.subTest(namespace=namespace):
                loaded = memory_manager.load_memory(namespace)
                self.assertEqual(loaded["identity"]["name"]["value"], name)
                expected_path = self.path if namespace is None else (
                    self.base / "memory" / namespace / "long_term.json"
                )
                self.assertTrue(expected_path.is_file())
        self.assertEqual(memory_manager.load_memory("unused")["identity"], {})

    def test_session_limit_truncation_and_pop_persist_consumption(self):
        self.assertIsNone(memory_manager.pop_last_session("alpha"))
        memory_manager.save_session_summary("   ", namespace="alpha")
        self.assertFalse((self.base / "memory").exists())

        for summary in ("first", "second", "third", "x" * 300):
            memory_manager.save_session_summary(summary, language="pt", namespace="alpha")

        sessions = memory_manager.load_memory("alpha")["sessions"]
        self.assertEqual([entry["summary"] for entry in sessions], ["second", "third", "x" * 280])
        self.assertEqual(memory_manager.pop_last_session("alpha"), {
            "date": "2025-01-02", "summary": "x" * 280, "language": "pt",
        })
        self.assertEqual(len(memory_manager.load_memory("alpha")["sessions"]), 2)
        self.assertEqual(memory_manager.pop_last_session("alpha")["summary"], "third")
        self.assertEqual(memory_manager.pop_last_session("alpha")["summary"], "second")
        self.assertIsNone(memory_manager.pop_last_session("alpha"))
        self.assertEqual(memory_manager.load_memory("alpha")["sessions"], [])
        self.assertFalse(self.path.exists())
