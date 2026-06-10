"""In-memory registry + background execution for segmentation runs.

State is process-local (prototype) — run gunicorn with workers=1. Each run is
isolated in its own ``runs/<job_id>/`` directory. Mirrors the synthetic-data
jobs pattern but drives the long-running segmentation agent.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from config import settings
from models.segmentation import TERMINAL_STATUSES, ProgressEvent, RunStatus
from services import excel_inspector, segmentation_export
from services.segmentation_agent import run_segmentation
from utils.logging_config.logger import get_logger

logger = get_logger()

_jobs: dict[str, "JobState"] = {}
_run_semaphore = asyncio.Semaphore(settings.max_concurrent_runs)


@dataclass
class JobState:
    job_id: str
    source: str
    ref: str
    segment_by: list[str]
    additional_details: str
    run_dir: Path
    status: str = RunStatus.queued.value
    events: list[ProgressEvent] = field(default_factory=list)
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int = 0
    report_path: Path | None = None
    pptx_path: Path | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None

    def add_event(self, ev: ProgressEvent) -> None:
        self.events.append(ev)
        self.updated_at = time.time()


def create_job(source: str, ref: str, segment_by: list[str], additional_details: str) -> JobState:
    job_id = uuid.uuid4().hex
    state = JobState(
        job_id=job_id,
        source=source,
        ref=ref,
        segment_by=segment_by,
        additional_details=additional_details,
        run_dir=settings.runs_dir / job_id,
    )
    _jobs[job_id] = state
    return state


def get_job(job_id: str) -> JobState | None:
    return _jobs.get(job_id)


def launch(job_id: str) -> None:
    _jobs[job_id].task = asyncio.create_task(_run(job_id))


async def _materialize_input(state: JobState) -> tuple[Path, dict[str, str] | None]:
    state.run_dir.mkdir(parents=True, exist_ok=True)
    dest = state.run_dir / "survey.xlsx"

    if state.source == "mongo":
        state.add_event(ProgressEvent(kind="status", message="Exporting survey from database…"))
        info = await asyncio.to_thread(segmentation_export.export_survey_to_xlsx, state.ref, dest)
        state.add_event(ProgressEvent(
            kind="status",
            message=f"Exported {info['rows']} usable respondents × {len(info['labels'])} questions",
        ))
        question_text = await asyncio.to_thread(segmentation_export.build_data_dictionary, state.ref)
        return dest, question_text

    src = excel_inspector.upload_path(state.ref)
    if not src.exists():
        raise FileNotFoundError(f"Uploaded file not found: {state.ref}")
    shutil.copyfile(src, dest)
    return dest, None


async def _run(job_id: str) -> None:
    state = _jobs[job_id]
    async with _run_semaphore:
        state.status = RunStatus.running.value
        state.add_event(ProgressEvent(kind="status", message="Run started"))
        logger.info("Segmentation job %s running (source=%s ref=%s)", job_id, state.source, state.ref)

        async def on_event(ev: ProgressEvent) -> None:
            state.add_event(ev)
            if ev.kind == "result" and ev.data:
                state.cost_usd = ev.data.get("cost_usd", state.cost_usd)
                state.num_turns = ev.data.get("num_turns", state.num_turns)

        try:
            excel_path, question_text = await _materialize_input(state)
            result = await run_segmentation(
                run_dir=state.run_dir,
                excel_path=excel_path,
                segment_by=state.segment_by,
                additional_details=state.additional_details,
                question_text=question_text,
                on_event=on_event,
                run_id=job_id,
            )
            state.status = result.status.value
            state.error = result.error
            state.cost_usd = result.total_cost_usd
            state.num_turns = result.num_turns
            state.report_path = Path(result.report_html_path) if result.report_html_path else None
            state.pptx_path = Path(result.report_pptx_path) if result.report_pptx_path else None
        except Exception as exc:
            logger.exception("Segmentation job %s failed", job_id)
            state.status = RunStatus.failed.value
            state.error = str(exc)
            state.add_event(ProgressEvent(kind="error", message=f"Run failed: {exc}"))
        finally:
            state.updated_at = time.time()
            if RunStatus(state.status) not in TERMINAL_STATUSES:
                state.status = RunStatus.failed.value
