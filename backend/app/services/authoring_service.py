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
from . import brief_analysis
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

    def draft_rubric(
        self,
        brief: str,
        concepts: list[Concept],
        mode: str = "executable",
        entry_call: str = "solve",
        requires_report: bool = False,
    ) -> list[DraftRubricItem]: ...
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

    It reads the brief for requirements a parser can actually verify (see
    ``brief_analysis``) and proposes concept tags by lexical overlap against
    the course graph. It is weaker than an LLM at understanding a rambling
    brief and exactly as safe, because A3 validates everything either produces
    and a human approves it either way.

    ``mode`` changes what it is willing to emit. In ``static`` mode there is no
    reference solution, so no test can ever be validated and no rubric item may
    claim to be test-checkable. Emitting one anyway would produce items that
    can never earn evidence, which is the worst possible failure here: a
    student loses marks for something the platform structurally cannot assess.
    """

    name = "heuristic-drafter-2.0"

    def draft_rubric(
        self,
        brief: str,
        concepts: list[Concept],
        mode: str = "executable",
        entry_call: str = "solve",
        requires_report: bool = False,
    ) -> list[DraftRubricItem]:
        requirements = brief_analysis.analyse_brief(brief, entry_call)

        items: list[DraftRubricItem] = []
        for index, requirement in enumerate(requirements[:12], start=1):
            checks = list(requirement.checkable_by)
            if mode == "executable" and requirement.static_check is None and "report" not in checks:
                checks.append("test")
            if not requires_report and "report" in checks:
                checks = [c for c in checks if c != "report"] or ["static"]
            items.append(
                DraftRubricItem(
                    item_key=f"rb_{index:02d}",
                    text=requirement.text,
                    category=requirement.category,
                    weight=requirement.weight,
                    concept_ids=self._propose_concepts(
                        f"{requirement.text} {requirement.source_phrase}", requirement.concept_hints, concepts
                    ),
                    checkable_by=checks,
                    static_check=requirement.static_check,
                )
            )

        # In executable mode the tests are the primary evidence, so there must
        # be at least one item they can attach to.
        if mode == "executable" and not any("test" in i.checkable_by for i in items):
            items.append(
                DraftRubricItem(
                    item_key=f"rb_{len(items) + 1:02d}",
                    text="Produces correct output for the specified behaviour",
                    category="correctness",
                    weight=10.0,
                    concept_ids=self._propose_concepts(brief, [], concepts),
                    checkable_by=["test"],
                )
            )

        # A thin rubric is an honest outcome for a vague brief, but a rubric of
        # one item grades nothing. Where the brief gave little to work with, add
        # the two things that are checkable for any program at all.
        #
        # Deliberately *not* added here: a generic "implements a recognisable
        # algorithm" item. The algorithm classifier knows a handful of named
        # algorithms from this course; asked about anything else it correctly
        # says "no idea", and an item built on that would mark down every
        # correct submission to an unusual assignment. It is only emitted when
        # the brief actually names an algorithm, which analyse_brief detects.
        if mode == "static" and len(items) < 3:
            existing = {(i.static_check or {}).get("kind") for i in items}
            if "min_functions" not in existing:
                items.append(
                    DraftRubricItem(
                        item_key=f"rb_{len(items) + 1:02d}",
                        text="Decomposes the problem into named functions rather than one block",
                        category="style",
                        weight=4.0,
                        concept_ids=self._propose_concepts(brief, [], concepts),
                        checkable_by=["static"],
                        static_check={"kind": "min_functions", "min": 1},
                    )
                )
            if "documented" not in existing:
                items.append(
                    DraftRubricItem(
                        item_key=f"rb_{len(items) + 1:02d}",
                        text="Explains its intent in comments a reader can follow",
                        category="style",
                        weight=4.0,
                        concept_ids=self._propose_concepts(brief, [], concepts),
                        checkable_by=["static"],
                        static_check={"kind": "documented", "min_ratio": 0.05},
                    )
                )
        return items

    def _propose_concepts(
        self, text: str, hints: list[str], concepts: list[Concept]
    ) -> list[str]:
        haystack_text = f"{text} {' '.join(hints)}"
        words = {w for w in re.findall(r"[a-z]{4,}", haystack_text.lower())}
        scored: list[tuple[float, str]] = []
        for concept in concepts:
            concept_text = f"{concept.name} {concept.description}".lower()
            concept_words = {w for w in re.findall(r"[a-z]{4,}", concept_text)}
            if not concept_words:
                continue
            overlap = len(words & concept_words) / len(concept_words)
            if overlap > 0:
                scored.append((overlap, concept.concept_key))
        scored.sort(reverse=True)
        return [key for _, key in scored[:2]]

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
    grading_mode: str = "executable"
    generated_parts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


#: What each mode means, in the words the review screen shows the instructor.
GRADING_MODES = {
    "executable": (
        "Executable. You supplied a reference solution, so tests were generated, validated against "
        "it, and will run against every submission in the sandbox. Output correctness is the primary "
        "evidence."
    ),
    "static": (
        "Approach-graded. You did not supply a reference solution, so there is no oracle and no test "
        "can be validated - none will run. The platform grades what it can verify without one: "
        "whether the code compiles, whether it uses the constructs your brief asks for, what "
        "algorithm it implements, and whether the report describes the code that was actually "
        "submitted. Expect more submissions to be routed to you for review, which is the honest "
        "consequence of grading without an oracle rather than a defect."
    ),
}


def resolve_grading_mode(reference_solution: str | None) -> str:
    """The mode follows from the inputs, not from a setting.

    Without a reference solution nothing can validate a generated test, so
    admitting one would mean grading a cohort against an unverified expected
    output — the exact failure A3 exists to prevent. So there is no way to ask
    for executable grading without supplying the thing that makes it safe.
    """
    return "executable" if (reference_solution or "").strip() else "static"


def draft_assignment_version(
    session: Session,
    assignment: Assignment,
    brief: str,
    reference_solution: str,
    entry_point: str = "solution.py",
    entry_call: str = "solve",
    drafter: Drafter | None = None,
    created_by: str | None = None,
    rubric: list[DraftRubricItem] | None = None,
    tests: list[DraftTestCase] | None = None,
    requires_report: bool = False,
) -> AuthoringResult:
    """A1 + A2 + A3. Produces an *unapproved* version. Nothing grades yet.

    Every input except the brief is optional. Whatever the instructor leaves
    blank is generated and **marked as generated**, so the review screen can
    ask for more scrutiny on the parts no human wrote.
    """
    drafter = drafter or HeuristicDrafter()
    mode = resolve_grading_mode(reference_solution)
    concepts = list(
        session.scalars(select(Concept).where(Concept.course_id == assignment.course_id))
    )
    concept_keys = {c.concept_key for c in concepts}

    generated: list[str] = []
    notes: list[str] = []

    if rubric:
        rubric_draft = list(rubric)
    else:
        rubric_draft = drafter.draft_rubric(brief, concepts, mode, entry_call, requires_report)
        generated.append("rubric")
        notes.append(brief_analysis.summarise(brief_analysis.analyse_brief(brief, entry_call)))

    if mode == "static":
        # No oracle exists, so no item may claim to be test-checkable. Leaving
        # such an item in place would create marks a submission can never earn.
        stripped = 0
        for item in rubric_draft:
            if "test" in item.checkable_by:
                item.checkable_by = [c for c in item.checkable_by if c != "test"]
                item.test_ids = []
                stripped += 1
                if not item.checkable_by:
                    item.checkable_by = ["structural"]
        if stripped:
            notes.append(
                f"{stripped} rubric item(s) asked for test evidence, which cannot exist without a "
                "reference solution. They now draw on static and structural evidence instead."
            )
        test_draft: list[DraftTestCase] = []
        if tests:
            notes.append(
                f"{len(tests)} supplied test case(s) were discarded: with no reference solution there "
                "is nothing to validate them against, and an unvalidated test can silently penalise "
                "a whole cohort."
            )
    elif tests:
        test_draft = list(tests)
    else:
        test_draft = drafter.draft_tests(brief, entry_call)
        generated.append("tests")

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
        reference_solution=reference_solution or "",
        created_by=created_by,
        drafted_by_model=drafter.name if generated else "faculty-authored",
        authoring_edits=[{"kind": "schema_repair", "detail": r} for r in repairs],
        grading_mode=mode,
        generated_parts=generated,
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

    if mode == "executable":
        validation = run_reference_validation(session, version)
    else:
        validation = ReferenceValidation(
            message=(
                "Skipped: there is no reference solution to validate against, so no test was "
                "generated and none will run."
            )
        )

    return AuthoringResult(
        version=version,
        repairs=repairs,
        validation=validation,
        halted=validation.halted,
        message=validation.message,
        grading_mode=mode,
        generated_parts=generated,
        notes=notes,
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
    all_tests = list(session.scalars(select(TestCase).where(TestCase.version_id == version.id)))
    admitted = [t for t in all_tests if t.validated_against_reference]
    items = list(session.scalars(select(RubricItem).where(RubricItem.version_id == version.id)))

    if not items:
        raise ValueError("This version has no rubric items, so there is nothing to grade against.")

    if version.grading_mode == "executable":
        if not admitted:
            raise ValueError(
                "No test survived reference validation. Approving this version would grade a cohort "
                "against tests that the reference solution itself fails."
            )
    else:
        # Approach-graded. There is no oracle, so the only thing that makes the
        # rubric gradeable at all is that its items carry checks which do not
        # need one.
        checkable = [
            item for item in items
            if item.static_check or {"structural", "report"} & set(item.checkable_by or [])
        ]
        if not checkable:
            raise ValueError(
                "Without a reference solution, every rubric item needs a static check, structural "
                "evidence, or the report to draw on - otherwise no item can earn evidence and every "
                "submission would score zero. Add a reference solution, or give the items checks."
            )

    version.approved_by = faculty_id
    version.approved_at = _now()
    version.authoring_edits = list(version.authoring_edits or []) + list(edits or [])
    session.flush()
    return version


# --------------------------------------------------------------------------
# Creating an assignment from whatever the instructor gives us
# --------------------------------------------------------------------------
@dataclass
class AssignmentDraftSpec:
    """Everything an instructor might supply. Only ``title`` and ``brief`` are
    genuinely required; the rest is filled in or worked around."""

    title: str
    brief: str
    code: str | None = None
    entry_point: str = "solution.py"
    entry_call: str = "solve"
    reference_solution: str = ""
    requires_report: bool = False
    due_at: datetime | None = None
    opens_at: datetime | None = None
    max_attempts: int = 10
    rubric: list[dict] | None = None
    tests: list[dict] | None = None
    approve_immediately: bool = True


def _next_code(session: Session, course_id: str) -> str:
    existing = list(
        session.scalars(select(Assignment.code).where(Assignment.course_id == course_id))
    )
    numbers = [
        int(match.group(1))
        for code in existing
        if (match := re.search(r"(\d+)$", code or ""))
    ]
    return f"LAB{max(numbers, default=0) + 1:02d}"


def _rubric_from_payload(rows: list[dict]) -> list[DraftRubricItem]:
    items: list[DraftRubricItem] = []
    for index, row in enumerate(rows, start=1):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        items.append(
            DraftRubricItem(
                item_key=row.get("item_key") or f"rb_{index:02d}",
                text=text,
                category=row.get("category") or "correctness",
                weight=float(row.get("weight") or 5.0),
                concept_ids=list(row.get("concept_ids") or []),
                checkable_by=list(row.get("checkable_by") or []) or ["static"],
                test_ids=list(row.get("test_ids") or []),
                static_check=row.get("static_check"),
            )
        )
    return items


def _tests_from_payload(rows: list[dict], entry_call: str) -> list[DraftTestCase]:
    tests: list[DraftTestCase] = []
    for index, row in enumerate(rows, start=1):
        if row.get("expected_output") is None and row.get("property_spec") is None:
            continue
        tests.append(
            DraftTestCase(
                test_key=row.get("test_key") or f"tc_{index:02d}",
                category=row.get("category") or "basic",
                weight=float(row.get("weight") or 1.0),
                call=row.get("call") or entry_call,
                args=row.get("args"),
                expected_output=row.get("expected_output"),
                property_spec=row.get("property_spec"),
                hidden=bool(row.get("hidden")),
            )
        )
    return tests


def create_assignment(
    session: Session,
    course_id: str,
    faculty_id: str,
    spec: AssignmentDraftSpec,
    drafter: Drafter | None = None,
) -> AuthoringResult:
    """Create an assignment from a partially-filled form.

    The whole point is that an instructor can stop at any level of effort:

    * brief only -> the platform reads the brief for checkable requirements and
      grades the approach;
    * brief + reference solution -> tests are generated, validated against the
      reference, and executed;
    * brief + rubric -> the instructor's rubric is used verbatim;
    * everything -> nothing is generated at all.

    What is *not* offered is executable grading without a reference solution.
    That would mean running tests nothing has verified.
    """
    assignment = Assignment(
        course_id=course_id,
        code=(spec.code or "").strip() or _next_code(session, course_id),
        title=spec.title.strip(),
        opens_at=spec.opens_at,
        due_at=spec.due_at,
        max_attempts=spec.max_attempts,
        requires_report=spec.requires_report,
    )
    session.add(assignment)
    session.flush()

    result = draft_assignment_version(
        session,
        assignment,
        brief=spec.brief,
        reference_solution=spec.reference_solution,
        entry_point=spec.entry_point or "solution.py",
        entry_call=spec.entry_call or "solve",
        drafter=drafter,
        created_by=faculty_id,
        rubric=_rubric_from_payload(spec.rubric) if spec.rubric else None,
        tests=_tests_from_payload(spec.tests, spec.entry_call) if spec.tests else None,
        requires_report=spec.requires_report,
    )

    if spec.approve_immediately and not result.halted:
        try:
            approve_version(session, result.version, faculty_id)
        except ValueError as exc:
            result.notes.append(f"Saved as a draft rather than published: {exc}")
    elif result.halted:
        result.notes.append(
            "Saved as a draft. Reference validation halted, so this must not go live until the "
            "brief is clarified."
        )

    session.flush()
    return result


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
