"""A small priority queue of background goals, worked one at a time."""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Priority(Enum):
    HIGH = 0
    NORMAL = 1
    LOW = 2

    @classmethod
    def parse(cls, text: str | None) -> "Priority":
        return {"high": cls.HIGH, "low": cls.LOW}.get((text or "").strip().lower(), cls.NORMAL)


class State(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    goal: str
    priority: Priority
    order: int
    state: State = State.QUEUED
    outcome: str = ""
    stop: threading.Event = field(default_factory=threading.Event)
    context: dict = field(default_factory=dict)   # passed through to the worker


Worker = Callable[[Job], str]


class TaskBoard:
    """Jobs run in priority order (HIGH first), oldest first within a
    priority, one at a time on a single background thread."""

    def __init__(self, worker: Worker | None = None):
        self._worker = worker
        self._jobs: dict[str, Job] = {}
        self._cv = threading.Condition()
        self._counter = itertools.count()
        self._thread: threading.Thread | None = None

    def _ensure_thread(self) -> None:
        if self._worker is not None and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._drain, name="agent-tasks", daemon=True)
            self._thread.start()

    def submit(self, goal: str, priority: Priority = Priority.NORMAL, **context) -> str:
        job = Job(uuid.uuid4().hex[:8], goal, priority, next(self._counter), context=context)
        with self._cv:
            self._jobs[job.id] = job
            self._cv.notify()
        self._ensure_thread()
        return job.id

    def next_job(self) -> Job | None:
        with self._cv:
            waiting = [j for j in self._jobs.values() if j.state is State.QUEUED]
            return min(waiting, key=lambda j: (j.priority.value, j.order)) if waiting else None

    def cancel(self, job_id: str) -> bool:
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None or job.state not in (State.QUEUED, State.RUNNING):
                return False
            job.stop.set()
            job.state = State.CANCELLED
            return True

    def status(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return {"id": job.id, "goal": job.goal, "state": job.state.value, "outcome": job.outcome}

    def overview(self) -> list[dict]:
        return [{"id": j.id, "goal": j.goal[:60], "state": j.state.value}
                for j in sorted(self._jobs.values(), key=lambda j: j.order)]

    def waiting(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state is State.QUEUED)

    def _drain(self) -> None:
        while True:
            with self._cv:
                job = self.next_job()
                while job is None:
                    self._cv.wait(timeout=30)
                    job = self.next_job()
                job.state = State.RUNNING
            try:
                outcome = self._worker(job)
                state = State.CANCELLED if job.stop.is_set() else State.DONE
            except Exception as err:
                outcome, state = f"failed: {err}", State.FAILED
            with self._cv:
                if job.state is not State.CANCELLED:
                    job.state = state
                job.outcome = outcome
            time.sleep(0)   # let a cancel/submit in flight land first


_board: TaskBoard | None = None
_board_lock = threading.Lock()


def board() -> TaskBoard:
    global _board
    with _board_lock:
        if _board is None:
            from agent.worker import pursue
            _board = TaskBoard(pursue)
        return _board
