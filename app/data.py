"""Data access layer — queries MongoDB LIVE (no exported JSON files).

This is the v2 data layer. Where v1 read per-survey folders from disk, this module
talks directly to the `research` database:

    surveys      — survey definitions (name, ordered list of section ids)
    sections     — section + question + answer definitions
    respondents  — one document per respondent, with their answered questions

The survey/section/question shapes returned here are identical to v1 so the
predictor and frontend need only minimal changes.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient

MONGO_URL = os.environ.get("SURVEY_MONGO_URL")
DB_NAME = os.environ.get("SURVEY_MONGO_DB", "research")

# Languages used in the surveys (confirmed via exploration)
LANG_AR = "67629b7c7263e88e07c1ab75"
LANG_EN = "67363ca93a8adec33bd56f86"


# ---------- connection -----------------------------------------------------

_client: MongoClient | None = None


def get_db():
    """Lazy singleton MongoClient -> research db.

    One client per process; pymongo pools connections and is thread-safe.
    `serverSelectionTimeoutMS` makes a dead DB fail in ~5s instead of hanging
    every request for the default 30s.
    """
    global _client
    if _client is None:
        if not MONGO_URL:
            raise RuntimeError(
                "SURVEY_MONGO_URL must be set (e.g. in app/.env). "
                "See .env.example for the expected format."
            )
        _client = MongoClient(
            MONGO_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=20,
        )
    return _client[DB_NAME]


# ---------- helpers --------------------------------------------------------

def _oid(v: Any) -> str | None:
    """Extract an id string from a native ObjectId, Extended JSON {'$oid': ...},
    or a pass-through string."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, dict) and "$oid" in v:
        return v["$oid"]
    if isinstance(v, str):
        return v
    return None


def _to_object_id(survey_id: str) -> ObjectId:
    """Parse a hex string to ObjectId. Raises KeyError (not bson InvalidId) on bad
    input so routes can map it to a uniform 404."""
    try:
        return ObjectId(survey_id)
    except (InvalidId, TypeError):
        raise KeyError(f"{survey_id!r} is not a valid id")


def _section_object_ids(survey: dict) -> list[ObjectId]:
    """The survey's `sections` may be native ObjectIds or Extended-JSON dicts."""
    out: list[ObjectId] = []
    for s in survey.get("sections", []) or []:
        if isinstance(s, ObjectId):
            out.append(s)
            continue
        sid = _oid(s)
        if sid:
            try:
                out.append(ObjectId(sid))
            except (InvalidId, TypeError):
                continue
    return out


def _localized(value_array: list[dict] | None, prefer: str = LANG_EN) -> str:
    """Pick a localized text value. Falls back to first available if preferred lang missing."""
    if not value_array:
        return ""
    for entry in value_array:
        if _oid(entry.get("language")) == prefer:
            return strip_html(entry.get("value", ""))
    return strip_html(value_array[0].get("value", ""))


_HTML_RE = None
def strip_html(s: str) -> str:
    """Naive HTML tag stripper good enough for the survey text (mostly <p>/<strong>/<span>)."""
    global _HTML_RE
    if _HTML_RE is None:
        _HTML_RE = re.compile(r"<[^>]+>")
    if not s:
        return ""
    return _HTML_RE.sub("", s).strip()


def _section_title(sec: dict) -> str:
    """Best-effort human title for a survey section. The schema varies, so try the
    common string fields first, then fall back to localized `value`-array fields."""
    for key in ("name", "title", "header", "label", "sectionLabel"):
        v = sec.get(key)
        if isinstance(v, str) and v.strip():
            return strip_html(v)
        if isinstance(v, list):  # localized [{language, value}, ...]
            t = _localized(v)
            if t:
                return t
    v = sec.get("value")
    if isinstance(v, list):
        return _localized(v)
    if isinstance(v, str):
        return strip_html(v)
    return ""


def jsonable(v: Any) -> Any:
    """Recursively convert BSON types (ObjectId, datetime) to JSON-safe primitives."""
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    return v


# ---------- dataclasses ----------------------------------------------------

@dataclass
class QuestionMeta:
    sqlQuestionId: int
    header: str | None
    type: str
    label: str | None
    text: str
    options: list[dict] = field(default_factory=list)  # for choice questions
    section: str | None = None  # human title of the survey section this question sits in

    def to_dict(self) -> dict:
        return {
            "sqlQuestionId": self.sqlQuestionId,
            "header": self.header,
            "type": self.type,
            "label": self.label,
            "text": self.text,
            "options": self.options,
            "section": self.section,
        }


@dataclass
class SurveySummary:
    id: str
    name: str
    respondent_count: int | None = None  # None == not yet counted (lazy)


# ---------- surveys + questions (static-ish, cached) -----------------------

@lru_cache(maxsize=128)
def get_survey(survey_id: str) -> dict:
    """Raw survey document (native BSON). Raises KeyError if not found.

    Cached: survey definitions are static within a session. Restart the server
    to pick up survey edits.
    """
    oid = _to_object_id(survey_id)
    doc = get_db().surveys.find_one({"_id": oid})
    if doc is None:
        raise KeyError(f"Survey {survey_id} not found")
    return doc


@lru_cache(maxsize=128)
def get_sections(survey_id: str) -> tuple[dict, ...]:
    """Section docs in the survey's declared order. Cached (tuple => hashable)."""
    survey = get_survey(survey_id)
    section_ids = _section_object_ids(survey)
    if not section_ids:
        return ()
    found = {s["_id"]: s for s in get_db().sections.find({"_id": {"$in": section_ids}})}
    return tuple(found[sid] for sid in section_ids if sid in found)


@lru_cache(maxsize=128)
def get_survey_questions(survey_id: str) -> tuple[QuestionMeta, ...]:
    """Flatten all questions across all sections into an ordered tuple of QuestionMeta.

    We skip nested gridQuestion subtypes for predictability — those represent rows
    of a matrix and aren't directly answered in the respondent.questions list.
    """
    sections = get_sections(survey_id)
    out: list[QuestionMeta] = []
    for sec in sections:
        sec_title = _section_title(sec)
        for q in sec.get("questions", []):
            if not q.get("isActive", True):
                continue
            qtype = q.get("type")
            if qtype == "gridQuestion":  # nested row of a parent grid
                continue
            options = []
            for a in q.get("answers", []) or []:
                if not a.get("isActive", True):
                    continue
                options.append({
                    "sqlAnswerId": a.get("sqlAnswerId"),
                    "header": a.get("header"),
                    "label": a.get("answerLabel"),
                    "text": _localized(a.get("value")),
                    "position": a.get("position"),
                })
            out.append(QuestionMeta(
                sqlQuestionId=q.get("sqlQuestionId"),
                header=q.get("header"),
                type=qtype,
                label=q.get("questionLabel"),
                text=_localized(q.get("value")),
                options=options,
                section=sec_title,
            ))
    return tuple(out)


def get_question(survey_id: str, sql_question_id: int) -> QuestionMeta | None:
    for q in get_survey_questions(survey_id):
        if q.sqlQuestionId == sql_question_id:
            return q
    return None


def list_surveys(search: str | None = None, limit: int = 50) -> list[SurveySummary]:
    """List surveys, optionally filtered by a case-insensitive name search.

    Respondent counts are intentionally NOT computed here (lazy) — they would
    require one count query per survey. The count is loaded after a survey is
    selected, via respondents_meta()'s `total`.
    """
    query: dict[str, Any] = {}
    if search and search.strip():
        query["name"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    cursor = (
        get_db().surveys
        .find(query, {"_id": 1, "name": 1})
        .sort("name", ASCENDING)
        .limit(max(1, limit))
    )
    return [
        SurveySummary(id=str(s["_id"]), name=s.get("name", str(s["_id"])))
        for s in cursor
    ]


# ---------- respondents (live) ---------------------------------------------

# Build the answered_count = $size(questions) expression once, guarding against
# missing or non-array `questions` fields.
_ANSWERED_COUNT_EXPR = {
    "$size": {
        "$ifNull": [
            {"$cond": [{"$isArray": "$questions"}, "$questions", []]},
            [],
        ]
    }
}


def filter_respondents(
    survey_id: str,
    *,
    min_answers: int | None = None,
    max_answers: int | None = None,
    status: list[str] | None = None,
    n: int = 50,
    seed: int | None = None,  # accepted for API compat; $sample has no seed (no-op)
) -> list[dict]:
    """Return up to `n` respondents matching the filters, randomly sampled.

    Single aggregation: narrow by surveyId (+status) using the index, compute the
    answered_count, filter on its range, then $sample N and project only the
    fields the UI needs.
    """
    sid = _to_object_id(survey_id)

    pre_match: dict[str, Any] = {"surveyId": sid}
    if status:
        pre_match["status"] = {"$in": list(status)}

    pipeline: list[dict] = [
        {"$match": pre_match},
        {"$addFields": {"answered_count": _ANSWERED_COUNT_EXPR}},
    ]

    count_match: dict[str, Any] = {}
    if min_answers is not None:
        count_match.setdefault("answered_count", {})["$gte"] = min_answers
    if max_answers is not None:
        count_match.setdefault("answered_count", {})["$lte"] = max_answers
    if count_match:
        pipeline.append({"$match": count_match})

    pipeline.append({"$sample": {"size": max(1, n)}})
    pipeline.append({"$project": {"_id": 1, "status": 1, "answered_count": 1}})

    docs = get_db().respondents.aggregate(pipeline, allowDiskUse=True)
    return [
        {
            "id": str(d["_id"]),
            "status": d.get("status"),
            "answered_count": d.get("answered_count", 0),
        }
        for d in docs
    ]


# respondents_meta is expensive (full scan of a survey's respondents) and the
# counts grow over time, so we cache it with a short TTL rather than forever.
_meta_cache: dict[str, tuple[float, dict]] = {}
_META_TTL = 60.0


def respondents_meta(survey_id: str) -> dict:
    """Aggregate stats for the UI: min/max answer count, total, status histogram.

    Cached for 60s. Raises KeyError if the survey id is malformed (uniform 404).
    """
    now = time.time()
    hit = _meta_cache.get(survey_id)
    if hit and now - hit[0] < _META_TTL:
        return hit[1]
    val = _respondents_meta_uncached(survey_id)
    _meta_cache[survey_id] = (now, val)
    return val


def _respondents_meta_uncached(survey_id: str) -> dict:
    sid = _to_object_id(survey_id)
    pipeline = [
        {"$match": {"surveyId": sid}},
        {"$addFields": {"answered_count": _ANSWERED_COUNT_EXPR}},
        {"$facet": {
            "bounds": [{"$group": {
                "_id": None,
                "min_answers": {"$min": "$answered_count"},
                "max_answers": {"$max": "$answered_count"},
                "total": {"$sum": 1},
            }}],
            "statuses": [
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ],
        }},
    ]
    res = list(get_db().respondents.aggregate(pipeline, allowDiskUse=True))
    facet = res[0] if res else {"bounds": [], "statuses": []}
    bounds = facet["bounds"][0] if facet["bounds"] else {}
    statuses = {(row["_id"] or "unknown"): row["count"] for row in facet["statuses"]}
    return {
        "min_answers": bounds.get("min_answers", 0) or 0,
        "max_answers": bounds.get("max_answers", 0) or 0,
        "total": bounds.get("total", 0),
        "statuses": statuses,
    }


def get_respondent(survey_id: str, respondent_id: str) -> dict:
    """Load one respondent document (native BSON). Raises KeyError if not found.

    The surveyId clause both uses the index and prevents loading a respondent
    that belongs to a different survey. NOT cached — must reflect live data.
    """
    try:
        rid = ObjectId(respondent_id)
    except (InvalidId, TypeError):
        raise KeyError(f"Respondent {respondent_id!r} is not a valid id")
    doc = get_db().respondents.find_one({"_id": rid, "surveyId": _to_object_id(survey_id)})
    if doc is None:
        raise KeyError(f"Respondent {respondent_id} not found")
    return doc


# v1 compatibility alias (server.py historically called this `load_respondent`).
load_respondent = get_respondent


def respondent_answers_by_qid(respondent: dict) -> dict[int, dict]:
    return {q["sqlQuestionId"]: q for q in respondent.get("questions", []) if "sqlQuestionId" in q}


# ---------- eligible cohort (PRD: submitted + not-excluded + date range) ----

def parse_submit_date(s: str | None) -> datetime | None:
    """Parse an ISO datetime or 'YYYY-MM-DD' into a naive-UTC datetime.

    submitDate is stored naive-UTC, so we strip tz to compare like-for-like.
    Returns None for empty input. Raises ValueError on a malformed string.
    """
    if not s or not str(s).strip():
        return None
    s = str(s).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%d")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _eligible_match(
    survey_id: str,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_all: bool = False,
) -> dict:
    """Mongo filter for the eligible universe: status=submitted AND exclude empty
    (False / null / missing), optionally narrowed by submitDate range."""
    match: dict[str, Any] = {
        "surveyId": _to_object_id(survey_id),
        "status": "submitted",
        "$or": [
            {"exclude": {"$in": [False, None]}},
            {"exclude": {"$exists": False}},
        ],
    }
    if not include_all and (date_from is not None or date_to is not None):
        rng: dict[str, Any] = {}
        if date_from is not None:
            rng["$gte"] = date_from
        if date_to is not None:
            rng["$lte"] = date_to
        match["submitDate"] = rng
    return match


def count_eligible(
    survey_id: str,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_all: bool = False,
) -> int:
    return get_db().respondents.count_documents(
        _eligible_match(survey_id, date_from=date_from, date_to=date_to, include_all=include_all)
    )


def sample_eligible(
    survey_id: str,
    n: int,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_all: bool = False,
) -> list[dict]:
    """Random sample of up to `n` eligible respondents (full docs, incl. questions)."""
    match = _eligible_match(survey_id, date_from=date_from, date_to=date_to, include_all=include_all)
    pipeline = [
        {"$match": match},
        {"$sample": {"size": max(1, n)}},
        {"$project": {"_id": 1, "status": 1, "submitDate": 1, "questions": 1}},
    ]
    return list(get_db().respondents.aggregate(pipeline, allowDiskUse=True))


# sample_eligible_capped is just sample_eligible with n=cap — $sample guarantees the
# DB returns at most `cap` docs even when the eligible universe is huge.
sample_eligible_capped = sample_eligible


def submit_date_bounds(survey_id: str) -> dict:
    """Min/max submitDate over the eligible universe (for the date picker defaults)."""
    match = _eligible_match(survey_id, include_all=True)
    res = list(get_db().respondents.aggregate([
        {"$match": match},
        {"$group": {"_id": None, "min": {"$min": "$submitDate"}, "max": {"$max": "$submitDate"}}},
    ], allowDiskUse=True))
    row = res[0] if res else {}
    return {"min": jsonable(row.get("min")), "max": jsonable(row.get("max"))}
