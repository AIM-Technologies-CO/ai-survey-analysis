"""Segmentation endpoints: upload/inspect Excel, survey detail (counts + labels),
start a run, poll, and serve the HTML report + PowerPoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError

from config import settings
from models.job import JobCreatedResponse, JobStatusResponse, RunRequest
from models.survey import CandidateLabel, SurveyCounts, SurveyDetail
from models.upload import UploadResponse
from services import data, excel_inspector, job_registry
from utils.logging_config.logger import get_logger

router = APIRouter(prefix="/api/segmentation", tags=["segmentation"])
logger = get_logger()

_ALLOWED_EXT = (".xlsx", ".xls")
_PPTX_MEDIA = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


# ---------- DB survey detail (counts + candidate labels) -------------------

@router.get("/surveys/{survey_id}", response_model=SurveyDetail)
def survey_detail(survey_id: str):
    try:
        survey = data.get_survey(survey_id)
        meta = data.respondents_meta(survey_id)
        usable = data.count_eligible(survey_id, include_all=True)
        questions = data.get_survey_questions(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        raise HTTPException(503, "database unavailable") from e

    labels: list[CandidateLabel] = []
    seen = set()
    for q in questions:
        label = q.label or (q.text[:60] if q.text else None)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(CandidateLabel(label=label, type=q.type, question_text=q.text or None))

    return SurveyDetail(
        id=survey_id,
        name=survey.get("name") or "Untitled survey",
        counts=SurveyCounts(
            total=meta.get("total", 0),
            submitted=meta.get("statuses", {}).get("submitted", 0),
            usable=usable,
        ),
        candidate_labels=labels,
    )


# ---------- upload an Excel ------------------------------------------------

@router.post("/upload", response_model=UploadResponse)
async def upload_excel(file: UploadFile = File(...)):
    filename = file.filename or ""
    if not filename.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(400, "File must be an Excel file (.xlsx or .xls)")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(400, f"File exceeds {settings.max_upload_mb} MB limit")

    upload_id = uuid.uuid4().hex
    dest = excel_inspector.upload_path(upload_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    try:
        result = await run_in_threadpool(excel_inspector.inspect_excel, dest)
    except ValueError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(exc))
    except Exception:
        logger.exception("Failed to inspect uploaded file %s", filename)
        dest.unlink(missing_ok=True)
        raise HTTPException(500, "Failed to read the Excel file")
    result.upload_id = upload_id
    return result


# ---------- runs -----------------------------------------------------------

@router.post("/runs", response_model=JobCreatedResponse)
async def start_run(req: RunRequest):
    if not req.segment_by:
        raise HTTPException(400, "Select at least one question label to segment by")

    if req.source == "upload":
        path = excel_inspector.upload_path(req.ref)
        if not path.exists():
            raise HTTPException(404, f"Upload not found: {req.ref}")
        columns = await run_in_threadpool(excel_inspector.column_names, path)
        unknown = [s for s in req.segment_by if s not in columns]
        if unknown:
            raise HTTPException(400, f"Unknown columns: {unknown}")
    else:  # mongo
        if not settings.mongo_url:
            raise HTTPException(503, "SURVEY_MONGO_URL not configured")
        try:
            await run_in_threadpool(data.get_survey, req.ref)
        except KeyError:
            raise HTTPException(404, f"Survey not found: {req.ref}")
        except PyMongoError as e:
            raise HTTPException(503, "database unavailable") from e

    state = job_registry.create_job(req.source, req.ref, req.segment_by, req.additional_details)
    job_registry.launch(state.job_id)
    logger.info("Started segmentation run %s (source=%s ref=%s)", state.job_id, req.source, req.ref)
    return JobCreatedResponse(
        job_id=state.job_id, status=state.status,
        status_url=f"/api/segmentation/runs/{state.job_id}",
    )


@router.get("/runs/{job_id}", response_model=JobStatusResponse)
def run_status(job_id: str, since: int = Query(0, ge=0)):
    state = job_registry.get_job(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    report_url = f"/api/segmentation/runs/{job_id}/report" if (state.report_path and state.report_path.exists()) else None
    pptx_url = f"/api/segmentation/runs/{job_id}/pptx" if (state.pptx_path and state.pptx_path.exists()) else None
    return JobStatusResponse(
        job_id=job_id,
        status=state.status,
        events=state.events[since:],
        error=state.error,
        cost_usd=state.cost_usd,
        num_turns=state.num_turns,
        report_url=report_url,
        pptx_url=pptx_url,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


@router.get("/runs/{job_id}/report")
def run_report(job_id: str):
    state = job_registry.get_job(job_id)
    if not state or not state.report_path or not state.report_path.exists():
        raise HTTPException(404, "Report not available")
    return FileResponse(str(state.report_path), media_type="text/html")


@router.get("/runs/{job_id}/pptx")
def run_pptx(job_id: str):
    state = job_registry.get_job(job_id)
    if not state or not state.pptx_path or not state.pptx_path.exists():
        raise HTTPException(404, "PowerPoint not available")
    return FileResponse(str(state.pptx_path), media_type=_PPTX_MEDIA, filename=f"segmentation_{job_id[:8]}.pptx")
