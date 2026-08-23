"""Submission, evaluation, review, and override.

The content-hash cache and the per-student rate limit both live here rather
than in the API layer, because they are correctness properties of grading (the
same bundle must produce the same result) and not merely transport concerns.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..engine.b0_ingest import compute_content_hash, rate_limit_exceeded
from ..engine.pipeline import EvaluationPipeline, build_cohort_corpus
from ..models import (
    Appeal,
    AppealState,
    Assignment,
    AssignmentVersion,
    AuditEvent,
    ConceptObservation,
    EscalationReason,
    EvaluationRun,
    FacultyOverride,
    RubricItem,
    RubricItemScore,
    StageName,
    Submission,
    SubmissionAttempt,
    Verdict,
    VerdictState,
)


class SubmissionRejected(Exception):
    """Raised for rate limits and closed assignments. Always explains itself."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def audit(session: Session, kind: str, actor_id: str | None, subject_id: str | None, **detail) -> None:
    session.add(AuditEvent(kind=kind, actor_id=actor_id, subject_id=subject_id, detail=detail))


# --------------------------------------------------------------------------
# Submission
# --------------------------------------------------------------------------
def submit(
    session: Session,
    assignment_id: str,
    student_id: str,
    files: dict[str, str],
    report_text: str = "",
    visible_only: bool | None = None,
) -> tuple[SubmissionAttempt, EvaluationRun, bool]:
    """Accept an attempt and evaluate it. Returns ``(attempt, run, from_cache)``.

    ``visible_only`` defaults to "before the deadline", which is what makes
    pre-deadline feedback safe: the hidden test set is never executed for a run
    a student can see.
    """
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise SubmissionRejected(f"assignment {assignment_id} does not exist")
    version = assignment.active_version
    if version is None or version.approved_at is None:
        raise SubmissionRejected(
            "This assignment has no approved version. Nothing grades until a human has "
            "approved the rubric and tests."
        )

    submission = session.scalar(
        select(Submission).where(
            Submission.assignment_id == assignment_id, Submission.student_id == student_id
        )
    )
    if submission is None:
        submission = Submission(assignment_id=assignment_id, student_id=student_id)
        session.add(submission)
        session.flush()

    recent = [a.submitted_at for a in submission.attempts]
    if rate_limit_exceeded(recent, _now().replace(tzinfo=None)):
        raise SubmissionRejected(
            f"Rate limit: at most {settings.ingest.submissions_per_student_per_hour} submissions per hour. "
            "This protects the queue at the deadline; your existing attempts are unaffected."
        )
    if len(submission.attempts) >= assignment.max_attempts:
        raise SubmissionRejected(f"Attempt limit of {assignment.max_attempts} reached for this assignment.")

    content_hash = compute_content_hash(files, report_text)

    # Idempotency: identical content returns the cached result rather than
    # re-running the cascade. This is also the deadline submit-spam defence.
    cached = session.scalar(
        select(EvaluationRun)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .where(
            Submission.assignment_id == assignment_id,
            Submission.student_id == student_id,
            SubmissionAttempt.content_hash == content_hash,
        )
        .order_by(EvaluationRun.started_at.desc())
    )
    if cached is not None:
        audit(session, "submission.cache_hit", student_id, cached.id, content_hash=content_hash)
        return session.get(SubmissionAttempt, cached.attempt_id), cached, True

    if visible_only is None:
        due = assignment.due_at
        visible_only = bool(due and _now().replace(tzinfo=None) < due)

    late_seconds = 0
    if assignment.due_at:
        delta = (_now().replace(tzinfo=None) - assignment.due_at).total_seconds()
        late_seconds = int(max(0, delta))

    attempt = SubmissionAttempt(
        submission_id=submission.id,
        attempt_no=len(submission.attempts) + 1,
        content_hash=content_hash,
        submitted_at=_now(),
        files=files,
        report_text=report_text,
        late_seconds=late_seconds,
        artifacts_uri=f"evalpro://artifacts/{content_hash[:16]}",
    )
    session.add(attempt)
    session.flush()

    run = evaluate(session, attempt, version, visible_only=visible_only)
    audit(
        session,
        "submission.evaluated",
        student_id,
        run.id,
        assignment=assignment.code,
        visible_only=visible_only,
        content_hash=content_hash,
    )
    return attempt, run, False


def evaluate(
    session: Session,
    attempt: SubmissionAttempt,
    version: AssignmentVersion,
    visible_only: bool = False,
) -> EvaluationRun:
    corpus = build_cohort_corpus(session, attempt.submission.assignment_id)
    pipeline = EvaluationPipeline(session)
    outcome = pipeline.run(attempt, version, visible_only=visible_only, cohort_corpus=corpus)
    session.flush()
    return outcome.run


def regrade(
    session: Session,
    assignment_id: str,
    version: AssignmentVersion | None = None,
) -> list[EvaluationRun]:
    """Bulk regrade under an amended rubric.

    A first-class operation rather than a script, because rubrics are versioned:
    "we found a bad test, regrade everyone against v2" should be a button, and
    the old runs stay on record so a student can see what changed and why.
    """
    assignment = session.get(Assignment, assignment_id)
    version = version or assignment.active_version
    runs: list[EvaluationRun] = []
    for submission in assignment.submissions:
        if not submission.attempts:
            continue
        latest = submission.attempts[-1]
        runs.append(evaluate(session, latest, version, visible_only=False))
    audit(session, "assignment.regraded", None, assignment_id, version=version.version, runs=len(runs))
    return runs


# --------------------------------------------------------------------------
# Review queue
# --------------------------------------------------------------------------
def review_queue(session: Session, course_id: str, limit: int = 50) -> list[dict]:
    """Sorted by the expected value of attention, not by arrival time.

    escalation severity x contested rubric weight x confidence deficit. The
    first item should be where a human minute is worth the most.
    """
    rows = session.execute(
        select(EvaluationRun, Verdict, SubmissionAttempt, Submission, Assignment)
        .join(Verdict, Verdict.run_id == EvaluationRun.id)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .join(Assignment, Assignment.id == Submission.assignment_id)
        .where(
            Assignment.course_id == course_id,
            Verdict.state == VerdictState.ESCALATED,
            EvaluationRun.visible_only.is_(False),
        )
    ).all()

    queue: list[dict] = []
    for run, verdict, attempt, submission, assignment in rows:
        gate_stage = next((s for s in run.stage_results if s.stage == StageName.GATE), None)
        priority = float((gate_stage.evidence or {}).get("review_priority", 0.0)) if gate_stage else 0.0
        contested = [s for s in run.item_scores if s.confidence < 0.75]
        queue.append(
            {
                "run_id": run.id,
                "assignment_id": assignment.id,
                "assignment": assignment.title,
                "student_id": submission.student_id,
                "attempt_no": attempt.attempt_no,
                "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
                "score": round(verdict.total_fraction, 4),
                "confidence": round(verdict.confidence, 4),
                "reasons": verdict.escalation_reasons,
                "integrity_flag": verdict.integrity_flag,
                "contested_items": [
                    {"item_key": s.item_key, "score": round(s.score_fraction, 3), "confidence": round(s.confidence, 3)}
                    for s in sorted(contested, key=lambda s: s.confidence)[:5]
                ],
                "priority": priority,
                "why_this_first": _why_first(verdict, contested),
            }
        )
    queue.sort(key=lambda item: item["priority"], reverse=True)
    return queue[:limit]


def _why_first(verdict: Verdict, contested: list[RubricItemScore]) -> str:
    if EscalationReason.INTEGRITY_FLAG.value in (verdict.escalation_reasons or []):
        return "Integrity signal: highest-consequence decision the system can get wrong, and it needs a person."
    if EscalationReason.REPORT_CONTRADICTION.value in (verdict.escalation_reasons or []):
        return "The report contradicts the code. Either a misunderstanding worth teaching to, or an integrity question."
    if EscalationReason.SIGNAL_CONFLICT.value in (verdict.escalation_reasons or []):
        item = min(contested, key=lambda s: s.confidence, default=None)
        return (
            f"Signals disagree on {item.item_key} (confidence {item.confidence:.2f}). "
            "Disagreement is the strongest predictor that a human is needed."
            if item
            else "Signals disagree on a high-weight item."
        )
    if EscalationReason.GRADE_BOUNDARY.value in (verdict.escalation_reasons or []):
        return f"Score {verdict.total_fraction:.1%} sits on a grade boundary, so a small error changes the grade."
    if EscalationReason.REPAIR_MATERIAL.value in (verdict.escalation_reasons or []):
        return f"A {verdict.syntax_penalty:.0%} syntax penalty is material to the outcome."
    return "Confidence is below the auto-release threshold."


def run_detail(session: Session, run_id: str) -> dict:
    """The full evidence trail for one run. This is what a student sees when
    they drill in, and what faculty see side-by-side with the code."""
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise LookupError(run_id)
    attempt = session.get(SubmissionAttempt, run.attempt_id)
    submission = attempt.submission
    assignment = session.get(Assignment, submission.assignment_id)
    version = session.get(AssignmentVersion, run.version_id)
    verdict = run.verdict
    rubric_text = {
        i.item_key: i.text
        for i in session.scalars(
            select(RubricItem).where(RubricItem.version_id == run.version_id)
        )
    }

    return {
        "run_id": run.id,
        "assignment": {"id": assignment.id, "code": assignment.code, "title": assignment.title},
        "student_id": submission.student_id,
        "attempt_no": attempt.attempt_no,
        "submitted_at": attempt.submitted_at.isoformat() if attempt.submitted_at else None,
        "late_seconds": attempt.late_seconds,
        "reproducibility": {
            "pipeline_version": run.pipeline_version,
            "model_versions": run.model_versions,
            "rubric_version": version.version if version else None,
            "content_hash": attempt.content_hash,
            "note": (
                "This submission regraded against this pinned rubric and these model versions "
                "will produce an identical result."
            ),
        },
        "verdict": {
            "state": verdict.state.value if verdict else "pending",
            "total_fraction": round(verdict.total_fraction, 4) if verdict else 0.0,
            "total_points": round(verdict.total_points, 2) if verdict else 0.0,
            "max_points": round(verdict.max_points, 2) if verdict else 0.0,
            "confidence": round(verdict.confidence, 4) if verdict else 0.0,
            "escalation_reasons": verdict.escalation_reasons if verdict else [],
            "integrity_flag": verdict.integrity_flag if verdict else False,
            "syntax_penalty": round(verdict.syntax_penalty, 4) if verdict else 0.0,
            "override_reason": verdict.override_reason if verdict else None,
        },
        "duration_ms": run.duration_ms,
        "visible_only": run.visible_only,
        "stages": [
            {
                "stage": s.stage.value,
                "status": s.status.value,
                "summary": s.summary,
                "duration_ms": s.duration_ms,
                "evidence": s.evidence,
            }
            for s in run.stage_results
        ],
        "items": [
            {
                "item_key": s.item_key,
                "item_text": rubric_text.get(s.item_key, s.item_key),
                "concepts": s.concept_ids,
                "weight": s.weight,
                "score_fraction": round(s.score_fraction, 4),
                "confidence": round(s.confidence, 4),
                "signal_agreement": round(s.signal_agreement, 4),
                "signals": s.signals,
                "evidence": s.evidence,
                "faculty_score_fraction": s.faculty_score_fraction,
                "faculty_reason": s.faculty_reason,
            }
            for s in run.item_scores
        ],
        "tests": [
            {
                "test_key": t.test_key,
                "category": t.category.value,
                "outcome": t.outcome.value,
                "hidden": t.hidden,
                "on_repaired_source": t.on_repaired_source,
                "expected": t.expected,
                "actual": t.actual,
                "diff": t.diff,
                "cpu_ms": t.cpu_ms,
                "stderr_excerpt": t.stderr_excerpt,
            }
            for t in run.test_results
        ],
        "files": attempt.files,
        "report_text": attempt.report_text,
    }


# --------------------------------------------------------------------------
# Override
# --------------------------------------------------------------------------
def override_item(
    session: Session,
    run_id: str,
    item_key: str,
    faculty_id: str,
    score_fraction: float,
    reason: str,
) -> dict:
    """Override with a mandatory reason.

    Not bureaucracy. This is the training signal: every override is a labelled
    example for the confidence estimator, and the reason text is what later
    tells you *why* the model was wrong rather than merely that it was.
    """
    reason = (reason or "").strip()
    if len(reason) < 8:
        raise ValueError(
            "An override requires a reason of at least a few words. The reason is the training "
            "signal that improves the model, and it is what a student sees on appeal."
        )
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise LookupError(run_id)
    score = next((s for s in run.item_scores if s.item_key == item_key), None)
    if score is None:
        raise LookupError(f"{run_id}/{item_key}")

    session.add(
        FacultyOverride(
            run_id=run_id,
            item_key=item_key,
            faculty_id=faculty_id,
            auto_score_fraction=score.score_fraction,
            auto_confidence=score.confidence,
            faculty_score_fraction=score_fraction,
            reason=reason,
        )
    )
    score.faculty_score_fraction = score_fraction
    score.faculty_reason = reason
    session.flush()

    _recompute_verdict(session, run, faculty_id, reason)
    audit(session, "verdict.overridden", faculty_id, run_id, item_key=item_key, to=score_fraction)
    return {"run_id": run_id, "item_key": item_key, "new_score": score_fraction}


def confirm_run(session: Session, run_id: str, faculty_id: str, note: str = "") -> dict:
    """Faculty confirms an escalated run as-is. Its evidence now feeds L2."""
    run = session.get(EvaluationRun, run_id)
    if run is None:
        raise LookupError(run_id)
    _recompute_verdict(session, run, faculty_id, note or "confirmed without change")
    audit(session, "verdict.confirmed", faculty_id, run_id)
    return {"run_id": run_id, "state": run.verdict.state.value}


def _recompute_verdict(session: Session, run: EvaluationRun, faculty_id: str, reason: str) -> None:
    verdict = run.verdict
    total_weight = sum(s.weight for s in run.item_scores) or 1.0
    effective = sum(
        (s.faculty_score_fraction if s.faculty_score_fraction is not None else s.score_fraction) * s.weight
        for s in run.item_scores
    ) / total_weight
    effective *= 1.0 - (verdict.syntax_penalty if verdict else 0.0)

    verdict.total_fraction = effective
    verdict.total_points = effective * total_weight
    verdict.max_points = total_weight
    verdict.state = VerdictState.OVERRIDDEN
    verdict.reviewed_by = faculty_id
    verdict.reviewed_at = _now()
    verdict.override_reason = reason
    verdict.released_at = verdict.released_at or _now()
    session.flush()

    # Faculty-confirmed evidence is exactly as admissible as auto-released
    # evidence, so the observation stream is refreshed for this run.
    session.query(ConceptObservation).filter(ConceptObservation.run_id == run.id).delete()
    from ..engine.b7_gate import ItemAggregate, Signal

    aggregates = [
        ItemAggregate(
            item_key=s.item_key,
            concept_ids=list(s.concept_ids or []),
            weight=s.weight,
            score_fraction=(
                s.faculty_score_fraction if s.faculty_score_fraction is not None else s.score_fraction
            ),
            confidence=1.0 if s.faculty_score_fraction is not None else s.confidence,
            signal_agreement=s.signal_agreement,
            signals=[Signal("manual", s.faculty_score_fraction or s.score_fraction, 1.0, "faculty review")]
            if s.faculty_score_fraction is not None
            else [Signal(**{k: v for k, v in sig.items() if k in ("source", "score", "reliability", "note")}) for sig in (s.signals or [])],
            evidence=list(s.evidence or []),
        )
        for s in run.item_scores
    ]
    EvaluationPipeline(session).emit_concept_observations(run, aggregates)
    session.flush()


# --------------------------------------------------------------------------
# Appeals
# --------------------------------------------------------------------------
def open_appeal(session: Session, run_id: str, student_id: str, item_key: str, reason: str) -> Appeal:
    """One-click appeal on a specific rubric item, routed with the full trail."""
    appeal = Appeal(run_id=run_id, student_id=student_id, item_key=item_key, reason=reason)
    session.add(appeal)
    run = session.get(EvaluationRun, run_id)
    if run and run.verdict and run.verdict.state == VerdictState.RELEASED:
        run.verdict.state = VerdictState.ESCALATED
        run.verdict.escalation_reasons = sorted(
            set((run.verdict.escalation_reasons or []) + [EscalationReason.APPEAL.value])
        )
    session.flush()
    audit(session, "appeal.opened", student_id, run_id, item_key=item_key)
    return appeal


def resolve_appeal(
    session: Session, appeal_id: str, faculty_id: str, upheld: bool, note: str
) -> Appeal:
    appeal = session.get(Appeal, appeal_id)
    if appeal is None:
        raise LookupError(appeal_id)
    appeal.state = AppealState.UPHELD if upheld else AppealState.REJECTED
    appeal.resolved_at = _now()
    appeal.resolution_note = note
    session.flush()
    audit(session, "appeal.resolved", faculty_id, appeal.run_id, upheld=upheld)
    return appeal


# --------------------------------------------------------------------------
# Gradebook writeback (Layer 0)
# --------------------------------------------------------------------------
def gradebook_rows(session: Session, course_id: str) -> list[dict]:
    """The payload an LTI Assignment and Grade Services writeback would post.

    Faculty maintaining two gradebooks is the difference between a tool that is
    adopted and one that is admired and unused, so this is modelled as a first-
    class export rather than a report.
    """
    rows = session.execute(
        select(Verdict, EvaluationRun, Submission, Assignment)
        .join(EvaluationRun, EvaluationRun.id == Verdict.run_id)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .join(Assignment, Assignment.id == Submission.assignment_id)
        .where(
            Assignment.course_id == course_id,
            Verdict.state.in_([VerdictState.RELEASED, VerdictState.OVERRIDDEN]),
            EvaluationRun.visible_only.is_(False),
        )
    ).all()

    latest: dict[tuple[str, str], dict] = {}
    for verdict, run, submission, assignment in rows:
        key = (submission.student_id, assignment.id)
        existing = latest.get(key)
        if existing and existing["_started"] >= run.started_at:
            continue
        latest[key] = {
            "student_id": submission.student_id,
            "assignment_id": assignment.id,
            "assignment_code": assignment.code,
            "score": round(verdict.total_points, 2),
            "max_score": round(verdict.max_points, 2),
            "state": verdict.state.value,
            "lti_resource_link_id": assignment.lti_resource_link_id,
            "synced_at": verdict.gradebook_synced_at.isoformat() if verdict.gradebook_synced_at else None,
            "_started": run.started_at,
        }
    output = []
    for row in latest.values():
        row.pop("_started", None)
        output.append(row)
    output.sort(key=lambda r: (r["assignment_code"], r["student_id"]))
    return output
