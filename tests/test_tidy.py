"""toolkit/files.py tidy(): the desktop "organize" and "clean" actions."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from toolkit.files import tidy


class TidyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        for name in ("notes.txt", "photo.png", ".hidden", "App.lnk"):
            (self.folder / name).write_text(name)
        (self.folder / "Projects").mkdir()

    def test_sweep_moves_only_loose_files_into_a_dated_folder(self):
        swept = self.folder / f"Swept {date.today():%Y-%m-%d}"
        message = tidy(self.folder, by="sweep")
        self.assertEqual(sorted(p.name for p in swept.iterdir()), ["notes.txt", "photo.png"])
        for kept in (".hidden", "App.lnk", "Projects"):
            self.assertTrue((self.folder / kept).exists())
        self.assertIn("2 loose file(s)", message)

    def test_second_sweep_keeps_both_copies(self):
        tidy(self.folder, by="sweep")
        (self.folder / "notes.txt").write_text("newer")
        tidy(self.folder, by="sweep")
        swept = self.folder / f"Swept {date.today():%Y-%m-%d}"
        self.assertEqual((swept / "notes.txt").read_text(), "notes.txt")
        self.assertEqual((swept / "notes (2).txt").read_text(), "newer")

    def test_nothing_to_move(self):
        empty = self.folder / "Projects"
        self.assertIn("no loose files", tidy(empty, by="sweep"))


if __name__ == "__main__":
    unittest.main()
