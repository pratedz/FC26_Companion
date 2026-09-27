"""FileTransport — the only thing that knows protocol v3 exists on disk.

Also home of the liveness model (§3.5): two independent facts, never one
number. ``session.json`` is the ARM marker (pid-checked, no TTL — an arm
marker does not decay); ``drain.json`` is the TICK marker (a diagnostic, not a
health verdict). v1 conflated them into a 90-second heartbeat file and told
every idle user the bridge was OFF.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ...domain.job import Job
from ...domain.outcome import ApplyOutcome, JobResult
from ..clock import Clock, SystemClock
from ..paths import AppPaths
from .jobfile import (
    atomic_write_json,
    job_filename,
    list_job_ids,
    read_json,
    read_worker_json,
    rewrite_job_index,
)


class Pill(Enum):
    """The status pill the UI renders. See the §3.5 matrix."""

    OFF = "off"
    ARMED = "armed"
    LIVE = "live"
    WAITING = "waiting"
    STALLED = "stalled"


@dataclass(frozen=True, slots=True)
class Liveness:
    pill: Pill
    message: str
    armed: bool
    pid: int | None = None
    pid_alive: bool = False
    session_id: str = ""
    core_version: str = ""
    save_uid: str = ""
    queue_depth: int = 0
    last_drain_age: float | None = None  # seconds; None = never drained
    capabilities: Mapping[str, Any] = field(default_factory=dict)


class PidProbe(Protocol):
    """Is this pid a live process? Injected so tests never need a real LE."""

    def __call__(self, pid: int) -> bool: ...


def _host_process_running() -> bool:
    """Best-effort host evidence when LE cannot expose its pid to Lua.

    Some LE builds have neither ``GetCurrentProcessId`` nor ``GetLEProcessId``;
    those correctly write ``le_pid=0``.  In that case, a valid arm session plus
    a running FC/LE process is stronger evidence than declaring the worker OFF.
    This lazy import keeps normal protocol use platform-independent.
    """
    try:
        from ...platform import procs

        observed = procs.describe()
        return bool(observed.get("game_running") or observed.get("le_running"))
    except Exception:
        return False


def _host_game_started_at() -> float | None:
    """Best-effort FC process creation time for PID-less session validation."""
    try:
        from ...platform import procs

        value = procs.describe().get("game_started_at")
        return float(value) if isinstance(value, (int, float)) else None
    except Exception:
        return None


def _default_pid_probe(pid: int) -> bool:
    """True iff ``pid`` is a running process on Windows (no signals needed)."""
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            STILL_ACTIVE = 259
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return False


# Thresholds for the WAITING/STALLED split (seconds).
_LIVE_AGE = 10.0
_STALLED_AGE = 120.0
_PIDLESS_SESSION_MAX_AGE = 24 * 60 * 60.0
_MIN_COMPAT_CORE = (2, 3, 1)
FAST_READ_WAKE = "_fast_read"
_FAST_READ_OPS = frozenset({"diag.ping", "db.dump", "export_squad", "snapshot"})


def job_requests_fast_read(job: Job) -> bool:
    """True when every op is an allow-listed read the CE timer may drain."""
    if not job.ops:
        return False
    for op in job.ops:
        if op.op == "budget":
            if str(op.body.get("action") or "get") != "get":
                return False
            continue
        if op.op not in _FAST_READ_OPS:
            return False
    return True


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    """Parse the numeric part of a worker version without accepting garbage."""
    parts = value.strip().split(".")
    if len(parts) != 3:
        return None
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError:
        return None
    if any(part < 0 for part in parsed):
        return None
    return parsed  # type: ignore[return-value]


def _session_problem(sess: Mapping[str, Any], now_ts: float) -> str:
    """Explain why this marker cannot represent the current worker build."""
    from ... import CORE_VERSION

    session_version = str(sess.get("core_version") or "")
    if session_version:
        running = _version_tuple(session_version)
        expected = _version_tuple(CORE_VERSION)
        if running is None:
            return f"worker has an invalid core version ({session_version})"
        if running < _MIN_COMPAT_CORE:
            return (
                f"worker core {session_version} is older than supported "
                f"{'.'.join(map(str, _MIN_COMPAT_CORE))}"
            )
        # A different minor line can carry protocol semantics this app does
        # not know. A newer patch on the same 2.6 line is still protocol v3;
        # refusing it is what left the app disconnected after a script copy.
        if expected is not None and running > expected:
            same_line = running[0] == expected[0] and running[1] == expected[1]
            contract = sess.get("contract")
            same_protocol = contract in (None, 3, "3")
            if not (same_line and same_protocol):
                return (
                    f"worker core {session_version} is newer than this Companion "
                    f"({CORE_VERSION})"
                )
    caps = sess.get("capabilities")
    ops = sess.get("ops")
    # Early v2 builds kept the same 2.0.0 version while these security
    # semantics changed, so capability truth is a stronger discriminator.
    if isinstance(caps, Mapping) and caps.get("atomic_rollback") is True:
        return "worker advertises obsolete atomic rollback support"
    if isinstance(ops, Mapping) and "raw_lua" in ops:
        return "worker advertises the removed raw_lua operation"

    pid = int(sess.get("le_pid") or 0)
    armed_at = sess.get("armed_at")
    if pid <= 0 and isinstance(armed_at, (int, float)):
        started = _host_game_started_at()
        if started is not None and float(armed_at) + 2.0 < started:
            return "worker marker predates the current FC 26 process"
        # If process times are inaccessible, retain a bounded fallback rather
        # than accepting a PID-less marker forever.
        if started is None and now_ts - float(armed_at) > _PIDLESS_SESSION_MAX_AGE:
            return "PID-less worker marker is from an earlier game session"
    return ""


class FileTransport:
    """Protocol v3 over the queue directory. Submit / result / liveness."""

    def __init__(
        self,
        paths: AppPaths,
        clock: Clock | None = None,
        pid_probe: PidProbe | None = None,
    ) -> None:
        self._paths = paths
        self._clock = clock or SystemClock()
        self._pid_probe = pid_probe or _default_pid_probe
        self._crash_observed: dict[str, tuple[str, float]] = {}

    # ---- submit -------------------------------------------------------

    def submit(self, job: Job) -> str:
        """Validate, write atomically to ``jobs/``, return the job_id.

        Validation happens before the file exists, so an invalid job never
        reaches the queue at all.
        """
        wire = job.to_wire(now=int(self._clock.now_ts()))
        atomic_write_json(self._paths.jobs / job_filename(job.job_id), wire)
        rewrite_job_index(self._paths.jobs)
        if job_requests_fast_read(job):
            try:
                (self._paths.queue / FAST_READ_WAKE).write_text("1", encoding="utf-8")
            except OSError:
                pass
        return job.job_id

    def cancel(self, job_id: str) -> bool:
        """Remove a job iff it has not been claimed yet."""
        p = self._paths.jobs / job_filename(job_id)
        try:
            p.unlink()
            rewrite_job_index(self._paths.jobs)
            return True
        except OSError:
            return False

    def clear_queue(self) -> int:
        """Remove pending/claimed work and resumable cursors, not history.

        The worker only discovers jobs through the two queue indexes on some
        Live Editor hosts, so both indexes are rewritten even when empty.
        """
        active = set(self.pending_ids()) | set(self.claimed_ids())
        # A missing claimed index is precisely the failure mode this command
        # must recover from. Filesystem enumeration is safe on the app side.
        for directory in (self._paths.jobs, self._paths.claimed):
            for path in directory.glob("*.json"):
                if path.name != "index.json":
                    active.add(path.stem)
                    try:
                        path.unlink()
                    except OSError:
                        pass
        for job_id in active:
            for path in (
                self._paths.state_dir / f"{job_id}.state.json",
                self._paths.results / f"{job_id}.partial.json",
            ):
                try:
                    path.unlink()
                except OSError:
                    pass
        rewrite_job_index(self._paths.jobs)
        rewrite_job_index(self._paths.claimed)
        return len(active)

    # ---- results ------------------------------------------------------

    def result(self, job_id: str) -> JobResult | None:
        """The result if one exists (terminal or partial), else None."""
        final = read_worker_json(self._paths.results / job_filename(job_id))
        if final is not None:
            result = JobResult.from_wire(final)
            if result.outcome is ApplyOutcome.CRASHED:
                live_sid = self._live_session_id()
                claim_path = self._paths.claimed / job_filename(job_id)
                claim = read_json(claim_path)
                owner = str((claim or {}).get("claimed_by") or "")
                uncertain_claim = claim_path.exists() and (not owner or owner == live_sid)
                if live_sid and uncertain_claim:
                    return JobResult.from_wire({**final, "state": "running", "outcome": "deferred",
                                                "diagnostic": "Claim is still live; waiting for the worker's final result."})
                # A synthetic owner-less crash can briefly outlive the claim
                # rename. Allow the real result replacement to land first.
                if live_sid and "claimed by session ?" in str(final.get("diagnostic") or ""):
                    stamp = repr(final)
                    seen = self._crash_observed.get(job_id)
                    if seen is None or seen[0] != stamp:
                        seen = (stamp, self._clock.monotonic())
                        self._crash_observed[job_id] = seen
                    if self._clock.monotonic() - seen[1] < 1.0:
                        return JobResult.from_wire({**final, "state": "running", "outcome": "deferred"})
            else:
                self._crash_observed.pop(job_id, None)
            return result
        partial = read_worker_json(self._paths.results / f"{job_id}.partial.json")
        if partial is not None:
            r = JobResult.from_wire(partial)
            if r.outcome.is_terminal:
                # A partial file never carries a terminal verdict.
                return JobResult.from_wire({**partial, "state": "running", "outcome": "deferred"})
            return r
        return None

    def pending_ids(self) -> list[str]:
        return list_job_ids(self._paths.jobs)

    def claimed_ids(self) -> list[str]:
        return list_job_ids(self._paths.claimed)

    def queue_depth(self) -> int:
        return len(self.pending_ids()) + len(self.claimed_ids())

    def await_result(
        self,
        job_id: str,
        *,
        timeout: float = 60.0,
        poll: float = 0.25,
        on_tick: Callable[[Liveness], None] | None = None,
    ) -> JobResult:
        """Poll until a terminal result lands or ``timeout`` passes.

        Timeout does NOT mean failure — the job may still run on a later event.
        The caller gets QUEUED/DEFERRED back and the UI says what to do
        (\"enter Career Mode or advance a day\"), never an indefinite spinner.
        """
        deadline = self._clock.monotonic() + timeout
        last: JobResult | None = None
        while True:
            last = self.result(job_id)
            if last is not None and last.outcome.is_terminal:
                return last
            if on_tick is not None:
                on_tick(self.liveness())
            if self._clock.monotonic() >= deadline:
                break
            self._clock.sleep(poll)
        if last is not None:
            return last
        still_queued = (self._paths.jobs / job_filename(job_id)).is_file()
        return JobResult.from_wire(
            {
                "job_id": job_id,
                "state": "queued" if still_queued else "running",
                "ok": False,
            }
        )

    # ---- liveness (§3.5) ----------------------------------------------

    def session(self) -> dict[str, Any] | None:
        """Read the arm marker. Fall back to the writer's .bak during replace.

        Lua ``write_atomic`` renames the live file aside before the new file
        lands. A poll in that window must still see the previous session,
        not "no file".
        """
        data = read_json(self._paths.session_file)
        if data is not None:
            return data
        backup = self._paths.session_file.with_name(self._paths.session_file.name + ".bak")
        return read_json(backup)

    def drain(self) -> dict[str, Any] | None:
        return read_json(self._paths.drain_file)

    def liveness(self) -> Liveness:
        sess = self.session()
        if sess is None:
            return Liveness(
                pill=Pill.OFF,
                message="Start LE — the companion arms itself.",
                armed=False,
            )
        problem = _session_problem(sess, self._clock.now_ts())
        save_uid = str(sess.get("save_uid") or "")
        if problem:
            return Liveness(
                pill=Pill.OFF,
                message=(
                    f"Old worker session ignored ({problem}). "
                    "Restart Live Editor and FC 26."
                ),
                armed=False,
                session_id=str(sess.get("session_id", "")),
                core_version=str(sess.get("core_version", "")),
                save_uid=save_uid,
                queue_depth=self.queue_depth(),
                capabilities=dict(sess.get("capabilities") or {}),
            )
        pid = int(sess.get("le_pid") or 0)
        pid_alive = self._pid_probe(pid)
        # PID zero means "host API unavailable", not "dead".  Confirm through
        # the process snapshot instead.  A non-zero dead PID remains decisive:
        # it prevents a stale session from attaching to an unrelated new game.
        alive = pid_alive or (pid <= 0 and _host_process_running())
        if not alive:
            unavailable = pid <= 0
            return Liveness(
                pill=Pill.OFF,
                message=(
                    "Worker session found, but FC 26 / Live Editor is not running."
                    if unavailable
                    else "LE isn't running."
                ),
                armed=False,
                pid=pid or None,
                pid_alive=False,
                session_id=str(sess.get("session_id", "")),
                core_version=str(sess.get("core_version", "")),
                save_uid=save_uid,
            )

        depth = self.queue_depth()
        drain = self.drain()
        age: float | None = None
        if drain is not None and drain.get("at"):
            # Parse the timestamp INSIDE the file, never the file's mtime —
            # mtime is refreshed by backup tools and AV scans (§3.1).
            try:
                age = max(0.0, self._clock.now_ts() - float(drain["at"]))
            except (TypeError, ValueError):
                age = None

        common = dict(
            armed=True,
            pid=pid or None,
            pid_alive=pid_alive,
            session_id=str(sess.get("session_id", "")),
            core_version=str(sess.get("core_version", "")),
            save_uid=save_uid,
            queue_depth=depth,
            last_drain_age=age,
            capabilities=dict(sess.get("capabilities") or {}),
        )
        from ... import CORE_VERSION

        compatibility_note = ""
        if (
            str(sess.get("core_version") or "")
            and str(sess.get("core_version")) != CORE_VERSION
        ):
            compatibility_note = (
                f" Core {sess.get('core_version')} is ready; new Phase 5 "
                "features activate on the next normal FC start."
            )
        if depth == 0:
            # ARMED with an empty queue is a SUCCESS state. v1 rendered OFF
            # here — the single most-reported confusion.
            return Liveness(
                pill=Pill.ARMED,
                message=(
                    "Ready. Existing features work on the next Career Mode tick."
                    + compatibility_note
                ),
                **common,
            )
        if age is not None and age < _LIVE_AGE:
            return Liveness(pill=Pill.LIVE, message="Working…", **common)
        if age is not None and age < _STALLED_AGE:
            return Liveness(
                pill=Pill.WAITING,
                message=(
                    f"{depth} job(s) queued — open Team Management or return to the Career hub "
                    "to release it safely."
                ),
                **common,
            )
        mins = int(age // 60) if age is not None else 0
        note = f"no game activity for {mins} min" if age is not None else "no drain yet"
        return Liveness(
            pill=Pill.STALLED,
            message=f"{depth} job(s) queued, {note}. Use Force Drain.",
            **common,
        )

    # ---- crash recovery ------------------------------------------------

    def _live_session_id(self) -> str:
        sess = self.session()
        if sess is not None:
            pid = int(sess.get("le_pid") or 0)
            if self._pid_probe(pid) or (pid <= 0 and _host_process_running()):
                return str(sess.get("session_id") or "")
        return ""

    def sweep_crashed(self) -> list[str]:
        """Emit a ``crashed`` result for claimed jobs from a dead session.

        The Lua core does this at arm; the Python side also sweeps so a user
        who never restarts LE still learns what happened. A claimed file whose
        session either differs from the current live session or whose session
        pid is dead can never complete.
        """
        live_sid = self._live_session_id()
        swept: list[str] = []
        for jid in self.claimed_ids():
            claimed_path = self._paths.claimed / job_filename(jid)
            job = read_json(claimed_path)
            claimed_sid = str((job or {}).get("claimed_by", ""))
            # An empty owner means the worker has renamed the file and has not
            # stamped it yet. Writing "crashed" in that window made every live
            # squad plan look failed, then the real result replaced the file
            # after Companion had already given up.
            if live_sid and (not claimed_sid or claimed_sid == live_sid):
                continue
            if (self._paths.results / job_filename(jid)).is_file():
                continue
            atomic_write_json(
                self._paths.results / job_filename(jid),
                {
                    "schema": 3,
                    "job_id": jid,
                    "state": "crashed",
                    "ok": False,
                    "outcome": "crashed",
                    "finished_at": int(self._clock.now_ts()),
                    "diagnostic": f"claimed by session {claimed_sid or '?'} which never completed it",
                    "writes_performed": None,
                },
            )
            swept.append(jid)
        return swept

    def force_drain_snippet(self) -> str:
        """Clipboard escape hatch: paste into LE's Lua Engine to drain now."""
        return (
            "if type(_G.LECompanionV2_ForceDrain) == 'function' then "
            "_G.LECompanionV2_ForceDrain() "
            'elseif Log then Log("[LEC] core not armed") end'
        )
