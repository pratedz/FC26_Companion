"""Scan and cure Career Mode squad injuries through protocol-v3 jobs."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Mapping, Sequence

from ...domain.job import Job, op_injury_cure, op_injury_scan
from ...domain.outcome import ApplyOutcome, JobResult
from .. import events as E
from ..services import Services
from .apply import submit_job


class InjuryError(RuntimeError):
    """Local precondition failure before a job is queued."""


def parse_scan_names(data: Mapping[str, Any] | None) -> list[str]:
    """Pure parser: injured player display names from an ``injury.scan`` data blob."""
    players = (data or {}).get("players") or ()
    names: list[str] = []
    for row in players:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            pid = row.get("playerid")
            name = str(pid).strip() if pid is not None else ""
        if name:
            names.append(name)
    return names


def parse_scan_players(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Pure parser: ``{playerid, name, injury}`` rows from scan data."""
    players = (data or {}).get("players") or ()
    out: list[dict[str, Any]] = []
    for row in players:
        if not isinstance(row, Mapping):
            continue
        try:
            pid = int(row.get("playerid"))
        except (TypeError, ValueError):
            continue
        name = str(row.get("name") or "").strip() or str(pid)
        injury = str(row.get("injury") or "").strip() or "injured"
        parsed = {"playerid": pid, "name": name, "injury": injury}
        if return_date_label(row.get("return_date")):
            parsed["return_date"] = int(row["return_date"])
        out.append(parsed)
    return out


def return_date_label(value: Any) -> str:
    """Format a validated in-game YYYYMMDD date, without inventing a date."""
    try:
        raw = str(value)
        if len(raw) != 8 or not raw.isdigit():
            return ""
        parsed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except (TypeError, ValueError):
        return ""
    return f"{parsed.day} {parsed:%b %Y}"


def cure_player_ids(result: JobResult) -> set[int]:
    ids: set[int] = set()
    for op in result.ops:
        if op.op == "injury.cure":
            for value in op.data.get("cured_ids") or ():
                try:
                    ids.add(int(value))
                except (ValueError, TypeError):
                    continue
    return ids


def result_message(result: JobResult) -> str:
    """Best human-readable message from a job result (Lua detail preferred)."""
    for failure in result.failures:
        text = (failure.detail or failure.reason or "").strip()
        if text:
            return text
    if result.diagnostic:
        return str(result.diagnostic).strip()
    err = result.error
    if isinstance(err, Mapping):
        text = str(err.get("message") or err.get("detail") or "").strip()
        if text:
            return text
    return result.outcome.label


def scan_data(result: JobResult) -> Mapping[str, Any]:
    for op in result.ops:
        if op.op == "injury.scan":
            return op.data
    return {}


def cure_names(result: JobResult) -> list[str]:
    for op in result.ops:
        if op.op == "injury.cure":
            raw = op.data.get("cured") or ()
            return [str(name) for name in raw if str(name).strip()]
    return []


def is_success(result: JobResult) -> bool:
    return result.outcome in (ApplyOutcome.APPLIED, ApplyOutcome.NO_OP)


def _require_armed(svc: Services) -> None:
    liveness = svc.transport.liveness()
    svc.store.dispatch(E.LivenessChanged(liveness))
    state = svc.store.snapshot()
    if not state.bridge.armed:
        raise InjuryError(
            state.bridge.liveness.message or "Live Editor is not armed."
        )


def scan_injuries(
    svc: Services,
    on_result: Callable[[JobResult], None] | None = None,
) -> str:
    """Queue ``injury.scan`` for the current Career Mode user squad."""
    _require_armed(svc)
    job = Job(
        ops=(op_injury_scan("scan"),),
        label="Scan squad injuries",
        origin="ui.injury.scan",
        require_cm=True,
    )
    return submit_job(svc, job, on_result=on_result)


def cure_injuries(
    svc: Services,
    playerids: Sequence[int] | None = None,
    on_result: Callable[[JobResult], None] | None = None,
) -> str:
    """Queue ``injury.cure``. Empty ``playerids`` cures every injured squad member."""
    _require_armed(svc)
    ids = [int(p) for p in (playerids or ())]
    job = Job(
        ops=(op_injury_cure("cure", playerids=ids),),
        label="Cure injured players" if not ids else f"Cure {len(ids)} injured",
        origin="ui.injury.cure",
        require_cm=True,
    )
    return submit_job(svc, job, on_result=on_result)
