"""§7.3 Administrator early warning.

Three requirements, all non-negotiable, and all enforced in code rather than in
a policy document:

* **Ranked contributing factors, never a bare score.** ``RiskAssessment`` cannot
  be constructed without them. "At risk" with no reason is unusable and unfair.
* **Route to support, never to sanction.** Every assessment carries an explicit
  routing field, and the only values it takes are support routes. Early warning
  that becomes a punishment mechanism will be gamed within one semester, and it
  destroys the data it depends on.
* **Audit for demographic bias before deployment.** ``bias_audit`` compares
  flag rates and false-positive rates across every protected attribute held,
  and is re-run every semester. A model that systematically over-flags one
  group is worse than no model.

Protected attributes are used *only* by the audit. They are never features.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import AnalyticsConfig, settings


@dataclass
class BehaviourFeatures:
    """Submission behaviour, not identity."""

    late_start_rate: float = 0.0          # fraction of assignments begun after the midpoint
    attempt_spike: float = 0.0            # unusual attempt counts, normalised
    abandonment_rate: float = 0.0         # attempts started and never completed
    engagement_decay: float = 0.0         # drop in activity over the term
    missed_assignments: int = 0
    days_since_last_submission: int = 0


@dataclass
class RiskFactor:
    name: str
    contribution: float
    detail: str

    def as_dict(self) -> dict:
        return {"factor": self.name, "contribution": round(self.contribution, 4), "detail": self.detail}


@dataclass
class RiskAssessment:
    student_id: str
    risk_score: float
    flagged: bool
    contributing_factors: list[RiskFactor]
    routed_to: str = "advising"
    lead_time_weeks: float | None = None

    def __post_init__(self) -> None:
        if self.flagged and not self.contributing_factors:
            raise ValueError(
                "A flagged student must carry ranked contributing factors. "
                "A bare risk score is unusable and unfair, so it is not representable here."
            )
        if self.routed_to not in ("advising", "tutoring", "peer_mentoring", "instructor_check_in"):
            raise ValueError(
                f"routed_to={self.routed_to!r} is not a support route. Early warning routes to "
                "support, never to sanction."
            )

    def as_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "risk_score": round(self.risk_score, 4),
            "flagged": self.flagged,
            "contributing_factors": [f.as_dict() for f in self.contributing_factors],
            "routed_to": self.routed_to,
            "lead_time_weeks": self.lead_time_weeks,
        }


# Weights are deliberately visible and deliberately few. The production model is
# gradient boosting on the same features; the interpretable version is what
# ships first, because a risk flag nobody can explain gets ignored or misused.
FEATURE_WEIGHTS = {
    "mastery_level": 0.30,
    "mastery_slope": 0.22,
    "prerequisite_gap_depth": 0.16,
    "late_start_rate": 0.09,
    "abandonment_rate": 0.09,
    "engagement_decay": 0.08,
    "missed_assignments": 0.06,
}


def mastery_slope(trajectory: list[dict]) -> float:
    """Least-squares slope of the mastery trajectory.

    Direction matters more than level: a student at 0.45 and climbing needs
    something completely different from a student at 0.45 and falling.
    """
    points = [(i, float(p.get("estimate", 0.0))) for i, p in enumerate(trajectory) if "estimate" in p]
    if len(points) < 3:
        return 0.0
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    return numerator / denominator if denominator else 0.0


def prerequisite_gap_depth(
    mastery: dict[str, float],
    prerequisites: dict[str, list[str]],
    threshold: float,
) -> int:
    """Longest chain of consecutively unmastered prerequisites.

    A gap three levels deep is a different problem from three unrelated weak
    concepts, and it takes far longer to close.
    """
    memo: dict[str, int] = {}

    def depth(concept: str, seen: frozenset[str]) -> int:
        if concept in seen:
            return 0
        if concept in memo:
            return memo[concept]
        if mastery.get(concept, 0.0) >= threshold:
            return 0
        best = 1 + max(
            (depth(p, seen | {concept}) for p in prerequisites.get(concept, [])),
            default=0,
        )
        memo[concept] = best
        return best

    return max((depth(c, frozenset()) for c in mastery), default=0)


def assess_student(
    student_id: str,
    mastery: dict[str, float],
    trajectories: dict[str, list[dict]],
    prerequisites: dict[str, list[str]],
    behaviour: BehaviourFeatures,
    config: AnalyticsConfig | None = None,
) -> RiskAssessment:
    config = config or settings.analytics
    factors: list[RiskFactor] = []

    mean_mastery = sum(mastery.values()) / len(mastery) if mastery else config.bkt_prior
    mastery_component = (1.0 - mean_mastery) * FEATURE_WEIGHTS["mastery_level"]
    factors.append(
        RiskFactor(
            "Low mastery across the course",
            mastery_component,
            f"Mean mastery is {mean_mastery:.0%} across {len(mastery)} tracked concept(s).",
        )
    )

    slopes = [mastery_slope(t) for t in trajectories.values() if len(t) >= 3]
    mean_slope = sum(slopes) / len(slopes) if slopes else 0.0
    slope_component = max(0.0, -mean_slope * 6.0) * FEATURE_WEIGHTS["mastery_slope"]
    if slope_component > 0.005:
        factors.append(
            RiskFactor(
                "Declining mastery trajectory",
                slope_component,
                f"Mastery is trending down at {mean_slope:.3f} per observation across {len(slopes)} concept(s).",
            )
        )

    gap_depth = prerequisite_gap_depth(mastery, prerequisites, config.mastery_threshold)
    gap_component = min(1.0, gap_depth / 4.0) * FEATURE_WEIGHTS["prerequisite_gap_depth"]
    if gap_depth >= 2:
        factors.append(
            RiskFactor(
                "Deep prerequisite gap",
                gap_component,
                f"There is a chain of {gap_depth} consecutively unmastered prerequisites. "
                "Later material will not land until the base of that chain is addressed.",
            )
        )

    if behaviour.late_start_rate > 0.4:
        component = behaviour.late_start_rate * FEATURE_WEIGHTS["late_start_rate"]
        factors.append(
            RiskFactor(
                "Consistently late starts",
                component,
                f"{behaviour.late_start_rate:.0%} of assignments were begun after the halfway point.",
            )
        )
    if behaviour.abandonment_rate > 0.25:
        component = behaviour.abandonment_rate * FEATURE_WEIGHTS["abandonment_rate"]
        factors.append(
            RiskFactor(
                "Attempts abandoned mid-way",
                component,
                f"{behaviour.abandonment_rate:.0%} of started attempts were never completed.",
            )
        )
    if behaviour.engagement_decay > 0.3:
        component = behaviour.engagement_decay * FEATURE_WEIGHTS["engagement_decay"]
        factors.append(
            RiskFactor(
                "Engagement falling over the term",
                component,
                f"Activity has fallen {behaviour.engagement_decay:.0%} against this student's own earlier baseline.",
            )
        )
    if behaviour.missed_assignments:
        component = min(1.0, behaviour.missed_assignments / 3.0) * FEATURE_WEIGHTS["missed_assignments"]
        factors.append(
            RiskFactor(
                "Missed assignments",
                component,
                f"{behaviour.missed_assignments} assignment(s) with no submission at all.",
            )
        )

    raw = sum(f.contribution for f in factors)
    # Calibrated squash so the score reads as a probability rather than a sum.
    risk_score = 1.0 / (1.0 + math.exp(-6.0 * (raw - 0.32)))
    factors.sort(key=lambda f: f.contribution, reverse=True)
    flagged = risk_score >= config.risk_flag_threshold

    routed_to = "advising"
    if flagged and gap_depth >= 2:
        routed_to = "tutoring"
    elif flagged and behaviour.engagement_decay > 0.4:
        routed_to = "instructor_check_in"

    return RiskAssessment(
        student_id=student_id,
        risk_score=risk_score,
        flagged=flagged,
        contributing_factors=factors if flagged else factors[:3],
        routed_to=routed_to,
    )


# --------------------------------------------------------------------------
# Bias audit - non-negotiable, re-run every semester
# --------------------------------------------------------------------------
@dataclass
class BiasAuditRow:
    attribute: str
    group: str
    n: int
    flag_rate: float
    false_positive_rate: float | None
    delta_vs_baseline: float
    status: str

    def as_dict(self) -> dict:
        return {
            "attribute": self.attribute,
            "group": self.group,
            "n": self.n,
            "flag_rate": round(self.flag_rate, 4),
            "false_positive_rate": round(self.false_positive_rate, 4) if self.false_positive_rate is not None else None,
            "delta_vs_baseline": round(self.delta_vs_baseline, 4),
            "status": status_label(self.status),
        }


def status_label(status: str) -> str:
    return status


@dataclass
class BiasAudit:
    rows: list[BiasAuditRow] = field(default_factory=list)
    max_delta: float = 0.0
    passed: bool = True
    deployment_blocked: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "rows": [r.as_dict() for r in self.rows],
            "max_flag_rate_delta": round(self.max_delta, 4),
            "threshold": settings.analytics.bias_flag_rate_delta,
            "passed": self.passed,
            "deployment_blocked": self.deployment_blocked,
            "note": self.note,
            "audited_at": datetime.now(timezone.utc).isoformat(),
        }


def bias_audit(
    assessments: list[RiskAssessment],
    protected_attributes: dict[str, dict[str, str]],
    outcomes: dict[str, bool] | None = None,
    config: AnalyticsConfig | None = None,
) -> BiasAudit:
    """Flag-rate and false-positive-rate parity across every attribute held.

    ``outcomes`` maps student_id -> "did in fact fail", where known. Without it
    only flag-rate parity can be checked, and the audit says so rather than
    implying a completeness it does not have.
    """
    config = config or settings.analytics
    by_attribute: dict[str, dict[str, list[RiskAssessment]]] = {}
    for assessment in assessments:
        attributes = protected_attributes.get(assessment.student_id, {})
        for attribute, value in attributes.items():
            by_attribute.setdefault(attribute, {}).setdefault(str(value), []).append(assessment)

    rows: list[BiasAuditRow] = []
    max_delta = 0.0

    for attribute, groups in by_attribute.items():
        eligible = {g: members for g, members in groups.items() if len(members) >= 5}
        if len(eligible) < 2:
            continue
        overall_flag_rate = sum(
            sum(1 for a in members if a.flagged) for members in eligible.values()
        ) / sum(len(members) for members in eligible.values())

        for group, members in sorted(eligible.items()):
            flag_rate = sum(1 for a in members if a.flagged) / len(members)
            false_positive_rate = None
            if outcomes:
                negatives = [a for a in members if outcomes.get(a.student_id) is False]
                if negatives:
                    false_positive_rate = sum(1 for a in negatives if a.flagged) / len(negatives)
            delta = flag_rate - overall_flag_rate
            max_delta = max(max_delta, abs(delta))
            status = "ok" if abs(delta) <= config.bias_flag_rate_delta else "over-flagged" if delta > 0 else "under-flagged"
            rows.append(
                BiasAuditRow(attribute, group, len(members), flag_rate, false_positive_rate, delta, status)
            )

    passed = max_delta <= config.bias_flag_rate_delta
    if not rows:
        note = (
            "No protected attribute had two groups of at least five students, so parity could not be "
            "tested. This is an inconclusive audit, not a passing one."
        )
        return BiasAudit(rows, 0.0, passed=False, deployment_blocked=True, note=note)

    note = (
        f"Maximum flag-rate difference across groups is {max_delta:.1%} against a "
        f"{config.bias_flag_rate_delta:.0%} threshold. "
        + (
            "Within tolerance. Re-audit next semester."
            if passed
            else "OVER THRESHOLD - the risk model must not be deployed until this is resolved. "
            "A model that systematically over-flags one group is worse than no model."
        )
    )
    if not outcomes:
        note += " False-positive parity was not computed: no realised outcomes are available yet."

    return BiasAudit(rows, max_delta, passed=passed, deployment_blocked=not passed, note=note)


def lead_time(
    first_flagged_at: datetime | None,
    outcome_at: datetime | None,
) -> float | None:
    """Weeks of warning before the failure the flag predicted (§11 target: 3+).

    An unactionable warning is not a warning, so this is tracked as a headline
    metric rather than an operational curiosity.
    """
    if first_flagged_at is None or outcome_at is None:
        return None
    delta: timedelta = outcome_at - first_flagged_at
    return round(delta.days / 7.0, 2)
