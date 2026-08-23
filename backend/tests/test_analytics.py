"""Tests for Layer 2 accumulation and Layer 3 action.

The properties under test are the ones that make the analytics defensible: that
mastery responds to evidence in the right direction, that uncertainty is honest,
that failure propagates to prerequisites, that remediation points at the *root*
gap rather than the symptom, that a broken rubric item is detected, and that the
bias audit can block deployment.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.analytics import attainment, bkt, clustering, item_analysis, remediation, risk
from app.analytics import confidence as confidence_model

START = datetime(2026, 1, 5)


def obs(concept, score, day, source="test", confidence=0.9):
    return bkt.Observation(
        concept_key=concept, score_fraction=score, confidence=confidence,
        evidence_source=source, observed_at=START + timedelta(days=day),
    )


# --------------------------------------------------------------------------
# Knowledge tracing
# --------------------------------------------------------------------------
def test_mastery_rises_with_success_and_falls_with_failure():
    params = {"c_a": bkt.BKTParams()}
    good = bkt.trace_student([obs("c_a", 1.0, i) for i in range(4)], params, {})
    bad = bkt.trace_student([obs("c_a", 0.0, i) for i in range(4)], params, {})
    assert good["c_a"].mastery > 0.8
    # Failure pulls mastery well below the prior; the learn rate keeps it from
    # collapsing to zero, which is BKT working as intended rather than a floor.
    assert bad["c_a"].mastery < bkt.BKTParams().prior
    assert good["c_a"].mastery - bad["c_a"].mastery > 0.6


def test_uncertainty_falls_as_evidence_accumulates():
    params = {"c_a": bkt.BKTParams()}
    few = bkt.trace_student([obs("c_a", 1.0, 0)], params, {})
    many = bkt.trace_student([obs("c_a", 1.0, i) for i in range(8)], params, {})
    assert many["c_a"].uncertainty < few["c_a"].uncertainty


def test_weak_evidence_moves_mastery_less_than_strong_evidence():
    """A concept mark from a passing test suite is stronger evidence than one
    from structural credit on non-compiling code."""
    params = {"c_a": bkt.BKTParams()}
    from_tests = bkt.trace_student([obs("c_a", 1.0, i, "test") for i in range(3)], params, {})
    from_structure = bkt.trace_student(
        [obs("c_a", 1.0, i, "structural", confidence=0.4) for i in range(3)], params, {}
    )
    assert from_tests["c_a"].mastery > from_structure["c_a"].mastery


def test_repeated_failure_propagates_down_the_prerequisite_dag():
    """Failing tree traversal because you do not understand pointers is
    evidence about pointers."""
    prerequisites = {"c_traversal": ["c_recursion"], "c_recursion": ["c_functions"]}
    params = {"c_traversal": bkt.BKTParams()}
    states = bkt.trace_student(
        [obs("c_traversal", 0.0, i) for i in range(4)], params, prerequisites
    )
    assert "c_recursion" in states
    assert states["c_recursion"].mastery < bkt.BKTParams().prior
    assert states["c_recursion"].source == "prereq_inferred"


def test_a_single_failure_does_not_propagate():
    """One bad day is not evidence about the prerequisites."""
    prerequisites = {"c_traversal": ["c_recursion"]}
    states = bkt.trace_student([obs("c_traversal", 0.0, 0)], {"c_traversal": bkt.BKTParams()}, prerequisites)
    assert "c_recursion" not in states


def test_em_fitting_falls_back_to_priors_on_tiny_cohorts():
    cohort = {"s1": [obs("c_a", 1.0, 0)], "s2": [obs("c_a", 0.0, 0)]}
    fitted = bkt.fit_parameters(cohort, "c_a")
    assert fitted.prior == pytest.approx(bkt.BKTParams().prior)


def test_em_fitting_produces_valid_parameters_on_a_real_cohort():
    cohort = {
        f"s{i}": [obs("c_a", 1.0 if i % 2 else 0.0, d) for d in range(3)]
        for i in range(10)
    }
    fitted = bkt.fit_parameters(cohort, "c_a")
    # Slip and guess above 0.5 make the model degenerate; they must be clamped.
    assert 0 < fitted.slip < 0.5
    assert 0 < fitted.guess < 0.5
    assert 0 < fitted.prior < 1


def test_predictive_validity_reports_auc():
    states = {
        "c_a": bkt.MasteryState("c_a", 0.9, 0.1, 5),
        "c_b": bkt.MasteryState("c_b", 0.2, 0.1, 5),
    }
    later = [obs("c_a", 1.0, 10), obs("c_b", 0.0, 10)]
    result = bkt.predictive_validity(states, later)
    assert result["auc"] == 1.0


# --------------------------------------------------------------------------
# Item analysis
# --------------------------------------------------------------------------
def _responses(pairs, item="rb_01"):
    return [
        item_analysis.ItemResponse(f"s{i}", item, item_score, total, ["c_a"])
        for i, (item_score, total) in enumerate(pairs)
    ]


def test_negative_discrimination_is_flagged_as_broken():
    """Strong students failing an item almost always means an ambiguous spec or
    a wrong test — the single most valuable thing item analysis surfaces."""
    pairs = [(0.0, 0.95), (0.0, 0.90), (0.0, 0.85), (1.0, 0.30), (1.0, 0.25), (1.0, 0.20)]
    stats = item_analysis.analyse_item(_responses(pairs), {})
    assert stats.discrimination < 0
    assert stats.flag == "anticorrelated"


def test_an_item_everyone_passes_is_flagged_as_uninformative():
    pairs = [(1.0, 0.9)] * 10
    stats = item_analysis.analyse_item(_responses(pairs), {})
    assert stats.flag == "too_easy"


def test_a_healthy_item_is_not_flagged():
    pairs = [(1.0, 0.9), (1.0, 0.85), (1.0, 0.8), (0.0, 0.4), (0.0, 0.35), (1.0, 0.7), (0.0, 0.3)]
    mastery = {f"s{i}": {"c_a": score} for i, (score, _) in enumerate(pairs)}
    stats = item_analysis.analyse_item(_responses(pairs), mastery)
    assert stats.discrimination > 0
    assert stats.flag == "ok"


def test_small_samples_are_reported_as_insufficient_not_as_ok():
    stats = item_analysis.analyse_item(_responses([(1.0, 0.9), (0.0, 0.2)]), {})
    assert stats.flag == "insufficient_data"


def test_bimodal_and_uniform_cohorts_are_distinguished():
    """Identical means, completely different interventions."""
    bimodal = [0.1] * 8 + [0.9] * 8
    uniform = [0.5] * 16
    assert item_analysis.cohort_distribution(bimodal)["shape"] == "bimodal"
    assert item_analysis.cohort_distribution(uniform)["shape"] != "bimodal"


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------
def _signature(run, tests, errors, concepts=("c_a",)):
    return clustering.FailureSignature(
        run_id=run, student_id=f"s{run}", failed_tests=list(tests),
        error_types=list(errors), concept_keys=list(concepts),
        ast_shape={"loop_depth": 1.0, "branches": 2.0},
    )


def test_shared_failure_shapes_cluster_together():
    signatures = [_signature(f"r{i}", ["tc_03"], ["IndexError"]) for i in range(6)]
    signatures += [_signature(f"q{i}", ["tc_05"], ["TimeoutError"], ("c_b",)) for i in range(5)]
    clusters, _noise = clustering.cluster_failures(signatures, min_cluster_size=3)
    assert len(clusters) >= 2
    assert all(c.signature for c in clusters)


def test_clustering_is_allowed_to_return_noise():
    """A misconception library full of clusters of size one is worse than an
    empty one, so unrelated failures must not be forced into a cluster."""
    signatures = [_signature(f"r{i}", [f"tc_{i:02d}"], [f"Error{i}"], (f"c_{i}",)) for i in range(6)]
    clusters, noise = clustering.cluster_failures(signatures, min_cluster_size=4)
    assert noise


def test_too_few_signatures_produce_no_clusters():
    clusters, noise = clustering.cluster_failures([_signature("r1", ["tc_01"], ["E"])])
    assert clusters == []
    assert len(noise) == 1


# --------------------------------------------------------------------------
# Remediation
# --------------------------------------------------------------------------
def _graph():
    return {
        "c_pointers": remediation.ConceptNode("c_pointers", "Pointers", [], [
            {"kind": "practice", "title": "Pointer drills", "url": "course://p"},
        ]),
        "c_recursion": remediation.ConceptNode("c_recursion", "Recursion", ["c_pointers"]),
        "c_trees": remediation.ConceptNode("c_trees", "Trees", ["c_recursion"]),
        "c_traversal": remediation.ConceptNode("c_traversal", "Tree traversal", ["c_trees"]),
    }


def test_remediation_walks_to_the_root_gap_not_the_symptom():
    """Someone failing tree traversal because they do not understand pointers
    must be sent to pointers, not to more trees."""
    mastery = {
        "c_traversal": remediation.MasterySnapshot("c_traversal", 0.10, 0.10, 5),
        "c_trees": remediation.MasterySnapshot("c_trees", 0.35, 0.10, 5),
        "c_recursion": remediation.MasterySnapshot("c_recursion", 0.30, 0.10, 5),
        "c_pointers": remediation.MasterySnapshot("c_pointers", 0.15, 0.10, 5),
    }
    recommendations = remediation.recommend_for_student(_graph(), mastery)
    assert recommendations
    assert recommendations[0].concept_key == "c_pointers"
    assert "Pointer drills" in recommendations[0].recommended_action
    assert "c_traversal" in recommendations[0].prerequisite_path


def test_uncertain_mastery_produces_a_diagnostic_not_remediation():
    """Do not prescribe four hours of practice for something you are not sure
    the student is weak at."""
    mastery = {"c_pointers": remediation.MasterySnapshot("c_pointers", 0.40, 0.85, 1)}
    recommendations = remediation.recommend_for_student(_graph(), mastery)
    assert recommendations[0].action_kind == "diagnose"


def test_mastered_concepts_produce_no_recommendation():
    mastery = {key: remediation.MasterySnapshot(key, 0.95, 0.05, 6) for key in _graph()}
    assert remediation.recommend_for_student(_graph(), mastery) == []


def test_reteach_signals_rank_by_downstream_impact():
    """A weak concept with dependents outranks an equally weak leaf."""
    graph = _graph()
    graph["c_leaf"] = remediation.ConceptNode("c_leaf", "A leaf topic", [])
    cohort = {
        f"s{i}": {
            "c_pointers": remediation.MasterySnapshot("c_pointers", 0.30, 0.1, 5),
            "c_leaf": remediation.MasterySnapshot("c_leaf", 0.30, 0.1, 5),
        }
        for i in range(8)
    }
    signals = remediation.reteach_signals(graph, cohort)
    assert signals[0].concept_key == "c_pointers"
    assert signals[0].downstream_dependents > 0


def test_downstream_impact_counts_transitive_dependents():
    assert remediation.downstream_impact("c_pointers", _graph()) == 3
    assert remediation.downstream_impact("c_traversal", _graph()) == 0


# --------------------------------------------------------------------------
# Risk and bias
# --------------------------------------------------------------------------
def test_a_flagged_student_must_carry_contributing_factors():
    """A bare risk score is unusable and unfair, so it is not representable."""
    with pytest.raises(ValueError, match="contributing factors"):
        risk.RiskAssessment("s1", 0.9, True, [])


def test_risk_cannot_be_routed_to_a_sanction():
    with pytest.raises(ValueError, match="support"):
        risk.RiskAssessment(
            "s1", 0.9, True,
            [risk.RiskFactor("Low mastery", 0.3, "detail")],
            routed_to="disciplinary_panel",
        )


def test_declining_trajectory_raises_risk_above_a_flat_one():
    prerequisites = {"c_a": []}
    flat = [{"estimate": 0.5} for _ in range(6)]
    declining = [{"estimate": v} for v in (0.8, 0.7, 0.6, 0.5, 0.4, 0.3)]
    behaviour = risk.BehaviourFeatures()
    flat_assessment = risk.assess_student("s1", {"c_a": 0.5}, {"c_a": flat}, prerequisites, behaviour)
    falling = risk.assess_student("s2", {"c_a": 0.5}, {"c_a": declining}, prerequisites, behaviour)
    assert falling.risk_score > flat_assessment.risk_score


def test_prerequisite_gap_depth_measures_the_chain():
    mastery = {"c_traversal": 0.1, "c_trees": 0.1, "c_recursion": 0.1, "c_pointers": 0.1}
    prerequisites = {
        "c_traversal": ["c_trees"], "c_trees": ["c_recursion"],
        "c_recursion": ["c_pointers"], "c_pointers": [],
    }
    assert risk.prerequisite_gap_depth(mastery, prerequisites, 0.7) == 4


def test_bias_audit_blocks_deployment_on_disparate_flag_rates():
    flagged = [
        risk.RiskAssessment(f"a{i}", 0.9, True, [risk.RiskFactor("Low mastery", 0.4, "d")])
        for i in range(6)
    ]
    clear = [risk.RiskAssessment(f"b{i}", 0.1, False, []) for i in range(6)]
    attributes = {a.student_id: {"group": "X"} for a in flagged}
    attributes.update({a.student_id: {"group": "Y"} for a in clear})
    audit = risk.bias_audit(flagged + clear, attributes)
    assert not audit.passed
    assert audit.deployment_blocked


def test_bias_audit_without_comparable_groups_is_inconclusive_not_passing():
    assessments = [risk.RiskAssessment(f"a{i}", 0.1, False, []) for i in range(4)]
    audit = risk.bias_audit(assessments, {a.student_id: {"group": "X"} for a in assessments})
    assert not audit.passed
    assert "inconclusive" in audit.note.lower()


# --------------------------------------------------------------------------
# Attainment
# --------------------------------------------------------------------------
def test_co_attainment_rolls_up_from_concept_mastery():
    outcomes = [attainment.OutcomeDefinition("CO1", "Write correct programs", ["PO1"], {"PO1": 3})]
    mapping = {"c_a": ["CO1"], "c_b": ["CO1"], "c_other": ["CO2"]}
    mastery = {f"s{i}": {"c_a": 0.9, "c_b": 0.8, "c_other": 0.1} for i in range(10)}
    results = attainment.compute_co_attainment(outcomes, mapping, mastery)
    assert results[0].code == "CO1"
    assert results[0].attainment_fraction == 1.0
    assert results[0].level == 3
    assert sorted(results[0].concept_keys) == ["c_a", "c_b"]


def test_an_outcome_with_no_mapped_concepts_reports_level_zero():
    outcomes = [attainment.OutcomeDefinition("CO9", "Unmapped outcome")]
    results = attainment.compute_co_attainment(outcomes, {"c_a": ["CO1"]}, {"s1": {"c_a": 0.9}})
    assert results[0].level == 0
    assert "no concepts mapped" in results[0].level_label


def test_po_attainment_respects_correlation_weights():
    co = [
        attainment.COAttainment("CO1", "", ["c_a"], 0.9, 10, 10, 1.0, 3, "L3", 10),
        attainment.COAttainment("CO2", "", ["c_b"], 0.2, 0, 10, 0.0, 0, "L0", 10),
    ]
    outcomes = [
        attainment.OutcomeDefinition("CO1", "", ["PO1"], {"PO1": 3}),
        attainment.OutcomeDefinition("CO2", "", ["PO1"], {"PO1": 1}),
    ]
    results = attainment.compute_po_attainment(co, outcomes)
    # 3:1 weighting pulls the result toward the well-attained outcome.
    assert results[0].weighted_attainment == pytest.approx(0.75)


# --------------------------------------------------------------------------
# Confidence estimator
# --------------------------------------------------------------------------
def test_confidence_rises_with_agreement_and_falls_with_contradiction():
    base = {
        "signal_agreement": 1.0, "evidence_completeness": 1.0, "boundary_distance": 1.0,
        "test_pass_rate": 1.0, "test_coverage": 1.0, "repair_distance_norm": 0.0,
        "similarity_max": 0.0, "entailment_contradiction_rate": 0.0,
        "stage_error": 0.0, "static_check_rate": 1.0,
    }
    conflicted = {**base, "signal_agreement": 0.1}
    contradicted = {**base, "entailment_contradiction_rate": 1.0}
    assert confidence_model.predict(base) > confidence_model.predict(conflicted)
    assert confidence_model.predict(base) > confidence_model.predict(contradicted)


def test_confidence_model_keeps_priors_on_tiny_training_sets():
    model = confidence_model.fit([({"signal_agreement": 1.0}, 1.0)] * 3)
    assert model.weights == confidence_model.PRIOR_WEIGHTS
    assert model.n_training_examples == 3


def test_confidence_model_trains_on_enough_examples():
    examples = []
    for i in range(30):
        agreement = i / 30
        examples.append((
            {
                "signal_agreement": agreement, "evidence_completeness": 1.0,
                "boundary_distance": 1.0, "test_pass_rate": agreement, "test_coverage": 1.0,
                "repair_distance_norm": 0.0, "similarity_max": 0.0,
                "entailment_contradiction_rate": 0.0, "stage_error": 0.0,
                "static_check_rate": 1.0,
            },
            agreement,
        ))
    model = confidence_model.fit(examples)
    assert model.n_training_examples == 30
    assert model.holdout_mae is not None


def test_confidence_explanation_is_ranked_by_contribution():
    contributions = confidence_model.explain({"signal_agreement": 1.0, "stage_error": 1.0})
    assert abs(contributions[0]["contribution"]) >= abs(contributions[-1]["contribution"])
