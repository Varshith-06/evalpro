"""The grading queue: what happens when everyone submits at once.

The deadline spike is the load pattern this system actually faces, so these
are properties about behaviour under contention rather than about throughput:

    never exceed the ceiling  ·  never starve a lab  ·  never drop accepted work

The last one matters most. A submission that has been accepted is a promise to
a student, and every failure mode here is written so that the promise survives:
overload becomes a longer wait, a crash becomes a recorded failure with its
exception intact, and shutdown drains rather than drops.
"""
from __future__ import annotations

import threading
import time

import pytest

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BloomLevel, Concept, Course, Enrollment, Role, User  # noqa: E402
from app.seed_data import ASSIGNMENTS  # noqa: E402
from app.services import queue_service  # noqa: E402
from app.services.queue_service import (  # noqa: E402
    DONE,
    FAILED,
    PRIORITY_GRADE,
    PRIORITY_PREVIEW,
    GradingQueue,
    QueueFull,
)

SORT = ASSIGNMENTS[0]["reference"]


@pytest.fixture
def queue():
    q = GradingQueue(workers=4, max_per_key=2)
    q.start()
    yield q
    q.stop(drain=False)


# --------------------------------------------------------------------------
# The ceilings
# --------------------------------------------------------------------------
def test_never_more_than_the_worker_ceiling_runs_at_once(queue):
    """Grading is CPU-bound and sandboxed. Exceeding the ceiling does not get
    more work done, it makes every run slower."""
    live = 0
    peak = 0
    lock = threading.Lock()

    def work():
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.03)
        with lock:
            live -= 1

    for index in range(40):
        queue.submit(work, key=f"lab-{index % 8}")
    assert queue.join(timeout=30)
    assert peak <= queue.workers


def test_one_flooded_lab_cannot_take_every_worker(queue):
    """The property a plain ThreadPoolExecutor does not give you: it is FIFO
    across everything, so the first lab to flood the queue owns the pool until
    it drains."""
    live_by_key: dict[str, int] = {}
    peak_by_key: dict[str, int] = {}
    lock = threading.Lock()

    def work(key):
        def run():
            with lock:
                live_by_key[key] = live_by_key.get(key, 0) + 1
                peak_by_key[key] = max(peak_by_key.get(key, 0), live_by_key[key])
            time.sleep(0.03)
            with lock:
                live_by_key[key] -= 1
        return run

    for _ in range(30):
        queue.submit(work("flooded"), key="flooded")
    for _ in range(4):
        queue.submit(work("quiet"), key="quiet")

    assert queue.join(timeout=30)
    assert peak_by_key["flooded"] <= queue.max_per_key
    # And the small lab actually got served rather than waiting for 30 jobs.
    assert peak_by_key.get("quiet", 0) >= 1


def test_the_small_lab_is_not_stuck_behind_the_flood(queue):
    """Ordering, not just eventual completion: work for a second assignment
    starts before a flood of the first has drained."""
    started: list[str] = []
    lock = threading.Lock()

    def work(key):
        def run():
            with lock:
                started.append(key)
            time.sleep(0.02)
        return run

    for _ in range(20):
        queue.submit(work("flooded"), key="flooded")
    queue.submit(work("quiet"), key="quiet")

    assert queue.join(timeout=30)
    assert "quiet" in started
    # It should be served early, not last: with a per-key ceiling of 2 it gets a
    # worker almost immediately rather than after all 20.
    assert started.index("quiet") < 10, "the quiet lab waited behind the flood"


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------
def test_marking_outranks_a_practice_run():
    """One decides a mark; the other is feedback the student can ask for again
    in a minute. Under load the mark goes first."""
    q = GradingQueue(workers=1, max_per_key=1)
    order: list[str] = []

    for index in range(4):
        q.submit(lambda i=index: order.append(f"preview-{i}"),
                 key="lab", priority=PRIORITY_PREVIEW)
    q.submit(lambda: order.append("grade"), key="lab", priority=PRIORITY_GRADE)

    q.start()
    assert q.join(timeout=30)
    q.stop(drain=False)
    assert order[0] == "grade"


def test_equal_priority_keeps_arrival_order():
    """A student who submitted first is not overtaken by one who submitted
    later at the same priority."""
    q = GradingQueue(workers=1, max_per_key=1)
    order: list[int] = []
    for index in range(12):
        q.submit(lambda i=index: order.append(i), key="lab")
    q.start()
    assert q.join(timeout=30)
    q.stop(drain=False)
    assert order == sorted(order)


# --------------------------------------------------------------------------
# Failure and shutdown
# --------------------------------------------------------------------------
def test_a_crashing_job_is_recorded_with_its_exception(queue):
    """The API maps SubmissionRejected to 409 and a rate limit to 429, which it
    cannot do from a formatted string - so the exception object is kept."""
    def boom():
        raise ValueError("bad bundle")

    job = queue.submit(boom, key="lab")
    assert job.wait(timeout=10)
    assert job.state == FAILED
    assert isinstance(job.exception, ValueError)
    assert "bad bundle" in job.error
    assert queue.stats()["failed"] == 1


def test_one_failure_does_not_stop_the_worker(queue):
    """A single bad submission must not take a worker down with it."""
    def boom():
        raise RuntimeError("nope")

    queue.submit(boom, key="lab")
    later = queue.submit(lambda: "fine", key="lab")
    assert later.wait(timeout=10)
    assert later.state == DONE
    assert later.result == "fine"


def test_shutdown_drains_accepted_work():
    """An accepted submission is a promise. Dropping it on shutdown loses a
    student's work after telling them it was received."""
    q = GradingQueue(workers=2, max_per_key=2)
    q.start()
    done: list[int] = []
    jobs = [q.submit(lambda i=i: done.append(i) or time.sleep(0.01), key="lab")
            for i in range(20)]
    q.stop(drain=True, timeout=30)
    assert len(done) == 20
    assert all(job.state == DONE for job in jobs)


def test_overload_is_a_wait_not_a_refusal():
    """Depth is a very high ceiling, not a throttle. It exists so a runaway
    client is eventually told to stop, not so students are turned away."""
    q = GradingQueue(workers=1, max_per_key=1, max_depth=4)
    # Not started: nothing drains, so everything lands in the backlog.
    for _ in range(4):
        q.submit(lambda: None, key="lab")
    with pytest.raises(QueueFull):
        q.submit(lambda: None, key="lab")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def test_the_queue_can_say_what_it_is_doing(queue):
    blocked = threading.Event()
    for _ in range(10):
        queue.submit(lambda: blocked.wait(timeout=5), key="lab")

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and queue.stats()["running"] == 0:
        time.sleep(0.01)

    stats = queue.stats()
    assert stats["workers"] == 4
    assert stats["running"] >= 1
    assert stats["depth"] >= 1
    assert "lab" in stats["by_assignment"]
    blocked.set()
    assert queue.join(timeout=30)


# --------------------------------------------------------------------------
# End to end: the deadline
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def setup():
    init_db()
    with TestClient(app) as client:
        with session_scope() as session:
            course = Course(code="QQ101", title="Queue course", term="T")
            session.add(course)
            session.flush()
            session.add(Concept(course_id=course.id, concept_key="c_sort",
                                name="Comparison sorting",
                                description="sort ascending order comparison exchange",
                                bloom_level=BloomLevel.APPLY))
            faculty = User(name="Prof Queue", role=Role.FACULTY)
            students = [User(name=f"Rusher {i}", role=Role.STUDENT) for i in range(24)]
            session.add_all([faculty, *students])
            session.flush()
            session.add(Enrollment(course_id=course.id, user_id=faculty.id, role=Role.FACULTY))
            for student in students:
                session.add(Enrollment(course_id=course.id, user_id=student.id, role=Role.STUDENT))
            session.flush()
            context = {"course": course.id, "faculty": faculty.id,
                       "students": [s.id for s in students]}

        created = client.post(
            f"/api/faculty/courses/{context['course']}/assignments",
            json={
                "faculty_id": context["faculty"],
                "title": "Sort a list",
                "brief": "Implement solve(nums) returning the input in ascending order.",
                "reference_solution": SORT,
            },
        ).json()
        context["assignment"] = created["assignment_id"]
        yield client, context


def test_a_ticket_comes_back_immediately_and_polls_to_a_result(setup):
    client, context = setup
    accepted = client.post("/api/submit", json={
        "assignment_id": context["assignment"],
        "student_id": context["students"][0],
        "files": {"solution.py": SORT},
        "force_full_run": True,
        "wait": False,
    })
    assert accepted.status_code == 202
    ticket = accepted.json()
    assert ticket["state"] in ("queued", "running")
    assert ticket["workers"] >= 1

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        polled = client.get(f"/api/jobs/{ticket['job_id']}")
        assert polled.status_code == 200
        body = polled.json()
        if body.get("state") == "done":
            assert body["detail"]["verdict"]["total_fraction"] > 0.9
            assert body["run_id"]
            return
        time.sleep(0.05)
    pytest.fail("the job never finished")


def test_a_deadline_rush_loses_nothing(setup):
    """Twenty students submit at once. Every one is marked, none is dropped,
    and the concurrency ceiling holds throughout."""
    client, context = setup
    students = context["students"][1:21]
    results: dict[str, dict] = {}
    errors: list[str] = []

    def rush(index, student_id):
        try:
            source = SORT.replace("def solve(nums):", f"def solve(nums):\n    _v = {index}")
            response = client.post("/api/submit", json={
                "assignment_id": context["assignment"],
                "student_id": student_id,
                "files": {"solution.py": source},
                "force_full_run": True,
            })
            if response.status_code != 200:
                errors.append(f"{student_id}: {response.status_code} {response.text[:120]}")
                return
            results[student_id] = response.json()
        except Exception as exc:                     # noqa: BLE001
            errors.append(f"{student_id}: {type(exc).__name__} {exc}")

    threads = [threading.Thread(target=rush, args=(i, s)) for i, s in enumerate(students)]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    elapsed = time.monotonic() - started

    assert not errors, f"submissions failed: {errors[:4]}"
    assert len(results) == len(students), "a submission was dropped"
    for body in results.values():
        assert body["detail"]["verdict"]["total_fraction"] > 0.9

    stats = queue_service.get_queue().stats()
    assert stats["failed"] == 0
    # Sanity, not a benchmark: 20 submissions on a laptop should not take
    # anywhere near the three-minute budget the deck claims.
    assert elapsed < 120, f"the rush took {elapsed:.0f}s"
