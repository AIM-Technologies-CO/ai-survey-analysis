"""Synthetic-data endpoints: AI suggests/answers questions for real respondents,
preview on a sample, and generate-all → Excel.

Thin HTTP shim over services/synth_service.py (the same logic backs the MCP
server in mcp_app.py); this module only maps service exceptions to HTTP ones."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from services import backtest_service as backtest
from services import data, predictor, tracking
from services import synth_service as synth

router = APIRouter(prefix="/api", tags=["synthetic-data"])


def _call(fn, *args, **kwargs):
    """Run a service function, translating its plain exceptions to HTTP errors."""
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    except PyMongoError as e:
        raise HTTPException(503, "database unavailable") from e
    except Exception as e:
        raise HTTPException(500, f"LLM call failed: {type(e).__name__}: {e}")


class FilterSpec(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    include_all: bool = False


class SuggestRequest(BaseModel):
    survey_id: str
    n: int = 5
    already: list[str] = []
    focus: str | None = None


@router.post("/suggest")
def suggest(req: SuggestRequest):
    def run():
        data.get_survey(req.survey_id)
        synth.require_api_key()
        return predictor.suggest_questions(req.survey_id, n=max(1, min(req.n, 12)),
                                           already=req.already, focus=req.focus)
    return _call(run)


class AskRequest(BaseModel):
    survey_id: str
    respondent_id: str
    questions: list[dict]


@router.post("/ask")
def ask(req: AskRequest):
    def run():
        respondent = data.get_respondent(req.survey_id, req.respondent_id)
        cleaned = synth.clean_questions(req.questions)
        synth.require_api_key()
        return predictor.ask_ad_hoc(req.survey_id, respondent, cleaned)
    return _call(run)


class EligibleCountRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    session_id: str | None = None


@router.post("/eligible-count")
def eligible_count(req: EligibleCountRequest):
    return _call(synth.eligible_count, req.survey_id,
                 date_from=req.filter.date_from, date_to=req.filter.date_to,
                 include_all=req.filter.include_all, session_id=req.session_id)


class PreviewRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    questions: list[dict]
    session_id: str | None = None


@router.post("/preview")
def preview(req: PreviewRequest):
    return _call(synth.run_preview, req.survey_id, req.questions,
                 date_from=req.filter.date_from, date_to=req.filter.date_to,
                 include_all=req.filter.include_all, session_id=req.session_id)


class GenerateAllRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    questions: list[dict]
    session_id: str | None = None


@router.post("/generate-all")
def generate_all(req: GenerateAllRequest):
    return _call(synth.start_generate_all, req.survey_id, req.questions,
                 date_from=req.filter.date_from, date_to=req.filter.date_to,
                 include_all=req.filter.include_all, session_id=req.session_id)


class BacktestPreviewRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    seed_qids: list[int]
    exclude_qids: list[int] = []
    session_id: str | None = None


@router.post("/backtest/preview")
def backtest_preview(req: BacktestPreviewRequest):
    return _call(backtest.run_backtest_preview, req.survey_id, req.seed_qids,
                 exclude_qids=req.exclude_qids,
                 date_from=req.filter.date_from, date_to=req.filter.date_to,
                 include_all=req.filter.include_all, session_id=req.session_id)


class BacktestGenerateRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    seed_qids: list[int]
    exclude_qids: list[int] = []
    session_id: str | None = None


@router.post("/backtest/generate-all")
def backtest_generate_all(req: BacktestGenerateRequest):
    return _call(backtest.start_backtest_all, req.survey_id, req.seed_qids,
                 exclude_qids=req.exclude_qids,
                 date_from=req.filter.date_from, date_to=req.filter.date_to,
                 include_all=req.filter.include_all, session_id=req.session_id)


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    return _call(synth.job_view, job_id)


@router.get("/jobs/{job_id}/download")
def job_download(job_id: str):
    try:
        path, filename = synth.job_file(job_id)
    except KeyError:
        raise HTTPException(404, "job not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return FileResponse(
        path, filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class TrackEvent(BaseModel):
    session_id: str | None = None
    survey_id: str | None = None
    action: str
    payload: dict = {}


@router.post("/track")
def track(ev: TrackEvent):
    tracking.log(ev.session_id, ev.survey_id, ev.action, ev.payload)
    return {"ok": True}
