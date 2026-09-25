"""voice/timeouts.py connect_with_timeout — GEMZ4US, Finding #48 (2026-09-17):
the Gemini Live connection handshake (client.aio.live.connect) had no
timeout at all, so a stalled one left the app stuck in "THINKING"
forever with no exception raised to trigger the reconnect loop's own
retry — only a full OS reboot cleared it (survived an app restart, a
companion switch, and a reinstall). Tests the extracted helper in
isolation with a fake async context manager — no real genai/network
dependency, since main.py's own import chain (sounddevice, the Gemini
SDK, etc.) is heavy and this logic doesn't need any of it to be exercised
correctly."""

import asyncio
import unittest

from voice import timeouts


class FakeConnectCM:
    """Stands in for client.aio.live.connect(...) — a plain async context
    manager whose __aenter__ can be made to hang (to exercise the
    timeout) and whose __aexit__ records exactly what it was called with."""

    def __init__(self, enter_delay: float = 0, enter_value="session"):
        self.enter_delay = enter_delay
        self.enter_value = enter_value
        self.aexit_calls = []
        self.suppress = False

    async def __aenter__(self):
        if self.enter_delay:
            await asyncio.sleep(self.enter_delay)
        return self.enter_value

    async def __aexit__(self, exc_type, exc, tb):
        self.aexit_calls.append((exc_type, exc, tb))
        return self.suppress


class ConnectWithTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_clean_exit_calls_aexit_with_no_exception(self):
        cm = FakeConnectCM()
        async with timeouts.connect_with_timeout(cm, timeout=5) as value:
            self.assertEqual(value, "session")
        self.assertEqual(cm.aexit_calls, [(None, None, None)])

    async def test_body_exception_is_forwarded_to_aexit_and_propagates(self):
        cm = FakeConnectCM()
        with self.assertRaises(ValueError):
            async with timeouts.connect_with_timeout(cm, timeout=5):
                raise ValueError("boom")
        self.assertEqual(len(cm.aexit_calls), 1)
        exc_type, exc, _tb = cm.aexit_calls[0]
        self.assertIs(exc_type, ValueError)
        self.assertEqual(str(exc), "boom")

    async def test_aexit_can_suppress_the_bodys_exception(self):
        cm = FakeConnectCM()
        cm.suppress = True
        async with timeouts.connect_with_timeout(cm, timeout=5):
            raise ValueError("boom")  # must not propagate past the `async with`
        self.assertEqual(len(cm.aexit_calls), 1)
        self.assertIs(cm.aexit_calls[0][0], ValueError)

    async def test_slow_handshake_times_out_without_calling_aexit(self):
        cm = FakeConnectCM(enter_delay=10)
        with self.assertRaises(asyncio.TimeoutError):
            async with timeouts.connect_with_timeout(cm, timeout=0.05):
                self.fail("body must never run if __aenter__ times out")
        self.assertEqual(cm.aexit_calls, [])  # __aenter__ never completed — nothing to exit

    async def test_fast_handshake_within_timeout_succeeds(self):
        cm = FakeConnectCM(enter_delay=0.01)
        async with timeouts.connect_with_timeout(cm, timeout=5) as value:
            self.assertEqual(value, "session")
        self.assertEqual(cm.aexit_calls, [(None, None, None)])


async def _fake_source(items, delay_before_index=None, delay=0):
    """A minimal async generator standing in for session.receive() — yields
    `items` in order, optionally pausing for `delay` seconds right before
    yielding the item at `delay_before_index` (to exercise the idle
    timeout)."""
    for i, item in enumerate(items):
        if delay_before_index is not None and i == delay_before_index:
            await asyncio.sleep(delay)
        yield item


class IterWithIdleTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """voice/timeouts.py iter_with_idle_timeout — GEMZ4US, 2026-09-20: a voice
    input got no transcript/reply, then even typed messages got no reply,
    for ~48 minutes straight until a full restart. Root cause: the Gemini
    SDK's session.receive() ultimately calls a plain websocket recv() with
    no timeout of its own — if the server stops producing messages without
    closing the socket, the receive loop blocks forever. This wraps any
    async iterable so each individual item wait is bounded."""

    async def test_yields_every_item_when_source_is_fast_enough(self):
        out = [x async for x in timeouts.iter_with_idle_timeout(_fake_source([1, 2, 3]), timeout=5)]
        self.assertEqual(out, [1, 2, 3])

    async def test_ends_normally_when_source_is_exhausted(self):
        # No TimeoutError just because the underlying source is done —
        # StopAsyncIteration must end the wrapped iteration the same way.
        count = 0
        async for _ in timeouts.iter_with_idle_timeout(_fake_source([]), timeout=5):
            count += 1
        self.assertEqual(count, 0)

    async def test_raises_timeout_error_when_an_item_is_late(self):
        source = _fake_source([1, 2, 3], delay_before_index=1, delay=10)
        collected = []
        with self.assertRaises(asyncio.TimeoutError):
            async for item in timeouts.iter_with_idle_timeout(source, timeout=0.05):
                collected.append(item)
        self.assertEqual(collected, [1])  # got the first item before the stall

    async def test_does_not_time_out_on_a_slow_but_within_budget_gap(self):
        source = _fake_source([1, 2], delay_before_index=1, delay=0.02)
        out = [x async for x in timeouts.iter_with_idle_timeout(source, timeout=5)]
        self.assertEqual(out, [1, 2])

    async def test_callable_timeout_is_evaluated_before_each_wait(self):
        source = _fake_source([1, 2, 3], delay_before_index=1, delay=0.02)
        calls = []

        def get_timeout():
            calls.append(len(calls))
            return 5

        out = [x async for x in timeouts.iter_with_idle_timeout(source, get_timeout)]
        self.assertEqual(out, [1, 2, 3])
        self.assertEqual(len(calls), 4)  # once per __anext__() call, including the final one that finds StopAsyncIteration

    async def test_callable_returning_none_waits_indefinitely(self):
        # GEMZ4US, 2026-09-21: "always listening" streams mic audio
        # continuously regardless of whether anyone is actually speaking,
        # so a flat timeout fired even during a normal, healthy idle gap
        # with nothing outstanding. None means "nothing pending right
        # now" — must not time out no matter how long the gap is.
        source = _fake_source([1, 2], delay_before_index=1, delay=0.1)
        out = [x async for x in timeouts.iter_with_idle_timeout(source, lambda: None)]
        self.assertEqual(out, [1, 2])

    async def test_callable_can_switch_from_none_to_a_real_timeout_mid_stream(self):
        # Models the real usage: nothing pending while idle (None), then a
        # reply becomes outstanding (a real number) once something is sent.
        pending = [None]
        source = _fake_source([1, 2], delay_before_index=1, delay=10)
        collected = []
        with self.assertRaises(asyncio.TimeoutError):
            async for item in timeouts.iter_with_idle_timeout(source, lambda: pending[0]):
                collected.append(item)
                pending[0] = 0.05  # a "reply" is now expected — arm the bound
        self.assertEqual(collected, [1])


class DescribeDisconnectTests(unittest.TestCase):
    """GEMZ4US 2026-09-24: "Connection lost (unhandled errors in a TaskGroup
    (1 sub-exception))" named the wrapper, never the cause."""

    def test_names_the_exception_inside_the_task_group(self):
        err = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("received 1011 (internal error) keepalive ping timeout")])
        self.assertEqual(timeouts.describe_disconnect(err), "ConnectionError: received 1011 (internal error) keepalive ping timeout")

    def test_unwraps_nested_groups_and_caps_at_three(self):
        inner = ExceptionGroup("inner", [ValueError("a"), KeyError("b")])
        err = ExceptionGroup("outer", [inner, RuntimeError("c"), OSError("d")])
        self.assertEqual(timeouts.describe_disconnect(err), "ValueError: a; KeyError: 'b'; RuntimeError: c")

    def test_plain_exception_and_empty_message(self):
        self.assertEqual(timeouts.describe_disconnect(TimeoutError()), "TimeoutError")
        self.assertEqual(timeouts.describe_disconnect(OSError("gone")), "OSError: gone")


if __name__ == "__main__":
    unittest.main()
