"""§7.1 Student remediation and §7.2 faculty recommendations.

Insight that does not name a next action is not insight.

The core algorithm of this whole layer is one traversal: from the student's
failures, walk the concept DAG to the **lowest-mastery unmastered
prerequisite**, and recommend practice *there*. Someone failing tree traversal
because they do not understand pointers must be sent to pointers, not to more
trees. That walk is the entire reason the DAG exists.

Where mastery is *uncertain* rather than low, the recommendation is a
**diagnostic** rather than remediation: resolve the ambiguity before
prescribing. Telling a student to spend four hours on something you are not
sure they are weak at is how a recommendation engine loses its audience.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AnalyticsConfig, settings


@dataclass
class ConceptNode:
    concept_key: str
    name: str
    prerequisites: list[str] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    course_outcomes: list[str] = field(default_factory=list)
    typical_misconceptions: list[str] = field(default_factory=list)
    syllabus_week: int | None = None


@dataclass
class MasterySnapshot:
    concept_key: str
    mastery: float
    uncertainty: float
    evidence_count: int
    evidence_refs: list[dict] = field(default_factory=list)


@dataclass
class Recommendation:
    concept_key: str
    concept_name: str
    mastery: float
    uncertainty: float
    why_flagged: str
    evidence_refs: list[dict]
    recommended_action: str
    action_kind: str          # remediate | diagnose | extend
    estimated_effort: str
    priority: float
    prerequisite_path: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "concept": self.concept_key,
            "concept_name": self.concept_name,
            "mastery": round(self.mastery, 3),
            "uncertainty": round(self.uncertainty, 3),
            "why_flagged": self.why_flagged,
            "evidence_refs": self.evidence_refs,
            "recommended_action": self.recommended_action,
            "action_kind": self.action_kind,
            "estimated_effort": self.estimated_effort,
            "priority": round(self.priority, 4),
            "prerequisite_path": self.prerequisite_path,
        }


def _effort(mastery: float, evidence_count: int) -> str:
    if evidence_count == 0:
        return "20-30 min diagnostic"
    if mastery < 0.25:
        return "2-3 hours"
    if mastery < 0.5:
        return "1-2 hours"
    return "30-45 min"


def walk_to_root_gap(
    concept_key: str,
    graph: dict[str, ConceptNode],
    mastery: dict[str, MasterySnapshot],
    config: AnalyticsConfig,
    _seen: set[str] | None = None,
) -> tuple[str, list[str]]:
    """Descend to the lowest-mastery unmastered prerequisite.

    Returns ``(target_concept, path_walked)``. If every prerequisite is
    mastered, the failing concept is itself the right place to work, which is
    the useful base case rather than a failure of the search.
    """
    _seen = _seen or set()
    if concept_key in _seen:
        return concept_key, []          # DAG, but defend against authoring cycles
    _seen.add(concept_key)

    node = graph.get(concept_key)
    if node is None or not node.prerequisites:
        return concept_key, []

    unmastered = []
    for prerequisite in node.prerequisites:
        snapshot = mastery.get(prerequisite)
        estimate = snapshot.mastery if snapshot else config.bkt_prior
        if estimate < config.mastery_threshold:
            unmastered.append((estimate, prerequisite))

    if not unmastered:
        return concept_key, []

    unmastered.sort()
    _, weakest = unmastered[0]
    target, path = walk_to_root_gap(weakest, graph, mastery, config, _seen)
    return target, [weakest, *path]


def recommend_for_student(
    graph: dict[str, ConceptNode],
    mastery: dict[str, MasterySnapshot],
    config: AnalyticsConfig | None = None,
    limit: int = 5,
) -> list[Recommendation]:
    """Ranked next actions for one student, each traceable to evidence."""
    config = config or settings.analytics
    recommendations: dict[str, Recommendation] = {}

    weak = sorted(
        (s for s in mastery.values() if s.mastery < config.mastery_threshold),
        key=lambda s: s.mastery,
    )

    for snapshot in weak:
        node = graph.get(snapshot.concept_key)
        if node is None:
            continue
        target_key, path = walk_to_root_gap(snapshot.concept_key, graph, mastery, config)
        target_node = graph.get(target_key, node)
        target_snapshot = mastery.get(target_key)
        target_mastery = target_snapshot.mastery if target_snapshot else config.bkt_prior
        target_uncertainty = target_snapshot.uncertainty if target_snapshot else 1.0
        evidence_count = target_snapshot.evidence_count if target_snapshot else 0

        if target_uncertainty >= config.uncertainty_diagnostic_threshold and evidence_count < config.min_evidence_for_confidence:
            action_kind = "diagnose"
            action = _diagnostic_action(target_node)
            why = (
                f"We do not have enough evidence about {target_node.name} yet "
                f"({evidence_count} observation(s), uncertainty {target_uncertainty:.2f}). "
                "A short diagnostic will tell us whether this is actually the gap."
            )
        else:
            action_kind = "remediate"
            action = _remediation_action(target_node)
            if target_key == snapshot.concept_key:
                why = (
                    f"Mastery of {target_node.name} is {target_mastery:.0%}, below the "
                    f"{config.mastery_threshold:.0%} threshold, and its prerequisites are all in place - "
                    "so this concept itself is the gap."
                )
            else:
                chain = " -> ".join(graph[c].name for c in [snapshot.concept_key, *path] if c in graph)
                why = (
                    f"Failures on {node.name} trace down the prerequisite chain to {target_node.name} "
                    f"({target_mastery:.0%} mastery). Working on {node.name} directly will not help until "
                    f"{target_node.name} is solid. Path: {chain}."
                )

        priority = (1.0 - target_mastery) * (1.0 + 0.4 * len(path)) * (0.7 + 0.3 * (1.0 - target_uncertainty))
        existing = recommendations.get(target_key)
        if existing is None or priority > existing.priority:
            recommendations[target_key] = Recommendation(
                concept_key=target_key,
                concept_name=target_node.name,
                mastery=target_mastery,
                uncertainty=target_uncertainty,
                why_flagged=why,
                evidence_refs=(target_snapshot.evidence_refs if target_snapshot else [])[:6],
                recommended_action=action,
                action_kind=action_kind,
                estimated_effort=_effort(target_mastery, evidence_count),
                priority=priority,
                prerequisite_path=[snapshot.concept_key, *path],
            )

    ranked = sorted(recommendations.values(), key=lambda r: r.priority, reverse=True)
    return ranked[:limit]


def _remediation_action(node: ConceptNode) -> str:
    for resource in node.resources:
        if resource.get("kind") in ("practice", "problem_set", "exercise"):
            return f"Work through: {resource.get('title')} ({resource.get('url', 'see course materials')})"
    for resource in node.resources:
        return f"Review: {resource.get('title')} ({resource.get('url', 'see course materials')})"
    return (
        f"No practice resource is attached to {node.name} yet. "
        "Ask your instructor to attach one - the recommendation engine reads them from the concept node."
    )


def _diagnostic_action(node: ConceptNode) -> str:
    if node.typical_misconceptions:
        return (
            f"Attempt a short diagnostic on {node.name}, targeting the common error "
            f"'{node.typical_misconceptions[0]}'."
        )
    return f"Attempt a short diagnostic on {node.name} so we can tell practice from a one-off slip."


# --------------------------------------------------------------------------
# §7.2 Faculty recommendations
# --------------------------------------------------------------------------
def downstream_impact(concept_key: str, graph: dict[str, ConceptNode]) -> int:
    """How many concepts depend on this one, transitively.

    A weak concept with six dependents is far more urgent than a leaf node, and
    this is the number that makes the re-teach list a priority order rather
    than an alphabetical one.
    """
    dependents: set[str] = set()
    frontier = [concept_key]
    while frontier:
        current = frontier.pop()
        for key, node in graph.items():
            if current in node.prerequisites and key not in dependents:
                dependents.add(key)
                frontier.append(key)
    return len(dependents)


@dataclass
class ReteachSignal:
    concept_key: str
    concept_name: str
    cohort_mastery: float
    students_below: int
    cohort_size: int
    downstream_dependents: int
    urgency: float
    rationale: str
    syllabus_week: int | None = None
    direct_evidence_share: float = 1.0

    def as_dict(self) -> dict:
        return {
            "concept": self.concept_key,
            "concept_name": self.concept_name,
            "cohort_mastery": round(self.cohort_mastery, 3),
            "students_below": self.students_below,
            "cohort_size": self.cohort_size,
            "downstream_dependents": self.downstream_dependents,
            "urgency": round(self.urgency, 4),
            "rationale": self.rationale,
            "syllabus_week": self.syllabus_week,
            "direct_evidence_share": round(self.direct_evidence_share, 3),
        }


def reteach_signals(
    graph: dict[str, ConceptNode],
    mastery_by_student: dict[str, dict[str, MasterySnapshot]],
    config: AnalyticsConfig | None = None,
) -> list[ReteachSignal]:
    config = config or settings.analytics
    cohort_size = len(mastery_by_student) or 1
    signals: list[ReteachSignal] = []

    for concept_key, node in graph.items():
        relevant = [
            snapshots[concept_key]
            for snapshots in mastery_by_student.values()
            if concept_key in snapshots
        ]
        if len(relevant) < 3:
            continue
        estimates = [snapshot.mastery for snapshot in relevant]
        cohort_mastery = sum(estimates) / len(estimates)
        if cohort_mastery >= config.cohort_reteach_threshold:
            continue
        below = sum(1 for e in estimates if e < config.mastery_threshold)
        dependents = downstream_impact(concept_key, graph)

        # A concept whose cohort estimate comes mostly from prerequisite
        # inference rather than from evidence is a weaker basis for rewriting a
        # lecture than one the cohort was actually assessed on. It stays on the
        # list -- it may be the real gap -- but it does not outrank a measured
        # one, and the rationale says which it is.
        direct = sum(1 for snapshot in relevant if snapshot.evidence_count > 0)
        evidence_share = direct / len(relevant)

        urgency = (
            (1.0 - cohort_mastery)
            * (1.0 + 0.35 * dependents)
            * (below / len(estimates))
            * (0.35 + 0.65 * evidence_share)
        )
        if evidence_share >= 0.5:
            provenance = f"Estimated from direct evidence for {direct} of {len(relevant)} students."
        else:
            provenance = (
                f"Only {direct} of {len(relevant)} students have been directly assessed on this "
                "concept; the rest is inferred from failures on concepts that depend on it. Worth a "
                "diagnostic before rewriting a lecture around it."
            )

        signals.append(
            ReteachSignal(
                concept_key=concept_key,
                concept_name=node.name,
                cohort_mastery=cohort_mastery,
                students_below=below,
                cohort_size=cohort_size,
                downstream_dependents=dependents,
                urgency=urgency,
                syllabus_week=node.syllabus_week,
                direct_evidence_share=evidence_share,
                rationale=(
                    f"Cohort mastery is {cohort_mastery:.0%} with {below} of {len(estimates)} students "
                    f"below the {config.mastery_threshold:.0%} threshold. "
                    + (
                        f"{dependents} later concept(s) depend on this one, so the gap compounds. "
                        if dependents
                        else "This is a leaf concept, so the gap does not compound - weigh it against the calendar. "
                    )
                    + provenance
                ),
            )
        )

    signals.sort(key=lambda s: s.urgency, reverse=True)
    return signals


def pacing_feedback(
    graph: dict[str, ConceptNode],
    mastery_by_student: dict[str, dict[str, MasterySnapshot]],
    current_week: int,
    config: AnalyticsConfig | None = None,
) -> list[dict]:
    """Concepts where mastery lags the syllabus schedule."""
    config = config or settings.analytics
    lagging: list[dict] = []
    for concept_key, node in graph.items():
        if node.syllabus_week is None or node.syllabus_week > current_week:
            continue
        estimates = [
            snapshots[concept_key].mastery
            for snapshots in mastery_by_student.values()
            if concept_key in snapshots
        ]
        if len(estimates) < 3:
            continue
        cohort_mastery = sum(estimates) / len(estimates)
        weeks_since = current_week - node.syllabus_week
        if cohort_mastery < config.mastery_threshold and weeks_since >= 1:
            lagging.append(
                {
                    "concept": concept_key,
                    "concept_name": node.name,
                    "taught_in_week": node.syllabus_week,
                    "weeks_since_taught": weeks_since,
                    "cohort_mastery": round(cohort_mastery, 3),
                    "note": (
                        f"Taught in week {node.syllabus_week}, {weeks_since} week(s) ago, and cohort mastery "
                        f"is still {cohort_mastery:.0%}. The syllabus has moved on; the cohort has not."
                    ),
                }
            )
    lagging.sort(key=lambda item: (item["cohort_mastery"], -item["weeks_since_taught"]))
    return lagging


def intervention_list(
    mastery_by_student: dict[str, dict[str, MasterySnapshot]],
    graph: dict[str, ConceptNode],
    student_names: dict[str, str],
    config: AnalyticsConfig | None = None,
    limit: int = 12,
) -> list[dict]:
    """Which students to talk to, why, and what specifically to raise."""
    config = config or settings.analytics
    rows: list[dict] = []
    for student_id, snapshots in mastery_by_student.items():
        if not snapshots:
            continue
        recommendations = recommend_for_student(graph, snapshots, config, limit=2)
        if not recommendations:
            continue
        weak = [s for s in snapshots.values() if s.mastery < config.mastery_threshold]
        if not weak:
            continue
        mean_mastery = sum(s.mastery for s in snapshots.values()) / len(snapshots)
        top = recommendations[0]
        rows.append(
            {
                "student_id": student_id,
                "student_name": student_names.get(student_id, student_id),
                "mean_mastery": round(mean_mastery, 3),
                "weak_concepts": len(weak),
                "raise_with_them": top.concept_name,
                "why": top.why_flagged,
                "suggested_action": top.recommended_action,
                "priority": round(top.priority * (1.0 - mean_mastery + 0.3), 4),
            }
        )
    rows.sort(key=lambda r: r["priority"], reverse=True)
    return rows[:limit]
