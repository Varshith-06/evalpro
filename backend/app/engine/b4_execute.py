"""B4 - Execute and test.

Each test runs in a fresh one-shot sandbox instance.

**The critical design decision: expected outputs never enter the sandbox.**
The in-guest harness invokes student code and emits the *actual* value to a
structured channel; comparison happens here, on the host. Student code cannot
read, infer, or hardcode an oracle it was never given.

Two further properties fall out of that:

* **Property-based tests beat fixed IO pairs.** ``is_ascending(f(x)) and
  multiset_equal(f(x), x)`` over random lists is worth twenty hardcoded arrays
  and cannot be defeated by memorising outputs. Inputs are seeded from
  ``(assignment_id, attempt_id)`` -- reproducible for a regrade, unpredictable
  for the student.
* **A timeout scores as a failure with an explicit reason, never as partial
  credit.** Otherwise ``sleep()`` becomes a scoring strategy.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import SandboxLimits, settings
from ..models import TestCategory, TestOutcome
from .sandbox import Sandbox, SandboxJob, SandboxResult


@dataclass
class TestExecution:
    test_key: str
    category: str
    outcome: TestOutcome
    hidden: bool
    actual: str = ""
    expected: str = ""
    diff: str = ""
    reason: str = ""
    cpu_ms: int = 0
    wall_ms: int = 0
    peak_memory_kb: int = 0
    exit_code: int | None = None
    stderr_excerpt: str = ""
    weight: float = 1.0
    property_failures: list[str] = field(default_factory=list)
    on_repaired_source: bool = False

    @property
    def passed(self) -> bool:
        return self.outcome == TestOutcome.PASS


# --------------------------------------------------------------------------
# Seeded input generation (host side)
# --------------------------------------------------------------------------
def derive_seed(*parts: str) -> int:
    """Reproducible for regrading, unpredictable for the student.

    The student never sees the seed, and it is a pure function of identifiers
    they cannot choose, so the same submission regraded next year draws the
    same inputs.
    """
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def generate_input(spec: dict, rng: random.Random) -> Any:
    """Declarative generators. Adding a kind here is how a new property test
    becomes available to the authoring stage without new code paths."""
    kind = spec.get("kind", "int_list")
    if kind == "int_list":
        n = rng.randint(*spec.get("n", [0, 40]))
        lo, hi = spec.get("lo", -100), spec.get("hi", 100)
        return [rng.randint(lo, hi) for _ in range(n)]
    if kind == "sorted_int_list":
        n = rng.randint(*spec.get("n", [0, 40]))
        lo, hi = spec.get("lo", -100), spec.get("hi", 100)
        return sorted(rng.randint(lo, hi) for _ in range(n))
    if kind == "unique_int_list":
        n = rng.randint(*spec.get("n", [0, 30]))
        lo, hi = spec.get("lo", -200), spec.get("hi", 200)
        return rng.sample(range(lo, hi), min(n, hi - lo))
    if kind == "int":
        return rng.randint(*spec.get("range", [0, 100]))
    if kind == "string":
        alphabet = spec.get("alphabet", "abcdefghijklmnopqrstuvwxyz")
        n = rng.randint(*spec.get("n", [0, 24]))
        return "".join(rng.choice(alphabet) for _ in range(n))
    if kind == "matrix":
        rows = rng.randint(*spec.get("rows", [1, 6]))
        cols = rng.randint(*spec.get("cols", [1, 6]))
        return [[rng.randint(-20, 20) for _ in range(cols)] for _ in range(rows)]
    if kind == "sorted_list_and_target":
        n = rng.randint(*spec.get("n", [0, 40]))
        lo, hi = spec.get("lo", -60), spec.get("hi", 60)
        haystack = sorted(rng.randint(lo, hi) for _ in range(n))
        # Half the cases target a value that is present and half one that may
        # not be: an implementation that only ever returns an index passes the
        # first kind and fails the second, which is exactly the bug worth catching.
        if haystack and rng.random() < 0.5:
            target = rng.choice(haystack)
        else:
            target = rng.randint(lo, hi)
        return [haystack, target]
    if kind == "nested_list":
        return _nested_list(rng, spec.get("max_depth", 4), spec.get("max_width", 4))
    if kind == "literal":
        return spec.get("value")
    raise ValueError(f"unknown generator kind: {kind}")


def _nested_list(rng: random.Random, max_depth: int, max_width: int, depth: int = 0) -> list:
    width = rng.randint(0, max_width)
    out: list = []
    for _ in range(width):
        if depth < max_depth - 1 and rng.random() < 0.45:
            out.append(_nested_list(rng, max_depth, max_width, depth + 1))
        else:
            out.append(rng.randint(-50, 50))
    return out


def _nesting_depth(value) -> int:
    if not isinstance(value, list):
        return 0
    if not value:
        return 1
    return 1 + max(_nesting_depth(v) for v in value)


# --------------------------------------------------------------------------
# Property predicates (host side - the oracle lives here)
# --------------------------------------------------------------------------
def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


PREDICATES: dict[str, Callable[[Any, Any], bool]] = {
    "is_ascending": lambda inp, out: all(
        a <= b for a, b in zip(_as_list(out), _as_list(out)[1:])
    ),
    "is_descending": lambda inp, out: all(
        a >= b for a, b in zip(_as_list(out), _as_list(out)[1:])
    ),
    "multiset_equal_to_input": lambda inp, out: sorted(_as_list(inp), key=repr)
    == sorted(_as_list(out), key=repr),
    "same_length_as_input": lambda inp, out: len(_as_list(out)) == len(_as_list(inp)),
    "subset_of_input": lambda inp, out: set(map(repr, _as_list(out)))
    <= set(map(repr, _as_list(inp))),
    "no_duplicates": lambda inp, out: len(_as_list(out)) == len(set(map(repr, _as_list(out)))),
    "is_not_none": lambda inp, out: out is not None,
    "is_bool": lambda inp, out: isinstance(out, bool),
    "is_int": lambda inp, out: isinstance(out, int) and not isinstance(out, bool),
    "non_negative": lambda inp, out: isinstance(out, (int, float)) and out >= 0,
    "index_in_range": lambda inp, out: isinstance(out, int) and -1 <= out < len(_as_list(inp)),
    "empty_in_empty_out": lambda inp, out: bool(_as_list(inp)) or not _as_list(out),
    # (haystack, target) -> index, or -1 when absent. The oracle for this lives
    # here on the host and is recomputed from the generated input every run.
    "search_index_correct": lambda inp, out: _search_index_correct(inp, out),
    # A counting map: every key came from the input, and the counts total.
    "counts_match_input": lambda inp, out: _counts_match_input(inp, out),
    "keys_are_input_values": lambda inp, out: isinstance(out, dict)
    and {str(k) for k in out} == {str(v) for v in _as_list(inp)},
    "depth_matches_input": lambda inp, out: out == _nesting_depth(inp),
}


def _search_index_correct(inp: Any, out: Any) -> bool:
    if not isinstance(inp, (list, tuple)) or len(inp) != 2:
        return False
    haystack, target = inp
    if not isinstance(out, int) or isinstance(out, bool):
        return False
    if out == -1:
        return target not in haystack
    return 0 <= out < len(haystack) and haystack[out] == target


def _counts_match_input(inp: Any, out: Any) -> bool:
    if not isinstance(out, dict):
        return False
    items = _as_list(inp)
    try:
        total = sum(int(v) for v in out.values())
    except (TypeError, ValueError):
        return False
    if total != len(items):
        return False
    expected: dict[str, int] = {}
    for item in items:
        expected[str(item)] = expected.get(str(item), 0) + 1
    return {str(k): int(v) for k, v in out.items()} == expected


def check_properties(spec: dict, generated_input: Any, actual_value: Any) -> list[str]:
    failures: list[str] = []
    for name in spec.get("predicates", []):
        predicate = PREDICATES.get(name)
        if predicate is None:
            failures.append(f"{name} (unknown predicate - authoring error)")
            continue
        try:
            if not predicate(generated_input, actual_value):
                failures.append(name)
        except Exception as exc:  # noqa: BLE001 - a predicate that throws is a failure
            failures.append(f"{name} (raised {type(exc).__name__})")
    return failures


# --------------------------------------------------------------------------
# Host-side comparison
# --------------------------------------------------------------------------
def _canonical(value_json: str | None) -> Any:
    if value_json is None:
        return None
    try:
        return json.loads(value_json)
    except (json.JSONDecodeError, TypeError):
        return value_json


def _render(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _diff(expected: str, actual: str) -> str:
    lines = list(
        difflib.unified_diff(
            expected.splitlines() or [""],
            actual.splitlines() or [""],
            fromfile="expected",
            tofile="actual",
            lineterm="",
            n=1,
        )
    )
    return "\n".join(lines[:40])


def _outcome_from_status(status: str) -> TestOutcome:
    return {
        "timeout": TestOutcome.TIMEOUT,
        "crash": TestOutcome.CRASH,
        "oom": TestOutcome.OOM,
        "harness_error": TestOutcome.CRASH,
    }.get(status, TestOutcome.FAIL)


def _explain_failure(result: SandboxResult, test_key: str) -> str:
    if result.status == "timeout":
        return (
            f"{test_key} exceeded the wall-clock budget and was killed. "
            "A timeout scores as a failure, never as partial credit."
        )
    if result.status == "oom":
        return f"{test_key} exhausted the memory budget."
    if result.status == "crash":
        return f"{test_key} raised {result.exception or 'an uncaught exception'}."
    if result.status == "harness_error":
        return f"{test_key} could not be executed: {result.exception}."
    return ""


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def execute_tests(
    files: dict[str, str],
    entry_point: str,
    tests: list,
    sandbox: Sandbox,
    seed_parts: tuple[str, ...],
    limits: SandboxLimits | None = None,
    visible_only: bool = False,
    on_repaired_source: bool = False,
) -> list[TestExecution]:
    """Run every admitted test in its own one-shot instance."""
    limits = limits or settings.sandbox
    executions: list[TestExecution] = []

    for test in tests:
        if visible_only and test.hidden:
            # Pre-deadline feedback runs the visible subset only. The hidden set
            # never appears in student-facing feedback.
            executions.append(
                TestExecution(
                    test_key=test.test_key,
                    category=_category_value(test.category),
                    outcome=TestOutcome.SKIPPED,
                    hidden=True,
                    reason="hidden test - not run for pre-deadline feedback",
                    weight=test.weight,
                )
            )
            continue

        seed = derive_seed(*seed_parts, test.test_key)
        rng = random.Random(seed)

        generated_input: Any = None
        if test.property_spec:
            generated_input = generate_input(test.property_spec.get("generator", {}), rng)
            # ``spread_args`` lets a generator produce a multi-argument call
            # (a haystack and a target, say) without the predicates losing
            # sight of the input as one object.
            if test.property_spec.get("spread_args") and isinstance(generated_input, list):
                args = list(generated_input)
            else:
                args = [generated_input]
        else:
            args = list(test.args or [])

        job = SandboxJob(
            test_key=test.test_key,
            files=files,
            entry_point=entry_point,
            call=test.call,
            args=args,
            setup=test.setup or "",
            seed=seed,
            limits=limits,
        )
        result = sandbox.run(job)
        execution = _evaluate(test, result, generated_input, on_repaired_source)
        executions.append(execution)

    return executions


def _category_value(category: Any) -> str:
    if isinstance(category, TestCategory):
        return category.value
    return str(category)


def _evaluate(
    test,
    result: SandboxResult,
    generated_input: Any,
    on_repaired_source: bool,
) -> TestExecution:
    execution = TestExecution(
        test_key=test.test_key,
        category=_category_value(test.category),
        outcome=TestOutcome.FAIL,
        hidden=bool(test.hidden),
        cpu_ms=result.cpu_ms,
        wall_ms=result.wall_ms,
        peak_memory_kb=result.peak_memory_kb,
        exit_code=result.exit_code,
        stderr_excerpt=(result.stderr or "")[:1000],
        weight=test.weight,
        on_repaired_source=on_repaired_source,
    )

    if result.status != "ok":
        execution.outcome = _outcome_from_status(result.status)
        execution.reason = _explain_failure(result, test.test_key)
        execution.actual = result.exception or ""
        return execution

    actual_value = _canonical(result.value)
    execution.actual = _render(actual_value)

    if test.property_spec:
        failures = check_properties(test.property_spec, generated_input, actual_value)
        execution.property_failures = failures
        execution.expected = " and ".join(test.property_spec.get("predicates", [])) or "(properties)"
        if failures:
            execution.outcome = TestOutcome.FAIL
            execution.reason = (
                f"Property violated on input {_render(generated_input)[:200]}: "
                + ", ".join(failures)
            )
            execution.diff = f"violated: {', '.join(failures)}"
        else:
            execution.outcome = TestOutcome.PASS
        return execution

    expected_value = _canonical(test.expected_output)
    execution.expected = _render(expected_value)
    if _values_equal(expected_value, actual_value):
        execution.outcome = TestOutcome.PASS
    else:
        execution.outcome = TestOutcome.FAIL
        execution.diff = _diff(execution.expected, execution.actual)
        execution.reason = (
            f"{test.test_key} returned {execution.actual[:160]}, expected {execution.expected[:160]}"
        )
    return execution


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected == actual:
        return True
    # Tolerate list/tuple interchange, which JSON round-tripping erases anyway.
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        return list(expected) == list(actual)
    if isinstance(expected, float) or isinstance(actual, float):
        try:
            return abs(float(expected) - float(actual)) < 1e-9
        except (TypeError, ValueError):
            return False
    return False


# --------------------------------------------------------------------------
# A3 - reference validation (authoring time, not grading time)
# --------------------------------------------------------------------------
@dataclass
class ReferenceValidation:
    admitted: list[str] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    halted: bool = False
    message: str = ""

    @property
    def failure_rate(self) -> float:
        total = len(self.admitted) + len(self.discarded)
        return len(self.discarded) / total if total else 0.0


def validate_against_reference(
    reference_files: dict[str, str],
    entry_point: str,
    tests: list,
    sandbox: Sandbox,
    seed_parts: tuple[str, ...],
    halt_threshold: float = 0.20,
) -> ReferenceValidation:
    """A3. Every generated test executes against the instructor's reference
    solution before admission.

    Passes on reference -> admitted. Fails -> discarded and logged. More than
    ~20% failing halts authoring and flags the brief as ambiguous. That halt is
    a feature: it catches spec problems before sixty students hit them, and it
    eliminates the largest failure mode of LLM-assisted grading -- a
    hallucinated expected output silently penalising a whole cohort.
    """
    validation = ReferenceValidation()
    executions = execute_tests(
        reference_files, entry_point, tests, sandbox, seed_parts, visible_only=False
    )
    by_key = {e.test_key: e for e in executions}
    for test in tests:
        execution = by_key.get(test.test_key)
        if execution is not None and execution.passed:
            validation.admitted.append(test.test_key)
        else:
            validation.discarded.append(
                {
                    "test_key": test.test_key,
                    "reason": (execution.reason if execution else "not executed")
                    or "did not pass on the reference solution",
                }
            )

    if validation.failure_rate > halt_threshold:
        validation.halted = True
        validation.message = (
            f"{validation.failure_rate:.0%} of generated tests fail on the reference solution "
            f"(threshold {halt_threshold:.0%}). Authoring halted: the brief is probably ambiguous. "
            "Review the specification before any student sees this assignment."
        )
    else:
        validation.message = (
            f"{len(validation.admitted)} test(s) admitted, {len(validation.discarded)} discarded "
            "after execution against the reference solution."
        )
    return validation
