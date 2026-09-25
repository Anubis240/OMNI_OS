"""dashboard/server.py PhoneLink routes, driven end to end the way the phone
page (dashboard/pages.py) uses them: pair, talk, listen, get turned away
without a session. Runs the FastAPI app in-process — no port, no TLS."""

import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dashboard import pages
from dashboard.server import CLOSE_NOT_PAIRED, LinkHooks, PhoneLink


class PhoneLinkRouteTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.link = PhoneLink(LinkHooks(phone_joined=lambda: self.events.append("joined"),
                                        phone_left=lambda: self.events.append("left")))
        self.client = TestClient(self.link._routes())
        self.addCleanup(self.client.close)

    def _pair(self) -> str:
        code = self.link.issue_pairing_code()
        reply = self.client.post("/pair", json={"code": code.lower()})
        self.assertEqual(reply.status_code, 200)
        return reply.json()["token"]

    def test_pages_are_served(self):
        self.assertIn("omni_session", self.client.get("/").text)
        self.assertIn("pair-code", self.client.get("/pair").text)

    def test_pairing_with_a_code(self):
        self.assertEqual(self.client.post("/pair", json={"code": "WRONG1"}).status_code, 401)
        token = self._pair()
        self.assertTrue(token)
        # the code is spent
        self.assertEqual(self.client.post("/pair", json={"code": self.link.codes.pending() or "X"}).status_code, 401)

    def test_pairing_by_qr_scan_works_once(self):
        code = self.link.issue_pairing_code()
        first = self.client.get(f"/pair/scan?code={code}")
        self.assertIn("sessionStorage.setItem('omni_session'", first.text)
        again = self.client.get(f"/pair/scan?code={code}")
        self.assertIn("expired", again.text)

    def test_typed_message_needs_a_session(self):
        self.assertEqual(self.client.post("/api/say", json={"text": "hi"}).status_code, 401)
        token = self._pair()
        reply = self.client.post("/api/say", json={"text": " hello "}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(reply.json(), {"ok": True})
        self.assertEqual(self.link.inbox.get_nowait(), "hello")

    def test_trader_view_without_a_trader(self):
        token = self._pair()
        state = self.client.get("/api/trader/state", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(state.json(), {"open": False})

    def test_sockets_refuse_unpaired_phones(self):
        for path in ("/socket/chat?token=nope", "/socket/voice?token="):
            with self.assertRaises(WebSocketDisconnect) as refused:
                with self.client.websocket_connect(path) as sock:
                    sock.receive_json()
            self.assertEqual(refused.exception.code, CLOSE_NOT_PAIRED)

    def test_chat_socket_replays_backlog_and_reports_join_and_leave(self):
        token = self._pair()   # pairing publishes a notice into the backlog
        with self.client.websocket_connect(f"/socket/chat?token={token}") as sock:
            first = sock.receive_json()
            self.assertEqual(first, {"type": "notice", "text": "Phone paired with the code."})
            sock.send_json({"type": "pong"})
            self.assertEqual(self.events, ["joined"])
        self.client.post("/api/say", json={"text": "flush"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(self.events, ["joined", "left"])

    def test_voice_socket_carries_mic_frames(self):
        token = self._pair()
        with self.client.websocket_connect(f"/socket/voice?token={token}") as sock:
            sock.send_bytes(b"\x01\x02" * 8)
            sock.send_bytes(b"\x03\x04" * 8)
            self.client.post("/api/say", json={"text": "sync"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(self.link.mic_frames.get_nowait(), b"\x01\x02" * 8)
        self.assertEqual(self.link.mic_frames.get_nowait(), b"\x03\x04" * 8)
        notices = [m["text"] for m in self.link._backlog if m.get("type") == "notice"]
        self.assertIn("Phone speaker and mic ready.", notices)

    def test_page_uses_only_routes_the_server_has(self):
        for fragment in ("'/socket/chat?token='", "'/socket/voice?token='", "fetch('/api/say'",
                         "fetch('/pair'", "location.replace('/pair')", "msg.type === 'notice'"):
            self.assertIn(fragment, pages.APP_HTML + pages.LOGIN_HTML)
        for gone in ("/auto-login", "/api/command", "'/login'", "/ws?token", "/ws/audio", "seraph_token"):
            self.assertNotIn(gone, pages.APP_HTML + pages.LOGIN_HTML)


if __name__ == "__main__":
    unittest.main()
