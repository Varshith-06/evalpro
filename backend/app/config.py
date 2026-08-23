"""Runtime configuration.

Everything tunable lives here so that a deployment is a config change, not a
code change. Values are read from the environment with conservative defaults
suitable for a laptop demo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
REPO_DIR = BASE_DIR.parent                                  # repo root
VAR_DIR = Path(os.environ.get("EVALPRO_VAR", BASE_DIR / "var"))
ARTIFACT_DIR = VAR_DIR / "artifacts"
WORK_DIR = VAR_DIR / "work"

for _d in (VAR_DIR, ARTIFACT_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("EVALPRO_DATABASE_URL", f"sqlite:///{VAR_DIR / 'evalpro.db'}")

# Pipeline identity. Bump when stage semantics change; every EvaluationRun
# records it so a regrade a year from now is reproducible (principle 4).
PIPELINE_VERSION = "1.4.0"
MODEL_VERSIONS = {
    "language_classifier": "heur-1.0",
    "structural_clone": "winnow-k5-w4",
    "algorithm_identifier": "pqgram-1.0",
    "claim_entailment": "lexical-nli-0.3",
    "confidence_estimator": "agreement-gbm-lite-1.1",
    "misconception_clusterer": "hdbscan-lite-1.0",
    "knowledge_tracer": "bkt-dag-1.2",
    "risk_model": "logit-1.0",
}


@dataclass(frozen=True)
class IngestLimits:
    """B0 decompression limits. Zip bombs and traversal archives are the first
    attack any deployment sees, so these are hard, not advisory."""

    max_entries: int = 400
    max_uncompressed_bytes: int = 32 * 1024 * 1024
    max_single_file_bytes: int = 4 * 1024 * 1024
    max_depth: int = 8
    max_compression_ratio: float = 200.0
    allowed_extensions: tuple[str, ...] = (
        ".py", ".c", ".h", ".cpp", ".cc", ".hpp", ".java", ".js", ".ts", ".go", ".rs",
        ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".csv",
        ".makefile", ".mk", "", ".sql", ".sh",
    )
    submissions_per_student_per_hour: int = 20


@dataclass(frozen=True)
class SandboxLimits:
    """Layer 5/6/9 budgets from the isolation stack.

    CPU time is the fair metric for performance-graded work; wall-clock is the
    safety backstop enforced by the supervisor outside the guest.
    """

    cpu_seconds: int = 5
    wall_seconds: float = 10.0
    memory_bytes: int = 256 * 1024 * 1024
    max_processes: int = 32
    max_file_bytes: int = 8 * 1024 * 1024
    max_open_files: int = 64
    stdout_cap_bytes: int = 64 * 1024
    stderr_cap_bytes: int = 32 * 1024
    build_cpu_seconds: int = 20
    build_wall_seconds: float = 30.0


@dataclass(frozen=True)
class GateConfig:
    """B7 routing thresholds.

    ``auto_release_confidence`` is exposed to instructors as a single dial:
    "auto-release everything I'd agree with 95% of the time".
    """

    auto_release_confidence: float = 0.75
    similarity_escalate: float = 0.62
    grade_boundary_epsilon: float = 0.015
    grade_boundaries: tuple[float, ...] = (0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
    repair_penalty_escalate: float = 0.10
    high_weight_conflict_share: float = 0.15


@dataclass(frozen=True)
class AnalyticsConfig:
    """L2 / L3 parameters."""

    bkt_prior: float = 0.25
    bkt_learn: float = 0.18
    bkt_slip: float = 0.10
    bkt_guess: float = 0.20
    bkt_em_iterations: int = 40
    mastery_threshold: float = 0.70
    uncertainty_diagnostic_threshold: float = 0.22
    min_evidence_for_confidence: int = 3
    prereq_propagation_factor: float = 0.45
    cohort_reteach_threshold: float = 0.55
    item_difficulty_easy: float = 0.95
    item_difficulty_hard: float = 0.20
    item_discrimination_floor: float = 0.15
    risk_flag_threshold: float = 0.55
    bias_flag_rate_delta: float = 0.05
    co_attainment_threshold: float = 0.60


@dataclass(frozen=True)
class Settings:
    ingest: IngestLimits = field(default_factory=IngestLimits)
    sandbox: SandboxLimits = field(default_factory=SandboxLimits)
    gate: GateConfig = field(default_factory=GateConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    demo_mode: bool = os.environ.get("EVALPRO_DEMO", "1") == "1"


settings = Settings()
