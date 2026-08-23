"""Editing a rubric after the fact, and the screens faculty need to run a lab.

The property that matters most here is that **editing an approved rubric does
not change a grade that has already been given**. Every run pins the version it
was marked against; if that version could be edited underneath it, "regrade this
next year and get the same answer" would stop being true, and a student's mark
could silently change without anybody regrading anything.
"""
from __future__ import annotations

import io
import zipfile

import pytest

# The database lives in a fresh temporary directory; see tests/conftest.py.
from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BloomLevel, Concept, Course, Enrollment, Role, User  # noqa: E402

BRIEF = """Write a function called tidy(items) that removes duplicates.
- Handle the empty list without crashing
- Do not use set
- Comment your approach
"""

SOLUTION = '''"""Remove duplicates, keeping first occurrence."""


def tidy(items):
    # nothing to do for an empty list
    if not items:
        return []
    seen = {}
    out = []
    for item in items:
        if item not in seen:
            seen[item] = True
            out.append(item)
    return out
'''


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def course(client):
    with session_scope() as session:
        row = Course(code="RUB101", title="Rubric editing course", term="T")
        session.add(row)
        session.flush()
        for key, name, description in [
            ("c_bounds", "Bounds checking", "empty index bounds guard length"),
            ("c_docs", "Documenting code", "comment document readable intent"),
        ]:
            session.add(Concept(course_id=row.id, concept_key=key, name=name,
                                description=description, bloom_level=BloomLevel.APPLY))
        faculty = User(name="Prof Edit", role=Role.FACULTY)
        students = [User(name=f"Student {i}", role=Role.STUDENT) for i in range(3)]
        session.add_all([faculty, *students])
        session.flush()
        session.add(Enrollment(course_id=row.id, user_id=faculty.id, role=Role.FACULTY))
        for student in students:
            session.add(Enrollment(course_id=row.id, user_id=student.id, role=Role.STUDENT))
        session.flush()
        return {"id": row.id, "faculty": faculty.id, "students": [s.id for s in students]}


@pytest.fixture(scope="module")
def assignment(client, course):
    body = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Remove duplicates",
            "brief": BRIEF,
            "entry_call": "tidy",
        },
    ).json()
    assert body["published"] is True
    detail = client.get(f"/api/assignments/{body['assignment_id']}").json()
    return {
        "id": body["assignment_id"],
        "version_id": detail["active_version"]["id"],
        "rubric": detail["active_version"]["rubric"],
    }


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------
def test_editing_an_approved_rubric_creates_a_new_version(client, course, assignment):
    items = [dict(item) for item in assignment["rubric"]]
    items[0]["weight"] = items[0]["weight"] + 4

    body = client.post(
        f"/api/faculty/versions/{assignment['version_id']}/rubric",
        json={"faculty_id": course["faculty"], "items": items, "note": "worth more"},
    ).json()

    assert body["created_new_version"] is True
    assert body["version"] == 2
    assert any(c["op"] == "update_weight" for c in body["changes"])
    assignment["version_id"] = body["version_id"]


def test_an_existing_mark_is_untouched_until_someone_regrades(client, course, assignment):
    """The reproducibility guarantee, tested rather than asserted."""
    student = course["students"][0]
    before = client.post("/api/submit", json={
        "assignment_id": assignment["id"], "student_id": student,
        "files": {"solution.py": SOLUTION}, "force_full_run": True,
    }).json()["detail"]
    graded_version = before["reproducibility"]["rubric_version"]

    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    items = [dict(item) for item in current["rubric"]]
    items[0]["text"] = "Completely rewritten criterion"
    items[0]["weight"] = 50
    client.post(
        f"/api/faculty/versions/{current['id']}/rubric",
        json={"faculty_id": course["faculty"], "items": items},
    )

    after = client.get(f"/api/runs/{before['run_id']}").json()
    assert after["verdict"]["total_fraction"] == pytest.approx(before["verdict"]["total_fraction"])
    assert after["reproducibility"]["rubric_version"] == graded_version


def test_regrading_moves_everyone_onto_the_new_rubric(client, course, assignment):
    for student in course["students"][1:]:
        client.post("/api/submit", json={
            "assignment_id": assignment["id"], "student_id": student,
            "files": {"solution.py": SOLUTION}, "force_full_run": True,
        })

    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    items = [dict(item) for item in current["rubric"]]
    items.append({
        "item_key": "rb_99",
        "text": "Splits the work into functions",
        "weight": 5,
        "checkable_by": ["static"],
        "static_check": {"kind": "min_functions", "min": 1},
        "concept_ids": [],
    })
    body = client.post(
        f"/api/faculty/versions/{current['id']}/rubric",
        json={"faculty_id": course["faculty"], "items": items, "regrade": True},
    ).json()

    assert body["regraded"] >= 3
    latest = client.get(f"/api/faculty/assignments/{assignment['id']}/submissions").json()
    graded = [s for s in latest["students"] if s["score"] is not None]
    assert graded
    detail = client.get(f"/api/runs/{graded[0]['run_id']}").json()
    assert any(item["item_key"] == "rb_99" for item in detail["items"])


def test_a_criterion_with_nothing_behind_it_is_refused(client, course, assignment):
    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    response = client.post(
        f"/api/faculty/versions/{current['id']}/rubric",
        json={
            "faculty_id": course["faculty"],
            "items": [{"text": "The code is elegant", "weight": 10, "checkable_by": ["manual"]}],
        },
    )
    assert response.status_code == 400
    assert "nothing behind them" in response.text


def test_an_empty_rubric_is_refused(client, course, assignment):
    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    response = client.post(
        f"/api/faculty/versions/{current['id']}/rubric",
        json={"faculty_id": course["faculty"], "items": []},
    )
    assert response.status_code == 400


def test_a_test_requirement_is_stripped_when_no_oracle_exists(client, course, assignment):
    """An approach-graded item asking for test evidence would be a mark no
    submission could ever earn."""
    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    items = [dict(item) for item in current["rubric"]]
    items[0]["checkable_by"] = ["test"]
    body = client.post(
        f"/api/faculty/versions/{current['id']}/rubric",
        json={"faculty_id": course["faculty"], "items": items},
    ).json()
    assert any("no reference solution" in r for r in body["repairs"])


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------
def test_unpublishing_stops_new_submissions(client, course, assignment):
    current = client.get(f"/api/assignments/{assignment['id']}").json()["active_version"]
    client.post(f"/api/faculty/versions/{current['id']}/publish",
                json={"faculty_id": course["faculty"], "published": False})

    response = client.post("/api/submit", json={
        "assignment_id": assignment["id"], "student_id": course["students"][0],
        "files": {"solution.py": "def tidy(items):\n    return items\n"},
    })
    assert response.status_code == 409

    client.post(f"/api/faculty/versions/{current['id']}/publish",
                json={"faculty_id": course["faculty"], "published": True})
    after = client.get(f"/api/assignments/{assignment['id']}").json()
    assert after["active_version"]["approved"] is True


# --------------------------------------------------------------------------
# The screens that run a lab
# --------------------------------------------------------------------------
def test_submissions_page_lists_students_who_have_not_submitted(client, course, assignment):
    """The absences are usually the most useful thing on the page."""
    body = client.get(f"/api/faculty/assignments/{assignment['id']}/submissions").json()
    assert body["summary"]["roster"] == len(course["students"])
    states = {s["state"] for s in body["students"]}
    assert states  # every student on the roster appears, submitted or not
    assert body["summary"]["submitted"] + body["summary"]["not_submitted"] == body["summary"]["roster"]


def test_gradebook_returns_a_student_by_assignment_matrix(client, course):
    body = client.get(f"/api/faculty/courses/{course['id']}/gradebook").json()
    assert body["assignments"]
    assert len(body["students"]) == len(course["students"])
    for student in body["students"]:
        assert len(student["marks"]) == len(body["assignments"])


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------
def test_a_single_file_can_be_uploaded(client, course, assignment):
    response = client.post(
        "/api/submit/upload",
        data={"assignment_id": assignment["id"], "student_id": course["students"][2],
              "force_full_run": "true"},
        files={"file": ("solution.py", SOLUTION.encode(), "text/x-python")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"] == ["solution.py"]


def test_a_zip_is_unpacked_and_marked(client, course, assignment):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("solution.py", SOLUTION)
        archive.writestr("notes.txt", "some notes")
    response = client.post(
        "/api/submit/upload",
        data={"assignment_id": assignment["id"], "student_id": course["students"][1],
              "force_full_run": "true"},
        files={"file": ("work.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    assert "solution.py" in response.json()["files"]


def test_a_decompression_bomb_is_rejected_at_upload(client, course, assignment):
    """An upload form is exactly where this arrives."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bomb.py", "A" * (12 * 1024 * 1024))
    response = client.post(
        "/api/submit/upload",
        data={"assignment_id": assignment["id"], "student_id": course["students"][0]},
        files={"file": ("bomb.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 400
    assert "bomb" in response.text.lower() or "exceeds" in response.text.lower()


# --------------------------------------------------------------------------
# Deleting
# --------------------------------------------------------------------------
def test_deleting_a_marked_assignment_needs_a_second_decision(client, course):
    """Throwing away a cohort's marks should take two decisions, not one
    misclick, and the refusal should say what would be lost."""
    created = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Temporary assignment",
            "brief": "Implement a function called temp(x). Handle the empty case.",
            "entry_call": "temp",
        },
    ).json()
    assignment_id = created["assignment_id"]

    client.post("/api/submit", json={
        "assignment_id": assignment_id, "student_id": course["students"][0],
        "files": {"solution.py": "def temp(x):\n    if not x:\n        return []\n    return x\n"},
        "force_full_run": True,
    })

    refused = client.delete(f"/api/faculty/assignments/{assignment_id}")
    assert refused.status_code == 409
    assert "already submitted" in refused.text

    confirmed = client.delete(f"/api/faculty/assignments/{assignment_id}?confirm=true")
    assert confirmed.status_code == 200
    assert client.get(f"/api/assignments/{assignment_id}").status_code == 404


def test_an_unused_assignment_deletes_without_a_prompt(client, course):
    created = client.post(
        f"/api/faculty/courses/{course['id']}/assignments",
        json={
            "faculty_id": course["faculty"],
            "title": "Never used",
            "brief": "Implement a function called unused(x). Handle the empty case.",
            "entry_call": "unused",
        },
    ).json()
    response = client.delete(f"/api/faculty/assignments/{created['assignment_id']}")
    assert response.status_code == 200
    assert response.json()["submissions_removed"] == 0
