"""B0 - Ingest and normalise.

Zip bombs and traversal archives are the first attack a deployment sees, so the
limits here are hard rather than advisory. The other job of this stage is
cheap and valuable: a content hash over normalised contents gives free
idempotency (a resubmitted identical bundle returns the cached result) and free
protection against deadline submit-spam.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta

from ..config import IngestLimits, settings


class IngestError(Exception):
    """Raised when a bundle violates a hard limit. Never silently truncated."""


@dataclass
class IngestResult:
    files: dict[str, str]
    report_text: str = ""
    content_hash: str = ""
    rejected: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def summary(self) -> str:
        return (
            f"{len(self.files)} file(s) accepted, {len(self.rejected)} rejected, "
            f"{self.total_bytes} bytes, hash {self.content_hash[:12]}"
        )


_REPORT_NAMES = re.compile(r"(report|readme|writeup|analysis)\.(md|txt)$", re.IGNORECASE)


def _normalise_source(text: str) -> str:
    """Line-ending and trailing-whitespace normalisation.

    Done before hashing so that a Windows-vs-Unix checkout of identical code
    hashes identically -- otherwise the cache never hits and the idempotency
    guarantee is decorative.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _safe_entry_name(name: str, limits: IngestLimits) -> str:
    """Reject absolute paths, drive letters, and ``..`` traversal outright."""
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        raise IngestError(f"absolute path in archive entry: {name!r}")
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise IngestError(f"path traversal in archive entry: {name!r}")
    if len(parts) > limits.max_depth:
        raise IngestError(f"archive nesting exceeds depth {limits.max_depth}: {name!r}")
    return posixpath.join(*parts) if parts else ""


def _extension_allowed(path: str, limits: IngestLimits) -> bool:
    lowered = path.lower()
    if lowered.endswith("makefile") or lowered.endswith("dockerfile"):
        return True
    dot = lowered.rfind(".")
    ext = lowered[dot:] if dot >= 0 else ""
    return ext in limits.allowed_extensions


def compute_content_hash(files: dict[str, str], report_text: str = "") -> str:
    """Stable hash over normalised contents, independent of file ordering."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_normalise_source(files[path]).encode("utf-8"))
        digest.update(b"\0")
    digest.update(b"REPORT\0")
    digest.update(_normalise_source(report_text).encode("utf-8"))
    return digest.hexdigest()


def ingest_files(
    files: dict[str, str],
    report_text: str = "",
    limits: IngestLimits | None = None,
) -> IngestResult:
    """Normalise an already-unpacked bundle (the API's direct-submit path)."""
    limits = limits or settings.ingest
    accepted: dict[str, str] = {}
    rejected: list[dict] = []
    warnings: list[str] = []
    total = 0

    if len(files) > limits.max_entries:
        raise IngestError(f"bundle has {len(files)} entries, limit is {limits.max_entries}")

    for raw_path, content in files.items():
        path = _safe_entry_name(raw_path, limits)
        if not path:
            continue
        if not _extension_allowed(path, limits):
            rejected.append({"path": raw_path, "reason": "extension not on allowlist"})
            continue
        normalised = _normalise_source(content)
        size = len(normalised.encode("utf-8"))
        if size > limits.max_single_file_bytes:
            rejected.append({"path": raw_path, "reason": f"file exceeds {limits.max_single_file_bytes} bytes"})
            continue
        total += size
        if total > limits.max_uncompressed_bytes:
            raise IngestError(f"bundle exceeds {limits.max_uncompressed_bytes} uncompressed bytes")
        if _REPORT_NAMES.search(path) and not report_text:
            report_text = normalised
            continue
        accepted[path] = normalised

    if not accepted:
        raise IngestError("no gradeable source files in bundle")
    if rejected:
        warnings.append(f"{len(rejected)} entries rejected by the allowlist")

    return IngestResult(
        files=accepted,
        report_text=_normalise_source(report_text),
        content_hash=compute_content_hash(accepted, report_text),
        rejected=rejected,
        warnings=warnings,
        total_bytes=total,
    )


def ingest_archive(blob: bytes, limits: IngestLimits | None = None) -> IngestResult:
    """Decompress a zip with hard limits on entries, size, depth, and ratio."""
    limits = limits or settings.ingest
    files: dict[str, str] = {}
    report_text = ""
    rejected: list[dict] = []
    warnings: list[str] = []
    total = 0

    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise IngestError(f"not a readable archive: {exc}") from exc

    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > limits.max_entries:
        raise IngestError(f"archive has {len(infos)} entries, limit is {limits.max_entries}")

    declared = sum(i.file_size for i in infos)
    compressed = sum(i.compress_size for i in infos) or 1
    ratio = declared / compressed
    if ratio > limits.max_compression_ratio:
        # Classic zip bomb: refuse before decompressing a single byte.
        raise IngestError(
            f"compression ratio {ratio:.0f}x exceeds {limits.max_compression_ratio:.0f}x "
            "(possible decompression bomb)"
        )
    if declared > limits.max_uncompressed_bytes:
        raise IngestError(f"archive declares {declared} uncompressed bytes, limit is {limits.max_uncompressed_bytes}")

    for info in infos:
        path = _safe_entry_name(info.filename, limits)
        if not path:
            continue
        if info.file_size > limits.max_single_file_bytes:
            rejected.append({"path": info.filename, "reason": "file exceeds per-file cap"})
            continue
        if not _extension_allowed(path, limits):
            rejected.append({"path": info.filename, "reason": "extension not on allowlist"})
            continue
        with archive.open(info) as handle:
            raw = handle.read(limits.max_single_file_bytes + 1)
        if len(raw) > limits.max_single_file_bytes:
            rejected.append({"path": info.filename, "reason": "actual size exceeded declared size"})
            continue
        total += len(raw)
        if total > limits.max_uncompressed_bytes:
            raise IngestError("archive exceeded the uncompressed byte budget during extraction")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("latin-1")
                warnings.append(f"{path}: decoded as latin-1")
            except UnicodeDecodeError:
                rejected.append({"path": info.filename, "reason": "undecodable bytes"})
                continue
        normalised = _normalise_source(text)
        if _REPORT_NAMES.search(path) and not report_text:
            report_text = normalised
            continue
        files[path] = normalised

    if not files:
        raise IngestError("no gradeable source files in archive")

    return IngestResult(
        files=files,
        report_text=report_text,
        content_hash=compute_content_hash(files, report_text),
        rejected=rejected,
        warnings=warnings,
        total_bytes=total,
    )


def rate_limit_exceeded(recent_submission_times: list, now, limits: IngestLimits | None = None) -> bool:
    """Per-student-per-hour cap. Deadline submit-spam is a real DoS vector."""
    limits = limits or settings.ingest
    window_start = now - timedelta(hours=1)
    recent = [t for t in recent_submission_times if t and t >= window_start]
    return len(recent) >= limits.submissions_per_student_per_hour
