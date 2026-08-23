"""Build the demo course and run a full semester through the cascade.

This is not fixture data pasted into tables. Every score, mastery estimate,
misconception cluster, item statistic, risk flag, and attainment figure in the
demo is produced by the real pipeline running real student code in the real
sandbox. That is the only way the demo means anything: if the numbers were
typed in, so is the product.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Assignment,
    ConceptObservation,
    AssignmentVersion,
    BloomLevel,
    Concept,
    Course,
    CourseOutcome,
    Enrollment,
    Institution,
    Role,
    RubricItem,
    TestCase,
    TestCategory,
    User,
)
from .seed_data import (
    ARCHETYPES,
    ASSIGNMENTS,
    CONCEPTS,
    COURSE_OUTCOMES,
    PLAGIARISED_LAB03,
    RESOURCES,
    STUDENT_NAMES,
)
from .services import analytics_service, authoring_service, grading_service, metrics_service

SEED = 20260223
TERM_START = datetime(2026, 1, 5)


def _week(n: int) -> datetime:
    return TERM_START + timedelta(weeks=n)


def already_seeded(session: Session) -> bool:
    return session.scalar(select(Course).where(Course.code == "CS201")) is not None


def ensure_seeded(session: Session) -> str | None:
    if already_seeded(session):
        return None
    return seed_all(session)


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------
def seed_structure(session: Session) -> tuple[Course, list[User], User, User]:
    institution = Institution(
        name="Government College of Engineering (demo)", lms_kind="moodle", lti_client_id="demo-client"
    )
    session.add(institution)
    session.flush()

    course = Course(
        institution_id=institution.id,
        code="CS201",
        title="Data Structures and Algorithms Laboratory",
        term="Even 2026",
        language="python",
    )
    session.add(course)
    session.flush()

    for outcome in COURSE_OUTCOMES:
        session.add(
            CourseOutcome(
                course_id=course.id,
                code=outcome["code"],
                text=outcome["text"],
                programme_outcomes=outcome["po"],
                po_weights=outcome["weights"],
            )
        )

    for concept in CONCEPTS:
        session.add(
            Concept(
                course_id=course.id,
                concept_key=concept["key"],
                name=concept["name"],
                description=concept["desc"],
                bloom_level=BloomLevel(concept["bloom"]),
                prerequisites=concept["prereq"],
                course_outcomes=concept["co"],
                typical_misconceptions=concept.get("misconceptions", []),
                resources=RESOURCES.get(concept["key"], []),
                syllabus_week=concept["week"],
            )
        )

    faculty = User(
        external_id="fac-001", name="Dr. S. Balakrishnan",
        email="balakrishnan@example.edu", role=Role.FACULTY,
    )
    admin = User(
        external_id="adm-001", name="Prof. R. Venkatesh (HoD)",
        email="venkatesh@example.edu", role=Role.ADMIN,
    )
    session.add_all([faculty, admin])

    rng = random.Random(SEED)
    students: list[User] = []
    for index, name in enumerate(STUDENT_NAMES):
        student = User(
            external_id=f"CS2026{index + 1:03d}",
            name=name,
            email=f"student{index + 1:03d}@example.edu",
            role=Role.STUDENT,
            # Held only so the §7.3 bias audit has something to audit. Never a
            # model feature; see analytics/risk.py.
            protected_attributes={
                "gender": rng.choice(["F", "M", "F", "M", "X"]),
                "admission_category": rng.choice(["general", "general", "reserved", "reserved"]),
                "first_generation": rng.choice(["yes", "no", "no"]),
                "medium_of_instruction": rng.choice(["english", "english", "regional"]),
            },
        )
        students.append(student)
    session.add_all(students)
    session.flush()

    for index, student in enumerate(students):
        session.add(
            Enrollment(
                course_id=course.id,
                user_id=student.id,
                section="A" if index % 2 == 0 else "B",
                role=Role.STUDENT,
                synced_from="lti",
            )
        )
    for staff in (faculty, admin):
        session.add(
            Enrollment(course_id=course.id, user_id=staff.id, section="-", role=staff.role, synced_from="lti")
        )
    session.flush()
    return course, students, faculty, admin


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------
def seed_assignments(session: Session, course: Course, faculty: User) -> list[Assignment]:
    """Author each assignment explicitly, then run A3 reference validation.

    The rubrics here are hand-authored rather than drafted, which is what an
    instructor's *approved* output looks like after A4. ``scripts/demo.py``
    exercises the drafting path (A1-A3) separately so both are visible.
    """
    created: list[Assignment] = []

    for spec in ASSIGNMENTS:
        assignment = Assignment(
            course_id=course.id,
            code=spec["code"],
            title=spec["title"],
            opens_at=_week(spec["week"] - 2),
            due_at=_week(spec["week"]),
            max_attempts=10,
            requires_report=spec["requires_report"],
            lti_resource_link_id=f"lti-{spec['code'].lower()}",
        )
        session.add(assignment)
        session.flush()

        version = AssignmentVersion(
            assignment_id=assignment.id,
            version=1,
            spec_text=spec["brief"],
            entry_point=spec["entry_point"],
            reference_solution=spec["reference"],
            created_by=faculty.id,
            drafted_by_model="faculty-authored",
        )
        session.add(version)
        session.flush()

        for ordinal, item in enumerate(spec["rubric"]):
            session.add(
                RubricItem(
                    version_id=version.id,
                    item_key=item["key"],
                    ordinal=ordinal,
                    text=item["text"],
                    category=item["category"],
                    weight=float(item["weight"]),
                    concept_ids=item["concepts"],
                    checkable_by=item["checks"],
                    test_ids=item.get("tests", []),
                    static_check=item.get("static"),
                )
            )
        for ordinal, test in enumerate(spec["tests"]):
            session.add(
                TestCase(
                    version_id=version.id,
                    test_key=test["key"],
                    ordinal=ordinal,
                    category=TestCategory(test["category"]),
                    weight=float(test["weight"]),
                    call=spec["entry_call"],
                    args=test.get("args"),
                    expected_output=test.get("expected"),
                    property_spec=test.get("property"),
                    hidden=bool(test.get("hidden")),
                )
            )
        session.flush()

        # A3: nothing is admitted until it has executed against the reference.
        authoring_service.run_reference_validation(session, version)
        authoring_service.approve_version(
            session,
            version,
            faculty.id,
            edits=[{"op": "approved_as_authored", "note": "Rubric and tests authored directly by faculty."}],
        )
        created.append(assignment)

    session.flush()
    return created


# --------------------------------------------------------------------------
# The cohort
# --------------------------------------------------------------------------
#: Ability bands. A cohort where everyone performs identically produces a
#: mastery model with nothing to say, so the demo has a real spread -- including
#: two students who are genuinely in trouble and should be flagged early.
BANDS = (
    ("strong", 0.22),
    ("solid", 0.30),
    ("mixed", 0.28),
    ("struggling", 0.20),
)

BAND_PREFERENCES: dict[str, dict[str, list[str]]] = {
    "strong": {
        "LAB01": ["correct", "correct_insertion", "correct"],
        "LAB02": ["correct", "correct_recursive", "correct"],
        "LAB03": ["correct", "correct_get", "correct"],
        "LAB04": ["correct", "correct_max", "correct"],
    },
    "solid": {
        "LAB01": ["correct", "correct_insertion", "missing_colon"],
        "LAB02": ["correct", "off_by_one", "correct_recursive"],
        "LAB03": ["correct_get", "correct", "missing_paren"],
        "LAB04": ["correct_max", "correct", "iterative_stack"],
    },
    "mixed": {
        "LAB01": ["off_by_one", "no_empty_guard", "correct_insertion", "missing_colon"],
        "LAB02": ["off_by_one", "absent_returns_zero", "linear_scan"],
        "LAB03": ["nested_loop", "keyerror", "correct_get"],
        "LAB04": ["first_element_only", "iterative_stack", "correct"],
    },
    "struggling": {
        "LAB01": ["no_empty_guard", "off_by_one", "uses_builtin"],
        "LAB02": ["linear_scan", "absent_returns_zero", "infinite_loop"],
        "LAB03": ["keyerror", "nested_loop", "missing_paren"],
        "LAB04": ["no_base_case", "first_element_only"],
    },
}


# --------------------------------------------------------------------------
# Cohort diversification
# --------------------------------------------------------------------------
# A real class does not submit six byte-identical files. It submits six
# implementations of the same idea that differ in guard style, whether a length
# was hoisted, and whether the work was split into a helper.
#
# Renaming alone would be pointless here: B2 normalises identifiers to
# positional placeholders precisely so that renaming does not hide a copy. So
# the variations below are *structural* -- they change the token stream and the
# CFG, which is what an honest cohort looks like and what makes the deliberate
# plagiarism pair stand out against it.

_DOCSTRINGS = (
    "Solution for the lab exercise.",
    "My implementation.",
    "Lab submission - see the report for details.",
    "Implements the required function.",
    "Attempt at the exercise.",
)


def _swap_guard_style(source: str) -> str:
    import re

    if "if not " in source:
        return re.sub(r"if not (\w+):", r"if len(\1) == 0:", source, count=1)
    return re.sub(r"if len\((\w+)\) == 0:", r"if not \1:", source, count=1)


def _hoist_length(source: str) -> str:
    import re

    match = re.search(r"^(\s+)for (\w+) in range\(len\((\w+)\)\):", source, re.M)
    if not match:
        return source
    indent, _var, target = match.groups()
    source = source.replace(f"len({target})", "size")
    body_start = source.index("\n", source.index("def solve")) + 1
    # Insert the hoist after the docstring, before the first statement.
    lines = source.split("\n")
    insert_at = next(
        (i for i, line in enumerate(lines) if line.startswith(indent) and line.strip() and not line.strip().startswith('"""')),
        1,
    )
    lines.insert(insert_at, f"{indent}size = len({target})")
    del body_start
    return "\n".join(lines)


def _wrap_in_helper(source: str, call: str = "solve") -> str:
    if f"def {call}(" not in source:
        return source
    import re

    signature = re.search(rf"def {call}\(([^)]*)\):", source)
    if not signature:
        return source
    params = signature.group(1)
    renamed = source.replace(f"def {call}(", f"def _compute(", 1)
    args = ", ".join(p.strip().split("=")[0].strip() for p in params.split(",") if p.strip())
    wrapper = f'\n\ndef {call}({params}):\n    """Entry point."""\n    return _compute({args})\n'
    return renamed + wrapper


def _replace_docstring(source: str, rng: random.Random) -> str:
    import re

    return re.sub(r'"""[^"]*"""', f'"""{rng.choice(_DOCSTRINGS)}"""', source, count=1)


_COPIER_NAMES = ("result", "data", "temp", "output", "store", "entry", "val", "acc", "buf", "tally")


def _rename_identifiers(source: str, rng: random.Random) -> str:
    """Rename locals and rewrite comments - the classic disguise.

    It defeats a diff and it defeats a reader skimming two files side by side.
    It does not defeat B2, because normalisation maps every identifier to a
    positional placeholder before a single fingerprint is taken. That is the
    whole reason the normalisation step exists.
    """
    import re

    body = source
    locals_found = [
        name
        for name in dict.fromkeys(re.findall(r"\b([a-z_][a-z0-9_]*)\s*=", body))
        if name not in ("solve", "return")
    ]
    pool = list(_COPIER_NAMES)
    rng.shuffle(pool)
    for index, name in enumerate(locals_found):
        if index >= len(pool):
            break
        body = re.sub(rf"\b{re.escape(name)}\b", pool[index], body)
    body = re.sub(r'"""[^"]*"""', '"""Counts how many times each value appears."""', body, count=1)
    return body


def _for_range_to_while(source: str) -> str:
    """Rewrite the outermost ``for i in range(...)`` as a ``while`` loop.

    A genuinely different control-flow shape, which is what makes two students'
    files distinguishable to a token fingerprint. Skipped when the body carries
    a ``continue``, where the rewrite would change what the loop does.
    """
    import re

    match = re.search(r"^(\s+)for (\w+) in range\(([^)]+)\):\s*$", source, re.M)
    if not match:
        return source
    indent, var, bounds = match.groups()
    parts = [p.strip() for p in bounds.split(",")]
    start, stop = ("0", parts[0]) if len(parts) == 1 else (parts[0], parts[1])

    lines = source.split("\n")
    header_index = source[: match.start()].count("\n")
    end_index = len(lines)
    for i in range(header_index + 1, len(lines)):
        line = lines[i]
        if line.strip() and (len(line) - len(line.lstrip())) <= len(indent):
            end_index = i
            break
    body = lines[header_index + 1 : end_index]
    if any(re.search(r"\b(continue|break)\b", line) for line in body):
        return source

    replacement = [
        f"{indent}{var} = {start}",
        f"{indent}while {var} < {stop}:",
        *body,
        f"{indent}    {var} = {var} + 1",
    ]
    return "\n".join(lines[:header_index] + replacement + lines[end_index:])


def _compiles(source: str) -> bool:
    try:
        compile(source, "<variant>", "exec")
        return True
    except SyntaxError:
        return False


def _invoke(source: str, call: str, args: tuple):
    namespace: dict = {}
    exec(compile(source, "<variant>", "exec"), namespace)  # noqa: S102 - seed sources are ours
    return namespace[call](*args)


def _behaviour_matches(original: str, candidate: str, call: str, samples: list[tuple]) -> bool:
    """Accept a variation only if it behaves identically on sample inputs.

    A diversifier that silently changes what a submission does would corrupt
    every score, mastery estimate, and attainment figure downstream. Compiling
    is not enough; the variant has to do the same thing, including failing the
    same way for the archetypes that are meant to fail.
    """
    for args in samples:
        try:
            expected = _invoke(original, call, args)
            expected_error = None
        except Exception as exc:  # noqa: BLE001 - archetypes are allowed to crash
            expected, expected_error = None, type(exc).__name__
        try:
            actual = _invoke(candidate, call, args)
            actual_error = None
        except Exception as exc:  # noqa: BLE001
            actual, actual_error = None, type(exc).__name__
        if expected_error != actual_error or expected != actual:
            return False
    return True


def diversify(
    source: str,
    rng: random.Random,
    call: str = "solve",
    samples: list[tuple] | None = None,
) -> str:
    """Apply a behaviour-preserving subset of the structural variations.

    An archetype that is *meant* to be broken keeps its defect: it fails the
    compile check, so only the textual transforms apply and repair distance
    still has exactly the one edit it is supposed to find.
    """
    result = _replace_docstring(source, rng)
    if not _compiles(source):
        if rng.random() < 0.5:
            swapped = _swap_guard_style(result)
            if not _compiles(swapped) and swapped != result:
                result = swapped
        return result

    samples = samples or [([],)]
    transforms = []
    if rng.random() < 0.60:
        transforms.append(_swap_guard_style)
    if rng.random() < 0.45:
        transforms.append(_for_range_to_while)
    if rng.random() < 0.40:
        transforms.append(_hoist_length)
    if rng.random() < 0.35:
        transforms.append(lambda s: _wrap_in_helper(s, call))
    rng.shuffle(transforms)

    for transform in transforms:
        candidate = transform(result)
        if candidate == result or not _compiles(candidate):
            continue
        if _behaviour_matches(result, candidate, call, samples):
            result = candidate
    return result


def assign_bands(students: list[User], rng: random.Random) -> dict[str, str]:
    bands: dict[str, str] = {}
    pool: list[str] = []
    for band, share in BANDS:
        pool.extend([band] * max(1, round(share * len(students))))
    while len(pool) < len(students):
        pool.append("mixed")
    rng.shuffle(pool)
    for student, band in zip(students, pool):
        bands[student.id] = band
    return bands


def seed_submissions(
    session: Session,
    course: Course,
    students: list[User],
    assignments: list[Assignment],
    progress=None,
) -> dict:
    rng = random.Random(SEED + 1)
    # Timing draws come from their own stream. Sharing one would mean that
    # changing how submissions are dated silently reshuffles who is strong and
    # who struggles, so the demo would stop being reproducible for no reason.
    timing_rng = random.Random(SEED + 2)
    bands = assign_bands(students, rng)
    stats = {"submitted": 0, "released": 0, "escalated": 0, "skipped": 0}

    by_code = {a.code: a for a in assignments}
    # The integrity demonstration. The copier submits a renamed version of a
    # specific classmate's *actual* file - not of the canonical solution -
    # because a copy of the common idiom is indistinguishable from independent
    # work, and a copy of one distinctive file is not.
    strong_students = [s for s in students if bands[s.id] in ("strong", "solid")]
    plagiarism_source_student = rng.choice(strong_students)
    plagiarism_copier = rng.choice([s for s in students if s.id != plagiarism_source_student.id])
    copied_source: str | None = None

    for assignment in assignments:
        spec = next(s for s in ASSIGNMENTS if s["code"] == assignment.code)
        archetypes = ARCHETYPES[assignment.code]
        # Behaviour-equivalence samples for the diversifier, drawn from the
        # assignment's own fixed test cases so a variant is checked against the
        # inputs that actually matter.
        samples = [tuple(t["args"]) for t in spec["tests"] if t.get("args") is not None]

        # The copier necessarily submits after the person they copied from.
        order = sorted(
            students,
            key=lambda s: (
                s.id == plagiarism_copier.id,
                s.id != plagiarism_source_student.id,
            ),
        )
        for student in order:
            band = bands[student.id]
            # Struggling students genuinely miss some work; that absence is a
            # real early-warning signal, so it is modelled rather than filled in.
            if band == "struggling" and rng.random() < 0.22:
                stats["skipped"] += 1
                continue

            choices = BAND_PREFERENCES[band][assignment.code]
            key = rng.choice(choices)
            archetype = archetypes[key]
            source = diversify(archetype["source"], rng, spec["entry_call"], samples)
            report = archetype.get("report", "") if spec["requires_report"] else ""

            if assignment.code == "LAB03":
                if student.id == plagiarism_source_student.id:
                    source = diversify(archetypes["correct_get"]["source"], rng, spec["entry_call"], samples)
                    copied_source = source
                elif student.id == plagiarism_copier.id and copied_source:
                    # Identifiers renamed and comments rewritten - exactly the
                    # transformation AST normalisation is built to see through.
                    source = _rename_identifiers(copied_source, rng)
                    report = archetypes["correct"].get("report", "")
                elif student.id == plagiarism_copier.id:
                    source = _rename_identifiers(PLAGIARISED_LAB03, rng)
                    report = archetypes["correct"].get("report", "")

            files = {spec["entry_point"]: source}
            try:
                _, run, _cached = grading_service.submit(
                    session,
                    assignment.id,
                    student.id,
                    files,
                    report,
                    visible_only=False,
                )
            except grading_service.SubmissionRejected:
                stats["skipped"] += 1
                continue

            # Place the attempt in the term rather than at import time. Without
            # this every seeded submission is months past its due date and the
            # whole demo reads as a class that never hands anything in on time -
            # and the late-start signal the risk model uses becomes meaningless
            # because it fires for everybody.
            _backdate(session, run, assignment, band, timing_rng)

            stats["submitted"] += 1
            verdict = run.verdict
            if verdict and verdict.state.value == "released":
                stats["released"] += 1
            else:
                stats["escalated"] += 1
            if progress:
                progress(assignment.code, student.name, verdict.state.value if verdict else "pending")

        session.flush()

    del by_code
    return stats


def _backdate(session: Session, run, assignment: Assignment, band: str, rng: random.Random) -> None:
    """Move an attempt to a plausible moment in the semester.

    Strong students hand in early, struggling ones late and occasionally past
    the deadline, which is exactly the behavioural signal §7.3 wants.
    """
    due = assignment.due_at
    if due is None:
        return
    offsets = {
        "strong": (-9, -3),
        "solid": (-6, -1),
        "mixed": (-3, 1),
        "struggling": (-1, 4),
    }[band]
    delta = timedelta(days=rng.randint(*offsets), hours=rng.randint(0, 23))
    submitted = due + delta

    attempt = run.attempt
    attempt.submitted_at = submitted
    attempt.late_seconds = max(0, int((submitted - due).total_seconds()))
    run.started_at = submitted
    run.finished_at = submitted + timedelta(seconds=1)
    for observation in session.query(ConceptObservation).filter(
        ConceptObservation.run_id == run.id
    ):
        observation.observed_at = run.finished_at
    session.flush()


def seed_faculty_review(session: Session, course: Course, faculty: User) -> dict:
    """A handful of realistic faculty actions, so the platform has override
    history, an appeal, and a labelled misconception cluster on first launch."""
    from .models import MisconceptionCluster

    queue = grading_service.review_queue(session, course.id, limit=6)
    overrides = 0
    for entry in queue[:3]:
        contested = entry["contested_items"]
        if not contested:
            continue
        item = contested[0]
        # A realistic partial-credit correction rather than a rubber stamp.
        adjusted = min(1.0, round(item["score"] + 0.25, 2))
        try:
            grading_service.override_item(
                session,
                entry["run_id"],
                item["item_key"],
                faculty.id,
                adjusted,
                reason=(
                    "The approach is right and the failure is a single boundary condition, so the "
                    "item earns partial credit rather than zero."
                ),
            )
            overrides += 1
        except (LookupError, ValueError):
            continue

    appeals = 0
    remaining = grading_service.review_queue(session, course.id, limit=4)
    for entry in remaining[:1]:
        if not entry["contested_items"]:
            continue
        grading_service.open_appeal(
            session,
            entry["run_id"],
            entry["student_id"],
            entry["contested_items"][0]["item_key"],
            "My solution handles the empty case in the caller, so I think this item should not be zero.",
        )
        appeals += 1

    known_labels = {
        "c_bounds_check": "Assumes the input is non-empty",
        "c_defensive_prog": "Assumes the input is non-empty",
        "c_binary_search": "Reports index 0 for an absent value",
        "c_search_correctness": "Reports index 0 for an absent value",
        "c_recursion_basics": "Recurses into the first branch only",
        "c_recursion_depth": "Recurses into the first branch only",
        "c_tree_traversal": "Recurses into the first branch only",
        "c_tree_structure": "Recurses into the first branch only",
        "c_complexity": "Correct answer by a quadratic route",
        "c_hash_concept": "Counts by rescanning instead of using a map",
        "c_dict_usage": "Counts by rescanning instead of using a map",
        "c_frequency_counting": "Counts by rescanning instead of using a map",
        "c_comparison_sort": "Inner loop stops one element short",
        "c_sort_invariants": "Inner loop stops one element short",
        "c_loops": "Inner loop stops one element short",
        "c_list_indexing": "Inner loop stops one element short",
    }
    labelled = 0
    clusters = sorted(
        session.scalars(
            select(MisconceptionCluster).where(MisconceptionCluster.course_id == course.id)
        ),
        key=lambda c: -c.size,
    )
    # An instructor names the big ones first and leaves the long tail alone,
    # which is what the screen actually looks like in a real second week.
    for cluster in clusters[:3]:
        if cluster.label:
            continue
        for key in cluster.concept_keys or []:
            if key in known_labels:
                cluster.label = known_labels[key]
                break
        if not cluster.label:
            continue
        cluster.named_by = faculty.id
        labelled += 1

    session.flush()
    return {"overrides": overrides, "appeals": appeals, "clusters_labelled": labelled}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def seed_all(session: Session, progress=None) -> str:
    course, students, faculty, admin = seed_structure(session)
    assignments = seed_assignments(session, course, faculty)
    stats = seed_submissions(session, course, students, assignments, progress=progress)
    session.flush()

    # L1 -> L2 -> L3.
    analytics_service.refresh_course_analytics(session, course.id)
    review = seed_faculty_review(session, course, faculty)
    analytics_service.refresh_course_analytics(session, course.id)
    metrics_service.train_confidence_model(session)
    session.flush()

    return (
        f"{course.code}: {len(students)} students, {len(assignments)} assignments, "
        f"{stats['submitted']} submissions ({stats['released']} released, {stats['escalated']} escalated, "
        f"{stats['skipped']} not submitted), {review['overrides']} faculty overrides, "
        f"{review['clusters_labelled']} misconception clusters named"
    )
