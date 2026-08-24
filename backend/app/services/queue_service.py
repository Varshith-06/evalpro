"""§5.4 Operational — the grading queue.

Everyone submits in the last hour before the deadline. That is not an edge
case, it is the normal shape of the traffic, and it is where a naive design
falls over: ``/api/submit`` used to run the whole cascade inline, so every
submission held an HTTP worker for the length of a sandboxed test run. Starlette
runs sync endpoints in a bounded threadpool, so the 41st simultaneous submission
waited behind the first forty and the student's browser timed out. Worse, forty
concurrent sandboxes on one box thrash the CPU and make *every* run slower.

So execution moves behind a queue, and the queue is the only way in. Three
properties matter more than throughput:

* **A ceiling on concurrent runs.** Grading is CPU-bound and sandboxed; running
  more of it at once than the box can take makes everything slower, not faster.
* **A ceiling per assignment.** One pathological lab -- a thousand-student
  course, or an assignment whose tests all run to timeout -- must not starve
  every other lab on the estate. This is the whole reason a plain
  ``ThreadPoolExecutor`` is not enough: it is FIFO across everything, so the
  first lab to flood the queue owns every worker until it drains.
* **Never reject at the deadline.** Backpressure is reported as a wait, never
  as a refusal. A student who submitted before the deadline submitted before
  the deadline, and a queue that sheds load has just failed the one person it
  exists for.

This is the in-process implementation: threads, one box, no broker. It is
deliberately not Kafka. A department-wide deadline is a few thousand
submissions over ten minutes -- around three a second -- and Kafka is a
distributed *log* built for a thousand times that, with no per-job ack, no
visibility timeout, no retry and no priority. The production path is the same
interface backed by Postgres ``SELECT ... FOR UPDATE SKIP LOCKED``, which
survives a worker being killed and makes the job claim transactional with the
grade write. ``GradingQueue`` is the seam where that swaps in.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..config import settings

logger = logging.getLogger("evalpro.queue")

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# Post-deadline grading outranks a pre-deadline practice run: one decides a
# mark, the other is feedback the student can ask for again in a minute.
PRIORITY_GRADE = 0
PRIORITY_PREVIEW = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    """One unit of grading work, and everything the admin view needs to see."""

    id: str
    key: str                                   # the assignment this belongs to
    priority: int
    fn: Callable[[], object]
    label: str = ""
    state: str = QUEUED
    enqueued_at: datetime = field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: object = None
    error: str | None = None
    # The exception object itself, not just its text: the API layer maps a
    # SubmissionRejected to 409 and a rate limit to 429, and it cannot do that
    # from a formatted string.
    exception: BaseException | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    # -- timings -----------------------------------------------------------
    @property
    def waited_ms(self) -> int:
        end = self.started_at or _now()
        return int((end - self.enqueued_at).total_seconds() * 1000)

    @property
    def ran_ms(self) -> int:
        if self.started_at is None:
            return 0
        end = self.finished_at or _now()
        return int((end - self.started_at).total_seconds() * 1000)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until this job finishes. False means it is still going."""
        return self._done.wait(timeout=timeout)

    def as_dict(self) -> dict:
        return {
            "job_id": self.id,
            "state": self.state,
            "assignment_id": self.key,
            "label": self.label,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at.isoformat(),
            "waited_ms": self.waited_ms,
            "ran_ms": self.ran_ms,
            "error": self.error,
        }


class QueueFull(Exception):
    """Raised only when the queue is so far past its ceiling that accepting more
    would be dishonest. Never raised for ordinary deadline load."""


class GradingQueue:
    """A bounded, per-key-fair work queue over a fixed pool of threads."""

    def __init__(
        self,
        workers: int | None = None,
        max_per_key: int | None = None,
        max_depth: int | None = None,
        history: int | None = None,
    ) -> None:
        config = settings.queue
        self.workers = max(1, workers if workers is not None else config.workers)
        self.max_per_key = max(1, max_per_key if max_per_key is not None else config.max_per_assignment)
        self.max_depth = max_depth if max_depth is not None else config.max_depth
        self.history = history if history is not None else config.history

        self._cond = threading.Condition()
        self._pending: list[Job] = []
        self._running: dict[str, Job] = {}
        self._by_key: Counter[str] = Counter()
        self._finished: dict[str, Job] = {}
        self._finished_order: list[str] = []
        self._threads: list[threading.Thread] = []
        self._stop = False
        self._seq = itertools.count()
        self._completed = 0
        self._failed = 0
        self._peak_depth = 0
        self._total_wait_ms = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._stop = False
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._worker, name=f"evalpro-grader-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        logger.info(
            "grading queue up: %d workers, %d concurrent per assignment",
            self.workers, self.max_per_key,
        )

    def stop(self, drain: bool = True, timeout: float = 30.0) -> None:
        """Stop accepting work. With ``drain``, let what is queued finish first.

        A submission already accepted is a promise; dropping it on shutdown
        would lose a student's work after telling them it was received.
        """
        if drain:
            self.join(timeout=timeout)
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()

    def join(self, timeout: float | None = None) -> bool:
        """Block until the queue is empty. Returns False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._pending or self._running:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(timeout=remaining if remaining is not None else 0.25)
        return True

    # -- producing ---------------------------------------------------------
    def submit(
        self,
        fn: Callable[[], object],
        *,
        key: str,
        priority: int = PRIORITY_GRADE,
        label: str = "",
    ) -> Job:
        job = Job(id=uuid.uuid4().hex, key=key, priority=priority, fn=fn, label=label)
        with self._cond:
            if self.max_depth and len(self._pending) >= self.max_depth:
                raise QueueFull(
                    f"{len(self._pending)} submissions are already waiting. "
                    "Nothing is lost -- try again in a moment."
                )
            # Stable order within a priority: the sequence number breaks ties by
            # arrival, so an early submission is never overtaken by a later one
            # at the same priority.
            job._order = next(self._seq)  # type: ignore[attr-defined]
            self._pending.append(job)
            self._pending.sort(key=lambda j: (j.priority, j._order))  # type: ignore[attr-defined]
            self._peak_depth = max(self._peak_depth, len(self._pending))
            self._cond.notify()
        return job

    # -- consuming ---------------------------------------------------------
    def _claim(self) -> Job | None:
        """Take the best job this worker is allowed to run.

        "Allowed" is what makes this fairer than a plain thread pool: the first
        pending job is skipped when its assignment is already at its ceiling,
        and the next assignment's work runs instead.
        """
        for index, job in enumerate(self._pending):
            if self._by_key[job.key] < self.max_per_key:
                self._pending.pop(index)
                return job
        return None

    def _worker(self) -> None:
        while True:
            with self._cond:
                job = None
                while not self._stop:
                    job = self._claim()
                    if job is not None:
                        break
                    self._cond.wait(timeout=0.5)
                if job is None:
                    return
                job.state = RUNNING
                job.started_at = _now()
                self._running[job.id] = job
                self._by_key[job.key] += 1
                self._total_wait_ms += job.waited_ms

            try:
                job.result = job.fn()
                job.state = DONE
            except BaseException as exc:            # noqa: BLE001 - recorded, not swallowed
                job.state = FAILED
                job.error = f"{type(exc).__name__}: {exc}"
                job.exception = exc
                logger.exception("grading job %s failed", job.id)
            finally:
                job.finished_at = _now()
                with self._cond:
                    self._running.pop(job.id, None)
                    self._by_key[job.key] -= 1
                    if self._by_key[job.key] <= 0:
                        del self._by_key[job.key]
                    if job.state == DONE:
                        self._completed += 1
                    else:
                        self._failed += 1
                    self._remember(job)
                    self._cond.notify_all()
                job._done.set()

    def _remember(self, job: Job) -> None:
        """Keep recent jobs so a client can still ask how its submission went."""
        self._finished[job.id] = job
        self._finished_order.append(job.id)
        while len(self._finished_order) > self.history:
            self._finished.pop(self._finished_order.pop(0), None)

    # -- observing ---------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._cond:
            if job_id in self._finished:
                return self._finished[job_id]
            if job_id in self._running:
                return self._running[job_id]
            for job in self._pending:
                if job.id == job_id:
                    return job
        return None

    def position(self, job_id: str) -> int:
        """How many jobs are ahead of this one. 0 once it is running."""
        with self._cond:
            for index, job in enumerate(self._pending):
                if job.id == job_id:
                    return index
        return 0

    def stats(self) -> dict:
        with self._cond:
            pending = list(self._pending)
            running = list(self._running.values())
            handled = self._completed + self._failed
            oldest = min((j.enqueued_at for j in pending), default=None)
            return {
                "workers": self.workers,
                "max_per_assignment": self.max_per_key,
                "depth": len(pending),
                "running": len(running),
                "peak_depth": self._peak_depth,
                "completed": self._completed,
                "failed": self._failed,
                "mean_wait_ms": int(self._total_wait_ms / handled) if handled else 0,
                "oldest_wait_ms": int((_now() - oldest).total_seconds() * 1000) if oldest else 0,
                "by_assignment": dict(Counter(j.key for j in pending + running)),
                "accepting": not self._stop,
            }


# --------------------------------------------------------------------------
# Process-wide instance
# --------------------------------------------------------------------------
_queue: GradingQueue | None = None
_queue_lock = threading.Lock()


def get_queue() -> GradingQueue:
    """The queue for this process, started on first use.

    Lazy rather than import-time so that tests, the seeder and one-shot scripts
    do not spawn worker threads they never use.
    """
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = GradingQueue()
            _queue.start()
        return _queue


def shutdown_queue(drain: bool = True) -> None:
    global _queue
    with _queue_lock:
        if _queue is not None:
            _queue.stop(drain=drain)
            _queue = None
