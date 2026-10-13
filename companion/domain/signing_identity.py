"""Chosen signing names keyed by the worker's actual Career player IDs."""
from __future__ import annotations

from typing import Any, Mapping

from .outcome import ApplyOutcome, JobResult


def _positive_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 and str(number) == str(value) else None


def _display_name(player: Mapping[str, Any]) -> str:
    names = player.get("names")
    if not isinstance(names, Mapping):
        return ""
    common = str(names.get("commonname") or "").strip()
    full = " ".join(str(names.get(key) or "").strip()
                    for key in ("firstname", "surname")).strip()
    return common or full or str(names.get("playerjerseyname") or "").strip()


def signed_player_identities(
    job: Mapping[str, Any], result: JobResult, *, save_uid: str,
) -> dict[int, dict[str, Any]]:
    """Use verified receipts, never Library IDs, labels, or the planned dummy.

    Batch result IDs follow request member order; a worker may have used a
    backup dummy. Old sessions are valid in the same save after app restarts.
    """
    if not save_uid or job.get("dry_run") or not result.outcome.is_terminal:
        return {}
    requires = job.get("requires") or {}
    if not isinstance(requires, Mapping):
        return {}
    if requires.get("save_uid") not in (None, "", save_uid):
        return {}
    if result.env.get("save_uid") not in (None, "", save_uid):
        return {}
    if (requires.get("session_id") and result.session_id
            and requires["session_id"] != result.session_id):
        return {}
    requests = job.get("ops") or ()
    if not isinstance(requests, (list, tuple)):
        return {}
    resolved: dict[int, dict[str, Any]] = {}
    for receipt in result.ops:
        if receipt.op != "add_to_team" or receipt.outcome is not ApplyOutcome.APPLIED:
            continue
        matches = [op for op in requests if isinstance(op, Mapping)
                   and op.get("op") == "add_to_team" and op.get("id") == receipt.op_id]
        if len(matches) != 1:
            continue
        request, data = matches[0], receipt.data
        teamid = _positive_id(data.get("teamid"))
        if (request.get("dry_run") or data.get("verified") is not True
                or teamid is None or teamid != _positive_id(request.get("teamid"))):
            continue
        members = request.get("batch") or (request,)
        raw_ids = data.get("playerids") or (data.get("playerid"),)
        if not isinstance(members, (list, tuple)) or not isinstance(raw_ids, (list, tuple)):
            continue
        ids = [_positive_id(value) for value in raw_ids]
        if len(ids) != len(members) or None in ids or len(set(ids)) != len(ids):
            continue
        names: dict[int, dict[str, Any]] = {}
        for pid, member in zip(ids, members):
            if not isinstance(member, Mapping):
                break
            pool = member.get("dummy_pool") or ()
            if pool and (not isinstance(pool, (list, tuple))
                         or pid not in {_positive_id(value) for value in pool}):
                break
            player = member.get("player")
            if not isinstance(player, Mapping):
                break
            name = _display_name(player)
            if not name:
                break
            source = _positive_id(player.get("source_playerid"))
            names[pid] = {"name": name, "source_playerid": source,
                          "player": dict(player), "job_id": result.job_id}
        else:
            resolved.update(names)
    return resolved


def signed_player_names(job: Mapping[str, Any], result: JobResult, *, save_uid: str) -> dict[int, str]:
    return {pid: identity["name"] for pid, identity in
            signed_player_identities(job, result, save_uid=save_uid).items()}
