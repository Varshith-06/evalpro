"""EvalPro application entry point.

Serves the API and the build-free frontend from one process, so a demo is
``python -m app.main`` and nothing else.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import admin, core, faculty, student
from .config import PIPELINE_VERSION, REPO_DIR, settings
from .db import init_db, session_scope
from .engine.b0_ingest import IngestError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("evalpro")

FRONTEND_DIR = REPO_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("EvalPro %s ready (pipeline %s)", __version__, PIPELINE_VERSION)
    if settings.demo_mode:
        from .seed import already_seeded, seed_all

        with session_scope() as session:
            if not already_seeded(session):
                # First run grades a whole cohort through the real cascade, which
                # takes over a minute. Say so, and keep saying so: a silent
                # console for ninety seconds looks like a hang.
                logger.info(
                    "First run: building the demo course by marking ~90 submissions through the "
                    "real pipeline. This takes about 80 seconds and only happens once."
                )
                seen: set[str] = set()

                def progress(code: str, _student: str, _state: str) -> None:
                    if code not in seen:
                        seen.add(code)
                        logger.info("  marking %s...", code)

                logger.info("Demo data: %s", seed_all(session, progress=progress))

    from .services.queue_service import get_queue, shutdown_queue

    get_queue()
    try:
        yield
    finally:
        # Drain rather than drop: a submission that was accepted has been
        # promised to a student, and losing it on shutdown is worse than
        # waiting a few seconds for it.
        shutdown_queue(drain=True)


app = FastAPI(
    title="EvalPro - Automated Programming Lab Evaluation Platform",
    description=(
        "An academic analytics platform whose sensor is an automated grader. "
        "Every submission is evidence about named competencies that accumulate across a course."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core.router)
app.include_router(student.router)
app.include_router(faculty.router)
app.include_router(admin.router)


@app.exception_handler(IngestError)
async def ingest_error_handler(_request, exc: IngestError):
    # Ingest limits are hard and their messages are the explanation a student
    # needs, so they are surfaced rather than flattened into a 500.
    return JSONResponse(status_code=400, content={"detail": f"Ingest rejected the bundle: {exc}"})


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    # The front end is plain files with no build step and no content hashing, so
    # a browser that caches app.js will happily keep running last week's UI
    # after the server has been updated - and the only symptom is that a feature
    # someone was told about is not there. These files are a few kilobytes;
    # revalidating them every time costs nothing and removes a whole class of
    # "it works on my machine" confusion.
    @app.middleware("http")
    async def no_store_for_the_app_shell(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(
            str(FRONTEND_DIR / "index.html"),
            headers={"Cache-Control": "no-store, must-revalidate"},
        )


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
