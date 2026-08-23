"""B7 - Aggregate and gate.

::

    item_score      = sum(signal_score * signal_reliability) / sum(signal_reliability)
    item_confidence = f(signal_agreement, evidence_completeness, distance_to_boundary)

**Signal agreement is the strongest confidence predictor.** When tests, static
checks, and the report all point the same way, release. When they conflict, a
human is almost certainly needed -- and that is the entire routing rule, stated
once.

The routing table below is the one from §4.2. Thresholds tune on a
faculty-graded holdout and are exposed to the instructor as a single dial:
*"auto-release everything I'd agree with 95% of the time"* is a setting, not a
constant.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analytics import confidence as confidence_model
from ..config import GateConfig, settings
from ..models import EscalationReason, VerdictState


@dataclass
class Signal:
    """One piece of evidence about one rubric item.

    ``reliability`` is the prior weight of the source, not its confidence in
    this instance: an executed test is simply better evidence than a structural
    similarity score, and that ordering does not depend on the submission.
    """

    source: str            # test | static | structural | report | repair
    score: float           # 0..1
    reliability: float     # 0..1
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "score": round(self.score, 4),
            "reliability": round(self.reliability, 3),
            "note": self.note,
        }


SOURCE_RELIABILITY = {
    "test": 1.00,             # executed, deterministic, oracle held outside the guest
    "static": 0.85,           # exact query against the code graph, but narrow
    # An advisory check -- loop nesting standing in for asymptotic complexity --
    # is a heuristic, not a proof. It participates at low reliability rather
    # than being discarded: excluding it entirely leaves items with almost no
    # evidence, which escalates work that a human then has to grade by hand.
    # "Advisory" should mean "cannot dominate", not "cannot count".
    "static_advisory": 0.45,
    "structural": 0.55,       # similarity, not semantics
    "repair": 0.70,           # tests on repaired source: real, with a caveat
    "report": 0.35,           # weakest; never the sole basis for a score
    "manual": 1.00,
}


@dataclass
class ItemAggregate:
    item_key: str
    concept_ids: list[str]
    weight: float
    score_fraction: float
    confidence: float
    signal_agreement: float
    signals: list[Signal] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    features: dict[str, float] = field(default_factory=dict)


def _weighted_score(signals: list[Signal]) -> float:
    total_reliability = sum(s.reliability for s in signals)
    if total_reliability <= 0:
        return 0.0
    return sum(s.score * s.reliability for s in signals) / total_reliability


def signal_agreement(signals: list[Signal]) -> float:
    """1 - reliability-weighted mean absolute deviation from the aggregate.

    A single signal cannot agree or disagree with anything, so its score stands
    in for agreement at a discount set by *how good that one signal is*. A
    passing test suite -- deterministic, executed, with the oracle held outside
    the sandbox -- is strong evidence on its own; a lone structural-similarity
    score is not. A flat penalty for single-signal items would treat those two
    cases the same, which is both wrong and the reason such a system ends up
    escalating everything and getting switched off.
    """
    if not signals:
        return 0.0
    if len(signals) == 1:
        return 0.35 + 0.5 * signals[0].reliability
    aggregate = _weighted_score(signals)
    total_reliability = sum(s.reliability for s in signals) or 1.0
    deviation = sum(abs(s.score - aggregate) * s.reliability for s in signals) / total_reliability
    return max(0.0, 1.0 - 2.0 * deviation)


def boundary_distance(fraction: float, config: GateConfig) -> float:
    """Normalised distance to the nearest grade boundary, capped at 1."""
    if not config.grade_boundaries:
        return 1.0
    nearest = min(abs(fraction - b) for b in config.grade_boundaries)
    return min(1.0, nearest / max(config.grade_boundary_epsilon * 5, 1e-6))


def aggregate_item(
    item_key: str,
    concept_ids: list[str],
    weight: float,
    signals: list[Signal],
    evidence: list[str],
    context: dict,
    config: GateConfig | None = None,
) -> ItemAggregate:
    config = config or settings.gate
    score = _weighted_score(signals)
    agreement = signal_agreement(signals)

    # A repair-run test still answers the item's "test" requirement, and an
    # advisory static check still answers its "static" one, so sources are
    # mapped back to the vocabulary the rubric item declared.
    equivalent = {"repair": "test", "static_advisory": "static"}
    declared = set(context.get("declared_checks") or [])
    produced = {equivalent.get(s.source, s.source) for s in signals}
    completeness = len(produced & declared) / len(declared) if declared else (1.0 if produced else 0.0)

    features = {
        "signal_agreement": agreement,
        "evidence_completeness": completeness,
        "boundary_distance": boundary_distance(score, config),
        "test_pass_rate": context.get("test_pass_rate", 0.0),
        "test_coverage": 1.0 if "test" in produced else 0.0,
        "repair_distance_norm": min(1.0, context.get("repair_distance", 0) / 5.0),
        "similarity_max": context.get("similarity_max", 0.0),
        "entailment_contradiction_rate": context.get("contradiction_rate", 0.0),
        "stage_error": 1.0 if context.get("stage_error") else 0.0,
        "static_check_rate": context.get("static_check_rate", 0.0),
        "has_executable_oracle": context.get("has_executable_oracle", 1.0),
    }
    item_confidence = confidence_model.predict(features)

    return ItemAggregate(
        item_key=item_key,
        concept_ids=list(concept_ids),
        weight=weight,
        score_fraction=round(score, 4),
        confidence=round(item_confidence, 4),
        signal_agreement=round(agreement, 4),
        signals=signals,
        evidence=evidence,
        features=features,
    )


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
@dataclass
class GateDecision:
    state: VerdictState
    total_fraction: float
    total_points: float
    max_points: float
    confidence: float
    escalation_reasons: list[str] = field(default_factory=list)
    integrity_flag: bool = False
    syntax_penalty: float = 0.0
    rationale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "total_fraction": round(self.total_fraction, 4),
            "total_points": round(self.total_points, 2),
            "max_points": round(self.max_points, 2),
            "confidence": round(self.confidence, 4),
            "escalation_reasons": self.escalation_reasons,
            "integrity_flag": self.integrity_flag,
            "syntax_penalty": round(self.syntax_penalty, 4),
            "rationale": self.rationale,
        }


def decide(
    items: list[ItemAggregate],
    context: dict,
    config: GateConfig | None = None,
) -> GateDecision:
    """Apply the routing table. Every escalation carries its reason."""
    config = config or settings.gate

    total_weight = sum(i.weight for i in items) or 1.0
    raw_fraction = sum(i.score_fraction * i.weight for i in items) / total_weight

    syntax_penalty = float(context.get("syntax_penalty", 0.0))
    total_fraction = max(0.0, raw_fraction * (1.0 - syntax_penalty))

    # Item confidences combine reliability-weighted, then the run-level
    # conditions below can only lower the result.
    run_confidence = (
        sum(i.confidence * i.weight for i in items) / total_weight if items else 0.0
    )

    reasons: list[str] = []
    rationale: list[str] = []

    # -- conflict on a high-weight item ---------------------------------
    for item in items:
        share = item.weight / total_weight
        if share >= config.high_weight_conflict_share and item.signal_agreement < 0.5:
            reasons.append(EscalationReason.SIGNAL_CONFLICT.value)
            rationale.append(
                f"{item.item_key} carries {share:.0%} of the weight and its signals disagree "
                f"(agreement {item.signal_agreement:.2f}): "
                + "; ".join(f"{s.source}={s.score:.2f}" for s in item.signals)
            )
            break

    # -- integrity ------------------------------------------------------
    similarity_max = float(context.get("similarity_max", 0.0))
    integrity_flag = similarity_max >= config.similarity_escalate
    if integrity_flag:
        reasons.append(EscalationReason.INTEGRITY_FLAG.value)
        rationale.append(
            f"Similarity {similarity_max:.0%} against the comparison corpus is at or above the "
            f"{config.similarity_escalate:.0%} review threshold. Evidence only - no determination is made."
        )

    # -- report contradiction -------------------------------------------
    if context.get("contradictions", 0) > 0:
        reasons.append(EscalationReason.REPORT_CONTRADICTION.value)
        rationale.append(
            f"{context['contradictions']} report claim(s) contradict the submitted code. "
            "Routed to a human: this is either a misunderstanding worth teaching to, or an "
            "integrity question. It is never an automatic penalty."
        )

    # -- grade boundary --------------------------------------------------
    for boundary in config.grade_boundaries:
        if abs(total_fraction - boundary) <= config.grade_boundary_epsilon:
            reasons.append(EscalationReason.GRADE_BOUNDARY.value)
            rationale.append(
                f"Score {total_fraction:.1%} is within {config.grade_boundary_epsilon:.1%} of the "
                f"{boundary:.0%} grade boundary."
            )
            break

    # -- repair materiality ----------------------------------------------
    if syntax_penalty >= config.repair_penalty_escalate:
        reasons.append(EscalationReason.REPAIR_MATERIAL.value)
        rationale.append(
            f"A {syntax_penalty:.0%} syntax penalty from {context.get('repair_distance', 0)} repair "
            "edit(s) is material to the final score."
        )

    # -- stage errors ------------------------------------------------------
    if context.get("stage_error"):
        reasons.append(EscalationReason.STAGE_ERROR.value)
        rationale.append(f"A pipeline stage errored: {context.get('stage_error')}.")

    # -- confidence --------------------------------------------------------
    if run_confidence < config.auto_release_confidence:
        reasons.append(EscalationReason.LOW_CONFIDENCE.value)
        rationale.append(
            f"Run confidence {run_confidence:.2f} is below the auto-release threshold "
            f"{config.auto_release_confidence:.2f}."
        )

    state = VerdictState.ESCALATED if reasons else VerdictState.RELEASED
    if state == VerdictState.RELEASED:
        rationale.append(
            f"All signals agree and run confidence is {run_confidence:.2f}, at or above the "
            f"{config.auto_release_confidence:.2f} threshold. Auto-released."
        )

    return GateDecision(
        state=state,
        total_fraction=total_fraction,
        total_points=total_fraction * total_weight,
        max_points=total_weight,
        confidence=run_confidence,
        escalation_reasons=sorted(set(reasons)),
        integrity_flag=integrity_flag,
        syntax_penalty=syntax_penalty,
        rationale=rationale,
    )


def review_priority(items: list[ItemAggregate], decision: GateDecision) -> float:
    """Expected value of a human minute (§9, faculty review queue).

    escalation weight x rubric weight x confidence deficit. The first item in
    the queue should be where attention is worth the most, which is a different
    ordering from "oldest first" and a much better use of a lecturer's evening.
    """
    reason_weight = {
        EscalationReason.INTEGRITY_FLAG.value: 1.0,
        EscalationReason.REPORT_CONTRADICTION.value: 0.9,
        EscalationReason.SIGNAL_CONFLICT.value: 0.85,
        EscalationReason.STAGE_ERROR.value: 0.8,
        EscalationReason.GRADE_BOUNDARY.value: 0.6,
        EscalationReason.REPAIR_MATERIAL.value: 0.5,
        EscalationReason.LOW_CONFIDENCE.value: 0.45,
        EscalationReason.APPEAL.value: 1.0,
    }
    severity = max((reason_weight.get(r, 0.3) for r in decision.escalation_reasons), default=0.0)
    total_weight = sum(i.weight for i in items) or 1.0
    contested = sum(
        i.weight * (1.0 - i.confidence) for i in items if i.confidence < 0.8
    ) / total_weight
    deficit = max(0.0, 1.0 - decision.confidence)
    return round(severity * (0.5 + contested) * (0.5 + deficit), 4)
