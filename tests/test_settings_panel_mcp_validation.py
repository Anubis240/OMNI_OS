"""settings_panel._looks_like_valid_mcp_host — GEMZ4US, 2026-09-22 (Part 6):
_on_add_mcp_server's URL check only looked for a http(s):// schema prefix,
so "http://not-a-valid-url" (a schema, but no dot/TLD, no way to ever
resolve) was accepted and added to the server list. A standalone function
so this is testable without constructing the QWidget it's used from."""

import unittest

from settings_panel import _looks_like_valid_mcp_host


class LooksLikeValidMcpHostTests(unittest.TestCase):
    def test_rejects_a_hostname_with_no_dot(self):
        self.assertFalse(_looks_like_valid_mcp_host("not-a-valid-url"))

    def test_rejects_empty_host(self):
        self.assertFalse(_looks_like_valid_mcp_host(""))

    def test_accepts_a_real_looking_domain(self):
        self.assertTrue(_looks_like_valid_mcp_host("seraph.kondux.io"))

    def test_accepts_localhost(self):
        self.assertTrue(_looks_like_valid_mcp_host("localhost"))

    def test_accepts_an_ipv4_address(self):
        self.assertTrue(_looks_like_valid_mcp_host("127.0.0.1"))

    def test_accepts_an_ipv6_address(self):
        self.assertTrue(_looks_like_valid_mcp_host("::1"))


if __name__ == "__main__":
    unittest.main()
