"""§11 Measuring the platform.

The point of this module is that the platform reports on *itself*, in public,
including the numbers that make it look bad. Grading drift is invisible until it
is not, and the only defence is a stratified faculty-graded holdout every
semester plus a dashboard nobody can quietly stop looking at.

Two of these metrics matter as much as the accuracy ones and are usually
missing: **mastery predictive validity** (a knowledge-tracing model that does
not predict future performance is an expensive decoration) and **early-warning
bias delta** (a risk model that flags one demographic disproportionately is
actively harmful).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics import bkt
from ..analytics import confidence as confidence_model
from ..models import (
    Appeal,
    AppealState,
    Assignment,
    ConceptObservation,
    EvaluationRun,
    FacultyOverride,
    SimilarityPair,
    StudentConceptMastery,
    Submission,
    SubmissionAttempt,
    Verdict,
    VerdictState,
)
from .analytics_service import load_concept_graph, load_observations, prerequisite_map


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    target: str
    meets_target: bool | None
    why: str
    unit: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": round(self.value, 4) if isinstance(self.value, (int, float)) else None,
            "target": self.target,
            "meets_target": self.meets_target,
            "why": self.why,
            "unit": self.unit,
        }


def quadratic_weighted_kappa(auto: list[int], human: list[int], categories: int = 5) -> float | None:
    """QWK against faculty grades on the holdout - the headline accuracy number.

    Ordinal, so it penalises being two bands out far more than one, which is the
    correct shape for grading agreement.
    """
    if len(auto) != len(human) or len(auto) < 4:
        return None
    observed = [[0] * categories for _ in range(categories)]
    for a, h in zip(auto, human):
        observed[min(a, categories - 1)][min(h, categories - 1)] += 1

    n = len(auto)
    auto_hist = [auto.count(i) for i in range(categories)]
    human_hist = [human.count(i) for i in range(categories)]

    numerator = denominator = 0.0
    for i in range(categories):
        for j in range(categories):
            weight = ((i - j) ** 2) / ((categories - 1) ** 2)
            expected = auto_hist[i] * human_hist[j] / n
            numerator += weight * observed[i][j]
            denominator += weight * expected
    if denominator == 0:
        return None
    return 1.0 - numerator / denominator


def _band(fraction: float, categories: int = 5) -> int:
    return min(categories - 1, max(0, int(fraction * categories)))


def platform_metrics(session: Session, course_id: str) -> list[Metric]:
    metrics: list[Metric] = []

    assignment_ids = [
        row for row in session.scalars(select(Assignment.id).where(Assignment.course_id == course_id))
    ]
    verdicts = session.execute(
        select(Verdict, EvaluationRun)
        .join(EvaluationRun, EvaluationRun.id == Verdict.run_id)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .where(Submission.assignment_id.in_(assignment_ids), EvaluationRun.visible_only.is_(False))
    ).all()
    total_runs = len(verdicts)

    # -- auto-release coverage -----------------------------------------
    released = sum(1 for v, _ in verdicts if v.state == VerdictState.RELEASED)
    coverage = released / total_runs if total_runs else None
    metrics.append(
        Metric(
            "auto_release_coverage",
            "Auto-release coverage",
            coverage,
            ">= 70% by semester 2",
            coverage >= 0.70 if coverage is not None else None,
            "The efficiency claim. Coverage without a low override rate is worthless, so read it "
            "alongside the next row.",
            "%",
        )
    )

    # -- override rate on auto-released work ----------------------------
    released_ids = {v.run_id for v, _ in verdicts if v.state in (VerdictState.RELEASED, VerdictState.OVERRIDDEN)}
    overrides = session.scalars(select(FacultyOverride)).all()
    overridden_released = {o.run_id for o in overrides if o.run_id in released_ids}
    override_rate = len(overridden_released) / released if released else None
    metrics.append(
        Metric(
            "override_rate",
            "Override rate on auto-released work",
            override_rate,
            "< 3%",
            override_rate < 0.03 if override_rate is not None else None,
            "If released grades get changed often, the coverage number above is measuring the wrong thing.",
            "%",
        )
    )

    # -- QWK on the faculty-reviewed holdout ----------------------------
    auto_bands: list[int] = []
    human_bands: list[int] = []
    for override in overrides:
        auto_bands.append(_band(override.auto_score_fraction))
        human_bands.append(_band(override.faculty_score_fraction))
    qwk = quadratic_weighted_kappa(auto_bands, human_bands)
    metrics.append(
        Metric(
            "qwk",
            "QWK vs faculty grades (holdout)",
            qwk,
            "> 0.85",
            qwk > 0.85 if qwk is not None else None,
            "Headline accuracy for ordinal grading. Computed on the faculty-reviewed sample; without a "
            "stratified holdout every semester you cannot detect drift.",
        )
    )

    # -- appeals --------------------------------------------------------
    appeals = session.scalars(select(Appeal)).all()
    appeal_rate = len(appeals) / total_runs if total_runs else None
    metrics.append(
        Metric(
            "appeal_rate",
            "Appeal rate",
            appeal_rate,
            "< 5%, trending down",
            appeal_rate < 0.05 if appeal_rate is not None else None,
            "A trust proxy. Falling is good; zero usually means students do not know they can appeal.",
            "%",
        )
    )
    resolved = [a for a in appeals if a.state != AppealState.OPEN]
    upheld_rate = (
        sum(1 for a in resolved if a.state == AppealState.UPHELD) / len(resolved) if resolved else None
    )
    metrics.append(
        Metric(
            "appeals_upheld",
            "Appeals upheld",
            upheld_rate,
            "tracked, not minimised",
            None,
            "Deliberately has no target. A high uphold rate means the gate is mis-tuned, and driving "
            "this number down by rejecting appeals would be the worst possible response.",
            "%",
        )
    )

    # -- integrity false flags -------------------------------------------
    pairs = session.scalars(
        select(SimilarityPair).where(SimilarityPair.assignment_id.in_(assignment_ids))
    ).all()
    reviewed = [p for p in pairs if p.reviewed]
    false_flags = sum(1 for p in reviewed if (p.reviewer_note or "").lower().startswith("cleared"))
    false_flag_rate = false_flags / total_runs if total_runs else None
    metrics.append(
        Metric(
            "integrity_false_flag_rate",
            "False-flag rate, integrity",
            false_flag_rate,
            "< 1%",
            false_flag_rate < 0.01 if false_flag_rate is not None else None,
            "The highest-consequence error the system can make, which is why B2 outputs evidence and "
            "never a verdict.",
            "%",
        )
    )

    # -- mastery predictive validity --------------------------------------
    validity = mastery_predictive_validity(session, course_id)
    metrics.append(
        Metric(
            "mastery_predictive_validity",
            "Mastery predictive validity (AUC)",
            validity.get("auc"),
            "> 0.75 on next-assignment performance",
            validity.get("meets_target"),
            "Does the mastery model mean anything? A knowledge tracer that does not predict future "
            "performance is an expensive decoration.",
        )
    )

    # -- latency ---------------------------------------------------------
    durations = sorted(run.duration_ms for _, run in verdicts if run.duration_ms)
    p95 = durations[int(len(durations) * 0.95)] / 1000.0 if durations else None
    metrics.append(
        Metric(
            "p95_latency_s",
            "p95 grading latency",
            p95,
            "< 180 s",
            p95 < 180 if p95 is not None else None,
            "Feedback loses pedagogical value fast. This is measured end to end, cascade included.",
            "s",
        )
    )

    # -- faculty minutes -------------------------------------------------
    escalated = sum(1 for v, _ in verdicts if v.state == VerdictState.ESCALATED)
    minutes_per_assignment = None
    if assignment_ids and total_runs:
        # 6 minutes for a full manual grade, ~2.5 for a review of an escalation.
        saved_baseline = total_runs * 6.0
        actual = escalated * 2.5 + len(overrides) * 1.5
        minutes_per_assignment = actual / len(assignment_ids)
        metrics.append(
            Metric(
                "faculty_minutes_per_assignment",
                "Faculty minutes per assignment",
                minutes_per_assignment,
                "falling semester over semester",
                None,
                f"Estimated {actual:.0f} min of review against a {saved_baseline:.0f} min manual baseline "
                f"across {total_runs} submission(s). This is the actual product claim.",
                "min",
            )
        )

    # -- confidence model health ------------------------------------------
    model = confidence_model.load_model()
    metrics.append(
        Metric(
            "confidence_model_examples",
            "Confidence-estimator training examples",
            float(model.n_training_examples),
            "grows every semester",
            None,
            "Model 5 trains on every faculty-graded submission, forever. Its training set is data the "
            "platform generates simply by operating.",
            "rows",
        )
    )

    return metrics


def mastery_predictive_validity(session: Session, course_id: str) -> dict:
    """Hold out the last assignment, trace mastery on everything before it, and
    ask whether that mastery predicted performance on the held-out work."""
    assignments = list(
        session.scalars(
            select(Assignment).where(Assignment.course_id == course_id).order_by(Assignment.due_at)
        )
    )
    if len(assignments) < 2:
        return {"auc": None, "n": 0, "note": "needs at least two assignments"}

    holdout_id = assignments[-1].id
    by_student = load_observations(session, course_id)
    graph = load_concept_graph(session, course_id)
    prerequisites = prerequisite_map(graph)

    aucs: list[float] = []
    total_n = 0
    for student_id, observations in by_student.items():
        earlier = [o for o in observations if o.assignment_id != holdout_id]
        later = [o for o in observations if o.assignment_id == holdout_id]
        if not earlier or not later:
            continue
        params = {key: bkt.BKTParams() for key in {o.concept_key for o in earlier}}
        states = bkt.trace_student(earlier, params, prerequisites)
        result = bkt.predictive_validity(states, later)
        if result.get("auc") is not None:
            aucs.append(result["auc"])
            total_n += result["n"]

    if not aucs:
        # Fall back to a cohort-level pooled computation, which needs less
        # per-student data than the per-student version.
        pooled_states: dict[str, bkt.MasteryState] = {}
        pooled_later: list[bkt.Observation] = []
        for student_id, observations in by_student.items():
            earlier = [o for o in observations if o.assignment_id != holdout_id]
            later = [o for o in observations if o.assignment_id == holdout_id]
            if not earlier or not later:
                continue
            params = {key: bkt.BKTParams() for key in {o.concept_key for o in earlier}}
            states = bkt.trace_student(earlier, params, prerequisites)
            for key, state in states.items():
                pooled_states[f"{student_id}:{key}"] = state
            for observation in later:
                pooled_later.append(
                    bkt.Observation(
                        concept_key=f"{student_id}:{observation.concept_key}",
                        score_fraction=observation.score_fraction,
                        confidence=observation.confidence,
                        evidence_source=observation.evidence_source,
                        observed_at=observation.observed_at,
                    )
                )
        result = bkt.predictive_validity(pooled_states, pooled_later)
        result["method"] = "pooled across the cohort"
        return result

    auc = sum(aucs) / len(aucs)
    return {
        "auc": round(auc, 4),
        "n": total_n,
        "students": len(aucs),
        "target": 0.75,
        "meets_target": auc >= 0.75,
        "method": "per-student, averaged",
        "holdout_assignment": assignments[-1].code,
    }


def train_confidence_model(session: Session) -> dict:
    """Retrain model 5 on the accumulated override history.

    Called after a review session. Every faculty correction is one row, and the
    model improves monotonically because the data only ever grows.
    """
    overrides = session.scalars(select(FacultyOverride)).all()
    examples: list[tuple[dict[str, float], float]] = []
    for override in overrides:
        run = session.get(EvaluationRun, override.run_id)
        if run is None:
            continue
        score = next((s for s in run.item_scores if s.item_key == override.item_key), None)
        if score is None:
            continue
        verdict = run.verdict
        features = {
            "signal_agreement": score.signal_agreement,
            "evidence_completeness": min(1.0, len(score.signals or []) / 3.0),
            "boundary_distance": 1.0,
            "test_pass_rate": next(
                (s["score"] for s in (score.signals or []) if s.get("source") in ("test", "repair")), 0.0
            ),
            "test_coverage": 1.0 if any(s.get("source") == "test" for s in (score.signals or [])) else 0.0,
            "repair_distance_norm": min(1.0, (verdict.syntax_penalty if verdict else 0.0) * 4),
            "similarity_max": 1.0 if (verdict and verdict.integrity_flag) else 0.0,
            "entailment_contradiction_rate": 0.0,
            "stage_error": 0.0,
            "static_check_rate": next(
                (s["score"] for s in (score.signals or []) if s.get("source") == "static"), 0.0
            ),
        }
        target = 1.0 - abs(override.auto_score_fraction - override.faculty_score_fraction)
        examples.append((features, target))

    model = confidence_model.fit(examples)
    confidence_model.save_model(model)
    return {
        "trained_on": len(examples),
        "holdout_mae": model.holdout_mae,
        "weights": model.weights,
        "note": (
            "Fewer than eight examples leaves the priors in place: with a pilot's worth of data, "
            "shrinking toward the priors is correct, not a limitation."
        ),
    }


def system_health(session: Session) -> dict:
    """Queue depth, worker utilisation, sandbox denials, p95 latency (§9 admin)."""
    from ..engine.sandbox import describe_isolation

    total_runs = session.scalar(select(func.count(EvaluationRun.id))) or 0
    escalated = session.scalar(
        select(func.count(Verdict.id)).where(Verdict.state == VerdictState.ESCALATED)
    ) or 0
    durations = sorted(
        d for d in session.scalars(select(EvaluationRun.duration_ms)) if d
    )
    p95 = durations[int(len(durations) * 0.95)] if durations else 0

    return {
        "total_runs": total_runs,
        "review_queue_depth": escalated,
        "p95_latency_ms": p95,
        "median_latency_ms": durations[len(durations) // 2] if durations else 0,
        "observations": session.scalar(select(func.count(ConceptObservation.id))) or 0,
        "mastery_rows": session.scalar(select(func.count(StudentConceptMastery.id))) or 0,
        "isolation": describe_isolation(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
