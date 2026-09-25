"""The Remote Dashboard (phone) side of the voice session.

The dashboard server lives for the whole app lifetime, across reconnects,
so these loops do too; they look up the *current* session on every item.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

from voice import timeouts

if TYPE_CHECKING:
    from voice.session import Assistant

PHONE_IDLE = 1.0   # seconds without phone audio before the phone counts as silent


class PhoneBridge:
    def __init__(self, assistant: "Assistant"):
        self.a = assistant
        self.server = None
        self.talking = False   # phone mic currently streaming (the PC mic yields to it)
        self._last_mirror: asyncio.Task | None = None

    # --- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Start the dashboard server if its optional dependencies are installed."""
        ui = self.a.ui
        try:
            from dashboard.server import LinkHooks, PhoneLink
        except Exception as err:
            print(f"[phone] dashboard unavailable: {err}")
            return

        def joined():
            ui.post("SYS: Your phone is linked — Remote Dashboard is live.")
            ui.notify_phone_connected()

        def left():
            ui.post("SYS: Your phone has left the Remote Dashboard.")
            ui.notify_phone_disconnected()

        def report(message: str) -> None:
            ui.post(f"SYS: Remote Dashboard {message}")

        self.server = PhoneLink(LinkHooks(phone_joined=joined, phone_left=left, failed=report, warning=report,
                                          trader_state=ui.get_trader_state, trader_action=ui.run_trader_action))
        asyncio.create_task(self.server.run())
        asyncio.create_task(self._typed_messages())
        asyncio.create_task(self._voice())

    def pairing(self):
        """(url, code, QR link) for the pairing card, or None with the reason logged."""
        if self.server is None:
            self.a.ui.post('SYS: Remote Dashboard unavailable — install fastapi, "uvicorn[standard]" and qrcode[pil].')
            return None
        if not self.server.running:
            why = self.server.start_error or "it hasn't started yet — try again in a moment."
            self.a.ui.post(f"SYS: Remote Dashboard isn't running: {why}")
            return None
        code, url = self.server.issue_pairing_code(), self.server.base_url
        return url, code, f"{url}/pair/scan?code={code}"

    # --- outbound -------------------------------------------------------
    def post(self, message: dict) -> None:
        if self.server is not None and self.a.loop is not None:
            asyncio.run_coroutine_threadsafe(self.server.publish(message), self.a.loop)

    def send_image(self, image_bytes: bytes, mime_type: str) -> None:
        """Called from generate_image's worker thread."""
        encoded = base64.b64encode(image_bytes).decode("ascii")
        self.post({"type": "image", "data": f"data:{mime_type};base64,{encoded}"})

    def mirror_audio(self, chunk: bytes) -> None:
        """Forward reply audio to the phone. Chunks arrive in bursts, so each
        send waits for the one before it — the phone must get them in order."""
        if self.server is None:
            return
        before = self._last_mirror

        async def send() -> None:
            if before is not None and not before.done():
                await asyncio.wait({before})
            await self.server.play_on_phone(chunk)

        self._last_mirror = asyncio.create_task(send())

    # --- inbound --------------------------------------------------------
    async def _typed_messages(self) -> None:
        """Phone-typed text → the live session."""
        inbox = self.server.inbox
        while True:
            text = await inbox.get()
            if not text:
                continue
            try:
                for _ in range(80):   # give a fresh connection up to 8 s to come up
                    if self.a.session is not None:
                        break
                    await asyncio.sleep(0.1)
                if self.a.session is None:
                    self.a.ui.post(f"SYS: Phone message dropped (no active session): {text}")
                    continue
                # Echo it to every connected client: typed turns have no input
                # transcription, so nothing else would show them.
                await self.server.publish({"type": "you", "text": text})
                self.a.ui.post(f"[Phone]: {text}")
                await self.a.send_text(text)
            except Exception as err:
                self.a.ui.post(f"SYS: Phone message failed ({err}).")

    async def _voice(self) -> None:
        """Phone mic PCM → the live session, same path as the PC mic.

        The phone is tap-to-talk: its stream stops dead on release, with no
        trailing silence for Gemini's voice detection to end the turn on, so
        a finished utterance could sit open and merge into the next one
        (GEMZ4US N30, 2026-09-23). When the phone goes quiet we send
        audio_stream_end to close it explicitly. Chunks that can't be
        delivered are reported once per episode, not per chunk.
        """
        inbox = self.server.mic_frames
        last_problem = None
        while True:
            try:
                chunk = await asyncio.wait_for(inbox.get(), timeout=PHONE_IDLE)
            except asyncio.TimeoutError:
                if self.talking and self.a.session is not None:
                    try:
                        await asyncio.wait_for(self.a.session.send_realtime_input(audio_stream_end=True),
                                               timeout=timeouts.SEND)
                    except Exception as err:
                        self.a.ui.post(f"SYS: Phone audio_stream_end signal failed — {err}")
                self.talking = False
                continue
            self.talking = True
            if self.a.ui.mic_muted:
                continue
            problem = self.a.queue_audio(chunk)
            if problem and problem != last_problem:
                self.a.ui.post(f"SYS: Phone audio dropped — {problem}.")
            last_problem = problem
