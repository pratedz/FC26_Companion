"""Presenters: AppState -> plain view dicts. The ONLY ``ApplyOutcome`` match sites.

Keeping every match here means adding an enum member breaks compilation in one
file rather than silently rendering as "unknown" in eight tabs. Views consume
these dicts and know nothing about the domain.

The honesty rules this file enforces, all of which v1 violated:
  - ``NO_OP`` is a *distinct, visible* outcome, never a green checkmark.
  - A timeout is not a failure — it renders as "still queued", with what to do.
  - ``ARMED`` with an empty queue is a success state, not OFF.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..core.transport.v3 import Liveness, Pill
from ..domain.automation_guide import map_job_failure
from ..domain.outcome import ApplyOutcome, JobResult
from .state import AppState

# Semantic tone names; the theme maps them to colours. No hex codes here.
TONE_OK = "ok"
TONE_WARN = "warn"
TONE_ERROR = "error"
TONE_INFO = "info"
TONE_MUTED = "muted"


def outcome_view(outcome: ApplyOutcome) -> dict[str, Any]:
    """The one exhaustive match. Every branch decides tone + whether to nag."""
    match outcome:
        case ApplyOutcome.APPLIED:
            return {"label": "Applied", "tone": TONE_OK, "icon": "check",
                    "detail": "Every field written and verified."}
        case ApplyOutcome.NO_OP:
            return {"label": "No change", "tone": TONE_WARN, "icon": "minus",
                    "detail": "Ran cleanly but nothing changed — the target was not found."}
        case ApplyOutcome.PARTIAL:
            return {"label": "Partial", "tone": TONE_WARN, "icon": "alert",
                    "detail": "Some fields did not apply. Open the report for which."}
        case ApplyOutcome.FAILED:
            return {"label": "Failed", "tone": TONE_ERROR, "icon": "x",
                    "detail": "The job errored inside the game."}
        case ApplyOutcome.REJECTED:
            return {"label": "Rejected", "tone": TONE_ERROR, "icon": "shield",
                    "detail": "Refused before running — nothing was written."}
        case ApplyOutcome.CRASHED:
            return {"label": "Crashed", "tone": TONE_ERROR, "icon": "skull",
                    "detail": "The game or LE closed while this job was running."}
        case ApplyOutcome.EXPIRED:
            return {"label": "Expired", "tone": TONE_MUTED, "icon": "clock",
                    "detail": "No game activity before the deadline. Re-queue it."}
        case ApplyOutcome.DEFERRED:
            return {"label": "Working…", "tone": TONE_INFO, "icon": "spinner",
                    "detail": "Running across several game events."}
        case ApplyOutcome.QUEUED:
            return {"label": "Queued", "tone": TONE_INFO, "icon": "clock",
                    "detail": "Waiting for the game to tick."}
    raise AssertionError(f"unhandled outcome {outcome!r}")  # pragma: no cover


def liveness_view(lv: Liveness) -> dict[str, Any]:
    """The §3.5 status matrix, rendered. ``ARMED`` + empty queue is success."""
    tone = {
        Pill.OFF: TONE_MUTED,
        Pill.ARMED: TONE_OK,
        Pill.LIVE: TONE_OK,
        Pill.WAITING: TONE_WARN,
        Pill.STALLED: TONE_ERROR,
    }[lv.pill]
    return {
        "pill": lv.pill.value.upper(),
        "tone": tone,
        "message": lv.message,
        "armed": lv.armed,
        "queue_depth": lv.queue_depth,
        "core_version": lv.core_version,
        "show_force_drain": lv.pill in (Pill.WAITING, Pill.STALLED),
        "age_text": _age_text(lv.last_drain_age),
    }


def _age_text(age: float | None) -> str:
    if age is None:
        return ""
    if age < 5:
        return "just now"
    if age < 90:
        return f"{int(age)}s ago"
    if age < 5400:
        return f"{int(age // 60)} min ago"
    return f"{int(age // 3600)} h ago"


def job_report(result: JobResult) -> dict[str, Any]:
    """The apply report: what happened, per op and per failed field.

    v1 could not produce this at all — the job id was lost to ``_run_now.lua``
    and every recent history row had an empty ``last_result``.
    """
    view = outcome_view(result.outcome)
    counts = dict(result.counts)
    failures = [
        {
            "op": f.op_id,
            "field": f.field,
            "target": f.target,
            "reason": f.reason,
            "detail": f.detail,
        }
        for f in result.failures
    ]
    return {
        "job_id": result.job_id,
        "label": result.label,
        "outcome": view,
        "counts": counts,
        "summary": _summary(result, counts),
        "ops": [
            {
                "id": o.op_id,
                "op": o.op,
                "outcome": outcome_view(o.outcome),
                "counts": dict(o.counts),
            }
            for o in result.ops
        ],
        "failures": failures,
        "has_failures": bool(failures),
        "error": dict(result.error) if result.error else None,
        "diagnostic": result.diagnostic,
        "log": list(result.log),
        "env": dict(result.env),
    }


def _summary(result: JobResult, counts: Mapping[str, Any]) -> str:
    written = int(counts.get("fields_written", 0) or 0)
    failed = int(counts.get("fields_failed", 0) or 0)
    requested = int(counts.get("fields_requested", 0) or 0)
    match result.outcome:
        case ApplyOutcome.APPLIED:
            if any(getattr(op, "op", "") == "career.set" for op in result.ops):
                return (
                    f"FC accepted {written} Career value call(s); no supported "
                    "read-back exists for these transient values."
                )
            return f"{written} field(s) written and verified."
        case ApplyOutcome.PARTIAL:
            return f"{written} of {requested or written + failed} field(s) applied; {failed} failed."
        case ApplyOutcome.NO_OP:
            return "Nothing changed — the player was not found in the live database."
        case ApplyOutcome.REJECTED:
            return "Refused before running. Nothing was written."
        case ApplyOutcome.CRASHED:
            return "The session ended before this job finished."
        case ApplyOutcome.EXPIRED:
            return "No game event arrived in time."
        case ApplyOutcome.FAILED:
            msg = (result.error or {}).get("message", "") or result.diagnostic
            return f"Failed inside the game: {msg}" if msg else "Failed inside the game."
        case ApplyOutcome.DEFERRED:
            return "Still running across game events."
        case ApplyOutcome.QUEUED:
            return "Queued — runs on the next Career Mode tick."
    raise AssertionError(f"unhandled outcome {result.outcome!r}")  # pragma: no cover


def header_view(state: AppState) -> dict[str, Any]:
    """The persistent chrome: target + bridge + in-flight count."""
    t = state.target
    ovr = None
    pos = ""
    if t.playerid is not None:
        for raw in state.squad.players:
            try:
                pid = int(raw.get("playerid") or raw.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if pid != int(t.playerid):
                continue
            ovr = raw.get("overallrating") or raw.get("ovr")
            pos = str(
                raw.get("position")
                or raw.get("preferredposition1")
                or raw.get("pos")
                or ""
            )
            break
    chip = t.name if t.locked and t.name else (
        str(t.playerid) if t.locked else "No player selected"
    )
    if t.locked:
        extras = [part for part in (str(ovr) if ovr not in (None, "", "—") else "", pos) if part]
        if extras:
            chip = f"{chip}  ·  {' · '.join(extras)}"
    return {
        "target": {
            "locked": t.locked,
            "playerid": t.playerid,
            "name": t.name or (str(t.playerid) if t.playerid else ""),
            "source": t.source,
            "ovr": ovr,
            "pos": pos,
            # Squad selection removes any need for users to see or copy an
            # implementation ID. Keep the number only as a manual-target
            # fallback when the game gave us no usable name.
            "text": chip,
        },
        "bridge": liveness_view(state.bridge.liveness),
        "in_flight": state.jobs.in_flight,
        "status": state.status,
        "status_tone": state.status_tone,
        "status_id": state.status_id,
    }


def search_view(state: AppState) -> dict[str, Any]:
    s = state.search
    return {
        "query": s.query,
        "year": s.year,
        "filters": dict(s.filters),
        "busy": s.busy,
        "error": s.error,
        "count": len(s.results),
        "results": [dict(r) for r in s.results],
        "selected": s.selected,
        "empty": (not s.busy and not s.results and bool(s.query)),
    }


def editor_view(state: AppState) -> dict[str, Any]:
    e = state.editor
    return {
        "has_changes": e.has_changes,
        "dirty_count": len(e.dirty),
        "dirty": dict(e.dirty),
        "merged": e.merged(),
        "source": e.source,
        "source_label": e.source_label,
        "warnings": list(e.warnings),
        "can_apply": e.has_changes and state.target.locked and state.bridge.armed,
        "blocked_reason": _apply_blocked_reason(state),
    }


def _apply_blocked_reason(state: AppState) -> str:
    if not state.target.locked:
        return "Pick a player first."
    if not state.editor.has_changes:
        return "No changes to apply."
    if not state.bridge.armed:
        return state.bridge.liveness.message
    return ""


# Trust window for squad cache display (matches commands.squad.TTL_SECONDS).
SQUAD_TTL_SECONDS = 120.0


def squad_view(state: AppState, now: Any = None) -> dict[str, Any]:
    """Present squad cache. Pass ``now`` (datetime) so age/TTL are honest and testable."""
    from datetime import datetime

    sq = state.squad
    clock = now
    if clock is None and sq.taken is not None:
        clock = datetime.now(tz=sq.taken.tzinfo) if sq.taken.tzinfo else datetime.now()
    age = sq.age_seconds(clock) if clock is not None else None

    ttl_stale = age is not None and age > SQUAD_TTL_SECONDS
    live_sid = getattr(state.bridge.liveness, "session_id", "") or ""
    session_stale = bool(
        sq.players and sq.session_id and live_sid and sq.session_id != live_sid
    )
    effective_stale = bool(sq.stale or ttl_stale or session_stale)

    if not sq.players:
        caption = "Squad · not synced"
    elif age is not None:
        caption = f"Squad · as of {_age_text(age)}"
        if ttl_stale:
            caption += " · aged"
        if session_stale:
            caption += " · session changed"
    else:
        caption = f"Squad · {len(sq.players)} players cached"

    return {
        "count": len(sq.players),
        "players": [dict(p) for p in sq.players],
        "stale": effective_stale,
        "flag_stale": bool(sq.stale),
        "ttl_stale": ttl_stale,
        "session_stale": session_stale,
        "save_uid": sq.save_uid,
        "session_id": sq.session_id,
        "age_text": _age_text(age),
        "age_seconds": age,
        "needs_refresh": effective_stale or not sq.players,
        "caption": caption,
    }


def in_flight_stage(
    outcome: ApplyOutcome | None = None,
    *,
    pill: Pill | None = None,
    claimed: bool = False,
) -> dict[str, str]:
    """One-line wait honesty for Activity, Sign, Transfers, and Automations."""
    if claimed or outcome is ApplyOutcome.DEFERRED:
        return {
            "chip": "In Live Editor",
            "detail": "Live Editor is applying this in Career Mode.",
        }
    if pill in (Pill.WAITING, Pill.STALLED):
        return {
            "chip": "Open Team Management",
            "detail": "Queued. Open Team Management or the Career hub so FC can claim it.",
        }
    return {
        "chip": "Queued",
        "detail": "Waiting for a safe Career screen. Open Team Management or return to the Career hub.",
    }


def jobs_view(state: AppState) -> dict[str, Any]:
    lv = state.bridge.liveness
    return {
        "active": [
            _job_list_item(j, active=True, pill=lv.pill) for j in state.jobs.active.values()
        ],
        "history": [_job_list_item(j, active=False, pill=lv.pill) for j in state.jobs.history],
        "show_force_drain": lv.pill in (Pill.WAITING, Pill.STALLED),
    }


def _job_list_item(job: Any, *, active: bool, pill: Pill | None = None) -> dict[str, Any]:
    """Compact queue data with the real worker result, not a generic status."""
    result = getattr(job, "result", None)
    stage = in_flight_stage(getattr(job, "outcome", None), pill=pill)
    if result is None:
        detail = stage["detail"] if active else "No result details were recorded."
    elif result.outcome in {
        ApplyOutcome.FAILED, ApplyOutcome.REJECTED, ApplyOutcome.CRASHED,
        ApplyOutcome.EXPIRED, ApplyOutcome.PARTIAL,
    }:
        detail = map_job_failure(result)
    else:
        detail = _summary(result, dict(result.counts))
    return {
        "job_id": job.job_id,
        "label": job.label,
        "outcome": outcome_view(job.outcome),
        "submitted": job.submitted.isoformat() if job.submitted else "",
        "detail": detail,
        "active": active,
        "stage": stage,
        "retry_suggested": bool(result is not None and result.outcome in {
            ApplyOutcome.FAILED, ApplyOutcome.REJECTED, ApplyOutcome.CRASHED,
            ApplyOutcome.EXPIRED, ApplyOutcome.PARTIAL,
        }),
    }
