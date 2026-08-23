"""B3 - Build.

Compilation *is* code execution. Makefile recipes, proc macros, preprocessor
includes, ``npm postinstall``, ``setup.py`` -- all of it runs arbitrary code
before a single test does. So the build gets the same sandbox as execution,
with a shorter budget, a different filesystem shape, and **no network**:
dependencies come from a pre-vendored, pinned, offline mirror. An egress
allowlist for PyPI is an exfiltration channel with extra steps.

Build and run are separately sandboxed so that a build compromise does not
inherit an execution environment.

Failure yields structured diagnostics ``(file, line, column, code, message)``
that go straight to B5, where they become the starting point for repair-distance
search rather than a zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SandboxLimits, settings
from .b1_structure import CodeGraph
from .sandbox import Sandbox, SandboxJob


@dataclass
class BuildResult:
    ok: bool
    diagnostics: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    toolchain: str = "cpython-compile"
    stderr: str = ""

    def summary(self) -> str:
        if self.ok:
            return f"build succeeded ({self.toolchain})"
        first = self.diagnostics[0] if self.diagnostics else {}
        return (
            f"build failed: {first.get('code', 'error')} at "
            f"{first.get('file', '?')}:{first.get('line', '?')} - {first.get('message', '')}"
        )


# The build "program" for Python: compile every module without importing it.
# ``compile`` does not execute module-level code, which is exactly the property
# we want at this stage -- execution belongs to B4, under its own budget.
_BUILD_DRIVER = '''
import json, sys, os

def solve():
    results = []
    ok = True
    root = os.path.dirname(os.path.abspath(__file__))
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            if not name.endswith(".py") or name.startswith("__evalpro"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as handle:
                    source = handle.read()
                compile(source, rel, "exec")
            except SyntaxError as exc:
                ok = False
                results.append({
                    "file": rel,
                    "line": exc.lineno or 0,
                    "column": exc.offset or 0,
                    "code": type(exc).__name__,
                    "message": exc.msg or str(exc),
                    "text": (exc.text or "").strip()[:200],
                })
            except Exception as exc:
                ok = False
                results.append({
                    "file": rel, "line": 0, "column": 0,
                    "code": type(exc).__name__, "message": str(exc)[:400], "text": "",
                })
    return {"ok": ok, "diagnostics": results}
'''


def build(
    files: dict[str, str],
    graph: CodeGraph,
    sandbox: Sandbox,
    limits: SandboxLimits | None = None,
) -> BuildResult:
    """Compile the bundle inside the sandbox and return structured diagnostics."""
    limits = limits or settings.sandbox
    build_limits = SandboxLimits(
        cpu_seconds=limits.build_cpu_seconds,
        wall_seconds=limits.build_wall_seconds,
        memory_bytes=limits.memory_bytes,
        max_processes=limits.max_processes,
        max_file_bytes=limits.max_file_bytes,
        max_open_files=limits.max_open_files,
        stdout_cap_bytes=limits.stdout_cap_bytes,
        stderr_cap_bytes=limits.stderr_cap_bytes,
    )

    payload = dict(files)
    payload["__evalpro_build__.py"] = _BUILD_DRIVER
    job = SandboxJob(
        test_key="__build__",
        files=payload,
        entry_point="__evalpro_build__.py",
        call="solve",
        args=[],
        limits=build_limits,
    )
    result = sandbox.run(job)

    if result.status != "ok" or not result.value:
        # The driver itself failing is a system condition, not a student one.
        # Fall back to the parse diagnostics B1 already recovered so the run
        # still produces evidence rather than an opaque error.
        diagnostics = list(graph.syntax_errors)
        return BuildResult(
            ok=not diagnostics,
            diagnostics=diagnostics,
            duration_ms=result.wall_ms,
            stderr=result.stderr[:2000],
        )

    import json

    try:
        parsed = json.loads(result.value)
    except (json.JSONDecodeError, TypeError):
        return BuildResult(ok=False, diagnostics=list(graph.syntax_errors), duration_ms=result.wall_ms)

    if isinstance(parsed, str):  # value arrives JSON-encoded from the harness
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            parsed = {"ok": False, "diagnostics": graph.syntax_errors}

    diagnostics = parsed.get("diagnostics", []) or []
    return BuildResult(
        ok=bool(parsed.get("ok")) and not diagnostics,
        diagnostics=diagnostics,
        duration_ms=result.wall_ms,
        stderr=result.stderr[:2000],
    )
