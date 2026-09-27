"""Sync live save state — the only source is the running game.

Modelled as a **cache with a save-scoped key and a short TTL**, never durable
data. v1's ``current_squad.json`` had no save key at all, so it would happily
show save A's squad while you edited save B.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ...domain.job import Job, op_export_squad
from ...domain.safe_dummy_candidates import REAL_FREE_AGENT_CANDIDATE_IDS
from ...domain.outcome import JobResult
from .. import events as E
from ..services import Services
from .apply import submit_job

# How long a squad snapshot is trusted before the UI calls it stale.
TTL_SECONDS = 120.0
# A valid-record pass over FC26's ~22k player rows takes about 3.8 seconds on
# the real Live Editor host. The generic 200ms mutation budget incorrectly
# discarded a complete 37-player Barcelona result as ``budget_overrun``.
SQUAD_READ_BUDGET_MS = 5000
# Automatic retries after a failed/empty read. Career-not-ready does not
# consume this; the coordinator simply waits for the next poll.
RETRY_BASE_SECONDS = 2.0
RETRY_MAX_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class SquadSyncTicket:
    """What ``sync_squad`` actually did. The job id alone cannot say which."""

    job_id: str
    created: bool


def sync_status_text(ticket: SquadSyncTicket) -> str:
    """User-facing line for a manual refresh."""
    if ticket.created:
        return (
            f"Squad sync queued ({ticket.job_id[:8]}…) — "
            "keep Career Mode open."
        )
    return "Squad sync already in progress."


def ensure_squad_fresh(svc: Services) -> str | None:
    """Queue a squad read when the cache is missing, stale, or for another save.

    Returns the new job id, or None when this poll should wait. A previous
    failure does not permanently suppress later polls; it only sets a cooldown.
    Success is recorded later, inside ``_ingest``, when ``SquadSynced`` fires.
    """
    live = svc.store.snapshot().bridge.liveness
    if not live.armed or not live.session_id:
        _trace_skip(svc, "not_armed" if not live.armed else "no_session")
        return None
    caps = dict(live.capabilities or {})
    # IsInCM is true on the career menus, before the save can be read.
    # hub_ready is set only after the career hub is open.
    if not caps.get("hub_ready"):
        _trace_skip(svc, "hub_not_ready")
        return None
    if _sync_in_flight(svc):
        _trace_skip(svc, "job_active")
        return None
    if _other_jobs_in_flight(svc):
        _trace_skip(svc, "other_job_active")
        return None
    memory = svc.squad_sync
    now = svc.clock.now_ts()
    if memory.retry_after_ts is not None and now < memory.retry_after_ts:
        wait = max(0.0, memory.retry_after_ts - now)
        _trace_skip(svc, f"retry_cooldown retry_in={wait:.0f}")
        return None
    if not is_stale(svc):
        memory.failures = 0
        memory.retry_after_ts = None
        memory.last_error = ""
        _trace_skip(svc, "fresh")
        return None
    reason = _queue_reason(svc)
    try:
        ticket = sync_squad(svc)
    except Exception:
        _note_squad_result(svc, job_id="", ok=False, reason="could not queue squad read")
        raise
    if not ticket.created:
        _trace_skip(svc, "job_active")
        return None
    from .sync_trace import sync_log

    sync_log("SYNC", f"queue reason={reason} job={ticket.job_id[:8]}")
    svc.store.dispatch(
        E.StatusSet("Connected to Live Editor — reading your Career Mode squad…")
    )
    return ticket.job_id


def _hub_ready(svc: Services) -> bool:
    """True when this session may read the career squad.

    An empty capability map is a test or offline host. A live worker must
    advertise ``hub_ready``; ``in_career`` and ``save_ready`` are set too early
    and queuing a read then crashes FC 26.
    """
    caps = dict(svc.store.snapshot().bridge.liveness.capabilities or {})
    if "hub_ready" not in caps and "save_ready" not in caps and "in_career" not in caps:
        return True
    return bool(caps.get("hub_ready"))


def _sync_in_flight(svc: Services) -> bool:
    return any(
        view.label == "Sync squad" and not view.done
        for view in svc.store.snapshot().jobs.active.values()
    )


def _other_jobs_in_flight(svc: Services) -> bool:
    """Hold an automatic re-read while a write or scan is still in the queue."""
    return any(
        view.label != "Sync squad" and not view.done
        for view in svc.store.snapshot().jobs.active.values()
    )


def _queue_reason(svc: Services) -> str:
    squad = svc.store.snapshot().squad
    live = svc.store.snapshot().bridge.liveness
    live_save = getattr(live, "save_uid", "") or ""
    if not squad.players or squad.taken is None:
        return "squad_missing"
    if live_save and squad.save_uid and live_save != squad.save_uid:
        return "save_changed"
    if live.session_id and squad.session_id != live.session_id:
        return "session_changed"
    if squad.stale:
        return "stale"
    return "ttl"


def _trace_skip(svc: Services, reason: str) -> None:
    from .sync_trace import sync_log

    state = svc.store.snapshot()
    squad = state.squad
    live = state.bridge.liveness
    hub = bool((live.capabilities or {}).get("hub_ready"))
    sync_log(
        "SYNC",
        "skip reason={reason} session_id={sid} save_uid={uid} hub_ready={hub} "
        "armed={armed} teamid={team} players={count} stale={stale} "
        "failures={fails}".format(
            reason=reason,
            sid=live.session_id or "-",
            uid=getattr(live, "save_uid", "") or "-",
            hub=hub,
            armed=live.armed,
            team=squad.teamid if squad.teamid is not None else "-",
            count=len(squad.players),
            stale=squad.stale,
            fails=svc.squad_sync.failures,
        ),
    )


def _note_squad_result(
    svc: Services, *, job_id: str, ok: bool, reason: str = ""
) -> None:
    """Record success or schedule a bounded retry. Empty job_id always counts."""
    memory = svc.squad_sync
    if job_id and job_id in memory.counted_jobs:
        return
    if job_id:
        memory.counted_jobs.add(job_id)
    if ok:
        memory.failures = 0
        memory.retry_after_ts = None
        memory.last_error = ""
        from .sync_trace import sync_log

        sync_log("SYNC", "retry_cleared failures=0")
        return
    memory.failures += 1
    delay = min(
        RETRY_MAX_SECONDS,
        RETRY_BASE_SECONDS * (2 ** (memory.failures - 1)),
    )
    memory.retry_after_ts = svc.clock.now_ts() + delay
    memory.last_error = reason
    from .sync_trace import sync_log

    sync_log(
        "SYNC",
        f"failure reason={reason or 'unknown'} failures={memory.failures} retry_in={delay:.0f}",
    )


def sync_squad(svc: Services, *, teamid: int | None = None) -> SquadSyncTicket:
    """Ask the game for the user squad. Result arrives as a SquadSynced event.

    A genuinely active Sync squad job is reused. Callers must read ``created``
    so they do not tell the user a new read was queued.
    """
    if not _hub_ready(svc):
        raise RuntimeError(
            "Open the career hub before reading the squad."
        )
    for job_id, view in svc.store.snapshot().jobs.active.items():
        if view.label == "Sync squad" and not view.done:
            return SquadSyncTicket(job_id=job_id, created=False)
    job = Job(
        ops=(
            op_export_squad(
                "squad",
                teamid=teamid,
                # A bounded probe of known real FC26 free agents.  Lua still
                # verifies every candidate against the currently loaded save.
                free_agent_candidates=REAL_FREE_AGENT_CANDIDATE_IDS,
            ),
        ),
        label="Sync squad",
        origin="ui.club.sync",
        require_cm=True,
        budget_ms=SQUAD_READ_BUDGET_MS,
    )
    job_id = submit_job(svc, job, on_result=lambda r: _ingest(svc, r))
    return SquadSyncTicket(job_id=job_id, created=True)


def is_export_result(svc: Services, result: JobResult) -> bool:
    """True for an export result, including a pre-op rejection after restart."""
    if any(op.op == "export_squad" for op in result.ops):
        return True
    if svc.db is None:
        return False
    try:
        rec = svc.db.job_record(result.job_id)
        raw = rec.job_json if rec is not None else None
        return isinstance(raw, Mapping) and any(
            isinstance(op, Mapping) and op.get("op") == "export_squad"
            for op in (raw.get("ops") or ())
        )
    except Exception:
        return False


def ingest_result(svc: Services, result: JobResult) -> bool:
    """Ingest export data; safe for the generic late-result collector."""
    if not is_export_result(svc, result):
        return False
    _ingest(svc, result)
    return True


def _failure_detail(result: JobResult) -> str:
    if result.failures:
        failure = result.failures[0]
        return failure.detail or failure.reason
    if result.error:
        return str(result.error.get("detail") or result.error.get("message") or "")
    return result.diagnostic


def _ingest(svc: Services, result: JobResult) -> None:
    live = svc.store.snapshot().bridge.liveness
    if not result.outcome.is_terminal:
        # Follow hands back QUEUED/DEFERRED at the 45s mark. The job is still
        # valid; cooldown and the error toast wait for a real terminal result.
        return
    if live.session_id and result.session_id and result.session_id != live.session_id:
        # Not a failure of the current session. Do not start its cooldown.
        svc.store.dispatch(
            E.StatusSet(
                "Ignored a squad result from an older Live Editor session. "
                "Waiting for the active Career save."
            )
        )
        return
    if not result.outcome.is_success:
        detail = _failure_detail(result)
        suffix = f": {detail}" if detail else ""
        _note_squad_result(svc, job_id=result.job_id, ok=False, reason=detail)
        svc.store.dispatch(
            E.StatusSet(
                "Could not read the live squad"
                f"{suffix}. Load a Career Mode save, then try Refresh squad."
            )
        )
        return
    players: Sequence[Mapping[str, Any]] = ()
    free_agents: Sequence[Mapping[str, Any]] = ()
    teamid: int | None = None
    for op in result.ops:
        if op.op == "export_squad":
            data = dict(op.data)
            raw = data.get("players")
            if isinstance(raw, list):
                players = [p for p in raw if isinstance(p, Mapping)]
            raw_free = data.get("free_agents")
            if isinstance(raw_free, list):
                free_agents = [
                    p for p in raw_free if isinstance(p, Mapping)
                ]
            tid = data.get("teamid")
            teamid = int(tid) if isinstance(tid, (int, float)) else None
    if not players:
        _note_squad_result(
            svc,
            job_id=result.job_id,
            ok=False,
            reason="no squad players",
        )
        svc.store.dispatch(
            E.StatusSet(
                "The worker returned no squad players. Keep Career Mode loaded "
                "and try Refresh squad; no cached squad was replaced."
            )
        )
        return
    save_uid = str(result.env.get("save_uid", ""))
    if teamid is None:
        env_tid = result.env.get("user_teamid")
        teamid = int(env_tid) if isinstance(env_tid, (int, float)) else None
    svc.store.dispatch(
        E.SquadSynced(
            save_uid=save_uid,
            session_id=result.session_id or live.session_id,
            teamid=teamid,
            players=players,
            taken=svc.clock.now(),
            free_agents=free_agents,
        )
    )
    _note_squad_result(svc, job_id=result.job_id, ok=True)
    from .sync_trace import sync_log

    sync_log(
        "SQUAD",
        f"success players={len(players)} session_id={result.session_id or live.session_id or '-'} "
        f"save_uid={save_uid or '-'} teamid={teamid if teamid is not None else '-'}",
    )
    if svc.db is not None:
        svc.db.put_save_snapshot(
            save_uid=save_uid,
            kind="squad",
            payload={
                "session_id": result.session_id or live.session_id,
                "teamid": teamid,
                "players": list(players),
                "free_agents": list(free_agents),
            },
            taken_ts=svc.clock.now_ts(),
            taken_utc=svc.clock.stamp(),
            stale=False,
        )
    _reconcile_target(svc, players, teamid)
    svc.store.dispatch(
        E.StatusSet(f"Squad ready — {len(players)} live player(s) read.")
    )


def _reconcile_target(
    svc: Services,
    players: Sequence[Mapping[str, Any]],
    teamid: int | None,
) -> None:
    """Refresh a squad target, or clear it when a new live squad replaces it."""
    target = svc.store.snapshot().target
    if not target.locked or target.playerid is None:
        return
    match: Mapping[str, Any] | None = None
    for player in players:
        raw_id = player.get("playerid", player.get("id"))
        try:
            if int(raw_id) == target.playerid:
                match = player
                break
        except (TypeError, ValueError):
            continue
    if match is not None:
        name = str(match.get("name") or match.get("playername") or target.name)
        svc.store.dispatch(
            E.TargetLocked(
                playerid=target.playerid,
                name=name,
                source=target.source,
                teamid=teamid,
            )
        )
    elif target.source == "squad":
        svc.store.dispatch(E.TargetCleared())


def invalidate(svc: Services, reason: str = "") -> None:
    """Any successful write job invalidates the squad view.

    The UI renders staleness rather than silently showing an old number — a
    value you did not just read is never presented as current.
    """
    svc.store.dispatch(E.SquadInvalidated(reason=reason))
    if svc.db is not None:
        uid = svc.store.snapshot().squad.save_uid or None
        svc.db.invalidate_save_snapshots(save_uid=uid, kinds=("squad",))


def is_stale(svc: Services) -> bool:
    sq = svc.store.snapshot().squad
    live = svc.store.snapshot().bridge.liveness
    if live.session_id and sq.session_id != live.session_id:
        return True
    live_save = getattr(live, "save_uid", "") or ""
    if live_save and sq.save_uid and live_save != sq.save_uid:
        return True
    if sq.stale or sq.taken is None:
        return True
    age = sq.age_seconds(svc.clock.now())
    return age is None or age > TTL_SECONDS
