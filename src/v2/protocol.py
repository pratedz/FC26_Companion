"""Protocol v3 wire format: ids, atomic file I/O, results, liveness, legacy.

This module knows what the files on disk *look like*. It does not know what an
op means (see :mod:`src.v2.jobs`) and it never drains a queue (see
:mod:`src.v2.transport`). No UI imports, no globals, no module-level filesystem
access — importing this module touches nothing.

Layout under the queue directory::

    queue/
      session.json          written once per LE session, at arm.   ARM marker.
      drain.json            rewritten at the end of every drain.   TICK marker.
      jobs/<job_id>.json    one job. one file. the directory listing IS the queue.
      claimed/<job_id>.json atomically renamed here at claim time (rename = lock)
      results/<job_id>.json one result, named by job id, written last
      archive/<date>/…      finished jobs, rotated by day

What v1 did instead, and why each is gone:

* ``_run_now.lua`` + ``_pending.txt`` wrote **two files for one job**. The Lua
  worker ran ``_run_now`` first and then skipped every pending job with an
  identical body — 103 duplicate-skip events, 73% of everything drained. The
  work happened under the name ``_run_now.lua``, so 68% of results named a file
  that identifies no job. Here a job's id is its filename and its result's
  filename; there is nothing to deduplicate.
* Job bodies were **Lua source**, validated by ``_`` prefix and ``.lua`` suffix
  and then ``load()``+``pcall()``ed. Appending one line to ``_pending.txt`` was
  arbitrary code execution inside FC 26. Jobs here are declarative ops.
* Liveness was one 90-second mtime check on ``_bridge_alive.txt``, so a
  correctly armed worker read ``WORKER OFF`` the moment the user stopped
  clicking. :class:`LiveState` keeps ``armed`` and ``last_drain_age`` apart, and
  reads timestamps out of the file *content* — mtime is the wrong clock, a
  backup tool or folder sync can forge it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .outcomes import Outcome, derive_job_outcome, parse_outcome

PROTOCOL_VERSION = 3
RESULT_VERSION = 3

# --- v3 layout ---------------------------------------------------------------
JOBS_DIR = "jobs"
CLAIMED_DIR = "claimed"
RESULTS_DIR = "results"
ARCHIVE_DIR = "archive"
SESSION_NAME = "session.json"
DRAIN_NAME = "drain.json"
SUBDIRS: Tuple[str, ...] = (JOBS_DIR, CLAIMED_DIR, RESULTS_DIR, ARCHIVE_DIR)

# --- v1 layout, read-only (migration) ----------------------------------------
LEGACY_JOB_STATUS = "_job_status.txt"
LEGACY_LAST_RESULT = "_last_result.txt"
LEGACY_ALIVE = "_bridge_alive.txt"
LEGACY_ARMED = "_bridge_armed.txt"
LEGACY_PENDING = "_pending.txt"
LEGACY_RUN_NOW = "_run_now.lua"

# Liveness thresholds. These describe *queue pressure*, never arm state:
# an armed session with an empty queue is healthy no matter how old the drain.
LIVE_WINDOW_SEC = 10.0
STALL_AFTER_SEC = 120.0

#: Job ids name files in four directories. Anything outside this class can
#: traverse. ``.`` is excluded, so ``..`` cannot be spelled at all.
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ProtocolError(Exception):
    """Malformed protocol data."""


class UnsafeJobId(ProtocolError):
    """A job id that could name a file outside the queue directory."""


# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------


def _b32(value: int, length: int) -> str:
    out: List[str] = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(*, ts_ms: Optional[int] = None, rand: Optional[int] = None) -> str:
    """26-char Crockford-base32 ULID: 48-bit ms timestamp + 80 random bits.

    Lexicographic order is creation order, which is why the drain needs no index
    file. v1's ``{stem}_{YYYYMMDDTHHMMSSZ}.lua`` collided inside one second.
    """
    stamp = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
    noise = secrets.randbits(80) if rand is None else int(rand)
    return _b32(stamp, 10) + _b32(noise, 16)


def new_job_id(prefix: str = "") -> str:
    """A fresh job id, optionally carrying a human prefix for log grepping."""
    ulid = new_ulid()
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(prefix or "")).strip("_")[:40]
    return f"{clean}_{ulid}" if clean else ulid


def is_safe_job_id(job_id: Any) -> bool:
    return isinstance(job_id, str) and bool(JOB_ID_RE.match(job_id))


def require_job_id(job_id: Any) -> str:
    """Return ``job_id`` or raise :class:`UnsafeJobId`. Use at every boundary."""
    if not is_safe_job_id(job_id):
        raise UnsafeJobId(f"unsafe job id: {job_id!r}")
    return str(job_id)


def confined_path(queue_dir: Union[str, Path], subdir: str, job_id: str) -> Path:
    """``<queue>/<subdir>/<job_id>.json``, proven to stay under the queue dir.

    The id class alone makes traversal unspellable; the ``relative_to`` check is
    the belt to that suspenders, and catches a symlinked subdirectory too.
    """
    safe = require_job_id(job_id)
    root = Path(queue_dir).resolve()
    full = (root / subdir / f"{safe}.json").resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:  # pragma: no cover - unreachable via JOB_ID_RE
        raise UnsafeJobId(f"job id escapes queue dir: {job_id!r}") from exc
    return full


def job_path(queue_dir: Union[str, Path], job_id: str) -> Path:
    return confined_path(queue_dir, JOBS_DIR, job_id)


def claimed_path(queue_dir: Union[str, Path], job_id: str) -> Path:
    return confined_path(queue_dir, CLAIMED_DIR, job_id)


def result_path(queue_dir: Union[str, Path], job_id: str) -> Path:
    return confined_path(queue_dir, RESULTS_DIR, job_id)


def archive_path(queue_dir: Union[str, Path], job_id: str, *, day: str = "") -> Path:
    safe = require_job_id(job_id)
    root = Path(queue_dir).resolve()
    stamp = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", stamp):
        raise UnsafeJobId(f"bad archive day: {day!r}")
    full = (root / ARCHIVE_DIR / stamp / f"{safe}.json").resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:  # pragma: no cover
        raise UnsafeJobId(f"archive path escapes queue dir: {job_id!r}") from exc
    return full


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------


def iso_utc(ts: Optional[float] = None) -> str:
    """ISO-8601 UTC with milliseconds and a ``Z`` suffix. The only formatter."""
    moment = datetime.now(timezone.utc) if ts is None else datetime.fromtimestamp(float(ts), timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(text: Any) -> Optional[float]:
    """ISO-8601 → epoch seconds, or None. Never raises."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


# ---------------------------------------------------------------------------
# atomic file I/O
# ---------------------------------------------------------------------------


def atomic_write_text(path: Union[str, Path], text: str) -> Path:
    """Write via a sibling ``.tmp`` + :func:`os.replace`.

    A reader either sees the previous file or the whole new one, never a
    half-written job. The temp file is a sibling so the replace stays inside one
    filesystem, and it is removed on any failure so a crashed write cannot leave
    litter that looks like a job.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def atomic_write_json(path: Union[str, Path], payload: Any) -> Path:
    return atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def read_json(path: Union[str, Path]) -> Optional[Any]:
    """Parse a JSON file. Returns None when missing, empty or malformed."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def ensure_layout(queue_dir: Union[str, Path]) -> Path:
    """Create the v3 directory skeleton. Idempotent."""
    root = Path(queue_dir)
    root.mkdir(parents=True, exist_ok=True)
    for name in SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def default_queue_dir() -> Path:
    """``paths.queue_dir()``, imported lazily so import stays side-effect free."""
    from .. import paths  # noqa: PLC0415 - deliberate: keeps import graph clean

    return paths.queue_dir()


# ---------------------------------------------------------------------------
# result contract
# ---------------------------------------------------------------------------


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_opt_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_opt_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return None


@dataclass(frozen=True, slots=True)
class FieldFailure:
    """One field that did not take. The unit v1 could not express at all."""

    field: str
    reason: str = ""
    detail: str = ""
    requested: Any = None
    readback: Any = None
    op: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"field": self.field, "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        if self.requested is not None:
            out["requested"] = self.requested
        if self.readback is not None:
            out["readback"] = self.readback
        if self.op:
            out["op"] = self.op
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FieldFailure":
        return cls(
            field=str(data.get("field") or ""),
            reason=str(data.get("reason") or ""),
            detail=str(data.get("detail") or ""),
            requested=data.get("requested"),
            readback=data.get("readback"),
            op=str(data.get("op") or ""),
        )


@dataclass(frozen=True, slots=True)
class LuaError:
    """The real Lua error text, whitespace and all.

    v1 pushed errors through a ``key=value`` line, so ``collapse_err`` squashed
    whitespace to underscores and truncated at 200 chars. JSON has no such
    constraint: message and traceback survive byte for byte.
    """

    message: str
    traceback: str = ""
    phase: str = ""
    op: str = ""
    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"message": self.message}
        for key, value in (
            ("traceback", self.traceback),
            ("phase", self.phase),
            ("op", self.op),
            ("kind", self.kind),
        ):
            if value:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, data: Any) -> Optional["LuaError"]:
        if not data:
            return None
        if isinstance(data, str):
            return cls(message=data)
        if not isinstance(data, Mapping):
            return None
        return cls(
            message=str(data.get("message") or ""),
            traceback=str(data.get("traceback") or ""),
            phase=str(data.get("phase") or ""),
            op=str(data.get("op") or ""),
            kind=str(data.get("kind") or ""),
        )


@dataclass(frozen=True, slots=True)
class GameContext:
    """What the game looked like when the job ran."""

    in_career: Optional[bool] = None
    save_uid: str = ""
    user_teamid: Optional[int] = None
    game_ver: str = ""
    core_version: str = ""
    game_date: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.in_career is not None:
            out["in_career"] = self.in_career
        for key, value in (
            ("save_uid", self.save_uid),
            ("game_ver", self.game_ver),
            ("core_version", self.core_version),
        ):
            if value:
                out[key] = value
        if self.user_teamid is not None:
            out["user_teamid"] = self.user_teamid
        if self.game_date:
            out["game_date"] = dict(self.game_date)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> Optional["GameContext"]:
        if not isinstance(data, Mapping) or not data:
            return None
        date = data.get("game_date")
        return cls(
            in_career=_as_opt_bool(data.get("in_career")),
            save_uid=str(data.get("save_uid") or ""),
            user_teamid=_as_opt_int(data.get("user_teamid")),
            game_ver=str(data.get("game_ver") or ""),
            core_version=str(data.get("core_version") or ""),
            game_date=dict(date) if isinstance(date, Mapping) else None,
        )


@dataclass(frozen=True, slots=True)
class OpResult:
    """Per-op verdict. ``outcome`` is semantic, not "pcall did not throw"."""

    id: str
    op: str
    outcome: Outcome = Outcome.BLOCKED
    written: int = 0
    failed: int = 0
    skipped: int = 0
    requested: int = 0
    found: Optional[bool] = None
    scanned: Optional[int] = None
    elapsed_ms: Optional[int] = None
    failures: Tuple[FieldFailure, ...] = ()
    error: Optional[LuaError] = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "op": self.op,
            "outcome": self.outcome.wire,
            "written": int(self.written),
            "failed": int(self.failed),
            "skipped": int(self.skipped),
            "requested": int(self.requested),
        }
        if self.found is not None:
            out["found"] = self.found
        if self.scanned is not None:
            out["scanned"] = int(self.scanned)
        if self.elapsed_ms is not None:
            out["elapsed_ms"] = int(self.elapsed_ms)
        if self.failures:
            out["failures"] = [f.to_dict() for f in self.failures]
        if self.error is not None:
            out["error"] = self.error.to_dict()
        if self.data:
            out["data"] = dict(self.data)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OpResult":
        raw_failures = data.get("failures")
        failures: Tuple[FieldFailure, ...] = ()
        if isinstance(raw_failures, Sequence) and not isinstance(raw_failures, (str, bytes)):
            failures = tuple(
                FieldFailure.from_dict(item) for item in raw_failures if isinstance(item, Mapping)
            )
        extra = data.get("data")
        return cls(
            id=str(data.get("id") or ""),
            op=str(data.get("op") or ""),
            # An unreadable op outcome is FAILED. It is never APPLIED.
            outcome=parse_outcome(data.get("outcome"), default=Outcome.FAILED),
            written=_as_int(data.get("written")),
            failed=_as_int(data.get("failed")),
            skipped=_as_int(data.get("skipped")),
            requested=_as_int(data.get("requested")),
            found=_as_opt_bool(data.get("found")),
            scanned=_as_opt_int(data.get("scanned")),
            elapsed_ms=_as_opt_int(data.get("elapsed_ms")),
            failures=failures,
            error=LuaError.from_dict(data.get("error")),
            data=dict(extra) if isinstance(extra, Mapping) else {},
        )


@dataclass(frozen=True, slots=True)
class JobResult:
    """``results/<job_id>.json`` — the only completion signal.

    ``ok`` is *derived* from ``outcome`` on the way out and ignored on the way
    in. v1 carried ``applied``, ``queued`` and ``outcome`` side by side and they
    could disagree; a derived field cannot.
    """

    job_id: str
    outcome: Outcome = Outcome.BLOCKED
    session_id: str = ""
    ops: Tuple[OpResult, ...] = ()
    started: Optional[float] = None
    finished: Optional[float] = None
    context: Optional[GameContext] = None
    error: Optional[LuaError] = None
    diagnostic: str = ""
    unsafe: bool = False
    core_version: str = ""
    log: Tuple[str, ...] = ()
    v: int = RESULT_VERSION
    reported_counts: Mapping[str, Any] = field(default_factory=dict)

    # -- derived ---------------------------------------------------------
    @property
    def ok(self) -> bool:
        return self.outcome.is_success

    @property
    def counts(self) -> Dict[str, int]:
        """Counts derived from the op list — the single source of truth."""
        return {
            "ops": len(self.ops),
            "ops_ok": sum(1 for o in self.ops if o.outcome is Outcome.APPLIED),
            "ops_failed": sum(1 for o in self.ops if o.outcome in (Outcome.FAILED, Outcome.BLOCKED)),
            "requested": sum(o.requested for o in self.ops),
            "written": sum(o.written for o in self.ops),
            "failed": sum(o.failed for o in self.ops),
            "skipped": sum(o.skipped for o in self.ops),
        }

    @property
    def writes_performed(self) -> int:
        return self.counts["written"]

    @property
    def duration_ms(self) -> Optional[int]:
        if self.started is None or self.finished is None:
            return None
        return max(0, int(round((self.finished - self.started) * 1000)))

    @property
    def failures(self) -> Tuple[FieldFailure, ...]:
        out: List[FieldFailure] = []
        for op in self.ops:
            for fail in op.failures:
                out.append(fail if fail.op else FieldFailure(**{**_ff_kwargs(fail), "op": op.id}))
        return tuple(out)

    @property
    def counts_disagree(self) -> bool:
        """True when the writer's own counts contradict its op list."""
        if not self.reported_counts:
            return False
        derived = self.counts
        for key, value in self.reported_counts.items():
            if key in derived and _as_opt_int(value) is not None and int(value) != derived[key]:
                return True
        return False

    def worst_op(self) -> Outcome:
        return derive_job_outcome([o.outcome for o in self.ops], default=self.outcome)

    def summary(self) -> str:
        counts = self.counts
        bits = [self.outcome.wire.upper()]
        if counts["ops"]:
            bits.append(f"ops={counts['ops_ok']}/{counts['ops']}")
        if counts["requested"] or counts["written"] or counts["failed"]:
            bits.append(f"written={counts['written']}/{counts['requested'] or counts['written']}")
        if counts["failed"]:
            bits.append(f"failed={counts['failed']}")
        if self.error and self.error.message:
            bits.append(f"error={self.error.message.splitlines()[0][:120]}")
        elif self.diagnostic:
            bits.append(self.diagnostic[:120])
        return " · ".join(bits)

    # -- wire ------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "v": int(self.v),
            "job_id": self.job_id,
            "session_id": self.session_id,
            "outcome": self.outcome.wire,
            "ok": self.ok,
            "unsafe": bool(self.unsafe),
            "started_utc": iso_utc(self.started) if self.started is not None else None,
            "finished_utc": iso_utc(self.finished) if self.finished is not None else None,
            "duration_ms": self.duration_ms,
            "core_version": self.core_version,
            "context": self.context.to_dict() if self.context else {},
            "ops": [o.to_dict() for o in self.ops],
            "counts": self.counts,
            "writes_performed": self.writes_performed,
            "error": self.error.to_dict() if self.error else None,
            "diagnostic": self.diagnostic,
            "log": list(self.log),
        }
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobResult":
        raw_ops = data.get("ops")
        ops: Tuple[OpResult, ...] = ()
        if isinstance(raw_ops, Sequence) and not isinstance(raw_ops, (str, bytes)):
            ops = tuple(OpResult.from_dict(item) for item in raw_ops if isinstance(item, Mapping))
        raw_log = data.get("log")
        log: Tuple[str, ...] = ()
        if isinstance(raw_log, Sequence) and not isinstance(raw_log, (str, bytes)):
            log = tuple(str(line) for line in raw_log)
        counts = data.get("counts")
        return cls(
            job_id=str(data.get("job_id") or ""),
            # A result whose outcome we cannot read is FAILED, never APPLIED.
            outcome=parse_outcome(data.get("outcome"), default=Outcome.FAILED),
            session_id=str(data.get("session_id") or ""),
            ops=ops,
            started=parse_iso(data.get("started_utc")),
            finished=parse_iso(data.get("finished_utc")),
            context=GameContext.from_dict(data.get("context")),
            error=LuaError.from_dict(data.get("error")),
            diagnostic=str(data.get("diagnostic") or ""),
            unsafe=bool(data.get("unsafe")),
            core_version=str(data.get("core_version") or ""),
            log=log,
            v=_as_int(data.get("v"), RESULT_VERSION),
            reported_counts=dict(counts) if isinstance(counts, Mapping) else {},
        )


def _ff_kwargs(fail: FieldFailure) -> Dict[str, Any]:
    return {
        "field": fail.field,
        "reason": fail.reason,
        "detail": fail.detail,
        "requested": fail.requested,
        "readback": fail.readback,
    }


def write_result(queue_dir: Union[str, Path], result: JobResult) -> Path:
    """Write ``results/<job_id>.json`` atomically."""
    return atomic_write_json(result_path(queue_dir, result.job_id), result.to_dict())


def read_result(queue_dir: Union[str, Path], job_id: str) -> Optional[JobResult]:
    """Read a result. Missing → None. Present but corrupt → FAILED, never None.

    "The file is unreadable" and "the job has not finished" are different
    answers, and conflating them is how a broken write reads as still-pending
    forever.
    """
    path = result_path(queue_dir, job_id)
    if not path.is_file():
        return None
    data = read_json(path)
    if not isinstance(data, Mapping):
        return JobResult(
            job_id=job_id,
            outcome=Outcome.FAILED,
            diagnostic=f"unreadable result file: {path.name}",
        )
    parsed = JobResult.from_dict(data)
    if parsed.job_id and parsed.job_id != job_id:
        return JobResult(
            job_id=job_id,
            outcome=Outcome.FAILED,
            diagnostic=f"result file names a different job: {parsed.job_id!r}",
        )
    if not parsed.job_id:
        parsed = JobResult(**{**_jr_kwargs(parsed), "job_id": job_id})
    return parsed


def _jr_kwargs(res: JobResult) -> Dict[str, Any]:
    return {
        "job_id": res.job_id,
        "outcome": res.outcome,
        "session_id": res.session_id,
        "ops": res.ops,
        "started": res.started,
        "finished": res.finished,
        "context": res.context,
        "error": res.error,
        "diagnostic": res.diagnostic,
        "unsafe": res.unsafe,
        "core_version": res.core_version,
        "log": res.log,
        "v": res.v,
        "reported_counts": res.reported_counts,
    }


# ---------------------------------------------------------------------------
# liveness: two independent facts
# ---------------------------------------------------------------------------


class LiveStatus(Enum):
    """What the status pill shows. See the §3.5 matrix."""

    OFF = "off"
    ARMED = "armed"
    LIVE = "live"
    WAITING = "waiting"
    STALLED = "stalled"


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """``session.json`` — written once per LE session, at arm. Does not decay."""

    session_id: str
    armed: Optional[float] = None
    le_pid: Optional[int] = None
    core_version: str = ""
    game_ver: str = ""
    queue_dir: str = ""
    handlers: Tuple[str, ...] = ()
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": int(self.v),
            "session_id": self.session_id,
            "armed_utc": iso_utc(self.armed) if self.armed is not None else None,
            "le_pid": self.le_pid,
            "core_version": self.core_version,
            "game_ver": self.game_ver,
            "queue_dir": self.queue_dir,
            "handlers": list(self.handlers),
            "capabilities": dict(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionInfo":
        handlers = data.get("handlers")
        caps = data.get("capabilities")
        return cls(
            session_id=str(data.get("session_id") or ""),
            armed=parse_iso(data.get("armed_utc")),
            le_pid=_as_opt_int(data.get("le_pid")),
            core_version=str(data.get("core_version") or ""),
            game_ver=str(data.get("game_ver") or ""),
            queue_dir=str(data.get("queue_dir") or ""),
            handlers=tuple(str(h) for h in handlers) if isinstance(handlers, Sequence) and not isinstance(handlers, (str, bytes)) else (),
            capabilities=dict(caps) if isinstance(caps, Mapping) else {},
            v=_as_int(data.get("v"), PROTOCOL_VERSION),
        )


@dataclass(frozen=True, slots=True)
class DrainInfo:
    """``drain.json`` — rewritten at the end of every drain. A diagnostic."""

    session_id: str = ""
    drain_seq: int = 0
    at: Optional[float] = None
    trigger: str = ""
    message_id: Optional[int] = None
    claimed: int = 0
    completed: int = 0
    deferred: int = 0
    budget_ms_used: int = 0
    queue_depth_after: int = 0
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": int(self.v),
            "session_id": self.session_id,
            "drain_seq": int(self.drain_seq),
            "at_utc": iso_utc(self.at) if self.at is not None else None,
            "trigger": self.trigger,
            "message_id": self.message_id,
            "claimed": int(self.claimed),
            "completed": int(self.completed),
            "deferred": int(self.deferred),
            "budget_ms_used": int(self.budget_ms_used),
            "queue_depth_after": int(self.queue_depth_after),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DrainInfo":
        return cls(
            session_id=str(data.get("session_id") or ""),
            drain_seq=_as_int(data.get("drain_seq")),
            at=parse_iso(data.get("at_utc")),
            trigger=str(data.get("trigger") or ""),
            message_id=_as_opt_int(data.get("message_id")),
            claimed=_as_int(data.get("claimed")),
            completed=_as_int(data.get("completed")),
            deferred=_as_int(data.get("deferred")),
            budget_ms_used=_as_int(data.get("budget_ms_used")),
            queue_depth_after=_as_int(data.get("queue_depth_after")),
            v=_as_int(data.get("v"), PROTOCOL_VERSION),
        )


@dataclass(frozen=True, slots=True)
class LiveState:
    """Arm state and drain freshness, kept apart on purpose.

    v1 answered one question — "is ``_bridge_alive.txt`` younger than 90 s?" —
    and used it for three: worker loaded, worker armed, events flowing. Idling
    for 90 seconds therefore read ``WORKER OFF``, which collapsed the apply
    timeout from 55 s to 2 s and hard-blocked applies on a perfectly healthy
    session. Here ``armed`` never decays, and ``last_drain_age`` is a
    diagnostic that only matters when the queue is non-empty.
    """

    armed: bool = False
    status: LiveStatus = LiveStatus.OFF
    session_id: str = ""
    le_pid: Optional[int] = None
    pid_alive: Optional[bool] = None
    armed_age: Optional[float] = None
    last_drain_age: Optional[float] = None
    drain_seq: int = 0
    queue_depth: int = 0
    core_version: str = ""
    session_present: bool = False

    @property
    def can_apply(self) -> bool:
        """True when submitting now has a live session to run it."""
        return self.armed

    def human(self) -> str:
        """One honest sentence. Never says OFF about an armed session."""
        depth = self.queue_depth
        if not self.session_present:
            return "Worker off — start Live Editor; the companion arms itself."
        if not self.armed:
            return "Worker off — Live Editor isn't running (session marker is stale)."
        if depth <= 0:
            return "Armed — ready. Jobs run on the next Career Mode tick."
        age = self.last_drain_age
        if self.status is LiveStatus.LIVE:
            return f"Live — working… ({depth} queued)"
        if self.status is LiveStatus.WAITING:
            return f"Armed — {depth} job(s) queued; enter Career Mode or advance a day."
        minutes = int((age or 0) // 60)
        return (
            f"Armed — {depth} job(s) queued, no game activity for {minutes}m. "
            "Use Force Drain."
        )

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"{self.status.value}: {self.human()}"


def compute_live_state(
    session: Optional[SessionInfo],
    drain: Optional[DrainInfo],
    *,
    queue_depth: int = 0,
    now: Optional[float] = None,
    pid_alive: Optional[bool] = None,
) -> LiveState:
    """The §3.5 status matrix, as a pure function.

    ``pid_alive`` is passed in (never probed here) so this stays testable and
    platform-free. ``None`` means "not probed" and is treated as alive: a
    session marker is proof of arm, and an arm marker does not expire.
    """
    stamp = time.time() if now is None else float(now)
    depth = max(0, int(queue_depth))
    if session is None:
        return LiveState(queue_depth=depth, session_present=False)

    armed = pid_alive is not False
    armed_age = None if session.armed is None else max(0.0, stamp - session.armed)
    drain_age = None
    if drain is not None and drain.at is not None:
        drain_age = max(0.0, stamp - drain.at)

    base = {
        "session_id": session.session_id,
        "le_pid": session.le_pid,
        "pid_alive": pid_alive,
        "armed_age": armed_age,
        "last_drain_age": drain_age,
        "drain_seq": drain.drain_seq if drain else 0,
        "queue_depth": depth,
        "core_version": session.core_version,
        "session_present": True,
    }
    if not armed:
        return LiveState(armed=False, status=LiveStatus.OFF, **base)
    if depth <= 0:
        # The success state v1 rendered as WORKER OFF.
        return LiveState(armed=True, status=LiveStatus.ARMED, **base)

    # Queue is non-empty: how long since the game last did anything? Fall back
    # to the arm time when this session has never drained.
    age = drain_age if drain_age is not None else armed_age
    if age is None or age <= LIVE_WINDOW_SEC:
        status = LiveStatus.LIVE
    elif age <= STALL_AFTER_SEC:
        status = LiveStatus.WAITING
    else:
        status = LiveStatus.STALLED
    return LiveState(armed=True, status=status, **base)


def read_session(queue_dir: Union[str, Path]) -> Optional[SessionInfo]:
    data = read_json(Path(queue_dir) / SESSION_NAME)
    if not isinstance(data, Mapping):
        return None
    info = SessionInfo.from_dict(data)
    return info if info.session_id else None


def write_session(queue_dir: Union[str, Path], session: SessionInfo) -> Path:
    return atomic_write_json(Path(queue_dir) / SESSION_NAME, session.to_dict())


def read_drain(queue_dir: Union[str, Path]) -> Optional[DrainInfo]:
    data = read_json(Path(queue_dir) / DRAIN_NAME)
    if not isinstance(data, Mapping):
        return None
    return DrainInfo.from_dict(data)


def write_drain(queue_dir: Union[str, Path], drain: DrainInfo) -> Path:
    return atomic_write_json(Path(queue_dir) / DRAIN_NAME, drain.to_dict())


# ---------------------------------------------------------------------------
# v1 compatibility (read-only)
# ---------------------------------------------------------------------------

_LEGACY_TOKEN_RE = re.compile(r"([A-Za-z_][\w]*)=([^\s]+)")
# msg= is last on the line and carries spaces (Lua error text).
_LEGACY_MSG_RE = re.compile(r"\bmsg=(.+)$", re.MULTILINE)
_BENIGN = ("", "ok", "none", "nil", "success", "-")


@dataclass(frozen=True, slots=True)
class LegacyStatus:
    """What a v1 install is reporting, mapped onto the v3 enum.

    Kept so a half-migrated machine still reports correctly: v1 files are read,
    never written. ``weak`` marks a verdict that came only from v1's "pcall did
    not throw" signal; ``attributable`` is False when the result names
    ``_run_now.lua`` and so identifies no job at all — 68% of v1 results.
    """

    present: bool = False
    outcome: Outcome = Outcome.BLOCKED
    weak: bool = False
    attributable: bool = True
    raw_status: str = ""
    raw_result: str = ""
    job: str = ""
    player_id: Optional[int] = None
    team_id: Optional[int] = None
    found: Optional[bool] = None
    ok: Optional[bool] = None
    written: Optional[int] = None
    failed_writes: Optional[int] = None
    scanned: Optional[int] = None
    reason: str = ""
    msg: str = ""
    path: str = ""
    processed: Optional[int] = None
    ok_count: Optional[int] = None
    last: str = ""
    idle: bool = False
    alive_unix: Optional[int] = None
    armed_unix: Optional[int] = None
    pending: Tuple[str, ...] = ()

    def summary(self) -> str:
        bits: List[str] = [self.outcome.wire]
        if self.job:
            bits.append(self.job)
        if self.player_id is not None:
            bits.append(f"id={self.player_id}")
        if self.found is False:
            bits.append("NOT FOUND")
        if self.written is not None:
            bits.append(f"written={self.written}")
        if self.failed_writes:
            bits.append(f"failed={self.failed_writes}")
        if self.reason and self.reason.lower() not in _BENIGN:
            bits.append(f"reason={self.reason}")
        if self.msg:
            bits.append(f"msg={self.msg[:160]}")
        if not self.attributable:
            bits.append("unattributable(_run_now.lua)")
        if self.weak:
            bits.append("weak(pcall-only)")
        return " · ".join(bits)


def parse_legacy_job_status(text: str) -> Dict[str, Any]:
    """Parse v1 ``_job_status.txt`` (``job=edit id=… found=… written=…``)."""
    raw = (text or "").strip()
    out: Dict[str, Any] = {
        "raw": raw,
        "present": bool(raw),
        "ok": None,
        "found": None,
        "job": "",
        "path": "",
        "player_id": None,
        "team_id": None,
        "reason": "",
        "msg": "",
        "err": "",
        "scanned": None,
        "written": None,
        "failed_writes": None,
    }
    if not raw:
        return out
    for match in _LEGACY_TOKEN_RE.finditer(raw):
        key, value = match.group(1).lower(), match.group(2)
        if key == "ok":
            out["ok"] = _as_opt_bool(value)
        elif key == "found":
            out["found"] = _as_opt_bool(value)
        elif key == "path":
            out["path"] = value
        elif key in ("id", "playerid"):
            out["player_id"] = _as_opt_int(value)
        elif key in ("team", "teamid"):
            out["team_id"] = _as_opt_int(value)
        elif key == "job":
            out["job"] = value
        elif key == "reason":
            out["reason"] = value
        elif key == "scanned":
            out["scanned"] = _as_opt_int(value)
        elif key == "written":
            out["written"] = _as_opt_int(value)
        elif key == "failed":
            out["failed_writes"] = _as_opt_int(value)
        elif key == "err" and value.lower() not in ("none", "nil", ""):
            out["err"] = value
    match = _LEGACY_MSG_RE.search(raw)
    if match:
        out["msg"] = match.group(1).strip()[:400]
    return out


def parse_legacy_result_line(text: str) -> Dict[str, Any]:
    """Parse v1 ``_last_result.txt`` (``OK processed=1 ok=1 last=…``)."""
    raw = (text or "").strip()
    out: Dict[str, Any] = {
        "raw": raw,
        "ok": raw.startswith("OK"),
        "fail": raw.startswith("FAIL"),
        "idle": ("queue_empty" in raw) or ("idle" in raw.lower()),
        "processed": None,
        "ok_count": None,
        "last": "",
    }
    match = re.search(r"processed=(\d+)", raw)
    if match:
        out["processed"] = int(match.group(1))
    match = re.search(r"\bok=(\d+)", raw)
    if match:
        out["ok_count"] = int(match.group(1))
    match = re.search(r"last=(\S+)", raw)
    if match:
        out["last"] = match.group(1)
    return out


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def read_legacy_status(queue_dir: Optional[Union[str, Path]] = None) -> LegacyStatus:
    """Read a v1 queue and answer in v3 terms. Never writes.

    Mapping rules, all of which exist because v1's own answer was too generous:

    * ``found=false`` or ``ok=false`` or "wrote nothing while writes failed"
      → FAILED. v1 reported ``OK processed=1 ok=1`` for a job that scanned
      25,000 records and wrote none.
    * ``OK processed=N ok=N`` with no status side-file → APPLIED but ``weak``,
      because all that proves is that ``pcall`` did not throw.
    * ``OK idle queue_empty`` → QUEUED if jobs are still on disk, else BLOCKED:
      the drain ran and had nothing to say about your job.
    * nothing on disk at all → BLOCKED, never QUEUED and never APPLIED.
    """
    root = Path(queue_dir) if queue_dir is not None else default_queue_dir()
    status_raw = _read_text(root / LEGACY_JOB_STATUS)
    result_raw = _read_text(root / LEGACY_LAST_RESULT)
    alive_raw = _read_text(root / LEGACY_ALIVE)
    armed_raw = _read_text(root / LEGACY_ARMED)
    pending_raw = _read_text(root / LEGACY_PENDING)

    status = parse_legacy_job_status(status_raw)
    result = parse_legacy_result_line(result_raw)

    pending = tuple(line.strip() for line in pending_raw.splitlines() if line.strip())
    alive_unix = None
    match = re.search(r"alive\s+(\d+)", alive_raw)
    if match:
        alive_unix = int(match.group(1))
    armed_unix = None
    match = re.search(r"armed\s+(\d+)", armed_raw)
    if match:
        armed_unix = int(match.group(1))

    outcome = Outcome.BLOCKED
    weak = False
    if status["present"]:
        failed = False
        if status["ok"] is False or status["found"] is False:
            failed = True
        reason = str(status["reason"] or "").lower()
        if reason and reason not in _BENIGN and status["ok"] is not True:
            failed = True
        if status["err"] and status["ok"] is not True:
            failed = True
        fw, wr = status["failed_writes"], status["written"]
        if fw is not None and fw > 0 and (wr is None or wr <= 0):
            failed = True
        if failed:
            outcome = Outcome.FAILED
        elif status["ok"] is True or (wr is not None and wr > 0):
            outcome = Outcome.APPLIED
        else:
            outcome = Outcome.FAILED
    elif result["raw"]:
        if result["fail"]:
            outcome = Outcome.FAILED
        elif result["idle"]:
            outcome = Outcome.QUEUED if pending else Outcome.BLOCKED
        elif result["ok"] and (result["processed"] or 0) >= 1:
            outcome = Outcome.APPLIED
            weak = True  # "pcall did not throw" is not "it worked"
        else:
            outcome = Outcome.BLOCKED
    elif pending:
        outcome = Outcome.QUEUED

    last = str(result["last"] or "")
    return LegacyStatus(
        present=bool(status_raw or result_raw or pending),
        outcome=outcome,
        weak=weak,
        attributable=bool(last) and not last.startswith("_"),
        raw_status=status_raw,
        raw_result=result_raw,
        job=str(status["job"] or ""),
        player_id=status["player_id"],
        team_id=status["team_id"],
        found=status["found"],
        ok=status["ok"],
        written=status["written"],
        failed_writes=status["failed_writes"],
        scanned=status["scanned"],
        reason=str(status["reason"] or ""),
        msg=str(status["msg"] or status["err"] or ""),
        path=str(status["path"] or ""),
        processed=result["processed"],
        ok_count=result["ok_count"],
        last=last,
        idle=bool(result["idle"]),
        alive_unix=alive_unix,
        armed_unix=armed_unix,
        pending=pending,
    )
