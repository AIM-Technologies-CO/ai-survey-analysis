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

    # MCP endpoint (/mcp/) — empty token == no auth (localhost dev only)
    mcp_auth_token: str = field(default_factory=lambda: os.getenv("MCP_AUTH_TOKEN", ""))
    # Public origin used to build absolute download URLs in MCP results,
    # e.g. https://surveys.example.com — falls back to http://localhost:{port}
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", ""))

    # MCP OAuth (Google Workspace login). Setting both GOOGLE_OAUTH_* values switches
    # /mcp/ from the static token to OAuth 2.1 + Google sign-in restricted to
    # MCP_ALLOWED_GOOGLE_DOMAINS (comma-separated). Requires PUBLIC_BASE_URL (https).
    google_oauth_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""))
    google_oauth_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""))
    mcp_allowed_google_domains: tuple[str, ...] = field(default_factory=lambda: tuple(
        d.strip().lower() for d in os.getenv("MCP_ALLOWED_GOOGLE_DOMAINS", "aimtechnologies.co").split(",")
        if d.strip()))
    mcp_access_token_ttl: int = field(default_factory=lambda: _get_int("MCP_ACCESS_TOKEN_TTL", 3600))
    mcp_refresh_token_ttl: int = field(default_factory=lambda: _get_int("MCP_REFRESH_TOKEN_TTL", 30 * 86400))

    @property
    def mcp_oauth_enabled(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

    # Segmentation agent (Agent SDK)
    segmentation_model: str = field(default_factory=lambda: os.getenv("SEGMENTATION_MODEL", "claude-opus-5-5"))
    fallback_model: str = field(default_factory=lambda: os.getenv("FALLBACK_MODEL", "claude-opus-4-8"))
    segmentation_effort: str = field(default_factory=lambda: os.getenv("SEGMENTATION_EFFORT", "medium"))
    # Limits are OFF by default (0 = unlimited). Set a positive value to re-enable one.
    max_turns: int = field(default_factory=lambda: _get_int("MAX_TURNS", 0))
    max_budget_usd: float = field(default_factory=lambda: _get_float("MAX_BUDGET_USD", 0.0))
    run_timeout_seconds: int = field(default_factory=lambda: _get_int("RUN_TIMEOUT_SECONDS", 0))
    max_concurrent_runs: int = field(default_factory=lambda: _get_int("MAX_CONCURRENT_RUNS", 2))

    # Inputs
    max_upload_mb: int = field(default_factory=lambda: _get_int("MAX_UPLOAD_MB", 50))
    segmentation_row_cap: int = field(default_factory=lambda: _get_int("SEGMENTATION_ROW_CAP", 8000))

    # Paths (under the project root unless overridden absolutely)
    runs_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / os.getenv("RUNS_DIR", "runs")).resolve())
    uploads_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / os.getenv("UPLOADS_DIR", "uploads")).resolve())
    logs_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / "logs").resolve())
    shared_dir: Path = field(default_factory=lambda: (PROJECT_ROOT / os.getenv("SHARED_DIR", "shared")).resolve())

    @property
    def base_url(self) -> str:
        """Public origin without trailing slash (PUBLIC_BASE_URL or localhost)."""
        return (self.public_base_url or f"http://localhost:{self.port}").rstrip("/")

    def ensure_dirs(self) -> None:
        for d in (self.runs_dir, self.uploads_dir, self.logs_dir, self.shared_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
