"""Transport-agnostic synthetic-data flows, shared by the HTTP routes and the MCP server.

Raises plain exceptions instead of HTTP ones; each transport maps them itself:
  ValueError   -> bad input (400 / ToolError)
  KeyError     -> not found (404 / ToolError)
  RuntimeError -> server misconfiguration (500 / ToolError)
  PyMongoError -> database unavailable (503 / ToolError)
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from services import data, predictor, tracking
from services import synth_jobs as jobs


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY env var is not set.")


def clean_questions(questions: list[dict]) -> list[dict]:
    if not questions:
        raise ValueError("questions list is empty")
    cleaned = []
    for i, q in enumerate(questions):
        text = (q.get("text") or "").strip()
        qtype = q.get("type")
        if not text:
            raise ValueError(f"question {i}: text is required")
        if qtype not in predictor.VALID_AD_HOC_TYPES:
            raise ValueError(f"question {i}: type must be one of {sorted(predictor.VALID_AD_HOC_TYPES)}")
        options = [o.strip() for o in (q.get("options") or []) if str(o).strip()]
        if qtype in ("multipleChoice", "checkBoxes") and len(options) < 2:
            raise ValueError(f"question {i}: choice questions need at least 2 options")
        cleaned.append({"id": q.get("id", i), "text": text, "type": qtype, "options": options})
    return cleaned


def build_filter_kwargs(date_from: str | None, date_to: str | None, include_all: bool) -> dict:
    try:
        df = data.parse_submit_date(date_from)
        dt = data.parse_submit_date(date_to)
    except ValueError as e:
        raise ValueError(f"bad date: {e}") from e
    if dt is not None and date_to and len(date_to.strip()) <= 10:
        # a date-only upper bound means "through the end of that day"
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)
    return {"date_from": df, "date_to": dt, "include_all": include_all}


def shape_preview_row(survey_id: str, doc: dict, generated_answers: list, error: str | None) -> dict:
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


def survey_questions(survey_id: str) -> dict:
    """Survey's existing questions (ordered) + submit-date bounds of the eligible cohort."""
    data.get_survey(survey_id)  # KeyError if unknown
    return {
        "survey_id": survey_id,
        "questions": [q.to_dict() for q in data.get_survey_questions(survey_id)],
        "submit_date_bounds": data.submit_date_bounds(survey_id),
    }


def eligible_count(survey_id: str, *, date_from: str | None = None, date_to: str | None = None,
                   include_all: bool = False, session_id: str | None = None) -> dict:
    data.get_survey(survey_id)
    kwargs = build_filter_kwargs(date_from, date_to, include_all)
    n = data.count_eligible(survey_id, **kwargs)
    tracking.log(session_id, survey_id, "filter_applied",
                 {"date_from": date_from, "date_to": date_to,
                  "include_all": include_all, "eligible": n})
    return {"eligible": n, "cap": jobs.MAX_GENERATE_RESPONDENTS}


def run_preview(survey_id: str, questions: list[dict], *, date_from: str | None = None,
                date_to: str | None = None, include_all: bool = False,
                session_id: str | None = None) -> dict:
    data.get_survey(survey_id)
    questions = clean_questions(questions)
    require_api_key()
    kwargs = build_filter_kwargs(date_from, date_to, include_all)

    eligible = data.count_eligible(survey_id, **kwargs)
    docs = data.sample_eligible(survey_id, jobs.PREVIEW_SAMPLE, **kwargs)

    results: list = [None] * len(docs)
    usages: list = [None] * len(docs)

    def work(i, doc):
        try:
            out = predictor.ask_ad_hoc(survey_id, doc, questions)
            return i, shape_preview_row(survey_id, doc, out["answers"], None), out.get("usage")
        except Exception as e:
            return i, shape_preview_row(survey_id, doc, [], f"{type(e).__name__}: {e}"), None

    if docs:
        with ThreadPoolExecutor(max_workers=jobs.PREVIEW_WORKERS) as ex:
            for f in as_completed([ex.submit(work, i, d) for i, d in enumerate(docs)]):
                i, row, usage = f.result()
                results[i] = row
                usages[i] = usage

    scored = [u for u in usages if u]
    total_in = sum(u.get("input_tokens", 0) for u in scored)
    total_out = sum(u.get("output_tokens", 0) for u in scored)
    total_cost = sum(u.get("cost_usd", 0.0) for u in scored)
    per_resp = (total_cost / len(scored)) if scored else 0.0

    tracking.log(session_id, survey_id, "preview_run",
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
            "projected_full_run_usd": per_resp * min(eligible, jobs.MAX_GENERATE_RESPONDENTS),
        },
        "results": results,
    }


def start_generate_all(survey_id: str, questions: list[dict], *, date_from: str | None = None,
                       date_to: str | None = None, include_all: bool = False,
                       session_id: str | None = None) -> dict:
    data.get_survey(survey_id)
    questions = clean_questions(questions)
    require_api_key()
    kwargs = build_filter_kwargs(date_from, date_to, include_all)

    eligible = data.count_eligible(survey_id, **kwargs)
    if eligible == 0:
        raise ValueError("no eligible respondents for this filter")

    view = jobs.start_job(survey_id, questions, kwargs, eligible=eligible, session_id=session_id)
    tracking.log(session_id, survey_id, "generate_all_started",
                 {"job_id": view["id"], "total": view["total"], "eligible": eligible,
                  "capped": view["capped"],
                  "questions": [{"text": q["text"], "type": q["type"]} for q in questions]})
    return view


def job_view(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if not job:
        raise KeyError("job not found")
    return jobs._public(job)


def job_file(job_id: str) -> tuple[str, str]:
    """(path, filename) of a finished job's workbook. KeyError if missing/not ready."""
    job = jobs.get_job(job_id)
    if not job:
        raise KeyError("job not found")
    if job["state"] != "done" or not job.get("file"):
        raise ValueError(f"job not ready (state={job['state']})")
    return job["file"], job["filename"]
