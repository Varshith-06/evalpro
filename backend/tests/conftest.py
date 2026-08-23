"""Test-session setup.

``app.config`` reads the database URL once, at import. Whichever module imported
it first therefore decided where every test wrote — and when the earliest module
happened not to set the variable at all, the whole suite quietly ran against the
developer's real demo database. That is how a passing suite starts depending on
what the last run left behind: the content-hash cache is doing its job, but the
"is this a fresh submission" assertion sees a hit from an hour ago.

pytest imports ``conftest.py`` before it collects anything, so this is the one
place that can set it for everybody. One fresh directory per session, removed
afterwards.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="evalpro-tests-"))
os.environ["EVALPRO_VAR"] = str(_TMP)
os.environ["EVALPRO_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["EVALPRO_DEMO"] = "0"


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001 - pytest hook
    shutil.rmtree(_TMP, ignore_errors=True)
