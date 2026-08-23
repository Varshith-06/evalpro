"""Faculty authoring with fields left blank.

The behaviour under test is the promise the feature makes: an instructor can
supply as little as a title and a brief, and what comes back is still a rubric
whose every item traces to something they wrote and can be shown to a student
as evidence.

The load-bearing safety rule is that **executable grading is not available
without a reference solution**. Admitting a generated test that nothing has
verified is how a whole cohort gets penalised for a hallucinated expected
output, so the mode is derived from the inputs rather than chosen.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="evalpro-authoring-"))
os.environ["EVALPRO_VAR"] = str(_TMP)
os.environ["EVALPRO_DATABASE_URL"] = f"sqlite:///{_TMP / 'authoring.db'}"
os.environ["EVALPRO_DEMO"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BloomLevel, Concept, Course, Enrollment, Role, User  # noqa: E402
from app.services import authoring_service, brief_analysis  # noqa: E402

BRIEF = """Write a program that reverses a linked list in place.
- Implement a function called reverse(head)
- You must use recursion rather than a loop
- Handle the empty list without crashing
- Do not use the built-in reversed
- Include comments explaining your approach
"""

RECURSIVE = '''"""Reverse a linked list."""


def reverse(head):
    # base case: nothing to reverse
    if not head:
        return None
    if head.get("next") is None:
        return head
    rest = reverse(head["next"])
    head["next"]["next"] = head
    head["next"] = None
    return rest
'''

ITERATIVE = """def reverse(head):
    previous = None
    current = head
    while current is not None:
        nxt = current.get("next")
        current["next"] = previous
        previous = current
        current = nxt
    return previous
"""


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def course(client):
    with session_scope() as session:
        row = Course(code="AUTH101", title="Authoring test course", term="T")
        session.add(row)
        session.flush()
        for key, name, description in [
            ("c_recursion", "Recursion", "recursion base case call stack recursive"),
            ("c_bounds", "Bounds checking", "empty index bounds guard length"),
            ("c_docs", "Documenting code", "comment document readable explain intent"),
        ]:
            session.add(
                Concept(
                    course_id=row.id, concept_key=key, name=name,
                    description=description, bloom_level=BloomLevel.APPLY,
                )
            )
        faculty = User(name="Prof Author", role=Role.FACULTY)
        student = User(name="Student One", role=Role.STUDENT)
        session.add_all([faculty, student])
        session.flush()
        session.add(Enrollment(course_id=row.id, user_id=faculty.id, role=Role.FACULTY))
        session.add(Enrollment(course_id=row.id, user_id=student.id, role=Role.STUDENT))
        session.flush()
        return {"id": row.id, "faculty": faculty.id, "student": student.id}


# --------------------------------------------------------------------------
# Reading a brief
# --------------------------------------------------------------------------
def test_brief_yields_a_check_for_every_stated_requirement():
    requirements = brief_analysis.analyse_brief(BRIEF, "reverse")
    kinds = {(r.static_check or {}).get("kind") for r in requirements}
    assert "function_defined" in kinds
    assert "recursion_present" in kinds
    assert "guard_present" in kinds
    assert "api_absent" in kinds
    assert "documented" in kinds
    # Every requirement must point back at the phrase that produced it.
    assert all(r.source_phrase for r in requirements)


def test_forbidden_api_is_read_out_of_the_brief():
    requirements = brief_analysis.analyse_brief("Do not use sorted anywhere in your solution.")
    forbid = [r for r in requirements if (r.static_check or {}).get("kind") == "api_absent"]
    assert forbid and forbid[0].static_check["target"] == "sorted"


def test_required_signature_arity_is_read():
    requirements = brief_analysis.analyse_brief("Implement a function called search(items, target).")
    defined = [r for r in requirements if (r.static_check or {}).get("kind") == "function_defined"]
    assert defined[0].static_check["target"] == "search"
    assert defined[0].static_check["arity"] == 2


def test_nothing_is_invented_from_an_empty_brief():
    """A rubric item the student was never told about is worse than none."""
    requirements = brief_analysis.analyse_brief("Do the thing.", "solve")
    # Only the entry point, which is implied by the assignment configuration.
    assert len(requirements) == 1
    assert requirements[0].static_check["kind"] == "function_defined"


# --------------------------------------------------------------------------
# Mode is derived, not chosen
# --------------------------------------------------------------------------
def test_grading_mode_follows_from_the_reference_solution():
    assert authoring_service.resolve_grading_mode("") == "static"
    assert authoring_service.resolve_grading_mode("   \n ") == "static"
    assert authoring_service.resolve_grading_mode("def solve(): return 1") == "executable"


def test_preview_shows_what_would_be_generated(client, course):
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments/preview",
        json={"brief": BRIEF, "entry_call": "reverse"},
    ).json()
    assert body["grading_mode"] == "static"
    assert body["rubric"]
    assert all(item["text"] for item in body["rubric"])
    # Concept tags are proposed from the course graph, not invented.
    tagged = [i for i in body["rubric"] if i["concept_ids"]]
    assert tagged, "at least one item should map onto the course concept graph"


# --------------------------------------------------------------------------
# Creating with fields blank
# --------------------------------------------------------------------------
def test_title_and_brief_alone_produce_a_publishable_assignment(client, course):
    response = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Reverse a linked list",
            "brief": BRIEF,
            "entry_call": "reverse",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published"] is True
    assert body["grading_mode"] == "static"
    assert "rubric" in body["generated"]
    assert body["rubric_items"] >= 4
    assert body["tests_admitted"] == 0
    course["static_assignment"] = body["assignment_id"]


def test_approach_grading_separates_a_good_submission_from_a_bad_one(client, course):
    assignment_id = course["static_assignment"]

    good = client.post("/api/submit", json={
        "assignment_id": assignment_id, "student_id": course["student"],
        "files": {"solution.py": RECURSIVE}, "force_full_run": True,
    }).json()["detail"]

    with session_scope() as session:
        other = User(name="Student Two", role=Role.STUDENT)
        session.add(other)
        session.flush()
        session.add(Enrollment(course_id=course["id"], user_id=other.id, role=Role.STUDENT))
        session.flush()
        other_id = other.id

    bad = client.post("/api/submit", json={
        "assignment_id": assignment_id, "student_id": other_id,
        "files": {"solution.py": ITERATIVE}, "force_full_run": True,
    }).json()["detail"]

    assert good["verdict"]["total_fraction"] > 0.85
    assert bad["verdict"]["total_fraction"] < 0.6
    # No test ran, and the evidence says why rather than looking like a failure.
    execute = next(s for s in good["stages"] if s["stage"] == "B4_execute")
    assert execute["status"] == "skipped"
    assert "no reference solution" in execute["summary"]
    assert not good["tests"]


def test_every_item_of_a_generated_rubric_can_earn_evidence(client, course):
    """The failure mode this guards against: an item with nothing behind it,
    which no submission can ever satisfy and which silently caps the grade."""
    detail = client.post("/api/submit", json={
        "assignment_id": course["static_assignment"], "student_id": course["student"],
        "files": {"solution.py": RECURSIVE}, "force_full_run": True,
    }).json()["detail"]
    for item in detail["items"]:
        assert item["signals"], f"{item['item_key']} produced no signal at all"
        assert item["evidence"], f"{item['item_key']} produced no readable evidence"


def test_a_reference_solution_switches_on_executable_grading(client, course):
    reference = "def solve(nums):\n    if not nums:\n        return []\n    return sorted(nums)\n"
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Sort a list",
            "brief": "Sort the input in ascending order. Handle the empty list.",
            "reference_solution": reference,
        },
    ).json()
    assert body["grading_mode"] == "executable"
    assert "tests" in body["generated"]
    assert body["tests_admitted"] > 0


def test_supplied_tests_are_discarded_when_there_is_nothing_to_validate_them(client, course):
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Approach only, with tests offered",
            "brief": "Explain and implement a queue. Handle the empty case.",
            "tests": [{"test_key": "tc_01", "args": [[]], "expected_output": "[]"}],
        },
    ).json()
    assert body["grading_mode"] == "static"
    assert body["tests_admitted"] == 0
    assert any("discarded" in note for note in body["notes"])


def test_a_supplied_rubric_is_used_verbatim(client, course):
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Instructor-written rubric",
            "brief": "Implement anything you like, but document it.",
            "rubric": [
                {
                    "text": "Uses recursion",
                    "weight": 10,
                    "checkable_by": ["static"],
                    "static_check": {"kind": "recursion_present"},
                },
            ],
        },
    ).json()
    assert body["rubric_items"] == 1
    assert "rubric" not in body["generated"]


def test_a_rubric_with_no_checkable_evidence_is_refused(client, course):
    """Every item would score zero forever, so this must not publish."""
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Unpublishable",
            "brief": "Write something good and elegant please.",
            "rubric": [{"text": "The code is elegant", "weight": 10, "checkable_by": ["manual"]}],
        },
    ).json()
    assert body["published"] is False
    assert any("static check" in note for note in body["notes"])


def test_an_unpublished_assignment_does_not_accept_submissions(client, course):
    created = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Saved as a draft",
            "brief": "Implement a function called draftonly(x). Handle the empty case.",
            "entry_call": "draftonly",
            "publish": False,
        },
    ).json()
    assert created["published"] is False
    response = client.post("/api/submit", json={
        "assignment_id": created["assignment_id"], "student_id": course["student"],
        "files": {"solution.py": "def draftonly(x):\n    return x\n"},
    })
    assert response.status_code == 409
    assert "approved" in response.text.lower()


def test_assignment_list_reports_mode_and_what_was_generated(client, course):
    rows = client.get(f"/api/faculty/courses/{course['id']}/assignments").json()
    assert rows
    static_rows = [r for r in rows if r["grading_mode"] == "static"]
    assert static_rows
    assert all(r["tests"] == 0 for r in static_rows)
    assert any("rubric" in r["generated_parts"] for r in rows)


# --------------------------------------------------------------------------
# Algorithm identification honesty
# --------------------------------------------------------------------------
def test_algorithm_identification_does_not_guess_from_substrings():
    """``lo`` and ``hi`` occur inside ordinary identifiers. Matching them as
    substrings reported a linked-list reversal as a binary search."""
    from app.engine.b1_structure import build_code_graph
    from app.engine.b5_partial import identify_algorithm

    graph = build_code_graph({"solution.py": RECURSIVE})
    matches = identify_algorithm(graph, RECURSIVE)
    assert not any(m["algorithm"] == "binary_search" for m in matches)


def test_structural_credit_does_not_punish_an_absent_reference():
    """0% similarity to a reference that does not exist is a statement about
    the assignment, not the student."""
    from app.engine.b1_structure import build_code_graph
    from app.engine.b5_partial import structural_credit

    graph = build_code_graph({"solution.py": RECURSIVE})
    credit = structural_credit(RECURSIVE, graph, reference_source="")
    assert credit.fraction > 0.3
    assert any("no reference solution" in line.lower() for line in credit.evidence)
