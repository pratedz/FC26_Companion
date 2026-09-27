"""Regressions from real shirt + PlayStyle plan failures; no game writes."""
from dataclasses import replace
import json

import pytest

from companion.app import events as E
from companion.app.commands.apply import collect_finished, FOLLOW_SECONDS, reconcile_recorded_worker_results
from companion.app.commands.squad_plan import apply_squad_plan
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.jobfile import atomic_write_json, read_json, read_worker_json
from companion.core.transport.v3 import FileTransport
from companion.domain.ids import new_ulid
from companion.domain.outcome import ApplyOutcome, JobResult
from companion.domain.squad_plan import build_apply_jobs, plan_from_rows, SHIRT_OPS_PER_JOB
from companion.domain.squad_session import (
    build_session, jersey_only, parse_recipe, roster_from_members, stage_per_player_plan,
)

PROMPT = "arrange these players jersey no by recommendation and improve playstyle+ no limit ceiling"


def selected_plan(count):
    return plan_from_rows([
        {"id": i, "name": f"Player {i}", "pos": "GK" if i == 1 else "CM", "_raw": {
            "playerid": i, "name": f"Player {i}", "position": "GK" if i == 1 else "CM",
            "overallrating": 80, "potential": 84, "teamid": 10, "jerseynumber": i,
            "icontrait1": 0, "icontrait2": 0,
        }} for i in range(1, count + 1)
    ])


def staged_plan(count, *, unlimited=True, bad_player=None):
    plan = selected_plan(count)
    recipe = replace(parse_recipe(PROMPT), unlimited_playstyles=unlimited)
    session = build_session(roster_from_members(plan.members), recipe)
    patches = {i: {"jerseynumber": i % count + 1, "icontrait1": 255, "icontrait2": 15}
               for i in plan.playerids}
    if bad_player is not None:
        patches[bad_player]["not_an_attribute"] = 1
    return stage_per_player_plan(plan, session, patches), session


@pytest.mark.parametrize("count", [20, 21, 22])
def test_combined_shirts_and_no_ceiling_reach_every_selected_players_apply(count):
    staged, session = staged_plan(count)
    assert session.playerids == staged.playerids == tuple(range(1, count + 1))
    assert session.targets[0].is_gk
    assert len(staged.overrides) == count
    preview = staged.matrix_rows()
    assert sum(r["field"] == "icontrait1" and r["after"] == 255 for r in preview) == count
    jobs = build_apply_jobs(staged)
    attrs = [op.to_wire() for job in jobs for op in job.ops if op.body["table"] == "players"]
    assert {op["key"]["value"] for op in attrs} == set(staged.playerids)
    assert all(op["fields"]["icontrait1"] == 255 and op["fields"]["icontrait2"] == 15 for op in attrs)
    worn = {i: i for i in staged.playerids}
    for job in jobs:
        job.validate()
        assert job.budget_ms <= 5000
        shirts = [op for op in job.ops if op.body["table"] == "teamplayerlinks"]
        assert len(shirts) <= SHIRT_OPS_PER_JOB
        for op in shirts:
            pid, number = op.body["key"]["value"], op.body["fields"]["jerseynumber"]
            assert not any(other != pid and shirt == number for other, shirt in worn.items())
            worn[pid] = number
    assert all(worn[pid] == number for pid, number in staged.jersey_numbers.items())


def test_ceiling_switch_caps_only_playstyle_bits_and_keeps_shirt_validation():
    staged, _ = staged_plan(20, unlimited=False)
    assert all(p["icontrait1"].bit_count() + p["icontrait2"].bit_count() == 2
               for p in staged.overrides.values())
    assert staged.jersey_numbers


def test_bad_player_does_not_drop_the_other_nineteen_or_authorize_unknown_id():
    staged, session = staged_plan(20, bad_player=5)
    assert 5 not in staged.overrides
    assert len(staged.overrides) == 19
    assert any("Player 5:" in note and "not_an_attribute" in note for note in staged.warnings)
    plan = selected_plan(20)
    safe = stage_per_player_plan(plan, session, {99999: {"jerseynumber": 8, "icontrait1": 255}})
    assert 99999 not in safe.overrides and 99999 not in safe.jersey_numbers
    assert any("unselected player" in note for note in safe.warnings)


def test_explicit_keeper_exclusion_and_combined_named_shirt_request():
    plan = selected_plan(20)
    session = build_session(roster_from_members(plan.members), parse_recipe(PROMPT + "; skip keepers"))
    assert 1 not in session.playerids
    assert not jersey_only(parse_recipe("Player One jersey 10 and improve playstyle+ no limit ceiling"))


def success(job):
    counts = {"ops_total": len(job.ops), "ops_ok": len(job.ops), "ops_failed": 0}
    return {"schema": 3, "job_id": job.job_id, "state": "done", "outcome": "applied", "ok": True,
            "counts": counts, "ops": [{"id": op.id, "op": op.op, "ok": True,
                "counts": {"fields_written": len(op.body.get("fields", {})),
                           "fields_requested": len(op.body.get("fields", {}))}} for op in job.ops]}


def service(tmp_path, transport, clock):
    paths = TempAppPaths(tmp_path)
    store = Store()
    store.dispatch(E.SquadSynced(save_uid="SAVE", teamid=10, players=(), taken=clock.now()))
    return Services(paths=paths, store=store, clock=clock, transport=transport, executor=InlineExecutor())


def test_part_two_runs_after_premature_crash_is_replaced_by_applied(tmp_path, monkeypatch):
    paths, clock = TempAppPaths(tmp_path), FakeClock()
    monkeypatch.setattr("companion.core.transport.v3._host_process_running", lambda: True)
    atomic_write_json(paths.session_file, {"session_id": "LIVE", "le_pid": 0})
    submitted, observed = [], []

    class Worker(FileTransport):
        def submit(self, job):
            if submitted:
                assert self.result(submitted[-1].job_id).outcome is ApplyOutcome.APPLIED
            submitted.append(job)
            jid = super().submit(job)
            (paths.jobs / f"{jid}.json").replace(paths.claimed / f"{jid}.json")
            if len(submitted) == 1:
                atomic_write_json(paths.results / f"{jid}.json", {
                    "job_id": jid, "state": "crashed", "outcome": "crashed", "ok": False,
                    "diagnostic": "claimed by session ? which never completed it",
                })
            else:
                atomic_write_json(paths.results / f"{jid}.json", success(job))
                (paths.claimed / f"{jid}.json").unlink()
            return jid

        def result(self, jid):
            result = super().result(jid)
            if result:
                observed.append(result.outcome)
            return result

    transport = Worker(paths, clock=clock, pid_probe=lambda _pid: False)
    svc = service(tmp_path, transport, clock)
    def release(seconds):
        clock.advance(seconds)
        if submitted:
            first = submitted[0]
            atomic_write_json(paths.results / f"{first.job_id}.json", success(first))
            (paths.claimed / f"{first.job_id}.json").unlink(missing_ok=True)
    clock.sleep = release
    finished = []
    apply_squad_plan(svc, staged_plan(20)[0], confirm_token="APPLY", on_result=finished.append)
    assert len(submitted) == len(build_apply_jobs(staged_plan(20)[0])) > 1
    assert ApplyOutcome.DEFERRED in observed and ApplyOutcome.CRASHED not in observed
    assert len(finished) == 1 and finished[0].outcome is ApplyOutcome.APPLIED


def test_follow_timeout_keeps_chain_alive_and_preserves_45_second_boundary(tmp_path):
    clock, transport = FakeClock(), FakeTransport()
    calls = []
    def follow(jid, *, timeout, **_kwargs):
        calls.append((jid, timeout))
        if len(calls) == 1:
            return transport.result(jid) or JobResult.queued(jid)
        transport.complete(jid, success(transport.job(jid)))
        return transport.result(jid)
    transport.await_result = follow
    svc = service(tmp_path, transport, clock)
    finished = []
    apply_squad_plan(svc, staged_plan(22)[0], confirm_token="APPLY", on_result=finished.append)
    assert len(transport.submitted) == len(build_apply_jobs(staged_plan(22)[0]))
    assert finished[0].outcome is ApplyOutcome.QUEUED
    assert finished[-1].outcome is ApplyOutcome.APPLIED
    assert all(timeout == FOLLOW_SECONDS == 45 for _, timeout in calls)


def test_worker_path_compatibility_is_narrow_and_does_not_loosen_job_json(tmp_path):
    path = tmp_path / "result.json"
    windows = r"C:\Users\Joshua\Desktop\snapshots\job.json"
    wire = {"job_id": new_ulid(), "ok": True, "outcome": "applied", "state": "done",
            "env": {"snapshot": {"path": windows}}}
    malformed = json.dumps(wire).replace(json.dumps(windows), '"' + windows + '"')
    path.write_text(malformed, encoding="utf-8")
    assert read_json(path) is None
    assert read_worker_json(path) == wire
    path.write_text(malformed[:-1], encoding="utf-8")
    assert read_worker_json(path) is None
    path.write_text('{"name":"bad\\q"}', encoding="utf-8")
    assert read_worker_json(path) is None
    paths = TempAppPaths(tmp_path / "queue-test")
    transport = FileTransport(paths, clock=FakeClock())
    result_path = paths.results / f"{wire['job_id']}.json"
    result_path.write_text(malformed, encoding="utf-8")
    assert transport.result(wire["job_id"]).outcome is ApplyOutcome.APPLIED


def test_collector_waits_for_live_claim_instead_of_persisting_false_crash(tmp_path):
    clock, paths = FakeClock(), TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=clock, pid_probe=lambda pid: pid == 42)
    atomic_write_json(paths.session_file, {"session_id": "LIVE", "le_pid": 42})
    svc = service(tmp_path, transport, clock)
    job = build_apply_jobs(staged_plan(20)[0])[0]
    transport.submit(job)
    svc.store.dispatch(E.JobSubmitted(job_id=job.job_id, label=job.label, at=clock.now()))
    (paths.jobs / f"{job.job_id}.json").replace(paths.claimed / f"{job.job_id}.json")
    atomic_write_json(paths.results / f"{job.job_id}.json", {
        "job_id": job.job_id, "state": "crashed", "outcome": "crashed", "ok": False,
    })
    assert collect_finished(svc) == []
    assert not svc.store.snapshot().jobs.active[job.job_id].done
    atomic_write_json(paths.results / f"{job.job_id}.json", success(job))
    (paths.claimed / f"{job.job_id}.json").unlink()
    assert collect_finished(svc)[0].outcome is ApplyOutcome.APPLIED


@pytest.mark.parametrize("count", [20, 21, 22])
def test_latest_runtime_delayed_first_part_and_legacy_path_do_not_abort_chain(tmp_path, count):
    paths, clock = TempAppPaths(tmp_path), FakeClock()
    submitted = []
    class DelayedWorker(FileTransport):
        def submit(self, job):
            if submitted:
                assert self.result(submitted[-1].job_id).outcome is ApplyOutcome.APPLIED
            jid = super().submit(job)
            submitted.append(job)
            if len(submitted) > 1:
                atomic_write_json(paths.results / f"{jid}.json", success(job))
                (paths.jobs / f"{jid}.json").unlink()
            return jid
    transport = DelayedWorker(paths, clock=clock)
    svc = service(tmp_path, transport, clock)
    db = Database(paths.state_db)
    svc = replace(svc, db=db)
    released = False
    def tick(seconds):
        nonlocal released
        clock.advance(seconds)
        if clock.monotonic() >= 58 and submitted and not released:
            released = True
            job = submitted[0]
            payload = success(job)
            windows = r"C:\Users\Joshua\snapshots\before.json"
            payload["env"] = {"snapshot": {"path": windows}}
            malformed = json.dumps(payload).replace(json.dumps(windows), '"' + windows + '"')
            (paths.results / f"{job.job_id}.json").write_text(malformed, encoding="utf-8")
            (paths.jobs / f"{job.job_id}.json").unlink()
    clock.sleep = tick
    finished = []
    try:
        apply_squad_plan(svc, staged_plan(count)[0], confirm_token="APPLY", on_result=finished.append)
        assert finished[0].outcome is ApplyOutcome.QUEUED
        assert finished[-1].outcome is ApplyOutcome.APPLIED
        assert len(submitted) == len(build_apply_jobs(staged_plan(count)[0]))
        assert all(db.job_record(job.job_id).outcome is ApplyOutcome.APPLIED for job in submitted)
        assert all(job.requires["save_uid"] == "SAVE" for job in submitted)
        assert "\\" not in submitted[0].snapshot_to
    finally:
        db.close()


def test_startup_corrects_synthetic_expiry_from_real_worker_result_without_requeue(tmp_path, monkeypatch):
    paths, clock = TempAppPaths(tmp_path), FakeClock()
    transport = FileTransport(paths, clock=clock)
    db = Database(paths.state_db)
    svc = replace(service(tmp_path, transport, clock), db=db)
    job = build_apply_jobs(staged_plan(20)[0])[0]
    try:
        db.record_submitted(job_id=job.job_id, job=job.to_wire(), label=job.label,
                            origin=job.origin, created_utc=clock.stamp())
        synthetic = {"job_id": job.job_id, "outcome": "expired", "state": "expired", "ok": False,
                     "diagnostic": "Queue entry is missing after the grace period."}
        db.record_finished(job.job_id, outcome=ApplyOutcome.EXPIRED, result=synthetic)
        svc.store.dispatch(E.JobFinished(job_id=job.job_id, result=JobResult.from_wire(synthetic)))
        real = {**success(job), "finished_at": int(clock.now_ts()) + 5, "duration_ms": 1382}
        atomic_write_json(paths.results / f"{job.job_id}.json", real)
        def forbid_undo_import(*_args):
            raise AssertionError("history reconciliation must preserve Undo")
        monkeypatch.setattr("companion.app.commands.apply._persist_prewrite_snapshot", forbid_undo_import)
        assert reconcile_recorded_worker_results(svc) == 1
        assert db.job_record(job.job_id).outcome is ApplyOutcome.APPLIED
        assert db.job_record(job.job_id).exec_ms == 1382
        assert db.job_record(job.job_id).latency_ms == 5000
        assert [v.outcome for v in svc.store.snapshot().jobs.history if v.job_id == job.job_id] == [ApplyOutcome.APPLIED]
        assert transport.pending_ids() == []
        assert reconcile_recorded_worker_results(svc) == 0
    finally:
        db.close()


def test_real_part_failure_stops_chain_with_specific_reason(tmp_path):
    clock, transport = FakeClock(), FakeTransport()
    def fail(job):
        transport.complete(job.job_id, {"state": "failed", "outcome": "failed", "ok": False,
                                      "error": {"message": "wrong_save", "detail": "Career save changed"}})
    transport.on_submit = fail
    svc = service(tmp_path, transport, clock)
    finished = []
    apply_squad_plan(svc, staged_plan(20)[0], confirm_token="APPLY", on_result=finished.append)
    assert len(transport.submitted) == 1
    assert finished[0].outcome is ApplyOutcome.FAILED
    assert finished[0].error["detail"] == "Career save changed"


def test_partial_file_cannot_claim_a_terminal_verdict(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FileTransport(paths, clock=FakeClock())
    jid = new_ulid()
    atomic_write_json(paths.results / f"{jid}.partial.json", {
        "job_id": jid, "state": "done", "outcome": "applied", "ok": True,
    })
    assert transport.result(jid).outcome is ApplyOutcome.DEFERRED


def test_failure_to_queue_a_later_part_completes_ui_with_the_specific_error(tmp_path):
    clock, transport = FakeClock(), FakeTransport()
    def complete_or_raise(job):
        if len(transport.submitted) == 2:
            raise OSError("queue folder is locked")
        transport.complete(job.job_id, success(job))
    transport.on_submit = complete_or_raise
    finished = []
    apply_squad_plan(service(tmp_path, transport, clock), staged_plan(20)[0],
                     confirm_token="APPLY", on_result=finished.append)
    assert len(finished) == 1
    assert finished[0].outcome is ApplyOutcome.FAILED
    assert "part 2" in finished[0].diagnostic and "queue folder is locked" in finished[0].diagnostic


def test_propose_keeps_failed_group_warning_when_library_hints_are_returned(monkeypatch):
    from companion.ui.surfaces.planner import session_propose_worker
    plan = selected_plan(20)
    session = build_session(roster_from_members(plan.members), parse_recipe(PROMPT))
    def propose(**kwargs):
        kwargs["problems"].append("Group 2 failed: provider unavailable")
        return ({pid: {"icontrait1": 255, "icontrait2": 15} for pid in plan.playerids},
                {}, {1: "Actual Library hint"})
    monkeypatch.setattr("companion.integrations.grok.propose_squad_players", propose)
    finished = []
    session_propose_worker(type("Token", (), {"cancelled": False})(), plan=plan,
                           session=session, on_success=finished.append)
    assert finished and len(finished[0].overrides) == 20
    assert "Group 2 failed: provider unavailable" in finished[0].warnings
