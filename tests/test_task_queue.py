"""Exercise the real queue synchronously; never start workers or an executor."""

from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch
from uuid import UUID

from agent.task_queue import TaskPriority, TaskQueue, TaskStatus


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(patch("agent.task_queue.uuid.uuid4", side_effect=[
            UUID(f"{index:08x}-0000-0000-0000-000000000000")
            for index in range(1, 10)
        ]))
        self.queue = TaskQueue()

    def test_submit_records_pending_defaults_and_truncates_only_summary(self):
        goal = "x" * 70
        task_id = self.queue.submit(goal)

        self.assertEqual(self.queue.get_status(task_id), {
            "task_id": task_id, "goal": goal, "status": "pending",
            "result": None, "error": "",
        })
        self.assertEqual(self.queue.get_all_statuses(), [{
            "task_id": task_id, "goal": goal[:50], "status": "pending",
        }])
        self.assertEqual(self.queue.pending_count(), 1)
        self.assertEqual(self.queue._next_task().priority, TaskPriority.NORMAL.value)
        self.assertIsNone(self.queue._worker_thread)
        self.assertIsNone(self.queue._executor)

    def test_priority_precedes_creation_time_and_equal_priority_uses_oldest(self):
        with patch("agent.task_queue.time.time", side_effect=[1.0, 30.0, 20.0, 40.0]):
            low = self.queue.submit("low", TaskPriority.LOW)
            newer_high = self.queue.submit("newer high", TaskPriority.HIGH)
            older_high = self.queue.submit("older high", TaskPriority.HIGH)
            normal = self.queue.submit("normal")

        # No worker threads: consume each selected task by cancelling it.
        for expected in [older_high, newer_high, normal, low]:
            self.assertEqual(self.queue._next_task().task_id, expected)
            self.assertTrue(self.queue.cancel(expected))
        self.assertIsNone(self.queue._next_task())
        self.assertEqual(self.queue.pending_count(), 0)

    def test_cancellation_sets_flag_and_rejects_terminal_tasks(self):
        task_id = self.queue.submit("cancel me")
        task = self.queue._next_task()
        self.assertTrue(self.queue.cancel(task_id))
        self.assertTrue(task.cancel_flag.is_set())
        self.assertEqual(self.queue.get_status(task_id)["status"], "cancelled")
        self.assertFalse(self.queue.cancel(task_id))
        self.assertEqual(self.queue.pending_count(), 0)

        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            with self.subTest(status=status):
                terminal_id = self.queue.submit("already finished")
                terminal = self.queue._tasks[terminal_id]
                terminal.status = status
                self.assertFalse(self.queue.cancel(terminal_id))
                self.assertEqual(terminal.status, status)
                self.assertFalse(terminal.cancel_flag.is_set())

    def test_missing_id_and_empty_queue_are_safe(self):
        self.assertIsNone(self.queue.get_status("missing"))
        self.assertFalse(self.queue.cancel("missing"))
        self.assertEqual(self.queue.get_all_statuses(), [])
        self.assertEqual(self.queue.pending_count(), 0)
        self.assertIsNone(self.queue._next_task())
