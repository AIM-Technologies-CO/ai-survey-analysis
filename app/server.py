"""FastAPI server for the synthetic-data app (v2 — live MongoDB)."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pymongo.errors import PyMongoError


# Load .env (next to this file) before importing the predictor so the
# anthropic client picks up the key when it lazy-inits.
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

_load_dotenv()

from . import data, jobs, predictor, tracking  # noqa: E402

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"

app = FastAPI(title="Survey Synthetic Data (live)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- error mapping --------------------------------------------------

def _db_guard(exc: Exception):
    """Map a Mongo failure to a 503 without leaking the connection string."""
    raise HTTPException(503, "database unavailable") from exc


# ---------- routes ---------------------------------------------------------

@app.get("/api/surveys")
def list_surveys(
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        return [s.__dict__ for s in data.list_surveys(search=search, limit=limit)]
    except PyMongoError as e:
        _db_guard(e)


@app.get("/api/surveys/{survey_id}")
def get_survey(survey_id: str):
    try:
        survey = data.get_survey(survey_id)
        qs = data.get_survey_questions(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    return {
        "id": survey_id,
        "name": survey.get("name"),
        "questions": [q.to_dict() for q in qs],
    }


@app.get("/api/surveys/{survey_id}/respondents")
def list_respondents(
    survey_id: str,
    limit: int = Query(50, ge=1, le=500),
    seed: int | None = None,
    min_answers: int | None = None,
    max_answers: int | None = None,
    status: list[str] | None = Query(None),
):
    try:
        rows = data.filter_respondents(
            survey_id,
            min_answers=min_answers,
            max_answers=max_answers,
            status=status,
            n=limit,
            seed=seed,
        )
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    return [
        {"_id": r["id"], "status": r["status"], "answered_count": r["answered_count"]}
        for r in rows
    ]


@app.get("/api/surveys/{survey_id}/respondents-meta")
def respondents_meta(survey_id: str):
    try:
        return data.respondents_meta(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)


@app.get("/api/surveys/{survey_id}/respondents/{respondent_id}")
def get_respondent(survey_id: str, respondent_id: str):
    try:
        r = data.get_respondent(survey_id, respondent_id)
    except KeyError:
        raise HTTPException(404, "respondent not found")
    except PyMongoError as e:
        _db_guard(e)
    # Strip heavy fields not needed in UI; serialize BSON (datetime/ObjectId) safely.
    payload = {
        "_id": respondent_id,
        "status": r.get("status"),
        "submitDate": r.get("submitDate"),
        "country": r.get("countryName"),
        "language": r.get("language"),
        "answered_count": len(r.get("questions", []) or []),
        "answers": [
            {
                "sqlQuestionId": q.get("sqlQuestionId"),
                "header": q.get("header"),
                "label": q.get("label"),
                "type": q.get("type"),
                "answers": [
                    {"value": a.get("value"), "answer": a.get("answer"), "position": a.get("position")}
                    for a in (q.get("answers") or [])
                ],
            }
            for q in r.get("questions", []) or []
        ],
    }
    return data.jsonable(payload)


class SuggestRequest(BaseModel):
    survey_id: str
    n: int = 5
    already: list[str] = []  # question texts already in the builder, to avoid duplicating


@app.post("/api/suggest")
def suggest(req: SuggestRequest):
    try:
        data.get_survey(req.survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "ANTHROPIC_API_KEY env var is not set.")
    n = max(1, min(req.n, 12))
    try:
        return predictor.suggest_questions(req.survey_id, n=n, already=req.already)
    except Exception as e:
        raise HTTPException(500, f"LLM call failed: {type(e).__name__}: {e}")


class AskRequest(BaseModel):
    survey_id: str
    respondent_id: str
    questions: list[dict]   # [{id, text, type, options?}]


@app.post("/api/ask")
def ask(req: AskRequest):
    try:
        respondent = data.get_respondent(req.survey_id, req.respondent_id)
    except KeyError:
        raise HTTPException(404, "respondent not found")
    except PyMongoError as e:
        _db_guard(e)

    cleaned = _clean_questions(req.questions)
    _require_key()
    try:
        result = predictor.ask_ad_hoc(req.survey_id, respondent, cleaned)
    except Exception as e:
        raise HTTPException(500, f"LLM call failed: {type(e).__name__}: {e}")
    return result


# ---------- PRD cohort flow: filter / preview / generate-all / track -------

class FilterSpec(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    include_all: bool = False


def _require_key():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "ANTHROPIC_API_KEY env var is not set.")


def _clean_questions(questions: list[dict]) -> list[dict]:
    """Validate + normalize the researcher's question set (shared by ask/preview/generate)."""
    if not questions:
        raise HTTPException(400, "questions list is empty")
    cleaned = []
    for i, q in enumerate(questions):
        text = (q.get("text") or "").strip()
        qtype = q.get("type")
        if not text:
            raise HTTPException(400, f"question {i}: text is required")
        if qtype not in predictor.VALID_AD_HOC_TYPES:
            raise HTTPException(400, f"question {i}: type must be one of {sorted(predictor.VALID_AD_HOC_TYPES)}")
        options = [o.strip() for o in (q.get("options") or []) if str(o).strip()]
        if qtype in ("multipleChoice", "checkBoxes") and len(options) < 2:
            raise HTTPException(400, f"question {i}: choice questions need at least 2 options")
        cleaned.append({"id": q.get("id", i), "text": text, "type": qtype, "options": options})
    return cleaned


def _filter_kwargs(f: FilterSpec) -> dict:
    """Parse a FilterSpec into data-layer kwargs (naive-UTC datetimes)."""
    try:
        df = data.parse_submit_date(f.date_from)
        dt = data.parse_submit_date(f.date_to)
    except ValueError as e:
        raise HTTPException(400, f"bad date: {e}")
    # a date-only `date_to` (YYYY-MM-DD) is inclusive of that whole day
    if dt is not None and f.date_to and len(f.date_to.strip()) <= 10:
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)
    return {"date_from": df, "date_to": dt, "include_all": f.include_all}


def _shape_preview_row(survey_id: str, doc: dict, generated_answers: list, error: str | None) -> dict:
    qmeta = {q.sqlQuestionId: q for q in data.get_survey_questions(survey_id)}
    real = []
    for qid, entry in data.respondent_answers_by_qid(doc).items():
        q = qmeta.get(qid)
        vals = [a.get("answer") or a.get("value") or "" for a in (entry.get("answers") or [])]
        vals = [v for v in vals if v]
        if not vals:
            continue
        real.append({
            "label": entry.get("label") or (q.label if q else None),
            "text": (q.text if q else None) or entry.get("label") or f"Q{qid}",
            "type": entry.get("type") or (q.type if q else ""),
            "section": (q.section if q else None) or "",
            "answer": ", ".join(vals),
        })
    return {
        "id": str(doc.get("_id", "")),
        "submitDate": data.jsonable(doc.get("submitDate")),
        "real_answers": real,
        "generated": generated_answers,
        "error": error,
    }


@app.get("/api/surveys/{survey_id}/date-bounds")
def date_bounds(survey_id: str):
    try:
        data.get_survey(survey_id)
        return data.submit_date_bounds(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)


class EligibleCountRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    session_id: str | None = None


@app.post("/api/eligible-count")
def eligible_count(req: EligibleCountRequest):
    try:
        data.get_survey(req.survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    kwargs = _filter_kwargs(req.filter)
    try:
        n = data.count_eligible(req.survey_id, **kwargs)
    except PyMongoError as e:
        _db_guard(e)
    tracking.log(req.session_id, req.survey_id, "filter_applied",
                 {"date_from": req.filter.date_from, "date_to": req.filter.date_to,
                  "include_all": req.filter.include_all, "eligible": n})
    return {"eligible": n, "cap": jobs.MAX_GENERATE_RESPONDENTS}


class PreviewRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    questions: list[dict]
    session_id: str | None = None


@app.post("/api/preview")
def preview(req: PreviewRequest):
    try:
        data.get_survey(req.survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    questions = _clean_questions(req.questions)
    _require_key()
    kwargs = _filter_kwargs(req.filter)

    try:
        eligible = data.count_eligible(req.survey_id, **kwargs)
        docs = data.sample_eligible(req.survey_id, jobs.PREVIEW_SAMPLE, **kwargs)
    except PyMongoError as e:
        _db_guard(e)

    results: list = [None] * len(docs)
    usages: list = [None] * len(docs)

    def work(i, doc):
        try:
            out = predictor.ask_ad_hoc(req.survey_id, doc, questions)
            return i, _shape_preview_row(req.survey_id, doc, out["answers"], None), out.get("usage")
        except Exception as e:
            return i, _shape_preview_row(req.survey_id, doc, [], f"{type(e).__name__}: {e}"), None

    if docs:
        with ThreadPoolExecutor(max_workers=jobs.PREVIEW_WORKERS) as ex:
            for f in as_completed([ex.submit(work, i, d) for i, d in enumerate(docs)]):
                i, row, usage = f.result()
                results[i] = row
                usages[i] = usage

    # Sum the real token cost of this 10-respondent run; per-respondent average lets the
    # client estimate the full generate-all run before it's launched.
    scored = [u for u in usages if u]
    total_in = sum(u.get("input_tokens", 0) for u in scored)
    total_out = sum(u.get("output_tokens", 0) for u in scored)
    total_cost = sum(u.get("cost_usd", 0.0) for u in scored)
    per_resp = (total_cost / len(scored)) if scored else 0.0

    tracking.log(req.session_id, req.survey_id, "preview_run",
                 {"sample": len(docs), "eligible": eligible,
                  "cost_usd": total_cost, "input_tokens": total_in, "output_tokens": total_out,
                  "questions": [{"text": q["text"], "type": q["type"]} for q in questions]})
    return {
        "model": predictor.MODEL,
        "eligible": eligible,
        "sample": len(docs),
        "cap": jobs.MAX_GENERATE_RESPONDENTS,
        "cost": {
            "scored": len(scored),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_usd": total_cost,
            "per_respondent_usd": per_resp,
        },
        "results": results,
    }


class GenerateAllRequest(BaseModel):
    survey_id: str
    filter: FilterSpec = FilterSpec()
    questions: list[dict]
    session_id: str | None = None


@app.post("/api/generate-all")
def generate_all(req: GenerateAllRequest):
    try:
        data.get_survey(req.survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    questions = _clean_questions(req.questions)
    _require_key()
    kwargs = _filter_kwargs(req.filter)

    try:
        eligible = data.count_eligible(req.survey_id, **kwargs)
    except PyMongoError as e:
        _db_guard(e)
    if eligible == 0:
        raise HTTPException(400, "no eligible respondents for this filter")

    view = jobs.start_job(req.survey_id, questions, kwargs, eligible=eligible, session_id=req.session_id)
    tracking.log(req.session_id, req.survey_id, "generate_all_started",
                 {"job_id": view["id"], "total": view["total"], "eligible": eligible,
                  "capped": view["capped"],
                  "questions": [{"text": q["text"], "type": q["type"]} for q in questions]})
    return view


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return jobs._public(job)


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["state"] != "done" or not job.get("file"):
        raise HTTPException(409, f"job not ready (state={job['state']})")
    return FileResponse(
        job["file"],
        filename=job["filename"],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class TrackEvent(BaseModel):
    session_id: str | None = None
    survey_id: str | None = None
    action: str
    payload: dict = {}


@app.post("/api/track")
def track(ev: TrackEvent):
    tracking.log(ev.session_id, ev.survey_id, ev.action, ev.payload)
    return {"ok": True}


# ---------- static UI ------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    idx = STATIC_DIR / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return {"ok": True, "hint": "static UI not found"}
