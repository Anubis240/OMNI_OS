"""agent/tasks.py — ordering, cancellation and status of background goals."""

import threading
import unittest

from agent.tasks import Priority, State, TaskBoard


class TaskBoardOrderingTests(unittest.TestCase):
    """No worker: nothing runs, so ordering can be inspected directly."""

    def setUp(self):
        self.board = TaskBoard(worker=None)

    def test_high_priority_first_then_oldest_first(self):
        low = self.board.submit("low", Priority.LOW)
        first_normal = self.board.submit("n1")
        high = self.board.submit("high", Priority.HIGH)
        second_normal = self.board.submit("n2")
        seen = []
        while (job := self.board.next_job()) is not None:
            seen.append(job.id)
            self.board.cancel(job.id)
        self.assertEqual(seen, [high, first_normal, second_normal, low])

    def test_status_cancel_and_waiting_count(self):
        job_id = self.board.submit("do a thing", note="x")
        self.assertEqual(self.board.status(job_id), {"id": job_id, "goal": "do a thing",
                                                     "state": "queued", "outcome": ""})
        self.assertEqual(self.board.waiting(), 1)
        self.assertTrue(self.board.cancel(job_id))
        self.assertFalse(self.board.cancel(job_id))
        self.assertEqual(self.board.status(job_id)["state"], "cancelled")
        self.assertEqual(self.board.waiting(), 0)
        self.assertIsNone(self.board.status("nope"))
        self.assertFalse(self.board.cancel("nope"))

    def test_priority_parsing(self):
        self.assertIs(Priority.parse("HIGH"), Priority.HIGH)
        self.assertIs(Priority.parse("low"), Priority.LOW)
        self.assertIs(Priority.parse(None), Priority.NORMAL)
        self.assertIs(Priority.parse("urgent"), Priority.NORMAL)


class TaskBoardWorkerTests(unittest.TestCase):
    def test_worker_runs_jobs_and_records_outcomes(self):
        finished = threading.Event()
        order = []

        def worker(job):
            order.append(job.goal)
            if job.goal == "boom":
                raise RuntimeError("nope")
            if job.goal == "last":
                finished.set()
            return f"did {job.goal} with {job.context.get('extra')}"

        board = TaskBoard(worker)
        ok = board.submit("fine", extra=1)
        bad = board.submit("boom")
        board.submit("last")
        self.assertTrue(finished.wait(5))
        for _ in range(100):
            if board.status(ok)["state"] == "done" and board.status(bad)["state"] == "failed":
                break
            threading.Event().wait(0.02)
        self.assertEqual(board.status(ok)["outcome"], "did fine with 1")
        self.assertEqual(board.status(bad)["state"], State.FAILED.value)
        self.assertIn("nope", board.status(bad)["outcome"])
        self.assertEqual(order, ["fine", "boom", "last"])


if __name__ == "__main__":
    unittest.main()
