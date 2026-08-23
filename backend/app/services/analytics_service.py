"""Layer 2/3 service: turns the observation stream into mastery, insight, and
named next actions, and writes the derived state back.

Everything here reads ``ConceptObservation`` -- the record written only for
released or faculty-confirmed evidence -- and nothing here reads a grade
directly. That separation is what keeps mastery honest: escalated-and-unreviewed
results never reach it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics import attainment as attainment_mod
from ..analytics import bkt, clustering, item_analysis, remediation, risk
from ..config import settings
from ..models import (
    Assignment,
    Concept,
    ConceptObservation,
    Course,
    CourseOutcome,
    Enrollment,
    EvaluationRun,
    MisconceptionCluster,
    Role,
    RubricItemScore,
    RubricItemStats,
    StudentConceptMastery,
    StudentRiskState,
    Submission,
    SubmissionAttempt,
    TestOutcome,
    TestResult,
    User,
    Verdict,
    VerdictState,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load_concept_graph(session: Session, course_id: str) -> dict[str, remediation.ConceptNode]:
    concepts = session.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    return {
        c.concept_key: remediation.ConceptNode(
            concept_key=c.concept_key,
            name=c.name,
            prerequisites=list(c.prerequisites or []),
            resources=list(c.resources or []),
            course_outcomes=list(c.course_outcomes or []),
            typical_misconceptions=list(c.typical_misconceptions or []),
            syllabus_week=c.syllabus_week,
        )
        for c in concepts
    }


def prerequisite_map(graph: dict[str, remediation.ConceptNode]) -> dict[str, list[str]]:
    return {key: node.prerequisites for key, node in graph.items()}


def load_observations(session: Session, course_id: str) -> dict[str, list[bkt.Observation]]:
    rows = session.scalars(
        select(ConceptObservation)
        .where(ConceptObservation.course_id == course_id)
        .order_by(ConceptObservation.observed_at)
    ).all()
    by_student: dict[str, list[bkt.Observation]] = defaultdict(list)
    for row in rows:
        by_student[row.student_id].append(
            bkt.Observation(
                concept_key=row.concept_key,
                score_fraction=row.score_fraction,
                confidence=row.confidence,
                evidence_source=row.evidence_source,
                observed_at=row.observed_at,
                assignment_id=row.assignment_id,
                run_id=row.run_id,
            )
        )
    return dict(by_student)


def enrolled_students(session: Session, course_id: str) -> list[User]:
    return list(
        session.scalars(
            select(User)
            .join(Enrollment, Enrollment.user_id == User.id)
            .where(Enrollment.course_id == course_id, Enrollment.role == Role.STUDENT)
            .order_by(User.name)
        )
    )


# --------------------------------------------------------------------------
# Knowledge tracing
# --------------------------------------------------------------------------
def recompute_mastery(session: Session, course_id: str) -> dict[str, dict[str, bkt.MasteryState]]:
    """Fit BKT parameters per concept on the cohort, then trace every student.

    Fitting on the cohort and tracing per student is what makes the estimates
    comparable: a mastery of 0.7 means the same thing for two students because
    it came from the same four parameters.
    """
    graph = load_concept_graph(session, course_id)
    prerequisites = prerequisite_map(graph)
    by_student = load_observations(session, course_id)

    concept_keys = sorted({o.concept_key for obs in by_student.values() for o in obs})
    params_by_concept = {
        key: bkt.fit_parameters(by_student, key) for key in concept_keys
    }

    all_states: dict[str, dict[str, bkt.MasteryState]] = {}
    for student_id, observations in by_student.items():
        states = bkt.trace_student(observations, params_by_concept, prerequisites)
        all_states[student_id] = states
        _persist_mastery(session, course_id, student_id, states)

    session.flush()
    return all_states


def _persist_mastery(
    session: Session,
    course_id: str,
    student_id: str,
    states: dict[str, bkt.MasteryState],
) -> None:
    existing = {
        row.concept_key: row
        for row in session.scalars(
            select(StudentConceptMastery).where(
                StudentConceptMastery.course_id == course_id,
                StudentConceptMastery.student_id == student_id,
            )
        )
    }
    for concept_key, state in states.items():
        row = existing.get(concept_key)
        if row is None:
            row = StudentConceptMastery(
                student_id=student_id, concept_key=concept_key, course_id=course_id
            )
            session.add(row)
        row.mastery_estimate = round(state.mastery, 4)
        row.uncertainty = round(state.uncertainty, 4)
        row.evidence_count = state.evidence_count
        row.trajectory = state.trajectory[-40:]
        row.source = state.source
        row.last_updated = _now()


def mastery_snapshots(
    session: Session, course_id: str
) -> dict[str, dict[str, remediation.MasterySnapshot]]:
    rows = session.scalars(
        select(StudentConceptMastery).where(StudentConceptMastery.course_id == course_id)
    ).all()
    evidence = _evidence_refs(session, course_id)
    result: dict[str, dict[str, remediation.MasterySnapshot]] = defaultdict(dict)
    for row in rows:
        result[row.student_id][row.concept_key] = remediation.MasterySnapshot(
            concept_key=row.concept_key,
            mastery=row.mastery_estimate,
            uncertainty=row.uncertainty,
            evidence_count=row.evidence_count,
            evidence_refs=evidence.get((row.student_id, row.concept_key), []),
        )
    return dict(result)


def _evidence_refs(session: Session, course_id: str) -> dict[tuple[str, str], list[dict]]:
    rows = session.scalars(
        select(ConceptObservation)
        .where(ConceptObservation.course_id == course_id)
        .order_by(ConceptObservation.observed_at.desc())
    ).all()
    refs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = (row.student_id, row.concept_key)
        if len(refs[key]) >= 8:
            continue
        refs[key].append(
            {
                "run_id": row.run_id,
                "assignment_id": row.assignment_id,
                "item_key": row.item_key,
                "score": round(row.score_fraction, 3),
                "confidence": round(row.confidence, 3),
                "source": row.evidence_source,
                "at": row.observed_at.isoformat() if row.observed_at else None,
            }
        )
    return dict(refs)


def flat_mastery(
    snapshots: dict[str, dict[str, remediation.MasterySnapshot]]
) -> dict[str, dict[str, float]]:
    return {
        student: {key: snap.mastery for key, snap in concepts.items()}
        for student, concepts in snapshots.items()
    }


# --------------------------------------------------------------------------
# Item analysis
# --------------------------------------------------------------------------
def recompute_item_stats(session: Session, course_id: str) -> list[item_analysis.ItemStats]:
    snapshots = mastery_snapshots(session, course_id)
    mastery = flat_mastery(snapshots)
    assignments = session.scalars(select(Assignment).where(Assignment.course_id == course_id)).all()

    all_stats: list[item_analysis.ItemStats] = []
    for assignment in assignments:
        responses: list[item_analysis.ItemResponse] = []
        for run, attempt, submission in _released_runs(session, assignment.id):
            verdict = run.verdict
            if verdict is None:
                continue
            for score in run.item_scores:
                responses.append(
                    item_analysis.ItemResponse(
                        student_id=submission.student_id,
                        item_key=score.item_key,
                        score_fraction=score.score_fraction,
                        total_fraction=verdict.total_fraction,
                        concept_keys=list(score.concept_ids or []),
                    )
                )
        if not responses:
            continue
        stats = item_analysis.analyse_cohort(responses, mastery)
        cohort_id = f"{course_id}:{assignment.code}"
        _persist_item_stats(session, assignment.id, cohort_id, stats)
        all_stats.extend(stats)

    session.flush()
    return all_stats


def _persist_item_stats(
    session: Session,
    assignment_id: str,
    cohort_id: str,
    stats: list[item_analysis.ItemStats],
) -> None:
    existing = {
        row.item_key: row
        for row in session.scalars(
            select(RubricItemStats).where(RubricItemStats.cohort_id == cohort_id)
        )
    }
    for stat in stats:
        row = existing.get(stat.item_key)
        if row is None:
            row = RubricItemStats(item_key=stat.item_key, cohort_id=cohort_id, assignment_id=assignment_id)
            session.add(row)
        row.n = stat.n
        row.difficulty = stat.difficulty
        row.discrimination = stat.discrimination
        row.concept_alignment = stat.concept_alignment
        row.flag = stat.flag
        row.computed_at = _now()


def _released_runs(session: Session, assignment_id: str):
    rows = session.execute(
        select(EvaluationRun, SubmissionAttempt, Submission)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .join(Verdict, Verdict.run_id == EvaluationRun.id)
        .where(
            Submission.assignment_id == assignment_id,
            EvaluationRun.visible_only.is_(False),
            Verdict.state.in_([VerdictState.RELEASED, VerdictState.OVERRIDDEN]),
        )
    ).all()
    return [(run, attempt, submission) for run, attempt, submission in rows]


# --------------------------------------------------------------------------
# Misconception clustering
# --------------------------------------------------------------------------
def recompute_clusters(session: Session, course_id: str) -> list[MisconceptionCluster]:
    """Cluster failed submissions per assignment and persist, preserving any
    instructor-supplied label from a previous run.

    The label persisting across recomputation is the whole point: it is how a
    per-course misconception library accumulates instead of being rebuilt from
    scratch every semester.
    """
    assignments = session.scalars(select(Assignment).where(Assignment.course_id == course_id)).all()
    produced: list[MisconceptionCluster] = []

    for assignment in assignments:
        signatures = _failure_signatures(session, assignment.id)
        if len(signatures) < 3:
            continue
        clusters, noise = clustering.cluster_failures(signatures)

        previous = {
            c.auto_signature: c
            for c in session.scalars(
                select(MisconceptionCluster).where(
                    MisconceptionCluster.assignment_id == assignment.id
                )
            )
        }
        # Rebuild the assignment's clusters, carrying labels forward by signature.
        for row in previous.values():
            session.delete(row)
        session.flush()

        for cluster in clusters:
            carried = previous.get(cluster.signature)
            row = MisconceptionCluster(
                course_id=course_id,
                assignment_id=assignment.id,
                label=carried.label if carried else "",
                auto_signature=cluster.signature,
                concept_keys=cluster.concept_keys,
                size=len(cluster.members),
                member_run_ids=[m.run_id for m in cluster.members],
                representative_run_id=cluster.representative.run_id,
                named_by=carried.named_by if carried else None,
                computed_at=_now(),
            )
            session.add(row)
            produced.append(row)

    session.flush()
    return produced


def _failure_signatures(session: Session, assignment_id: str) -> list[clustering.FailureSignature]:
    signatures: list[clustering.FailureSignature] = []
    rows = session.execute(
        select(EvaluationRun, SubmissionAttempt, Submission)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .where(Submission.assignment_id == assignment_id, EvaluationRun.visible_only.is_(False))
    ).all()

    for run, attempt, submission in rows:
        failed = [
            t for t in run.test_results
            if t.outcome in (TestOutcome.FAIL, TestOutcome.CRASH, TestOutcome.TIMEOUT, TestOutcome.OOM)
        ]
        if not failed:
            continue
        error_types: list[str] = []
        for result in failed:
            if result.outcome != TestOutcome.FAIL:
                error_types.append(result.outcome.value)
            elif result.stderr_excerpt:
                error_types.append(result.stderr_excerpt.split(":")[0][:40])
            elif result.actual and "Error" in result.actual:
                error_types.append(result.actual.split(":")[0][:40])
        weak_concepts = [
            c
            for score in run.item_scores
            if score.score_fraction < 0.5
            for c in (score.concept_ids or [])
        ]
        graph = run.code_graph or {}
        functions = graph.get("functions", {}) or {}
        ast_shape = {
            "loop_depth": float(max((f.get("loop_depth", 0) for f in functions.values()), default=0)),
            "branches": float(sum(f.get("branch_count", 0) for f in functions.values())),
            "recursive": float(any(f.get("is_recursive") for f in functions.values())),
            "functions": float(len(functions)),
            "syntax_error": float(bool(graph.get("syntax_errors"))),
        }
        excerpt = ""
        if attempt.files:
            first = sorted(attempt.files)[0]
            excerpt = attempt.files[first][:600]
        signatures.append(
            clustering.FailureSignature(
                run_id=run.id,
                student_id=submission.student_id,
                failed_tests=sorted({t.test_key for t in failed}),
                error_types=sorted(set(error_types)),
                concept_keys=sorted(set(weak_concepts)),
                ast_shape=ast_shape,
                excerpt=excerpt,
            )
        )
    return signatures


# --------------------------------------------------------------------------
# Risk
# --------------------------------------------------------------------------
def recompute_risk(session: Session, course_id: str) -> list[risk.RiskAssessment]:
    graph = load_concept_graph(session, course_id)
    prerequisites = prerequisite_map(graph)
    snapshots = mastery_snapshots(session, course_id)
    students = enrolled_students(session, course_id)
    assignments = session.scalars(select(Assignment).where(Assignment.course_id == course_id)).all()

    trajectories_by_student = _trajectories(session, course_id)
    assessments: list[risk.RiskAssessment] = []

    for student in students:
        mastery = {k: s.mastery for k, s in snapshots.get(student.id, {}).items()}
        behaviour = _behaviour_features(session, student.id, assignments)
        assessment = risk.assess_student(
            student.id,
            mastery,
            trajectories_by_student.get(student.id, {}),
            prerequisites,
            behaviour,
        )
        assessments.append(assessment)
        _persist_risk(session, course_id, assessment)

    session.flush()
    return assessments


def _trajectories(session: Session, course_id: str) -> dict[str, dict[str, list[dict]]]:
    rows = session.scalars(
        select(StudentConceptMastery).where(StudentConceptMastery.course_id == course_id)
    ).all()
    out: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    for row in rows:
        out[row.student_id][row.concept_key] = list(row.trajectory or [])
    return dict(out)


def _behaviour_features(
    session: Session, student_id: str, assignments: list[Assignment]
) -> risk.BehaviourFeatures:
    submissions = session.scalars(
        select(Submission).where(Submission.student_id == student_id)
    ).all()
    by_assignment = {s.assignment_id: s for s in submissions}

    late_starts = 0
    considered = 0
    abandoned = 0
    total_attempts = 0
    completed_attempts = 0

    for assignment in assignments:
        submission = by_assignment.get(assignment.id)
        if submission is None:
            continue
        considered += 1
        attempts = submission.attempts
        total_attempts += len(attempts)
        completed_attempts += sum(1 for a in attempts if a.runs)
        abandoned += sum(1 for a in attempts if not a.runs)
        if attempts and assignment.due_at and assignment.opens_at:
            window = (assignment.due_at - assignment.opens_at).total_seconds() or 1
            first = attempts[0].submitted_at
            if first and first.tzinfo is None:
                opens = assignment.opens_at
                elapsed = (first - opens).total_seconds()
                if elapsed / window > 0.5:
                    late_starts += 1

    missed = len(assignments) - considered
    attempt_spike = 0.0
    if considered:
        mean_attempts = total_attempts / considered
        attempt_spike = min(1.0, max(0.0, (mean_attempts - 2.0) / 6.0))

    engagement_decay = 0.0
    if len(assignments) >= 3 and considered:
        halfway = len(assignments) // 2
        early = sum(1 for a in assignments[:halfway] if a.id in by_assignment)
        late = sum(1 for a in assignments[halfway:] if a.id in by_assignment)
        early_rate = early / max(1, halfway)
        late_rate = late / max(1, len(assignments) - halfway)
        engagement_decay = max(0.0, early_rate - late_rate)

    return risk.BehaviourFeatures(
        late_start_rate=late_starts / considered if considered else 0.0,
        attempt_spike=attempt_spike,
        abandonment_rate=abandoned / total_attempts if total_attempts else 0.0,
        engagement_decay=engagement_decay,
        missed_assignments=max(0, missed),
        days_since_last_submission=0,
    )


def _persist_risk(session: Session, course_id: str, assessment: risk.RiskAssessment) -> None:
    row = session.scalar(
        select(StudentRiskState).where(
            StudentRiskState.course_id == course_id,
            StudentRiskState.student_id == assessment.student_id,
        )
    )
    if row is None:
        row = StudentRiskState(student_id=assessment.student_id, course_id=course_id)
        session.add(row)
    row.risk_score = assessment.risk_score
    row.contributing_factors = [f.as_dict() for f in assessment.contributing_factors]
    row.routed_to = assessment.routed_to
    row.last_updated = _now()
    if assessment.flagged and row.first_flagged_at is None:
        row.first_flagged_at = _now()
    if not assessment.flagged:
        row.first_flagged_at = None


def run_bias_audit(session: Session, course_id: str, assessments: list[risk.RiskAssessment]) -> dict:
    students = enrolled_students(session, course_id)
    protected = {s.id: dict(s.protected_attributes or {}) for s in students}
    return risk.bias_audit(assessments, protected).as_dict()


# --------------------------------------------------------------------------
# Attainment
# --------------------------------------------------------------------------
def compute_attainment(session: Session, course_id: str) -> dict:
    course = session.get(Course, course_id)
    outcome_rows = session.scalars(
        select(CourseOutcome).where(CourseOutcome.course_id == course_id).order_by(CourseOutcome.code)
    ).all()
    outcomes = [
        attainment_mod.OutcomeDefinition(
            code=row.code,
            text=row.text,
            programme_outcomes=list(row.programme_outcomes or []),
            po_weights=dict(row.po_weights or {}),
        )
        for row in outcome_rows
    ]
    concepts = session.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    concept_to_outcomes = {c.concept_key: list(c.course_outcomes or []) for c in concepts}

    snapshots = mastery_snapshots(session, course_id)
    mastery = flat_mastery(snapshots)
    evidence_counts = {
        key: sum(1 for s in snapshots.values() if key in s) for key in concept_to_outcomes
    }

    co = attainment_mod.compute_co_attainment(outcomes, concept_to_outcomes, mastery, evidence_counts)
    po = attainment_mod.compute_po_attainment(co, outcomes)
    return attainment_mod.attainment_report(course.code, course.term, co, po)


# --------------------------------------------------------------------------
# Full refresh
# --------------------------------------------------------------------------
def refresh_course_analytics(session: Session, course_id: str) -> dict:
    """Recompute L2 and L3 for a course. Idempotent, and cheap enough to run
    after every batch of releases."""
    recompute_mastery(session, course_id)
    stats = recompute_item_stats(session, course_id)
    clusters = recompute_clusters(session, course_id)
    assessments = recompute_risk(session, course_id)
    audit = run_bias_audit(session, course_id, assessments)
    return {
        "mastery_rows": session.scalar(
            select(func.count(StudentConceptMastery.id)).where(
                StudentConceptMastery.course_id == course_id
            )
        ),
        "item_stats": len(stats),
        "clusters": len(clusters),
        "risk_assessments": len(assessments),
        "flagged": sum(1 for a in assessments if a.flagged),
        "bias_audit_passed": audit["passed"],
    }
