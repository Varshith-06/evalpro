"""§6.3 Item analysis. Classical psychometrics, applied to rubric items.

This grades the *assessment*, not the student. It is cheap, entirely classical,
and almost no competing tool does it -- which is why it closes the loop every
other autograder leaves open.

Three statistics per item per cohort:

* **Difficulty** -- proportion achieving full marks. Below 0.2 or above 0.95
  the item carries little information, whichever way it points.
* **Discrimination** -- point-biserial correlation with total score. **Negative
  discrimination means the item is broken**: strong students are failing it,
  which almost always indicates an ambiguous spec or a wrong test.
* **Concept alignment** -- correlation between item performance and
  independently-estimated mastery of its tagged concepts. Weak alignment means
  the item is mis-tagged and its evidence is polluting the mastery model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import AnalyticsConfig, settings


@dataclass
class ItemResponse:
    student_id: str
    item_key: str
    score_fraction: float
    total_fraction: float
    concept_keys: list[str] = field(default_factory=list)


@dataclass
class ItemStats:
    item_key: str
    n: int
    difficulty: float
    discrimination: float
    concept_alignment: float
    flag: str
    explanation: str

    def as_dict(self) -> dict:
        return {
            "item_key": self.item_key,
            "n": self.n,
            "difficulty": round(self.difficulty, 4),
            "discrimination": round(self.discrimination, 4),
            "concept_alignment": round(self.concept_alignment, 4),
            "flag": self.flag,
            "explanation": self.explanation,
        }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    return (sum((v - mu) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = (
        sum((x - mx) ** 2 for x in xs) ** 0.5 * sum((y - my) ** 2 for y in ys) ** 0.5
    )
    return numerator / denominator if denominator else 0.0


def point_biserial(item_scores: list[float], total_scores: list[float], cut: float = 0.999) -> float:
    """Point-biserial between a dichotomised item and the total score.

    Dichotomising at "full marks" matches the difficulty definition, so the two
    statistics describe the same event and can be read together.
    """
    if len(item_scores) < 3:
        return 0.0
    groups_high = [t for s, t in zip(item_scores, total_scores) if s >= cut]
    groups_low = [t for s, t in zip(item_scores, total_scores) if s < cut]
    if not groups_high or not groups_low:
        return 0.0
    sd = _std(total_scores)
    if sd == 0:
        return 0.0
    p = len(groups_high) / len(item_scores)
    return ((_mean(groups_high) - _mean(groups_low)) / sd) * (p * (1 - p)) ** 0.5


def analyse_item(
    responses: list[ItemResponse],
    mastery_by_student: dict[str, dict[str, float]],
    config: AnalyticsConfig | None = None,
) -> ItemStats:
    config = config or settings.analytics
    item_key = responses[0].item_key
    item_scores = [r.score_fraction for r in responses]
    total_scores = [r.total_fraction for r in responses]

    difficulty = sum(1 for s in item_scores if s >= 0.999) / len(item_scores)
    discrimination = point_biserial(item_scores, total_scores)

    concept_keys = responses[0].concept_keys
    alignment_scores: list[float] = []
    mastery_scores: list[float] = []
    for response in responses:
        student_mastery = mastery_by_student.get(response.student_id, {})
        relevant = [student_mastery[c] for c in concept_keys if c in student_mastery]
        if relevant:
            alignment_scores.append(response.score_fraction)
            mastery_scores.append(_mean(relevant))
    concept_alignment = pearson(alignment_scores, mastery_scores)

    flag, explanation = _classify(difficulty, discrimination, concept_alignment, len(responses), config)
    return ItemStats(item_key, len(responses), difficulty, discrimination, concept_alignment, flag, explanation)


def _classify(
    difficulty: float,
    discrimination: float,
    alignment: float,
    n: int,
    config: AnalyticsConfig,
) -> tuple[str, str]:
    if n < 5:
        return "insufficient_data", f"Only {n} response(s); statistics are not yet meaningful."

    # Ordered by how much a faculty member should care. A negative
    # discrimination outranks everything: it means the item is actively
    # inverting the signal it was supposed to provide.
    if discrimination < -config.item_discrimination_floor:
        return "anticorrelated", (
            f"Discrimination is {discrimination:.2f}. Students who scored well overall are failing this "
            "item, which almost always means an ambiguous specification or an incorrect test. "
            "Review the item before it is used again."
        )
    if difficulty > config.item_difficulty_easy:
        return "too_easy", (
            f"{difficulty:.0%} of students earned full marks. The item separates nobody and its "
            "evidence adds little to the mastery model."
        )
    if difficulty < config.item_difficulty_hard:
        return "too_hard", (
            f"Only {difficulty:.0%} of students earned full marks. Either the concept was not taught "
            "to the depth the item requires, or the item is testing something it does not state."
        )
    if abs(discrimination) < config.item_discrimination_floor:
        return "non_discriminating", (
            f"Discrimination is {discrimination:.2f}. Performance on this item is roughly independent "
            "of overall performance, so it is measuring something other than the course."
        )
    if alignment < 0.2:
        return "misaligned_concepts", (
            f"Correlation with tagged-concept mastery is {alignment:.2f}. The concept tags are probably "
            "wrong, and this item's evidence is polluting the mastery estimates for those concepts."
        )
    return "ok", (
        f"Difficulty {difficulty:.0%}, discrimination {discrimination:.2f}, concept alignment "
        f"{alignment:.2f}. The item is behaving as an assessment instrument should."
    )


def analyse_cohort(
    responses: list[ItemResponse],
    mastery_by_student: dict[str, dict[str, float]],
    config: AnalyticsConfig | None = None,
) -> list[ItemStats]:
    grouped: dict[str, list[ItemResponse]] = {}
    for response in responses:
        grouped.setdefault(response.item_key, []).append(response)
    stats = [analyse_item(group, mastery_by_student, config) for group in grouped.values()]
    # Broken items first: this list exists to be acted on, not browsed.
    severity = {
        "anticorrelated": 0, "misaligned_concepts": 1, "non_discriminating": 2,
        "too_hard": 3, "too_easy": 4, "insufficient_data": 5, "ok": 6,
    }
    stats.sort(key=lambda s: (severity.get(s.flag, 9), -abs(s.discrimination)))
    return stats


def cohort_distribution(scores: list[float], bins: int = 10) -> dict:
    """Distribution, not just the mean.

    A bimodal cohort and a uniformly mediocre one have identical averages and
    need completely different interventions (§6.5), so the shape is reported.
    """
    if not scores:
        return {"histogram": [], "mean": 0.0, "median": 0.0, "std": 0.0, "shape": "no data"}
    ordered = sorted(scores)
    histogram = [0] * bins
    for score in scores:
        index = min(bins - 1, int(max(0.0, min(1.0, score)) * bins))
        histogram[index] += 1
    median = ordered[len(ordered) // 2]
    mean = _mean(scores)
    std = _std(scores)

    # Edge bins count as peaks. A cohort split between "got nothing" and "got
    # everything" piles up in the first and last bins, which is exactly the
    # bimodal case this exists to catch, and an interior-only scan misses it.
    threshold = max(2, len(scores) * 0.1)
    padded = [0, *histogram, 0]
    peaks = [
        i for i in range(bins)
        if padded[i + 1] > padded[i] and padded[i + 1] >= padded[i + 2] and padded[i + 1] >= threshold
    ]
    if len(peaks) >= 2 and (max(peaks) - min(peaks)) >= 3:
        shape = "bimodal"
    elif std < 0.12:
        shape = "tightly clustered"
    elif mean < 0.5:
        shape = "left-skewed (cohort struggling)"
    else:
        shape = "unimodal"

    return {
        "histogram": histogram,
        "bin_edges": [round(i / bins, 2) for i in range(bins + 1)],
        "mean": round(mean, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "shape": shape,
        "interpretation": {
            "bimodal": "Two distinct populations. A single re-teach will reach one of them; consider splitting the intervention.",
            "tightly clustered": "The cohort is behaving as one group; a whole-class intervention is efficient here.",
            "left-skewed (cohort struggling)": "Most of the cohort is below the midpoint. Treat this as a teaching signal, not a student signal.",
            "unimodal": "A conventional single-peaked distribution.",
            "no data": "",
        }[shape],
    }
