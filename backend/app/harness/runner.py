"""In-guest harness. Runs INSIDE the sandbox alongside untrusted student code.

Contract, and the reason the whole anti-cheat story holds:

* This file receives a **job manifest** containing the entry module, the
  function to call, and the *inputs*. It never receives an expected output.
  The oracle stays on the host (§5.2). Student code therefore cannot read,
  infer, or hardcode an answer it was never given.
* The harness **owns the result channel**. It writes a single JSON document to
  the path named by ``EVALPRO_RESULT_PATH`` and nothing else is read by the
  host. A student ``print()`` cannot forge a result, and a catch-all
  ``except: pass`` cannot turn a crash into a pass, because the outcome is
  decided by the host from this document plus the exit code.
* Stdlib only. The sandbox base image has no third-party packages and no
  network, so an import of anything else is itself a finding.

Everything here runs untrusted-adjacent. Keep it small and boring.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
import traceback

RESULT_ENV = "EVALPRO_RESULT_PATH"
MANIFEST_ENV = "EVALPRO_MANIFEST_PATH"
MAX_REPR = 16_000


def _safe_repr(value: object) -> str:
    """Canonical, comparable text for a returned value.

    JSON where possible so that host-side comparison is structural rather than
    formatting-sensitive; ``repr`` otherwise, truncated.
    """
    try:
        return json.dumps(value, sort_keys=True, default=str)[:MAX_REPR]
    except Exception:
        try:
            return repr(value)[:MAX_REPR]
        except Exception:
            return "<unrepresentable>"


def _load_module(entry_path: str):
    directory = os.path.dirname(os.path.abspath(entry_path))
    if directory not in sys.path:
        sys.path.insert(0, directory)
    name = os.path.splitext(os.path.basename(entry_path))[0]
    spec = importlib.util.spec_from_file_location(f"student_{name}", entry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {entry_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_callable(module, dotted: str):
    target = module
    for part in dotted.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{dotted} is not callable")
    return target


def main() -> int:
    result_path = os.environ.get(RESULT_ENV)
    manifest_path = os.environ.get(MANIFEST_ENV)
    if not result_path or not manifest_path:
        sys.stderr.write("harness: missing channel configuration\n")
        return 90

    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)

    payload: dict = {
        "harness_version": "1.1",
        "test_key": manifest.get("test_key"),
        "status": "error",
        "value": None,
        "stdout": "",
        "exception": None,
        "cpu_ms": 0,
        "wall_ms": 0,
    }

    # Student stdout is captured, not trusted. It is evidence, never a result.
    captured = io.StringIO()
    real_stdout = sys.stdout
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    try:
        sys.stdout = captured
        module = _load_module(manifest["entry_path"])
        func = _resolve_callable(module, manifest.get("call", "solve"))
        args = manifest.get("args") or []
        kwargs = manifest.get("kwargs") or {}
        value = func(*args, **kwargs)
        payload["status"] = "ok"
        payload["value"] = _safe_repr(value)
    except RecursionError:
        payload["status"] = "crash"
        payload["exception"] = "RecursionError: maximum recursion depth exceeded"
    except MemoryError:
        payload["status"] = "oom"
        payload["exception"] = "MemoryError"
    except BaseException as exc:  # noqa: BLE001 - student code may raise anything
        payload["status"] = "crash"
        tb = traceback.format_exception_only(type(exc), exc)
        frames = traceback.extract_tb(sys.exc_info()[2])
        where = ""
        if frames:
            last = frames[-1]
            where = f" at {os.path.basename(last.filename)}:{last.lineno}"
        payload["exception"] = ("".join(tb).strip() + where)[:2000]
    finally:
        sys.stdout = real_stdout
        payload["wall_ms"] = int((time.perf_counter() - started_wall) * 1000)
        payload["cpu_ms"] = int((time.process_time() - started_cpu) * 1000)
        payload["stdout"] = captured.getvalue()[:16_000]

    # Written last, atomically enough for a one-shot instance. A truncated or
    # absent document is itself a signal the host treats as a crash.
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
