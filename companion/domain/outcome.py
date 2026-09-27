"""The one closed outcome enum, and the typed result it lives in.

v1 had nine competing result shapes and converted between six of them in a
single apply. v2 has exactly one, ordered worst-to-best so "worst op wins" is
``min()``. See ``docs/V2_ARCHITECTURE.md §4.4``.

The Lua core writes a wire result (``state`` + ``ok`` + ``counts`` + ``failures``,
per ``docs/V2_INGAME_CORE.md §4``). ``JobResult.from_wire`` maps that onto this
enum. The mapping is the single place the wire vocabulary meets the domain
vocabulary — every presenter matches on ``ApplyOutcome``, never on a string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Mapping, Sequence


class ApplyOutcome(IntEnum):
    """Worst-to-best. ``min()`` over a job's ops yields the job outcome."""

    CRASHED = 0   # session died mid-job; found in claimed/ at next arm
    FAILED = 1    # an op raised, or a verify read-back disagreed
    REJECTED = 2  # preconditions unmet: not in career, wrong save, grant denied
    EXPIRED = 3   # deadline passed with no drain (job never claimed)
    PARTIAL = 4   # some ops/fields applied, some did not
    NO_OP = 5     # ran cleanly, changed nothing (the found=false case)
    APPLIED = 6   # every op applied and verified
    DEFERRED = 7  # ran out of drain budget; resumes on the next event
    QUEUED = 8    # accepted, never claimed yet

    @property
    def is_terminal(self) -> bool:
        return self not in (ApplyOutcome.QUEUED, ApplyOutcome.DEFERRED)

    @property
    def is_success(self) -> bool:
        return self in (ApplyOutcome.APPLIED, ApplyOutcome.NO_OP)

    @property
    def label(self) -> str:
        return {
            ApplyOutcome.CRASHED: "Crashed",
            ApplyOutcome.FAILED: "Failed",
            ApplyOutcome.REJECTED: "Rejected",
            ApplyOutcome.EXPIRED: "Expired",
            ApplyOutcome.PARTIAL: "Partial",
            ApplyOutcome.NO_OP: "No change",
            ApplyOutcome.APPLIED: "Applied",
            ApplyOutcome.DEFERRED: "Working…",
            ApplyOutcome.QUEUED: "Queued",
        }[self]


TERMINAL = frozenset(o for o in ApplyOutcome if o.is_terminal)

# Wire ``state`` string -> ApplyOutcome. The Lua core's vocabulary (§4).
_WIRE_STATE: Mapping[str, ApplyOutcome] = {
    "queued": ApplyOutcome.QUEUED,
    "claimed": ApplyOutcome.QUEUED,
    "running": ApplyOutcome.DEFERRED,
    "deferred": ApplyOutcome.DEFERRED,
    "done": ApplyOutcome.APPLIED,      # refined by ok/counts below
    "no_op": ApplyOutcome.NO_OP,
    "partial": ApplyOutcome.PARTIAL,
    "rejected": ApplyOutcome.REJECTED,
    "failed": ApplyOutcome.FAILED,
    "poisoned": ApplyOutcome.FAILED,
    "crashed": ApplyOutcome.CRASHED,
    "expired": ApplyOutcome.EXPIRED,
}


def outcome_from_wire(state: str, ok: bool, counts: Mapping[str, Any] | None) -> ApplyOutcome:
    """Map a wire (state, ok, counts) triple onto the closed enum.

    ``done`` is refined: ok -> APPLIED; not-ok with something found/written but
    also something failed -> PARTIAL; not-ok with nothing found/written -> NO_OP
    (the classic ``found=false`` case that v1 reported as OK); not-ok with
    nothing found at all -> NO_OP.
    """
    base = _WIRE_STATE.get((state or "").lower(), ApplyOutcome.FAILED)
    if base is not ApplyOutcome.APPLIED:
        return base
    c = counts or {}
    written = int(c.get("fields_written", 0) or 0)
    found = int(c.get("found", 0) or 0)
    failed = int(c.get("fields_failed", 0) or 0) + int(c.get("ops_failed", 0) or 0)
    # Fail closed when a stale or mismatched worker contradicts itself.  This
    # prevents the v1 ``written=88 failed=3 ok=true`` lie at the one boundary
    # every presenter uses.
    if failed > 0:
        return ApplyOutcome.PARTIAL if written > 0 else ApplyOutcome.FAILED
    if ok:
        return ApplyOutcome.APPLIED
    if written > 0 and failed > 0:
        return ApplyOutcome.PARTIAL
    if written > 0:
        return ApplyOutcome.PARTIAL  # ok=false with writes => something didn't verify
    if found == 0:
        return ApplyOutcome.NO_OP
    return ApplyOutcome.NO_OP


@dataclass(frozen=True, slots=True)
class OpResult:
    """One op's outcome within a job."""

    op_id: str
    op: str
    outcome: ApplyOutcome
    counts: Mapping[str, Any] = field(default_factory=dict)
    data: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "OpResult":
        counts = dict(d.get("counts") or {})
        oc = outcome_from_wire(
            "done" if d.get("ok") else "partial",
            bool(d.get("ok")),
            counts,
        )
        # An op that ran cleanly and changed nothing is NO_OP, not APPLIED.
        changed = (
            int(counts.get("written", counts.get("fields_written", 0)) or 0)
            + int(counts.get("side_effects", 0) or 0)
        )
        requested = int(
            counts.get(
                "requested",
                counts.get("fields_requested", counts.get("targets", 0)),
            )
            or 0
        )
        if d.get("ok") and changed == 0 and requested == 0:
            oc = ApplyOutcome.NO_OP
        return cls(
            op_id=str(d.get("id", "")),
            op=str(d.get("op", "")),
            outcome=oc,
            counts=counts,
            data=dict(d.get("data") or {}),
        )


@dataclass(frozen=True, slots=True)
class Failure:
    op_id: str
    reason: str
    detail: str = ""
    field: str | None = None
    target: Any | None = None

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "Failure":
        return cls(
            op_id=str(d.get("op", d.get("op_id", ""))),
            reason=str(d.get("reason", "")),
            detail=str(d.get("detail", "")),
            field=d.get("field"),
            target=d.get("target"),
        )


@dataclass(frozen=True, slots=True)
class JobResult:
    """The typed result the whole app reasons about. Built from a wire dict."""

    job_id: str
    outcome: ApplyOutcome
    ops: tuple[OpResult, ...] = ()
    failures: tuple[Failure, ...] = ()
    started: datetime | None = None
    finished: datetime | None = None
    counts: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, Any] = field(default_factory=dict)
    diagnostic: str = ""
    error: Mapping[str, Any] | None = None
    session_id: str = ""
    label: str = ""
    log: tuple[str, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome.is_success

    def worst_op(self) -> ApplyOutcome:
        return min((o.outcome for o in self.ops), default=self.outcome)

    @classmethod
    def queued(cls, job_id: str, label: str = "") -> "JobResult":
        return cls(job_id=job_id, outcome=ApplyOutcome.QUEUED, label=label)

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "JobResult":
        state = str(d.get("state", "failed"))
        ok = bool(d.get("ok", False))
        counts = dict(d.get("counts") or {})
        # Prefer an explicit wire outcome name if the core provided one.
        explicit = d.get("outcome")
        if isinstance(explicit, str) and explicit.upper() in ApplyOutcome.__members__:
            outcome = ApplyOutcome[explicit.upper()]
        else:
            outcome = outcome_from_wire(state, ok, counts)
        ops = tuple(OpResult.from_wire(o) for o in (d.get("ops") or []))
        failures = tuple(Failure.from_wire(f) for f in (d.get("failures") or []))
        failed_count = int(counts.get("fields_failed", 0) or 0) + int(
            counts.get("ops_failed", 0) or 0
        )
        written_count = int(counts.get("fields_written", 0) or 0)
        # A persisted explicit outcome is useful when hydrating history, but
        # it cannot bless contradictory runtime evidence.
        if outcome.is_success and (failed_count > 0 or failures):
            outcome = (
                ApplyOutcome.PARTIAL
                if written_count > 0 or int(counts.get("ops_ok", 0) or 0) > 0
                else ApplyOutcome.FAILED
            )
        # The job outcome is never better than its worst op (arch §3.4).
        if ops and outcome.is_terminal:
            outcome = min(outcome, min(o.outcome for o in ops))
        err = d.get("error")
        return cls(
            job_id=str(d.get("job_id", "")),
            outcome=outcome,
            ops=ops,
            failures=failures,
            started=_ts(d.get("started_at")),
            finished=_ts(d.get("finished_at")),
            counts=counts,
            env=dict(d.get("env") or {}),
            diagnostic=str(d.get("diagnostic") or (err.get("message", "") if isinstance(err, Mapping) else "")),
            error=err if isinstance(err, Mapping) else None,
            session_id=str(d.get("session_id", "")),
            label=str(d.get("label", "")),
            log=tuple(str(x) for x in (d.get("log") or [])),
            raw=dict(d),
        )


def _ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def worst(outcomes: Sequence[ApplyOutcome], default: ApplyOutcome = ApplyOutcome.APPLIED) -> ApplyOutcome:
    """Aggregate a batch: the worst outcome wins."""
    return min(outcomes, default=default)
