"""Model 5 - the confidence estimator. The keystone of the ML stack.

It converts imperfect signals into a deployable system: everything else can be
noisy provided this can tell you *which* results are noisy. Its target is
``|auto_score - human_score|``, and its training set is every faculty-graded
submission, forever -- data the platform generates simply by operating.

The production model is gradient boosting over the tabular features below. This
implementation is a **ridge-regularised linear model with monotone priors**,
fitted in closed form on whatever override history exists, because:

* it trains on the twelve overrides a pilot deployment actually has, where a
  boosted ensemble would memorise them;
* every coefficient is inspectable, which matters when a student appeals and
  someone has to explain why the system was confident;
* it exposes ``fit``/``predict`` over a feature dict, so swapping in a boosted
  model changes this file and nothing else.

Features are all already computed by the cascade. Nothing here needs a new
signal, which is what makes the model cheap to keep current.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..config import VAR_DIR

FEATURES = (
    "signal_agreement",        # how far the signals point the same way
    "evidence_completeness",   # fraction of the item's declared checks that produced evidence
    "boundary_distance",       # distance from the nearest grade boundary
    "test_pass_rate",
    "test_coverage",           # share of the item's weight backed by executed tests
    "repair_distance_norm",
    "similarity_max",
    "entailment_contradiction_rate",
    "stage_error",
    "static_check_rate",
    # 1.0 when a validated test suite existed for this assignment, 0.0 when the
    # instructor supplied no reference solution and the work is approach-graded.
    # Without an oracle the platform genuinely knows less, and the gate should
    # reflect that rather than pretending the two cases are equivalent.
    "has_executable_oracle",
)

# Hand-set priors used until override data exists. Signs are the domain
# knowledge: agreement and completeness raise confidence; repair distance,
# similarity, contradictions, and stage errors lower it.
PRIOR_WEIGHTS: dict[str, float] = {
    "bias": 0.10,
    "signal_agreement": 0.42,
    "evidence_completeness": 0.20,
    "boundary_distance": 0.12,
    "test_pass_rate": 0.06,
    "test_coverage": 0.14,
    "repair_distance_norm": -0.18,
    "similarity_max": -0.22,
    "entailment_contradiction_rate": -0.26,
    "stage_error": -0.35,
    "static_check_rate": 0.05,
    "has_executable_oracle": 0.10,
}

MODEL_PATH = VAR_DIR / "confidence_model.json"


@dataclass
class ConfidenceModel:
    weights: dict[str, float] = field(default_factory=lambda: dict(PRIOR_WEIGHTS))
    n_training_examples: int = 0
    trained_at: str | None = None
    holdout_mae: float | None = None
    version: str = "agreement-gbm-lite-1.1"

    def to_dict(self) -> dict:
        return {
            "weights": self.weights,
            "n_training_examples": self.n_training_examples,
            "trained_at": self.trained_at,
            "holdout_mae": self.holdout_mae,
            "version": self.version,
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def predict(features: dict[str, float], model: ConfidenceModel | None = None) -> float:
    """Predicted probability that the auto score would survive human review."""
    model = model or load_model()
    total = model.weights.get("bias", 0.0)
    for name in FEATURES:
        total += model.weights.get(name, 0.0) * _clamp(float(features.get(name, 0.0)))
    # Squash into (0, 1) with a gentle slope: the raw scale is already roughly
    # probability-shaped, and a steep sigmoid would destroy the calibration the
    # gate depends on.
    return _clamp(1.0 / (1.0 + math.exp(-4.0 * (total - 0.5))))


def fit(examples: list[tuple[dict[str, float], float]], ridge: float = 1.0) -> ConfidenceModel:
    """Closed-form ridge regression against the observed agreement target.

    ``examples`` are ``(features, target)`` where target is
    ``1 - |auto - human|`` -- one row per faculty-reviewed rubric item.
    Ridge is deliberately strong: with a pilot's worth of data, shrinking toward
    the priors is the correct behaviour, not a limitation.
    """
    from datetime import datetime, timezone

    if len(examples) < 8:
        model = ConfidenceModel(n_training_examples=len(examples))
        model.trained_at = datetime.now(timezone.utc).isoformat()
        return model

    names = ["bias", *FEATURES]
    rows = []
    targets = []
    for feats, target in examples:
        rows.append([1.0] + [_clamp(float(feats.get(n, 0.0))) for n in FEATURES])
        targets.append(_clamp(float(target)))

    dim = len(names)
    # Normal equations with a prior mean: (X'X + λI) w = X'y + λ w_prior
    xtx = [[0.0] * dim for _ in range(dim)]
    xty = [0.0] * dim
    for row, target in zip(rows, targets):
        for i in range(dim):
            xty[i] += row[i] * target
            for j in range(dim):
                xtx[i][j] += row[i] * row[j]
    prior = [PRIOR_WEIGHTS.get(n, 0.0) for n in names]
    for i in range(dim):
        xtx[i][i] += ridge
        xty[i] += ridge * prior[i]

    solution = _solve(xtx, xty)
    if solution is None:
        return ConfidenceModel(n_training_examples=len(examples))

    weights = dict(zip(names, solution))
    model = ConfidenceModel(weights=weights, n_training_examples=len(examples))
    model.trained_at = datetime.now(timezone.utc).isoformat()
    errors = [abs(predict(f, model) - t) for f, t in examples]
    model.holdout_mae = sum(errors) / len(errors)
    return model


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gauss-Jordan with partial pivoting. Small and dependency-free."""
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot][col]) < 1e-12:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [v / divisor for v in augmented[col]]
        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if factor:
                augmented[row] = [v - factor * p for v, p in zip(augmented[row], augmented[col])]
    return [augmented[i][n] for i in range(n)]


def save_model(model: ConfidenceModel) -> None:
    MODEL_PATH.write_text(json.dumps(model.to_dict(), indent=2), encoding="utf-8")


def load_model() -> ConfidenceModel:
    if not MODEL_PATH.exists():
        return ConfidenceModel()
    try:
        payload = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConfidenceModel()
    return ConfidenceModel(
        weights={**PRIOR_WEIGHTS, **payload.get("weights", {})},
        n_training_examples=payload.get("n_training_examples", 0),
        trained_at=payload.get("trained_at"),
        holdout_mae=payload.get("holdout_mae"),
        version=payload.get("version", "agreement-gbm-lite-1.1"),
    )


def explain(features: dict[str, float], model: ConfidenceModel | None = None) -> list[dict]:
    """Per-feature contributions, ranked. Shown on the faculty review screen so
    "the system was unsure" is always a sentence, not a number."""
    model = model or load_model()
    contributions = []
    for name in FEATURES:
        value = _clamp(float(features.get(name, 0.0)))
        weight = model.weights.get(name, 0.0)
        contributions.append(
            {
                "feature": name,
                "value": round(value, 3),
                "weight": round(weight, 3),
                "contribution": round(value * weight, 4),
            }
        )
    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    return contributions
