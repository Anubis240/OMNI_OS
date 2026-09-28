"""main.py voice-health notices — GEMZ4US 2026-09-25.

N42: the desktop mic went silent after an app restart with no message at
all (Listening on, no "You:" line) — _mic_problem() now names it.
N30/B: a spoken message sent just before a "Connection lost" vanished —
_voice_lost_in_disconnect() now asks the user to repeat it.
"""

import unittest

import numpy as np

import main


class PeakTests(unittest.TestCase):
    def test_peak_handles_empty_and_extreme_samples(self):
        self.assertEqual(main._peak(np.array([], dtype=np.int16)), 0)
        self.assertEqual(main._peak(np.array([3, -7, 5], dtype=np.int16)), 7)
        self.assertEqual(main._peak(np.array([-32768], dtype=np.int16)), 32768)   # no int16 overflow


class MicProblemTests(unittest.TestCase):
    def test_too_early_to_tell(self):
        self.assertIsNone(main._mic_problem(now=103.0, listening_since=100.0, last_frame_at=0.0, last_sound_at=0.0))

    def test_no_frames_at_all(self):
        self.assertIn("isn't delivering any audio",
                      main._mic_problem(now=106.0, listening_since=100.0, last_frame_at=50.0, last_sound_at=50.0))

    def test_frames_but_only_digital_silence(self):
        self.assertIn("only silence",
                      main._mic_problem(now=106.0, listening_since=100.0, last_frame_at=105.9, last_sound_at=50.0))

    def test_healthy_mic(self):
        self.assertIsNone(main._mic_problem(now=106.0, listening_since=100.0, last_frame_at=105.9, last_sound_at=105.0))

    def test_silence_after_sound_is_reported(self):
        # GEMZ4US 2026-09-27: Listening at 100, sound until 105, mic muted in
        # Windows. The old check saw "sound since listening began" and stayed quiet.
        self.assertIsNone(main._mic_problem(now=108.0, listening_since=100.0, last_frame_at=107.9, last_sound_at=105.0))
        self.assertIn("only silence",
                      main._mic_problem(now=150.0, listening_since=100.0, last_frame_at=149.9, last_sound_at=105.0))

    def test_frames_stopping_mid_session_is_reported(self):
        self.assertIn("isn't delivering any audio",
                      main._mic_problem(now=150.0, listening_since=100.0, last_frame_at=120.0, last_sound_at=120.0))

    def test_not_listening(self):
        self.assertIsNone(main._mic_problem(now=106.0, listening_since=None, last_frame_at=0.0, last_sound_at=0.0))


class MicWatchTests(unittest.TestCase):
    def test_one_notice_per_dropout_and_again_after_recovery(self):
        watch = main._MicWatch()
        self.assertIsNone(watch.check(100.0, True, 100.0, 100.0))             # listening starts, sound
        self.assertIsNone(watch.check(104.0, True, 104.0, 104.0))
        self.assertIsNotNone(watch.check(170.0, True, 169.9, 104.0))          # muted in Windows
        self.assertIsNone(watch.check(171.0, True, 170.9, 104.0))             # same dropout: once
        self.assertIsNone(watch.check(172.0, True, 171.9, 171.9))             # sound is back
        self.assertIsNotNone(watch.check(240.0, True, 239.9, 172.0))          # a new dropout

    def test_at_most_one_notice_a_minute(self):
        watch = main._MicWatch()
        watch.check(100.0, True, 100.0, 100.0)
        self.assertIsNotNone(watch.check(110.0, True, 109.9, 100.0))
        watch.check(111.0, True, 110.9, 110.9)                               # brief recovery
        self.assertIsNone(watch.check(120.0, True, 119.9, 111.0))             # dropout again, too soon
        self.assertIsNotNone(watch.check(171.0, True, 170.9, 111.0))          # still silent a minute on

    def test_not_listening_resets(self):
        watch = main._MicWatch()
        self.assertIsNone(watch.check(100.0, False, 0.0, 0.0))
        self.assertIsNone(watch.check(200.0, True, 0.0, 0.0))                 # listening only just started
        self.assertIsNotNone(watch.check(206.0, True, 0.0, 0.0))


class LostVoiceTests(unittest.TestCase):
    def test_recent_speech_with_no_transcript_was_lost(self):
        self.assertTrue(main._voice_lost_in_disconnect(now=110.0, speech_started_at=100.0,
                                                       last_speech_at=102.0, last_heard_at=90.0))

    def test_transcribed_speech_was_not_lost(self):
        self.assertFalse(main._voice_lost_in_disconnect(now=110.0, speech_started_at=100.0,
                                                        last_speech_at=102.0, last_heard_at=101.0))

    def test_old_speech_is_not_blamed_on_this_disconnect(self):
        self.assertFalse(main._voice_lost_in_disconnect(now=200.0, speech_started_at=100.0,
                                                        last_speech_at=102.0, last_heard_at=90.0))

    def test_no_speech_at_all(self):
        self.assertFalse(main._voice_lost_in_disconnect(now=110.0, speech_started_at=None,
                                                        last_speech_at=0.0, last_heard_at=0.0))


class SpeechTrackingTests(unittest.TestCase):
    def setUp(self):
        self.live = object.__new__(main.JarvisLive)
        self.live._speech_started_at = None
        self.live._last_speech_at = 0.0

    def test_background_noise_is_not_speech(self):
        self.live._note_outgoing_audio(main.SPEECH_PEAK)
        self.assertIsNone(self.live._speech_started_at)

    def test_a_pause_starts_a_new_message(self):
        self.live._note_outgoing_audio(main.SPEECH_PEAK + 1)
        first = self.live._speech_started_at
        self.assertIsNotNone(first)
        self.live._note_outgoing_audio(main.SPEECH_PEAK + 1)          # same message, no pause
        self.assertEqual(self.live._speech_started_at, first)
        # a pause: move the earlier message back in time (the clock may not tick between calls)
        self.live._speech_started_at = first - 60
        self.live._last_speech_at -= main.SPEECH_GAP_S + 0.1
        self.live._note_outgoing_audio(main.SPEECH_PEAK + 1)
        self.assertGreaterEqual(self.live._speech_started_at, first)


if __name__ == "__main__":
    unittest.main()
