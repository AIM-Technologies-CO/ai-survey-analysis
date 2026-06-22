"""In-memory background jobs for 'Generate Answers for All Respondents'.

Single uvicorn process, so an in-memory job store is fine. Each job samples up to
MAX_GENERATE_RESPONDENTS eligible respondents, runs predictor.ask_ad_hoc on each
(bounded concurrency), writes an .xlsx, and exposes progress for polling.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from services import data, excelout, predictor, tracking

# ---- cost controls --------------------------------------------------------
MAX_GENERATE_RESPONDENTS = int(os.environ.get("MAX_GENERATE_RESPONDENTS", "50"))
GENERATE_WORKERS = int(os.environ.get("GENERATE_WORKERS", "5"))
PREVIEW_SAMPLE = int(os.environ.get("PREVIEW_SAMPLE", "10"))
PREVIEW_WORKERS = int(os.environ.get("PREVIEW_WORKERS", "10"))

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _public(job: dict) -> dict:
    """The subset of a job safe to return to the client."""
    with _LOCK:
        return {
            "id": job["id"],
            "state": job["state"],
            "done": job["done"],
            "ok": job["ok"],
            "failed": job["failed"],
            "total": job["total"],
            "eligible": job["eligible"],
            "capped": job["capped"],
            "error": job["error"],
            "per_errors": job["per_errors"],
            "filename": job["filename"],
        }


def get_job(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def start_job(survey_id, questions, filter_kwargs, *, eligible, session_id) -> dict:
    """Create + launch a capped generate-all job. Returns the public view."""
    total = min(eligible, MAX_GENERATE_RESPONDENTS)
    job = {
        "id": uuid.uuid4().hex,
        "survey_id": survey_id,
        "state": "pending",
        "done": 0, "ok": 0, "failed": 0,
        "total": total,
        "eligible": eligible,
        "capped": total < eligible,
        "file": None, "filename": None,
        "error": None,
        "per_errors": [],
        "session_id": session_id,
        "_questions": questions,
        "_filter": filter_kwargs,
        "started_at": time.time(), "finished_at": None,
    }
    _JOBS[job["id"]] = job
    threading.Thread(target=_run_job, args=(job["id"],), daemon=True).start()
    return _public(job)


def _run_job(job_id: str) -> None:
    job = _JOBS[job_id]
    try:
        with _LOCK:
            job["state"] = "running"
        docs = data.sample_eligible_capped(job["survey_id"], job["total"], **job["_filter"])
        questions = job["_questions"]
        results: list = [None] * len(docs)

        def work(i, doc):
            try:
                out = predictor.ask_ad_hoc(job["survey_id"], doc, questions)
                return i, doc, out["answers"], None
            except Exception as e:  # per-respondent failure: skip & continue
                return i, doc, [], f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=GENERATE_WORKERS) as ex:
            futures = [ex.submit(work, i, d) for i, d in enumerate(docs)]
            for f in as_completed(futures):
                i, doc, answers, err = f.result()
                results[i] = (doc, answers, err)
                with _LOCK:
                    job["done"] += 1
                    if err:
                        job["failed"] += 1
                        job["per_errors"].append({"respondent_id": str(doc.get("_id", "")), "error": err})
                    else:
                        job["ok"] += 1

        path, fname = excelout.build_workbook(job["survey_id"], questions, results)
        with _LOCK:
            job["state"] = "done"
            job["file"] = str(path)
            job["filename"] = fname
            job["finished_at"] = time.time()
        tracking.log(job["session_id"], job["survey_id"], "generate_all_finished",
                     {"job_id": job["id"], "ok": job["ok"], "failed": job["failed"], "file": fname})
    except Exception as e:
        with _LOCK:
            job["state"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            job["finished_at"] = time.time()


# ---- backtest jobs (predict held-out answers, score vs real) --------------

def start_backtest_job(survey_id, seed_qids, holdout_qs, filter_kwargs, *, eligible, session_id) -> dict:
    """Create + launch a capped backtest job. Returns the public view.
    Shares the _JOBS registry so the existing /api/jobs/{id}[/download] endpoints work."""
    total = min(eligible, MAX_GENERATE_RESPONDENTS)
    job = {
        "id": uuid.uuid4().hex,
        "survey_id": survey_id,
        "state": "pending",
        "done": 0, "ok": 0, "failed": 0,
        "total": total,
        "eligible": eligible,
        "capped": total < eligible,
        "file": None, "filename": None,
        "error": None,
        "per_errors": [],
        "session_id": session_id,
        "_seed_qids": seed_qids,
        "_holdout": holdout_qs,
        "_filter": filter_kwargs,
        "started_at": time.time(), "finished_at": None,
    }
    _JOBS[job["id"]] = job
    threading.Thread(target=_run_backtest_job, args=(job["id"],), daemon=True).start()
    return _public(job)


def _run_backtest_job(job_id: str) -> None:
    job = _JOBS[job_id]
    try:
        with _LOCK:
            job["state"] = "running"
        docs = data.sample_eligible_capped(job["survey_id"], job["total"], **job["_filter"])
        seed_qids = job["_seed_qids"]
        holdout = job["_holdout"]
        results: list = [None] * len(docs)

        def work(i, doc):
            try:
                out = predictor.ask_backtest(job["survey_id"], doc, seed_qids, holdout)
                return i, doc, out["answers"], None
            except Exception as e:  # per-respondent failure: skip & continue
                return i, doc, [], f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=GENERATE_WORKERS) as ex:
            futures = [ex.submit(work, i, d) for i, d in enumerate(docs)]
            for f in as_completed(futures):
                i, doc, answers, err = f.result()
                results[i] = (doc, answers, err)
                with _LOCK:
                    job["done"] += 1
                    if err:
                        job["failed"] += 1
                        job["per_errors"].append({"respondent_id": str(doc.get("_id", "")), "error": err})
                    else:
                        job["ok"] += 1

        path, fname = excelout.build_backtest_workbook(job["survey_id"], seed_qids, holdout, results)
        with _LOCK:
            job["state"] = "done"
            job["file"] = str(path)
            job["filename"] = fname
            job["finished_at"] = time.time()
        tracking.log(job["session_id"], job["survey_id"], "backtest_all_finished",
                     {"job_id": job["id"], "ok": job["ok"], "failed": job["failed"], "file": fname})
    except Exception as e:
        with _LOCK:
            job["state"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
            job["finished_at"] = time.time()
