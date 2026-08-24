"""Course, assignment, submission, and evidence endpoints.

Shared by all three roles. Role-specific landing views live in the sibling
routers, each answering exactly one question.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_session
from ..engine.b0_ingest import IngestError, ingest_archive, ingest_files
from ..engine.sandbox import describe_isolation
from ..models import (
    Assignment,
    AssignmentVersion,
    Concept,
    Course,
    CourseOutcome,
    Enrollment,
    Role,
    RubricItem,
    Submission,
    SubmissionAttempt,
    TestCase,
    User,
    Verdict,
)
from ..services import authoring_service, grading_service

router = APIRouter(prefix="/api", tags=["core"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class SubmitRequest(BaseModel):
    assignment_id: str
    student_id: str
    files: dict[str, str] = Field(..., description="relative path -> source text")
    report_text: str = ""
    force_full_run: bool = Field(
        False,
        description="Run the hidden test set too. Faculty-only; ignored for pre-deadline student feedback.",
    )
    wait: bool = Field(
        True,
        description=(
            "Wait for the grading queue to finish and return the marked result. "
            "Set false at a deadline to get a job ticket back immediately and poll "
            "/api/jobs/{job_id}."
        ),
    )


class AppealRequest(BaseModel):
    student_id: str
    item_key: str
    reason: str


class DraftRequest(BaseModel):
    brief: str
    reference_solution: str
    entry_point: str = "solution.py"
    entry_call: str = "solve"
    created_by: str | None = None


class ApproveRequest(BaseModel):
    faculty_id: str
    edits: list[dict] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Courses
# --------------------------------------------------------------------------
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "isolation": describe_isolation()["applied_count"]}


@router.get("/courses")
def list_courses(session: Session = Depends(get_session)) -> list[dict]:
    courses = session.scalars(select(Course).order_by(Course.code)).all()
    out = []
    for course in courses:
        students = session.scalars(
            select(Enrollment).where(
                Enrollment.course_id == course.id, Enrollment.role == Role.STUDENT
            )
        ).all()
        out.append(
            {
                "id": course.id,
                "code": course.code,
                "title": course.title,
                "term": course.term,
                "language": course.language,
                "students": len(students),
                "concepts": len(course.concepts),
                "assignments": len(course.assignments),
            }
        )
    return out


@router.get("/courses/{course_id}")
def get_course(course_id: str, session: Session = Depends(get_session)) -> dict:
    course = session.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "course not found")
    return {
        "id": course.id,
        "code": course.code,
        "title": course.title,
        "term": course.term,
        "outcomes": [
            {
                "code": o.code,
                "text": o.text,
                "programme_outcomes": o.programme_outcomes,
                "po_weights": o.po_weights,
            }
            for o in sorted(course.outcomes, key=lambda o: o.code)
        ],
        "assignments": [
            {"id": a.id, "code": a.code, "title": a.title, "due_at": a.due_at.isoformat() if a.due_at else None}
            for a in course.assignments
        ],
    }


@router.get("/courses/{course_id}/concepts")
def concept_graph(course_id: str, session: Session = Depends(get_session)) -> dict:
    """The spine, in a shape a force-directed layout can render directly."""
    concepts = session.scalars(
        select(Concept).where(Concept.course_id == course_id).order_by(Concept.syllabus_week, Concept.name)
    ).all()
    nodes = [
        {
            "id": c.concept_key,
            "name": c.name,
            "description": c.description,
            "bloom": c.bloom_level.value,
            "outcomes": c.course_outcomes,
            "misconceptions": c.typical_misconceptions,
            "resources": c.resources,
            "week": c.syllabus_week,
        }
        for c in concepts
    ]
    keys = {c.concept_key for c in concepts}
    edges = [
        {"from": prerequisite, "to": c.concept_key}
        for c in concepts
        for prerequisite in (c.prerequisites or [])
        if prerequisite in keys
    ]
    return {"nodes": nodes, "edges": edges}


@router.get("/courses/{course_id}/staff")
def list_staff(course_id: str, session: Session = Depends(get_session)) -> list[dict]:
    """Faculty and administrators on this course.

    A real deployment gets the acting identity from the LTI launch; this exists
    so the demo can attribute an override to a real person rather than a
    placeholder, which matters because overrides are training data.
    """
    rows = session.execute(
        select(User, Enrollment)
        .join(Enrollment, Enrollment.user_id == User.id)
        .where(Enrollment.course_id == course_id, Enrollment.role != Role.STUDENT)
        .order_by(User.name)
    ).all()
    return [
        {"id": user.id, "name": user.name, "role": user.role.value}
        for user, _enrollment in rows
    ]


@router.get("/courses/{course_id}/students")
def list_students(course_id: str, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(
        select(User, Enrollment)
        .join(Enrollment, Enrollment.user_id == User.id)
        .where(Enrollment.course_id == course_id, Enrollment.role == Role.STUDENT)
        .order_by(User.name)
    ).all()
    return [
        {"id": user.id, "name": user.name, "external_id": user.external_id, "section": enrollment.section}
        for user, enrollment in rows
    ]


# --------------------------------------------------------------------------
# Assignments and authoring
# --------------------------------------------------------------------------
@router.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: str, session: Session = Depends(get_session)) -> dict:
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "assignment not found")
    version = assignment.active_version
    return {
        "id": assignment.id,
        "code": assignment.code,
        "title": assignment.title,
        "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
        "requires_report": assignment.requires_report,
        "versions": [
            {
                "id": v.id,
                "version": v.version,
                "approved": v.approved_at is not None,
                "approved_at": v.approved_at.isoformat() if v.approved_at else None,
                "drafted_by_model": v.drafted_by_model,
                "authoring_edits": v.authoring_edits,
            }
            for v in assignment.versions
        ],
        "active_version": _version_payload(session, version) if version else None,
    }


def _version_payload(session: Session, version: AssignmentVersion) -> dict:
    items = session.scalars(
        select(RubricItem).where(RubricItem.version_id == version.id).order_by(RubricItem.ordinal)
    ).all()
    tests = session.scalars(
        select(TestCase).where(TestCase.version_id == version.id).order_by(TestCase.ordinal)
    ).all()
    return {
        "id": version.id,
        "version": version.version,
        "spec_text": version.spec_text,
        "entry_point": version.entry_point,
        "approved": version.approved_at is not None,
        "rubric": [
            {
                "item_key": i.item_key,
                "text": i.text,
                "category": i.category,
                "weight": i.weight,
                "concept_ids": i.concept_ids,
                "checkable_by": i.checkable_by,
                "test_ids": i.test_ids,
                "static_check": i.static_check,
            }
            for i in items
        ],
        "tests": [
            {
                "test_key": t.test_key,
                "category": t.category.value,
                "weight": t.weight,
                "hidden": t.hidden,
                "property_spec": t.property_spec,
                "admitted": t.validated_against_reference,
                "validation_note": t.validation_note,
            }
            for t in tests
        ],
        "admitted_tests": sum(1 for t in tests if t.validated_against_reference),
        "discarded_tests": sum(1 for t in tests if not t.validated_against_reference),
    }


@router.post("/assignments/{assignment_id}/draft")
def draft_version(
    assignment_id: str, payload: DraftRequest, session: Session = Depends(get_session)
) -> dict:
    """A1-A3. Produces an unapproved version. Nothing grades until A4."""
    assignment = session.get(Assignment, assignment_id)
    if assignment is None:
        raise HTTPException(404, "assignment not found")
    result = authoring_service.draft_assignment_version(
        session,
        assignment,
        payload.brief,
        payload.reference_solution,
        payload.entry_point,
        payload.entry_call,
        created_by=payload.created_by,
    )
    session.commit()
    return {
        "version": _version_payload(session, result.version),
        "schema_repairs": result.repairs,
        "reference_validation": {
            "admitted": result.validation.admitted,
            "discarded": result.validation.discarded,
            "failure_rate": round(result.validation.failure_rate, 3),
            "halted": result.halted,
            "message": result.message,
        },
    }


@router.post("/versions/{version_id}/approve")
def approve(version_id: str, payload: ApproveRequest, session: Session = Depends(get_session)) -> dict:
    version = session.get(AssignmentVersion, version_id)
    if version is None:
        raise HTTPException(404, "version not found")
    if payload.edits:
        authoring_service.apply_faculty_edits(session, version, payload.edits)
    try:
        authoring_service.approve_version(session, version, payload.faculty_id, payload.edits)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return {"version_id": version.id, "approved_at": version.approved_at.isoformat()}


# --------------------------------------------------------------------------
# Submission and evidence
# --------------------------------------------------------------------------
def _raise_for_job(job) -> None:
    """Map a failure that happened on a queue worker onto its HTTP status."""
    exc = job.exception
    if isinstance(exc, grading_service.SubmissionRejected):
        raise HTTPException(429 if "Rate limit" in str(exc) else 409, str(exc)) from exc
    if isinstance(exc, IngestError):
        raise HTTPException(400, f"Ingest rejected the bundle: {exc}") from exc
    raise HTTPException(500, job.error or "Grading failed.")


def _ticket(job, queue) -> dict:
    """What a caller gets when the work is still in flight."""
    stats = queue.stats()
    ahead = queue.position(job.id)
    return {
        **job.as_dict(),
        "position": ahead,
        "queue_depth": stats["depth"],
        "running": stats["running"],
        "workers": stats["workers"],
    }


@router.post("/submit")
def submit(payload: SubmitRequest, session: Session = Depends(get_session)) -> dict:
    """Accept a submission. Grading happens on the queue, never in this request.

    By default the request waits for its own job so callers see the marked
    result exactly as before. At a deadline a client should pass
    ``wait: false``, take the ticket, and poll - which is the difference between
    a slow page and a browser timeout.
    """
    from ..services import queue_service

    queue = queue_service.get_queue()
    try:
        job = grading_service.submit_queued(
            payload.assignment_id,
            payload.student_id,
            payload.files,
            payload.report_text,
            visible_only=False if payload.force_full_run else None,
            label=f"student {payload.student_id[:8]}",
        )
    except queue_service.QueueFull as exc:
        raise HTTPException(503, str(exc)) from exc

    if not payload.wait:
        return JSONResponse(status_code=202, content=_ticket(job, queue))

    if not job.wait(timeout=settings.queue.wait_timeout_s):
        # Still running. Hand back the ticket rather than holding the socket
        # open indefinitely; the work is not lost and the client can poll.
        return JSONResponse(status_code=202, content=_ticket(job, queue))

    if job.state == queue_service.FAILED:
        _raise_for_job(job)

    result = dict(job.result)                      # type: ignore[arg-type]
    # The worker committed on its own session; start a fresh transaction here so
    # this one is not reading a snapshot from before that commit.
    session.rollback()
    return {
        **result,
        "job_id": job.id,
        "queued_ms": job.waited_ms,
        "graded_ms": job.ran_ms,
        "detail": grading_service.run_detail(session, result["run_id"]),
    }


@router.get("/jobs/{job_id}")
def job_status(job_id: str, session: Session = Depends(get_session)) -> dict:
    """Poll a submission that was accepted with ``wait: false``."""
    from ..services import queue_service

    queue = queue_service.get_queue()
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(404, "No such job. Tickets are kept for the recent past only.")
    if job.state == queue_service.FAILED:
        _raise_for_job(job)
    if job.state != queue_service.DONE:
        return _ticket(job, queue)

    result = dict(job.result)                      # type: ignore[arg-type]
    session.rollback()
    return {
        **result,
        "job_id": job.id,
        "state": job.state,
        "queued_ms": job.waited_ms,
        "graded_ms": job.ran_ms,
        "detail": grading_service.run_detail(session, result["run_id"]),
    }


@router.get("/queue")
def queue_stats() -> dict:
    """What the grading queue is doing right now."""
    from ..services import queue_service

    return queue_service.get_queue().stats()


@router.post("/submit/upload")
async def submit_upload(
    assignment_id: str = Form(...),
    student_id: str = Form(...),
    report_text: str = Form(""),
    force_full_run: bool = Form(False),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    """Submit by uploading a file instead of pasting code.

    A ``.zip`` goes through the same B0 limits as everything else - entry
    count, uncompressed size, nesting depth, compression ratio, and entry-name
    validation - because an upload form is exactly where a decompression bomb
    arrives. A single source file is accepted directly.
    """
    blob = await file.read()
    name = (file.filename or "solution.py").replace("\\", "/").split("/")[-1]

    try:
        if name.lower().endswith(".zip") or blob[:2] == b"PK":
            ingested = ingest_archive(blob)
            files, report = ingested.files, ingested.report_text
        else:
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(400, "That file is not text. Upload source code or a .zip.")
            ingested = ingest_files({name: text})
            files, report = ingested.files, ingested.report_text
    except IngestError as exc:
        raise HTTPException(400, f"Upload rejected: {exc}") from exc

    try:
        attempt, run, from_cache = grading_service.submit(
            session, assignment_id, student_id, files,
            report_text or report, visible_only=False if force_full_run else None,
        )
    except grading_service.SubmissionRejected as exc:
        raise HTTPException(429 if "Rate limit" in str(exc) else 409, str(exc)) from exc
    session.commit()
    return {
        "attempt_id": attempt.id,
        "run_id": run.id,
        "from_cache": from_cache,
        "files": sorted(files),
        "detail": grading_service.run_detail(session, run.id),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return grading_service.run_detail(session, run_id)
    except LookupError as exc:
        raise HTTPException(404, "run not found") from exc


@router.post("/runs/{run_id}/appeal")
def appeal(run_id: str, payload: AppealRequest, session: Session = Depends(get_session)) -> dict:
    """One-click appeal on a specific rubric item, with the full evidence trail."""
    appeal_row = grading_service.open_appeal(
        session, run_id, payload.student_id, payload.item_key, payload.reason
    )
    session.commit()
    return {"appeal_id": appeal_row.id, "state": appeal_row.state.value}
