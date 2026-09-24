"""Bounded waits around Gemini Live SDK calls that have no timeout of their own."""

from __future__ import annotations

import asyncio
import contextlib
import sys

# Seconds. Each exists because the matching call once hung with no error
# (GEMZ4US reports, 2026-09):
CONNECT = 30        # live.connect() handshake (Finding #48 — stuck "THINKING" forever)
TOOL_CALL = 200     # one tool call; above claude_agent's own 180 s limit
SEND = 30           # any send on an open session; a stall means the link is bad
REPLY_IDLE = 900    # silence while a reply is outstanding before reconnecting
PLAYBACK_TAIL = 0.3  # speaker drain margin before the mic may listen again


@contextlib.asynccontextmanager
async def connect_with_timeout(connect_cm, timeout: float):
    """`async with connect_cm` where only entering is time-limited.

    If entering times out, the caller gets asyncio.TimeoutError and
    __aexit__ is never called (nothing was entered). Otherwise the body's
    outcome is forwarded to __aexit__ exactly as a plain `async with` would,
    including letting __aexit__ suppress the body's exception.
    """
    value = await asyncio.wait_for(connect_cm.__aenter__(), timeout=timeout)
    try:
        yield value
    except BaseException:
        if not await connect_cm.__aexit__(*sys.exc_info()):
            raise
    else:
        await connect_cm.__aexit__(None, None, None)


async def iter_with_idle_timeout(source, timeout):
    """Yield from async iterable `source`, bounding each wait for the next item.

    `timeout` is seconds, or a zero-argument callable returning seconds or
    None (no bound), re-evaluated before every wait — so the bound can apply
    only while a reply is actually pending (GEMZ4US 2026-09-21: an always-on
    mic means long, perfectly healthy silences). Raises asyncio.TimeoutError
    when the bound elapses; stops when `source` does.
    """
    iterator = source.__aiter__()
    limit = timeout if callable(timeout) else (lambda: timeout)
    while True:
        try:
            yield await asyncio.wait_for(iterator.__anext__(), timeout=limit())
        except StopAsyncIteration:
            return
