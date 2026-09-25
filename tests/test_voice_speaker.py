"""voice/audio.py Speaker and voice/phone.py mirror_audio.

The speaker is driven by the sound card: _fill() is the device callback.
These tests call it directly with a plain bytearray standing in for the
device buffer, so no audio hardware is needed. They pin the behaviour the
GEMZ4US fixes rely on: the mic stays closed until the reply has actually
been handed to the device plus PLAYBACK_TAIL (2026-09-21), and speech mute
still lets the conversation carry on in text."""

import asyncio
import time
import unittest

from voice import audio
from voice.phone import PhoneBridge

BLOCK = audio.FRAMES * 2            # bytes per device block (int16 mono)


class _InlineLoop:
    """call_soon_threadsafe that runs the callback immediately."""
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


def _speaker(tail=0.0, muted=False):
    changes, levels = [], []
    s = audio.Speaker(changes.append, tail, on_level=levels.append)
    s._loop = _InlineLoop()
    s._muted = lambda: muted
    return s, changes, levels


def _pull(s, size=BLOCK) -> bytes:
    out = bytearray(size)
    s._fill(out, size // 2, None, None)
    return bytes(out)


class SpeakerTests(unittest.TestCase):
    def test_feed_starts_speaking(self):
        s, changes, _ = _speaker()
        s.feed(b"\x01\x00" * 10)
        self.assertTrue(s.speaking)
        self.assertEqual(changes, [True])

    def test_device_gets_exactly_one_block_padded_with_silence(self):
        s, _, _ = _speaker()
        s.feed(b"\x05\x00" * 100)                    # 200 bytes, less than a block
        out = _pull(s)
        self.assertEqual(out[:200], b"\x05\x00" * 100)
        self.assertEqual(out[200:], bytes(BLOCK - 200))
        s.feed(b"\x07\x00" * BLOCK)                   # two blocks' worth
        self.assertEqual(_pull(s), b"\x07\x00" * (BLOCK // 2))
        self.assertEqual(len(s._buffer), BLOCK)

    def test_keeps_speaking_until_turn_over_and_buffer_played(self):
        s, changes, _ = _speaker()
        s.feed(b"\x01\x00" * BLOCK)                   # two blocks
        _pull(s)
        self.assertTrue(s.speaking)                   # turn not over yet
        s.end_of_turn()
        self.assertTrue(s.speaking)                   # still one block buffered
        _pull(s)                                      # last real samples go out; no tail here
        self.assertFalse(s.speaking)
        self.assertEqual(changes, [True, False])

    def test_tail_holds_the_mic_closed_after_the_last_sample(self):
        s, _, _ = _speaker(tail=0.2)
        s.feed(b"\x01\x00" * 10)
        s.end_of_turn()
        _pull(s)
        _pull(s)
        self.assertTrue(s.speaking)                   # tail not yet elapsed
        s._last_sound = time.monotonic() - 0.25
        _pull(s)
        self.assertFalse(s.speaking)

    def test_new_audio_after_turn_end_keeps_speaking(self):
        s, _, _ = _speaker()
        s.feed(b"\x01\x00" * 10)
        s.end_of_turn()
        s.feed(b"\x01\x00" * 10)                      # the next turn has begun
        _pull(s)
        _pull(s)
        self.assertTrue(s.speaking)

    def test_speech_mute_discards_backlog_and_outputs_silence(self):
        s, _, levels = _speaker(muted=True)
        s.feed(b"\x40\x1f" * (BLOCK * 3))
        s.end_of_turn()
        out = _pull(s)
        self.assertEqual(out, bytes(BLOCK))
        self.assertEqual(len(s._buffer), 0)
        self.assertEqual(levels[-1], 0.0)
        _pull(s)
        self.assertFalse(s.speaking)

    def test_silence_drops_pending_audio(self):
        s, changes, _ = _speaker()
        s.feed(b"\x01\x00" * BLOCK)
        s.silence()
        self.assertFalse(s.speaking)
        self.assertEqual(len(s._buffer), 0)
        self.assertEqual(changes, [True, False])

    def test_loudness(self):
        self.assertEqual(audio._loudness(b""), 0.0)
        self.assertEqual(audio._loudness(bytes(100)), 0.0)
        loud = (int(12000).to_bytes(2, "little", signed=True) + int(-12000).to_bytes(2, "little", signed=True)) * 50
        self.assertEqual(audio._loudness(loud), 1.0)
        quiet = (int(1500).to_bytes(2, "little", signed=True)) * 100
        self.assertAlmostEqual(audio._loudness(quiet), 0.25, places=2)


class _SlowServer:
    def __init__(self):
        self.sent = []

    async def play_on_phone(self, chunk):
        # later chunks finish faster: without ordering they'd overtake
        await asyncio.sleep(0.01 * (10 - chunk[0]))
        self.sent.append(chunk[0])


class PhoneMirrorOrderTests(unittest.TestCase):
    def test_chunks_reach_the_phone_in_order(self):
        async def scenario():
            bridge = PhoneBridge(assistant=None)
            bridge.server = _SlowServer()
            for i in range(10):
                bridge.mirror_audio(bytes([i]))
            await bridge._last_mirror
            return bridge.server.sent

        self.assertEqual(asyncio.run(scenario()), list(range(10)))


if __name__ == "__main__":
    unittest.main()
