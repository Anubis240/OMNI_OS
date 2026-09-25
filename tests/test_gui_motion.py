"""gui/chatlog.py fade-in and gui/orb.py motion, run headless (offscreen Qt)."""

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QTextCursor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from gui.chatlog import ChatLog  # noqa: E402
from gui.orb import OrbView  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _app.processEvents()
        time.sleep(0.005)


def _alpha_at(log: ChatLog, position: int) -> int:
    cursor = QTextCursor(log.document())
    cursor.setPosition(position + 1)
    return cursor.charFormat().foreground().color().alpha()


class ChatLogFadeTests(unittest.TestCase):
    def test_line_appears_whole_then_fades_in(self):
        log = ChatLog()
        log.post("You: hello there")
        _app.processEvents()
        self.assertIn("You: hello there", log.toPlainText())   # whole line at once
        self.assertLess(_alpha_at(log, 0), 128)                 # still faint
        _pump(0.6)
        self.assertEqual(_alpha_at(log, 0), 255)
        self.assertFalse(log._fade_clock.isActive())

    def test_several_lines_fade_independently_and_keep_order(self):
        log = ChatLog()
        for text in ("SYS: one", "Omni: two", "ERR: three"):
            log.post(text)
        _app.processEvents()
        self.assertEqual(log.toPlainText().splitlines(), ["SYS: one", "Omni: two", "ERR: three"])
        _pump(0.6)
        self.assertFalse(log._fades)

    def test_links_are_shown_as_markup(self):
        log = ChatLog()
        log.post('Ready: <a href="https://example.test/f">report.pdf</a>')
        _app.processEvents()
        self.assertIn("report.pdf", log.toPlainText())
        self.assertNotIn("<a href", log.toPlainText())


class OrbMotionTests(unittest.TestCase):
    def _run(self, orb: OrbView, seconds: float) -> None:
        _pump(seconds)

    def test_speech_loudness_swells_the_orb(self):
        orb = OrbView()
        orb.speaking, orb.voice_level = True, 1.0
        self._run(orb, 0.4)
        self.assertGreater(orb._scale, 1.08)
        loud_envelope = orb._envelope
        orb.voice_level = 0.0
        self._run(orb, 0.1)
        self.assertLess(orb._envelope, loud_envelope)             # releases, gently

    def test_idle_breath_stays_small(self):
        orb = OrbView()
        self._run(orb, 0.5)
        self.assertLess(abs(orb._scale - 1.0), 0.02)

    def test_muted_dims(self):
        orb = OrbView()
        orb.muted = True
        self._run(orb, 1.5)
        self.assertLess(orb._light, 0.2)
        self.assertLess(orb._scale, 1.0)


if __name__ == "__main__":
    unittest.main()
