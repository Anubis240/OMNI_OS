"""Assistant — keeps one Gemini Live session running and wires it to the app.

Each connection runs five concurrent jobs (send, mic, receive, speaker,
reconnect-watch). Any failure — or an explicit reconnect request — tears
the connection down and a new one starts three seconds later; the session
resumption handle carries the conversation across.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
import traceback

from google import genai
from google.genai import types

from actions.launch_trader import launch_trader
from core import settings_store
from memory import profile
from toolkit import llm
from voice import audio, prompt, timeouts
from voice.dispatch import ToolRouter
from voice.phone import PhoneBridge

_CONTROL_TOKENS = re.compile(r"<ctrl\d+>|[\x00-\x08\x0b-\x1f]", re.IGNORECASE)


class Reconnect(Exception):
    """Drop the current connection and start a fresh one."""


def _clean(text: str) -> str:
    return _CONTROL_TOKENS.sub("", text or "").strip()


class Assistant:
    def __init__(self, ui, voice_loader):
        self.ui = ui
        self._voice_loader = voice_loader
        self.session = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.namespace: str | None = None
        self.companion: dict | None = None
        self.tools = ToolRouter(self)
        self.phone = PhoneBridge(self)
        self._outbox: asyncio.Queue | None = None
        self._speaker: audio.Speaker | None = None
        self._reconnect = asyncio.Event()
        self._awaiting_reply_since: float | None = None   # arms the REPLY_IDLE watchdog
        self._resume_handle: str | None = None
        self._transcript: list[str] = []                  # this connection's turns, for the summary
        self._connections = 0

        ui.on_text_command = self._typed
        ui.on_voice_change = lambda _name: self.request_reconnect()
        ui.on_companions_changed = self.request_reconnect
        ui.on_remote_clicked = self.phone.pairing
        ui.on_trader_clicked = lambda: launch_trader(player=ui)
        ui.on_always_listening_toggled = lambda on: on and threading.Thread(target=_chime, daemon=True).start()

    @property
    def dashboard(self):
        return self.phone.server

    # --- things other parts of the app call ----------------------------
    def request_reconnect(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._reconnect.set)

    def speak(self, text: str) -> None:
        """Have the assistant say something (thread-safe; no-op when offline)."""
        if self.loop is not None and self.session is not None:
            asyncio.run_coroutine_threadsafe(self.send_text(text), self.loop)

    async def send_text(self, text: str) -> bool:
        """Send a user turn. A failed or stalled send means the connection is
        bad, so it triggers a reconnect instead of silently doing nothing."""
        if self.session is None:
            return False
        self._awaiting_reply_since = time.monotonic()
        try:
            await asyncio.wait_for(
                self.session.send_client_content(turns={"parts": [{"text": text}]}, turn_complete=True),
                timeout=timeouts.SEND)
            return True
        except asyncio.TimeoutError:
            self.ui.write_log("SYS: Lost the connection sending a message — reconnecting.")
        except Exception as err:
            self.ui.write_log(f"SYS: Failed to send ({err}) — reconnecting.")
        self.request_reconnect()
        return False

    def queue_audio(self, pcm: bytes) -> str | None:
        """Queue mic/phone audio for the session; returns why it couldn't be, if so."""
        if self.session is None or self._outbox is None:
            return "no active voice session yet"
        try:
            self._outbox.put_nowait(pcm)
        except asyncio.QueueFull:
            return "outgoing queue full"
        return None

    def delegate(self, agent_name: str, task: str) -> str:
        if not agent_name or not task:
            return "I need both a sub-agent name and a task."
        settings = settings_store.load_settings()
        agent = next((c for c in settings["companions"] if c.get("backend") in prompt.AGENT_BACKENDS
                      and c["name"].lower() == agent_name.lower()), None)
        if agent is None:
            return f"There's no sub-agent called {agent_name}."
        send = _agent_sender(agent["backend"])
        asyncio.run_coroutine_threadsafe(self._delegated(agent, send, task), self.loop)
        return f"{agent['name']} is on it — I'll tell you when it's done."

    async def close(self) -> None:
        self.ui.write_log("SYS: Shutdown requested.")
        await self._summarize_conversation()
        self.speak("Goodbye.")
        threading.Timer(1.5, lambda: os._exit(0)).start()

    # --- typed input (desktop) -----------------------------------------
    def _typed(self, text: str) -> None:
        if self.loop is None:
            return
        settings = settings_store.load_settings()
        active = next((c for c in settings.get("companions", [])
                       if c.get("id") == settings.get("active_companion_id")), None)
        send = _agent_sender(active.get("backend")) if active else None
        if active and send and active.get("enabled", True):
            # A text-only sub-agent companion is active: it answers the text box itself.
            asyncio.run_coroutine_threadsafe(self._agent_reply(active, send, text), self.loop)
        elif self.session is not None:
            asyncio.run_coroutine_threadsafe(self.send_text(text), self.loop)

    async def _agent_reply(self, companion, send, text):
        self.ui.write_log(f"SYS: {companion['name']} is thinking…")
        self.ui.write_log(f"{companion['name']}: {await send(companion, text)}")

    async def _delegated(self, companion, send, task):
        self.ui.write_log(f"SYS: {companion['name']} started: {task[:80]}")
        result = await send(companion, task)
        self.ui.write_log(f"{companion['name']}: {result}")
        self.speak(f"{companion['name']} finished: {result[:300]}")

    # --- speaking state -------------------------------------------------
    def _speaking_changed(self, speaking: bool) -> None:
        if speaking:
            if not self.tools.busy:
                self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def _mic_open(self) -> bool:
        speaking = self._speaker is not None and self._speaker.speaking
        return (self.ui.always_listening and not self.ui.muted
                and not speaking and not self.phone.talking)

    # --- the per-connection jobs ---------------------------------------
    async def _send_audio(self) -> None:
        while True:
            pcm = await self._outbox.get()
            try:
                await asyncio.wait_for(
                    self.session.send_realtime_input(audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")),
                    timeout=timeouts.SEND)
            except asyncio.TimeoutError:
                self.ui.write_log("SYS: Lost the connection sending voice input — reconnecting.")
                raise Reconnect()

    async def _watch_for_reconnect(self) -> None:
        await self._reconnect.wait()
        self._reconnect.clear()
        raise Reconnect()

    async def _receive(self) -> None:
        heard: list[str] = []
        said: list[str] = []
        heard_from_phone = False
        pending = lambda: timeouts.REPLY_IDLE if self._awaiting_reply_since is not None else None  # noqa: E731
        while True:   # session.receive() yields one turn, then ends
            try:
                async for msg in timeouts.iter_with_idle_timeout(self.session.receive(), pending):
                    if msg.data:
                        self._speaker.turn_finished.clear()
                        self._speaker.queue.put_nowait(msg.data)

                    update = msg.session_resumption_update
                    if update and update.resumable and update.new_handle:
                        self._resume_handle = update.new_handle

                    content = msg.server_content
                    if content is not None:
                        if content.output_transcription and content.output_transcription.text:
                            said.append(_clean(content.output_transcription.text))
                        if content.input_transcription and content.input_transcription.text:
                            if not heard:   # decide the source when the turn starts, not when it ends
                                heard_from_phone = self.phone.talking
                            heard.append(_clean(content.input_transcription.text))
                        if content.turn_complete:
                            self._speaker.turn_finished.set()
                            self._awaiting_reply_since = None
                            self._record("[Phone]" if heard_from_phone else "You", heard, "you")
                            self._record("Omni", said, "seraph")   # "seraph" = dashboard wire type
                            heard, said, heard_from_phone = [], [], False

                    if msg.tool_call:
                        await self._answer_tools(msg.tool_call.function_calls)
            except asyncio.TimeoutError:
                self.ui.write_log(f"SYS: no response from Gemini for {timeouts.REPLY_IDLE}s — reconnecting.")
                raise Reconnect()

    def _record(self, who: str, pieces: list[str], wire_type: str) -> None:
        text = " ".join(p for p in pieces if p).strip()
        if not text:
            return
        self.ui.write_log(f"{who}: {text}")
        self._transcript.append(f"{who}: {text}")
        if self.dashboard is not None:
            asyncio.create_task(self.dashboard.broadcast({"type": wire_type, "text": text}))

    async def _answer_tools(self, calls) -> None:
        replies = []
        for call in calls:
            try:
                replies.append(await asyncio.wait_for(self.tools.run(call), timeout=timeouts.TOOL_CALL))
            except asyncio.TimeoutError:
                # The worker thread can't be killed, but the session must not wait on it.
                self.tools.busy = False
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                self.ui.write_log(f"SYS: '{call.name}' didn't respond in time — cancelled, try again.")
                replies.append(types.FunctionResponse(id=call.id, name=call.name,
                                                      response={"error": "Tool call timed out"}))
        try:
            await asyncio.wait_for(self.session.send_tool_response(function_responses=replies),
                                   timeout=timeouts.SEND)
        except asyncio.TimeoutError:
            self.ui.write_log("SYS: Lost the connection sending a tool result back — reconnecting.")
            raise Reconnect()

    # --- connection lifecycle ------------------------------------------
    async def _connect_config(self) -> tuple[str, types.LiveConnectConfig]:
        brief = await asyncio.to_thread(prompt.build, settings_store.DEFAULT_LIVE_MODEL, self._voice_loader())
        self.companion, self.namespace = brief.companion, brief.namespace
        declarations = await self.tools.declarations(brief)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription={},
            output_audio_transcription={},
            system_instruction=brief.instruction,
            tools=[{"function_declarations": declarations}],
            session_resumption=types.SessionResumptionConfig(handle=self._resume_handle),
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=brief.voice))),
        )
        return brief.model, config

    async def _summarize_conversation(self) -> None:
        lines, self._transcript = self._transcript, []
        if len(lines) < 3:
            return
        try:
            summary = await asyncio.to_thread(
                llm.ask, "In one or two sentences, what did the user do or talk about here? "
                         "Reply with only the summary.\n\n" + "\n".join(lines[-40:]))
            profile.add_session_summary(summary, namespace=self.namespace)
        except Exception as err:
            print(f"[memory] session summary failed: {err}")

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        client = genai.Client(api_key=llm.api_key(), http_options={"api_version": "v1beta"})
        self.phone.start()
        while True:
            try:
                self.ui.set_state("THINKING")
                model, config = await self._connect_config()
                try:
                    async with (timeouts.connect_with_timeout(client.aio.live.connect(model=model, config=config),
                                                              timeouts.CONNECT) as session,
                                asyncio.TaskGroup() as jobs):
                        self.session = session
                        self._outbox = asyncio.Queue(maxsize=10)
                        self._speaker = audio.Speaker(self._speaking_changed, timeouts.PLAYBACK_TAIL)
                        self._connections += 1
                        self.ui.set_state("LISTENING")
                        self.ui.write_log("SYS: OMNI-OS online." if self._connections == 1
                                          else f"SYS: Reconnected (session #{self._connections}).")
                        jobs.create_task(self._send_audio())
                        jobs.create_task(audio.capture(lambda pcm: self.queue_audio(pcm), self._mic_open,
                                                       self.ui.write_log))
                        jobs.create_task(self._receive())
                        jobs.create_task(self._speaker.run(lambda: self.ui.speech_muted,
                                                           self.phone.mirror_audio))
                        jobs.create_task(self._watch_for_reconnect())
                except asyncio.TimeoutError:
                    self.ui.write_log(f"SYS: connection handshake timed out after {timeouts.CONNECT}s — retrying.")
            except Exception as err:
                wanted = isinstance(err, Reconnect) or (isinstance(err, ExceptionGroup) and err.subgroup(Reconnect))
                if not wanted:
                    traceback.print_exc()
                    self.ui.write_log(f"SYS: Connection lost ({timeouts.describe_disconnect(err)}) — reconnecting.")
            self.session = None
            await self._summarize_conversation()
            if self._speaker is not None:
                self._speaker.stop_speaking()
            self.ui.set_state("THINKING")
            await asyncio.sleep(3)


def _agent_sender(backend: str | None):
    """The async send(companion, text) for a text sub-agent backend, or None.
    (Static imports so the packager bundles every backend.)"""
    if backend == "claude_agent":
        from actions.claude_companion import send
    elif backend == "codex_agent":
        from actions.codex_companion import send
    elif backend == "opencode_agent":
        from actions.opencode_companion import send
    elif backend == "openhands_agent":
        from actions.openhands_companion import send
    elif backend == "grok_agent":
        from actions.grok_companion import send
    elif backend == "blackbox_agent":
        from actions.blackbox_companion import send
    else:
        return None
    return send


def _chime() -> None:
    """Two rising beeps when always-listening is switched on (Windows only)."""
    try:
        import winsound
        winsound.Beep(700, 90)
        winsound.Beep(1000, 90)
    except Exception:
        pass
