"""§7.4 CO-PO attainment.

Because ``RubricItem -> concept_ids -> course_outcomes`` already exists, outcome
attainment is a rollup, not a project::

    CO attainment = weighted mean of mastery over concepts mapped to that CO,
                    aggregated across enrolled students,
                    thresholded per institutional policy

This is **direct** attainment, computed from actual performance evidence, with
per-student traceability down to individual submissions -- as opposed to the
indirect attainment usually reconstructed from a spreadsheet at the end of
semester. Programme-outcome attainment follows via the standard CO-PO matrix.

For NBA/NAAC-style accreditation this is the administrator-facing pull: a report
that currently costs weeks of manual work becomes a live view, and it is close
to free once the concept taxonomy exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AnalyticsConfig, settings

# Standard 3-level attainment scale used by NBA-style processes.
ATTAINMENT_LEVELS = (
    (0.80, 3, "Level 3 - substantially attained"),
    (0.70, 2, "Level 2 - moderately attained"),
    (0.60, 1, "Level 1 - partially attained"),
    (0.00, 0, "Level 0 - not attained"),
)


@dataclass
class OutcomeDefinition:
    code: str
    text: str
    programme_outcomes: list[str] = field(default_factory=list)
    po_weights: dict[str, float] = field(default_factory=dict)


@dataclass
class COAttainment:
    code: str
    text: str
    concept_keys: list[str]
    mean_mastery: float
    students_attaining: int
    cohort_size: int
    attainment_fraction: float
    level: int
    level_label: str
    evidence_count: int

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "text": self.text,
            "concepts": self.concept_keys,
            "mean_mastery": round(self.mean_mastery, 4),
            "students_attaining": self.students_attaining,
            "cohort_size": self.cohort_size,
            "attainment_fraction": round(self.attainment_fraction, 4),
            "level": self.level,
            "level_label": self.level_label,
            "evidence_count": self.evidence_count,
        }


def _level(fraction: float) -> tuple[int, str]:
    for threshold, level, label in ATTAINMENT_LEVELS:
        if fraction >= threshold:
            return level, label
    return 0, ATTAINMENT_LEVELS[-1][2]


def compute_co_attainment(
    outcomes: list[OutcomeDefinition],
    concept_to_outcomes: dict[str, list[str]],
    mastery_by_student: dict[str, dict[str, float]],
    evidence_counts: dict[str, int] | None = None,
    config: AnalyticsConfig | None = None,
) -> list[COAttainment]:
    """Direct attainment per course outcome, from evidence."""
    config = config or settings.analytics
    evidence_counts = evidence_counts or {}
    cohort_size = len(mastery_by_student)
    results: list[COAttainment] = []

    for outcome in outcomes:
        concept_keys = sorted(
            key for key, codes in concept_to_outcomes.items() if outcome.code in codes
        )
        if not concept_keys:
            results.append(
                COAttainment(
                    outcome.code, outcome.text, [], 0.0, 0, cohort_size, 0.0, 0,
                    "Level 0 - no concepts mapped to this outcome", 0,
                )
            )
            continue

        per_student: list[float] = []
        for snapshots in mastery_by_student.values():
            relevant = [snapshots[k] for k in concept_keys if k in snapshots]
            if relevant:
                per_student.append(sum(relevant) / len(relevant))

        if not per_student:
            results.append(
                COAttainment(
                    outcome.code, outcome.text, concept_keys, 0.0, 0, cohort_size, 0.0, 0,
                    "Level 0 - no evidence yet", 0,
                )
            )
            continue

        mean_mastery = sum(per_student) / len(per_student)
        attaining = sum(1 for m in per_student if m >= config.co_attainment_threshold)
        fraction = attaining / len(per_student)
        level, label = _level(fraction)
        results.append(
            COAttainment(
                code=outcome.code,
                text=outcome.text,
                concept_keys=concept_keys,
                mean_mastery=mean_mastery,
                students_attaining=attaining,
                cohort_size=len(per_student),
                attainment_fraction=fraction,
                level=level,
                level_label=label,
                evidence_count=sum(evidence_counts.get(k, 0) for k in concept_keys),
            )
        )

    results.sort(key=lambda r: r.code)
    return results


@dataclass
class POAttainment:
    code: str
    weighted_attainment: float
    level: int
    level_label: str
    contributing: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "weighted_attainment": round(self.weighted_attainment, 4),
            "level": self.level,
            "level_label": self.level_label,
            "contributing_cos": self.contributing,
        }


def compute_po_attainment(
    co_attainments: list[COAttainment],
    outcomes: list[OutcomeDefinition],
) -> list[POAttainment]:
    """Roll CO attainment up through the CO-PO matrix.

    Correlation weights are the standard 1/2/3 scale. Where an institution has
    not filled the matrix in, an unweighted mean is used and the row says so
    through its contributing list rather than silently pretending to a weight.
    """
    by_code = {c.code: c for c in co_attainments}
    accumulators: dict[str, list[tuple[float, float, str]]] = {}

    for outcome in outcomes:
        attainment = by_code.get(outcome.code)
        if attainment is None:
            continue
        for po in outcome.programme_outcomes:
            weight = float(outcome.po_weights.get(po, 1.0))
            accumulators.setdefault(po, []).append(
                (attainment.attainment_fraction, weight, outcome.code)
            )

    results: list[POAttainment] = []
    for po, entries in accumulators.items():
        total_weight = sum(w for _, w, _ in entries) or 1.0
        weighted = sum(value * w for value, w, _ in entries) / total_weight
        level, label = _level(weighted)
        results.append(
            POAttainment(
                code=po,
                weighted_attainment=weighted,
                level=level,
                level_label=label,
                contributing=[
                    {"co": code, "attainment": round(value, 4), "correlation_weight": w}
                    for value, w, code in sorted(entries, key=lambda e: e[2])
                ],
            )
        )
    results.sort(key=lambda r: (len(r.code), r.code))
    return results


def attainment_report(
    course_code: str,
    term: str,
    co_attainments: list[COAttainment],
    po_attainments: list[POAttainment],
    config: AnalyticsConfig | None = None,
) -> dict:
    """The document an accreditation visit actually asks for."""
    config = config or settings.analytics
    return {
        "course": course_code,
        "term": term,
        "attainment_threshold": config.co_attainment_threshold,
        "method": "direct",
        "method_note": (
            "Direct attainment computed from per-submission performance evidence. Every figure "
            "traces to rubric-item scores, which trace to specific test results, static checks, and "
            "faculty confirmations. No indirect or survey data is mixed in."
        ),
        "course_outcomes": [c.as_dict() for c in co_attainments],
        "programme_outcomes": [p.as_dict() for p in po_attainments],
        "summary": {
            "cos_attained": sum(1 for c in co_attainments if c.level >= 2),
            "cos_total": len(co_attainments),
            "weakest_co": min(co_attainments, key=lambda c: c.attainment_fraction).code
            if co_attainments
            else None,
            "mean_attainment": round(
                sum(c.attainment_fraction for c in co_attainments) / len(co_attainments), 4
            )
            if co_attainments
            else 0.0,
        },
    }
