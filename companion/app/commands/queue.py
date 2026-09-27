"""Commands for managing waiting worker jobs without lying about in-flight work."""

from __future__ import annotations

from .. import events as E
from ..services import Services


def _cancelled_result(job_id: str, label: str) -> object:
    """A truthful terminal record for a job removed before LE claimed it."""
    from ...domain.outcome import JobResult

    return JobResult.from_wire(
        {
            "job_id": job_id,
            "label": label,
            "state": "expired",
            "outcome": "expired",
            "ok": False,
            "diagnostic": "Canceled by the player before Live Editor claimed it.",
            "counts": {},
            "failures": [
                {
                    "reason": "cancelled_by_user",
                    "detail": "Removed from the waiting queue before execution.",
                    "phase": "queue",
                }
            ],
            "error": {
                "phase": "queue",
                "message": "cancelled_by_user",
                "detail": "Removed from the waiting queue before execution.",
            },
        }
    )


def clear_all(svc: Services) -> int:
    """Cancel only jobs still waiting on disk; claimed jobs may already write.

    A claimed add-player job can be executing inside FC 26 even if its queue
    file disappears. The normal Clear action must never call that work
    "cancelled" and hide a later real result. It removes only entries that the
    transport confirms are still pending, then preserves claimed jobs in the
    visible Activity list.
    """
    from .apply import _persist_result, reconcile_missing_queue_entries

    active = dict(svc.store.snapshot().jobs.active)
    pending = set(svc.transport.pending_ids())
    cancelled = 0
    for job_id in pending:
        if not svc.transport.cancel(job_id):
            # A claim race is safe: keep tracking it as working.
            continue
        view = active.get(job_id)
        final = svc.transport.result(job_id)
        if final is not None and final.outcome.is_terminal:
            result = final
        else:
            result = _cancelled_result(job_id, view.label if view is not None else "")
        _persist_result(svc, result)  # durable rows must not resurrect on restart
        svc.store.dispatch(E.JobFinished(job_id=job_id, result=result))
        cancelled += 1

    # A prior app version could leave rows active after files were already
    # removed. Reconcile only records older than the normal grace window.
    ghosts = reconcile_missing_queue_entries(svc)
    claimed = len(getattr(svc.transport, "claimed_ids", lambda: [])())
    detail = (
        f"Cleared {cancelled} waiting job{'s' if cancelled != 1 else ''}"
        + (f" and closed {ghosts} old missing record{'s' if ghosts != 1 else ''}" if ghosts else "")
        + "."
    )
    if claimed:
        detail += f" {claimed} already-working job{'s' if claimed != 1 else ''} were left to finish safely."
    svc.store.dispatch(E.StatusSet(detail))
    return cancelled
