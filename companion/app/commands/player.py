"""Target and read one player without ever fabricating editor values."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ...domain.job import Job, op_snapshot
from ...domain.outcome import ApplyOutcome, JobResult
from ...domain.player import (
    EDITABLE_FIELDS,
    PlayerRecord,
    PlayerValidationError,
    categories_for,
    normalize_record,
    player_id,
)
from .. import events as E
from ..services import Services
from .apply import submit_job

# Compact squad rows only carry identity + OVR/POT. A live ``player.read``
# returns the full editor schema; this floor is the same sparse check the card
# importer uses so preview and staging agree on when a read is still needed.
LIVE_VALUE_FIELD_FLOOR = 10
_live_read_hooks: dict[tuple[int, int], list[Callable[[Mapping[str, Any]], None]]] = {}


def lock_player(
    svc: Services,
    playerid: Any,
    *,
    record: Mapping[str, Any] | None = None,
    name: str = "",
    source: str = "manual",
    teamid: int | None = None,
) -> PlayerRecord:
    """Lock one app-wide target and load only values we actually possess."""
    pid = player_id(playerid)
    candidate = dict(record or _cached_player(svc, pid) or {})
    candidate["playerid"] = pid
    if name and not candidate.get("name") and not candidate.get("playername"):
        candidate["name"] = name
    player = PlayerRecord.from_mapping(candidate)
    svc.store.dispatch(
        E.TargetLocked(
            playerid=pid,
            name=player.name or name,
            source=source,
            teamid=teamid,
        )
    )
    svc.store.dispatch(
        E.EditorLoaded(
            base=player.fields,
            categories=categories_for(player.fields),
        )
    )
    return player


def reload_player(
    svc: Services,
    *,
    follow: bool = True,
    on_loaded: Any = None,
) -> str:
    """Read the locked player from the active career database via ``snapshot``."""
    # The button is an explicit user request: sample transport now rather than
    # refusing from a status pill that may be one polling interval behind.
    liveness = svc.transport.liveness()
    svc.store.dispatch(E.LivenessChanged(liveness))
    state = svc.store.snapshot()
    if state.target.playerid is None:
        raise PlayerValidationError("Pick or enter a player ID first.")
    if state.editor.has_changes:
        raise PlayerValidationError(
            "Reset or apply staged changes before reloading live values."
        )
    if not state.bridge.armed:
        raise PlayerValidationError(
            state.bridge.liveness.message or "Live Editor is not armed."
        )
    pid = state.target.playerid
    if player_read_pending(state, pid):
        if on_loaded is not None:
            _add_live_read_hook(svc, int(pid), on_loaded)
            return player_read_job_id(state, pid)
        raise PlayerValidationError(
            "A live read for this player is already queued. Wait for it to finish "
            "instead of queuing another copy."
        )
    job = Job(
        ops=(
            op_snapshot(
                "player.read",
                playerids=(pid,),
                fields=EDITABLE_FIELDS,
            ),
        ),
        label=f"Read player {pid}",
        origin="ui.player.reload",
        require_cm=True,
    )

    def loaded(result: JobResult) -> None:
        row = _snapshot_row(result, pid)
        if row is None:
            if result.outcome.is_terminal:
                _pop_live_read_hooks(svc, int(pid))
            if result.outcome in (ApplyOutcome.APPLIED, ApplyOutcome.NO_OP):
                svc.store.dispatch(E.StatusSet(f"Player {pid} was not found in this save."))
            return
        # Ignore a late result when the user selected another player meanwhile.
        if svc.store.snapshot().target.playerid != pid:
            _pop_live_read_hooks(svc, int(pid))
            return
        if svc.store.snapshot().editor.has_changes:
            _pop_live_read_hooks(svc, int(pid))
            svc.store.dispatch(
                E.StatusSet(
                    "Live read finished, but newer staged changes were preserved. "
                    "Reset them before reloading."
                )
            )
            return
        lock_player(
            svc,
            pid,
            record=row,
            name=state.target.name,
            source="live",
            teamid=state.target.teamid,
        )
        svc.store.dispatch(E.StatusSet(f"Loaded live values for player {pid}."))
        callbacks = []
        if on_loaded is not None:
            callbacks.append(on_loaded)
        callbacks.extend(_pop_live_read_hooks(svc, int(pid)))
        for callback in callbacks:
            try:
                callback(row)
            except Exception as exc:  # noqa: BLE001
                svc.store.dispatch(E.StatusSet(str(exc)))

    return submit_job(svc, job, follow=follow, on_result=loaded)


def editor_has_live_values(state: Any) -> bool:
    """True when the editor holds a full live read, not a compact squad row."""
    return len(getattr(getattr(state, "editor", None), "base", {}) or {}) >= (
        LIVE_VALUE_FIELD_FLOOR
    )


def player_read_job_id(state: Any, playerid: int | None) -> str:
    """Job id of the in-flight live read for ``playerid``, or ``""``."""
    if playerid is None:
        return ""
    label = f"Read player {int(playerid)}"
    for job_id, view in state.jobs.active.items():
        if view.label == label and not view.done:
            return str(job_id)
    return ""


def player_read_pending(state: Any, playerid: int | None) -> bool:
    """Whether this exact target already has a submitted live-read job.

    The queue is intentionally asynchronous, so repeat clicks used to create a
    stack of identical snapshots.  One read contains all editable fields; a
    second one cannot make the result fresher and only delays the queue.
    """
    return bool(player_read_job_id(state, playerid))


def ensure_live_player(
    svc: Services,
    *,
    on_loaded: Callable[[Mapping[str, Any]], None] | None = None,
) -> str:
    """Start a live player read when the editor only has a compact squad row.

    A second caller while the read is in flight attaches ``on_loaded`` to that
    job instead of queueing a duplicate snapshot. Already-complete live values
    invoke ``on_loaded`` immediately and submit nothing.
    """
    state = svc.store.snapshot()
    if editor_has_live_values(state):
        if on_loaded is not None:
            on_loaded(dict(state.editor.base))
        return ""
    pid = state.target.playerid
    if player_read_pending(state, pid):
        if on_loaded is not None:
            _add_live_read_hook(svc, int(pid), on_loaded)
        return player_read_job_id(state, pid)
    return reload_player(svc, on_loaded=on_loaded)


def _add_live_read_hook(
    svc: Services, playerid: int, callback: Callable[[Mapping[str, Any]], None]
) -> None:
    _live_read_hooks.setdefault((id(svc), int(playerid)), []).append(callback)


def _pop_live_read_hooks(
    svc: Services, playerid: int
) -> list[Callable[[Mapping[str, Any]], None]]:
    return list(_live_read_hooks.pop((id(svc), int(playerid)), ()))


def _cached_player(svc: Services, pid: int) -> Mapping[str, Any] | None:
    for row in svc.store.snapshot().squad.players:
        try:
            if player_id(row.get("playerid", row.get("id"))) == pid:
                return row
        except PlayerValidationError:
            continue
    return None


def _snapshot_row(result: JobResult, pid: int) -> dict[str, Any] | None:
    for op in result.ops:
        if op.op_id != "player.read" and op.op != "snapshot":
            continue
        rows = op.data.get("rows")
        if not isinstance(rows, (list, tuple)):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                if player_id(row.get("playerid")) == pid:
                    return normalize_record(row)
            except PlayerValidationError:
                continue
    return None
