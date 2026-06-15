"""Convert a survey's usable respondents into the flat survey.xlsx the agent analyzes.

Reuses the shared Mongo data layer (services/data.py). "Usable" = the PRD cohort:
status == 'submitted' AND exclude empty (False / null / missing). One row per
respondent; one column per question label (answers joined); metadata columns kept.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from config import settings
from services import data
from utils.logging_config.logger import get_logger

logger = get_logger()

_META_COLS = ["respondentId", "status", "submitDate"]


def _extract_answer(question: dict) -> str:
    parts: list[str] = []
    for ans in question.get("answers", []) or []:
        if not isinstance(ans, dict):
            parts.append(str(ans))
            continue
        v = ans.get("answer") or ans.get("value") or ans.get("header")
        if v not in (None, ""):
            parts.append(str(v))
    return "; ".join(parts)


def build_data_dictionary(survey_id: str) -> dict[str, str]:
    """label -> full question text, from the survey definition (for DATA_DICTIONARY.md)."""
    out: dict[str, str] = {}
    try:
        for q in data.get_survey_questions(survey_id):
            if q.label and q.text:
                out[q.label] = q.text
    except Exception:
        logger.exception("data dictionary build failed for %s", survey_id)
    return out


def _collect_rows(survey_id: str, match: dict, ordered_labels: list[str], extra: dict | None = None) -> list[dict]:
    """Build flat respondent rows for a Mongo match, appending any new question
    labels to the shared `ordered_labels` (so multiple cohorts share one column set).
    `extra` adds constant columns to every row (e.g. a wave label)."""
    db = data.get_db()
    projection = {"_id": 1, "status": 1, "submitDate": 1, "questions": 1}
    cap = settings.segmentation_row_cap
    cursor = db.respondents.find(match, projection)
    if cap and cap > 0:
        cursor = cursor.limit(cap)

    rows: list[dict] = []
    for r in cursor:
        row: dict = {
            "respondentId": str(r.get("_id", "")),
            "status": r.get("status"),
            "submitDate": data.jsonable(r.get("submitDate")),
        }
        if extra:
            row.update(extra)
        for q in r.get("questions", []) or []:
            label = q.get("label") or q.get("header")
            if not label or label in _META_COLS:
                continue
            ans = _extract_answer(q)
            if label in row:
                if ans:
                    row[label] = f"{row[label]}; {ans}".strip("; ") if row[label] else ans
            else:
                row[label] = ans
                if label not in ordered_labels:
                    ordered_labels.append(label)
        rows.append(row)
    return rows


def export_survey_to_xlsx(
    survey_id: str,
    dest_path: str | Path,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_all: bool = True,
) -> dict:
    """Write usable respondents of a survey to a flat Excel at dest_path.

    When ``include_all`` is False, the cohort is narrowed to submitDate in
    [date_from, date_to]. Defaults preserve the original behavior (all dates).
    """
    match = data._eligible_match(
        survey_id, date_from=date_from, date_to=date_to, include_all=include_all
    )
    ordered_labels: list[str] = []
    rows = _collect_rows(survey_id, match, ordered_labels)

    columns = _META_COLS + ordered_labels
    df = pd.DataFrame(rows).reindex(columns=columns)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(dest, index=False, engine="openpyxl")

    logger.info(
        "segmentation export %s -> %s (%d rows, %d label columns)",
        survey_id, dest, len(rows), len(ordered_labels),
    )
    return {"rows": len(rows), "labels": ordered_labels, "columns": columns}


def export_waves_to_xlsx(survey_id: str, dest_path: str | Path, windows: list[dict]) -> dict:
    """Write a wave-over-wave dataset: one Excel where each row carries a ``wave``
    column. ``windows`` = [{"label", "survey_id"?, "date_from"?, "date_to"?}]. Each wave
    is pulled either from a sibling survey (its own ``survey_id``, whole cohort) or as a
    date window of the base survey, then tagged with its label. Returns per-wave counts."""
    ordered_labels: list[str] = []
    all_rows: list[dict] = []
    wave_counts: list[dict] = []
    for w in windows:
        sid = w.get("survey_id") or survey_id
        whole = w.get("date_from") is None and w.get("date_to") is None
        match = data._eligible_match(
            sid, date_from=w.get("date_from"), date_to=w.get("date_to"), include_all=whole
        )
        rows = _collect_rows(sid, match, ordered_labels, extra={"wave": w["label"]})
        wave_counts.append({"label": w["label"], "rows": len(rows)})
        all_rows.extend(rows)

    # `wave` leads so it's the obvious grouping column; metadata then question labels.
    columns = ["wave"] + _META_COLS + ordered_labels
    df = pd.DataFrame(all_rows).reindex(columns=columns)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(dest, index=False, engine="openpyxl")

    logger.info(
        "wave export %s -> %s (%s, %d label columns)",
        survey_id, dest, ", ".join(f"{c['label']}={c['rows']}" for c in wave_counts), len(ordered_labels),
    )
    return {"rows": len(all_rows), "labels": ordered_labels, "columns": columns, "waves": wave_counts}
