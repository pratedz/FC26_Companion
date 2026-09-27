"""Persist last N apply/export jobs for Job history UI."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

MAX_ENTRIES = 40


def _path() -> Path:
    return paths.app_root() / "job_history.json"


def load() -> List[Dict[str, Any]]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def add(
    *,
    kind: str,
    detail: str,
    outcome: str,
    reason: str = "",
    target_id: Optional[int] = None,
    queue_file: str = "",
    last_result: str = "",
) -> None:
    rows = load()
    rows.insert(
        0,
        {
            "ts": time.time(),
            "kind": kind,
            "detail": (detail or "")[:200],
            "outcome": outcome,
            "reason": (reason or "")[:240],
            "target_id": target_id,
            "queue_file": queue_file,
            "last_result": (last_result or "")[:120],
        },
    )
    rows = rows[:MAX_ENTRIES]
    try:
        # Compact JSON — history is rewritten every boost; indent costs I/O
        _path().write_text(
            json.dumps(rows, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def format_line(row: Dict[str, Any]) -> str:
    import datetime as _dt

    ts = row.get("ts") or 0
    try:
        when = _dt.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        when = "??"
    oc = str(row.get("outcome") or "?")
    kind = str(row.get("kind") or "job")
    detail = str(row.get("detail") or "")
    tid = row.get("target_id")
    tid_s = f" id={tid}" if tid else ""
    return f"[{when}] {oc:12} {kind}{tid_s} · {detail}"
