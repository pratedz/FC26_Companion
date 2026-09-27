"""The apply pipeline: build a Job, submit it, follow it to a terminal result.

This is the flow v1 got most wrong, and every fix here maps to a specific v1
defect:

  - v1 wrote **two files per job** (the named job *and* ``_run_now.lua``), so
    the bridge skipped 73% of pending jobs as duplicates and reported the work
    under the name ``_run_now.lua``. The job id was gone; every recent history
    row had ``"last_result": ""``. Here one job is one file, named by its id.
  - v1 reported ``ok`` when ``pcall`` merely didn't throw. Here the outcome
    comes from the result file's semantic verdict.
  - v1's failure path referenced ``e`` from ``except Exception as e:`` inside a
    deferred Tk callback — Python deletes ``e`` when the block exits, so **every
    failed apply crashed its own error handler**, showed no message, and left
    the Apply button stuck until restart (13 instances). Here the message is
    bound to a local before any callback closes over it.
  - v1's pre-apply snapshot failing was swallowed and the apply proceeded, so
    the user lost their only rollback with no warning. Here that is a hard stop
    unless explicitly overridden.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from ...domain.job import Job, is_sign_job_label, job_apply_player, op_set_fields
from ...core.transport.jobfile import read_worker_json
from ...domain.outcome import ApplyOutcome, JobResult
from ...domain.player import PlayerValidationError, normalize_patch, player_id
from .. import events as E
from ..services import Services

# How long to follow a job before handing it back to the UI as "still queued".
# This is NOT a failure deadline — the job stays valid and will run on a later
# event. v1's single 55-second wall-clock timeout was simply wrong whenever the
# user was sitting in a menu, because no Career Mode event was coming.
FOLLOW_SECONDS = 45.0
# A queue file may take a moment to appear/rename. Do not classify a fresh
# submission as a ghost just because the app polls between those operations.
MISSING_QUEUE_GRACE_SECONDS = 60.0


class ApplyError(RuntimeError):
    """Raised only for *local* failures (validation, snapshot). Never for a
    job that ran and came back unhappy — that is a JobResult, not an exception."""


def submit_job(
    svc: Services,
    job: Job,
    *,
    follow: bool = True,
    on_result: Callable[[JobResult], None] | None = None,
) -> str:
    """Submit and (optionally) follow a job on a worker thread.

    Returns the job_id immediately. Progress and completion arrive as Events.
    """
    job.validate()  # fail loudly here, before anything reaches the queue
    job_id = svc.transport.submit(job)
    if svc.db is not None:
        svc.db.record_submitted(
            job_id=job_id,
            job=job.to_wire(),
            origin=job.origin,
            label=job.label,
            created_utc=svc.clock.stamp(),
            submitted_utc=svc.clock.stamp(),
        )
    svc.store.dispatch(
        E.JobSubmitted(job_id=job_id, label=job.label, at=svc.clock.now())
    )
    if svc.log is not None:
        svc.log.info("job submitted", extra={"job_id": job_id, "label": job.label})

    if not follow:
        return job_id

    def _follow(_token: Any) -> JobResult:
        result = svc.transport.await_result(
            job_id,
            timeout=FOLLOW_SECONDS,
            on_tick=lambda lv: svc.executor.on_ui_thread(
                lambda: svc.store.dispatch(E.LivenessChanged(lv))
            ),
        )
        # Bind the values NOW; never let a callback close over a loop variable
        # or an `except ... as e` name (the v1 bug that ate every error).
        outcome = result.outcome
        captured = result

        def _publish() -> None:
            if outcome.is_terminal:
                svc.store.dispatch(E.JobFinished(job_id=job_id, result=captured))
                _persist_result(svc, captured)
            else:
                svc.store.dispatch(E.JobProgressed(job_id=job_id, result=captured))
            svc.store.dispatch(E.StatusSet(_status_line(captured)))
            if outcome is ApplyOutcome.APPLIED and _is_write_job(job):
                from .squad import invalidate

                invalidate(svc, reason=f"{job.label or job_id} applied")
            if on_result is not None:
                on_result(captured)

        svc.executor.on_ui_thread(_publish)
        return result

    svc.executor.submit(f"apply:{job_id}", _follow)
    return job_id


def _is_write_job(job: Job) -> bool:
    if job.dry_run:
        return False
    read_only = {"diag.ping", "db.dump", "export_squad", "snapshot", "injury.scan"}
    return any(op.op not in read_only for op in job.ops)


def _is_recorded_write(svc: Services, job_id: str) -> bool:
    """Recover mutation intent for a result collected after a restart."""
    if svc.db is None:
        return False
    rec = svc.db.job_record(job_id)
    if rec is None or not isinstance(rec.job_json, Mapping):
        return False
    if bool(rec.job_json.get("dry_run", False)):
        return False
    read_only = {"diag.ping", "db.dump", "export_squad", "snapshot", "injury.scan"}
    return any(
        isinstance(op, Mapping) and str(op.get("op", "")) not in read_only
        for op in (rec.job_json.get("ops") or ())
    )


def _commit_recorded_editor_patch(
    svc: Services, job_id: str, result: JobResult
) -> None:
    """Promote a verified editor patch even when its in-memory callback was lost."""
    if result.outcome is not ApplyOutcome.APPLIED or svc.db is None:
        return
    rec = svc.db.job_record(job_id)
    raw = rec.job_json if rec is not None else None
    origin = str(raw.get("origin") or "") if isinstance(raw, Mapping) else ""
    if not isinstance(raw, Mapping) or not (
        origin == "ui.editor.apply" or origin.startswith("ui.build.")
    ):
        return
    ops = raw.get("ops")
    if not isinstance(ops, (list, tuple)) or len(ops) != 1:
        return
    op = ops[0]
    if not isinstance(op, Mapping) or op.get("op") != "set_fields":
        return
    key = op.get("key")
    fields = op.get("fields")
    if not isinstance(key, Mapping) or not isinstance(fields, Mapping):
        return
    if key.get("field") != "playerid":
        return
    try:
        pid = player_id(key.get("value"))
        patch = normalize_patch(fields)
    except PlayerValidationError:
        return
    current = svc.store.snapshot()
    if current.target.playerid != pid or dict(current.editor.dirty) != patch:
        return
    svc.store.dispatch(
        E.EditorLoaded(
            base=current.editor.merged(),
            categories=current.editor.categories,
        )
    )


def _persist_result(svc: Services, result: JobResult, *, import_snapshot: bool = True,
                    finished_utc: str | None = None) -> None:
    if svc.db is None:
        return
    if import_snapshot:
        _persist_prewrite_snapshot(svc, result)
    counts = result.counts
    svc.db.record_finished(
        result.job_id,
        outcome=result.outcome,
        result=dict(result.raw),
        finished_utc=finished_utc or svc.clock.stamp(),
        applied_count=int(
            counts.get("fields_written", counts.get("ops_ok", 0)) or 0
        ),
        failed_count=int(
            counts.get("fields_failed", counts.get("ops_failed", 0)) or 0
        ),
        trigger_event=str(result.env.get("trigger_event", "") or "") or None,
        save_uid=str(result.env.get("save_uid", "") or "") or None,
        exec_ms=int(counts.get("exec_ms", result.raw.get("duration_ms", 0)) or 0) or None,
    )


def _persist_prewrite_snapshot(svc: Services, result: JobResult) -> None:
    """Import the worker's verified pre-write artifact into the snapshot store.

    The file remains the crash-safe source of truth; SQLite powers Activity and
    restore after the Companion is restarted. Missing/malformed artifacts are
    never converted into a fake undo entry.
    """
    if svc.db is None or result.outcome is not ApplyOutcome.APPLIED:
        return
    rec = svc.db.job_record(result.job_id)
    raw_job = rec.job_json if rec is not None else None
    if not isinstance(raw_job, Mapping):
        return
    raw_path = raw_job.get("snapshot_to")
    if not isinstance(raw_path, str) or not raw_path:
        return
    try:
        path = Path(raw_path).resolve()
        root = svc.paths.root.resolve()
        path.relative_to(root)
        payload = read_worker_json(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return
    rows = payload.get("rows") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list) or not rows:
        return
    stored: list[dict[str, Any]] = []
    target_id: int | None = None
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("existed", False):
            continue
        table = str(row.get("table") or "players")
        key = row.get("key")
        values = row.get("fields")
        if not isinstance(key, Mapping) or not isinstance(values, Mapping):
            continue
        key_field = str(key.get("field") or "playerid")
        key_value = key.get("value")
        if key_field == "playerid" and target_id is None:
            try:
                target_id = int(key_value)
            except (TypeError, ValueError):
                pass
        for field_name, value in values.items():
            stored.append(
                {
                    "table": table,
                    "key": {"field": key_field, "value": key_value},
                    "f": str(field_name),
                    "v": value,
                }
            )
    if not stored:
        return
    svc.db.put_snapshot(
        id=result.job_id,
        ts=float(payload.get("taken_at") or svc.clock.now()),
        fields=stored,
        label=rec.label or result.label or f"Before {result.job_id}",
        kind="pre_write",
        target_id=target_id,
        save_uid=str(payload.get("save_uid") or "") or None,
        extra={"path": str(path), "job_id": result.job_id},
        is_last_apply=True,
        taken_utc=svc.clock.stamp(),
    )


def restore_snapshot(
    svc: Services,
    snapshot_id: str | None = None,
    *,
    follow: bool = True,
) -> str:
    """Restore a stored Phase 3 pre-write snapshot through verified set_fields."""
    if svc.db is None:
        raise ApplyError("Undo history is unavailable.")
    snap = svc.db.snapshot(snapshot_id) if snapshot_id else svc.db.last_apply_snapshot()
    if snap is None or not snap.fields:
        raise ApplyError("No verified undo snapshot is available.")
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    keys: dict[tuple[str, str, str], Any] = {}
    for item in snap.fields:
        if not isinstance(item, Mapping) or "f" not in item:
            continue
        table = str(item.get("table") or "players")
        key = item.get("key")
        if isinstance(key, Mapping):
            key_field = str(key.get("field") or "playerid")
            key_value = key.get("value")
        else:
            key_field, key_value = "playerid", snap.target_id
        if key_value is None:
            continue
        token = (table, key_field, str(key_value))
        grouped.setdefault(token, {})[str(item["f"])] = item.get("v")
        keys[token] = key_value
    if not grouped:
        raise ApplyError("The undo snapshot contains no restorable fields.")
    ops = tuple(
        op_set_fields(
            f"restore.{index}",
            table=table,
            key_field=key_field,
            key_value=keys[token],
            fields=fields,
            growth_mirror="auto" if table == "players" else "off",
            upsert=table == "editedplayernames",
        )
        for index, (token, fields) in enumerate(grouped.items(), start=1)
        for table, key_field, _ in (token,)
    )
    job = Job(
        ops=ops,
        label=f"Undo: {snap.label or snap.id}",
        origin="ui.activity.undo",
    )
    undo_dir = svc.paths.root / "snapshots"
    undo_dir.mkdir(parents=True, exist_ok=True)
    job = replace(job, snapshot_to=str((undo_dir / f"{job.job_id}.json").resolve()))
    return submit_job(svc, job, follow=follow)


def _status_line(result: JobResult) -> str:
    # Career fitness/form/morale/sharpness live in LE's Career Mode structs,
    # not the editable database.  FC exposes setters but no supported getter
    # for a read-back verification.  Showing "verified" here would recreate
    # exactly the false-success wording this companion is meant to avoid.
    career_set = any(getattr(op, "op", "") == "career.set" for op in result.ops)
    match result.outcome:
        case ApplyOutcome.APPLIED:
            n = int(result.counts.get("fields_written", 0) or 0)
            if career_set:
                return (
                    f"Applied — FC accepted {n} Career value call(s). "
                    "Refresh squad to confirm the current save."
                )
            return f"Applied — {n} field(s) verified." if n else "Applied."
        case ApplyOutcome.NO_OP:
            return "Nothing changed — the player was not found in the live database."
        case ApplyOutcome.PARTIAL:
            reason = ""
            if result.failures:
                failure = result.failures[0]
                reason = failure.detail or failure.reason
            if not reason and result.error:
                reason = str(
                    result.error.get("detail")
                    or result.error.get("message")
                    or ""
                )
            if is_sign_job_label(str(result.label or "")) and reason:
                lowered = reason.lower()
                if "name_not_visible" in lowered or "name_visibility" in lowered:
                    return (
                        "Signing is incomplete: FC did not verify the visible player name. "
                        "Earlier steps may already be present in the save; open Activity before retrying."
                    )
                return (
                    f"Signing failed at {reason}. "
                    "The player was not transferred — open Activity for the full report, "
                    "then try again after Refresh squad."
                )
            f = int(result.counts.get("fields_failed", 0) or 0)
            if reason:
                return f"Partly applied — {reason}. Open the report."
            return f"Partly applied — {f} field(s) failed. Open the report."
        case ApplyOutcome.QUEUED:
            return "Queued. Undo lives in Activity if a snapshot landed."
        case ApplyOutcome.DEFERRED:
            return "Working — running across game events."
        case ApplyOutcome.REJECTED:
            from ...domain.automation_guide import map_job_failure

            return map_job_failure(result)
        case ApplyOutcome.CRASHED:
            return "The session ended before the job finished."
        case ApplyOutcome.EXPIRED:
            return "Expired with no game activity. Re-queue it."
        case ApplyOutcome.FAILED:
            from ...domain.automation_guide import map_job_failure

            return map_job_failure(result)
    raise AssertionError(f"unhandled outcome {result.outcome!r}")  # pragma: no cover


def apply_fields(
    svc: Services,
    playerid: int,
    fields: Mapping[str, Any],
    *,
    names: Mapping[str, str] | None = None,
    label: str = "",
    origin: str = "ui.editor.apply",
    snapshot_to: str = "",
    dry_run: bool = False,
    follow: bool = True,
    on_result: Callable[[JobResult], None] | None = None,
) -> str:
    """Apply a field set to one player, mirroring growth XP by default.

    The growth mirror defaults to on because writing an attribute to the DB is
    **not enough in Career Mode**: the development plan outranks the players
    table and overwrites it on the next tick. Without the mirror the user sees
    the edit land and then silently revert, which is the single most confusing
    failure the app can produce.
    """
    if not fields:
        raise ApplyError("Nothing to apply.")
    try:
        safe_playerid = player_id(playerid)
        safe_fields = normalize_patch(fields)
    except PlayerValidationError as exc:
        raise ApplyError(str(exc)) from exc
    state = svc.store.snapshot()
    save_uid = str(state.squad.save_uid or "").strip()
    if not save_uid and not dry_run:
        raise ApplyError("Read the squad from this Career save before applying.")
    job = job_apply_player(
        safe_playerid,
        safe_fields,
        names=names,
        label=label or f"Apply {len(fields)} field(s) to {playerid}",
        origin=origin,
        growth_mirror="auto" if state.prefs.growth_mirror else "off",
        snapshot_to=snapshot_to,
        dry_run=dry_run,
        requires={"save_uid": save_uid} if save_uid else None,
    )
    if not dry_run and not snapshot_to:
        snapshot_dir = svc.paths.root / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        job = replace(
            job,
            snapshot_to=str((snapshot_dir / f"{job.job_id}.json").resolve()),
        )
    return submit_job(svc, job, follow=follow, on_result=on_result)


def apply_editor(svc: Services, *, dry_run: bool = False) -> str:
    """Apply exactly the dirty fields in the editor to the locked target."""
    state = svc.store.snapshot()
    if not state.target.locked or state.target.playerid is None:
        raise ApplyError("Pick a player first.")
    if not state.editor.has_changes:
        raise ApplyError("No changes to apply.")
    playerid = state.target.playerid
    patch = dict(state.editor.dirty)

    def committed(result: JobResult) -> None:
        if result.outcome is not ApplyOutcome.APPLIED:
            return
        current = svc.store.snapshot()
        # Do not clear another target's changes, or changes staged while this
        # job was running. Only the exact submitted patch becomes the new base.
        if current.target.playerid != playerid or dict(current.editor.dirty) != patch:
            return
        svc.store.dispatch(
            E.EditorLoaded(
                base=current.editor.merged(),
                categories=current.editor.categories,
            )
        )

    return apply_fields(
        svc,
        playerid,
        patch,
        label=f"{state.target.name or state.target.playerid}: {len(state.editor.dirty)} change(s)",
        origin=(
            f"ui.build.{state.editor.source}"
            if state.editor.source else "ui.editor.apply"
        ),
        dry_run=dry_run,
        on_result=committed,
    )


def refresh_liveness(svc: Services) -> None:
    """Poll the transport's liveness and publish it. Cheap; safe to call often.

    A single missing session.json is held as the previous session. The
    reducer therefore does not treat that blink as ABC → "".
    """
    from .liveness_hold import stabilize_liveness

    raw = svc.transport.liveness()
    before = svc.store.snapshot()
    lv = stabilize_liveness(svc.liveness_hold, raw, svc.clock.now())
    svc.store.dispatch(E.LivenessChanged(lv))
    after = svc.store.snapshot()
    from .sync_trace import sync_debug_enabled, sync_log

    if sync_debug_enabled():
        hub = bool((lv.capabilities or {}).get("hub_ready"))
        sync_log(
            "LIVENESS",
            f"session_id={lv.session_id or '-'} save_uid={lv.save_uid or '-'} "
            f"hub_ready={hub} armed={lv.armed} pill={lv.pill.value} "
            f"players={len(after.squad.players)} stale={after.squad.stale}",
        )
        if before.squad.players and not after.squad.players:
            sync_log(
                "CONTEXT",
                f"squad_cleared session_id={lv.session_id or '-'} save_uid={lv.save_uid or '-'}",
            )
        elif (not before.squad.stale) and after.squad.stale and after.squad.players:
            sync_log("CONTEXT", "squad_marked_stale reason=hub_exit")


def collect_finished(svc: Services) -> list[JobResult]:
    """Sweep results for jobs the UI is still tracking (covers a missed follow).

    Also sweeps crashed sessions, so a job whose LE died is reported rather
    than sitting in the active list forever.
    """
    out: list[JobResult] = []
    for job_id in list(svc.store.snapshot().jobs.active.keys()):
        res = svc.transport.result(job_id)
        if res is not None and res.outcome.is_terminal:
            svc.store.dispatch(E.JobFinished(job_id=job_id, result=res))
            _persist_result(svc, res)
            svc.store.dispatch(E.StatusSet(_status_line(res)))
            from .squad import ingest_result

            ingest_result(svc, res)
            if res.outcome is ApplyOutcome.APPLIED and _is_recorded_write(svc, job_id):
                _commit_recorded_editor_patch(svc, job_id, res)
                from .squad import invalidate

                invalidate(svc, reason=f"{res.label or job_id} applied")
            out.append(res)
    for job_id in svc.transport.sweep_crashed():
        res = svc.transport.result(job_id)
        if res is not None:
            svc.store.dispatch(E.JobFinished(job_id=job_id, result=res))
            _persist_result(svc, res)
            svc.store.dispatch(E.StatusSet(_status_line(res)))
            from .squad import ingest_result

            ingest_result(svc, res)
            out.append(res)
    reconciled = reconcile_missing_queue_entries(svc)
    if reconciled:
        svc.store.dispatch(
            E.StatusSet(
                f"Closed {reconciled} old queue record{'s' if reconciled != 1 else ''} "
                "whose worker files are no longer present."
            )
        )
    return out


def reconcile_recorded_worker_results(svc: Services) -> int:
    """At startup, repair synthetic history verdicts superseded by real results.

    This updates reporting only. Never resubmit a job or invent missing parts.
    Real failures, partial results and other terminal verdicts are left intact.
    """
    if svc.db is None:
        return 0
    corrected = 0
    for record in svc.db.job_records(limit=200):
        raw = record.result_json or {}
        diagnostic = str(raw.get("diagnostic") or "")
        synthetic = (
            record.outcome is ApplyOutcome.CRASHED and diagnostic.startswith("claimed by session ")
        ) or (
            record.outcome is ApplyOutcome.EXPIRED and diagnostic == "Queue entry is missing after the grace period."
        )
        if not synthetic:
            continue
        real = svc.transport.result(record.job_id)
        if (real is None or real.job_id != record.job_id or not real.outcome.is_terminal
                or real.outcome == record.outcome):
            continue
        svc.store.dispatch(E.JobFinished(job_id=record.job_id, result=real))
        # Historical reporting correction must not move the last-Undo pointer
        # or turn yesterday's worker finish into a new execution today.
        finished = (real.finished.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    if real.finished is not None else record.finished_utc)
        _persist_result(svc, real, import_snapshot=False, finished_utc=finished)
        corrected += 1
    return corrected


def reconcile_missing_queue_entries(
    svc: Services,
    *,
    grace_seconds: float = MISSING_QUEUE_GRACE_SECONDS,
) -> int:
    """Terminalize genuinely old ghost records without racing a live worker.

    Queue files can vanish after a prior app crash or an old unsafe clear. We
    always look for a final result first, leave pending/claimed work alone, and
    give a fresh submission a grace window before calling it expired.
    """
    try:
        known = set(svc.transport.pending_ids()) | set(svc.transport.claimed_ids())
    except Exception:
        return 0
    now = svc.clock.now()
    reconciled = 0
    for job_id, view in tuple(svc.store.snapshot().jobs.active.items()):
        if job_id in known:
            continue
        real = svc.transport.result(job_id)
        if real is not None and real.outcome.is_terminal:
            svc.store.dispatch(E.JobFinished(job_id=job_id, result=real))
            _persist_result(svc, real)
            reconciled += 1
            continue
        submitted = view.submitted
        if submitted is not None:
            try:
                if (now - submitted).total_seconds() < grace_seconds:
                    continue
            except Exception:
                continue
        result = _queue_entry_missing_result(job_id, view.label)
        svc.store.dispatch(E.JobFinished(job_id=job_id, result=result))
        _persist_result(svc, result)
        reconciled += 1
    return reconciled


def _queue_entry_missing_result(job_id: str, label: str) -> JobResult:
    """Truthful terminal outcome for a stale DB row with no worker file."""
    return JobResult.from_wire(
        {
            "job_id": job_id,
            "label": label,
            "state": "expired",
            "outcome": "expired",
            "ok": False,
            "diagnostic": "Queue entry is missing after the grace period.",
            "counts": {},
            "failures": [
                {
                    "reason": "queue_entry_missing",
                    "detail": "No pending or claimed worker file exists for this old job.",
                    "phase": "queue",
                }
            ],
            "error": {
                "phase": "queue",
                "message": "queue_entry_missing",
                "detail": "No pending or claimed worker file exists for this old job.",
            },
        }
    )
