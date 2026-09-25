"""The closed-source build must not pull in GPL-only components: the Qt
binding is PySide6 (LGPL), and PyQt plus PyAutoGUI's optional GPL helpers
are excluded from the bundle."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class LicenceHygieneTests(unittest.TestCase):
    def test_requirements_use_pyside_not_pyqt(self):
        names = [re.split(r"[\s;=<>\[]", line.strip())[0].lower()
                 for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        self.assertIn("pyside6", names)
        self.assertFalse([n for n in names if n.startswith("pyqt")])

    def test_no_source_imports_pyqt(self):
        offenders = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
                     if not any(part.startswith((".venv", "venv", "build", "dist")) for part in p.parts)
                     and re.search(r"^\s*(from|import)\s+PyQt\d", p.read_text(encoding="utf-8", errors="ignore"), re.M)]
        self.assertEqual(offenders, [])

    def test_build_excludes_gpl_modules(self):
        spec = (ROOT / "omni-os.spec").read_text(encoding="utf-8")
        match = re.search(r"^GPL_EXCLUDES = \[(.*?)\]", spec, re.M)
        self.assertIsNotNone(match)
        for name in ("PyQt6", "PyQt5", "pymsgbox", "mouseinfo"):
            self.assertIn(f'"{name}"', match.group(1))
        self.assertIn("excludes=GPL_EXCLUDES", spec)


if __name__ == "__main__":
    unittest.main()
