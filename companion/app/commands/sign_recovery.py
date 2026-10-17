"""Explicit recovery for a Sign job FC refused because the squad was full.

Before this module the app auto-queued a "squad-full relink" job whenever a
Sign job failed with ``squad_full`` — and because that relink job is itself a
Sign job, each refusal queued the next one (six failed Messi signings on
2026-10-15). Nothing here runs on its own any more:

* ``squad_full_message`` explains the refusal with the worker's own counts
  ("Squad full: 42 + 10 on loan = 52/52. ...");
* ``retry_signing`` is called only when the user presses Retry;
* ``restore_plan`` / ``queue_restore_free_agent`` build a reviewed
  ``restore_partial_add`` job for a signing that wrote a free agent's stats
  but never transferred him, using the pre-write values the worker captured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from ...domain.job import (
    Grants, Job, JobValidationError, is_sign_job_label, op_add_to_team,
    op_restore_partial_add,
)
from ...domain.outcome import ApplyOutcome, JobResult
from ..services import Services
from .apply import submit_job

_RETRIED_PREF = "sign.retried_job_ids"
_RETRIED_KEEP = 100
# Oldest worker that can move an already-written free agent (relink_only).
_RELINK_CORE = "2.6.32"
_ROOM_RE = re.compile(r"(\d+) senior \+ (\d+) on loan = (\d+) of (\d+)")


@dataclass(frozen=True, slots=True)
class SquadRoom:
    present: int
    loaned: int
    total: int
    limit: int
    needed: int = 1

    @property
    def free(self) -> int:
        return self.limit - self.total


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """What a Restore would write, shown to the user before queueing."""

    source_job_id: str
    members: tuple[dict[str, Any], ...]
    names: tuple[str, ...]

    @property
    def field_count(self) -> int:
        return sum(len(member["restore"]) for member in self.members)


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sign_progress(result: JobResult) -> Mapping[str, Any]:
    """The add_to_team progress a failed worker result carries (data.progress)."""
    for op in result.ops:
        if op.op == "add_to_team":
            progress = (op.data or {}).get("progress")
            if isinstance(progress, Mapping):
                return progress
    raw_ops = (result.raw or {}).get("ops") or ()
    for raw in raw_ops:
        if isinstance(raw, Mapping) and raw.get("op") in (None, "add_to_team"):
            progress = (raw.get("data") or {}).get("progress") if isinstance(raw.get("data"), Mapping) else None
            if isinstance(progress, Mapping):
                return progress
    return {}


def is_squad_full_refusal(result: JobResult | None) -> bool:
    if result is None or not result.outcome.is_terminal or result.outcome is ApplyOutcome.APPLIED:
        return False
    for failure in result.failures:
        reason = str(failure.reason or "")
        if reason == "squad_full":
            return True
        if reason == "team_not_applied" and "111592" in str(failure.detail or ""):
            return True
    return False


def squad_room_from_result(result: JobResult | None) -> SquadRoom | None:
    """The worker's squad count: structured progress first, detail text second."""
    if result is None:
        return None
    room = _sign_progress(result).get("squad_room")
    if isinstance(room, Mapping) and room.get("readable") is True:
        values = [_int(room.get(key)) for key in ("present", "loaned", "total", "limit")]
        if None not in values:
            present, loaned, total, limit = values  # type: ignore[misc]
            return SquadRoom(present, loaned, total, limit, max(1, _int(room.get("needed")) or 1))
    for failure in result.failures:
        match = _ROOM_RE.search(str(failure.detail or ""))
        if match:
            present, loaned, total, limit = (int(part) for part in match.groups())
            needed = 1
            batch = re.search(r"room for \d+ of (\d+) players", str(failure.detail))
            if batch:
                needed = int(batch.group(1))
            return SquadRoom(present, loaned, total, limit, needed)
    return None


def written_untransferred_members(result: JobResult | None) -> tuple[Mapping[str, Any], ...]:
    """Dummies this signing wrote a card onto that never joined the club."""
    if result is None:
        return ()
    progress = _sign_progress(result)
    members = progress.get("members")
    out: list[Mapping[str, Any]] = []
    if isinstance(members, (list, tuple)):
        for member in members:
            if not isinstance(member, Mapping):
                continue
            pid = _int(member.get("playerid"))
            if (pid and pid > 0 and member.get("fields_written") is True
                    and not member.get("relink_only")
                    and not member.get("transferred") and not member.get("team_verified")):
                out.append(member)
        return tuple(out)
    # v3 single-player progress keeps the same keys at the top level.
    pid = _int(progress.get("playerid"))
    if pid and pid > 0 and progress.get("written_values") and not progress.get("verify_source"):
        out.append(progress)
    return tuple(out)


def _restorable(member: Mapping[str, Any]) -> bool:
    written = member.get("written_values")
    original = member.get("original_values")
    if not isinstance(written, Mapping) or not written or not isinstance(original, Mapping):
        return False
    return all(key in original for key in written if key != "playerid")


def has_restorable_members(result: JobResult | None) -> bool:
    members = written_untransferred_members(result)
    return bool(members) and all(_restorable(member) for member in members)


def squad_full_message(result: JobResult | None) -> str:
    """Plain words for the Sign handoff; empty when this is not a squad refusal."""
    if not is_squad_full_refusal(result):
        return ""
    room = squad_room_from_result(result)
    if room is None:
        text = "Squad full: FC refused the transfer. Release or loan out a player, then press Retry."
    elif room.needed > 1 and room.free > 0:
        missing = room.needed - room.free
        text = (
            f"Squad has room for {room.free} of {room.needed} players: "
            f"{room.present} + {room.loaned} on loan = {room.total}/{room.limit}. "
            f"Release or loan out {missing} player{'s' if missing != 1 else ''}, then press Retry."
        )
    else:
        missing = max(1, room.needed - max(0, room.free))
        who = "a player" if missing == 1 else f"{missing} players"
        text = (
            f"Squad full: {room.present} + {room.loaned} on loan = {room.total}/{room.limit}. "
            f"Release or loan out {who}, then press Retry."
        )
    written = written_untransferred_members(result)
    if written:
        if has_restorable_members(result):
            text += (" The free agent already has the new stats: Retry finishes this signing, "
                     "or Restore free agent puts its original values back.")
        else:
            text += (" The free agent already has the new stats; this signing ran on an older "
                     "worker that did not record its original values, so it cannot be restored "
                     "automatically.")
    return text


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def _record_and_result(svc: Services, job_id: str) -> tuple[Any, JobResult]:
    if svc.db is None:
        raise JobValidationError("Signing history is unavailable.")
    record = svc.db.job_record(job_id)
    if record is None:
        raise JobValidationError("This signing is no longer in the activity history.")
    result = None
    transport = getattr(svc, "transport", None)
    if transport is not None:
        result = transport.result(job_id)
    if (result is None or not result.outcome.is_terminal) and isinstance(record.result_json, Mapping) \
            and record.result_json:
        result = JobResult.from_wire(record.result_json)
    if result is None or not result.outcome.is_terminal:
        raise JobValidationError("This signing has not finished yet.")
    return record, result


def _source_op(record: Any) -> Mapping[str, Any]:
    job_json = record.job_json if isinstance(record.job_json, Mapping) else {}
    ops = job_json.get("ops")
    op = ops[0] if isinstance(ops, list) and ops else {}
    if not isinstance(op, Mapping) or op.get("op") != "add_to_team":
        raise JobValidationError("Only a Sign job can be retried.")
    return op


def _source_members(op: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    batch = op.get("batch")
    if isinstance(batch, list) and batch:
        members = [member for member in batch if isinstance(member, Mapping)]
        if len(members) == len(batch):
            return members
        raise JobValidationError("The original signing request is incomplete.")
    if isinstance(op.get("player"), Mapping):
        return [{key: op.get(key) for key in ("dummy_pool", "dummy_filter", "player", "contract")}]
    raise JobValidationError("The original signing request is incomplete.")


def _retried(svc: Services) -> list[str]:
    raw = svc.db.get_pref(_RETRIED_PREF, []) if svc.db is not None else []
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _restored_ok(svc: Services, job_id: str) -> bool:
    if svc.db is None:
        return False
    for record in svc.db.job_records(op="restore_partial_add", limit=None):
        ops = (record.job_json or {}).get("ops") or ()
        if not ops or not isinstance(ops[0], Mapping) or ops[0].get("source_job_id") != job_id:
            continue
        if record.outcome is ApplyOutcome.APPLIED:
            return True
    return False


def retry_signing(svc: Services, job_id: str) -> str:
    """Queue the same reviewed signing again. Only ever called from a button.

    * A precheck refusal wrote nothing: the same cards go to the same reviewed
      free agents (those still verified live), and the worker counts the squad
      again before any write.
    * An older partial already wrote the card onto the free agent: Retry moves
      that exact free agent onto the club (relink), unless it was restored.
    """
    from .. import events as E
    from .team import (
        _add_job_envelope, _dummy_filter_for, _validate_team_add,
        _verified_free_agent_rows, active_team_add_jobs,
    )
    from ... import core_at_least

    record, result = _record_and_result(svc, job_id)
    if not is_squad_full_refusal(result):
        raise JobValidationError("Retry is only offered for a signing refused because the squad was full.")
    source = _source_op(record)
    state = _validate_team_add(svc)
    save_uid = str(state.squad.save_uid or "")
    if record.save_uid and save_uid and record.save_uid != save_uid:
        raise JobValidationError("This signing belongs to another Career save.")
    if _int(source.get("teamid")) not in (None, int(state.squad.teamid or 0)):
        raise JobValidationError("This signing was for another club.")
    if job_id in _retried(svc):
        raise JobValidationError("This signing was already retried. Check Activity for the new attempt.")
    if active_team_add_jobs(svc):
        raise JobValidationError("Another signing is still running. Wait for it to finish, then press Retry.")
    members = _source_members(source)
    # The refusal's squad count is history; the worker counts again before
    # writing, so a still-full squad is refused again with zero writes.
    written = written_untransferred_members(result)
    if written and not _restored_ok(svc, job_id):
        live = state.bridge.liveness
        if not core_at_least(str(getattr(live, "core_version", "") or ""), _RELINK_CORE):
            raise JobValidationError("Restart Live Editor with the current worker before retrying.")
        progress_members = _sign_progress(result).get("members")
        all_ids = []
        if isinstance(progress_members, list):
            all_ids = [_int((m or {}).get("playerid")) for m in progress_members if isinstance(m, Mapping)]
        if len(all_ids) != len(members) or None in all_ids or len(set(all_ids)) != len(all_ids):
            raise JobValidationError("The original free agents could not be identified; review the signing again.")
        batch = [
            {
                "relink_only": True,
                "playerid": pid,
                "dummy_pool": [pid],
                "dummy_filter": {},
                "player": dict(member.get("player") or {}),
                "contract": dict(member.get("contract") or {}),
            }
            for pid, member in zip(all_ids, members, strict=True)
        ]
        origin = "ui.sign.retry_relink"
    else:
        free = {int(row["playerid"]): row for row in _verified_free_agent_rows(svc)}
        used: set[int] = set()
        batch = []
        for member in members:
            name = str(((member.get("player") or {}).get("names") or {}).get("commonname") or "this player")
            pool = [pid for pid in (_int(x) for x in member.get("dummy_pool") or ())
                    if pid is not None and pid in free and pid not in used]
            if not pool:
                raise JobValidationError(
                    f"The reviewed free agent for {name} is no longer available. "
                    "Review the signing again from Sign."
                )
            used.update(pool)
            body = dict(member)
            body["dummy_pool"] = pool
            body["dummy_filter"] = _dummy_filter_for(free[pool[0]])
            batch.append(body)
        origin = "ui.sign.retry"
    op = op_add_to_team(
        "add",
        teamid=int(state.squad.teamid),
        batch=tuple(batch),
        verify={"name": True, "team": True, "fields": True},
        experimental=True,
    )
    label = str((record.job_json or {}).get("label") or record.label or "")
    if not is_sign_job_label(label):
        label = f"Sign {len(batch)}: retry" if len(batch) > 1 else "Sign: retry"
    job = replace(_add_job_envelope(state, op, label), origin=origin)
    if origin == "ui.sign.retry_relink":
        job = replace(job, requires={
            "core_version": _RELINK_CORE,
            **({"save_uid": save_uid} if save_uid else {}),
        })
    new_id = submit_job(svc, job)
    done = _retried(svc)
    done.append(job_id)
    svc.db.set_pref(_RETRIED_PREF, done[-_RETRIED_KEEP:])
    svc.store.dispatch(E.SigningBatchQueued(
        job_ids=(new_id,),
        selected_count=len(batch),
        save_uid=save_uid,
        teamid=int(state.squad.teamid),
        session_id=str(getattr(state.bridge.liveness, "session_id", "") or ""),
        at=svc.clock.now(),
    ))
    if svc.log is not None:
        svc.log.info("user retried signing %s as %s (%s)", job_id, new_id, origin)
    return new_id


# ---------------------------------------------------------------------------
# Restore free agent
# ---------------------------------------------------------------------------


def _member_names(record: Any, count: int) -> list[str]:
    try:
        members = _source_members(_source_op(record))
    except JobValidationError:
        members = []
    names: list[str] = []
    for index in range(count):
        member = members[index] if index < len(members) else {}
        player = member.get("player") if isinstance(member, Mapping) else None
        player_names = (player or {}).get("names") if isinstance(player, Mapping) else None
        name = str((player_names or {}).get("commonname") or "").strip() if isinstance(player_names, Mapping) else ""
        names.append(name or "Player")
    return names


def restore_plan(svc: Services, job_id: str) -> RestorePlan:
    """Validate a Restore and return exactly what it would write, or explain why not."""
    record, result = _record_and_result(svc, job_id)
    _source_op(record)
    if result.outcome is ApplyOutcome.APPLIED:
        raise JobValidationError("This signing completed; there is no free agent to restore.")
    state = svc.store.snapshot()
    save_uid = str(state.squad.save_uid or "")
    if not save_uid:
        raise JobValidationError("Refresh squad first so Companion knows the current Career save.")
    record_save = str(record.save_uid or ((record.job_json or {}).get("requires") or {}).get("save_uid") or "")
    if record_save and record_save != save_uid:
        raise JobValidationError("This signing belongs to another Career save; load that save to restore it.")
    progress_members = _sign_progress(result).get("members")
    written = written_untransferred_members(result)
    if not written:
        raise JobValidationError("This signing did not leave a changed free agent behind.")
    if not all(_restorable(member) for member in written):
        raise JobValidationError(
            "This signing ran on an older worker that did not record the free agent's original "
            "values, so Companion cannot restore it automatically."
        )
    roster = set()
    for player in state.squad.players:
        pid = _int(player.get("playerid") or player.get("id")) if isinstance(player, Mapping) else None
        if pid:
            roster.add(pid)
    if _restored_ok(svc, job_id):
        raise JobValidationError("This free agent was already restored.")
    for view in state.jobs.active.values():
        if not view.done and str(view.label or "").startswith("Restore free agent"):
            raise JobValidationError("A restore is already running. Wait for it to finish.")
    all_names = _member_names(record, len(progress_members) if isinstance(progress_members, list) else 1)
    members: list[dict[str, Any]] = []
    names: list[str] = []
    for member in written:
        pid = int(member["playerid"])
        if pid in roster:
            raise JobValidationError(
                f"Player {pid} is on your club now; Restore only works for a free agent."
            )
        written_values = {k: v for k, v in dict(member["written_values"]).items() if k != "playerid"}
        original = dict(member["original_values"])
        members.append({
            "playerid": pid,
            "expected": written_values,
            "restore": {key: original[key] for key in written_values},
        })
        index = 0
        if isinstance(progress_members, list):
            for position, candidate in enumerate(progress_members):
                if isinstance(candidate, Mapping) and _int(candidate.get("playerid")) == pid:
                    index = position
                    break
        names.append(all_names[index] if index < len(all_names) else "Player")
    return RestorePlan(job_id, tuple(members), tuple(names))


def restore_review_lines(plan: RestorePlan, *, limit: int = 8) -> list[str]:
    """Human-readable diff: field, value now (written by the signing) -> original."""
    lines: list[str] = []
    for member, name in zip(plan.members, plan.names, strict=True):
        lines.append(f"Free agent {member['playerid']} (had {name}'s card):")
        for shown, field in enumerate(sorted(member["restore"])):
            if shown >= limit:
                lines.append(f"  … and {len(member['restore']) - limit} more fields")
                break
            lines.append(f"  {field}: {member['expected'][field]} → {member['restore'][field]}")
    return lines


def build_restore_job(svc: Services, plan: RestorePlan) -> Job:
    state = svc.store.snapshot()
    if not state.prefs.experimental_add_to_team:
        raise JobValidationError("Sign support is off. Enable Sign support to restore a free agent.")
    if not state.bridge.armed:
        raise JobValidationError("Start FC 26 through Live Editor and load Career Mode.")
    caps = state.bridge.liveness.capabilities or {}
    if not caps.get("restore_partial_add"):
        raise JobValidationError("Restart Live Editor with the updated worker to restore free agents.")
    label = "Restore free agent: " + ", ".join(plan.names)
    return Job(
        ops=(op_restore_partial_add("restore", source_job_id=plan.source_job_id,
                                    members=plan.members),),
        label=label[:180],
        origin="ui.sign.restore",
        grants=Grants(allow_add_to_team=True),
        requires={"save_uid": str(state.squad.save_uid)},
        # One attempt: the worker refuses a second run anyway (values changed).
        max_attempts=1,
        budget_ms=2000,
    )


def queue_restore_free_agent(svc: Services, plan: RestorePlan) -> str:
    return submit_job(svc, build_restore_job(svc, plan))


def recovery_for_results(results: Sequence[tuple[str, JobResult | None]]) -> tuple[str, str, bool]:
    """(job_id, message, restorable) for the first squad-full refusal, else empties."""
    for job_id, result in results:
        message = squad_full_message(result)
        if message:
            return job_id, message, has_restorable_members(result)
    return "", "", False


__all__ = [
    "RestorePlan",
    "SquadRoom",
    "build_restore_job",
    "has_restorable_members",
    "is_squad_full_refusal",
    "queue_restore_free_agent",
    "recovery_for_results",
    "restore_plan",
    "restore_review_lines",
    "retry_signing",
    "squad_full_message",
    "squad_room_from_result",
    "written_untransferred_members",
]
