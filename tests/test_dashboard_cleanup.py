"""dashboard/server.py — centralized control-client removal and the
disconnect callback. Uses fake WebSocket-shaped objects; never starts a
real HTTP(S) listener."""

import unittest

from dashboard.server import DashboardServer


class _FakeWebSocket:
    """Just enough surface for _remove_client/broadcast: hashable (used in
    a set) and an awaitable send_json that can be made to fail."""

    def __init__(self, fails=False):
        self.fails = fails
        self.sent: list[dict] = []

    async def send_json(self, msg):
        if self.fails:
            raise RuntimeError("connection closed")
        self.sent.append(msg)


class DashboardCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = DashboardServer()
        self.calls = 0
        self.server.set_disconnect_callback(lambda: setattr(self, "calls", self.calls + 1))

    def test_removing_the_last_client_fires_callback_once(self):
        ws = _FakeWebSocket()
        self.server._clients.add(ws)
        self.server._remove_client(ws)
        self.assertEqual(self.calls, 1)
        self.assertNotIn(ws, self.server._clients)

    def test_removing_one_of_several_clients_does_not_fire(self):
        ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
        self.server._clients.update({ws1, ws2})
        self.server._remove_client(ws1)
        self.assertEqual(self.calls, 0)
        self.assertIn(ws2, self.server._clients)

    def test_removing_an_already_absent_client_is_a_no_op(self):
        ws = _FakeWebSocket()  # never added
        self.server._remove_client(ws)
        self.assertEqual(self.calls, 0)

    def test_double_removal_of_the_same_client_fires_once(self):
        """Mirrors the real /ws handler: the heartbeat's dead-path and the
        main loop's own finally-block cleanup can both run for the same
        disconnect. Only the one that actually empties the set should fire."""
        ws = _FakeWebSocket()
        self.server._clients.add(ws)
        self.server._remove_client(ws)  # heartbeat path
        self.server._remove_client(ws)  # finally-block path, same websocket
        self.assertEqual(self.calls, 1)

    async def test_broadcast_removing_the_final_client_fires_callback(self):
        """The exact race a Codex review found: broadcast() used to remove
        dead clients via `self._clients -= dead` directly, bypassing the
        was-it-still-there check entirely, so the callback never fired when
        broadcast() (not the heartbeat) was the one to notice the last
        client was gone."""
        ws = _FakeWebSocket(fails=True)
        self.server._clients.add(ws)
        await self.server.broadcast({"type": "sys", "text": "hello"})
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.server._clients, set())

    async def test_broadcast_removing_one_dead_client_among_live_ones_does_not_fire(self):
        dead = _FakeWebSocket(fails=True)
        alive = _FakeWebSocket()
        self.server._clients.update({dead, alive})
        await self.server.broadcast({"type": "sys", "text": "hello"})
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.server._clients, {alive})
        self.assertEqual(alive.sent, [{"type": "sys", "text": "hello"}])

    async def test_broadcast_then_heartbeat_cleanup_on_same_client_fires_once(self):
        """The full race: broadcast() sees the send fail and removes the
        client first; the heartbeat's own cleanup for that same socket runs
        after. Must not double-fire, and must not silently skip firing."""
        ws = _FakeWebSocket(fails=True)
        self.server._clients.add(ws)
        await self.server.broadcast({"type": "sys", "text": "hello"})
        self.server._remove_client(ws)  # heartbeat/finally cleanup, runs later
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
