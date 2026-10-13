"""Snapshot squad players for a Grok session without locking the Player tab."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from ...domain.job import Job, op_snapshot
from ...domain.outcome import ApplyOutcome, JobResult
from ...domain.player import EDITABLE_FIELDS, PlayerValidationError, normalize_record, player_id
from ..services import Services
from .apply import submit_job


def snapshot_players(
    svc: Services,
    playerids: Sequence[int],
    *,
    follow: bool = True,
    on_rows: Callable[[Mapping[int, Mapping[str, Any]]], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> str:
    """Queue one live snapshot for many ids. Does not change the locked target."""
    ids: list[int] = []
    seen: set[int] = set()
    for raw in playerids:
        pid = player_id(raw)
        if pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    if not ids:
        raise PlayerValidationError("No players to read.")
    source = svc.store.snapshot().squad
    liveness = svc.transport.liveness()
    from .. import events as E

    svc.store.dispatch(E.LivenessChanged(liveness))
    if not svc.store.snapshot().bridge.armed:
        raise PlayerValidationError(
            svc.store.snapshot().bridge.liveness.message or "Live Editor is not armed."
        )
    if source.save_uid and getattr(liveness, "save_uid", "") and source.save_uid != liveness.save_uid:
        raise PlayerValidationError("The Career save changed. Refresh Club and rebuild the plan.")
    if source.session_id and liveness.session_id and source.session_id != liveness.session_id:
        raise PlayerValidationError("The game session changed. Refresh Club before reading this plan.")
    job = Job(
        ops=(
            op_snapshot(
                "squad.session.read",
                playerids=ids,
                fields=EDITABLE_FIELDS,
            ),
        ),
        label="Read squad for Grok session",
        origin="ui.squad_planner.session_read",
        require_cm=True,
        budget_ms=5000,
        requires={"save_uid": source.save_uid} if source.save_uid else {},
    )

    def loaded(result: JobResult) -> None:
        try:
            current = svc.store.snapshot().squad
            if source.save_uid and (current.save_uid != source.save_uid or current.teamid != source.teamid):
                raise PlayerValidationError("The Career save or club changed during the read. Reopen the plan from Club.")
            result_save = str(result.env.get("save_uid") or "")
            if source.save_uid and result_save and source.save_uid != result_save:
                raise PlayerValidationError("The read belongs to another Career save. Nothing was staged.")
            if source.session_id and result.session_id and source.session_id != result.session_id:
                raise PlayerValidationError("The read belongs to an older game session. Refresh Club.")
            rows = rows_from_snapshot_result(result, ids)
            if not rows and result.outcome in (ApplyOutcome.APPLIED, ApplyOutcome.NO_OP):
                raise PlayerValidationError("Live read returned no players.")
            if on_rows is not None:
                on_rows(rows)
        except Exception as exc:  # noqa: BLE001
            if on_error is not None:
                on_error(exc)
            else:
                svc.store.dispatch(E.StatusSet(str(exc)))

    return submit_job(svc, job, follow=follow, on_result=loaded)


def rows_from_snapshot_result(
    result: JobResult,
    playerids: Sequence[int],
) -> dict[int, dict[str, Any]]:
    wanted = {int(pid) for pid in playerids}
    found: dict[int, dict[str, Any]] = {}
    for op in result.ops:
        if op.op not in {"snapshot"} and op.op_id not in {"squad.session.read", "player.read"}:
            continue
        rows = op.data.get("rows")
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                pid = player_id(row.get("playerid") or row.get("id"))
            except PlayerValidationError:
                continue
            if pid in wanted:
                normalized = normalize_record(row)
                if not row.get("name") and not row.get("playername"):
                    normalized.pop("name", None)
                found[pid] = normalized
    return found
