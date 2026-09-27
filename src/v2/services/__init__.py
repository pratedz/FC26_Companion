"""v2 domain services — the only thing the UI is allowed to call.

Every surface in ``docs/V2_UX_DESIGN.md`` is backed by one module here:

===============  ==========================================================
``squad``        Club — read the squad, filter it, describe one player.
``player``       Player — the single edit surface, with field provenance.
``signings``     Transfers ▸ Sign — search 55k players, preflight, sign.
``transfers``    Transfers — move / loan / release / budget.
``automations``  Automations — 51 profiles + 19 packs + the queue.
``library``      Library — catalog sync status, sources, index, stats.
===============  ==========================================================

Rules these modules keep (they are the reason v1's UI was untestable):

* **No tkinter, no widgets, no globals.** Everything returned is a plain
  dataclass. A test can construct the whole world from ``tmp_path``.
* **Nothing blocks.** Reads are file/SQLite reads measured in microseconds.
  Anything that can be slow takes ``progress`` and defaults ``wait=False``,
  so the caller owns the worker thread.
* **Every mutating call returns a** :class:`MutationResult` carrying the
  outcome enum, a human reason, the job id, and whether undo is available.
* **Never claim applied without proof.** :func:`from_apply_result` refuses to
  emit :attr:`Outcome.APPLIED` unless the worker returned a result token for
  the job. Elapsed time and heartbeats are not proof (UX doc §5.3 H1).

Interface note for the transport/protocol agent
-----------------------------------------------
``src/v2/protocol.py``, ``transport.py``, ``jobs.py`` and ``outcomes.py`` did
not exist when this layer was written, so :class:`Outcome` and
:class:`MutationResult` are defined here as the thin contract this layer
expects. If ``v2.outcomes`` later defines its own enum, the mapping to
reconcile is :data:`APPLY_OUTCOME_MAP` — v1's ``apply_service`` strings on the
left, this enum on the right. Nothing else in this package depends on the
spelling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = [
    "Outcome",
    "Provenance",
    "MutationResult",
    "Progress",
    "APPLY_OUTCOME_MAP",
    "TERMINAL_FAILURES",
    "blocked",
    "from_apply_result",
    "tick",
]


#: ``progress(fraction_0_to_1, human_message)`` — optional on every slow call.
Progress = Optional[Callable[[float, str], None]]


def tick(progress: Progress, frac: float, message: str) -> None:
    """Call ``progress`` without ever letting a UI callback break a service."""
    if progress is None:
        return
    try:
        progress(float(frac), str(message))
    except Exception:  # noqa: BLE001 - a broken UI callback is not our failure
        pass


class Outcome(str, Enum):
    """The five honest states of a change, plus ``STAGED`` for "not written".

    Mirrors the state machine in ``docs/V2_UX_DESIGN.md`` §5.3. ``QUEUED`` is
    *not* a failure and *not* a timeout: the job is on disk and runs when the
    game next fires a Career Mode event.
    """

    STAGED = "staged"
    QUEUED = "queued"
    RUNNING = "running"
    APPLIED = "applied"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


#: ``apply_service`` outcome string -> :class:`Outcome`.
APPLY_OUTCOME_MAP: Dict[str, Outcome] = {
    "applied": Outcome.APPLIED,
    "queued_live": Outcome.QUEUED,
    "timeout": Outcome.TIMEOUT,
    "blocked": Outcome.BLOCKED,
    "error": Outcome.ERROR,
}

#: Outcomes that must never be reported to the user as success.
TERMINAL_FAILURES = (Outcome.TIMEOUT, Outcome.BLOCKED, Outcome.ERROR)


class Provenance(str, Enum):
    """Where a field's displayed value came from.

    ``READ``     we read it out of the user's save (squad export / DB capture).
    ``EDITED``   the user changed it in this session. **Only these are written.**
    ``UNKNOWN``  we do not know it. Renders as ``—`` and is structurally
    excluded from every write (UX doc §8.3 H2).
    """

    READ = "read"
    EDITED = "edited"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


@dataclass(frozen=True)
class MutationResult:
    """What every mutating service call returns. Never a bare bool.

    Attributes:
        outcome: The honest state. ``APPLIED`` requires ``proof``.
        reason: One sentence a human can read, in the user's language.
        job_id: The queue file name — the protocol's identity for this job.
            Empty when nothing was written (``BLOCKED`` before the queue).
        undo_available: True only when a snapshot exists to roll this back.
        undo_id: The snapshot id to hand back to ``player.undo`` /
            ``product.restore_snapshot``. Empty when there is none.
        proof: The worker's own result token that justified ``APPLIED``.
        detail: The job description written into the queue metadata.
        meta: Service-specific extras (written fields, blast radius, …).
    """

    outcome: Outcome
    reason: str = ""
    job_id: str = ""
    undo_available: bool = False
    undo_id: str = ""
    queue_file: str = ""
    proof: str = ""
    detail: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is Outcome.APPLIED and not self.proof:
            raise ValueError(
                "APPLIED requires proof — a worker result token for this job. "
                "Use Outcome.QUEUED when all you have is elapsed time."
            )

    @property
    def applied(self) -> bool:
        """True only with worker proof. Never infer this from anything else."""
        return self.outcome is Outcome.APPLIED

    @property
    def queued(self) -> bool:
        return self.outcome is Outcome.QUEUED

    @property
    def ok(self) -> bool:
        """The change is either done or safely on its way. Not "no error"."""
        return self.outcome in (Outcome.APPLIED, Outcome.QUEUED)

    @property
    def failed(self) -> bool:
        return self.outcome in TERMINAL_FAILURES

    def with_meta(self, **extra: Any) -> "MutationResult":
        merged = dict(self.meta)
        merged.update(extra)
        return MutationResult(
            outcome=self.outcome,
            reason=self.reason,
            job_id=self.job_id,
            undo_available=self.undo_available,
            undo_id=self.undo_id,
            queue_file=self.queue_file,
            proof=self.proof,
            detail=self.detail,
            meta=merged,
        )


def blocked(reason: str, **meta: Any) -> MutationResult:
    """A refusal that never touched the queue. Always says why."""
    return MutationResult(outcome=Outcome.BLOCKED, reason=reason, meta=dict(meta))


def _job_id_from(queue_file: Optional[str]) -> str:
    if not queue_file:
        return ""
    text = str(queue_file).replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def from_apply_result(
    result: Any,
    *,
    fallback_reason: str = "",
    undo_id: str = "",
    undo_available: Optional[bool] = None,
    **meta: Any,
) -> MutationResult:
    """Translate an ``apply_service.ApplyResult`` into a :class:`MutationResult`.

    This is the single choke point for the "never claim applied without proof"
    rule. ``ApplyResult.applied`` is trusted only when the worker also returned
    a result token (``last_result``); otherwise the state is downgraded to
    ``QUEUED`` and the reason says so, because a job on disk with no worker
    reply is exactly what "queued" means.
    """
    outcome_s = str(getattr(result, "outcome", "") or "").strip().lower()
    outcome = APPLY_OUTCOME_MAP.get(outcome_s, Outcome.ERROR)
    reason = str(getattr(result, "reason", "") or "") or fallback_reason
    proof = str(getattr(result, "last_result", "") or "").strip()
    raw_meta = getattr(result, "meta", None)
    extra: Dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, Mapping) else {}
    extra.update(meta)

    if outcome is Outcome.APPLIED and not proof:
        # The worker never said anything about this job. On disk is not done.
        outcome = Outcome.QUEUED
        reason = (
            "Queued — the job is written but the worker has not reported a "
            "result yet. This runs the next time a Career Mode event fires."
        )
        extra["downgraded_from"] = "applied_without_proof"

    snapshot_error = extra.get("snapshot_error")
    if undo_available is None:
        undo_available = bool(undo_id) and not snapshot_error
    if snapshot_error:
        extra["undo_available"] = False
        undo_available = False

    queue_file = str(getattr(result, "queue_file", "") or "")
    return MutationResult(
        outcome=outcome,
        reason=reason,
        job_id=_job_id_from(queue_file),
        undo_available=bool(undo_available),
        undo_id=undo_id if undo_available else "",
        queue_file=queue_file,
        proof=proof if outcome is Outcome.APPLIED else "",
        detail=str(getattr(result, "detail", "") or ""),
        meta=extra,
    )
