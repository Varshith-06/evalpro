"""The administrator view. One landing question: *where is this programme weak?*

CO-PO attainment with drill-down to evidence, cohort trends, an at-risk view
routed to advising, platform trust metrics, the integrity dashboard, and system
health.

Two things on this surface are deliberately uncomfortable and deliberately
prominent: the bias audit, which can report that the risk model must not be
deployed, and the platform trust metrics, which include the numbers that would
show the grader drifting away from human judgement.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import (
    Assignment,
    Course,
    EvaluationRun,
    SimilarityPair,
    StudentRiskState,
    Submission,
    SubmissionAttempt,
    User,
    Verdict,
    VerdictState,
)
from ..services import analytics_service, grading_service, metrics_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/courses/{course_id}/attainment")
def attainment(course_id: str, session: Session = Depends(get_session)) -> dict:
    """Direct CO-PO attainment, computed from evidence, exportable."""
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "course not found")
    return analytics_service.compute_attainment(session, course_id)


@router.get("/courses/{course_id}/risk")
def risk_view(course_id: str, session: Session = Depends(get_session)) -> dict:
    """At-risk cohort view. Ranked factors, routed to support, never a bare score."""
    rows = session.scalars(
        select(StudentRiskState)
        .where(StudentRiskState.course_id == course_id)
        .order_by(StudentRiskState.risk_score.desc())
    ).all()
    names = {u.id: u.name for u in session.scalars(select(User))}

    flagged = [r for r in rows if r.first_flagged_at is not None]
    return {
        "question": "Where is this programme weak, and who needs support?",
        "policy": (
            "Early warning routes to support, never to sanction. A warning that becomes a punishment "
            "mechanism is gamed within one semester and destroys the data it depends on."
        ),
        "cohort_size": len(rows),
        "flagged": len(flagged),
        "students": [
            {
                "student_id": r.student_id,
                "student_name": names.get(r.student_id, r.student_id),
                "risk_score": round(r.risk_score, 4),
                "flagged": r.first_flagged_at is not None,
                "first_flagged_at": r.first_flagged_at.isoformat() if r.first_flagged_at else None,
                "routed_to": r.routed_to,
                "contributing_factors": r.contributing_factors,
            }
            for r in rows
        ],
    }


@router.get("/courses/{course_id}/bias-audit")
def bias_audit(course_id: str, session: Session = Depends(get_session)) -> dict:
    """Re-run the demographic bias audit for the risk model.

    Non-negotiable before deployment and re-run every semester. Protected
    attributes are used only here; they are never model features.
    """
    assessments = analytics_service.recompute_risk(session, course_id)
    audit = analytics_service.run_bias_audit(session, course_id, assessments)
    session.commit()
    return audit


@router.get("/courses/{course_id}/metrics")
def metrics(course_id: str, session: Session = Depends(get_session)) -> dict:
    """§11 platform trust metrics. Is the grader still agreeing with humans?"""
    rows = metrics_service.platform_metrics(session, course_id)
    return {
        "metrics": [m.as_dict() for m in rows],
        "note": (
            "Hold out a stratified faculty-graded sample every semester. Without it you cannot "
            "detect drift, and grading drift is invisible until it isn't."
        ),
    }


@router.get("/courses/{course_id}/integrity")
def integrity(course_id: str, session: Session = Depends(get_session)) -> dict:
    """Integrity dashboard: ranked similarity with aligned regions, never verdicts."""
    assignment_ids = [
        a for a in session.scalars(select(Assignment.id).where(Assignment.course_id == course_id))
    ]
    pairs = session.scalars(
        select(SimilarityPair)
        .where(SimilarityPair.assignment_id.in_(assignment_ids))
        .order_by(SimilarityPair.combined.desc())
    ).all()
    names = {u.id: u.name for u in session.scalars(select(User))}
    assignments = {
        a.id: a.code for a in session.scalars(select(Assignment).where(Assignment.id.in_(assignment_ids)))
    }

    return {
        "policy": (
            "Ranked similarity evidence with aligned code regions. The platform makes no determination "
            "of misconduct: machine-generated-code detectors are not reliable enough to carry "
            "consequences, and automated accusations are a legal and ethical liability."
        ),
        "pairs": [
            {
                "id": p.id,
                "assignment": assignments.get(p.assignment_id, ""),
                "student_a": names.get(p.student_id_a, p.student_id_a),
                "student_b": names.get(p.student_id_b, p.student_id_b),
                "combined": round(p.combined, 4),
                "token_similarity": round(p.token_similarity, 4),
                "structural_similarity": round(p.structural_similarity, 4),
                "corpus": p.corpus,
                "reviewed": p.reviewed,
                "aligned_regions": p.aligned_regions[:3],
            }
            for p in pairs[:40]
        ],
    }


@router.get("/courses/{course_id}/trends")
def trends(course_id: str, session: Session = Depends(get_session)) -> dict:
    """Cohort and assignment trends, plus section comparison."""
    assignments = session.scalars(
        select(Assignment).where(Assignment.course_id == course_id).order_by(Assignment.due_at)
    ).all()
    from ..analytics import item_analysis

    per_assignment = []
    for assignment in assignments:
        verdicts = session.execute(
            select(Verdict)
            .join(EvaluationRun, EvaluationRun.id == Verdict.run_id)
            .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
            .join(Submission, Submission.id == SubmissionAttempt.submission_id)
            .where(Submission.assignment_id == assignment.id, EvaluationRun.visible_only.is_(False))
        ).scalars().all()
        scores = [v.total_fraction for v in verdicts]
        if not scores:
            continue
        per_assignment.append(
            {
                "assignment": assignment.code,
                "title": assignment.title,
                "n": len(scores),
                "released": sum(1 for v in verdicts if v.state == VerdictState.RELEASED),
                "escalated": sum(1 for v in verdicts if v.state == VerdictState.ESCALATED),
                **item_analysis.cohort_distribution(scores),
            }
        )

    return {"assignments": per_assignment}


@router.get("/courses/{course_id}/gradebook")
def gradebook(course_id: str, session: Session = Depends(get_session)) -> dict:
    """The LTI Assignment and Grade Services writeback payload."""
    rows = grading_service.gradebook_rows(session, course_id)
    return {
        "rows": rows,
        "note": (
            "Grade writeback means faculty never maintain two gradebooks. That is the difference "
            "between a tool that is adopted and one that is admired and unused."
        ),
    }


@router.get("/system-health")
def system_health(session: Session = Depends(get_session)) -> dict:
    """Queue depth, latency, and the honest isolation report for this host."""
    return metrics_service.system_health(session)


@router.post("/courses/{course_id}/refresh")
def refresh(course_id: str, session: Session = Depends(get_session)) -> dict:
    """Recompute Layer 2 and Layer 3 for the course. Idempotent."""
    result = analytics_service.refresh_course_analytics(session, course_id)
    session.commit()
    return result


@router.post("/train-confidence")
def train_confidence(session: Session = Depends(get_session)) -> dict:
    """Retrain the confidence estimator on accumulated faculty overrides."""
    return metrics_service.train_confidence_model(session)
