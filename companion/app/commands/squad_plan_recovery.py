"""Crash-safe parent records for ordered squad writes and complete rollback."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Callable

from ... import CORE_VERSION

from ...core.transport.jobfile import read_json, read_worker_json
from ...domain.ids import is_ulid
from ...domain.job import Grants, Job, Op, op_snapshot
from ...domain.ids import new_ulid
from ...domain.outcome import ApplyOutcome, JobResult
from ...domain.player import FIELD_SPECS
from ...domain.squad_plan import SquadPlan, plan_from_rows, build_apply_jobs
from .. import events as E
from .apply import ApplyError, FOLLOW_SECONDS, _persist_result, _status_line


def record_path(svc: Any, plan_id: str) -> Path:
    if not is_ulid(plan_id):
        raise ApplyError("Invalid squad plan identifier.")
    return svc.paths.root / "squad_plans" / f"{plan_id}.json"


def load_record(svc: Any, plan_id: str) -> dict[str, Any]:
    record = read_json(record_path(svc, plan_id))
    if not record or record.get("schema") != 1:
        raise ApplyError("Squad plan recovery record is unavailable.")
    return record


def save_record(svc: Any, record: Mapping[str, Any]) -> None:
    path = record_path(svc, str(record["plan_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def runner_lock(svc: Any, plan_id: str):
    """Do not run two followers/retries of the same parent concurrently."""
    from ...platform.procs import is_pid_alive

    path = record_path(svc, plan_id).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            owner = int(path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            owner = 0
        if not owner:
            try:
                unpublished = time.time() - path.stat().st_mtime < 30
            except OSError:
                unpublished = True
            if unpublished:
                raise ApplyError("This squad plan is already running.")
        if owner and is_pid_alive(owner):
            raise ApplyError("This squad plan is already running.")
        path.unlink(missing_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ApplyError("This squad plan is already running.") from exc
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def job_from_wire(raw: Mapping[str, Any]) -> Job:
    operations = []
    for item in raw.get("ops", ()):
        body = {key: value for key, value in item.items() if key not in {"op", "id", "on_error", "v"}}
        operations.append(Op(str(item["op"]), str(item["id"]), body,
                             on_error=item.get("on_error", "abort"), v=int(item.get("v", 1))))
    fields = {key: value for key, value in raw.items() if key in Job.__dataclass_fields__
              and key not in {"ops", "grants"}}
    return Job(ops=tuple(operations), grants=Grants(**dict(raw.get("grants") or {})), **fields)


def make_record(svc: Any, plan: Any, jobs: tuple[Job, ...], *, kind: str = "apply",
                undo_of: str = "") -> dict[str, Any]:
    expected: dict[str, dict[str, dict[str, Any]]] = {}
    for job in jobs:
        for operation in job.ops:
            table = str(operation.body.get("table") or "players")
            pid = str(operation.body["key"]["value"])
            expected.setdefault(table, {}).setdefault(pid, {}).update(operation.body["fields"])
    return {"schema": 1, "plan_id": jobs[0].job_id, "kind": kind, "undo_of": undo_of,
            "label": plan.label or jobs[0].label, "created_utc": svc.clock.stamp(),
            "updated_utc": svc.clock.stamp(), "status": "ready", "cursor": 0,
            "source": {"save_uid": plan.source_save_uid, "teamid": plan.source_teamid,
                       "snapshot_revision": plan.source_snapshot_revision,
                       "snapshot_fingerprint": plan.source_snapshot_fingerprint},
            "names": {**{str(pid): name for pid, name in plan.shirt_names.items()},
                      **{str(member.playerid): member.name for member in plan.members}},
            "playerids": list(plan.playerids), "expected": expected,
            "baseline": {"players": {str(member.playerid): dict(member.base) for member in plan.members},
                         "teamplayerlinks": {str(pid): {"jerseynumber": number} for pid, number in
                            {**{m.playerid: m.base.get("jerseynumber") for m in plan.members}, **dict(plan.shirt_book)}.items()}},
            "shirt_book": {str(pid): number for pid, number in plan.shirt_book.items()},
            "parts": [{"job": job.to_wire(), "status": "unsent", "attempts": []}
                      for job in jobs], "verification": {"status": "pending", "parts": []},
            "receipt": {}}


def receipt(record: Mapping[str, Any]) -> dict[str, Any]:
    expected = record.get("expected") or {}
    requested_players = set(record.get("playerids") or ())
    applied_players: set[int] = set()
    changed_fields: set[tuple[str, str, str]] = set()
    unidentified: dict[tuple[str, str], int] = {}
    failed_fields = 0
    complete_parts = 0
    for part in record.get("parts", ()):
        if part.get("status") == "applied":
            complete_parts += 1
        for attempt in [*part.get("attempts", ()), {"job": part["job"], "result": part.get("result"), "status": part.get("status")}]:
            raw = attempt.get("result") or {}
            outcomes = {str(item.get("id")): item for item in raw.get("ops", ())}
            for operation in attempt["job"].get("ops", ()):
                item = outcomes.get(str(operation["id"]))
                table, pid = str(operation.get("table") or "players"), str(operation["key"]["value"])
                succeeded = bool(item.get("ok")) if item else attempt.get("status") == "applied"
                fields = set(operation.get("fields", {}))
                if not succeeded:
                    failed = _failed_field_names(raw, operation)
                    written = int((item.get("counts") or {}).get("fields_written", 0)) if item else 0
                    candidates = fields - failed
                    fields = candidates if (failed and written == len(candidates)) else fields if (not failed and written == len(fields)) else set()
                    if written and not fields:
                        unidentified[(table, pid)] = max(unidentified.get((table, pid), 0), written)
                        applied_players.add(int(pid))
                if not fields:
                    continue
                applied_players.add(int(pid))
                changed_fields.update((table, pid, field) for field in fields)
        failed_fields += int(((part.get("result") or {}).get("counts") or {}).get("fields_failed", 0) or 0)
    verification = record.get("verification") or {}
    known_counts: dict[tuple[str, str], int] = {}
    for table, pid, _field in changed_fields:
        known_counts[(table, pid)] = known_counts.get((table, pid), 0) + 1
    unknown_count = sum(max(0, written - known_counts.get(entity, 0)) for entity, written in unidentified.items())
    return {"plan_id": record["plan_id"], "status": record.get("status"),
            "players_requested": len(requested_players), "players_applied": len(applied_players & requested_players),
            "fields_requested": sum(len(fields) for rows in expected.values() for fields in rows.values()),
            "fields_applied": len(changed_fields) + unknown_count, "fields_failed": failed_fields,
            "applied_fields_unidentified": unknown_count,
            "parts_total": len(record.get("parts", ())), "parts_applied": complete_parts,
            "next_part": min(int(record.get("cursor", 0)) + 1, len(record.get("parts", ()))),
            "verification": verification.get("status", "pending"),
            "fields_verified": int(verification.get("fields_verified", 0)),
            "verification_errors": list(verification.get("errors") or ()),
            "undo_plan_id": record.get("undo_plan_id", ""),
            "error": record.get("error", "")}


def aggregate_result(record: Mapping[str, Any], latest: JobResult) -> JobResult:
    report = receipt(record)
    outcome = latest.outcome
    if record.get("status") == "applied":
        outcome = ApplyOutcome.APPLIED
    elif record.get("status") == "verification_failed":
        outcome = ApplyOutcome.PARTIAL
    elif record.get("status") in {"running", "awaiting_verification", "ready"}:
        outcome = ApplyOutcome.QUEUED
    raw = {**dict(latest.raw), "job_id": record["plan_id"], "label": record["label"],
           "outcome": outcome.name.lower(), "ok": outcome.is_success,
           "plan_receipt": report, "counts": {**dict(latest.counts),
               "fields_written": report["fields_applied"], "fields_failed": report["fields_failed"],
               "players_requested": report["players_requested"], "players_applied": report["players_applied"],
               "fields_verified": report["fields_verified"]}}
    return replace(latest, job_id=str(record["plan_id"]), label=str(record["label"]),
                   outcome=outcome, counts=raw["counts"], raw=raw)


def read_before_fields(svc: Any, record: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Earliest verified worker value wins, including failed partial attempts."""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for part in record.get("parts", ()):
        attempts = [*part.get("attempts", ()), {"job": part["job"], "result": part.get("result")}]
        for attempt in attempts:
            raw_result = attempt.get("result") or {}
            if not raw_result and part.get("status") == "unsent":
                continue
            job = attempt["job"]
            raw_path = str(job.get("snapshot_to") or "")
            if not raw_path:
                if not job.get("dry_run"):
                    raise ApplyError("A squad part has no rollback snapshot. Undo is unavailable.")
                continue
            path = Path(raw_path).resolve()
            try:
                path.relative_to(svc.paths.root.resolve())
            except ValueError as exc:
                raise ApplyError("Rollback snapshot is outside the application directory.") from exc
            payload = read_worker_json(path)
            if not payload:
                # A rejected-before-execution part did not mutate anything.
                if raw_result and JobResult.from_wire(raw_result).outcome in {ApplyOutcome.REJECTED, ApplyOutcome.EXPIRED}:
                    continue
                raise ApplyError("A required rollback snapshot is missing. Undo cannot safely continue.")
            if str(payload.get("save_uid") or "") != str(record["source"].get("save_uid") or ""):
                raise ApplyError("Rollback snapshot belongs to a different Career save.")
            rows = payload.get("rows")
            if not isinstance(rows, list):
                raise ApplyError("Rollback snapshot contains no verified rows.")
            for row in rows:
                if not row.get("existed"):
                    continue
                table = str(row.get("table") or "players")
                if table == "teamplayerlinks" and int(row.get("teamid") or 0) != int(record["source"].get("teamid") or 0):
                    raise ApplyError("Rollback shirt snapshot is missing the original club identity.")
                pid = str(row["key"]["value"])
                fields = result.setdefault(table, {}).setdefault(pid, {})
                for name, value in (row.get("fields") or {}).items():
                    fields.setdefault(name, value)
    if not result:
        raise ApplyError("No verified rollback values are available for this squad plan.")
    return result


def _roster_rows(svc: Any) -> dict[int, dict[str, Any]]:
    rows = {}
    for row in svc.store.snapshot().squad.players:
        raw = {**dict(row), **dict(row.get("_raw") or {})}
        try:
            rows[int(raw.get("playerid") or raw.get("id"))] = raw
        except (TypeError, ValueError):
            continue
    return rows


def _check_source(svc: Any, source: Mapping[str, Any], affected: Any, *, initial: bool = False,
                  baseline: Mapping[int, Mapping[str, Any]] | None = None) -> None:
    from ...core.transport.v3 import FileTransport
    squad = svc.store.snapshot().squad
    live_known = bool(squad.save_uid or squad.teamid) or isinstance(svc.transport, FileTransport)
    if live_known and (not source.get("save_uid") or not source.get("teamid") or not source.get("snapshot_revision")):
        raise ApplyError("This plan has no original Career export identity. Refresh the squad and rebuild it.")
    if source.get("save_uid") and source["save_uid"] != squad.save_uid:
        raise ApplyError("Career save changed since this plan was built. Switch back or rebuild the plan.")
    if source.get("teamid") and int(source["teamid"]) != int(squad.teamid or 0):
        raise ApplyError("Club changed since this plan was built. Switch back or rebuild the plan.")
    if initial and live_known:
        if squad.stale or squad.taken is None:
            raise ApplyError("Refresh the live squad before applying this plan.")
        if str(source["snapshot_revision"]) != squad.taken.isoformat():
            current = _roster_rows(svc)
            if not baseline:
                raise ApplyError("The squad export changed since this plan was built. Rebuild the plan from the refreshed squad.")
            for pid, original in baseline.items():
                live = current.get(int(pid), {})
                for name, value in original.items():
                    # Display labels and derived age are not database changes.
                    # Snapshot and Club export metadata have different shapes.
                    if name not in FIELD_SPECS and name != "jerseynumber":
                        continue
                    if name in live and not _same_value(live[name], value):
                        label = FIELD_SPECS[name].label if name in FIELD_SPECS else "Shirt number"
                        player = live.get("name") or f"Player {pid}"
                        raise ApplyError(
                            f"{player}: {label} changed from {value} to {live[name]} since this plan "
                            "was built. Rebuild it from the refreshed squad."
                        )
    if live_known:
        missing = set(int(pid) for pid in affected) - set(_roster_rows(svc))
        if missing:
            raise ApplyError("These planned players are no longer in the original club: "
                             + ", ".join(str(pid) for pid in sorted(missing)) + ". Refresh and rebuild.")


def _snapshots(svc: Any, jobs: tuple[Job, ...], save_uid: str) -> tuple[Job, ...]:
    directory = svc.paths.root / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    return tuple(replace(job, requires={**dict(job.requires), "core_version": CORE_VERSION,
                                       **({"save_uid": save_uid} if save_uid else {})},
                         snapshot_to="" if job.dry_run else (directory / f"{job.job_id}.json").resolve().as_posix())
                 for job in jobs)


def apply_squad_plan(svc: Any, plan: SquadPlan, *, dry_run: bool = False, follow: bool = True,
                     on_result: Callable[[JobResult], None] | None = None, confirm_token: str = "") -> str:
    if not plan.members:
        raise ApplyError("Select players first.")
    if plan.player_count > 1 and not confirm_squad_apply(confirm_token):
        raise ApplyError(f"Type APPLY to confirm a plan for {plan.player_count} players ({plan.field_count} fields).")
    source = {"save_uid": plan.source_save_uid, "teamid": plan.source_teamid,
              "snapshot_revision": plan.source_snapshot_revision}
    _check_source(svc, source, set(plan.playerids) | set(plan.jersey_numbers), initial=True,
                  baseline={member.playerid: member.base for member in plan.members})
    growth = "auto" if svc.store.snapshot().prefs.growth_mirror else "off"
    try:
        jobs = _snapshots(svc, build_apply_jobs(plan, growth_mirror=growth, dry_run=dry_run,
                                              label=f"Squad plan · {plan.blast_summary()}"), plan.source_save_uid)
    except Exception as exc:
        raise ApplyError(str(exc)) from exc
    record = make_record(svc, plan, jobs)
    save_record(svc, record)
    _start_record(svc, record["plan_id"], follow=follow, on_result=on_result)
    return str(record["plan_id"])


def confirm_squad_apply(typed: str) -> bool:
    return (typed or "").strip() == "APPLY"


def list_squad_plans(svc: Any) -> list[dict[str, Any]]:
    records = []
    for path in (svc.paths.root / "squad_plans").glob("*.json"):
        record = read_json(path)
        if record and record.get("schema") == 1:
            records.append(record)
    records.sort(key=lambda item: item.get("updated_utc", ""), reverse=True)
    return [squad_plan_receipt(svc, record["plan_id"]) for record in records]


def squad_plan_receipt(svc: Any, plan_id: str) -> dict[str, Any]:
    record = load_record(svc, plan_id)
    report = receipt(record)
    observed = (record.get("verification") or {}).get("observed") or {}
    baseline = record.get("baseline") or {}
    try:
        captured = read_before_fields(svc, record)
    except ApplyError:
        captured = {}
    rows = []
    for table, players in record["expected"].items():
        for pid, fields in players.items():
            for field, planned in fields.items():
                before = captured.get(table, {}).get(pid, {}).get(field,
                         baseline.get(table, {}).get(pid, {}).get(field))
                actual = observed.get(table, {}).get(pid, {}).get(field)
                rows.append({"table": table, "playerid": int(pid), "field": field, "before": before,
                             "planned": planned, "readback": actual,
                             "name": record.get("names", {}).get(pid, pid),
                             "verified": actual is not None and _same_value(actual, planned)})
    report.update(label=record["label"], source=record["source"], rows=rows,
                  kind=record.get("kind", "apply"), undo_of=record.get("undo_of", ""),
                  parts=[{"job_id": part["job"]["job_id"], "label": part["job"].get("label", ""),
                          "status": part["status"]} for part in record["parts"]])
    return report


def _affected_ids(record: Mapping[str, Any]) -> set[int]:
    return set(record.get("playerids") or ()) | {int(pid) for rows in record["expected"].values() for pid in rows}


def resume_squad_plan(svc: Any, plan_id: str, *, follow: bool = True,
                      on_result: Callable[[JobResult], None] | None = None) -> str:
    record = load_record(svc, plan_id)
    _check_source(svc, record["source"], _affected_ids(record))
    if record.get("undo_plan_id"):
        raise ApplyError("This plan has an undo in progress or already completed. Resume the undo, or build a new plan.")
    if record.get("status") == "applied":
        return plan_id
    if record.get("status") == "verification_failed":
        verification = record["verification"]
        previous = {key: value for key, value in verification.items() if key != "attempts"}
        verification.setdefault("attempts", []).append(previous)
        verification.update(status="pending", errors=[], observed={}, fields_verified=0,
                            parts=[{"job": replace(job_from_wire(part["job"]), job_id=new_ulid()).to_wire(),
                                    "status": "unsent"} for part in verification.get("parts", ())])
        save_record(svc, record)
    _start_record(svc, plan_id, follow=follow, on_result=on_result)
    return plan_id


def _start_record(svc: Any, plan_id: str, *, follow: bool,
                  on_result: Callable[[JobResult], None] | None) -> None:
    def work(token: Any) -> None:
        try:
            with runner_lock(svc, plan_id):
                _run_record(svc, load_record(svc, plan_id), token, follow=follow, on_result=on_result)
        except Exception as exc:
            message = str(exc)
            if "already running" in message:
                svc.executor.on_ui_thread(lambda: svc.store.dispatch(E.StatusSet(message)))
                return
            record = load_record(svc, plan_id)
            record.update(status="failed", error=message, updated_utc=svc.clock.stamp())
            save_record(svc, record)
            failure = JobResult.from_wire({"job_id": plan_id, "state": "failed", "ok": False,
                                          "diagnostic": message, "error": {"message": message, "phase": "companion"}})
            _publish_parent(svc, record, failure, on_result)
    if follow and svc.executor is not None and hasattr(svc.executor, "submit"):
        svc.executor.submit(f"squad.plan.parts:{plan_id}", work)
    else:
        work(type("Token", (), {"cancelled": False})())


def _submit_part(svc: Any, record: dict[str, Any], part: dict[str, Any], job: Job) -> str:
    part["status"] = "submitting"
    record.update(status="running", updated_utc=svc.clock.stamp())
    save_record(svc, record)
    try:
        job_id = svc.transport.submit(job)
    except Exception:
        # A failed enqueue with a demonstrably absent queue entry is still an
        # unsent part. An ambiguous enqueue must be reconciled before replay.
        try:
            present = set(svc.transport.pending_ids()) | set(svc.transport.claimed_ids())
            part["status"] = "pending" if job.job_id in present else "unsent"
        except Exception:
            part["status"] = "pending"
        save_record(svc, record)
        raise
    part["status"] = "pending"
    save_record(svc, record)
    if svc.db is not None:
        svc.db.record_submitted(job_id=job_id, job=job.to_wire(), origin=job.origin, label=job.label,
                                created_utc=svc.clock.stamp(), submitted_utc=svc.clock.stamp())
    svc.store.dispatch(E.JobSubmitted(job_id=job_id, label=job.label, at=svc.clock.now()))
    return job_id


def _await_part(svc: Any, job: Job, token: Any,
                on_result: Callable[[JobResult], None] | None = None) -> JobResult:
    while True:
        if getattr(token, "cancelled", False):
            return JobResult.queued(job.job_id, job.label)
        result = svc.transport.await_result(job.job_id, timeout=FOLLOW_SECONDS)
        if result.outcome.is_terminal:
            return result
        recorded = next((view.result for view in svc.store.snapshot().jobs.history
                         if view.job_id == job.job_id and view.result is not None), None)
        if recorded is not None and recorded.outcome is ApplyOutcome.EXPIRED:
            return recorded
        svc.executor.on_ui_thread(lambda result=result: svc.store.dispatch(E.JobProgressed(job_id=job.job_id, result=result)))
        if on_result is not None:
            svc.executor.on_ui_thread(lambda result=result: on_result(result))


def _finish_part(svc: Any, job: Job, result: JobResult) -> None:
    def publish() -> None:
        svc.store.dispatch(E.JobFinished(job_id=job.job_id, result=result))
        _persist_result(svc, result)
        if result.outcome is ApplyOutcome.APPLIED and not job.dry_run and all(op.op == "set_fields" for op in job.ops):
            from .squad import invalidate
            invalidate(svc, reason=f"{job.label or job.job_id} applied")
    svc.executor.on_ui_thread(publish)


def _retry_part(svc: Any, part: dict[str, Any]) -> Job:
    job = job_from_wire(part["job"])
    old_result = part.get("result") or {}
    complete_ids = {str(item["id"]) for item in old_result.get("ops", ()) if item.get("ok")}
    remaining = []
    outcomes = {str(item.get("id")): item for item in old_result.get("ops", ())}
    for operation in job.ops:
        if operation.id in complete_ids:
            continue
        item = outcomes.get(operation.id) or {}
        counts = item.get("counts") or {}
        failed = _failed_field_names(old_result, operation.to_wire())
        fields = dict(operation.body.get("fields") or {})
        verified = len(fields) - len(failed)
        # Keep all fields when their development-plan mirror still failed:
        # retrying the failed database field alone would leave prior mirrors old.
        if (failed and int(counts.get("fields_written", 0)) == verified
                and not int(counts.get("growth_failed", 0)) and not int(counts.get("growth_no_plan", 0))):
            operation = replace(operation, body={**dict(operation.body),
                                "fields": {name: value for name, value in fields.items() if name in failed}})
        remaining.append(operation)
    remaining = tuple(remaining) or job.ops
    part.setdefault("attempts", []).append({"job": part["job"], "result": old_result, "status": part["status"]})
    retry = _snapshots(svc, (replace(job, ops=remaining, job_id=new_ulid()),), str(job.requires.get("save_uid") or ""))[0]
    part.update(job=retry.to_wire(), status="unsent", result={})
    return retry


def _failed_field_names(raw: Mapping[str, Any], operation: Mapping[str, Any]) -> set[str]:
    failing_ops = [str(item.get("id")) for item in raw.get("ops", ()) if not item.get("ok")]
    names = set()
    for failure in raw.get("failures", ()):
        identifier = failure.get("op_id") or failure.get("id")
        belongs = str(identifier) == str(operation["id"]) if identifier else failing_ops == [str(operation["id"])]
        if belongs and failure.get("field") in operation.get("fields", {}):
            names.add(str(failure["field"]))
    return names


def _run_record(svc: Any, record: dict[str, Any], token: Any, *, follow: bool,
                on_result: Callable[[JobResult], None] | None) -> None:
    latest = JobResult.queued(record["plan_id"], record["label"])
    for index, part in enumerate(record["parts"]):
        if part.get("status") == "applied":
            latest = JobResult.from_wire(part["result"])
            continue
        if getattr(token, "cancelled", False):
            return
        _check_source(svc, record["source"], _affected_ids(record))
        job = job_from_wire(part["job"])
        existing = svc.transport.result(job.job_id)
        if existing is not None and existing.outcome.is_terminal:
            if existing.outcome is ApplyOutcome.APPLIED:
                result = existing
            else:
                part.update(result=dict(existing.raw), status="failed")
                job = _retry_part(svc, part)
                save_record(svc, record)
                result = None
        elif part.get("status") == "failed":
            job = _retry_part(svc, part)
            save_record(svc, record)
            result = None
        else:
            result = None
        if result is None:
            if part["status"] == "unsent":
                try:
                    _submit_part(svc, record, part, job)
                except Exception as exc:
                    raise ApplyError(f"Squad plan stopped at part {index + 1} of {len(record['parts'])}: {exc}") from exc
            elif part["status"] in {"pending", "submitting"}:
                queued = set(svc.transport.pending_ids()) | set(svc.transport.claimed_ids())
                if job.job_id not in queued and (existing is None or existing.outcome.is_terminal):
                    raise ApplyError(f"Part {index + 1} has no queue entry or final result. Its writes are uncertain; inspect Activity before retrying.")
            if not follow:
                return
            result = _await_part(svc, job, token, on_result)
        if not result.outcome.is_terminal:
            return
        latest = result
        part.update(result=dict(result.raw), status="applied" if result.outcome is ApplyOutcome.APPLIED else "failed")
        record.update(cursor=index + 1 if part["status"] == "applied" else index,
                      status="running" if part["status"] == "applied" else "failed",
                      error="" if part["status"] == "applied" else (result.diagnostic or _status_line(result)),
                      updated_utc=svc.clock.stamp())
        save_record(svc, record)
        _finish_part(svc, job, result)
        if part["status"] != "applied":
            _publish_parent(svc, record, result, on_result)
            return
    if not getattr(token, "cancelled", False):
        _verify_record(svc, record, token)
        if record["verification"].get("status") == "pending":
            record.update(status="awaiting_verification", updated_utc=svc.clock.stamp())
            save_record(svc, record)
        _publish_parent(svc, record, latest, on_result)


def _same_value(actual: Any, planned: Any) -> bool:
    if isinstance(planned, int):
        try:
            return int(actual) == planned
        except (TypeError, ValueError):
            return False
    return actual == planned


def _verify_record(svc: Any, record: dict[str, Any], token: Any) -> None:
    from ...core.transport.v3 import FileTransport
    supports = getattr(svc.transport, "supports_plan_readback", isinstance(svc.transport, FileTransport))
    dry_run = all(part["job"].get("dry_run") for part in record["parts"])
    verification = record["verification"]
    if not supports or dry_run:
        verification.update(status="dry_run" if dry_run else "unsupported", fields_verified=0)
        record.update(status="applied", updated_utc=svc.clock.stamp())
        save_record(svc, record)
        return
    _check_source(svc, record["source"], _affected_ids(record))
    if not verification.get("parts"):
        for table, rows in record["expected"].items():
            ids = [int(pid) for pid in rows]
            fields = sorted({field for values in rows.values() for field in values})
            for offset in range(0, len(ids), 8):
                operation = op_snapshot("squad.plan.verify", playerids=ids[offset:offset + 8], table=table,
                                        fields=sorted(set(fields) | ({"teamid"} if table == "teamplayerlinks" else set())))
                if record["source"].get("teamid"):
                    operation = replace(operation, body={**dict(operation.body), "teamid": record["source"]["teamid"]})
                job = Job(ops=(operation,), label=f"Verify squad plan · {table}", origin="ui.squad_planner.verify",
                          requires={"core_version": CORE_VERSION,
                                    **({"save_uid": record["source"]["save_uid"]} if record["source"]["save_uid"] else {})}, budget_ms=5000)
                verification["parts"].append({"job": job.to_wire(), "status": "unsent"})
        save_record(svc, record)
    observed: dict[str, dict[str, Any]] = {}
    errors = []
    for part in verification["parts"]:
        job = job_from_wire(part["job"])
        result = svc.transport.result(job.job_id)
        if part["status"] == "applied":
            result = JobResult.from_wire(part["result"])
        elif part["status"] == "failed":
            job = replace(job, job_id=new_ulid())
            part.update(job=job.to_wire(), status="unsent", result={})
            result = None
        if result is None or not result.outcome.is_terminal:
            if part["status"] == "unsent":
                _submit_part(svc, record, part, job)
            result = _await_part(svc, job, token)
        if not result.outcome.is_terminal:
            return
        part.update(result=dict(result.raw), status="applied" if result.outcome.is_success else "failed")
        save_record(svc, record)
        _finish_part(svc, job, result)
        if not result.outcome.is_success:
            errors.append(result.diagnostic or "Final read-back could not be completed.")
        for operation in result.ops:
            table = str(operation.data.get("table") or job.ops[0].body["table"])
            data = operation.data
            if data.get("save_uid") and data["save_uid"] != record["source"]["save_uid"]:
                errors.append("Final read-back belongs to a different Career save.")
                continue
            for row in data.get("rows", ()):
                if table == "teamplayerlinks" and int(row.get("teamid") or 0) != int(record["source"]["teamid"] or 0):
                    errors.append("Final shirt read-back belongs to another team.")
                    continue
                observed.setdefault(table, {})[str(row.get("playerid"))] = dict(row)
    verified = 0
    for table, players in record["expected"].items():
        for pid, fields in players.items():
            for field, planned in fields.items():
                actual = observed.get(table, {}).get(pid, {}).get(field)
                if _same_value(actual, planned):
                    verified += 1
                else:
                    errors.append(f"Player {pid}: {table}.{field} expected {planned}, read {actual}.")
    verification.update(status="failed" if errors else "verified", fields_verified=verified,
                        errors=errors, observed=observed)
    record.update(status="verification_failed" if errors else "applied", error=" ".join(errors),
                  updated_utc=svc.clock.stamp())
    save_record(svc, record)


def _publish_parent(svc: Any, record: dict[str, Any], latest: JobResult,
                    on_result: Callable[[JobResult], None] | None) -> None:
    aggregate = aggregate_result(record, latest)
    report = aggregate.raw["plan_receipt"]
    def publish() -> None:
        if record["status"] == "applied":
            verified = report["verification"] == "verified"
            svc.store.dispatch(E.StatusSet(
                f"Squad plan applied: {report['players_applied']}/{report['players_requested']} players, "
                f"{report['fields_applied']}/{report['fields_requested']} fields. "
                + ("Final read-back verified." if verified else "Final read-back unavailable.")))
        else:
            svc.store.dispatch(E.StatusSet(f"Squad plan stopped: {record.get('error') or _status_line(latest)}"))
        if on_result is not None:
            on_result(aggregate)
    svc.executor.on_ui_thread(publish)


def undo_squad_plan(svc: Any, plan_id: str, *, follow: bool = True,
                    on_result: Callable[[JobResult], None] | None = None, confirm_token: str = "") -> str:
    if (confirm_token or "").strip() != "UNDO":
        raise ApplyError("Type UNDO to restore this squad plan.")
    # Hold the original parent's lock through generation and linkage. A live
    # writer must not race its undo or overwrite the persisted undo link.
    with runner_lock(svc, plan_id):
        return _undo_locked(svc, plan_id, follow=follow, on_result=on_result)


def _undo_locked(svc: Any, plan_id: str, *, follow: bool,
                 on_result: Callable[[JobResult], None] | None) -> str:
    original = load_record(svc, plan_id)
    _check_source(svc, original["source"], _affected_ids(original))
    squad = svc.store.snapshot().squad
    if squad.stale or squad.taken is None:
        raise ApplyError("Refresh the live squad before undo so shirt collisions can be avoided.")
    queued = set(svc.transport.pending_ids()) | set(svc.transport.claimed_ids())
    if any(part["job"]["job_id"] in queued and
           (svc.transport.result(part["job"]["job_id"]) is None
            or not svc.transport.result(part["job"]["job_id"]).outcome.is_terminal)
           for part in original["parts"]):
        raise ApplyError("A squad part is still queued or running. Wait for it to finish before undo.")
    if original.get("undo_plan_id"):
        return resume_squad_plan(svc, original["undo_plan_id"], follow=follow, on_result=on_result)
    before = read_before_fields(svc, original)
    live_rows = _roster_rows(svc)
    affected = {int(pid) for rows in before.values() for pid in rows}
    plan = plan_from_rows([live_rows[pid] for pid in sorted(affected)], source_save_uid=squad.save_uid,
                          source_teamid=squad.teamid, source_snapshot_revision=squad.taken.isoformat())
    shirts = {pid: int(row["jerseynumber"]) for pid, row in live_rows.items() if row.get("jerseynumber") not in (None, "")}
    plan = replace(plan, shirt_book=shirts).with_overrides(
        {int(pid): fields for pid, fields in before.get("players", {}).items()}, source="undo",
        label=f"Undo squad plan: {original['label']}",
        jersey_numbers={int(pid): int(fields["jerseynumber"]) for pid, fields in before.get("teamplayerlinks", {}).items()
                        if "jerseynumber" in fields})
    jobs = _snapshots(svc, build_apply_jobs(plan, label=plan.label), plan.source_save_uid)
    record = make_record(svc, plan, jobs, kind="undo", undo_of=plan_id)
    save_record(svc, record)
    original["undo_plan_id"] = record["plan_id"]
    save_record(svc, original)
    _start_record(svc, record["plan_id"], follow=follow, on_result=on_result)
    return str(record["plan_id"])
