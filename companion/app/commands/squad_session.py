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
    liveness = svc.transport.liveness()
    from .. import events as E

    svc.store.dispatch(E.LivenessChanged(liveness))
    if not svc.store.snapshot().bridge.armed:
        raise PlayerValidationError(
            svc.store.snapshot().bridge.liveness.message or "Live Editor is not armed."
        )
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
    )

    def loaded(result: JobResult) -> None:
        try:
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
                found[pid] = normalize_record(row)
    return found
