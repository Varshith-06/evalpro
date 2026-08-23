"""§4.1 Assignment authoring. The only place an LLM touches the critical path.

The flow is draft -> validate -> review -> approve, and the order is the point:

* **A1 rubric drafting** turns a messy instructor brief into concrete,
  individually-checkable requirements with weights, ``checkable_by`` tags, and
  proposed ``concept_ids`` drawn from the course concept graph. Strict JSON out;
  a schema violation is rejected and retried, and no free prose from this stage
  ever reaches grading logic.
* **A2 test generation** emits ``(setup, input, expected_output, category,
  weight)`` with a bias toward property-based tests.
* **A3 reference validation is non-negotiable** and lives in
  ``engine.b4_execute.validate_against_reference``. Nothing is admitted until it
  has executed against the instructor's reference solution.
* **A4 faculty review** is where ``approved_by`` gets set. Nothing grades until
  it is, and **every edit is training data** -- deletions, weight changes, and
  concept re-tags are exactly the supervision the drafting model needs.

The drafter is a Protocol. ``HeuristicDrafter`` is the offline default so the
platform is fully functional with no API key; an ``LLMDrafter`` implementing the
same three methods is a drop-in replacement, and because A3 gates everything it
produces, a hallucinating drafter degrades to "fewer admitted tests" rather than
"a whole cohort silently penalised".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.b4_execute import ReferenceValidation, validate_against_reference
from ..engine.sandbox import DEFAULT_SANDBOX
from ..models import (
    Assignment,
    AssignmentVersion,
    Concept,
    RubricItem,
    TestCase,
    TestCategory,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DraftRubricItem:
    item_key: str
    text: str
    category: str
    weight: float
    concept_ids: list[str] = field(default_factory=list)
    checkable_by: list[str] = field(default_factory=list)
    test_ids: list[str] = field(default_factory=list)
    static_check: dict | None = None
    auto_gradeable: bool = True


@dataclass
class DraftTestCase:
    test_key: str
    category: str
    weight: float
    call: str
    args: list | None = None
    expected_output: str | None = None
    property_spec: dict | None = None
    hidden: bool = False
    setup: str = ""


@dataclass
class DraftConcept:
    concept_key: str
    name: str
    prerequisites: list[str] = field(default_factory=list)
    bloom_level: str = "apply"
    course_outcomes: list[str] = field(default_factory=list)
    typical_misconceptions: list[str] = field(default_factory=list)


class Drafter(Protocol):
    """Every drafting backend implements exactly this."""

    name: str

    def draft_rubric(self, brief: str, concepts: list[Concept]) -> list[DraftRubricItem]: ...
    def draft_tests(self, brief: str, entry_call: str) -> list[DraftTestCase]: ...
    def draft_concepts(self, syllabus: str, course_code: str) -> list[DraftConcept]: ...


# --------------------------------------------------------------------------
# Schema validation - applied to any drafter's output, LLM or otherwise
# --------------------------------------------------------------------------
VALID_CHECKS = {"test", "static", "structural", "report", "manual"}
VALID_CATEGORIES = {c.value for c in TestCategory}


class SchemaViolation(Exception):
    pass


def validate_rubric_draft(items: list[DraftRubricItem], concept_keys: set[str]) -> list[str]:
    """Reject on schema violation. Returns the list of repairs applied.

    A drafter is allowed to be wrong; it is not allowed to be wrong *silently*.
    Every correction here is recorded on the version as authoring provenance.
    """
    repairs: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item.item_key or item.item_key in seen:
            raise SchemaViolation(f"duplicate or missing item_key: {item.item_key!r}")
        seen.add(item.item_key)
        if not item.text.strip():
            raise SchemaViolation(f"{item.item_key}: empty rubric text")
        if item.weight <= 0:
            raise SchemaViolation(f"{item.item_key}: weight must be positive")

        unknown = [c for c in item.concept_ids if c not in concept_keys]
        if unknown:
            # A concept tag outside the course graph is the single most damaging
            # drafting error, because it silently creates a concept nobody teaches.
            item.concept_ids = [c for c in item.concept_ids if c in concept_keys]
            repairs.append(f"{item.item_key}: dropped concept tag(s) not in the course graph: {unknown}")
        if not item.concept_ids:
            repairs.append(
                f"{item.item_key}: no concept tags - this item's evidence cannot reach the mastery model "
                "until a human tags it"
            )
        bad_checks = [c for c in item.checkable_by if c not in VALID_CHECKS]
        if bad_checks:
            item.checkable_by = [c for c in item.checkable_by if c in VALID_CHECKS]
            repairs.append(f"{item.item_key}: dropped unknown checkable_by value(s) {bad_checks}")
    return repairs


def validate_test_draft(tests: list[DraftTestCase]) -> list[str]:
    repairs: list[str] = []
    seen: set[str] = set()
    for test in tests:
        if not test.test_key or test.test_key in seen:
            raise SchemaViolation(f"duplicate or missing test_key: {test.test_key!r}")
        seen.add(test.test_key)
        if test.category not in VALID_CATEGORIES:
            repairs.append(f"{test.test_key}: unknown category {test.category!r}, defaulted to basic")
            test.category = "basic"
        if test.expected_output is None and test.property_spec is None:
            raise SchemaViolation(f"{test.test_key}: neither an expected output nor a property specification")
    return repairs


# --------------------------------------------------------------------------
# Offline drafter
# --------------------------------------------------------------------------
_REQUIREMENT_MARKERS = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.{6,})$", re.M)

_CATEGORY_HINTS = (
    (("empty", "edge", "boundary", "null", "zero-length"), "robustness", ["static", "test"]),
    (("efficien", "complexity", "o(", "performance", "time limit"), "efficiency", ["static", "test"]),
    (("recursi",), "correctness", ["static", "structural", "test"]),
    (("document", "comment", "readab", "style"), "style", ["static"]),
    (("report", "explain", "justify", "describe"), "communication", ["report"]),
    (("error", "exception", "invalid input"), "robustness", ["static", "test"]),
)


class HeuristicDrafter:
    """Deterministic drafter. No API key, no network, fully reproducible.

    It reads the structure instructors actually write -- bulleted requirements --
    and proposes concept tags by lexical overlap against the course graph. It is
    weaker than an LLM at understanding a rambling brief and exactly as safe,
    because A3 validates everything either produces.
    """

    name = "heuristic-drafter-1.0"

    def draft_rubric(self, brief: str, concepts: list[Concept]) -> list[DraftRubricItem]:
        requirements = [m.group(1).strip() for m in _REQUIREMENT_MARKERS.finditer(brief)]
        if not requirements:
            requirements = [s.strip() for s in re.split(r"(?<=[.!?])\s+", brief) if len(s.strip()) > 25]

        items: list[DraftRubricItem] = []
        for index, text in enumerate(requirements[:12], start=1):
            lowered = text.lower()
            category, checks = "correctness", ["test"]
            for needles, cat, check_list in _CATEGORY_HINTS:
                if any(n in lowered for n in needles):
                    category, checks = cat, list(check_list)
                    break
            items.append(
                DraftRubricItem(
                    item_key=f"rb_{index:02d}",
                    text=text.rstrip("."),
                    category=category,
                    weight=8.0 if category == "correctness" else 5.0,
                    concept_ids=self._propose_concepts(text, concepts),
                    checkable_by=checks,
                    static_check=self._propose_static_check(lowered),
                )
            )
        return items

    def _propose_concepts(self, text: str, concepts: list[Concept]) -> list[str]:
        words = {w for w in re.findall(r"[a-z]{4,}", text.lower())}
        scored: list[tuple[float, str]] = []
        for concept in concepts:
            haystack = f"{concept.name} {concept.description}".lower()
            concept_words = {w for w in re.findall(r"[a-z]{4,}", haystack)}
            if not concept_words:
                continue
            overlap = len(words & concept_words) / len(concept_words)
            if overlap > 0:
                scored.append((overlap, concept.concept_key))
        scored.sort(reverse=True)
        return [key for _, key in scored[:2]]

    def _propose_static_check(self, lowered: str) -> dict | None:
        if "empty" in lowered or "boundary" in lowered:
            return {"kind": "guard_present", "target": "input_length"}
        if "recursi" in lowered:
            return {"kind": "recursion_present"}
        if "exception" in lowered or "error" in lowered:
            return {"kind": "error_handling"}
        if "complexity" in lowered or "o(n)" in lowered:
            return {"kind": "loop_nesting", "max_depth": 1}
        if "document" in lowered or "comment" in lowered:
            return {"kind": "documented", "min_ratio": 0.05}
        return None

    def draft_tests(self, brief: str, entry_call: str) -> list[DraftTestCase]:
        """Property tests first.

        ``is_ascending(f(x)) and multiset_equal(f(x), x)`` over random lists is
        worth twenty hardcoded arrays and cannot be defeated by memorising
        outputs, so the generic draft leads with properties and adds fixed cases
        only for the boundaries a generator will rarely hit.
        """
        return [
            DraftTestCase("tc_01", "smoke", 1.0, entry_call, args=[[]], expected_output="[]"),
            DraftTestCase(
                "tc_02", "property", 3.0, entry_call,
                property_spec={
                    "generator": {"kind": "int_list", "n": [0, 40], "lo": -100, "hi": 100},
                    "predicates": ["is_ascending", "multiset_equal_to_input"],
                },
            ),
            DraftTestCase(
                "tc_03", "edge", 2.0, entry_call,
                property_spec={
                    "generator": {"kind": "int_list", "n": [0, 1], "lo": -5, "hi": 5},
                    "predicates": ["is_ascending", "same_length_as_input"],
                },
            ),
            DraftTestCase(
                "tc_04", "stress", 2.0, entry_call, hidden=True,
                property_spec={
                    "generator": {"kind": "int_list", "n": [400, 800], "lo": -10_000, "hi": 10_000},
                    "predicates": ["is_ascending", "multiset_equal_to_input"],
                },
            ),
        ]

    def draft_concepts(self, syllabus: str, course_code: str) -> list[DraftConcept]:
        topics = [m.group(1).strip() for m in _REQUIREMENT_MARKERS.finditer(syllabus)]
        drafts: list[DraftConcept] = []
        previous: str | None = None
        for topic in topics:
            key = "c_" + re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:28]
            drafts.append(
                DraftConcept(
                    concept_key=key,
                    name=topic,
                    prerequisites=[previous] if previous else [],
                )
            )
            previous = key
        return drafts


# --------------------------------------------------------------------------
# Authoring flow
# --------------------------------------------------------------------------
@dataclass
class AuthoringResult:
    version: AssignmentVersion
    repairs: list[str]
    validation: ReferenceValidation
    halted: bool
    message: str


def draft_assignment_version(
    session: Session,
    assignment: Assignment,
    brief: str,
    reference_solution: str,
    entry_point: str = "solution.py",
    entry_call: str = "solve",
    drafter: Drafter | None = None,
    created_by: str | None = None,
) -> AuthoringResult:
    """A1 + A2 + A3. Produces an *unapproved* version. Nothing grades yet."""
    drafter = drafter or HeuristicDrafter()
    concepts = list(
        session.scalars(select(Concept).where(Concept.course_id == assignment.course_id))
    )
    concept_keys = {c.concept_key for c in concepts}

    rubric_draft = drafter.draft_rubric(brief, concepts)
    test_draft = drafter.draft_tests(brief, entry_call)
    repairs = validate_rubric_draft(rubric_draft, concept_keys)
    repairs += validate_test_draft(test_draft)

    # Wire each rubric item to the tests that can evidence it.
    test_keys = [t.test_key for t in test_draft]
    for item in rubric_draft:
        if "test" in item.checkable_by and not item.test_ids:
            item.test_ids = list(test_keys)

    next_version = max((v.version for v in assignment.versions), default=0) + 1
    version = AssignmentVersion(
        assignment_id=assignment.id,
        version=next_version,
        spec_text=brief,
        entry_point=entry_point,
        reference_solution=reference_solution,
        created_by=created_by,
        drafted_by_model=drafter.name,
        authoring_edits=[{"kind": "schema_repair", "detail": r} for r in repairs],
    )
    session.add(version)
    session.flush()

    for ordinal, item in enumerate(rubric_draft):
        session.add(
            RubricItem(
                version_id=version.id,
                item_key=item.item_key,
                ordinal=ordinal,
                text=item.text,
                category=item.category,
                weight=item.weight,
                concept_ids=item.concept_ids,
                checkable_by=item.checkable_by,
                test_ids=item.test_ids,
                static_check=item.static_check,
                auto_gradeable=item.auto_gradeable,
            )
        )
    for ordinal, test in enumerate(test_draft):
        session.add(
            TestCase(
                version_id=version.id,
                test_key=test.test_key,
                ordinal=ordinal,
                category=TestCategory(test.category),
                weight=test.weight,
                call=test.call,
                setup=test.setup,
                args=test.args,
                expected_output=test.expected_output,
                property_spec=test.property_spec,
                hidden=test.hidden,
            )
        )
    session.flush()

    validation = run_reference_validation(session, version)
    return AuthoringResult(
        version=version,
        repairs=repairs,
        validation=validation,
        halted=validation.halted,
        message=validation.message,
    )


def run_reference_validation(session: Session, version: AssignmentVersion) -> ReferenceValidation:
    """A3. Execute every drafted test against the reference solution."""
    tests = list(
        session.scalars(
            select(TestCase).where(TestCase.version_id == version.id).order_by(TestCase.ordinal)
        )
    )
    reference_files = {version.entry_point: version.reference_solution}
    validation = validate_against_reference(
        reference_files,
        version.entry_point,
        tests,
        DEFAULT_SANDBOX,
        seed_parts=(version.assignment_id, "reference"),
    )

    admitted = set(validation.admitted)
    discarded_reasons = {d["test_key"]: d["reason"] for d in validation.discarded}
    for test in tests:
        test.validated_against_reference = test.test_key in admitted
        test.validation_note = (
            "Passed on the reference solution; admitted."
            if test.test_key in admitted
            else f"Discarded: {discarded_reasons.get(test.test_key, 'did not pass on the reference')}"
        )
    session.flush()
    return validation


def approve_version(
    session: Session,
    version: AssignmentVersion,
    faculty_id: str,
    edits: list[dict] | None = None,
) -> AssignmentVersion:
    """A4. Nothing grades until this runs.

    ``edits`` is the supervision signal: which items faculty deleted, which
    weights they changed, which concept tags they corrected. It is stored on the
    version, not thrown away, because it is the training set for the next
    drafting model.
    """
    unvalidated = [
        t for t in session.scalars(select(TestCase).where(TestCase.version_id == version.id))
        if not t.validated_against_reference
    ]
    admitted = [
        t for t in session.scalars(select(TestCase).where(TestCase.version_id == version.id))
        if t.validated_against_reference
    ]
    if not admitted:
        raise ValueError(
            "No test survived reference validation. Approving this version would grade a cohort "
            "against tests that the reference solution itself fails."
        )

    version.approved_by = faculty_id
    version.approved_at = _now()
    version.authoring_edits = list(version.authoring_edits or []) + list(edits or [])
    session.flush()
    return version


def apply_faculty_edits(
    session: Session,
    version: AssignmentVersion,
    edits: list[dict],
) -> list[dict]:
    """Apply and record faculty changes to a drafted rubric.

    Each edit is ``{op, item_key, ...}`` with ops ``update_weight``,
    ``retag_concepts``, ``edit_text``, ``delete``. Recording them in the same
    shape they are applied is what makes them usable as training data later.
    """
    applied: list[dict] = []
    items = {
        i.item_key: i
        for i in session.scalars(select(RubricItem).where(RubricItem.version_id == version.id))
    }
    for edit in edits:
        item = items.get(edit.get("item_key", ""))
        if item is None:
            continue
        op = edit.get("op")
        if op == "update_weight":
            applied.append({**edit, "from": item.weight})
            item.weight = float(edit["weight"])
        elif op == "retag_concepts":
            applied.append({**edit, "from": list(item.concept_ids or [])})
            item.concept_ids = list(edit["concept_ids"])
        elif op == "edit_text":
            applied.append({**edit, "from": item.text})
            item.text = edit["text"]
        elif op == "delete":
            applied.append({**edit, "from": item.text})
            session.delete(item)
    version.authoring_edits = list(version.authoring_edits or []) + applied
    session.flush()
    return applied
