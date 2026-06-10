"""Shared survey + respondent endpoints (used by both tabs)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pymongo.errors import PyMongoError

from services import data

router = APIRouter(prefix="/api", tags=["surveys"])


def _db_guard(exc: Exception):
    raise HTTPException(503, "database unavailable") from exc


@router.get("/surveys")
def list_surveys(search: str | None = Query(None), limit: int = Query(50, ge=1, le=200)):
    try:
        return [s.__dict__ for s in data.list_surveys(search=search, limit=limit)]
    except PyMongoError as e:
        _db_guard(e)


@router.get("/surveys/{survey_id}")
def get_survey(survey_id: str):
    try:
        survey = data.get_survey(survey_id)
        qs = data.get_survey_questions(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    return {"id": survey_id, "name": survey.get("name"), "questions": [q.to_dict() for q in qs]}


@router.get("/surveys/{survey_id}/respondents")
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
            survey_id, min_answers=min_answers, max_answers=max_answers,
            status=status, n=limit, seed=seed,
        )
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
    return [
        {"_id": r["id"], "status": r["status"], "answered_count": r["answered_count"]}
        for r in rows
    ]


@router.get("/surveys/{survey_id}/respondents-meta")
def respondents_meta(survey_id: str):
    try:
        return data.respondents_meta(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)


@router.get("/surveys/{survey_id}/respondents/{respondent_id}")
def get_respondent(survey_id: str, respondent_id: str):
    try:
        r = data.get_respondent(survey_id, respondent_id)
    except KeyError:
        raise HTTPException(404, "respondent not found")
    except PyMongoError as e:
        _db_guard(e)
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


@router.get("/surveys/{survey_id}/date-bounds")
def date_bounds(survey_id: str):
    try:
        data.get_survey(survey_id)
        return data.submit_date_bounds(survey_id)
    except KeyError:
        raise HTTPException(404, "survey not found")
    except PyMongoError as e:
        _db_guard(e)
