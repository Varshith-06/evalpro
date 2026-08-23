"""Partial credit: is the idea marked, or only the output?

The complaint that destroys trust in an autograder is a student who plainly
understood the problem being told they got nothing because a single character
was wrong. So the properties here are about the *ordering* of outcomes rather
than exact numbers:

    correct  >  right idea with a bug  >  unbuildable but right shape  >  nothing

and the rule that makes it safe: structural credit can only ever raise an item.
If it could lower one, every correct submission would be quietly taxed by a
similarity heuristic.
"""
from __future__ import annotations

import pytest

# The database lives in a fresh temporary directory; see tests/conftest.py.
from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.engine.b1_structure import build_code_graph  # noqa: E402
from app.engine.b5_partial import structural_credit  # noqa: E402
from app.engine.b7_gate import Signal, aggregate_item, signal_agreement, weighted_score  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BloomLevel, Concept, Course, Enrollment, Role, User  # noqa: E402
from app.seed_data import ASSIGNMENTS  # noqa: E402

SORT = ASSIGNMENTS[0]["reference"]
DESCENDING = SORT.replace("if items[j] < items[smallest]:", "if items[j] > items[smallest]:")
NEVER_SORTS = SORT.replace("            smallest = j", "            smallest = i")
UNBUILDABLE = (
    SORT.replace("def solve(nums):", "def solve(nums)")
    .replace("if not nums:", "if not nums")
    .replace("return items", "return items(")
)
NOTHING = "def solve(nums):\n    return []\n"


# --------------------------------------------------------------------------
# The rule that makes structural credit safe
# --------------------------------------------------------------------------
def test_structural_credit_can_only_raise_an_item():
    """A reliability-weighted mean rises exactly when the added signal beats
    the current score, which is why the cascade tests that before adding it."""
    for current, added in ((0.0, 0.7), (0.4, 0.7), (0.9, 0.7), (1.0, 0.7)):
        before = [Signal("test", current, 1.0)]
        after = before + [Signal("structural", added, 0.55, corroborating=False)]
        if added > current:
            assert weighted_score(after) > weighted_score(before)
        else:
            assert weighted_score(after) <= weighted_score(before)


def test_partial_credit_does_not_count_as_disagreement():
    """Tests measure output and structural credit measures comprehension. Both
    can be true at once, so pairing them must not read as a conflict and send
    the submission to a human."""
    failing_test = [Signal("test", 0.0, 1.0)]
    with_credit = failing_test + [Signal("structural", 0.8, 0.55, corroborating=False)]
    assert signal_agreement(with_credit) == pytest.approx(signal_agreement(failing_test))

    # A genuine second opinion still does count.
    conflicting = failing_test + [Signal("static", 1.0, 0.85)]
    assert signal_agreement(conflicting) < signal_agreement(failing_test)


def test_a_failing_item_with_the_right_approach_is_not_zero():
    item = aggregate_item(
        "rb_01", ["c_x"], 10.0,
        [Signal("test", 0.0, 1.0), Signal("structural", 0.7, 0.55, corroborating=False)],
        [], {"declared_checks": ["test"], "test_pass_rate": 0.0},
    )
    assert item.score_fraction > 0.2


# --------------------------------------------------------------------------
# Structural credit itself
# --------------------------------------------------------------------------
def test_structural_credit_separates_a_real_attempt_from_nothing():
    for source in (SORT, DESCENDING, NEVER_SORTS):
        graph = build_code_graph({"solution.py": source})
        assert structural_credit(source, graph, SORT).fraction > 0.5
    graph = build_code_graph({"solution.py": NOTHING})
    assert structural_credit(NOTHING, graph, SORT).fraction < 0.2


def test_credit_survives_code_that_does_not_compile():
    """pq-grams need an AST, and code that fails to build has none - which is
    exactly the submission this credit exists for."""
    graph = build_code_graph({"solution.py": UNBUILDABLE})
    assert not graph.parsed
    assert graph.functions, "the error-tolerant parse should still recover the function"
    assert structural_credit(UNBUILDABLE, graph, SORT).fraction > 0.4


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def setup():
    init_db()
    with TestClient(app) as client:
        with session_scope() as session:
            course = Course(code="PC101", title="Partial credit course", term="T")
            session.add(course)
            session.flush()
            for key, name, description in [
                ("c_sort", "Comparison sorting", "sort ascending order comparison exchange"),
                ("c_bounds", "Bounds checking", "empty index bounds guard length"),
            ]:
                session.add(Concept(course_id=course.id, concept_key=key, name=name,
                                    description=description, bloom_level=BloomLevel.APPLY))
            faculty = User(name="Prof Credit", role=Role.FACULTY)
            students = [User(name=f"Candidate {i}", role=Role.STUDENT) for i in range(6)]
            session.add_all([faculty, *students])
            session.flush()
            session.add(Enrollment(course_id=course.id, user_id=faculty.id, role=Role.FACULTY))
            for student in students:
                session.add(Enrollment(course_id=course.id, user_id=student.id, role=Role.STUDENT))
            session.flush()
            context = {
                "course": course.id,
                "faculty": faculty.id,
                "students": [s.id for s in students],
            }

        created = client.post(
            f"/api/faculty/courses/{context['course']}/assignments",
            json={
                "faculty_id": context["faculty"],
                "title": "Sort a list",
                "brief": (
                    "Implement a function called solve(nums) returning the input in ascending "
                    "order.\n- Handle the empty list without crashing\n"
                    "- Do not use the built-in sorted\n"
                ),
                "reference_solution": SORT,
            },
        ).json()
        assert created["tests_admitted"] > 0
        context["assignment"] = created["assignment_id"]
        yield client, context


def _score(client, context, index, source):
    detail = client.post("/api/submit", json={
        "assignment_id": context["assignment"],
        "student_id": context["students"][index],
        "files": {"solution.py": source},
        "force_full_run": True,
    }).json()["detail"]
    tests = [t for t in detail["tests"] if t["outcome"] != "skipped"]
    return detail["verdict"]["total_fraction"], sum(1 for t in tests if t["outcome"] == "pass")


def test_the_credit_ladder_is_ordered(setup):
    client, context = setup
    correct, correct_passed = _score(client, context, 0, SORT)
    descending, descending_passed = _score(client, context, 1, DESCENDING)
    unbuildable, _ = _score(client, context, 2, UNBUILDABLE)
    nothing, _ = _score(client, context, 3, NOTHING)

    assert correct_passed > descending_passed
    assert correct > descending > unbuildable > nothing

    # The headline promise: understanding the problem is worth real marks even
    # when the output is wrong throughout.
    assert descending > 0.35, "a right approach with a wrong comparison should not be near zero"
    # And it is still clearly separated from a correct answer.
    assert descending < correct - 0.2
    # Code that understood nothing earns almost nothing.
    assert nothing < 0.3
    assert descending > nothing + 0.25, "understanding the problem must be worth real marks"


def test_a_syntax_slip_keeps_almost_all_the_marks(setup):
    client, context = setup
    slipped, passed = _score(client, context, 4, SORT.replace("def solve(nums):", "def solve(nums)"))
    assert passed > 0, "the repaired source should still run the tests"
    assert slipped > 0.85


def test_correct_work_is_not_taxed_by_partial_credit(setup):
    """Structural credit must never pull a fully correct submission below full
    marks - if it could, the mechanism would cost more than it gives."""
    client, context = setup
    correct, _ = _score(client, context, 5, SORT)
    assert correct > 0.95
