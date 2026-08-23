"""The student view. One landing question: *what should I work on next?*

The landing payload is the **mastery map**, not a grade list. Scores are a
drill-in. Uncertainty is shown honestly rather than rounded away, because "we
don't have enough evidence about your recursion yet" is a useful thing for a
student to know and a dishonest thing to hide.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..services import analytics_service
from ..analytics import remediation
from ..models import (
    Assignment,
    Concept,
    EvaluationRun,
    Submission,
    SubmissionAttempt,
    User,
    Verdict,
    VerdictState,
)

router = APIRouter(prefix="/api/student", tags=["student"])


@router.get("/{student_id}/courses/{course_id}")
def student_dashboard(
    student_id: str, course_id: str, session: Session = Depends(get_session)
) -> dict:
    student = session.get(User, student_id)
    if student is None:
        raise HTTPException(404, "student not found")

    graph = analytics_service.load_concept_graph(session, course_id)
    snapshots = analytics_service.mastery_snapshots(session, course_id).get(student_id, {})
    recommendations = remediation.recommend_for_student(graph, snapshots, limit=4)

    concepts = session.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    keys = {c.concept_key for c in concepts}

    nodes = []
    for concept in sorted(concepts, key=lambda c: (c.syllabus_week or 99, c.name)):
        snapshot = snapshots.get(concept.concept_key)
        nodes.append(
            {
                "id": concept.concept_key,
                "name": concept.name,
                "week": concept.syllabus_week,
                "outcomes": concept.course_outcomes,
                "mastery": round(snapshot.mastery, 3) if snapshot else None,
                "uncertainty": round(snapshot.uncertainty, 3) if snapshot else 1.0,
                "evidence_count": snapshot.evidence_count if snapshot else 0,
                "state": _state(snapshot),
                "prerequisite_gap": _is_prerequisite_gap(concept.concept_key, graph, snapshots),
            }
        )
    edges = [
        {"from": p, "to": c.concept_key}
        for c in concepts
        for p in (c.prerequisites or [])
        if p in keys
    ]

    return {
        "student": {"id": student.id, "name": student.name, "external_id": student.external_id},
        "question": "What should I work on next?",
        "mastery_map": {"nodes": nodes, "edges": edges},
        "summary": _summary(nodes),
        "next_actions": [r.as_dict() for r in recommendations],
        "assignments": _assignment_rows(session, student_id, course_id),
        "trajectories": _trajectories(session, student_id, course_id),
        "disclosure": (
            "This platform infers concept-level mastery estimates from your submissions. Those "
            "inferences are personal data: you can see every one of them here, along with the "
            "specific evidence behind it, and you can appeal any rubric item."
        ),
    }


def _state(snapshot) -> str:
    if snapshot is None or snapshot.evidence_count == 0:
        return "no_evidence"
    if snapshot.uncertainty >= settings.analytics.uncertainty_diagnostic_threshold and snapshot.evidence_count < settings.analytics.min_evidence_for_confidence:
        return "uncertain"
    if snapshot.mastery >= settings.analytics.mastery_threshold:
        return "mastered"
    if snapshot.mastery >= 0.45:
        return "developing"
    return "gap"


def _is_prerequisite_gap(concept_key: str, graph, snapshots) -> bool:
    """True when this concept is holding up something later.

    Highlighting these is the difference between a mastery map and a heat map:
    it points at the concept whose repair unblocks the most.
    """
    snapshot = snapshots.get(concept_key)
    if snapshot is None or snapshot.mastery >= settings.analytics.mastery_threshold:
        return False
    return any(concept_key in node.prerequisites for node in graph.values())


def _summary(nodes: list[dict]) -> dict:
    tracked = [n for n in nodes if n["evidence_count"] > 0]
    return {
        "concepts_total": len(nodes),
        "concepts_with_evidence": len(tracked),
        "mastered": sum(1 for n in tracked if n["state"] == "mastered"),
        "developing": sum(1 for n in tracked if n["state"] == "developing"),
        "gaps": sum(1 for n in tracked if n["state"] == "gap"),
        "uncertain": sum(1 for n in tracked if n["state"] == "uncertain"),
        "mean_mastery": round(
            sum(n["mastery"] or 0 for n in tracked) / len(tracked), 3
        ) if tracked else None,
    }


def _assignment_rows(session: Session, student_id: str, course_id: str) -> list[dict]:
    assignments = session.scalars(
        select(Assignment).where(Assignment.course_id == course_id).order_by(Assignment.due_at)
    ).all()
    rows = []
    for assignment in assignments:
        submission = session.scalar(
            select(Submission).where(
                Submission.assignment_id == assignment.id, Submission.student_id == student_id
            )
        )
        attempts = submission.attempts if submission else []
        history = []
        for attempt in attempts:
            for run in attempt.runs:
                verdict = run.verdict
                if verdict is None:
                    continue
                history.append(
                    {
                        "run_id": run.id,
                        "attempt_no": attempt.attempt_no,
                        "at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
                        "score": round(verdict.total_fraction, 4),
                        "state": verdict.state.value,
                        "visible_only": run.visible_only,
                        "confidence": round(verdict.confidence, 3),
                    }
                )
        latest = history[-1] if history else None
        deltas = [
            round(history[i]["score"] - history[i - 1]["score"], 4) for i in range(1, len(history))
        ]
        rows.append(
            {
                "assignment_id": assignment.id,
                "code": assignment.code,
                "title": assignment.title,
                "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
                "attempts": len(attempts),
                "latest": latest,
                "attempt_deltas": deltas,
                "released": bool(latest and latest["state"] in ("released", "overridden")),
            }
        )
    return rows


def _trajectories(session: Session, student_id: str, course_id: str) -> list[dict]:
    from ..models import StudentConceptMastery

    rows = session.scalars(
        select(StudentConceptMastery).where(
            StudentConceptMastery.course_id == course_id,
            StudentConceptMastery.student_id == student_id,
        )
    ).all()
    out = []
    for row in rows:
        points = [p for p in (row.trajectory or []) if "estimate" in p]
        if len(points) < 2:
            continue
        out.append(
            {
                "concept": row.concept_key,
                "points": [
                    {
                        "t": p.get("t"),
                        "estimate": p.get("estimate"),
                        "uncertainty": p.get("uncertainty"),
                        "source": p.get("source", "observation"),
                    }
                    for p in points[-20:]
                ],
            }
        )
    out.sort(key=lambda t: t["points"][-1]["estimate"])
    return out[:12]


@router.get("/{student_id}/courses/{course_id}/runs/{run_id}")
def run_breakdown(
    student_id: str, course_id: str, run_id: str, session: Session = Depends(get_session)
) -> dict:
    """Per-assignment drill-in.

    Not "72/100" but *"Empty-input handling: 0/8. Test 11 crashed with
    IndexError at solution.py:14. No length guard found on the input path."*
    Hidden tests are stripped from any run a student can reach.
    """
    from ..services import grading_service

    try:
        detail = grading_service.run_detail(session, run_id)
    except LookupError as exc:
        raise HTTPException(404, "run not found") from exc
    if detail["student_id"] != student_id:
        raise HTTPException(403, "this run belongs to another student")

    if detail["visible_only"]:
        detail["tests"] = [t for t in detail["tests"] if not t["hidden"]]
        detail["hidden_test_note"] = (
            "This is pre-deadline feedback, so only the visible test subset was run. "
            "Hidden tests never appear here."
        )

    verdict_state = detail["verdict"]["state"]
    if verdict_state == "escalated":
        detail["student_note"] = (
            "This submission is with a human reviewer. The system flagged it as one it should not "
            "release on its own, and the reasons are listed above."
        )
    return detail
