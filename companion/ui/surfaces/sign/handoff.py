"""Verified signing receipts resolved against the refreshed Career roster."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ....app.state import SigningBatch
from ....domain.outcome import ApplyOutcome, JobResult


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def signing_batches(svc: Any) -> tuple[SigningBatch, ...]:
    """Session bags plus durable audit receipts, never guessed Library IDs."""
    state = svc.store.snapshot()
    batches = list(state.signing.batches)
    known = {jid for batch in batches for jid in batch.job_ids}
    db = getattr(svc, "db", None)
    cache = getattr(getattr(svc, "ui", None), "_signing_receipt_cache", None)
    signature = (id(db), state.squad.save_uid, state.squad.teamid, state.jobs, state.signing)
    if (cache is not None and cache[0][:3] == signature[:3]
            and cache[0][3] is state.jobs and cache[0][4] is state.signing):
        return cache[1]
    if db is not None and state.squad.save_uid and state.squad.teamid:
        for record in db.job_records(limit=200, save_uid=state.squad.save_uid):
            if record.job_id in known:
                continue
            job = record.job_json or {}
            ops = [op for op in job.get("ops", ()) if op.get("op") == "add_to_team"]
            if not ops or job.get("dry_run") or any(op.get("dry_run") for op in ops):
                continue
            if any(_integer(op.get("teamid")) != state.squad.teamid for op in ops):
                continue
            count = sum(len(op.get("batch") or (op,)) for op in ops)
            raw = record.result_json or {}
            result = JobResult.from_wire(raw) if raw else None
            transport = getattr(svc, "transport", None)
            if transport is not None and (result is None or not result.outcome.is_terminal):
                live = transport.result(record.job_id)
                if live is not None and live.outcome.is_terminal:
                    result = live
            batches.append(SigningBatch(
                (record.job_id,), count, record.save_uid, state.squad.teamid,
                str(job.get("requires", {}).get("session_id") or ""),
                _date(record.submitted_utc or record.created_utc),
                {record.job_id: result} if result is not None else {},
            ))
    ordered = tuple(sorted(batches, key=lambda batch: batch.submitted.timestamp() if batch.submitted else 0,
                           reverse=True))
    waiting = any(
        (batch.results.get(jid) is None or not batch.results[jid].outcome.is_terminal)
        for batch in ordered for jid in batch.job_ids
    )
    if not waiting and hasattr(getattr(svc, "ui", None), "_signing_receipt_cache"):
        svc.ui._signing_receipt_cache = (signature, ordered)
    return ordered


def verified_batch_ids(batch: SigningBatch) -> tuple[int, ...]:
    """Verified successful operations survive unrelated failed/waiting jobs."""
    ids = []
    for jid in batch.job_ids:
        result = batch.results.get(jid)
        if result is None or not result.outcome.is_terminal:
            continue
        if result.env.get("save_uid") and result.env["save_uid"] != batch.save_uid:
            continue
        if batch.session_id and result.session_id and batch.session_id != result.session_id:
            continue
        for op in result.ops:
            if op.op != "add_to_team" or op.outcome is not ApplyOutcome.APPLIED:
                continue
            data = op.data
            if data.get("verified") is not True or _integer(data.get("teamid")) != batch.teamid:
                continue
            raw_ids = data.get("playerids") or (data.get("playerid"),)
            if not isinstance(raw_ids, (tuple, list)):
                continue
            op_ids = []
            for value in raw_ids:
                pid = _integer(value)
                if pid is None or pid < 1 or pid in op_ids:
                    op_ids = []
                    break
                op_ids.append(pid)
            ids.extend(pid for pid in op_ids if pid not in ids)
    return tuple(ids) if len(ids) <= batch.selected_count else ()


def fresh_batch_rows(svc: Any, batch: SigningBatch) -> tuple[dict[str, Any], ...]:
    state = svc.store.snapshot()
    squad, live = state.squad, state.bridge.liveness
    from ....app.presenters import squad_view
    if squad_view(state, now=svc.clock.now()).get("stale"):
        return ()
    if (squad.stale or not squad.taken or squad.save_uid != batch.save_uid or squad.teamid != batch.teamid
            or not squad.save_uid or not squad.teamid):
        return ()
    if live.save_uid and live.save_uid != squad.save_uid:
        return ()
    if live.session_id and squad.session_id and live.session_id != squad.session_id:
        return ()
    ids = verified_batch_ids(batch)
    if not ids:
        return ()
    completed = max((result.finished or batch.submitted for result in batch.results.values()
                     if result.finished or batch.submitted), default=None)
    if completed and squad.taken < completed:
        return ()
    roster = {_integer(row.get("playerid") or row.get("id")): row for row in squad.players}
    return tuple(dict(roster[pid]) for pid in ids if pid in roster)


def recently_added_ids(svc: Any) -> tuple[int, ...]:
    return tuple(dict.fromkeys(int(row.get("playerid") or row.get("id"))
                               for batch in signing_batches(svc) for row in fresh_batch_rows(svc, batch)))


def _squad_refusal_message(results: list[Any]) -> str:
    """Explain a finished sign that never joined the club."""
    for result in results:
        if result is None:
            continue
        for failure in getattr(result, "failures", ()):
            reason = str(getattr(failure, "reason", "") or "")
            detail = str(getattr(failure, "detail", "") or "")
            if reason == "squad_full":
                text = detail or "FC refused the transfer because the squad is full."
                return (
                    f"{text} Restart FC with Live Editor and Companion will place "
                    "these players on the squad."
                )
            if reason == "team_not_applied" and "111592" in detail:
                return (
                    "FC refused the transfer because the squad is full, so these players "
                    "stayed free agents. Restart FC with Live Editor and Companion will "
                    "place them on the squad."
                )
    return ""


def latest_signing_handoff(svc: Any) -> tuple[str, SigningBatch | None, tuple[dict[str, Any], ...]]:
    state = svc.store.snapshot()
    batches = [batch for batch in signing_batches(svc)
               if batch.save_uid == state.squad.save_uid and batch.teamid == state.squad.teamid]
    if not batches:
        return "", None, ()
    batch = batches[0]
    results = [batch.results.get(jid) for jid in batch.job_ids]
    waiting_jobs = sum(result is None or not result.outcome.is_terminal for result in results)
    ids = verified_batch_ids(batch)
    rows = fresh_batch_rows(svc, batch)
    if waiting_jobs:
        message = (f"{batch.selected_count} players reviewed · {len(ids)} verified · "
                   f"{waiting_jobs} signing job{'s' if waiting_jobs != 1 else ''} waiting.")
        return message, batch, rows
    if not ids:
        blocked = _squad_refusal_message(results)
        if blocked:
            return blocked, batch, ()
        return "Signing has no complete verified receipt. Check Activity before planning.", batch, ()
    if not rows:
        return f"{len(ids)} of {batch.selected_count} players verified. Refresh this Career squad to plan them.", batch, ()
    unconfirmed = batch.selected_count - len(ids)
    suffix = f" · {unconfirmed} player results unconfirmed; check Activity." if unconfirmed else ""
    missing = len(ids) - len(rows)
    if missing:
        suffix += f" · {missing} verified players absent from the current squad."
    return f"{len(rows)} signed players ready to plan ({len(ids)} of {batch.selected_count} verified){suffix}", batch, rows


def plan_signed_players(svc: Any, batch: SigningBatch) -> bool:
    rows = fresh_batch_rows(svc, batch)
    if not rows:
        from .._common import set_status
        set_status(svc, "Refresh the same Career squad before planning these signed players.")
        return False
    from ..planner import open_planner
    club = str(rows[0].get("teamname") or rows[0].get("club") or "")
    open_planner(svc, rows, mode="per_player", club_name=club)
    return True


def open_signed_club(svc: Any, batch: SigningBatch) -> bool:
    rows = fresh_batch_rows(svc, batch)
    if not rows:
        from .._common import set_status
        set_status(svc, "Refresh the same Career squad before opening these signed players.")
        return False
    return bool(svc.ui.open_club(tuple(row.get("playerid") or row.get("id") for row in rows),
                                 batch.save_uid, batch.teamid, recently_added=True))
