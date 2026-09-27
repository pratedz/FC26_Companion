"""Boost multi-run queue — fire many Runs without waiting for each LE apply.

UI problem: wait=True + _apply_busy blocked every Run until LE finished (~seconds).
clear_stale=True also wiped sibling jobs so only one boost could live in queue/.

This manager:
  1. Accepts many enqueue() calls immediately (UI never blocks on LE).
  2. Writes jobs to queue/ serially with wait=False, clear_stale=False
     (only optional one-shot clear when the client queue was empty).
  3. Background watcher marks applied/error when LE archives each file.

LE still drains Lua one-at-a-time (bridge design); "parallel" here means
no wasted UI wait — you can stack Match Day + Morale + Contracts in one click storm.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import le_apply
from . import paths
from . import product

Listener = Callable[[], None]

# How long to watch a queued job for LE completion
_WATCH_TIMEOUT_SEC = 90.0
_WATCH_POLL_SEC = 0.25
_MAX_HISTORY = 40
# Grace after the job file disappears before we call a missing done/ artifact
# a failure (finish_job writes done/<name> *before* deleting the queue copy,
# so this only covers filesystem lag).
_ARTIFACT_GRACE_SEC = 3.0
# done/ prefixes that are NOT proof the LE worker ran the job:
#   skipped_ = clear_stale_jobs archived it, orphan_ = companion archived it,
#   poison_  = quarantined. Counting these as "applied" is how a wiped job
#   used to report success.
_NON_BRIDGE_PREFIXES = ("skipped_", "poison_", "orphan_")


def _bridge_artifact_for(name: str, done: Path) -> Optional[Path]:
    """done/ copy that only the LE worker's finish_job() could have written."""
    if not name:
        return None
    try:
        exact = done / name
        if exact.is_file():
            return exact
        suffix = "_" + name  # bridge collision form: done/<os.time()>_<name>
        for p in done.iterdir():
            n = p.name
            if n != name and not n.endswith(suffix):
                continue
            if n.lower().startswith(_NON_BRIDGE_PREFIXES):
                continue
            if p.is_file():
                return p
    except OSError:
        return None
    return None


@dataclass
class BoostJob:
    id: str
    profile_id: str
    label: str
    status: str = "pending"  # pending|writing|queued|applied|error|cancelled
    reason: str = ""
    queue_file: str = ""
    ts: float = field(default_factory=time.time)
    pack_id: str = ""
    # First time the watcher saw the queue file gone (0.0 = still there)
    gone_ts: float = 0.0

    def line(self) -> str:
        st = self.status.upper()
        mark = {
            "pending": "·",
            "writing": "…",
            "queued": "→",
            "applied": "✓",
            "error": "✗",
            "cancelled": "–",
        }.get(self.status, "·")
        extra = f" · {self.reason}" if self.reason and self.status in ("error", "queued") else ""
        if self.status == "applied" and self.reason:
            extra = f" · {self.reason}" if len(self.reason) < 40 else ""
        return f"{mark} {st:8}  {self.label}{extra}"


class BoostQueueManager:
    """Process-wide boost queue (one instance per app session)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: List[BoostJob] = []
        self._listeners: List[Listener] = []
        self._write_cv = threading.Condition(self._lock)
        self._stop = False
        self._writer = threading.Thread(
            target=self._write_loop, name="boost-queue-writer", daemon=True
        )
        self._watcher = threading.Thread(
            target=self._watch_loop, name="boost-queue-watcher", daemon=True
        )
        self._writer.start()
        self._watcher.start()
        self._cleared_for_burst = False

    # ── public API ───────────────────────────────────────────────────

    def subscribe(self, fn: Listener) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        with self._lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass

    def enqueue_profile(
        self,
        profile_id: str,
        *,
        pack_id: str = "",
        label: Optional[str] = None,
    ) -> BoostJob:
        from . import profiles as profiles_mod

        try:
            prof = profiles_mod.get_profile(profile_id)
            lab = label or prof.label
        except Exception:
            lab = label or profile_id
        job = BoostJob(
            id=uuid.uuid4().hex[:10],
            profile_id=profile_id,
            label=lab,
            pack_id=pack_id or "",
            status="pending",
        )
        with self._write_cv:
            self._jobs.append(job)
            self._trim_locked()
            self._write_cv.notify()
        self._emit()
        return job

    def enqueue_pack(self, pack_id: str) -> List[BoostJob]:
        pack = product.PACKS.get(pack_id)
        if not pack:
            raise KeyError(f"Unknown pack {pack_id!r}")
        jobs: List[BoostJob] = []
        ids: List[str] = list(pack.get("profile_ids") or [])
        if not ids:
            return jobs
        # One clear at start of this pack burst if nothing pending to write
        for pid in ids:
            try:
                from . import profiles as profiles_mod

                lab = profiles_mod.get_profile(pid).label
            except Exception:
                lab = f"{pack.get('label', pack_id)} · {pid}"
            jobs.append(
                self.enqueue_profile(
                    pid,
                    pack_id=pack_id,
                    label=f"{pack.get('label', pack_id)} · {lab}",
                )
            )
        return jobs

    def jobs_snapshot(self) -> List[BoostJob]:
        with self._lock:
            return list(self._jobs)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            c: Dict[str, int] = {
                "pending": 0,
                "writing": 0,
                "queued": 0,
                "applied": 0,
                "error": 0,
                "cancelled": 0,
                "active": 0,
                "total": len(self._jobs),
            }
            for j in self._jobs:
                c[j.status] = c.get(j.status, 0) + 1
            c["active"] = c["pending"] + c["writing"] + c["queued"]
            return c

    def clear_finished(self) -> int:
        with self._lock:
            before = len(self._jobs)
            self._jobs = [
                j
                for j in self._jobs
                if j.status not in ("applied", "error", "cancelled")
            ]
            n = before - len(self._jobs)
        if n:
            self._emit()
        return n

    def cancel_pending(self) -> int:
        """Mark not-yet-written jobs cancelled (files already in LE stay)."""
        n = 0
        with self._lock:
            for j in self._jobs:
                if j.status == "pending":
                    j.status = "cancelled"
                    j.reason = "cancelled before write"
                    n += 1
        if n:
            self._emit()
        return n

    def summary_line(self) -> str:
        c = self.counts()
        if c["active"] == 0 and c["applied"] == 0 and c["error"] == 0:
            return "Queue empty · Run boosts anytime (no wait)"
        parts = []
        if c["active"]:
            parts.append(f"{c['active']} in flight")
        if c["applied"]:
            parts.append(f"{c['applied']} applied")
        if c["error"]:
            parts.append(f"{c['error']} failed")
        return " · ".join(parts) if parts else "Queue idle"

    # ── writer: disk only, no LE wait ────────────────────────────────

    def _write_loop(self) -> None:
        while not self._stop:
            job: Optional[BoostJob] = None
            with self._write_cv:
                while not self._stop:
                    job = self._next_pending_locked()
                    if job is not None:
                        break
                    self._write_cv.wait(timeout=0.5)
                if self._stop:
                    return
            if job is None:
                continue
            self._write_one(job)

    def _next_pending_locked(self) -> Optional[BoostJob]:
        for j in self._jobs:
            if j.status == "pending":
                return j
        return None

    def _write_one(self, job: BoostJob) -> None:
        with self._lock:
            if job.status != "pending":
                return
            job.status = "writing"
            job.reason = "writing…"
            # Clear stale LE files only once when starting from empty active write set
            active_writing = sum(
                1 for j in self._jobs if j.status in ("writing", "queued") and j is not job
            )
            do_clear = active_writing == 0 and not any(
                j.status == "queued" for j in self._jobs if j is not job
            )
        self._emit()

        try:
            # clear_stale only if nothing else is already live in our client queue
            # Still False when siblings queued — never wipe multi-run.
            r = product.run_profile_turbo(
                job.profile_id,
                wait=False,
                clear_stale=bool(do_clear),
            )
            with self._lock:
                job.queue_file = r.queue_file or ""
                job.gone_ts = 0.0
                if r.outcome in ("error", "timeout") or not r.queued:
                    job.status = "error"
                    job.reason = r.reason or f"write failed ({r.outcome})"
                else:
                    job.status = "queued"
                    job.reason = "in LE queue" if r.live else "queued (worker may be OFF)"
                    job.ts = time.time()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                job.status = "error"
                job.reason = str(e)
        self._emit()

    # ── watcher: mark applied when LE drains file ───────────────────

    def _watch_loop(self) -> None:
        while not self._stop:
            try:
                self._watch_once()
            except Exception:
                pass
            time.sleep(_WATCH_POLL_SEC)

    def _verdict_from_last_result(self, job: BoostJob) -> tuple[str, str]:
        """('applied'|'error', reason) — the bridge's own FAIL line wins."""
        name = Path(job.queue_file).name
        try:
            text = (le_apply.last_result_text() or "").strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            return "applied", "drained by LE worker"
        fresh = True
        try:
            age = le_apply.last_result_age_sec()
            if age is not None:
                # Ignore a result written before this job was queued
                fresh = (time.time() - float(age)) >= float(job.ts or 0.0) - 1.0
        except Exception:  # noqa: BLE001
            fresh = True
        if not fresh:
            return "applied", "drained by LE worker"
        low = text.lower()
        names_job = name and (name in text or f"last={name}" in low)
        if text.upper().startswith("FAIL") and names_job:
            return "error", f"LE reported failure · {text[:160]}"
        # "OK processed=N ok=0" — ran everything and everything failed
        if names_job and "processed=" in low:
            m = re.search(r"\bok=(\d+)", text, flags=re.I)
            if m and int(m.group(1)) == 0:
                return "error", f"LE ran the job but it failed · {text[:160]}"
        return "applied", "drained by LE worker"

    def _watch_once(self) -> None:
        with self._lock:
            watching = [
                j
                for j in self._jobs
                if j.status == "queued" and j.queue_file
            ]
        if not watching:
            return
        changed = False
        now = time.time()
        for job in watching:
            qf = Path(job.queue_file)
            age = now - float(job.ts or now)
            # The file leaving queue/ proves nothing on its own: clear_stale_jobs
            # and a manual delete also make it vanish. Require a bridge-written
            # done/ artifact, then let _last_result.txt veto success.
            if not qf.is_file():
                artifact = _bridge_artifact_for(qf.name, paths.queue_dir() / "done")
                if artifact is None:
                    with self._lock:
                        if job.status != "queued":
                            continue
                        if not job.gone_ts:
                            job.gone_ts = now
                            job.reason = "left queue/ — waiting for LE result"
                            continue
                        if now - job.gone_ts < _ARTIFACT_GRACE_SEC:
                            continue
                        job.status = "error"
                        job.reason = (
                            "job left queue/ without an LE artifact "
                            "(cleared or skipped — not applied)"
                        )
                        changed = True
                    continue
                verdict, note = self._verdict_from_last_result(job)
                with self._lock:
                    if job.status == "queued":
                        job.status = verdict
                        job.reason = note
                        changed = True
                continue
            if job.gone_ts:
                # File reappeared (re-armed) — restart the artifact grace window
                with self._lock:
                    job.gone_ts = 0.0
            if age > _WATCH_TIMEOUT_SEC:
                with self._lock:
                    if job.status == "queued":
                        # Still on disk — may be stuck
                        if le_apply.bridge_alive(90):
                            job.reason = f"still pending {int(age)}s"
                            # don't error yet — keep watching with soft note
                            if age > _WATCH_TIMEOUT_SEC * 2:
                                job.status = "error"
                                job.reason = f"timeout {int(age)}s — Force drain?"
                                changed = True
                        else:
                            job.status = "error"
                            job.reason = "worker OFF — job still in queue/"
                            changed = True
        if changed:
            self._emit()

    def _trim_locked(self) -> None:
        if len(self._jobs) <= _MAX_HISTORY:
            return
        # Drop oldest finished first
        finished_idx = [
            i
            for i, j in enumerate(self._jobs)
            if j.status in ("applied", "error", "cancelled")
        ]
        while len(self._jobs) > _MAX_HISTORY and finished_idx:
            i = finished_idx.pop(0)
            # adjust indices after pop — simpler: rebuild
            keep_id = self._jobs[i].id
            self._jobs = [j for j in self._jobs if j.id != keep_id]
            finished_idx = [
                i
                for i, j in enumerate(self._jobs)
                if j.status in ("applied", "error", "cancelled")
            ]
        # hard cap
        if len(self._jobs) > _MAX_HISTORY:
            self._jobs = self._jobs[-_MAX_HISTORY:]

    def _emit(self) -> None:
        listeners: List[Listener]
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn()
            except Exception:
                pass


# Session singleton
_manager: Optional[BoostQueueManager] = None
_manager_lock = threading.Lock()


def get_boost_queue() -> BoostQueueManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BoostQueueManager()
        return _manager
