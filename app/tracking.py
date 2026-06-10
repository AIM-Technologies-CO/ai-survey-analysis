"""Lightweight local tracking — append researcher actions to a JSONL log.

PRD: track which questions the researcher saw (generated) vs which they selected
or added, and record generated files. No auth in the app, so events are keyed by a
client-generated session_id purely for correlation.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_PATH = Path(__file__).parent / "tracking" / "events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(session_id: str | None, survey_id: str | None, action: str, payload: dict | None = None) -> None:
    """Append one event line. Never raises into the request path."""
    rec = {
        "ts": _now_iso(),
        "session_id": session_id,
        "survey_id": survey_id,
        "action": action,
        "payload": payload or {},
    }
    try:
        with _LOCK:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            with _PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        # Tracking must never break the feature.
        pass
