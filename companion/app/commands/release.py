"""Release ticked Club players through the existing Career transfer op.

The worker calls Live Editor's ``ReleasePlayerFromTeam``. Companion does not
invent a second removal path, and it only queues ids that are in the current
squad snapshot.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ...domain.job import Job, JobValidationError, op_transfer
from ..services import Services
from .apply import submit_job


def queue_club_release(svc: Services, rows: Sequence[Mapping[str, Any]]) -> str:
    """Queue one release job for the selected current-squad players."""
    return submit_job(svc, build_release_job(svc, rows))


def build_release_job(svc: Services, rows: Sequence[Mapping[str, Any]]) -> Job:
    state = svc.store.snapshot()
    blocked = _squad_block(state)
    if blocked:
        raise JobValidationError(blocked)
    targets = _targets(rows, state)
    if not targets:
        raise JobValidationError("Tick the players you want to release.")
    ops = tuple(
        replace(
            op_transfer(f"release-{index}", action="release", playerid=pid, verify=True),
            on_error="continue",
        )
        for index, (pid, _name) in enumerate(targets, start=1)
    )
    requires: dict[str, Any] = {}
    save_uid = str(state.squad.save_uid or "").strip()
    if save_uid:
        requires["save_uid"] = save_uid
    return Job(
        ops=ops,
        label=_label([name for _pid, name in targets]),
        origin="ui.club.release",
        requires=requires,
        # One attempt: a retry would call ReleasePlayerFromTeam again for
        # players who already left, then fail the read-back.
        max_attempts=1,
        budget_ms=min(5000, max(500, 200 * len(ops))),
    )


def _squad_block(state: Any) -> str:
    if not state.bridge.armed:
        return "Start FC 26 through Live Editor and load Career Mode."
    squad = state.squad
    if not squad.players or not squad.teamid:
        return "Refresh squad before releasing players."
    if squad.stale:
        return "Squad data is stale. Refresh squad before releasing players."
    live_sid = str(getattr(state.bridge.liveness, "session_id", "") or "")
    if squad.session_id and live_sid and squad.session_id != live_sid:
        return "Squad data is from another session. Refresh squad before releasing players."
    return ""


def _targets(
    rows: Sequence[Mapping[str, Any]],
    state: Any,
) -> tuple[tuple[int, str], ...]:
    roster = {}
    for player in state.squad.players:
        if not isinstance(player, Mapping):
            continue
        pid = _as_id(player.get("playerid") or player.get("id"))
        if pid is None:
            continue
        roster[pid] = str(player.get("name") or player.get("playername") or pid)
    chosen: list[tuple[int, str]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pid = _row_id(row)
        if pid is None or pid in seen or pid not in roster:
            continue
        seen.add(pid)
        raw = row.get("_raw") if isinstance(row.get("_raw"), Mapping) else {}
        name = str(row.get("name") or raw.get("name") or roster[pid] or pid).strip() or roster[pid]
        chosen.append((pid, name))
    return tuple(chosen)


def _row_id(row: Mapping[str, Any]) -> int | None:
    raw = row.get("_raw") if isinstance(row.get("_raw"), Mapping) else {}
    return _as_id(row.get("playerid") or row.get("id") or raw.get("playerid") or raw.get("id"))


def _as_id(value: Any) -> int | None:
    if value in (None, "", "—") or isinstance(value, bool):
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    return pid


def _label(names: Sequence[str]) -> str:
    if len(names) == 1:
        return f"Release: {names[0]}"
    prefix = f"Release {len(names)}: "
    joined = ", ".join(names)
    budget = 180 - len(prefix)
    if len(joined) <= budget:
        return prefix + joined
    return prefix + joined[: max(0, budget - 1)] + "…"
