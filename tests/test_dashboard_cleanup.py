"""dashboard/server.py — phone socket bookkeeping and pairing keys.
Uses fake sockets; never starts a real listener."""

import time
import unittest
from unittest.mock import patch

from dashboard.server import DashboardServer, PairingKey


class _FakeSocket:
    def __init__(self, fails=False):
        self.fails = fails
        self.sent: list[dict] = []

    async def send_json(self, msg):
        if self.fails:
            raise RuntimeError("connection closed")
        self.sent.append(msg)


class PhoneSocketTests(unittest.IsolatedAsyncioTestCase):
    """The desktop's CONNECTED/DISCONNECTED badge depends on the 'last phone
    left' callback firing exactly once per real disconnect, whichever of the
    heartbeat, a failed send or the handler's own exit notices first."""

    def setUp(self):
        self.server = DashboardServer()
        self.left = 0
        self.server.set_disconnect_callback(lambda: setattr(self, "left", self.left + 1))

    def test_last_phone_leaving_fires_once(self):
        sock = _FakeSocket()
        self.server.phones.sockets.add(sock)
        self.server.phones.drop(sock)
        self.server.phones.drop(sock)   # a second path noticing the same disconnect
        self.assertEqual(self.left, 1)

    def test_one_of_several_leaving_does_not_fire(self):
        a, b = _FakeSocket(), _FakeSocket()
        self.server.phones.sockets.update({a, b})
        self.server.phones.drop(a)
        self.assertEqual(self.left, 0)
        self.assertEqual(self.server.phones.sockets, {b})

    def test_dropping_an_unknown_socket_is_a_no_op(self):
        self.server.phones.drop(_FakeSocket())
        self.assertEqual(self.left, 0)

    async def test_failed_send_drops_the_socket_and_fires_once(self):
        dead = _FakeSocket(fails=True)
        self.server.phones.sockets.add(dead)
        await self.server.broadcast({"type": "sys", "text": "hi"})
        self.server.phones.drop(dead)   # heartbeat cleanup arriving later
        self.assertEqual(self.left, 1)
        self.assertEqual(self.server.phones.sockets, set())

    async def test_live_phones_still_receive_when_another_fails(self):
        dead, live = _FakeSocket(fails=True), _FakeSocket()
        self.server.phones.sockets.update({dead, live})
        await self.server.broadcast({"type": "sys", "text": "hi"})
        self.assertEqual(self.left, 0)
        self.assertEqual(live.sent, [{"type": "sys", "text": "hi"}])

    async def test_history_is_capped(self):
        for n in range(150):
            await self.server.broadcast({"n": n})
        self.assertEqual(len(self.server._history), 100)
        self.assertEqual(self.server._history[0], {"n": 50})


class PairingKeyTests(unittest.TestCase):
    """GEMZ4US Item G: a superseded key must stop working immediately — this
    gates access to an app that can trade real funds."""

    def test_new_key_revokes_the_previous_one(self):
        keys = PairingKey()
        first, second = keys.issue(), keys.issue()
        self.assertNotEqual(first, second)
        self.assertFalse(keys.redeem(first))
        self.assertTrue(keys.redeem(second))

    def test_a_key_works_once_and_ignores_case(self):
        keys = PairingKey()
        key = keys.issue()
        self.assertTrue(keys.redeem(key.lower()))
        self.assertFalse(keys.redeem(key))

    def test_expired_key_is_refused(self):
        keys = PairingKey()
        key = keys.issue(lifetime=60)
        with patch("dashboard.server.time.time", return_value=time.time() + 61):
            self.assertFalse(keys.redeem(key))

    def test_keys_avoid_look_alike_characters(self):
        keys = PairingKey()
        for _ in range(50):
            self.assertFalse(set(keys.issue()) & set("OIL01"))


if __name__ == "__main__":
    unittest.main()
