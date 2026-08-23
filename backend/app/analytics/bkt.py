"""§6.2 Knowledge tracing. Bayesian Knowledge Tracing, with two extensions.

BKT is four parameters per concept -- prior, learn, slip, guess -- fitted per
cohort by EM. Deep knowledge tracing scores better on large datasets and is a
sensible later upgrade; it is not the right *starting* model here, because BKT
is interpretable (a faculty member can be shown exactly why mastery moved),
works on the small data a single course produces, and survives an appeal.

Two domain-specific extensions matter more than a fancier model:

* **Observations are weighted by evidence confidence.** A concept mark from a
  passing test suite is stronger evidence than one from structural credit on
  non-compiling code, so slip and guess vary by evidence source.
* **Updates propagate down the prerequisite DAG.** Repeated failure on a
  concept whose prerequisites are unmastered should move the *prerequisites*,
  not just the concept. This is what makes remediation point somewhere useful
  instead of telling a student to try the same thing again.

``uncertainty`` is a first-class output. "We don't have enough evidence about
your recursion yet" is an honest and useful thing to display, and it decides
whether the next recommendation is practice or a diagnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..config import AnalyticsConfig, settings


@dataclass
class BKTParams:
    prior: float = 0.25      # P(L0) - mastery before any evidence
    learn: float = 0.18      # P(T)  - chance of learning between observations
    slip: float = 0.10       # P(S)  - mastered but got it wrong
    guess: float = 0.20      # P(G)  - not mastered but got it right

    def clamped(self) -> BKTParams:
        return BKTParams(
            prior=min(0.95, max(0.01, self.prior)),
            learn=min(0.60, max(0.001, self.learn)),
            # Slip and guess are capped well below 0.5. Above that the model is
            # degenerate: "wrong answers indicate mastery" fits some datasets
            # and is never the explanation you want.
            slip=min(0.40, max(0.001, self.slip)),
            guess=min(0.40, max(0.001, self.guess)),
        )


# Evidence-source-specific slip/guess multipliers. A passing test is hard to
# fake and hard to pass by luck; structural credit is neither.
SOURCE_RELIABILITY_ADJUSTMENT = {
    "test":       {"slip": 0.85, "guess": 0.80},
    "repair":     {"slip": 1.00, "guess": 0.95},
    "static":     {"slip": 0.95, "guess": 1.05},
    "structural": {"slip": 1.35, "guess": 1.45},
    "report":     {"slip": 1.50, "guess": 1.60},
    "manual":     {"slip": 0.60, "guess": 0.60},
}


@dataclass
class Observation:
    concept_key: str
    score_fraction: float
    confidence: float
    evidence_source: str = "test"
    observed_at: datetime | None = None
    assignment_id: str = ""
    run_id: str = ""


@dataclass
class MasteryState:
    concept_key: str
    mastery: float
    uncertainty: float
    evidence_count: int
    trajectory: list[dict] = field(default_factory=list)
    source: str = "bkt"


def _adjusted(params: BKTParams, source: str, confidence: float) -> tuple[float, float]:
    """Slip and guess for one observation, widened when confidence is low.

    A low-confidence observation should move the estimate less. Inflating slip
    and guess is the principled way to say that inside BKT: it makes the
    likelihood ratio closer to 1 rather than discarding the evidence.
    """
    adjustment = SOURCE_RELIABILITY_ADJUSTMENT.get(source, {"slip": 1.2, "guess": 1.2})
    dilution = 1.0 + (1.0 - max(0.0, min(1.0, confidence)))
    slip = min(0.45, params.slip * adjustment["slip"] * dilution)
    guess = min(0.45, params.guess * adjustment["guess"] * dilution)
    return slip, guess


def update(mastery: float, correct: float, params: BKTParams, source: str, confidence: float) -> float:
    """One BKT posterior update, then the learning transition.

    ``correct`` is continuous in [0, 1] rather than binary, because a rubric
    item is partially satisfiable. The posterior is the correct/incorrect
    update blended by that fraction, which reduces to standard BKT at 0 and 1.
    """
    slip, guess = _adjusted(params, source, confidence)
    correct = max(0.0, min(1.0, correct))

    p_correct_given_mastery = 1.0 - slip
    p_correct_given_not = guess
    numerator_c = mastery * p_correct_given_mastery
    denominator_c = numerator_c + (1.0 - mastery) * p_correct_given_not
    posterior_correct = numerator_c / denominator_c if denominator_c > 0 else mastery

    numerator_i = mastery * slip
    denominator_i = numerator_i + (1.0 - mastery) * (1.0 - guess)
    posterior_incorrect = numerator_i / denominator_i if denominator_i > 0 else mastery

    posterior = correct * posterior_correct + (1.0 - correct) * posterior_incorrect
    # Transition: the student may have learned it between observations.
    return posterior + (1.0 - posterior) * params.learn


def _uncertainty(mastery: float, evidence_count: int, config: AnalyticsConfig) -> float:
    """Uncertainty falls with evidence and rises near the decision boundary.

    Both terms matter. Ten observations that all leave the estimate at 0.5 is a
    genuinely uncertain state, and reporting it as confident because the count
    is high would be exactly the wrong answer.
    """
    if evidence_count <= 0:
        return 1.0
    sampling = 1.0 / (1.0 + evidence_count) ** 0.5
    boundary = 1.0 - 2.0 * abs(mastery - 0.5)
    return round(min(1.0, 0.65 * sampling + 0.35 * boundary), 4)


def trace_student(
    observations: list[Observation],
    params_by_concept: dict[str, BKTParams],
    prerequisites: dict[str, list[str]],
    config: AnalyticsConfig | None = None,
) -> dict[str, MasteryState]:
    """Run BKT over one student's observation stream, then propagate downward."""
    config = config or settings.analytics
    ordered = sorted(observations, key=lambda o: (o.observed_at or datetime.min))

    states: dict[str, MasteryState] = {}
    for observation in ordered:
        params = params_by_concept.get(observation.concept_key, BKTParams(
            prior=config.bkt_prior, learn=config.bkt_learn,
            slip=config.bkt_slip, guess=config.bkt_guess,
        )).clamped()
        state = states.get(observation.concept_key)
        if state is None:
            state = MasteryState(observation.concept_key, params.prior, 1.0, 0)
            states[observation.concept_key] = state

        state.mastery = update(
            state.mastery,
            observation.score_fraction,
            params,
            observation.evidence_source,
            observation.confidence,
        )
        state.evidence_count += 1
        state.uncertainty = _uncertainty(state.mastery, state.evidence_count, config)
        state.trajectory.append(
            {
                "t": (observation.observed_at or datetime.min).isoformat(),
                "estimate": round(state.mastery, 4),
                "uncertainty": state.uncertainty,
                "assignment_id": observation.assignment_id,
                "source": observation.evidence_source,
                "score": round(observation.score_fraction, 3),
            }
        )

    propagate_prerequisites(states, prerequisites, observations, config)
    return states


def propagate_prerequisites(
    states: dict[str, MasteryState],
    prerequisites: dict[str, list[str]],
    observations: list[Observation],
    config: AnalyticsConfig | None = None,
) -> None:
    """Push repeated failure down the DAG onto unmastered prerequisites.

    Someone failing tree traversal because they do not understand pointers has
    given you evidence about *pointers*. Recording it only against traversal is
    how a system ends up recommending more traversal practice.

    Prerequisites with their own direct evidence are moved less: a concept the
    student has actually been tested on should not be overwritten by inference.
    """
    config = config or settings.analytics
    direct_evidence: dict[str, int] = {}
    failures: dict[str, list[float]] = {}
    for observation in observations:
        direct_evidence[observation.concept_key] = direct_evidence.get(observation.concept_key, 0) + 1
        if observation.score_fraction < 0.5:
            failures.setdefault(observation.concept_key, []).append(
                (1.0 - observation.score_fraction) * observation.confidence
            )

    for concept_key, failure_weights in failures.items():
        if len(failure_weights) < 2:
            continue    # one bad day is not evidence about the prerequisites
        strength = sum(failure_weights) / len(failure_weights)
        for depth, prerequisite in enumerate(_walk_prerequisites(concept_key, prerequisites)):
            decay = config.prereq_propagation_factor ** (depth + 1)
            damping = 1.0 / (1.0 + direct_evidence.get(prerequisite, 0))
            adjustment = strength * decay * damping
            if adjustment < 0.01:
                continue
            state = states.get(prerequisite)
            if state is None:
                state = MasteryState(prerequisite, config.bkt_prior, 1.0, 0, source="prereq_inferred")
                states[prerequisite] = state
            state.mastery = max(0.02, state.mastery - adjustment)
            state.uncertainty = min(1.0, state.uncertainty + 0.10 * decay)
            state.trajectory.append(
                {
                    "t": datetime.utcnow().isoformat(),
                    "estimate": round(state.mastery, 4),
                    "uncertainty": round(state.uncertainty, 4),
                    "source": "prerequisite_propagation",
                    "from_concept": concept_key,
                }
            )


def _walk_prerequisites(concept_key: str, prerequisites: dict[str, list[str]], max_depth: int = 3) -> list[str]:
    """Breadth-first walk down the DAG, nearest prerequisites first."""
    seen = {concept_key}
    ordered: list[str] = []
    frontier = list(prerequisites.get(concept_key, []))
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for node in frontier:
            if node in seen:
                continue
            seen.add(node)
            ordered.append(node)
            next_frontier.extend(prerequisites.get(node, []))
        frontier = next_frontier
        depth += 1
    return ordered


# --------------------------------------------------------------------------
# EM parameter fitting (per cohort, per concept)
# --------------------------------------------------------------------------
def fit_parameters(
    cohort_observations: dict[str, list[Observation]],
    concept_key: str,
    config: AnalyticsConfig | None = None,
    iterations: int | None = None,
) -> BKTParams:
    """Expectation-maximisation for one concept across a cohort.

    Small, well-conditioned, and interpretable. With fewer than a handful of
    students the estimate collapses onto the configured priors, which is the
    right behaviour: a cohort of three does not identify four parameters.
    """
    config = config or settings.analytics
    iterations = iterations or config.bkt_em_iterations

    sequences: list[list[Observation]] = []
    for observations in cohort_observations.values():
        sequence = [o for o in observations if o.concept_key == concept_key]
        if sequence:
            sequences.append(sorted(sequence, key=lambda o: (o.observed_at or datetime.min)))

    params = BKTParams(
        prior=config.bkt_prior, learn=config.bkt_learn,
        slip=config.bkt_slip, guess=config.bkt_guess,
    )
    if len(sequences) < 4:
        return params

    for _ in range(iterations):
        prior_num = prior_den = 0.0
        learn_num = learn_den = 0.0
        slip_num = slip_den = 0.0
        guess_num = guess_den = 0.0

        for sequence in sequences:
            mastery = params.prior
            prior_den += 1.0
            prior_num += mastery
            for observation in sequence:
                correct = max(0.0, min(1.0, observation.score_fraction))
                slip, guess = _adjusted(params, observation.evidence_source, observation.confidence)

                p_correct = mastery * (1.0 - slip) + (1.0 - mastery) * guess
                p_correct = min(max(p_correct, 1e-6), 1 - 1e-6)
                # Responsibility that the student was in the mastered state.
                responsibility_correct = mastery * (1.0 - slip) / p_correct
                responsibility_incorrect = mastery * slip / (1.0 - p_correct)
                responsibility = correct * responsibility_correct + (1.0 - correct) * responsibility_incorrect
                responsibility = min(max(responsibility, 0.0), 1.0)

                slip_den += responsibility
                slip_num += responsibility * (1.0 - correct)
                guess_den += 1.0 - responsibility
                guess_num += (1.0 - responsibility) * correct

                posterior = responsibility
                learn_den += 1.0 - posterior
                transitioned = posterior + (1.0 - posterior) * params.learn
                learn_num += max(0.0, transitioned - posterior)
                mastery = transitioned

        params = BKTParams(
            prior=prior_num / prior_den if prior_den else params.prior,
            learn=learn_num / learn_den if learn_den > 1e-9 else params.learn,
            slip=slip_num / slip_den if slip_den > 1e-9 else params.slip,
            guess=guess_num / guess_den if guess_den > 1e-9 else params.guess,
        ).clamped()

    return params


def predictive_validity(
    states_before: dict[str, MasteryState],
    later_observations: list[Observation],
    threshold: float = 0.5,
) -> dict:
    """AUC of mastery against next-assignment performance (§11).

    A knowledge-tracing model that does not predict future performance is an
    expensive decoration, so this metric is reported alongside the accuracy
    numbers rather than beneath them.
    """
    pairs: list[tuple[float, int]] = []
    for observation in later_observations:
        state = states_before.get(observation.concept_key)
        if state is None:
            continue
        pairs.append((state.mastery, 1 if observation.score_fraction >= threshold else 0))

    positives = [score for score, label in pairs if label == 1]
    negatives = [score for score, label in pairs if label == 0]
    if not positives or not negatives:
        return {"auc": None, "n": len(pairs), "note": "insufficient class balance to compute AUC"}

    wins = ties = 0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    auc = (wins + 0.5 * ties) / (len(positives) * len(negatives))
    return {
        "auc": round(auc, 4),
        "n": len(pairs),
        "positives": len(positives),
        "negatives": len(negatives),
        "target": 0.75,
        "meets_target": auc >= 0.75,
    }
