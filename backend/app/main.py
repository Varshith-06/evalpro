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
        from .seed import ensure_seeded

        with session_scope() as session:
            summary = ensure_seeded(session)
        if summary:
            logger.info("Demo data: %s", summary)
    yield


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

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
