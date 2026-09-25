"""Microphone in, speaker out — raw 16-bit mono PCM at Gemini Live's rates."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

MIC_RATE = 16_000
SPEAKER_RATE = 24_000
FRAMES = 1024


def preferred_input_device() -> int | None:
    """A built-in 'Microphone Array' if there is one; otherwise the system
    default. (On some laptops the default input is an empty headset jack.)"""
    try:
        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0 and "microphone array" in dev["name"].lower():
                return index
    except Exception:
        pass
    return None


async def capture(send: Callable[[bytes], None], allowed: Callable[[], bool],
                  report: Callable[[str], None]) -> None:
    """Stream mic frames to `send` whenever `allowed()` is true, until cancelled.

    A mic that can't be opened (no device, or Windows' microphone privacy
    switch is off) is reported once and then ignored — typed input, the
    phone, tools and playback all keep working without it.
    """
    loop = asyncio.get_running_loop()

    def on_frames(indata, _frames, _time, _status):
        if allowed():
            loop.call_soon_threadsafe(send, bytes(indata))

    try:
        with sd.RawInputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=FRAMES,
                               device=preferred_input_device(), callback=on_frames):
            await asyncio.Event().wait()   # runs until the task is cancelled
    except asyncio.CancelledError:
        raise
    except Exception as err:
        report(f"SYS: Microphone unavailable ({err}) — voice input from this PC is off for this "
               "session. Check Settings → Privacy & security → Microphone → \"Let desktop apps "
               "access your microphone\" and your input device, then restart Omni-OS. Typed "
               "input, the phone mic and tools still work.")


class Speaker:
    """Reply audio out, pulled by the sound card rather than pushed at it.

    Gemini's chunks are appended to a byte buffer (feed()); the output
    stream's callback takes exactly what the device asks for each block and
    pads with silence when the buffer is short. Because that callback only
    ever hands over samples the device is about to play, "speaking" is
    decided there too: it ends once the turn is over, the buffer is empty,
    and `tail` seconds have passed since the last real sample went out —
    the device still has that audio queued, and the mic must not hear the
    end of the reply (GEMZ4US 2026-09-21).

    `on_level` gets the loudness (0..1) of each block handed to the device,
    for the orb.
    """

    def __init__(self, on_change: Callable[[bool], None], tail: float,
                 on_level: Callable[[float], None] | None = None):
        self.speaking = False
        self._on_change = on_change
        self._on_level = on_level
        self._tail = tail
        self._buffer = bytearray()
        self._guard = threading.Lock()
        self._turn_over = False
        self._last_sound = 0.0
        self._muted: Callable[[], bool] = lambda: False
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- event-loop side ----------------------------------------------------------
    def feed(self, pcm: bytes) -> None:
        with self._guard:
            self._buffer += pcm
            self._turn_over = False
        self._mark(True)

    def end_of_turn(self) -> None:
        with self._guard:
            self._turn_over = True

    def silence(self) -> None:
        """Drop anything not yet played and stop counting as speaking."""
        with self._guard:
            self._buffer.clear()
            self._turn_over = True
        self._mark(False)

    def _mark(self, speaking: bool) -> None:
        if speaking != self.speaking:
            self.speaking = speaking
            self._on_change(speaking)

    async def run(self, muted: Callable[[], bool]) -> None:
        """Keep the output device open until cancelled."""
        self._muted = muted
        self._loop = asyncio.get_running_loop()
        try:
            with sd.RawOutputStream(samplerate=SPEAKER_RATE, channels=1, dtype="int16",
                                    blocksize=FRAMES, callback=self._fill):
                await asyncio.Event().wait()
        finally:
            self.silence()

    # --- device side (sounddevice's callback thread) ---------------------------------
    def _fill(self, outdata, _frames, _time, _status) -> None:
        wanted = len(outdata)
        muted = self._muted()
        with self._guard:
            # With speech muted, the whole backlog is discarded at once, so the
            # mic reopens as soon as the tail has passed instead of waiting out
            # audio nobody hears.
            take = len(self._buffer) if muted else min(wanted, len(self._buffer))
            block = bytes(self._buffer[:take])
            del self._buffer[:take]
            finished = self._turn_over and not self._buffer
        audible = b"" if muted else block
        outdata[:len(audible)] = audible
        outdata[len(audible):] = bytes(wanted - len(audible))

        now = time.monotonic()
        if block:
            self._last_sound = now
        if self._on_level is not None:
            self._on_level(_loudness(audible))
        if self.speaking and finished and now - self._last_sound >= self._tail and self._loop is not None:
            self._loop.call_soon_threadsafe(self._end_if_still_quiet)

    def _end_if_still_quiet(self) -> None:
        with self._guard:
            quiet = self._turn_over and not self._buffer
        if quiet:
            self._mark(False)


def _loudness(pcm: bytes) -> float:
    """RMS of 16-bit PCM scaled so ordinary speech lands around 0.3–0.9."""
    if len(pcm) < 2:
        return 0.0
    samples = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32)
    return float(min(1.0, np.sqrt(np.mean(samples * samples)) / 6000.0))
