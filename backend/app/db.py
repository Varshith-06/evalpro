"""Database session plumbing."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


IS_SQLITE = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver glue
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        # WAL lets readers run during a write, but writers still serialise.
        # With a pool of grading workers that contention is normal rather than
        # exceptional, so wait for the lock instead of failing on it.
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and background work."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# SQLite permits exactly one writer. A grading transaction stays open for the
# length of the cascade, so concurrent workers do not overlap their writes --
# they queue behind one lock and the losers eventually fail with "database is
# locked". Measured on a 60-submission burst: 5 of 60 lost that way, and even
# the survivors finished at 1.0s each, which is serial anyway.
#
# So on SQLite the writer is made explicit rather than left to a timeout, which
# turns a race into a queue and drops the failure rate to zero. It also states
# the real limit plainly: concurrent *grading* needs a database that supports
# concurrent writers, which is the Postgres path. On any other backend this is
# a no-op and the workers run genuinely in parallel.
_write_lock = threading.Lock()


def grading_write_lock():
    """Serialise write transactions on backends that only allow one writer."""
    return _write_lock if IS_SQLITE else nullcontext()


def init_db() -> None:
    from . import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
