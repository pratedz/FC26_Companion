"""Matchday submission and durable results, separate from unrelated Activity."""
from __future__ import annotations

from typing import Any

from ... import core_at_least
from ...core.db import JobRecord
from ...domain.automation_guide import map_job_failure, trusted_cached_teamid
from ...domain.matchday import ORIGIN, ROUTINE_KEY, TARGETS, Routine, build_preparation
from ...domain.outcome import ApplyOutcome, JobResult
from .. import events as E
from .apply import submit_job


class MatchdayError(ValueError):
    pass


def load_routine(svc: Any) -> Routine:
    return Routine.from_pref(svc.db.get_pref(ROUTINE_KEY) if svc.db else None)


def save_routine(svc: Any, routine: Routine) -> None:
    if svc.db:
        svc.db.set_pref(ROUTINE_KEY, routine.to_pref())


def _records(svc: Any) -> list[JobRecord]:
    # Include every pending preparation, even if hundreds of exports followed it.
    rows = svc.db.query(
        "SELECT * FROM job_record WHERE origin=? ORDER BY created_utc DESC, job_id DESC",
        (ORIGIN,),
    ) if svc.db else []
    return [JobRecord.from_row(row) for row in rows]


def pending_preparation(svc: Any) -> str:
    for record in _records(svc):
        if not _view_outcome(svc, record).is_terminal:
            return record.job_id
    return ""


def _current_job(svc: Any, job_id: str) -> Any:
    jobs = svc.store.snapshot().jobs
    return jobs.active.get(job_id) or next((j for j in jobs.history if j.job_id == job_id), None)


def _view_outcome(svc: Any, record: Any) -> ApplyOutcome:
    current = _current_job(svc, record.job_id)
    return current.outcome if current is not None else record.outcome


def disabled_reason(svc: Any, routine: Routine) -> str:
    state = svc.store.snapshot()
    live = state.bridge.liveness
    if not state.bridge.armed:
        return live.message or "Start FC 26 with Live Editor to prepare your squad."
    if live.capabilities.get("in_career") is False or live.capabilities.get("save_ready") is False:
        return "Load your Career save before preparing the squad."
    minimum = "2.6.31" if routine.injuries else "2.6.4"
    if not core_at_least(live.core_version, minimum):
        return "Restart FC 26 with Live Editor to load the updated Companion worker."
    if routine.empty:
        return "Choose at least one step in your routine."
    if pending_preparation(svc):
        return "A preparation is already waiting. Open Team Management or return to the Career hub."
    return ""


def prepare_squad(svc: Any, routine: Routine) -> str:
    # Fresh transport state prevents a just-switched save from inheriting the
    # prior view's scope. The worker also enforces the save requirement.
    svc.store.dispatch(E.LivenessChanged(svc.transport.liveness()))
    reason = disabled_reason(svc, routine)
    if reason:
        raise MatchdayError(reason)
    state = svc.store.snapshot()
    live = state.bridge.liveness
    teamid = trusted_cached_teamid(state, now=svc.clock.now())
    save_routine(svc, routine)
    return submit_job(svc, build_preparation(routine, save_uid=live.save_uid, teamid=teamid), follow=False)


def last_preparation(svc: Any) -> dict[str, Any]:
    live = svc.store.snapshot().bridge.liveness
    records = _records(svc)
    # A pending action must remain visible even after changing saves; it must
    # not become an invisible blocker. Completed actions belong to their save.
    record = next((r for r in records if not _view_outcome(svc, r).is_terminal), None)
    if record is None:
        record = next((r for r in records if live.save_uid and
                       str((r.job_json or {}).get("requires", {}).get("save_uid") or r.save_uid or "")
                       == live.save_uid), None)
    if record is None:
        return {"title": "Your next match starts here", "detail": "Prepare once, then head back to FC.",
                "tone": "muted", "job_id": "", "busy": False, "steps": []}
    outcome = _view_outcome(svc, record)
    current = _current_job(svc, record.job_id)
    result = (current.result if current else None) or (JobResult.from_wire(record.result_json) if record.result_json else None)
    titles = {ApplyOutcome.APPLIED: "Preparation applied", ApplyOutcome.NO_OP: "No changes needed",
              ApplyOutcome.PARTIAL: "Preparation partly applied", ApplyOutcome.QUEUED: "Preparation queued",
              ApplyOutcome.DEFERRED: "Preparing your squad…"}
    title = titles.get(outcome, "Preparation needs attention")
    tone = "ok" if outcome.is_success else "warn" if outcome in (
        ApplyOutcome.QUEUED, ApplyOutcome.DEFERRED, ApplyOutcome.PARTIAL) else "error"
    if not outcome.is_terminal:
        detail = "Open Team Management or return to the Career hub so FC can apply it."
        bound_save = str((record.job_json or {}).get("requires", {}).get("save_uid") or "")
        if bound_save and live.save_uid and bound_save != live.save_uid:
            detail = "This preparation belongs to another Career save. Open Activity to review or cancel it."
    elif outcome == ApplyOutcome.APPLIED:
        detail = "FC accepted the selected preparation steps. Check your squad in Team Management."
    elif outcome == ApplyOutcome.NO_OP:
        detail = "The worker finished without changing anything. Review the result in Activity."
    else:
        detail = map_job_failure(result) if result else "Open Activity for the recorded result."
    fields = next((op for op in (record.job_json or {}).get("ops", ()) if op.get("op") == "career.set"), {})
    steps = [f"{label} {fields[key]}" for key, label, _ in TARGETS if key in fields]
    if any(op.get("op") == "injury.cure" for op in (record.job_json or {}).get("ops", ())):
        steps.append("Injury recovery")
    return {"title": title, "detail": detail, "tone": tone, "job_id": record.job_id,
            "busy": not outcome.is_terminal, "steps": steps,
            "submitted": record.submitted_utc or record.created_utc}


# Used by Activity retry: rebuilding uses today's save and selected steps,
# never historical team IDs or a raw replay of a former Career save.
def retry_preparation(svc: Any, job_id: str) -> str:
    record = svc.db.job_record(job_id) if svc.db else None
    if record is None or record.origin != ORIGIN:
        raise MatchdayError("This is not a Matchday preparation.")
    ops = (record.job_json or {}).get("ops", ())
    fields = next((op for op in ops if op.get("op") == "career.set"), {})
    routine = Routine(**{key: key in fields for key, _label, _ in TARGETS},
                      injuries=any(op.get("op") == "injury.cure" for op in ops))
    return prepare_squad(svc, routine)

