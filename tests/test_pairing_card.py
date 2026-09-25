"""gui/pairing.py PairingCard: code tiles, auto-renewal, linked/left states."""

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from gui import pairing  # noqa: E402

_app = QApplication.instance() or QApplication([])


class PairingCardTests(unittest.TestCase):
    def setUp(self):
        self.issued = []

        def fresh():
            code = f"NEW{len(self.issued):03d}"
            self.issued.append(code)
            return "https://10.0.0.2:8000", code, f"https://10.0.0.2:8000/pair/scan?code={code}"

        self.card = pairing.PairingCard("https://10.0.0.2:8000", "ABC234", "https://10.0.0.2:8000/pair/scan?code=ABC234")
        self.card.request_new_key = fresh

    def tiles(self) -> str:
        return "".join(t.text() for t in self.card._tiles)

    def test_code_is_shown_as_tiles(self):
        self.assertEqual(self.tiles(), "ABC234")
        self.assertEqual(self.card._address.text(), "https://10.0.0.2:8000")

    def test_expired_code_is_replaced_not_closed(self):
        closed = []
        self.card.closed.connect(lambda: closed.append(True))
        self.card._issued_at = time.time() - pairing.CODE_LIFETIME - 1
        self.card._age_code()
        self.assertEqual(self.issued, ["NEW000"])
        self.assertEqual(self.tiles(), "NEW000")
        self.assertEqual(closed, [])

    def test_linked_then_left(self):
        self.card.mark_connected()
        self.assertTrue(self.card._pairing_part.isHidden())
        self.assertFalse(self.card._linked_part.isHidden())
        self.assertEqual(self.card._status.text(), "Phone linked")
        self.card.mark_disconnected()
        self.assertFalse(self.card._pairing_part.isHidden())
        self.assertEqual(self.tiles(), "NEW000")          # a fresh code straight away
        self.assertIn("left", self.card._status.text())

    def test_failed_renewal_says_so(self):
        self.card.request_new_key = lambda: None
        self.card._renew()
        self.assertIn("Couldn't make a new code", self.card._status.text())
        self.assertFalse(self.card._ager.isActive())

    def test_dismiss_emits_closed(self):
        seen = []
        self.card.closed.connect(lambda: seen.append(True))
        self.card.dismiss()
        self.assertEqual(seen, [True])


if __name__ == "__main__":
    unittest.main()
