"""memory/profile.py — the per-companion long-term memory file."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from memory import profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(patch.object(profile, "get_data_dir", return_value=self.root))
        self.enterContext(patch.object(profile, "_today", return_value="2026-09-25"))

    def test_missing_or_corrupt_file_reads_as_empty_without_touching_it(self):
        self.assertEqual(profile.load(), {c: {} for c in profile.CATEGORIES})
        path = profile.path_for()
        path.parent.mkdir(parents=True)
        for junk in ("{nope", "[1, 2]", "null"):
            path.write_text(junk, encoding="utf-8")
            self.assertEqual(profile.load()["identity"], {})
            self.assertEqual(path.read_text(encoding="utf-8"), junk)

    def test_existing_file_format_from_earlier_releases_loads(self):
        path = profile.path_for()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"identity": {"name": {"value": "Ana", "updated": "2024-01-01"}},
                                    "sessions": [{"date": "2024-01-02", "summary": "hi"}]}), encoding="utf-8")
        data = profile.load()
        self.assertEqual(data["identity"]["name"]["value"], "Ana")
        self.assertEqual(data["projects"], {})
        self.assertEqual(profile.take_last_session()["summary"], "hi")

    def test_remember_merges_skips_blanks_truncates_and_keeps_dates_of_unchanged_values(self):
        profile.remember({"identity": {"name": "Ana"}})
        with patch.object(profile, "_today", return_value="2026-10-01"):
            data = profile.remember({
                "identity": {"name": {"value": "Ana"}, "city": "Lisbon"},
                "notes": {"long": "y" * 500, "empty": " ", "none": None},
                "preferences": {"count": 0},
            })
        self.assertEqual(data["identity"]["name"]["updated"], "2026-09-25")
        self.assertEqual(data["identity"]["city"], {"value": "Lisbon", "updated": "2026-10-01"})
        self.assertEqual(data["notes"]["long"]["value"], "y" * profile.VALUE_LIMIT + "…")
        self.assertNotIn("empty", data["notes"])
        self.assertNotIn("none", data["notes"])
        self.assertEqual(data["preferences"]["count"]["value"], "0")
        self.assertEqual(profile.load(), data)

    def test_oldest_facts_are_dropped_to_stay_within_budget(self):
        for n in range(40):
            with patch.object(profile, "_today", return_value=f"2026-{n // 28 + 1:02d}-{n % 28 + 1:02d}"):
                profile.remember({"notes": {f"fact_{n:02d}": "z" * 90}})
        data = profile.load()
        self.assertLessEqual(profile._facts_size(data), profile.FILE_BUDGET)
        self.assertIn("fact_39", data["notes"])

    def test_namespaces_are_separate_files(self):
        profile.remember({"identity": {"name": "Default"}})
        profile.remember({"identity": {"name": "Alice"}}, "alpha")
        self.assertEqual(profile.load()["identity"]["name"]["value"], "Default")
        self.assertEqual(profile.load("alpha")["identity"]["name"]["value"], "Alice")
        self.assertTrue((self.root / "memory" / "alpha" / "long_term.json").is_file())
        self.assertEqual(profile.load("beta")["identity"], {})

    def test_sessions_are_capped_and_each_is_taken_once(self):
        self.assertIsNone(profile.take_last_session("alpha"))
        profile.add_session_summary("   ", namespace="alpha")
        self.assertFalse(profile.path_for("alpha").exists())
        for text in ("one", "two", "three", "q" * 400):
            profile.add_session_summary(text, language="en", namespace="alpha")
        self.assertEqual(len(profile.load("alpha")["sessions"]), profile.SESSIONS_KEPT)
        self.assertEqual(profile.take_last_session("alpha"),
                         {"date": "2026-09-25", "summary": "q" * profile.SUMMARY_LIMIT, "language": "en"})
        self.assertEqual(profile.take_last_session("alpha")["summary"], "three")
        self.assertEqual(profile.take_last_session("alpha")["summary"], "two")
        self.assertIsNone(profile.take_last_session("alpha"))

    def test_prompt_block_lists_identity_first_and_is_empty_without_facts(self):
        self.assertEqual(profile.prompt_block(profile.load()), "")
        profile.remember({"preferences": {"favorite_food": "pizza"}, "identity": {"city": "Porto", "name": "Ana"}})
        block = profile.prompt_block(profile.load())
        self.assertLess(block.index("Name: Ana"), block.index("City: Porto"))
        self.assertIn("Favorite food — pizza", block)
        self.assertTrue(block.endswith("\n"))

    def test_forget_fact(self):
        profile.remember({"notes": {"x": "1"}})
        self.assertTrue(profile.forget_fact("notes", "x"))
        self.assertFalse(profile.forget_fact("notes", "x"))


if __name__ == "__main__":
    unittest.main()
