"""main.py's _connect_with_timeout — GEMZ4US, Finding #48 (2026-09-17):
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

import main


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
        async with main._connect_with_timeout(cm, timeout=5) as value:
            self.assertEqual(value, "session")
        self.assertEqual(cm.aexit_calls, [(None, None, None)])

    async def test_body_exception_is_forwarded_to_aexit_and_propagates(self):
        cm = FakeConnectCM()
        with self.assertRaises(ValueError):
            async with main._connect_with_timeout(cm, timeout=5):
                raise ValueError("boom")
        self.assertEqual(len(cm.aexit_calls), 1)
        exc_type, exc, _tb = cm.aexit_calls[0]
        self.assertIs(exc_type, ValueError)
        self.assertEqual(str(exc), "boom")

    async def test_aexit_can_suppress_the_bodys_exception(self):
        cm = FakeConnectCM()
        cm.suppress = True
        async with main._connect_with_timeout(cm, timeout=5):
            raise ValueError("boom")  # must not propagate past the `async with`
        self.assertEqual(len(cm.aexit_calls), 1)
        self.assertIs(cm.aexit_calls[0][0], ValueError)

    async def test_slow_handshake_times_out_without_calling_aexit(self):
        cm = FakeConnectCM(enter_delay=10)
        with self.assertRaises(asyncio.TimeoutError):
            async with main._connect_with_timeout(cm, timeout=0.05):
                self.fail("body must never run if __aenter__ times out")
        self.assertEqual(cm.aexit_calls, [])  # __aenter__ never completed — nothing to exit

    async def test_fast_handshake_within_timeout_succeeds(self):
        cm = FakeConnectCM(enter_delay=0.01)
        async with main._connect_with_timeout(cm, timeout=5) as value:
            self.assertEqual(value, "session")
        self.assertEqual(cm.aexit_calls, [(None, None, None)])


if __name__ == "__main__":
    unittest.main()
