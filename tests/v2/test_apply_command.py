"""The apply pipeline, end to end, with no game and no files.

This is the test v1 could not write: ``FakeTransport`` + ``InlineExecutor``
means the whole flow runs synchronously in-process.
"""

from __future__ import annotations

import pytest

from companion.app import events as E
from companion.app.commands.apply import (
    ApplyError,
    apply_editor,
    apply_fields,
    collect_finished,
    refresh_liveness,
    restore_snapshot,
    submit_job,
)
from companion.app.commands.queue import clear_all
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.ids import new_ulid
from companion.domain.job import Grants, Job, job_ping, op_create_player
from companion.domain.outcome import ApplyOutcome, JobResult


@pytest.fixture
def svc(tmp_path):
    clock = FakeClock()
    store = Store()
    store.dispatch(
        E.SquadSynced(
            save_uid="SAVE-TEST",
            teamid=1,
            players=(),
            taken=clock.now(),
        )
    )
    return Services(
        paths=TempAppPaths(tmp_path),
        clock=clock,
        executor=InlineExecutor(),
        transport=FakeTransport(),
        store=store,
    )


def test_submit_dispatches_job_submitted(svc):
    jid = submit_job(svc, job_ping(), follow=False)
    assert jid in svc.store.snapshot().jobs.active
    assert svc.store.snapshot().jobs.in_flight == 1


def test_apply_fields_builds_the_right_job(svc):
    jid = apply_fields(svc, 158023, {"overallrating": 94}, names={"surname": "Messi"},
                       follow=False)
    job = svc.transport.job(jid)
    assert job is not None
    wire = job.to_wire()
    assert wire["ops"][0]["op"] == "set_fields"
    assert wire["ops"][0]["fields"] == {"overallrating": 94}
    assert wire["ops"][0]["growth_mirror"] == "auto"  # mirror on by default
    assert wire["requires"]["save_uid"] == "SAVE-TEST"
    assert wire["ops"][1]["table"] == "editedplayernames"
    assert wire["snapshot_to"].endswith(f"{jid}.json")
    assert str(svc.paths.root.resolve()) in wire["snapshot_to"]


def test_dry_run_does_not_claim_an_undo_snapshot(svc):
    jid = apply_fields(svc, 158023, {"overallrating": 94}, dry_run=True, follow=False)
    assert "snapshot_to" not in svc.transport.job(jid).to_wire()


def test_restore_last_snapshot_builds_verified_set_fields_job(svc):
    db = Database(svc.paths.state_db)
    with_db = Services(
        paths=svc.paths,
        clock=svc.clock,
        executor=svc.executor,
        transport=svc.transport,
        store=svc.store,
        db=db,
    )
    db.put_snapshot(
        id="undo-one",
        ts=1.0,
        target_id=158023,
        label="Before ratings edit",
        fields=[
            {
                "table": "players",
                "key": {"field": "playerid", "value": 158023},
                "f": "overallrating",
                "v": 91,
            }
        ],
        is_last_apply=True,
    )
    jid = restore_snapshot(with_db, follow=False)
    wire = svc.transport.job(jid).to_wire()
    assert wire["origin"] == "ui.activity.undo"
    assert wire["ops"][0]["op"] == "set_fields"
    assert wire["ops"][0]["fields"] == {"overallrating": 91}
    assert wire["snapshot_to"].endswith(f"{jid}.json")
    db.close()


def test_growth_mirror_follows_prefs(svc):
    svc.store.dispatch(E.PrefsLoaded({"growth_mirror": False}))
    jid = apply_fields(svc, 1, {"finishing": 90}, follow=False)
    assert svc.transport.job(jid).to_wire()["ops"][0]["growth_mirror"] == "off"


def test_apply_editor_uses_only_dirty_fields(svc):
    svc.store.dispatch(E.TargetLocked(playerid=158023, name="Messi"))
    svc.store.dispatch(E.EditorLoaded(base={"overallrating": 91, "pace": 80}))
    svc.store.dispatch(E.FieldEdited("overallrating", 94))
    jid = apply_editor(svc)
    fields = svc.transport.job(jid).to_wire()["ops"][0]["fields"]
    assert fields == {"overallrating": 94}  # untouched `pace` is not written


def test_verified_editor_apply_promotes_patch_to_base(svc):
    svc.store.dispatch(E.TargetLocked(playerid=158023, name="Messi"))
    svc.store.dispatch(E.EditorLoaded(base={"overallrating": 91}))
    svc.store.dispatch(E.FieldEdited("overallrating", 94))
    svc.transport.on_submit = lambda job: svc.transport.complete(
        job.job_id,
        {
            "state": "done",
            "ok": True,
            "counts": {"fields_written": 1, "ops_total": 1, "ops_ok": 1},
        },
    )
    apply_editor(svc)
    assert svc.store.snapshot().editor.base["overallrating"] == 94
    assert svc.store.snapshot().editor.dirty == {}


def test_apply_editor_requires_target_and_changes(svc):
    with pytest.raises(ApplyError, match="Pick a player"):
        apply_editor(svc)
    svc.store.dispatch(E.TargetLocked(playerid=1, name="x"))
    with pytest.raises(ApplyError, match="No changes"):
        apply_editor(svc)


def test_invalid_job_raises_before_reaching_transport(svc):
    bad = Job(ops=(op_create_player("mk"),))  # missing grant
    with pytest.raises(Exception):
        submit_job(svc, bad)
    assert svc.transport.submitted == []


def test_follow_publishes_terminal_result(svc):
    ft: FakeTransport = svc.transport
    jid_holder = {}

    def on_submit(job: Job) -> None:
        # The core "responds" the instant the job lands.
        ft.complete(
            job.job_id,
            {"state": "done", "ok": True,
             "counts": {"fields_written": 2, "ops_total": 1, "ops_ok": 1}},
        )
        jid_holder["id"] = job.job_id

    ft.on_submit = on_submit
    jid = apply_fields(svc, 158023, {"overallrating": 94})
    snap = svc.store.snapshot()
    assert snap.jobs.active == {}
    assert snap.jobs.history[0].outcome is ApplyOutcome.APPLIED
    assert "Applied" in snap.status


def test_no_op_status_is_honest(svc):
    ft: FakeTransport = svc.transport
    ft.on_submit = lambda job: ft.complete(
        job.job_id,
        {"state": "done", "ok": False, "counts": {"found": 0, "fields_written": 0}},
    )
    apply_fields(svc, 499998, {"overallrating": 94})
    snap = svc.store.snapshot()
    assert snap.jobs.history[0].outcome is ApplyOutcome.NO_OP
    assert "not found" in snap.status.lower()


def test_partial_status_names_the_count(svc):
    ft: FakeTransport = svc.transport
    ft.on_submit = lambda job: ft.complete(
        job.job_id,
        {"state": "done", "ok": False,
         "counts": {"found": 1, "fields_written": 88, "fields_failed": 3}},
    )
    apply_fields(svc, 158023, {"overallrating": 94})
    assert "3 field(s) failed" in svc.store.snapshot().status


def test_failed_job_does_not_crash_its_own_error_handler(svc):
    """v1's 13-instance bug: `except Exception as e` referenced in a deferred
    callback, so every failed apply lost its message and stuck the button."""
    ft: FakeTransport = svc.transport
    msg = "field.lua:63: attempt to perform bitwise operation on a nil value"
    ft.on_submit = lambda job: ft.complete(
        job.job_id,
        {"state": "failed", "ok": False, "error": {"message": msg}},
    )
    apply_fields(svc, 158023, {"overallrating": 94})
    snap = svc.store.snapshot()
    assert snap.jobs.history[0].outcome is ApplyOutcome.FAILED
    assert msg in snap.status  # the real Lua text survived to the UI


def test_timeout_leaves_job_queued_not_failed(svc):
    """No result ever arrives: the job stays QUEUED, which is not a failure."""
    jid = apply_fields(svc, 158023, {"overallrating": 94})
    view = svc.store.snapshot().jobs.active.get(jid)
    assert view is not None and view.outcome is ApplyOutcome.QUEUED


def test_refresh_liveness_publishes(svc):
    svc.transport.set_liveness(
        Liveness(pill=Pill.STALLED, message="stalled", armed=True, queue_depth=3)
    )
    refresh_liveness(svc)
    assert svc.store.snapshot().bridge.pill is Pill.STALLED


def test_collect_finished_sweeps_late_results(svc):
    ft: FakeTransport = svc.transport
    jid = submit_job(svc, job_ping(), follow=False)
    ft.complete_ok(jid)
    done = collect_finished(svc)
    assert len(done) == 1
    assert svc.store.snapshot().jobs.active == {}


def _with_db(svc):
    db = Database(svc.paths.state_db)
    return Services(
        paths=svc.paths,
        clock=svc.clock,
        executor=svc.executor,
        transport=svc.transport,
        store=svc.store,
        db=db,
    )


def test_clear_waiting_queue_persists_a_terminal_cancellation(svc):
    with_db = _with_db(svc)
    jid = submit_job(with_db, job_ping(), follow=False)
    assert clear_all(with_db) == 1
    record = with_db.db.job_record(jid)
    assert record is not None and record.outcome is ApplyOutcome.EXPIRED
    assert svc.store.snapshot().jobs.active == {}
    assert svc.transport.pending_ids() == []
    with_db.db.close()


def test_reconcile_missing_old_queue_record_does_not_return_as_active(svc):
    with_db = _with_db(svc)
    jid = submit_job(with_db, job_ping(), follow=False)
    svc.transport.submitted.clear()  # prior unsafe clear / app crash removed the file
    svc.clock.advance(61)
    collect_finished(with_db)
    record = with_db.db.job_record(jid)
    assert record is not None and record.outcome is ApplyOutcome.EXPIRED
    assert jid not in svc.store.snapshot().jobs.active
    with_db.db.close()


def test_reconcile_missing_record_keeps_a_raced_real_terminal_result(svc):
    with_db = _with_db(svc)
    jid = submit_job(with_db, job_ping(), follow=False)
    svc.transport.complete_ok(jid)
    svc.transport.submitted.clear()
    svc.clock.advance(61)
    collect_finished(with_db)
    record = with_db.db.job_record(jid)
    assert record is not None and record.outcome is ApplyOutcome.APPLIED
    with_db.db.close()
