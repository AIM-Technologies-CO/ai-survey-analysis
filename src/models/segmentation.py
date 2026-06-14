"""Models shared between the agent engine and the job layer."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    queued = "queued"
    preparing = "preparing"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"
    artifacts_missing = "artifacts_missing"
    cancelled = "cancelled"


# Statuses that mean the job is finished (no more events will come).
TERMINAL_STATUSES = {
    RunStatus.succeeded,
    RunStatus.failed,
    RunStatus.timed_out,
    RunStatus.artifacts_missing,
    RunStatus.cancelled,
}


class ProgressEvent(BaseModel):
    """A single progress line streamed from the engine to the UI."""

    ts: float = Field(default_factory=time.time)
    kind: str = "text"  # status | tool_use | tool_result | assistant_text | result | error
    message: str = ""
    tool_name: str | None = None
    data: dict | None = None


@dataclass
class SegmentationResult:
    run_id: str
    status: RunStatus
    report_html_path: str | None = None
    report_pptx_path: str | None = None
    session_id: str | None = None
    num_turns: int = 0
    total_cost_usd: float | None = None
    error: str | None = None
    audit_path: str | None = None
