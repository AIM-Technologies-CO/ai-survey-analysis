"""Persist MCP tool results as shareable artifacts, served at /view/{id}.

One JSON file per result under shared/ so links survive restarts (unlike the
in-memory job registry). Ids are unguessable uuid4 hex — that is the only access
control, matching the rest of the app (no auth anywhere).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone

from config import settings

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_LOCK = threading.Lock()  # tools run in worker threads; serialize workspace upserts


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def base_url() -> str:
    return (settings.public_base_url or f"http://localhost:{settings.port}").rstrip("/")


def view_url(result_id: str) -> str:
    return f"{base_url()}/view/{result_id}"


def save(kind: str, survey_id: str | None, payload: dict) -> str:
    """Write one shareable result; returns its id."""
    rid = uuid.uuid4().hex
    rec = {
        "id": rid,
        "kind": kind,  # suggest | ask | preview | job
        "survey_id": survey_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "payload": payload,
    }
    settings.shared_dir.mkdir(parents=True, exist_ok=True)
    path = settings.shared_dir / f"{rid}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8")
    return rid


def load(result_id: str) -> dict:
    if not _ID_RE.match(result_id or ""):
        raise KeyError("result not found")
    path = settings.shared_dir / f"{result_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise KeyError("result not found")


# ---- living workspaces: ONE page per survey, sections updated as work happens ----

def _index_path():
    return settings.shared_dir / "_workspaces.json"


def workspace_id(survey_id: str) -> str | None:
    """The existing workspace id for a survey, if any."""
    try:
        return json.loads(_index_path().read_text(encoding="utf-8")).get(survey_id)
    except FileNotFoundError:
        return None


def upsert_workspace(survey_id: str, survey_name: str, section: str, data,
                     *, merge_questions: list[dict] | None = None) -> str:
    """Create-or-update the survey's workspace page and return its id.

    `section` ("questions" | "ask" | "preview" | "job") is replaced with `data`.
    `merge_questions` additionally folds questions into the cumulative "questions"
    section (deduped by text), the way the web UI's builder accumulates them.
    """
    with _LOCK:
        settings.shared_dir.mkdir(parents=True, exist_ok=True)
        try:
            idx = json.loads(_index_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            idx = {}
        wid = idx.get(survey_id)
        rec = None
        if wid:
            try:
                rec = load(wid)
            except KeyError:
                rec = None
        if rec is None:
            wid = uuid.uuid4().hex
            idx[survey_id] = wid
            _index_path().write_text(json.dumps(idx), encoding="utf-8")
            rec = {"id": wid, "kind": "workspace", "survey_id": survey_id,
                   "created_at": _now(), "payload": {}}

        payload = rec["payload"]
        payload["survey_name"] = survey_name
        if data is not None:
            payload[section] = data
        if merge_questions:
            qsec = payload.setdefault("questions", {})
            qlist = qsec.setdefault("questions", [])
            seen = {q.get("text", "").strip().lower() for q in qlist}
            for q in merge_questions:
                key = q.get("text", "").strip().lower()
                if key and key not in seen:
                    qlist.append(q)
                    seen.add(key)
        rec["updated_at"] = _now()
        (settings.shared_dir / f"{wid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8")
        return wid
