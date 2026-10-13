"""Restart, identity, full rollback, and final-readback squad contracts."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path

import pytest

from companion.app import events as E
from companion import CORE_VERSION
from companion.app.commands.apply import ApplyError
from companion.app.commands.squad_plan import (
    apply_squad_plan, list_squad_plans, resume_squad_plan, squad_plan_receipt, undo_squad_plan,
)
from companion.app.commands.squad_plan_recovery import load_record, record_path, save_record
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.jobfile import atomic_write_json
from companion.domain.outcome import ApplyOutcome
from companion.domain.squad_plan import build_apply_jobs, plan_from_rows


class Backend:
    def __init__(self, count=20):
        self.players = {pid: {"playerid": pid, "name": f"Player {pid}", "position": "CM",
                             "overallrating": 80, "potential": 85, "skillmoves": 1}
                        for pid in range(1, count + 1)}
        self.shirts = {pid: pid for pid in self.players}
        self.national_shirts = {pid: 99 - pid for pid in self.players}
        self.results = {}
        self.write_jobs = []
        self.fail_write_part = None
        self.partial_failure = False
        self.read_mismatch = False

    def roster(self):
        return tuple({**deepcopy(row), "teamid": 10, "jerseynumber": self.shirts[pid]}
                     for pid, row in self.players.items())


class Worker(FakeTransport):
    supports_plan_readback = True

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self._results = backend.results
        self.on_submit = self.execute

    def execute(self, job):
        backend = self.backend
        assert job.requires["core_version"] == CORE_VERSION
        if job.ops[0].op == "snapshot":
            operation = job.ops[0]
            table = operation.body["table"]
            rows = []
            for pid in operation.body["playerids"]:
                if table == "players":
                    row = {key: backend.players[pid].get(key) for key in operation.body["fields"]}
                    if backend.read_mismatch and pid == 1:
                        row["skillmoves"] = 2
                else:
                    assert operation.body["teamid"] == 10
                    row = {"jerseynumber": backend.shirts[pid], "teamid": 10}
                rows.append({"playerid": pid, **row})
            self.complete(job.job_id, {"state": "done", "ok": True, "outcome": "applied",
                "ops": [{"id": operation.id, "op": "snapshot", "ok": True, "counts": {"targets": len(rows)},
                         "data": {"table": table, "save_uid": "SAVE", "rows": rows}}]})
            return
        backend.write_jobs.append(job)
        assert job.requires["save_uid"] == "SAVE"
        assert job.snapshot_to
        snapshot_rows = []
        for operation in job.ops:
            pid, table = operation.body["key"]["value"], operation.body["table"]
            assert operation.body["teamid"] == 10
            values = backend.players[pid] if table == "players" else {"jerseynumber": backend.shirts[pid]}
            snapshot_rows.append({"table": table, "key": operation.body["key"], "existed": True,
                                  "fields": {name: values[name] for name in operation.body["fields"]},
                                  **({"teamid": 10} if table == "teamplayerlinks" else {})})
        atomic_write_json(Path(job.snapshot_to),
                          {"job_id": job.job_id, "save_uid": "SAVE", "rows": snapshot_rows})
        should_fail = len(backend.write_jobs) == backend.fail_write_part
        ops, written = [], 0
        for index, operation in enumerate(job.ops):
            fail_here = should_fail and (not backend.partial_failure or index == 1)
            fields = operation.body["fields"]
            if fail_here:
                ops.append({"id": operation.id, "op": "set_fields", "ok": False,
                            "counts": {"fields_requested": len(fields), "fields_failed": len(fields)}})
                break
            pid, table = operation.body["key"]["value"], operation.body["table"]
            if table == "players":
                backend.players[pid].update(fields)
            else:
                number = fields["jerseynumber"]
                assert not any(other != pid and shirt == number for other, shirt in backend.shirts.items())
                backend.shirts[pid] = number
                assert operation.body.get("displace") is False
            written += len(fields)
            ops.append({"id": operation.id, "op": "set_fields", "ok": True,
                        "counts": {"fields_requested": len(fields), "fields_written": len(fields)}})
        self.complete(job.job_id, {"state": "partial" if should_fail and written else "failed" if should_fail else "done",
            "outcome": "partial" if should_fail and written else "failed" if should_fail else "applied", "ok": not should_fail,
            "counts": {"fields_written": written, "fields_failed": 1 if should_fail else 0}, "ops": ops,
            "diagnostic": "Mock middle-part failure" if should_fail else ""})


def _service(tmp_path, backend, clock=None):
    clock = clock or FakeClock()
    store = Store()
    store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=10, players=backend.roster(), taken=clock.now()))
    return Services(paths=TempAppPaths(tmp_path), store=store, clock=clock,
                    transport=Worker(backend), executor=InlineExecutor())


def _plan(svc, *, shirts=True):
    squad = svc.store.snapshot().squad
    plan = plan_from_rows(squad.players).bind_source(squad.save_uid, squad.teamid, squad.taken.isoformat())
    return replace(plan, shirt_book={row["playerid"]: row["jerseynumber"] for row in squad.players}).with_overrides(
        {pid: {"skillmoves": 4, "overallrating": 90} for pid in plan.playerids},
        label="Full team", jersey_numbers={pid: pid % len(plan.members) + 1 for pid in plan.playerids} if shirts else {})


def _refresh(svc, backend):
    svc.clock.advance(1)
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=10, players=backend.roster(), taken=svc.clock.now()))


def test_failed_middle_part_restarts_without_replaying_success_and_verifies_all(tmp_path):
    backend = Backend()
    backend.fail_write_part = 2
    svc = _service(tmp_path, backend)
    plan = _plan(svc)
    results = []
    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY", on_result=results.append)
    first_id = backend.write_jobs[0].job_id
    assert len(backend.write_jobs) == 2
    assert results[-1].outcome is ApplyOutcome.FAILED
    record = load_record(svc, plan_id)
    assert record["cursor"] == 1
    assert [part["status"] for part in record["parts"]][:3] == ["applied", "failed", "unsent"]
    backend.fail_write_part = None
    restarted = _service(tmp_path, backend, svc.clock)
    resume_squad_plan(restarted, plan_id, on_result=results.append)
    assert sum(job.job_id == first_id for job in backend.write_jobs) == 1
    report = squad_plan_receipt(restarted, plan_id)
    assert report["status"] == "applied"
    assert report["players_applied"] == report["players_requested"] == 20
    assert report["fields_applied"] == report["fields_requested"] == report["fields_verified"] == 60
    assert report["verification"] == "verified"
    assert all(row["verified"] for row in report["rows"])
    assert all(part["job"]["snapshot_to"] for part in load_record(restarted, plan_id)["parts"])
    assert results[-1].job_id == plan_id
    assert results[-1].counts["fields_written"] == 60
    assert list_squad_plans(restarted)[0]["plan_id"] == plan_id


def test_partial_part_retry_skips_successful_operations_and_keeps_full_receipt(tmp_path):
    backend = Backend()
    backend.fail_write_part, backend.partial_failure = 2, True
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), confirm_token="APPLY")
    successful = backend.write_jobs[1].ops[0]
    backend.fail_write_part = None
    resume_squad_plan(_service(tmp_path, backend, svc.clock), plan_id)
    assert successful.id not in {operation.id for operation in backend.write_jobs[2].ops}
    report = squad_plan_receipt(svc, plan_id)
    assert report["players_applied"] == 20
    assert report["fields_applied"] == report["fields_verified"] == 40


def test_complete_undo_restores_every_player_and_safe_club_shirts(tmp_path):
    backend = Backend()
    original_players, original_shirts, national = deepcopy(backend.players), dict(backend.shirts), dict(backend.national_shirts)
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc), confirm_token="APPLY")
    _refresh(svc, backend)
    undo_id = undo_squad_plan(svc, plan_id, confirm_token="UNDO")
    assert backend.players == original_players
    assert backend.shirts == original_shirts
    assert backend.national_shirts == national
    assert squad_plan_receipt(svc, undo_id)["fields_verified"] == 60
    assert squad_plan_receipt(svc, plan_id)["undo_plan_id"] == undo_id
    with pytest.raises(ApplyError, match="undo"):
        resume_squad_plan(svc, plan_id)


@pytest.mark.parametrize("change", ["save", "team", "unbound"])
def test_original_identity_and_membership_block_before_queue(tmp_path, change):
    backend, svc = Backend(3), None
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    if change == "unbound":
        plan = replace(plan, source_save_uid="", source_teamid=None, source_snapshot_revision="")
    else:
        rows = backend.roster()
        if change == "membership":
            rows = rows[1:]
        elif change == "material_refresh":
            backend.players[1]["overallrating"] = 81
            rows = backend.roster()
            svc.clock.advance(1)
        svc.store.dispatch(E.SquadSynced(save_uid="OTHER" if change == "save" else "SAVE",
                                        teamid=11 if change == "team" else 10, players=rows, taken=svc.clock.now()))
    with pytest.raises(ApplyError):
        apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert not svc.transport.submitted


def test_periodic_unchanged_squad_refresh_preserves_valid_original_plan(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    _refresh(svc, backend)
    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert squad_plan_receipt(svc, plan_id)["verification"] == "verified"
    assert load_record(svc, plan_id)["source"]["snapshot_revision"] == plan.source_snapshot_revision


def test_refresh_display_metadata_cannot_block_an_unchanged_player(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    # Reproduce the old detailed snapshot's empty name plus derived metadata.
    plan = replace(plan, members=tuple(replace(member, base={**member.base, "name": "", "age": 24})
                                       for member in plan.members))
    _refresh(svc, backend)
    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert squad_plan_receipt(svc, plan_id)["verification"] == "verified"


def test_pending_part_result_after_restart_is_reconciled_without_duplicate_queue(tmp_path):
    backend = Backend()
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), follow=False, confirm_token="APPLY")
    assert len(backend.write_jobs) == 1
    assert load_record(svc, plan_id)["parts"][0]["status"] == "pending"
    resume_squad_plan(_service(tmp_path, backend, svc.clock), plan_id)
    assert len(backend.write_jobs) == 3
    assert squad_plan_receipt(svc, plan_id)["fields_verified"] == 40


def test_final_mismatch_is_partial_and_verify_again_never_replays_writes(tmp_path):
    backend = Backend(3)
    backend.read_mismatch = True
    svc = _service(tmp_path, backend)
    results = []
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), confirm_token="APPLY", on_result=results.append)
    assert results[-1].outcome is ApplyOutcome.PARTIAL
    report = squad_plan_receipt(svc, plan_id)
    assert report["status"] == "verification_failed"
    assert any("skillmoves" in message for message in report["verification_errors"])
    backend.read_mismatch = False
    resume_squad_plan(svc, plan_id, on_result=results.append)
    assert len(backend.write_jobs) == 1
    assert squad_plan_receipt(svc, plan_id)["verification"] == "verified"
    assert results[-1].outcome is ApplyOutcome.APPLIED


def test_live_runner_lock_does_not_corrupt_running_parent(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), follow=False, confirm_token="APPLY")
    before = load_record(svc, plan_id)
    record_path(svc, plan_id).with_suffix(".lock").write_text(str(os.getpid()), encoding="ascii")
    try:
        resume_squad_plan(svc, plan_id)
        assert load_record(svc, plan_id) == before
        assert len(backend.write_jobs) == 1
    finally:
        record_path(svc, plan_id).with_suffix(".lock").unlink()


def test_full_selection_above_twenty_five_is_kept_and_writes_remain_bounded(tmp_path):
    backend = Backend(34)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    assert plan.player_count == 34
    assert all(len(job.ops) <= 8 for job in build_apply_jobs(plan))
    plan_id = apply_squad_plan(svc, plan, confirm_token="APPLY")
    assert squad_plan_receipt(svc, plan_id)["players_applied"] == 34


def test_undo_missing_part_snapshot_and_wrong_save_are_rejected(tmp_path):
    backend = Backend()
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc), confirm_token="APPLY")
    _refresh(svc, backend)
    svc.store.dispatch(E.SquadSynced(save_uid="OTHER", teamid=10, players=backend.roster(), taken=svc.clock.now()))
    with pytest.raises(ApplyError, match="save changed"):
        undo_squad_plan(svc, plan_id, confirm_token="UNDO")
    _refresh(svc, backend)
    Path(load_record(svc, plan_id)["parts"][1]["job"]["snapshot_to"]).unlink()
    with pytest.raises(ApplyError, match="snapshot is missing"):
        undo_squad_plan(svc, plan_id, confirm_token="UNDO")


def test_undo_refuses_live_original_runner_and_does_not_erase_progress(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), confirm_token="APPLY")
    _refresh(svc, backend)
    lock = record_path(svc, plan_id).with_suffix(".lock")
    lock.write_text(str(os.getpid()), encoding="ascii")
    before = load_record(svc, plan_id)
    try:
        with pytest.raises(ApplyError, match="already running"):
            undo_squad_plan(svc, plan_id, confirm_token="UNDO")
        assert load_record(svc, plan_id) == before
        assert len(backend.write_jobs) == 1
    finally:
        lock.unlink()


def test_undo_refuses_pending_original_job_even_when_no_follower_is_alive(tmp_path):
    backend = Backend(3)
    svc = replace(_service(tmp_path, backend), transport=FakeTransport())
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), follow=False, confirm_token="APPLY")
    with pytest.raises(ApplyError, match="still queued"):
        undo_squad_plan(svc, plan_id, confirm_token="UNDO")
    assert len(svc.transport.submitted) == 1


def test_new_empty_runner_lock_is_busy_until_its_owner_is_published(tmp_path):
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), follow=False, confirm_token="APPLY")
    lock = record_path(svc, plan_id).with_suffix(".lock")
    lock.write_text("", encoding="ascii")
    before = load_record(svc, plan_id)
    try:
        resume_squad_plan(svc, plan_id)
        assert load_record(svc, plan_id) == before
        assert lock.exists()
    finally:
        lock.unlink()


def test_cancelled_final_readback_does_not_publish_applied_and_can_resume(tmp_path):
    from companion.core.executor import _Token
    from companion.domain.outcome import JobResult

    class Executor(InlineExecutor):
        def submit(self, key, fn):
            self.token = _Token()
            return fn(self.token)

    backend = Backend(3)
    executor = Executor()
    svc = replace(_service(tmp_path, backend), executor=executor)
    transport = svc.transport
    execute = transport.execute
    transport.on_submit = lambda job: None if job.ops[0].op == "snapshot" else execute(job)
    await_original = transport.await_result

    def follow(job_id, **kwargs):
        if transport.job(job_id).ops[0].op == "snapshot":
            executor.token.cancel()
            return JobResult.queued(job_id)
        return await_original(job_id, **kwargs)

    transport.await_result = follow
    results = []
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), confirm_token="APPLY", on_result=results.append)
    assert results[-1].outcome is ApplyOutcome.QUEUED
    assert squad_plan_receipt(svc, plan_id)["status"] == "awaiting_verification"
    assert len(backend.write_jobs) == 1
    record = load_record(svc, plan_id)
    pending_read = record["verification"]["parts"][0]["job"]["job_id"]
    execute(transport.job(pending_read))
    transport.await_result = await_original
    transport.on_submit = execute
    resume_squad_plan(svc, plan_id, on_result=results.append)
    assert results[-1].outcome is ApplyOutcome.APPLIED
    assert len(backend.write_jobs) == 1
    assert squad_plan_receipt(svc, plan_id)["fields_verified"] == 6


def test_failed_enqueue_stays_unsent_then_restart_retries_only_remaining_parts(tmp_path):
    backend = Backend()
    svc = _service(tmp_path, backend)
    original_submit = svc.transport.submit

    def enqueue(job):
        if len(backend.write_jobs) == 1:
            raise OSError("Mock locked queue")
        return original_submit(job)

    svc.transport.submit = enqueue
    plan_id = apply_squad_plan(svc, _plan(svc, shirts=False), confirm_token="APPLY")
    record = load_record(svc, plan_id)
    assert record["parts"][0]["status"] == "applied"
    assert record["parts"][1]["status"] == "unsent"
    assert "Mock locked queue" in record["error"]
    restarted = _service(tmp_path, backend, svc.clock)
    resume_squad_plan(restarted, plan_id)
    assert len(backend.write_jobs) == 3
    assert squad_plan_receipt(restarted, plan_id)["fields_verified"] == 40


@pytest.mark.parametrize("identify_field", [False, True])
def test_partial_field_receipt_and_retry_do_not_hide_verified_writes(tmp_path, identify_field):
    from companion.app.commands.squad_plan_recovery import _retry_part, make_record, receipt
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    record = make_record(svc, plan, build_apply_jobs(plan))
    part = record["parts"][0]
    operation = part["job"]["ops"][0]
    raw = {"job_id": part["job"]["job_id"], "state": "partial", "ok": False,
           "counts": {"fields_written": 1, "fields_failed": 1},
           "ops": [{"id": operation["id"], "op": "set_fields", "ok": False,
                    "counts": {"fields_written": 1, "fields_failed": 1}}],
           "failures": [{"field": "overallrating", "reason": "mock"}] if identify_field else []}
    part.update(status="failed", result=raw)
    report = receipt(record)
    assert report["fields_applied"] == 1
    assert report["players_applied"] == 1
    assert report["applied_fields_unidentified"] == (0 if identify_field else 1)
    retry = _retry_part(svc, part)
    assert retry.ops[0].body["fields"] == ({"overallrating": 90} if identify_field else {"skillmoves": 4, "overallrating": 90})


def test_growth_mirror_failure_keeps_verified_database_fields_visible_for_undo(tmp_path):
    from companion.app.commands.squad_plan_recovery import _retry_part, make_record, receipt
    backend = Backend(3)
    svc = _service(tmp_path, backend)
    plan = _plan(svc, shirts=False)
    record = make_record(svc, plan, build_apply_jobs(plan))
    part = record["parts"][0]
    operation = part["job"]["ops"][0]
    part.update(status="failed", result={"job_id": part["job"]["job_id"], "state": "partial", "ok": False,
                "counts": {"fields_written": 2, "growth_failed": 2},
                "ops": [{"id": operation["id"], "op": "set_fields", "ok": False,
                         "counts": {"fields_written": 2, "growth_failed": 2}}]})
    report = receipt(record)
    assert report["players_applied"] == 1
    assert report["fields_applied"] == 2
    assert report["applied_fields_unidentified"] == 0
    assert _retry_part(svc, part).ops[0].body["fields"] == operation["fields"]
