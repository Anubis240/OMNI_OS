"""Microphone in, speaker out — raw 16-bit mono PCM at Gemini Live's rates."""

from __future__ import annotations

import asyncio
import time
from typing import Callable

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
    """Plays reply audio and tracks whether the assistant is audibly talking.

    `speaking` stays true until the reply has finished *and* PLAYBACK_TAIL
    has passed since the last chunk was written: stream.write() returns once
    audio is buffered, not once it's heard, so the mic must wait a moment
    longer or it hears the end of the reply (GEMZ4US 2026-09-21).
    """

    def __init__(self, on_change: Callable[[bool], None], tail: float):
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.turn_finished = asyncio.Event()
        self.speaking = False
        self._on_change = on_change
        self._tail = tail

    def _set(self, value: bool) -> None:
        if value != self.speaking:
            self.speaking = value
            self._on_change(value)

    def stop_speaking(self) -> None:
        self._set(False)

    async def run(self, muted: Callable[[], bool], mirror: Callable[[bytes], None] | None = None) -> None:
        stream = sd.RawOutputStream(samplerate=SPEAKER_RATE, channels=1, dtype="int16", blocksize=FRAMES)
        stream.start()
        last_write = 0.0
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if (self.speaking and self.turn_finished.is_set() and self.queue.empty()
                            and time.monotonic() - last_write >= self._tail):
                        self.turn_finished.clear()
                        self._set(False)
                    continue
                self._set(True)
                if not muted():
                    await asyncio.to_thread(stream.write, chunk)
                last_write = time.monotonic()
                if mirror is not None:
                    mirror(chunk)
        finally:
            self._set(False)
            stream.stop()
            stream.close()
