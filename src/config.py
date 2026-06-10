"""Centralized configuration for Survey Intelligence.

Loaded from environment / .env. Run the service from inside ``src/`` so bare
absolute imports (``from config import settings``) resolve.

Note: the synthetic-data modules (services/data.py, predictor.py, synth_jobs.py)
read a few of their own env vars directly (SURVEY_MONGO_URL, ANTHROPIC_MODEL,
MAX_GENERATE_RESPONDENTS, …). This module's job is to (a) load .env early and
(b) expose the settings the segmentation engine + server need.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("false", "0", "no", "")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Auth / external services
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    mongo_url: str = field(default_factory=lambda: os.getenv("SURVEY_MONGO_URL", ""))

    # Server
    port: int = field(default_factory=lambda: _get_int("PORT_NUMBER", 8766))
    dev_mode: bool = field(default_factory=lambda: _get_bool("DEV_MODE", True))

    # Segmentation agent (Agent SDK)
    segmentation_model: str = field(default_factory=lambda: os.getenv("SEGMENTATION_MODEL", "claude-opus-4-8"))
    fallback_model: str = field(default_factory=lambda: os.getenv("FALLBACK_MODEL", "claude-opus-4-7"))
    max_turns: int = field(default_factory=lambda: _get_int("MAX_TURNS", 120))
    max_budget_usd: float = field(default_factory=lambda: _get_float("MAX_BUDGET_USD", 8.0))
    run_timeout_seconds: int = field(default_factory=lambda: _get_int("RUN_TIMEOUT_SECONDS", 1800))
    max_concurrent_runs: int = field(default_factory=lambda: _get_int("MAX_CONCURRENT_RUNS", 2))

    # Inputs
    max_upload_mb: int = field(default_factory=lambda: _get_int("MAX_UPLOAD_MB", 50))
    segmentation_row_cap: int = field(default_factory=lambda: _get_int("SEGMENTATION_ROW_CAP", 8000))

    # Paths (under the project root unless overridden absolutely)
    runs_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / os.getenv("RUNS_DIR", "runs")).resolve())
    uploads_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / os.getenv("UPLOADS_DIR", "uploads")).resolve())
    logs_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / "logs").resolve())

    def ensure_dirs(self) -> None:
        for d in (self.runs_dir, self.uploads_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
