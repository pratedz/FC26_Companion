"""The one closed outcome enum for protocol v3.

v1 spread the same question across nine shapes: ``ApplyResult.applied`` /
``.queued`` / ``.outcome`` (three fields that can disagree), five different
``wait_until_applied`` dicts, ten ``{"ok": bool}`` variants, bare bools, and a
regex-scraped status line. It had no ``timeout`` member at all until late, so a
90-second hang and a job that was never collected both reported ``queued_live``
— "fine, it is on its way".

v3 has six members and nothing else. Ordered worst-to-best so "worst wins" is
``min()``. There is deliberately no ``UNKNOWN``: when the transport cannot tell
what happened, the answer is :data:`Outcome.BLOCKED`, which is never reported as
success.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable, Mapping, Optional, Union


class Outcome(IntEnum):
    """Closed set of job / op outcomes. Lower is worse."""

    BLOCKED = 0   # never reached the game: not armed, rejected, cancelled, vanished
    FAILED = 1    # ran and did not do what was asked (incl. found=false, partial)
    TIMEOUT = 2   # accepted, but no verdict inside the wait window
    APPLIED = 3   # ran, wrote, and verified
    RUNNING = 4   # claimed by a session, mid-flight
    QUEUED = 5    # written to the queue, not claimed yet

    @property
    def wire(self) -> str:
        """Lowercase name as written into result JSON."""
        return self.name.lower()

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL

    @property
    def is_success(self) -> bool:
        """Only APPLIED. ``TIMEOUT``/``FAILED``/``BLOCKED`` are never success."""
        return self is Outcome.APPLIED

    @property
    def human(self) -> str:
        return _HUMAN[self]


#: Outcomes that will never change again for a given job.
TERMINAL = frozenset({Outcome.APPLIED, Outcome.FAILED, Outcome.TIMEOUT, Outcome.BLOCKED})
#: Outcomes that mean "ask again later".
PENDING = frozenset({Outcome.QUEUED, Outcome.RUNNING})
#: Terminal outcomes that must never be surfaced to a caller as ok/success.
FAILED_OUTCOMES = frozenset({Outcome.FAILED, Outcome.TIMEOUT, Outcome.BLOCKED})

_HUMAN: Mapping[Outcome, str] = {
    Outcome.BLOCKED: "Blocked — never reached the game",
    Outcome.FAILED: "Failed — the game ran it and it did not do what was asked",
    Outcome.TIMEOUT: "Timed out — no verdict inside the wait window",
    Outcome.APPLIED: "Applied — written and verified",
    Outcome.RUNNING: "Running — claimed by the in-game core",
    Outcome.QUEUED: "Queued — waiting for the next Career Mode tick",
}

#: Every legacy / in-game spelling we accept on the way in. Anything richer than
#: the six members collapses *downwards*: ``partial`` and ``no_op`` are FAILED,
#: because a job that wrote 88 of 91 fields, or found nothing at all, is not an
#: apply. That collapse is the point of the enum, not a lossy accident.
ALIASES: Mapping[str, Outcome] = {
    "applied": Outcome.APPLIED,
    "ok": Outcome.APPLIED,
    "success": Outcome.APPLIED,
    "done": Outcome.APPLIED,
    "queued": Outcome.QUEUED,
    "queued_live": Outcome.QUEUED,
    "accepted": Outcome.QUEUED,
    "running": Outcome.RUNNING,
    "claimed": Outcome.RUNNING,
    "deferred": Outcome.RUNNING,
    "timeout": Outcome.TIMEOUT,
    "timed_out": Outcome.TIMEOUT,
    "expired": Outcome.TIMEOUT,
    "failed": Outcome.FAILED,
    "fail": Outcome.FAILED,
    "error": Outcome.FAILED,
    "partial": Outcome.FAILED,
    "no_op": Outcome.FAILED,
    "crashed": Outcome.FAILED,
    "poisoned": Outcome.FAILED,
    "blocked": Outcome.BLOCKED,
    "rejected": Outcome.BLOCKED,
    "cancelled": Outcome.BLOCKED,
    "canceled": Outcome.BLOCKED,
    "skipped": Outcome.BLOCKED,
}


def parse_outcome(
    value: Union[str, int, Outcome, None],
    *,
    default: Optional[Outcome] = None,
) -> Outcome:
    """Coerce a wire value to an :class:`Outcome`.

    Unknown input never becomes APPLIED. Pass ``default`` to choose the fallback
    (callers reading a result file should pass ``Outcome.FAILED``); without one,
    unknown input raises.
    """
    if isinstance(value, Outcome):
        return value
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly
        raise ValueError("refusing to read a bool as an Outcome")
    if isinstance(value, int):
        try:
            return Outcome(value)
        except ValueError:
            pass
    elif isinstance(value, str):
        hit = ALIASES.get(value.strip().lower())
        if hit is not None:
            return hit
    if default is not None:
        return default
    raise ValueError(f"not an outcome: {value!r}")


def worst(outcomes: Iterable[Outcome], *, default: Outcome = Outcome.BLOCKED) -> Outcome:
    """Lowest member of the total order, or ``default`` when empty."""
    return min(outcomes, default=default)


def derive_job_outcome(
    op_outcomes: Iterable[Outcome],
    *,
    default: Outcome = Outcome.BLOCKED,
) -> Outcome:
    """Job outcome from its ops: worst terminal op, or RUNNING if any is pending.

    Plain :func:`worst` is not enough on its own — RUNNING and QUEUED sort
    *above* APPLIED, so a job with one applied op and one still in flight would
    read APPLIED. A job is only as finished as its least-finished op.
    """
    items = list(op_outcomes)
    if not items:
        return default
    if any(o in PENDING for o in items):
        return Outcome.RUNNING
    return worst(items, default=default)
