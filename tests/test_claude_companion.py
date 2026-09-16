"""actions/claude_companion.py — _format_error's known-upstream-bug
detection. Doesn't invoke a real (paid or authenticated) Claude CLI
session; pure-function coverage only."""

import unittest

from actions.claude_companion import _format_error


class FormatErrorTests(unittest.TestCase):
    def test_oauth_expired_gets_the_known_bug_note(self):
        msg = _format_error(
            "Failed to authenticate: OAuth session expired and could not be refreshed."
        )
        self.assertIn("Failed to authenticate: OAuth session expired", msg)
        self.assertIn("anthropics/claude-code#81937", msg)
        self.assertIn("Error: ", msg)

    def test_unrelated_error_passes_through_unchanged(self):
        self.assertEqual(_format_error("Rate limit exceeded"), "Error: Rate limit exceeded")

    def test_none_result_is_a_bare_prefix(self):
        self.assertEqual(_format_error(None), "Error: ")

    def test_oauth_mention_without_expiry_wording_is_not_flagged(self):
        # "oauth" alone shouldn't trip the note — only the specific
        # expired-and-unrefreshable phrasing this bug actually produces.
        msg = _format_error("OAuth token is invalid for this request.")
        self.assertNotIn("anthropics/claude-code#81937", msg)


if __name__ == "__main__":
    unittest.main()
