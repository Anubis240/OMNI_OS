"""actions/codex_companion.py — argv construction, persona injection, and
per-companion serialization. Mocks subprocess execution entirely; never
invokes a real (paid or authenticated) Codex CLI session."""

import asyncio
import json
import unittest
from unittest.mock import patch

from actions import codex_companion as cc


def _fake_completed_process(returncode=0, thread_id="thread-1", reply="ok"):
    lines = [json.dumps({"type": "thread.started", "thread_id": thread_id})]
    if reply is not None:
        lines.append(json.dumps({"type": "item.completed",
                                  "item": {"type": "agent_message", "text": reply}}))
    return unittest.mock.Mock(stdout="\n".join(lines), stderr="", returncode=returncode)


class CodexArgvTests(unittest.TestCase):
    def setUp(self):
        cc._sessions.clear()
        cc._locks.clear()
        self.ca = {"cliPath": "codex.exe", "vaultDir": ""}

    def test_initial_turn_places_global_options_before_prompt(self):
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex({"id": "c1"}, "hello", self.ca)
        argv = run.call_args.args[0]
        prompt_index = argv.index("hello")
        # Every global/flag token must appear before the bare prompt.
        for flag in ("--json", "--skip-git-repo-check", "--sandbox", "--ask-for-approval"):
            self.assertLess(argv.index(flag), prompt_index)
        self.assertNotIn("resume", argv)

    def test_resumed_turn_with_vault_dir_places_cd_before_resume(self):
        cc._sessions["c1"] = "thread-1"
        ca = {"cliPath": "codex.exe", "vaultDir": "C:/vault"}
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex({"id": "c1"}, "continue please", ca)
        argv = run.call_args.args[0]
        resume_index = argv.index("resume")
        cd_index = argv.index("--cd")
        self.assertLess(cd_index, resume_index)
        # resume <thread_id> <prompt>, in that order, right after "resume".
        self.assertEqual(argv[resume_index + 1], "thread-1")
        self.assertEqual(argv[resume_index + 2], "continue please")

    def test_unrestricted_bypass_flag_is_gone(self):
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex({"id": "c1"}, "hello", self.ca)
        argv = run.call_args.args[0]
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertIn("--ask-for-approval", argv)
        self.assertEqual(argv[argv.index("--ask-for-approval") + 1], "never")


class CodexPersonaTests(unittest.TestCase):
    def setUp(self):
        cc._sessions.clear()
        cc._locks.clear()
        self.ca = {"cliPath": "codex.exe", "vaultDir": ""}

    def test_new_thread_includes_system_prompt(self):
        companion = {"id": "c1", "system_prompt": "You are Forge, a coding specialist."}
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex(companion, "fix the bug", self.ca)
        argv = run.call_args.args[0]
        prompt = argv[-1]
        self.assertIn("You are Forge, a coding specialist.", prompt)
        self.assertIn("fix the bug", prompt)

    def test_resumed_turn_does_not_duplicate_persona(self):
        cc._sessions["c1"] = "thread-1"
        companion = {"id": "c1", "system_prompt": "You are Forge, a coding specialist."}
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex(companion, "now fix the other bug", self.ca)
        argv = run.call_args.args[0]
        prompt = argv[argv.index("resume") + 2]
        self.assertNotIn("Forge", prompt)
        self.assertEqual(prompt, "now fix the other bug")

    def test_no_system_prompt_is_a_no_op(self):
        companion = {"id": "c1"}  # no system_prompt key at all
        with patch.object(cc.subprocess, "run", return_value=_fake_completed_process()) as run:
            cc._run_codex(companion, "hello", self.ca)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "hello")


class CodexSerializationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cc._sessions.clear()
        cc._locks.clear()
        self._settings_patch = patch.object(
            cc.settings_store, "load_settings",
            return_value={"codex_agent": {"enabled": True, "cliPath": "codex.exe", "vaultDir": ""}},
        )
        self._settings_patch.start()
        self.addCleanup(self._settings_patch.stop)

    async def test_overlapping_calls_same_companion_run_sequentially(self):
        order = []

        def fake_run(companion, text, ca):
            order.append(f"start:{text}")
            # First call claims the thread id, as the real CLI's
            # thread.started event would on an actual first turn.
            cc._sessions.setdefault(companion["id"], "thread-1")
            order.append(f"end:{text}")
            return f"reply to {text}"

        with patch.object(cc, "_run_codex", side_effect=fake_run):
            companion = {"id": "c1"}
            results = await asyncio.gather(
                cc.send(companion, "first"), cc.send(companion, "second"),
            )

        # Sequential, not interleaved: each start is immediately followed by
        # its own end before the other call's start appears.
        self.assertEqual(order, ["start:first", "end:first", "start:second", "end:second"])
        self.assertEqual(set(results), {"reply to first", "reply to second"})
        self.assertEqual(cc.get_status("c1"), "idle")

    async def test_different_companions_are_not_serialized_together(self):
        release = {}

        async def fake_to_thread(fn, *args):
            # Block companion "a" until "b" has already been dispatched, to
            # prove they don't share a lock — if they did, this would
            # deadlock instead of completing.
            companion_id = args[0]["id"]
            if companion_id == "a":
                await release["b_started"].wait()
            else:
                release["b_started"].set()
            return f"reply-{companion_id}"

        release["b_started"] = asyncio.Event()
        with patch.object(cc.asyncio, "to_thread", side_effect=fake_to_thread):
            results = await asyncio.wait_for(
                asyncio.gather(cc.send({"id": "a"}, "x"), cc.send({"id": "b"}, "y")),
                timeout=5,
            )
        self.assertEqual(set(results), {"reply-a", "reply-b"})

    async def test_forget_session_clears_lock_and_thread(self):
        cc._sessions["c1"] = "thread-1"
        cc._locks["c1"] = asyncio.Lock()
        cc.forget_session("c1")
        self.assertNotIn("c1", cc._sessions)
        self.assertNotIn("c1", cc._locks)


if __name__ == "__main__":
    unittest.main()
