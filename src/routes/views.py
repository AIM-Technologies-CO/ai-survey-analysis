"""Shareable read-only pages for MCP-produced artifacts, plus an activity feed.

/view/{id}  -> static viewer page (fetches /api/results/{id}; job views also poll
               /api/jobs/{job_id} live)
/activity   -> feed of researcher + Claude (MCP) actions from tracking/events.jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from services import result_store

router = APIRouter(tags=["views"])

_STATIC = Path(__file__).resolve().parents[1] / "static"
_EVENTS = Path(__file__).resolve().parents[2] / "tracking" / "events.jsonl"


@router.get("/view/{result_id}")
def view_page(result_id: str):
    return FileResponse(str(_STATIC / "view.html"))


@router.get("/api/results/{result_id}")
def get_result(result_id: str):
    try:
        return result_store.load(result_id)
    except KeyError:
        raise HTTPException(404, "result not found")


@router.get("/activity")
def activity_page():
    return FileResponse(str(_STATIC / "activity.html"))


@router.get("/api/activity")
def get_activity(limit: int = Query(100, ge=1, le=500), session_id: str | None = None):
    try:
        lines = _EVENTS.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    events = []
    for line in reversed(lines):  # newest first
        if len(events) >= limit:
            break
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if session_id and ev.get("session_id") != session_id:
            continue
        events.append(ev)
    return {"events": events}
