"""The data model of §3.

Two properties are load-bearing and everything else follows from them:

1. **Immutable-and-versioned.** Assignments version their spec; rubrics and test
   suites hang off a version; an EvaluationRun pins the pipeline and model
   versions it used. That is what makes "regrade a year later, get an identical
   result" true rather than aspirational.

2. **Nothing is scoped only to a single assignment.** ``RubricItem.concept_ids``
   is the single field connecting Layer 1 (sensing) to Layer 2 (accumulation).
   Without it the platform is twelve disconnected gradebooks.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex[:16]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------
class Role(str, enum.Enum):
    STUDENT = "student"
    FACULTY = "faculty"
    ADMIN = "admin"


class BloomLevel(str, enum.Enum):
    REMEMBER = "remember"
    UNDERSTAND = "understand"
    APPLY = "apply"
    ANALYSE = "analyse"
    EVALUATE = "evaluate"
    CREATE = "create"


class CheckableBy(str, enum.Enum):
    TEST = "test"
    STATIC = "static"
    STRUCTURAL = "structural"
    REPORT = "report"
    MANUAL = "manual"


class TestCategory(str, enum.Enum):
    SMOKE = "smoke"
    BASIC = "basic"
    EDGE = "edge"
    STRESS = "stress"
    PROPERTY = "property"


class StageName(str, enum.Enum):
    INGEST = "B0_ingest"
    STRUCTURE = "B1_structure"
    INTEGRITY = "B2_integrity"
    BUILD = "B3_build"
    EXECUTE = "B4_execute"
    PARTIAL_CREDIT = "B5_partial_credit"
    REPORT_CHECK = "B6_report_check"
    GATE = "B7_gate"


class StageStatus(str, enum.Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    SKIPPED = "skipped"


class TestOutcome(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    CRASH = "crash"
    OOM = "oom"
    SKIPPED = "skipped"


class VerdictState(str, enum.Enum):
    RELEASED = "released"
    ESCALATED = "escalated"
    PENDING = "pending"
    OVERRIDDEN = "overridden"


class EscalationReason(str, enum.Enum):
    SIGNAL_CONFLICT = "signal_conflict"
    LOW_CONFIDENCE = "low_confidence"
    INTEGRITY_FLAG = "integrity_flag"
    REPORT_CONTRADICTION = "report_contradiction"
    GRADE_BOUNDARY = "grade_boundary"
    REPAIR_MATERIAL = "repair_material"
    STAGE_ERROR = "stage_error"
    APPEAL = "appeal"


class AppealState(str, enum.Enum):
    OPEN = "open"
    UPHELD = "upheld"
    REJECTED = "rejected"


# --------------------------------------------------------------------------
# Layer 0 - institutional context
# --------------------------------------------------------------------------
class Institution(Base):
    __tablename__ = "institution"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    lms_kind: Mapped[str | None] = mapped_column(String(40), default=None)
    lti_client_id: Mapped[str | None] = mapped_column(String(120), default=None)

    courses: Mapped[list[Course]] = relationship(back_populates="institution")


class User(Base):
    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(200), default=None)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.STUDENT)
    # Held only for the §7.3 bias audit; never a model feature.
    protected_attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="user")


class Course(Base):
    __tablename__ = "course"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    institution_id: Mapped[str | None] = mapped_column(ForeignKey("institution.id"), default=None)
    code: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    term: Mapped[str] = mapped_column(String(40), default="")
    language: Mapped[str] = mapped_column(String(24), default="python")

    institution: Mapped[Institution | None] = relationship(back_populates="courses")
    concepts: Mapped[list[Concept]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    outcomes: Mapped[list[CourseOutcome]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    enrollments: Mapped[list[Enrollment]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )


class CourseOutcome(Base):
    __tablename__ = "course_outcome"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    code: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    programme_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    po_weights: Mapped[dict] = mapped_column(JSON, default=dict)

    course: Mapped[Course] = relationship(back_populates="outcomes")


class Enrollment(Base):
    __tablename__ = "enrollment"
    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_enrollment"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    section: Mapped[str] = mapped_column(String(24), default="A")
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.STUDENT)
    synced_from: Mapped[str] = mapped_column(String(32), default="seed")

    course: Mapped[Course] = relationship(back_populates="enrollments")
    user: Mapped[User] = relationship(back_populates="enrollments")


# --------------------------------------------------------------------------
# §3.1 The concept graph - the spine
# --------------------------------------------------------------------------
class Concept(Base):
    __tablename__ = "concept"
    __table_args__ = (UniqueConstraint("course_id", "concept_key", name="uq_concept_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    concept_key: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    bloom_level: Mapped[BloomLevel] = mapped_column(Enum(BloomLevel), default=BloomLevel.APPLY)
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)
    course_outcomes: Mapped[list] = mapped_column(JSON, default=list)
    typical_misconceptions: Mapped[list] = mapped_column(JSON, default=list)
    # Instructor-supplied remediation targets, consumed by §7.1.
    resources: Mapped[list] = mapped_column(JSON, default=list)
    syllabus_week: Mapped[int | None] = mapped_column(Integer, default=None)

    course: Mapped[Course] = relationship(back_populates="concepts")


# --------------------------------------------------------------------------
# §3.2 Assessment entities
# --------------------------------------------------------------------------
class Assignment(Base):
    __tablename__ = "assignment"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"))
    code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    opens_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10)
    requires_report: Mapped[bool] = mapped_column(Boolean, default=False)
    lti_resource_link_id: Mapped[str | None] = mapped_column(String(120), default=None)

    course: Mapped[Course] = relationship(back_populates="assignments")
    versions: Mapped[list[AssignmentVersion]] = relationship(
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="AssignmentVersion.version",
    )
    submissions: Mapped[list[Submission]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )

    @property
    def active_version(self) -> AssignmentVersion | None:
        approved = [v for v in self.versions if v.approved_at is not None]
        if approved:
            return approved[-1]
        return self.versions[-1] if self.versions else None


class AssignmentVersion(Base):
    __tablename__ = "assignment_version"
    __table_args__ = (UniqueConstraint("assignment_id", "version", name="uq_assignment_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignment.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    spec_text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(24), default="python")
    entry_point: Mapped[str] = mapped_column(String(120), default="solution.py")
    reference_solution: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("user.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("user.id"), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # A4 draft provenance: which model drafted this, and what faculty changed.
    drafted_by_model: Mapped[str | None] = mapped_column(String(64), default=None)
    authoring_edits: Mapped[list] = mapped_column(JSON, default=list)
    # How this version can be graded, which follows from what the instructor
    # supplied rather than from a setting. Without a reference solution no test
    # can be validated, so no test may be admitted, so the oracle does not
    # exist - and the platform grades the approach instead of the output.
    grading_mode: Mapped[str] = mapped_column(String(24), default="executable")
    # Which parts the platform generated because the instructor left them blank.
    # Surfaced on the review screen: a generated rubric deserves more scrutiny
    # than one a human wrote.
    generated_parts: Mapped[list] = mapped_column(JSON, default=list)

    assignment: Mapped[Assignment] = relationship(back_populates="versions")
    rubric_items: Mapped[list[RubricItem]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="RubricItem.ordinal"
    )
    test_cases: Mapped[list[TestCase]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="TestCase.ordinal"
    )


class RubricItem(Base):
    """The unit of evidence. ``concept_ids`` is what makes Layer 2 possible."""

    __tablename__ = "rubric_item"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("assignment_version.id"))
    item_key: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="correctness")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    checkable_by: Mapped[list] = mapped_column(JSON, default=list)
    test_ids: Mapped[list] = mapped_column(JSON, default=list)
    static_check: Mapped[dict | None] = mapped_column(JSON, default=None)
    auto_gradeable: Mapped[bool] = mapped_column(Boolean, default=True)

    version: Mapped[AssignmentVersion] = relationship(back_populates="rubric_items")


class TestCase(Base):
    __tablename__ = "test_case"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(ForeignKey("assignment_version.id"))
    test_key: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[TestCategory] = mapped_column(Enum(TestCategory), default=TestCategory.BASIC)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    call: Mapped[str] = mapped_column(String(120), default="solve")
    setup: Mapped[str] = mapped_column(Text, default="")
    # Fixed IO tests carry ``args``; property tests carry a generator + predicate
    # evaluated on the host (B4). The expected output NEVER enters the sandbox.
    args: Mapped[list | None] = mapped_column(JSON, default=None)
    expected_output: Mapped[str | None] = mapped_column(Text, default=None)
    property_spec: Mapped[dict | None] = mapped_column(JSON, default=None)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    validated_against_reference: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_note: Mapped[str] = mapped_column(Text, default="")

    version: Mapped[AssignmentVersion] = relationship(back_populates="test_cases")


class Submission(Base):
    __tablename__ = "submission"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignment.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    attempts: Mapped[list[SubmissionAttempt]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionAttempt.attempt_no",
    )


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempt"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submission.id"))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    artifacts_uri: Mapped[str] = mapped_column(String(400), default="")
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    report_text: Mapped[str] = mapped_column(Text, default="")
    late_seconds: Mapped[int] = mapped_column(Integer, default=0)

    submission: Mapped[Submission] = relationship(back_populates="attempts")
    runs: Mapped[list[EvaluationRun]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan", order_by="EvaluationRun.started_at"
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_run"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("submission_attempt.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("assignment_version.id"))
    pipeline_version: Mapped[str] = mapped_column(String(24))
    model_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cached_from_run_id: Mapped[str | None] = mapped_column(String(32), default=None)
    visible_only: Mapped[bool] = mapped_column(Boolean, default=False)
    code_graph: Mapped[dict] = mapped_column(JSON, default=dict)

    attempt: Mapped[SubmissionAttempt] = relationship(back_populates="runs")
    stage_results: Mapped[list[StageResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="StageResult.ordinal"
    )
    item_scores: Mapped[list[RubricItemScore]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    test_results: Mapped[list[TestResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    verdict: Mapped[Verdict | None] = relationship(
        back_populates="run", uselist=False, cascade="all, delete-orphan"
    )


class StageResult(Base):
    __tablename__ = "stage_result"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[StageName] = mapped_column(Enum(StageName))
    status: Mapped[StageStatus] = mapped_column(Enum(StageStatus), default=StageStatus.OK)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped[EvaluationRun] = relationship(back_populates="stage_results")


class TestResult(Base):
    __tablename__ = "test_result"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    test_key: Mapped[str] = mapped_column(String(32))
    category: Mapped[TestCategory] = mapped_column(Enum(TestCategory), default=TestCategory.BASIC)
    outcome: Mapped[TestOutcome] = mapped_column(Enum(TestOutcome), default=TestOutcome.FAIL)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    on_repaired_source: Mapped[bool] = mapped_column(Boolean, default=False)
    actual: Mapped[str] = mapped_column(Text, default="")
    expected: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    cpu_ms: Mapped[int] = mapped_column(Integer, default=0)
    wall_ms: Mapped[int] = mapped_column(Integer, default=0)
    peak_memory_kb: Mapped[int] = mapped_column(Integer, default=0)
    exit_code: Mapped[int | None] = mapped_column(Integer, default=None)
    stderr_excerpt: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[EvaluationRun] = relationship(back_populates="test_results")


class RubricItemScore(Base):
    """One rubric item's worth of evidence. Carries the concept tags forward."""

    __tablename__ = "rubric_item_score"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    item_key: Mapped[str] = mapped_column(String(32))
    concept_ids: Mapped[list] = mapped_column(JSON, default=list)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    score_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    signal_agreement: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    faculty_score_fraction: Mapped[float | None] = mapped_column(Float, default=None)
    faculty_reason: Mapped[str | None] = mapped_column(Text, default=None)

    run: Mapped[EvaluationRun] = relationship(back_populates="item_scores")


class Verdict(Base):
    __tablename__ = "verdict"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"), unique=True)
    total_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    total_points: Mapped[float] = mapped_column(Float, default=0.0)
    max_points: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[VerdictState] = mapped_column(Enum(VerdictState), default=VerdictState.PENDING)
    escalation_reasons: Mapped[list] = mapped_column(JSON, default=list)
    integrity_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    syntax_penalty: Mapped[float] = mapped_column(Float, default=0.0)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("user.id"), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    override_reason: Mapped[str | None] = mapped_column(Text, default=None)
    gradebook_synced_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    run: Mapped[EvaluationRun] = relationship(back_populates="verdict")


# --------------------------------------------------------------------------
# §3.3 Longitudinal state
# --------------------------------------------------------------------------
class ConceptObservation(Base):
    """(student, concept, score_fraction, confidence, evidence_refs, timestamp).

    Only auto-released or faculty-confirmed scores are written here - never
    build mastery on evidence the system itself flagged as uncertain (§6.1).
    """

    __tablename__ = "concept_observation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    concept_key: Mapped[str] = mapped_column(String(64), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignment.id"))
    item_key: Mapped[str] = mapped_column(String(32))
    score_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_source: Mapped[str] = mapped_column(String(32), default="test")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StudentConceptMastery(Base):
    __tablename__ = "student_concept_mastery"
    __table_args__ = (
        UniqueConstraint("student_id", "concept_key", "course_id", name="uq_mastery"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    concept_key: Mapped[str] = mapped_column(String(64), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"), index=True)
    mastery_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    uncertainty: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    trajectory: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(24), default="bkt")


class StudentRiskState(Base):
    __tablename__ = "student_risk_state"
    __table_args__ = (UniqueConstraint("student_id", "course_id", name="uq_risk"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("user.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"), index=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    contributing_factors: Mapped[list] = mapped_column(JSON, default=list)
    first_flagged_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    routed_to: Mapped[str] = mapped_column(String(40), default="advising")


# --------------------------------------------------------------------------
# §3.4 Item quality + §6.4 misconceptions
# --------------------------------------------------------------------------
class RubricItemStats(Base):
    __tablename__ = "rubric_item_stats"
    __table_args__ = (UniqueConstraint("item_key", "cohort_id", name="uq_item_stats"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    item_key: Mapped[str] = mapped_column(String(32), index=True)
    cohort_id: Mapped[str] = mapped_column(String(64), index=True)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignment.id"))
    n: Mapped[int] = mapped_column(Integer, default=0)
    difficulty: Mapped[float] = mapped_column(Float, default=0.0)
    discrimination: Mapped[float] = mapped_column(Float, default=0.0)
    concept_alignment: Mapped[float] = mapped_column(Float, default=0.0)
    flag: Mapped[str] = mapped_column(String(32), default="ok")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MisconceptionCluster(Base):
    __tablename__ = "misconception_cluster"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    course_id: Mapped[str] = mapped_column(ForeignKey("course.id"), index=True)
    assignment_id: Mapped[str | None] = mapped_column(ForeignKey("assignment.id"), default=None)
    label: Mapped[str] = mapped_column(String(160), default="")
    auto_signature: Mapped[str] = mapped_column(Text, default="")
    concept_keys: Mapped[list] = mapped_column(JSON, default=list)
    size: Mapped[int] = mapped_column(Integer, default=0)
    member_run_ids: Mapped[list] = mapped_column(JSON, default=list)
    representative_run_id: Mapped[str | None] = mapped_column(String(32), default=None)
    named_by: Mapped[str | None] = mapped_column(ForeignKey("user.id"), default=None)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SimilarityPair(Base):
    """B2 output. A ranked report with aligned regions - never a verdict."""

    __tablename__ = "similarity_pair"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignment.id"), index=True)
    run_id_a: Mapped[str] = mapped_column(String(32))
    run_id_b: Mapped[str] = mapped_column(String(32))
    student_id_a: Mapped[str] = mapped_column(String(32))
    student_id_b: Mapped[str] = mapped_column(String(32))
    token_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    structural_similarity: Mapped[float] = mapped_column(Float, default=0.0)
    combined: Mapped[float] = mapped_column(Float, default=0.0)
    aligned_regions: Mapped[list] = mapped_column(JSON, default=list)
    corpus: Mapped[str] = mapped_column(String(32), default="cohort")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer_note: Mapped[str | None] = mapped_column(Text, default=None)


class Appeal(Base):
    __tablename__ = "appeal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    student_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    item_key: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[AppealState] = mapped_column(Enum(AppealState), default=AppealState.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    resolution_note: Mapped[str | None] = mapped_column(Text, default=None)


class FacultyOverride(Base):
    """Every override is training data for the confidence estimator (§10)."""

    __tablename__ = "faculty_override"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_run.id"))
    item_key: Mapped[str] = mapped_column(String(32))
    faculty_id: Mapped[str] = mapped_column(ForeignKey("user.id"))
    auto_score_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    auto_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    faculty_score_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditEvent(Base):
    """Per-job audit trail (§5.4) and access log for FERPA/GDPR/DPDP."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), default=None)
    subject_id: Mapped[str | None] = mapped_column(String(32), default=None)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
