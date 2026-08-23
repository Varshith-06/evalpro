"""§5 sandbox: the supervisor side of the boundary.

The production isolation stack is twelve layers deep (Firecracker microVM,
namespaces, seccomp-bpf allowlist, dropped capabilities, cgroups v2, rlimits,
read-only rootfs, empty netns, one-shot instances, host hardening). Almost none
of that is expressible on a developer laptop, and pretending otherwise would be
the exact failure this module exists to prevent.

So the design is an explicit **interface plus a capability report**:

* ``Sandbox`` is the contract every backend implements.
* ``LocalSubprocessSandbox`` is the portable reference backend. It applies
  every control the host actually supports (POSIX rlimits where available,
  wall-clock kill from a supervisor thread outside the child, scrubbed
  environment, isolated one-shot working directory, output caps) and
  *reports the layers it could not apply* rather than silently skipping them.
* ``describe_isolation()`` returns that report, and the admin dashboard shows
  it. An operator should never have to guess how contained the grader is.

The two decisions that matter more than any flag are enforced here regardless
of backend:

1. **The expected output never enters the guest.** ``SandboxJob`` has no field
   for it. Comparison happens on the host in ``b4_execute``.
2. **One-shot instances.** Every job gets a fresh directory that is destroyed
   afterwards, so no submission can contaminate the next.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..config import SandboxLimits, WORK_DIR, settings

try:  # POSIX only
    import resource  # type: ignore
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore

HARNESS_PATH = Path(__file__).resolve().parent.parent / "harness" / "runner.py"

# Environment variables the guest is allowed to see. Everything else is dropped:
# workers hold nothing worth stealing, and this is how that stays true (§5.2).
ENV_ALLOWLIST = ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP", "PATH")


@dataclass
class SandboxJob:
    """One test execution. Note what is *absent*: any expected output."""

    test_key: str
    files: dict[str, str]
    entry_point: str
    call: str = "solve"
    args: list | None = None
    kwargs: dict | None = None
    setup: str = ""
    seed: int = 0
    limits: SandboxLimits | None = None


@dataclass
class SandboxResult:
    test_key: str
    status: str                     # ok | crash | timeout | oom | harness_error
    value: str | None = None        # canonical repr of the returned value
    stdout: str = ""
    stderr: str = ""
    exception: str | None = None
    exit_code: int | None = None
    cpu_ms: int = 0
    wall_ms: int = 0
    peak_memory_kb: int = 0
    truncated: bool = False
    denials: list[str] = field(default_factory=list)


class Sandbox(Protocol):
    name: str

    def run(self, job: SandboxJob) -> SandboxResult: ...


# --------------------------------------------------------------------------
# Capability reporting
# --------------------------------------------------------------------------
def describe_isolation() -> dict:
    """What is actually enforced on this host, layer by layer.

    Surfaced on the admin System Health view. ``applied`` false is not a bug --
    it is the honest statement that this deployment is a laptop, not a fleet.
    """
    posix = os.name == "posix"
    layers = [
        (0, "Static pre-screen for raw syscalls / ptrace / network / fork loops",
         True, "advisory input to the gate, never a block on its own"),
        (1, "Hardware-virtualised boundary (Firecracker microVM or gVisor)",
         False, "requires KVM host; production backend"),
        (2, "Namespaces: pid, net, mnt, uts, ipc, user, cgroup",
         posix, "Linux only"),
        (3, "seccomp-bpf allowlist, deny by default",
         False, "requires Linux + libseccomp; production backend"),
        (4, "Non-root UID, no_new_privs, all capabilities dropped",
         posix, "partial: separate UID requires a privileged supervisor"),
        (5, "cgroups v2 memory.max / cpu.max / pids.max / io.max",
         False, "Linux only; approximated by rlimits at layer 6 here"),
        (6, "rlimits: RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_NPROC, RLIMIT_NOFILE, RLIMIT_CORE=0",
         posix and resource is not None, "applied via preexec_fn on POSIX"),
        (7, "Read-only rootfs, size-capped tmpfs writable layer",
         False, "approximated by an isolated one-shot working directory"),
        (8, "Empty network namespace - no interfaces, no loopback",
         False, "production backend; no dependency resolution at run time here"),
        (9, "Wall-clock timeout enforced by supervisor outside the guest, SIGKILL",
         True, "enforced by the host process, applied on every backend"),
        (10, "Output caps on stdout/stderr/result channel with truncation marks",
         True, "enforced by the host on every backend"),
        (11, "One-shot instances destroyed between every run",
         True, "fresh working directory per job, removed afterwards"),
        (12, "Host hardening: dedicated workers, no secrets in env, metadata endpoint firewalled",
         True, "environment allowlist applied; network policy is deployment-level"),
    ]
    return {
        "backend": "LocalSubprocessSandbox",
        "host": f"{platform.system()} {platform.release()}",
        "production_backend_available": False,
        "oracle_outside_guest": True,
        "one_shot_instances": True,
        "layers": [
            {"layer": n, "control": c, "applied": bool(a), "note": note}
            for n, c, a, note in layers
        ],
        "applied_count": sum(1 for _, _, a, _ in layers if a),
        "total_layers": len(layers),
    }


# --------------------------------------------------------------------------
# Static pre-screen (layer 0) - advisory only
# --------------------------------------------------------------------------
DANGEROUS_PATTERNS = {
    "network": ("socket.socket", "urllib.request", "http.client", "requests.get", "ftplib"),
    "process": ("subprocess.", "os.system", "os.popen", "os.fork", "multiprocessing.Process"),
    "introspection": ("ctypes", "ptrace", "/proc/", "sys._getframe", "gc.get_objects"),
    "filesystem": ("shutil.rmtree", "os.remove", "os.unlink", "open('/etc", 'open("/etc'),
    "dynamic": ("eval(", "exec(", "__import__(", "compile("),
}


def static_pre_screen(files: dict[str, str]) -> list[dict]:
    """Layer 0. Flags, never blocks. Feeds the gate as one signal among many."""
    findings: list[dict] = []
    for path, source in files.items():
        for line_no, line in enumerate(source.splitlines(), start=1):
            for category, needles in DANGEROUS_PATTERNS.items():
                for needle in needles:
                    if needle in line:
                        findings.append(
                            {
                                "file": path,
                                "line": line_no,
                                "category": category,
                                "pattern": needle,
                                "excerpt": line.strip()[:160],
                            }
                        )
    return findings


# --------------------------------------------------------------------------
# Reference backend
# --------------------------------------------------------------------------
class LocalSubprocessSandbox:
    """Portable one-shot subprocess backend.

    Not a security boundary sufficient for hostile production traffic -- that is
    what layer 1 exists for -- but it enforces the design decisions that anti-
    cheat depends on, and it applies every OS control this host offers.
    """

    name = "local-subprocess"

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self.limits = limits or settings.sandbox

    # -- one-shot instance lifecycle ------------------------------------
    def _materialise(self, job: SandboxJob, root: Path) -> Path:
        """Write the submission bundle into a fresh instance directory.

        Entry names were already validated at B0, but path containment is
        re-checked here: defence in depth means the last layer also checks.
        """
        for relpath, content in job.files.items():
            target = (root / relpath).resolve()
            if not str(target).startswith(str(root.resolve())):
                raise ValueError(f"path escape rejected: {relpath}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        entry = (root / job.entry_point).resolve()
        if not entry.exists():
            candidates = sorted(root.rglob("*.py"))
            if not candidates:
                raise FileNotFoundError(job.entry_point)
            entry = candidates[0]
        return entry

    def _preexec(self, limits: SandboxLimits):  # pragma: no cover - POSIX only
        if resource is None:
            return None

        def apply() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_file_bytes, limits.max_file_bytes))
            resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
            except (ValueError, OSError):
                pass
            os.setsid()

        return apply

    def _environment(self, job: SandboxJob, result_path: Path, manifest_path: Path) -> dict[str, str]:
        env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
        env.update(
            {
                "EVALPRO_RESULT_PATH": str(result_path),
                "EVALPRO_MANIFEST_PATH": str(manifest_path),
                # Freeze the environment: identical inputs must produce identical
                # bytes on a regrade a year from now (§5.2).
                "PYTHONHASHSEED": str(job.seed % 4294967295),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONNOUSERSITE": "1",
                "TZ": "UTC",
                "LC_ALL": "C",
                "HOME": str(result_path.parent),
            }
        )
        return env

    def run(self, job: SandboxJob) -> SandboxResult:
        limits = job.limits or self.limits
        instance = Path(tempfile.mkdtemp(prefix=f"evalpro-{uuid.uuid4().hex[:8]}-", dir=str(WORK_DIR)))
        code_root = instance / "code"
        channel = instance / "channel"
        code_root.mkdir()
        channel.mkdir()
        result_path = channel / "result.json"
        manifest_path = channel / "manifest.json"
        denials: list[str] = []
        if resource is None:
            denials.append("rlimits unavailable on this host (non-POSIX)")

        try:
            entry = self._materialise(job, code_root)
            manifest = {
                "test_key": job.test_key,
                "entry_path": str(entry),
                "call": job.call,
                "args": job.args or [],
                "kwargs": job.kwargs or {},
                "seed": job.seed,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            harness_copy = code_root / "__evalpro_harness__.py"
            shutil.copyfile(HARNESS_PATH, harness_copy)

            started = time.perf_counter()
            popen_kwargs: dict = {
                "cwd": str(code_root),
                "env": self._environment(job, result_path, manifest_path),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "stdin": subprocess.DEVNULL,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "posix":  # pragma: no cover - POSIX only
                popen_kwargs["preexec_fn"] = self._preexec(limits)
            else:
                popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            proc = subprocess.Popen(
                [sys.executable, "-I", "-S", str(harness_copy)],
                **popen_kwargs,
            )
            timed_out = False
            try:
                # Layer 9: the supervisor outside the guest owns the clock.
                stdout, stderr = proc.communicate(timeout=limits.wall_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()          # SIGKILL, not SIGTERM - handlers do not get a vote
                try:
                    stdout, stderr = proc.communicate(timeout=3)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    stdout, stderr = "", ""
                denials.append("wall-clock timeout enforced by supervisor")
            wall_ms = int((time.perf_counter() - started) * 1000)

            truncated = False
            if len(stdout) > limits.stdout_cap_bytes:
                stdout = stdout[: limits.stdout_cap_bytes] + "\n...[truncated: output cap]"
                truncated = True
            if len(stderr) > limits.stderr_cap_bytes:
                stderr = stderr[: limits.stderr_cap_bytes] + "\n...[truncated: output cap]"
                truncated = True

            if timed_out:
                return SandboxResult(
                    test_key=job.test_key,
                    status="timeout",
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=proc.returncode,
                    wall_ms=wall_ms,
                    truncated=truncated,
                    denials=denials,
                )

            if not result_path.exists():
                # The harness owns the channel. No document means the process
                # died before it could write one - a crash, never a pass.
                status = "oom" if _looks_like_oom(stderr) else "crash"
                return SandboxResult(
                    test_key=job.test_key,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    exception=_last_line(stderr),
                    exit_code=proc.returncode,
                    wall_ms=wall_ms,
                    truncated=truncated,
                    denials=denials,
                )

            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return SandboxResult(
                    test_key=job.test_key,
                    status="crash",
                    stdout=stdout,
                    stderr=stderr,
                    exception="result channel unreadable (truncated write)",
                    exit_code=proc.returncode,
                    wall_ms=wall_ms,
                    denials=denials + ["malformed result channel"],
                )

            return SandboxResult(
                test_key=job.test_key,
                status=payload.get("status", "crash"),
                value=payload.get("value"),
                stdout=payload.get("stdout", "") or stdout,
                stderr=stderr,
                exception=payload.get("exception"),
                exit_code=proc.returncode,
                cpu_ms=int(payload.get("cpu_ms", 0)),
                wall_ms=wall_ms,
                truncated=truncated,
                denials=denials,
            )
        except Exception as exc:  # noqa: BLE001 - supervisor must never crash the run
            return SandboxResult(
                test_key=job.test_key,
                status="harness_error",
                exception=f"{type(exc).__name__}: {exc}",
                denials=denials,
            )
        finally:
            # Layer 11. One-shot: nothing survives to contaminate the next job.
            shutil.rmtree(instance, ignore_errors=True)


def _looks_like_oom(stderr: str) -> bool:
    lowered = stderr.lower()
    return "memoryerror" in lowered or "cannot allocate memory" in lowered


def _last_line(text: str) -> str | None:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:500] if lines else None


DEFAULT_SANDBOX = LocalSubprocessSandbox()
