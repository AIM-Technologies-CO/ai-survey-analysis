"""Segmentation endpoints: upload/inspect Excel, survey detail (counts + labels),
start a run, poll, and serve the HTML report + PowerPoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError

from config import settings
from models.job import JobCreatedResponse, JobStatusResponse, RunRequest, SuggestAxesRequest
from models.survey import CandidateLabel, SurveyCounts, SurveyDetail
from models.upload import UploadResponse
from services import data, excel_inspector, job_registry, predictor
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
        bounds = data.submit_date_bounds(survey_id)
        waves = data.detect_waves(survey_id)
        family = data.detect_survey_waves(survey_id)
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
        date_bounds=bounds,
        wave_capable=waves["wave_capable"],
        detected_waves=waves["waves"],
        wave_family_capable=family["family_capable"],
        wave_family=family["waves"],
    )


@router.get("/surveys/{survey_id}/eligible")
def survey_eligible(
    survey_id: str,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    include_all: bool = Query(False),
):
    """Live count of usable respondents for a date range (drives the picker)."""
    try:
        df = data.parse_submit_date(date_from)
        dt = data.parse_submit_date(date_to)
    except ValueError:
        raise HTTPException(400, "Invalid date; use YYYY-MM-DD")
    try:
        n = data.count_eligible(survey_id, date_from=df, date_to=dt, include_all=include_all)
    except PyMongoError as e:
        raise HTTPException(503, "database unavailable") from e
    return {"eligible": n}


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


# ---------- AI plan preview ("let the AI choose") --------------------------

@router.post("/suggest-axes")
async def suggest_axes(req: SuggestAxesRequest):
    """Propose the 3-6 segmentation axes the AI would build personas around, so the
    user can see (and run with) that plan before kicking off the full agent."""
    if req.source == "upload":
        path = excel_inspector.upload_path(req.ref)
        if not path.exists():
            raise HTTPException(404, f"Upload not found: {req.ref}")
        cols = await run_in_threadpool(excel_inspector.column_names, path)
        labels = [{"label": c} for c in cols]
        survey_name = "Uploaded survey"
    else:  # mongo
        if not settings.mongo_url:
            raise HTTPException(503, "SURVEY_MONGO_URL not configured")
        try:
            survey = await run_in_threadpool(data.get_survey, req.ref)
            questions = await run_in_threadpool(data.get_survey_questions, req.ref)
        except KeyError:
            raise HTTPException(404, f"Survey not found: {req.ref}")
        except PyMongoError as e:
            raise HTTPException(503, "database unavailable") from e
        survey_name = survey.get("name") or "Survey"
        labels, seen = [], set()
        for q in questions:
            lbl = q.label or (q.text[:60] if q.text else None)
            if not lbl or lbl in seen:
                continue
            seen.add(lbl)
            labels.append({"label": lbl, "type": q.type, "question_text": q.text or None})

    if not labels:
        raise HTTPException(400, "No candidate question labels found for this survey")
    try:
        result = await run_in_threadpool(predictor.suggest_segmentation_axes, survey_name, labels)
    except Exception as exc:
        logger.exception("suggest-axes failed for %s", req.ref)
        raise HTTPException(502, f"AI suggestion failed: {exc}")
    if not result.get("axes"):
        raise HTTPException(502, "The AI did not return any usable axes; try again")
    return result


# ---------- runs -----------------------------------------------------------

@router.post("/runs", response_model=JobCreatedResponse)
async def start_run(req: RunRequest):
    # segment_by: empty = let the AI choose the axes; otherwise at least 3 labels.
    if req.segment_by and len(req.segment_by) < 3:
        raise HTTPException(400, "Choose at least 3 question labels, or let the AI choose the segments.")

    # Date range + wave comparison apply to the database source only (uploads have no
    # guaranteed date column).
    date_from = date_to = None
    include_all = True
    waves_parsed: list[dict] | None = None
    if req.source == "upload":
        if req.waves:
            raise HTTPException(400, "Wave-over-wave comparison needs the database source")
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

        if req.waves:
            if len(req.waves) < 2:
                raise HTTPException(400, "Wave-over-wave needs at least two waves")
            waves_parsed = []
            seen_labels: dict[str, int] = {}
            for i, w in enumerate(req.waves):
                # A wave is either a sibling survey (survey_id, whole cohort) or a date
                # window of the base survey (no survey_id -> req.ref + date bounds).
                wsid = (w.survey_id or "").strip() or req.ref
                try:
                    wf = data.parse_submit_date(w.date_from)
                    wt = data.parse_submit_date(w.date_to)
                except ValueError:
                    raise HTTPException(400, "Invalid wave date; use YYYY-MM-DD")
                whole = wf is None and wt is None  # whole survey (sibling / opened survey)
                try:
                    n = await run_in_threadpool(
                        data.count_eligible, wsid, date_from=wf, date_to=wt, include_all=whole,
                    )
                except Exception as e:
                    raise HTTPException(400, f"Wave '{w.label}': could not read survey ({e})")
                if n == 0:
                    raise HTTPException(400, f"Wave '{w.label}' has no eligible respondents")
                label = (w.label or "").strip() or f"Wave {i + 1}"
                # the `wave` column needs distinct values — suffix any duplicate label
                if label in seen_labels:
                    seen_labels[label] += 1
                    label = f"{label} ({seen_labels[label]})"
                else:
                    seen_labels[label] = 1
                waves_parsed.append({"label": label, "survey_id": wsid, "date_from": wf, "date_to": wt})
        else:
            try:
                date_from = data.parse_submit_date(req.date_from)
                date_to = data.parse_submit_date(req.date_to)
            except ValueError:
                raise HTTPException(400, "Invalid date; use YYYY-MM-DD")
            include_all = req.include_all or (date_from is None and date_to is None)

    state = job_registry.create_job(
        req.source, req.ref, req.segment_by, req.additional_details,
        date_from=date_from, date_to=date_to, include_all=include_all,
        waves=waves_parsed,
    )
    job_registry.launch(state.job_id)
    logger.info("Started segmentation run %s (source=%s ref=%s)", state.job_id, req.source, req.ref)
    return JobCreatedResponse(
        job_id=state.job_id, status=state.status,
        status_url=f"/api/segmentation/runs/{state.job_id}",
    )


def _status_response(job_id: str, state, since: int = 0) -> JobStatusResponse:
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


@router.get("/runs/{job_id}", response_model=JobStatusResponse)
def run_status(job_id: str, since: int = Query(0, ge=0)):
    state = job_registry.get_job(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    return _status_response(job_id, state, since)


@router.post("/runs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_run(job_id: str):
    """Stop a queued/running segmentation job. Idempotent-ish: 409 if already finished."""
    state = job_registry.get_job(job_id)
    if not state:
        raise HTTPException(404, "job not found")
    if not job_registry.cancel_job(job_id):
        raise HTTPException(409, f"Run already {state.status}; nothing to cancel")
    logger.info("Cancellation requested for segmentation run %s", job_id)
    return _status_response(job_id, state)


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
