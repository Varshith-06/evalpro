"""The evaluation cascade: B0 -> B7, ordered by cost ascending, with early exits.

Each stage writes structured evidence; **none writes a score directly**. Scores
appear only in B7, derived from the evidence trail, which is what makes a grade
interrogable rather than merely defensible.

The other thing this module does, and the reason the platform is not a grader:
every released rubric-item score is written out as a set of *concept
observations*. One submission becomes evidence about named competencies that
accumulate across the whole course.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import confidence as confidence_model
from ..config import MODEL_VERSIONS, PIPELINE_VERSION, settings
from ..models import (
    Assignment,
    AssignmentVersion,
    Concept,
    ConceptObservation,
    EvaluationRun,
    RubricItem,
    RubricItemScore,
    SimilarityPair,
    StageName,
    StageResult,
    StageStatus,
    SubmissionAttempt,
    TestCase,
    TestOutcome,
    TestResult,
    Verdict,
    VerdictState,
)
from . import b2_integrity, b3_build, b4_execute, b5_partial, b6_report, b7_gate
from .b0_ingest import ingest_files
from .b1_structure import build_code_graph
from .sandbox import DEFAULT_SANDBOX, Sandbox, describe_isolation, static_pre_screen


@dataclass
class StageRecord:
    stage: StageName
    status: StageStatus
    summary: str
    evidence: dict = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class PipelineOutcome:
    run: EvaluationRun
    decision: b7_gate.GateDecision
    items: list[b7_gate.ItemAggregate]
    stages: list[StageRecord]
    review_priority: float = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _concatenate(files: dict[str, str]) -> str:
    return "\n\n".join(files[path] for path in sorted(files))


class EvaluationPipeline:
    """Runs one submission attempt end to end."""

    def __init__(self, session: Session, sandbox: Sandbox | None = None) -> None:
        self.session = session
        self.sandbox = sandbox or DEFAULT_SANDBOX

    # ------------------------------------------------------------------
    def run(
        self,
        attempt: SubmissionAttempt,
        version: AssignmentVersion,
        visible_only: bool = False,
        cohort_corpus: list[dict] | None = None,
    ) -> PipelineOutcome:
        started = time.perf_counter()
        run = EvaluationRun(
            attempt_id=attempt.id,
            version_id=version.id,
            pipeline_version=PIPELINE_VERSION,
            model_versions=dict(MODEL_VERSIONS),
            started_at=_now(),
            visible_only=visible_only,
        )
        self.session.add(run)
        self.session.flush()

        stages: list[StageRecord] = []
        # Approach-graded assignments have no reference solution, so no test was
        # ever admitted and none will run. Several stages behave differently,
        # and the evidence has to say so rather than looking like a failure.
        static_mode = (version.grading_mode or "executable") != "executable"
        context: dict = {
            "stage_error": None,
            "grading_mode": "static" if static_mode else "executable",
            "has_executable_oracle": 0.0 if static_mode else 1.0,
        }

        # -- B0 ---------------------------------------------------------
        t0 = time.perf_counter()
        ingested = ingest_files(attempt.files, attempt.report_text)
        stages.append(
            StageRecord(
                StageName.INGEST,
                StageStatus.OK,
                ingested.summary(),
                {
                    "content_hash": ingested.content_hash,
                    "accepted": sorted(ingested.files),
                    "rejected": ingested.rejected,
                    "warnings": ingested.warnings,
                    "total_bytes": ingested.total_bytes,
                    "note": (
                        "Identical content hash returns the cached result: free idempotency "
                        "and free protection against deadline submit-spam."
                    ),
                },
                int((time.perf_counter() - t0) * 1000),
            )
        )
        files = ingested.files
        source_blob = _concatenate(files)

        # -- B1 ---------------------------------------------------------
        t0 = time.perf_counter()
        graph = build_code_graph(files, version.entry_point)
        run.code_graph = graph.to_dict()
        structure_status = StageStatus.OK if graph.parsed else StageStatus.WARN
        stages.append(
            StageRecord(
                StageName.STRUCTURE,
                structure_status,
                (
                    f"{graph.language}: {len(graph.functions)} function(s), "
                    f"{len(graph.classes)} class(es), {graph.loc} LOC"
                    + (" (partial tree recovered from a syntax error)" if graph.partial else "")
                ),
                {
                    "entry_point": graph.entry_point,
                    "functions": list(graph.functions),
                    "call_graph": graph.call_graph,
                    "data_structures": graph.data_structures,
                    "syntax_errors": graph.syntax_errors,
                    "partial_parse": graph.partial,
                },
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- B2 ---------------------------------------------------------
        t0 = time.perf_counter()
        similarity_max, similarity_stage = self._integrity(
            run, attempt, graph, source_blob, cohort_corpus or [], version
        )
        context["similarity_max"] = similarity_max
        stages.append(similarity_stage)
        stages[-1].duration_ms = int((time.perf_counter() - t0) * 1000)

        # -- B3 ---------------------------------------------------------
        t0 = time.perf_counter()
        build_result = b3_build.build(files, graph, self.sandbox)
        stages.append(
            StageRecord(
                StageName.BUILD,
                StageStatus.OK if build_result.ok else StageStatus.WARN,
                build_result.summary(),
                {"diagnostics": build_result.diagnostics, "toolchain": build_result.toolchain},
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- B5a (runs before B4 when the build failed) -----------------
        repair = b5_partial.RepairResult(repaired=True, edit_distance=0, note="compiles as submitted")
        execution_files = files
        execution_graph = graph
        if not build_result.ok:
            t0 = time.perf_counter()
            repair = b5_partial.repair_bundle(files, set(graph.symbols))
            if repair.repaired and repair.repaired_files:
                execution_files = repair.repaired_files
                execution_graph = build_code_graph(execution_files, version.entry_point)
            stages.append(
                StageRecord(
                    StageName.PARTIAL_CREDIT,
                    StageStatus.OK if repair.repaired else StageStatus.WARN,
                    (
                        f"repair distance {repair.edit_distance}: {repair.note}"
                        if repair.repaired
                        else f"no repair found: {repair.note}"
                    ),
                    {
                        "repaired": repair.repaired,
                        "edit_distance": repair.edit_distance,
                        "edits": repair.edits,
                        "policy": (
                            "A repaired program that compiles and passes tests earns the test score "
                            "with a syntax penalty proportional to the edit distance. A missing "
                            "delimiter costs a small number of marks, not all of them."
                        ),
                    },
                    int((time.perf_counter() - t0) * 1000),
                )
            )
        context["repair_distance"] = repair.edit_distance
        syntax_penalty = repair.penalty_fraction() if repair.edit_distance else 0.0
        context["syntax_penalty"] = syntax_penalty

        # -- B4 ---------------------------------------------------------
        t0 = time.perf_counter()
        tests = list(
            self.session.scalars(
                select(TestCase)
                .where(TestCase.version_id == version.id)
                .order_by(TestCase.ordinal)
            )
        )
        admitted = [t for t in tests if t.validated_against_reference]
        executions: list[b4_execute.TestExecution] = []
        if admitted and (build_result.ok or repair.repaired):
            executions = b4_execute.execute_tests(
                execution_files,
                execution_graph.entry_point or version.entry_point,
                admitted,
                self.sandbox,
                seed_parts=(version.assignment_id, attempt.id),
                visible_only=visible_only,
                on_repaired_source=not build_result.ok,
            )
        ran = [e for e in executions if e.outcome != TestOutcome.SKIPPED]
        passed = [e for e in ran if e.passed]
        pass_rate = len(passed) / len(ran) if ran else 0.0
        context["test_pass_rate"] = pass_rate
        context["grading_mode"] = static_mode and "static" or "executable"
        context["has_executable_oracle"] = 0.0 if static_mode else 1.0
        self._persist_tests(run, executions)
        if ran:
            execute_summary = (
                f"{len(passed)}/{len(ran)} test(s) passed"
                + (" on repaired source" if not build_result.ok else "")
            )
        elif static_mode:
            execute_summary = (
                "skipped - this assignment has no reference solution, so no oracle exists and no "
                "test was ever admitted"
            )
        else:
            execute_summary = "no executable tests (build failed and no repair found)"

        stages.append(
            StageRecord(
                StageName.EXECUTE,
                StageStatus.OK if ran else StageStatus.SKIPPED,
                execute_summary,
                {
                    "pass_rate": round(pass_rate, 4),
                    "grading_mode": context["grading_mode"],
                    "oracle_location": (
                        "none - approach-graded assignment"
                        if static_mode
                        else "host - expected outputs never entered the sandbox"
                    ),
                    "isolation": describe_isolation()["applied_count"],
                    "results": [
                        {
                            "test_key": e.test_key,
                            "category": e.category,
                            "outcome": e.outcome.value,
                            "hidden": e.hidden,
                            "reason": e.reason,
                            "cpu_ms": e.cpu_ms,
                        }
                        for e in executions
                    ],
                },
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- B5b/B5c ----------------------------------------------------
        t0 = time.perf_counter()
        structural = b5_partial.structural_credit(
            source_blob,
            execution_graph,
            version.reference_solution,
            expected_algorithm=self._expected_algorithm(version),
        )
        pre_screen = static_pre_screen(files)
        if static_mode:
            structural_summary = (
                f"{len(structural.algorithm_matches)} algorithm class(es) identified "
                "(no reference solution to compare against)"
            )
        else:
            structural_summary = (
                f"structural similarity to reference {structural.similarity_to_reference:.0%}; "
                f"{len(structural.algorithm_matches)} algorithm class(es) identified"
            )
        stages.append(
            StageRecord(
                StageName.PARTIAL_CREDIT,
                StageStatus.OK,
                structural_summary,
                {
                    "similarity_to_reference": round(structural.similarity_to_reference, 4),
                    "algorithm_matches": structural.algorithm_matches,
                    "evidence": structural.evidence,
                    "static_pre_screen": pre_screen[:20],
                    "static_pre_screen_note": "Layer 0 is advisory: it informs the gate, never blocks alone.",
                },
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- B6 ---------------------------------------------------------
        t0 = time.perf_counter()
        rubric_items = list(
            self.session.scalars(
                select(RubricItem)
                .where(RubricItem.version_id == version.id)
                .order_by(RubricItem.ordinal)
            )
        )
        report_result = b6_report.check_report(
            attempt.report_text, execution_graph, source_blob, rubric_items
        )
        total_claims = len(report_result.entailments) or 1
        context["contradictions"] = report_result.contradictions
        context["contradiction_rate"] = report_result.contradictions / total_claims
        stages.append(
            StageRecord(
                StageName.REPORT_CHECK,
                StageStatus.WARN if report_result.contradictions else StageStatus.OK,
                (
                    f"{report_result.entailed} entailed, {report_result.contradictions} contradicted, "
                    f"{report_result.unsupported} unsupported; rubric coverage "
                    f"{report_result.coverage_fraction:.0%}"
                    if report_result.has_report
                    else "no report submitted"
                ),
                report_result.as_evidence(),
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- B7 ---------------------------------------------------------
        t0 = time.perf_counter()
        items = self._aggregate_items(
            rubric_items, executions, execution_graph, structural, report_result,
            context, repair, source_blob,
        )
        static_rate = _static_check_rate(items)
        context["static_check_rate"] = static_rate
        for item in items:
            # static_check_rate is a run-level feature, so item confidences are
            # re-scored once every item has contributed to it.
            item.features["static_check_rate"] = static_rate
            item.confidence = round(confidence_model.predict(item.features), 4)
        decision = b7_gate.decide(items, context)
        priority = b7_gate.review_priority(items, decision)
        stages.append(
            StageRecord(
                StageName.GATE,
                StageStatus.OK,
                (
                    f"{decision.state.value}: {decision.total_fraction:.1%} "
                    f"(confidence {decision.confidence:.2f})"
                ),
                {**decision.as_dict(), "review_priority": priority},
                int((time.perf_counter() - t0) * 1000),
            )
        )

        # -- persist ----------------------------------------------------
        self._persist_items(run, items)
        verdict = Verdict(
            run_id=run.id,
            total_fraction=decision.total_fraction,
            total_points=decision.total_points,
            max_points=decision.max_points,
            confidence=decision.confidence,
            state=decision.state,
            escalation_reasons=decision.escalation_reasons,
            integrity_flag=decision.integrity_flag,
            syntax_penalty=decision.syntax_penalty,
            released_at=_now() if decision.state == VerdictState.RELEASED else None,
        )
        self.session.add(verdict)
        self._persist_stages(run, stages)

        run.finished_at = _now()
        run.duration_ms = int((time.perf_counter() - started) * 1000)
        self.session.flush()

        # -- L1 -> L2 ---------------------------------------------------
        if decision.state == VerdictState.RELEASED and not visible_only:
            self.emit_concept_observations(run, items)

        return PipelineOutcome(run=run, decision=decision, items=items, stages=stages, review_priority=priority)

    # ------------------------------------------------------------------
    # Stage helpers
    # ------------------------------------------------------------------
    def _expected_algorithm(self, version: AssignmentVersion) -> str | None:
        spec = (version.spec_text or "").lower()
        for name in b5_partial.ALGORITHM_SIGNATURES:
            if name.replace("_", " ") in spec:
                return name
        if "binary search" in spec:
            return "binary_search"
        if "merge sort" in spec or "divide" in spec:
            return "divide_and_conquer_sort"
        return None

    def _integrity(
        self,
        run: EvaluationRun,
        attempt: SubmissionAttempt,
        graph,
        source_blob: str,
        corpus: list[dict],
        version: AssignmentVersion,
    ) -> tuple[float, StageRecord]:
        if not corpus:
            return 0.0, StageRecord(
                StageName.INTEGRITY,
                StageStatus.SKIPPED,
                "no comparison corpus available for this assignment yet",
                {"note": "The first submission of an assignment has nothing to be compared against."},
            )

        excluded = b2_integrity.excluded_fingerprints([version.reference_solution], corpus)
        screen = b2_integrity.screen_against_corpus(source_blob, graph, corpus, excluded)
        assignment_id = self.session.get(SubmissionAttempt, attempt.id).submission.assignment_id
        student_id = self.session.get(SubmissionAttempt, attempt.id).submission.student_id

        # Only outlier pairs are persisted to the integrity dashboard. Recording
        # every above-median pair would bury the one that matters.
        for entry in (screen.ranked if screen.outlier else []):
            if entry["combined"] < b2_integrity.ABSOLUTE_FLOOR:
                continue
            self.session.add(
                SimilarityPair(
                    assignment_id=assignment_id,
                    run_id_a=run.id,
                    run_id_b=entry["against_id"],
                    student_id_a=student_id,
                    student_id_b=entry.get("against_student_id") or "",
                    token_similarity=entry["token_similarity"],
                    structural_similarity=entry["structural_similarity"],
                    combined=entry["combined"],
                    aligned_regions=entry["aligned_regions"],
                    corpus=entry.get("corpus", "cohort"),
                )
            )

        return screen.flag_score, StageRecord(
            StageName.INTEGRITY,
            StageStatus.WARN if screen.outlier else StageStatus.OK,
            (
                f"top similarity {screen.top:.0%} against {len(corpus)} comparison document(s); "
                + ("reported as a cohort outlier" if screen.outlier else "not an outlier - no report raised")
            ),
            {
                **screen.as_dict(),
                "excluded_fingerprints": len(excluded),
                "exclusion_note": (
                    "Base code from the reference solution and idioms shared by more than 40% of the "
                    "cohort are removed before comparison. Forty students writing the same inner loop "
                    "is evidence about the exercise, not about any student."
                ),
                "policy": (
                    "Ranked similarity with aligned regions, never a verdict. Automated integrity "
                    "accusations are a legal and ethical liability; faculty decide."
                ),
            },
        )

    # ------------------------------------------------------------------
    def _aggregate_items(
        self,
        rubric_items: list[RubricItem],
        executions: list[b4_execute.TestExecution],
        graph,
        structural: b5_partial.StructuralCredit,
        report_result: b6_report.ReportCheckResult,
        context: dict,
        repair: b5_partial.RepairResult,
        source_blob: str = "",
    ) -> list[b7_gate.ItemAggregate]:
        by_test = {e.test_key: e for e in executions}
        coverage_by_item = {c["item_key"]: c for c in report_result.coverage}
        aggregates: list[b7_gate.ItemAggregate] = []

        for item in rubric_items:
            signals: list[b7_gate.Signal] = []
            evidence: list[str] = []

            # --- test signal -------------------------------------------
            item_tests = [by_test[k] for k in item.test_ids if k in by_test]
            ran = [e for e in item_tests if e.outcome != TestOutcome.SKIPPED]
            if ran:
                weight_total = sum(e.weight for e in ran) or 1.0
                score = sum(e.weight for e in ran if e.passed) / weight_total
                source = "repair" if any(e.on_repaired_source for e in ran) else "test"
                signals.append(
                    b7_gate.Signal(
                        source,
                        score,
                        b7_gate.SOURCE_RELIABILITY[source],
                        f"{sum(1 for e in ran if e.passed)}/{len(ran)} tests passed",
                    )
                )
                for execution in ran:
                    if execution.passed:
                        evidence.append(f"{execution.test_key} ({execution.category}) passed.")
                    else:
                        detail = execution.reason or f"expected {execution.expected[:80]}, got {execution.actual[:80]}"
                        evidence.append(f"{execution.test_key} ({execution.category}) {execution.outcome.value}: {detail}")
            elif item.test_ids:
                evidence.append(
                    "Tests for this item were not executed"
                    + (" (hidden set withheld from pre-deadline feedback)." if any(
                        by_test.get(k) and by_test[k].hidden for k in item.test_ids
                    ) else " because the submission could not be built or repaired.")
                )

            # --- static signal -----------------------------------------
            if item.static_check:
                result = b5_partial.run_static_check(graph, item.static_check, source_blob)
                source = "static_advisory" if result.advisory else "static"
                signals.append(
                    b7_gate.Signal(
                        source,
                        1.0 if result.passed else 0.0,
                        b7_gate.SOURCE_RELIABILITY[source],
                        result.kind + (" (advisory)" if result.advisory else ""),
                    )
                )
                evidence.append(result.detail)

            # --- structural signal --------------------------------------
            # Structural credit applies when the item asks for it, or when it
            # asked for tests that could not be run - the B5b case where code
            # that is structurally the right algorithm earns comprehension
            # marks at a 0% pass rate. It is not a filler signal for items that
            # are checked some other way.
            wants_structural = "structural" in (item.checkable_by or [])
            tests_expected_but_absent = bool(item.test_ids) and not ran
            # Where an exact static query already answers the item -- is there a
            # guard, is it recursive, was the forbidden API used -- a similarity
            # heuristic can only dilute a definitive answer, so it is not added.
            decided_by_static = any(s.source == "static" for s in signals)
            if (wants_structural or (tests_expected_but_absent and item.auto_gradeable)) and not decided_by_static:
                signals.append(
                    b7_gate.Signal(
                        "structural",
                        structural.fraction,
                        b7_gate.SOURCE_RELIABILITY["structural"],
                        "algorithm-comprehension credit from AST/CFG comparison",
                    )
                )
                evidence.extend(structural.evidence)

            # --- report signal -------------------------------------------
            if "report" in (item.checkable_by or []) and report_result.has_report:
                coverage = coverage_by_item.get(item.item_key)
                if coverage:
                    signals.append(
                        b7_gate.Signal(
                            "report",
                            1.0 if coverage["covered"] else coverage["score"],
                            b7_gate.SOURCE_RELIABILITY["report"],
                            "rubric coverage by the report",
                        )
                    )
                    evidence.append(
                        f"Report coverage {coverage['score']:.0%}: "
                        + (coverage["evidence_sentence"] or "no matching sentence found.")
                    )

            if not signals:
                signals.append(
                    b7_gate.Signal("structural", 0.0, 0.3, "no evidence could be produced for this item")
                )
                evidence.append("No signal produced evidence for this item; routed for manual review.")

            item_context = {
                **context,
                "declared_checks": item.checkable_by or [],
            }
            aggregates.append(
                b7_gate.aggregate_item(
                    item.item_key,
                    item.concept_ids or [],
                    item.weight,
                    signals,
                    evidence,
                    item_context,
                )
            )
        return aggregates

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _persist_stages(self, run: EvaluationRun, stages: list[StageRecord]) -> None:
        for ordinal, record in enumerate(stages):
            self.session.add(
                StageResult(
                    run_id=run.id,
                    ordinal=ordinal,
                    stage=record.stage,
                    status=record.status,
                    duration_ms=record.duration_ms,
                    summary=record.summary,
                    evidence=record.evidence,
                )
            )

    def _persist_tests(self, run: EvaluationRun, executions: list[b4_execute.TestExecution]) -> None:
        from ..models import TestCategory

        for execution in executions:
            try:
                category = TestCategory(execution.category)
            except ValueError:
                category = TestCategory.BASIC
            self.session.add(
                TestResult(
                    run_id=run.id,
                    test_key=execution.test_key,
                    category=category,
                    outcome=execution.outcome,
                    hidden=execution.hidden,
                    on_repaired_source=execution.on_repaired_source,
                    actual=execution.actual[:4000],
                    expected=execution.expected[:4000],
                    diff=execution.diff[:4000],
                    cpu_ms=execution.cpu_ms,
                    wall_ms=execution.wall_ms,
                    peak_memory_kb=execution.peak_memory_kb,
                    exit_code=execution.exit_code,
                    stderr_excerpt=execution.stderr_excerpt[:2000],
                )
            )

    def _persist_items(self, run: EvaluationRun, items: list[b7_gate.ItemAggregate]) -> None:
        for item in items:
            self.session.add(
                RubricItemScore(
                    run_id=run.id,
                    item_key=item.item_key,
                    concept_ids=item.concept_ids,
                    weight=item.weight,
                    score_fraction=item.score_fraction,
                    confidence=item.confidence,
                    signals=[s.as_dict() for s in item.signals],
                    signal_agreement=item.signal_agreement,
                    evidence=item.evidence,
                )
            )

    # ------------------------------------------------------------------
    def emit_concept_observations(
        self, run: EvaluationRun, items: list[b7_gate.ItemAggregate]
    ) -> int:
        """L1 -> L2. The step that makes this an analytics platform.

        Only released or faculty-confirmed evidence gets here: mastery built on
        results the system itself flagged as uncertain is worse than no mastery
        model at all.
        """
        attempt = self.session.get(SubmissionAttempt, run.attempt_id)
        submission = attempt.submission
        assignment = self.session.get(Assignment, submission.assignment_id)
        written = 0
        for item in items:
            primary_source = max(item.signals, key=lambda s: s.reliability).source if item.signals else "none"
            for concept_key in item.concept_ids:
                self.session.add(
                    ConceptObservation(
                        course_id=assignment.course_id,
                        student_id=submission.student_id,
                        concept_key=concept_key,
                        run_id=run.id,
                        assignment_id=assignment.id,
                        item_key=item.item_key,
                        score_fraction=item.score_fraction,
                        confidence=item.confidence,
                        evidence_source=primary_source,
                        observed_at=run.finished_at or _now(),
                    )
                )
                written += 1
        return written


def _static_check_rate(items: list[b7_gate.ItemAggregate]) -> float:
    static_signals = [s for item in items for s in item.signals if s.source == "static"]
    if not static_signals:
        return 0.0
    return sum(s.score for s in static_signals) / len(static_signals)


def build_cohort_corpus(session: Session, assignment_id: str, exclude_run_id: str | None = None) -> list[dict]:
    """Comparison corpus for B2: this cohort, plus whatever prior-year and
    public-solution documents have been indexed for the assignment."""
    from ..models import Submission

    corpus: list[dict] = []
    runs = session.scalars(
        select(EvaluationRun)
        .join(SubmissionAttempt, SubmissionAttempt.id == EvaluationRun.attempt_id)
        .join(Submission, Submission.id == SubmissionAttempt.submission_id)
        .where(Submission.assignment_id == assignment_id)
    ).all()
    for other in runs:
        if exclude_run_id and other.id == exclude_run_id:
            continue
        attempt = session.get(SubmissionAttempt, other.attempt_id)
        if attempt is None or not attempt.files:
            continue
        graph = build_code_graph(attempt.files)
        corpus.append(
            {
                "id": other.id,
                "student_id": attempt.submission.student_id,
                "source": _concatenate(attempt.files),
                "graph": graph,
                "corpus": "cohort",
            }
        )
    return corpus


def concept_keys_for_course(session: Session, course_id: str) -> list[str]:
    return list(
        session.scalars(select(Concept.concept_key).where(Concept.course_id == course_id))
    )
