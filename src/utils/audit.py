"""Append-only audit trail for an agent run (one JSON object per line)."""

from __future__ import annotations

import json
import time
from pathlib import Path


class AuditTrail:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        record = {"ts": time.time(), **record}
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except Exception:
            # Auditing must never break a run.
            pass
