"""The faculty view. One landing question: *what should I teach next?*

Course health first: cohort mastery by concept, re-teach signals ranked by
downstream impact, broken-item alerts, and an intervention list. The review
queue is a drill-in, sorted by the expected value of attention rather than by
arrival time.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics import item_analysis, remediation
from ..db import get_session
from ..models import (
    Appeal,
    AppealState,
    Assignment,
    Concept,
    Course,
    EvaluationRun,
    MisconceptionCluster,
    RubricItem,
    RubricItemStats,
    SimilarityPair,
    Submission,
    SubmissionAttempt,
    User,
    Verdict,
    VerdictState,
)
from ..services import analytics_service, authoring_service, brief_analysis, grading_service, metrics_service

router = APIRouter(prefix="/api/faculty", tags=["faculty"])


class NewAssignmentRequest(BaseModel):
    """Everything an instructor might fill in. Only title and brief are needed.

    Leaving ``reference_solution`` blank switches the assignment to
    approach-graded: no tests are generated, none run, and the rubric draws on
    static and structural evidence instead. Leaving ``rubric`` blank has the
    platform read one out of the brief.
    """

    faculty_id: str
    title: str = Field(..., min_length=3)
    brief: str = Field(..., min_length=10)
    code: str | None = None
    entry_point: str = "solution.py"
    entry_call: str = "solve"
    reference_solution: str = ""
    requires_report: bool = False
    due_at: datetime | None = None
    opens_at: datetime | None = None
    max_attempts: int = Field(10, ge=1, le=50)
    rubric: list[dict] | None = None
    tests: list[dict] | None = None
    publish: bool = True


class PreviewRequest(BaseModel):
    brief: str
    entry_call: str = "solve"
    reference_solution: str = ""
    requires_report: bool = False


class OverrideRequest(BaseModel):
    faculty_id: str
    item_key: str
    score_fraction: float = Field(..., ge=0.0, le=1.0)
    reason: str


class ConfirmRequest(BaseModel):
    faculty_id: str
    note: str = ""


class LabelRequest(BaseModel):
    faculty_id: str
    label: str


class AppealResolution(BaseModel):
    faculty_id: str
    upheld: bool
    note: str = ""


@router.get("/courses/{course_id}/assignments")
def faculty_assignments(course_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Assignment list with the numbers a lecturer actually opens this page for."""
    assignments = session.scalars(
        select(Assignment).where(Assignment.course_id == course_id).order_by(Assignment.due_at)
    ).all()
    out = []
    for assignment in assignments:
        version = assignment.active_version
        rows = session.execute(
            select(Verdict)
            .join(EvaluationRun, EvaluationRun.id == Verdict.run_id)
            .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
            .join(Submission, Submission.id == SubmissionAttempt.submission_id)
            .where(Submission.assignment_id == assignment.id, EvaluationRun.visible_only.is_(False))
        ).scalars().all()
        students = session.scalar(
            select(func.count(Submission.id)).where(Submission.assignment_id == assignment.id)
        ) or 0
        scores = [v.total_fraction for v in rows]
        out.append(
            {
                "id": assignment.id,
                "code": assignment.code,
                "title": assignment.title,
                "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
                "requires_report": assignment.requires_report,
                "published": bool(version and version.approved_at),
                "grading_mode": version.grading_mode if version else "executable",
                "generated_parts": version.generated_parts if version else [],
                "rubric_items": len(version.rubric_items) if version else 0,
                "tests": sum(1 for t in version.test_cases if t.validated_against_reference)
                if version
                else 0,
                "submissions": students,
                "graded": len(rows),
                "needs_review": sum(1 for v in rows if v.state == VerdictState.ESCALATED),
                "average": round(sum(scores) / len(scores), 4) if scores else None,
            }
        )
    return out


@router.post("/courses/{course_id}/assignments/preview")
def preview_assignment(
    course_id: str, payload: PreviewRequest, session: Session = Depends(get_session)
) -> dict:
    """Show what would be generated, before anything is created.

    An instructor should be able to see the rubric the platform read out of
    their brief and decide whether to accept it, edit it, or write their own -
    without first committing an assignment to the course.
    """
    concepts = list(session.scalars(select(Concept).where(Concept.course_id == course_id)))
    mode = authoring_service.resolve_grading_mode(payload.reference_solution)
    drafter = authoring_service.HeuristicDrafter()
    items = drafter.draft_rubric(
        payload.brief, concepts, mode, payload.entry_call, payload.requires_report
    )
    requirements = brief_analysis.analyse_brief(payload.brief, payload.entry_call)
    concept_names = {c.concept_key: c.name for c in concepts}
    return {
        "grading_mode": mode,
        "grading_mode_explained": authoring_service.GRADING_MODES[mode],
        "summary": brief_analysis.summarise(requirements),
        "rubric": [
            {
                "item_key": item.item_key,
                "text": item.text,
                "category": item.category,
                "weight": item.weight,
                "checkable_by": item.checkable_by,
                "static_check": item.static_check,
                "concept_ids": item.concept_ids,
                "concept_names": [concept_names.get(c, c) for c in item.concept_ids],
            }
            for item in items
        ],
        "read_from_brief": [
            {"text": r.text, "from": r.source_phrase, "check": r.static_check}
            for r in requirements
        ],
    }


@router.post("/courses/{course_id}/assignments")
def create_assignment(
    course_id: str, payload: NewAssignmentRequest, session: Session = Depends(get_session)
) -> dict:
    """Create an assignment from a partially-filled form."""
    if session.get(Course, course_id) is None:
        raise HTTPException(404, "course not found")

    spec = authoring_service.AssignmentDraftSpec(
        title=payload.title,
        brief=payload.brief,
        code=payload.code,
        entry_point=payload.entry_point,
        entry_call=payload.entry_call,
        reference_solution=payload.reference_solution,
        requires_report=payload.requires_report,
        due_at=payload.due_at,
        opens_at=payload.opens_at,
        max_attempts=payload.max_attempts,
        rubric=payload.rubric,
        tests=payload.tests,
        approve_immediately=payload.publish,
    )
    try:
        result = authoring_service.create_assignment(session, course_id, payload.faculty_id, spec)
    except authoring_service.SchemaViolation as exc:
        raise HTTPException(400, f"Rubric or test definition rejected: {exc}") from exc
    session.commit()

    version = result.version
    return {
        "assignment_id": version.assignment_id,
        "version_id": version.id,
        "published": version.approved_at is not None,
        "grading_mode": result.grading_mode,
        "grading_mode_explained": authoring_service.GRADING_MODES[result.grading_mode],
        "generated": result.generated_parts,
        "notes": result.notes,
        "schema_repairs": result.repairs,
        "reference_validation": {
            "admitted": result.validation.admitted,
            "discarded": result.validation.discarded,
            "halted": result.halted,
            "message": result.message,
        },
        "rubric_items": len(version.rubric_items),
        "tests_admitted": sum(1 for t in version.test_cases if t.validated_against_reference),
    }


@router.get("/courses/{course_id}/health")
def course_health(course_id: str, current_week: int = 8, session: Session = Depends(get_session)) -> dict:
    graph = analytics_service.load_concept_graph(session, course_id)
    snapshots = analytics_service.mastery_snapshots(session, course_id)
    students = analytics_service.enrolled_students(session, course_id)
    names = {s.id: s.name for s in students}

    reteach = remediation.reteach_signals(graph, snapshots)
    pacing = remediation.pacing_feedback(graph, snapshots, current_week)
    interventions = remediation.intervention_list(snapshots, graph, names)

    return {
        "question": "What should I teach next?",
        "heatmap": _heatmap(graph, snapshots, names),
        "reteach_signals": [s.as_dict() for s in reteach[:8]],
        "pacing": pacing[:6],
        "broken_items": _broken_items(session, course_id),
        "interventions": interventions,
        "misconceptions": _clusters(session, course_id),
        "queue_depth": len(grading_service.review_queue(session, course_id, limit=500)),
        "cohort_distribution": _distribution(session, course_id),
    }


def _heatmap(graph, snapshots, names) -> dict:
    concept_keys = sorted(graph, key=lambda k: (graph[k].syllabus_week or 99, graph[k].name))
    rows = []
    for student_id, concepts in sorted(snapshots.items(), key=lambda kv: names.get(kv[0], kv[0])):
        rows.append(
            {
                "student_id": student_id,
                "student_name": names.get(student_id, student_id),
                "cells": [
                    {
                        "concept": key,
                        "mastery": round(concepts[key].mastery, 3) if key in concepts else None,
                        "uncertainty": round(concepts[key].uncertainty, 3) if key in concepts else 1.0,
                    }
                    for key in concept_keys
                ],
            }
        )
    column_means = []
    for key in concept_keys:
        values = [c[key].mastery for c in snapshots.values() if key in c]
        column_means.append(
            {
                "concept": key,
                "name": graph[key].name,
                "week": graph[key].syllabus_week,
                "mean": round(sum(values) / len(values), 3) if values else None,
                "n": len(values),
            }
        )
    return {"concepts": column_means, "rows": rows}


def _broken_items(session: Session, course_id: str) -> list[dict]:
    assignments = {
        a.id: a for a in session.scalars(select(Assignment).where(Assignment.course_id == course_id))
    }
    stats = session.scalars(
        select(RubricItemStats).where(RubricItemStats.assignment_id.in_(list(assignments)))
    ).all()
    item_text = {
        i.item_key: i.text
        for i in session.scalars(select(RubricItem))
    }
    severity = {
        "anticorrelated": 0, "misaligned_concepts": 1, "non_discriminating": 2,
        "too_hard": 3, "too_easy": 4, "insufficient_data": 5, "ok": 6,
    }
    rows = [
        {
            "item_key": s.item_key,
            "item_text": item_text.get(s.item_key, ""),
            "assignment": assignments[s.assignment_id].code if s.assignment_id in assignments else "",
            "n": s.n,
            "difficulty": round(s.difficulty, 3),
            "discrimination": round(s.discrimination, 3),
            "concept_alignment": round(s.concept_alignment, 3),
            "flag": s.flag,
        }
        for s in stats
        if s.flag not in ("ok", "insufficient_data")
    ]
    rows.sort(key=lambda r: severity.get(r["flag"], 9))
    return rows


def _clusters(session: Session, course_id: str) -> list[dict]:
    clusters = session.scalars(
        select(MisconceptionCluster)
        .where(MisconceptionCluster.course_id == course_id)
        .order_by(MisconceptionCluster.size.desc())
    ).all()
    return [
        {
            "id": c.id,
            "label": c.label or None,
            "auto_signature": c.auto_signature,
            "size": c.size,
            "concepts": c.concept_keys,
            "representative_run_id": c.representative_run_id,
            "assignment_id": c.assignment_id,
            "named": bool(c.label),
        }
        for c in clusters
    ]


def _distribution(session: Session, course_id: str) -> dict:
    rows = session.execute(
        select(Verdict)
        .join(EvaluationRun, EvaluationRun.id == Verdict.run_id)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .join(Assignment, Assignment.id == Submission.assignment_id)
        .where(Assignment.course_id == course_id, EvaluationRun.visible_only.is_(False))
    ).scalars().all()
    return item_analysis.cohort_distribution([v.total_fraction for v in rows])


@router.get("/courses/{course_id}/queue")
def queue(course_id: str, session: Session = Depends(get_session)) -> list[dict]:
    return grading_service.review_queue(session, course_id)


@router.get("/runs/{run_id}/review")
def review(run_id: str, session: Session = Depends(get_session)) -> dict:
    """Side-by-side: student code, evidence trail, proposed score, reference."""
    try:
        detail = grading_service.run_detail(session, run_id)
    except LookupError as exc:
        raise HTTPException(404, "run not found") from exc

    from ..models import AssignmentVersion

    run = session.get(EvaluationRun, run_id)
    version = session.get(AssignmentVersion, run.version_id)
    detail["reference_solution"] = version.reference_solution if version else ""
    detail["spec_text"] = version.spec_text if version else ""

    similarity = session.scalars(
        select(SimilarityPair).where(
            (SimilarityPair.run_id_a == run_id) | (SimilarityPair.run_id_b == run_id)
        ).order_by(SimilarityPair.combined.desc())
    ).all()
    detail["similarity"] = [
        {
            "id": p.id,
            "combined": round(p.combined, 4),
            "token_similarity": round(p.token_similarity, 4),
            "structural_similarity": round(p.structural_similarity, 4),
            "other_student_id": p.student_id_b if p.run_id_a == run_id else p.student_id_a,
            "aligned_regions": p.aligned_regions[:5],
            "corpus": p.corpus,
            "reviewed": p.reviewed,
        }
        for p in similarity[:5]
    ]
    detail["similarity_disclaimer"] = (
        "Similarity evidence only. The platform never determines misconduct; aligned regions are "
        "shown so that you can."
    )
    return detail


@router.post("/runs/{run_id}/override")
def override(run_id: str, payload: OverrideRequest, session: Session = Depends(get_session)) -> dict:
    """Override with a mandatory reason - this is the training signal."""
    try:
        result = grading_service.override_item(
            session, run_id, payload.item_key, payload.faculty_id, payload.score_fraction, payload.reason
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.commit()

    run = session.get(EvaluationRun, run_id)
    course_id = _course_for_run(session, run)
    analytics_service.refresh_course_analytics(session, course_id)
    training = metrics_service.train_confidence_model(session)
    session.commit()
    return {**result, "confidence_model": training}


@router.post("/runs/{run_id}/confirm")
def confirm(run_id: str, payload: ConfirmRequest, session: Session = Depends(get_session)) -> dict:
    try:
        result = grading_service.confirm_run(session, run_id, payload.faculty_id, payload.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.commit()
    run = session.get(EvaluationRun, run_id)
    analytics_service.refresh_course_analytics(session, _course_for_run(session, run))
    session.commit()
    return result


def _course_for_run(session: Session, run: EvaluationRun) -> str:
    attempt = session.get(SubmissionAttempt, run.attempt_id)
    assignment = session.get(Assignment, attempt.submission.assignment_id)
    return assignment.course_id


@router.post("/clusters/{cluster_id}/label")
def label_cluster(cluster_id: str, payload: LabelRequest, session: Session = Depends(get_session)) -> dict:
    """Name a misconception once; the label persists across semesters.

    This is how a reusable per-course misconception library accumulates instead
    of being rediscovered every year.
    """
    cluster = session.get(MisconceptionCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "cluster not found")
    cluster.label = payload.label
    cluster.named_by = payload.faculty_id
    session.commit()
    return {"id": cluster.id, "label": cluster.label}


@router.get("/clusters/{cluster_id}/briefing")
def cluster_briefing(cluster_id: str, session: Session = Depends(get_session)) -> dict:
    """A misconception briefing: the shared error, with representative code.

    "Nineteen students failed the same edge case and their ASTs share a common
    shape" is worth more to a lecturer than nineteen individual grades.
    """
    cluster = session.get(MisconceptionCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "cluster not found")

    representatives = []
    for run_id in (cluster.member_run_ids or [])[:3]:
        run = session.get(EvaluationRun, run_id)
        if run is None:
            continue
        attempt = session.get(SubmissionAttempt, run.attempt_id)
        failed = [
            {"test_key": t.test_key, "outcome": t.outcome.value, "reason": t.diff or t.stderr_excerpt}
            for t in run.test_results
            if t.outcome.value != "pass"
        ][:4]
        representatives.append(
            {
                "run_id": run_id,
                "is_medoid": run_id == cluster.representative_run_id,
                "files": attempt.files if attempt else {},
                "failed_tests": failed,
            }
        )

    return {
        "id": cluster.id,
        "label": cluster.label or None,
        "auto_signature": cluster.auto_signature,
        "size": cluster.size,
        "concepts": cluster.concept_keys,
        "representatives": representatives,
        "suggested_lecture_note": (
            f"{cluster.size} students share this failure shape"
            + (f" on {', '.join(cluster.concept_keys[:2])}" if cluster.concept_keys else "")
            + ". Address the actual error in the next session rather than a guess at it."
        ),
    }


@router.get("/courses/{course_id}/appeals")
def appeals(course_id: str, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Appeal).order_by(Appeal.created_at.desc())).all()
    names = {u.id: u.name for u in session.scalars(select(User))}
    out = []
    for appeal in rows:
        run = session.get(EvaluationRun, appeal.run_id)
        if run is None:
            continue
        if _course_for_run(session, run) != course_id:
            continue
        out.append(
            {
                "id": appeal.id,
                "run_id": appeal.run_id,
                "student": names.get(appeal.student_id, appeal.student_id),
                "item_key": appeal.item_key,
                "reason": appeal.reason,
                "state": appeal.state.value,
                "created_at": appeal.created_at.isoformat() if appeal.created_at else None,
            }
        )
    return out


@router.post("/appeals/{appeal_id}/resolve")
def resolve_appeal(
    appeal_id: str, payload: AppealResolution, session: Session = Depends(get_session)
) -> dict:
    try:
        appeal = grading_service.resolve_appeal(
            session, appeal_id, payload.faculty_id, payload.upheld, payload.note
        )
    except LookupError as exc:
        raise HTTPException(404, "appeal not found") from exc
    session.commit()
    return {"id": appeal.id, "state": appeal.state.value}


@router.post("/assignments/{assignment_id}/regrade")
def regrade(assignment_id: str, session: Session = Depends(get_session)) -> dict:
    """Bulk regrade under the current approved rubric version."""
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "assignment not found")
    runs = grading_service.regrade(session, assignment_id)
    session.commit()
    analytics_service.refresh_course_analytics(session, assignment.course_id)
    session.commit()
    return {"regraded": len(runs), "version": assignment.active_version.version}
