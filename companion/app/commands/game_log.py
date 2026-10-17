"""Game log: Live Editor's log file, tailed and parsed, for Activity, doctor and the CLI.

In-game worker failures such as LE refusing ``TransferPlayer`` because the
squad is full are written only to ``Logs\\live_editor_<date>.log``. This module
glues ``platform/le_logs`` (find + tail, read-only) to ``domain/game_log``
(parse + ring buffer) and keeps the poll memory on ``Services.game_log``.

Nothing here writes anywhere; nothing persists. Log text is data.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ...domain.game_log import (
    DEFAULT_CAPACITY,
    FILTER_WORKER,
    GameLogBuffer,
    LogEntry,
    summarize,
)
from ...platform import le_logs

#: How long a resolved Logs directory is trusted before re-checking the registry.
DIR_CACHE_SECONDS = 30.0
#: UI poll cadence while the Game log tab is visible.
POLL_MS = 2500
#: Ceiling for a full-file catch-up (doctor / CLI) in one call.
CATCH_UP_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GameLogView:
    """What a renderer needs. ``entries`` are already filtered, newest first."""

    entries: tuple[LogEntry, ...]
    version: int
    file_name: str
    logs_dir: str
    source: str
    message: str      # "" when fine, else why there is nothing to show
    total: int        # entries held in the buffer (all filters)


class GameLogMonitor:
    """Tail + buffer pair, safe to poll from a worker thread.

    ``start="end"`` (the UI) shows the recent tail of a long file at once;
    ``start="start"`` reads the whole file (doctor tally, CLI).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY, *, start: str = "end",
                 max_bytes: int = le_logs.MAX_READ_BYTES) -> None:
        self._lock = threading.RLock()
        self.start = start
        self.buffer = GameLogBuffer(capacity)
        self.tail = le_logs.LogTail(start=start, max_bytes=max_bytes)
        self.logs_dir: le_logs.LogsDir = le_logs.LogsDir(None)
        self._dir_checked = 0.0
        self._scanner: GameLogMonitor | None = None

    # -- polling --------------------------------------------------------------

    def poll(self, logs_dir: le_logs.LogsDir, *, today: date, known_ids: Iterable[str] = ()) -> bool:
        """One capped read. True when the buffer changed."""
        with self._lock:
            before = self.buffer.version
            if logs_dir.path != self.logs_dir.path:
                self.logs_dir = logs_dir
                self.tail = le_logs.LogTail(start=self.start, max_bytes=self.tail.max_bytes)
            else:
                self.logs_dir = logs_dir
            known = tuple(known_ids)
            for chunk in self.tail.poll(logs_dir.path, today=today):
                if chunk.reset:
                    self.buffer.begin_file(chunk.path.name, chunk.file_date)
                self.buffer.ingest_lines(chunk.lines, known_ids=known)
            return self.buffer.version != before

    def catch_up(self, logs_dir: le_logs.LogsDir, *, today: date, known_ids: Iterable[str] = (),
                 max_total: int = CATCH_UP_MAX_BYTES) -> None:
        """Poll until the current file is read to EOF (bounded by ``max_total``)."""
        with self._lock:
            spent = 0
            while spent < max_total:
                offset_before = self.tail.offset
                path_before = self.tail.path
                self.poll(logs_dir, today=today, known_ids=known_ids)
                if self.tail.path is None or self.tail.at_eof():
                    break
                if self.tail.path == path_before and self.tail.offset == offset_before:
                    break
                spent += self.tail.max_bytes

    def view(self, mode: str = FILTER_WORKER, *, limit: int | None = None) -> GameLogView:
        with self._lock:
            entries = tuple(self.buffer.filtered(mode, limit=limit))
            return GameLogView(
                entries=entries,
                version=self.buffer.version,
                file_name=self.buffer.file_name or (self.tail.path.name if self.tail.path else ""),
                logs_dir=str(self.logs_dir.path or ""),
                source=self.logs_dir.source,
                message=_why_empty(self.logs_dir, self.tail),
                total=len(self.buffer.entries),
            )

    def for_job(self, job_id: str) -> list[LogEntry]:
        with self._lock:
            return self.buffer.for_job(job_id)

    def scanner(self) -> "GameLogMonitor":
        """A whole-file reader kept beside the live tail (doctor tally)."""
        with self._lock:
            if self._scanner is None:
                self._scanner = GameLogMonitor(capacity=50, start="start")
            return self._scanner


def _why_empty(logs_dir: le_logs.LogsDir, tail: le_logs.LogTail) -> str:
    if logs_dir.path is None:
        return f"Live Editor Logs folder not found ({logs_dir.detail})."
    if tail.path is None:
        return f"No live_editor_<date>.log in {logs_dir.path} yet. Start FC 26 through Live Editor."
    return ""


# --- svc glue ------------------------------------------------------------------

_FALLBACK: dict[int, GameLogMonitor] = {}


def monitor_for(svc: Any) -> GameLogMonitor:
    found = getattr(svc, "game_log", None)
    if isinstance(found, GameLogMonitor):
        return found
    # Test doubles (SimpleNamespace) have no slot for it.
    return _FALLBACK.setdefault(id(svc), GameLogMonitor())


def _prefs(svc: Any) -> dict[str, Any]:
    try:
        return dict(svc.store.snapshot().prefs.values)
    except Exception:  # noqa: BLE001
        return {}


def _now_ts(svc: Any) -> float:
    clock = getattr(svc, "clock", None)
    try:
        return float(clock.now_ts()) if clock is not None else datetime.now().timestamp()
    except Exception:  # noqa: BLE001
        return datetime.now().timestamp()


def local_today(svc: Any) -> date:
    """LE names its files by *local* date."""
    return datetime.fromtimestamp(_now_ts(svc)).date()


def _hint(svc: Any) -> Path | None:
    try:
        return Path(svc.paths.root)
    except Exception:  # noqa: BLE001
        return None


def resolve_dir(svc: Any, *, fresh: bool = False) -> le_logs.LogsDir:
    """The Logs dir for this app (pref override honoured), cached briefly."""
    monitor = monitor_for(svc)
    now = _now_ts(svc)
    override = _prefs(svc).get(le_logs.PREF_LOGS_DIR)
    cached = monitor.logs_dir
    if (
        not fresh
        and cached.path is not None
        and now - monitor._dir_checked < DIR_CACHE_SECONDS
        and (cached.source == "pref") == bool(str(override or "").strip())
    ):
        return cached
    resolved = le_logs.resolve_logs_dir(override=override, hint=_hint(svc))
    monitor._dir_checked = now
    return resolved


def known_job_ids(svc: Any) -> tuple[str, ...]:
    try:
        jobs = svc.store.snapshot().jobs
    except Exception:  # noqa: BLE001
        return ()
    ids: list[str] = []
    for bucket in (getattr(jobs, "active", None), getattr(jobs, "history", None)):
        if isinstance(bucket, Mapping):
            ids.extend(str(k) for k in bucket)
        elif isinstance(bucket, (list, tuple)):
            for item in bucket:
                jid = item.get("job_id") if isinstance(item, dict) else getattr(item, "job_id", "")
                if jid:
                    ids.append(str(jid))
    return tuple(dict.fromkeys(ids))[:200]


def poll(svc: Any) -> bool:
    """One capped incremental read for the live view. Never raises."""
    try:
        monitor = monitor_for(svc)
        return monitor.poll(resolve_dir(svc), today=local_today(svc), known_ids=known_job_ids(svc))
    except Exception:  # noqa: BLE001 - a diagnostic must not break the app
        return False


def view(svc: Any, mode: str = FILTER_WORKER, *, limit: int | None = 200) -> GameLogView:
    return monitor_for(svc).view(mode, limit=limit)


def entries_for_job(svc: Any, job_id: str) -> list[LogEntry]:
    return monitor_for(svc).for_job(job_id)


# --- doctor ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GameLogSummary:
    ok: bool
    detail: str
    worker_errors: int
    other_errors: int
    file_name: str
    logs_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "detail": self.detail, "worker_errors": self.worker_errors,
            "other_errors": self.other_errors, "file": self.file_name, "logs_dir": self.logs_dir,
        }


def summary(svc: Any) -> GameLogSummary:
    """Worker errors in the current LE log, read whole-file and incrementally cached."""
    logs_dir = resolve_dir(svc)
    if logs_dir.path is None:
        return GameLogSummary(True, f"Game log: Live Editor Logs folder not found ({logs_dir.detail})", 0, 0, "", "")
    today = local_today(svc)
    scanner = monitor_for(svc).scanner()
    scanner.catch_up(logs_dir, today=today, known_ids=known_job_ids(svc))
    path = scanner.tail.path
    if path is None:
        return GameLogSummary(True, f"Game log: no live_editor log in {logs_dir.path} yet", 0, 0, "", str(logs_dir.path))
    tally = scanner.buffer.tally
    file_day = le_logs.log_date(path)
    is_today = file_day == today or _modified_today(path, today)
    text = summarize(tally, file_name=path.name, is_today=is_today)
    return GameLogSummary(tally.worker == 0, text, tally.worker, tally.other, path.name, str(logs_dir.path))


def _modified_today(path: Path, today: date) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).date() == today
    except OSError:
        return False


def read_recent(svc: Any, *, mode: str = FILTER_WORKER, lines: int = 50) -> tuple[GameLogView, GameLogSummary | None]:
    """Whole current file into a fresh buffer (CLI). Does not disturb the live tail."""
    logs_dir = resolve_dir(svc, fresh=True)
    capacity = max(DEFAULT_CAPACITY, int(lines) * 4)
    reader = GameLogMonitor(capacity=capacity, start="start")
    if logs_dir.path is not None:
        reader.catch_up(logs_dir, today=local_today(svc), known_ids=known_job_ids(svc))
    else:
        reader.logs_dir = logs_dir
    view_ = reader.view(mode, limit=max(1, int(lines)))
    tally = reader.buffer.tally
    path = reader.tail.path
    summ = None
    if path is not None:
        is_today = le_logs.log_date(path) == local_today(svc) or _modified_today(path, local_today(svc))
        summ = GameLogSummary(
            tally.worker == 0, summarize(tally, file_name=path.name, is_today=is_today),
            tally.worker, tally.other, path.name, str(logs_dir.path or ""),
        )
    return view_, summ


__all__ = [
    "GameLogMonitor",
    "GameLogSummary",
    "GameLogView",
    "POLL_MS",
    "entries_for_job",
    "known_job_ids",
    "local_today",
    "monitor_for",
    "poll",
    "read_recent",
    "resolve_dir",
    "summary",
    "view",
]
