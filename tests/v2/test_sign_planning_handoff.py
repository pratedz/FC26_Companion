"""Signing receipts hand off actual IDs only after a fresh matching roster."""
from dataclasses import replace

import pytest

from companion.app import events as E
from companion.app.services import Services
from companion.app.state import SigningBatch
from companion.app.store import Store
from companion.app.ui_actions import UiActions
from companion.core.clock import FakeClock
from companion.core.db import Database, JobRecord
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.outcome import ApplyOutcome, JobResult
from companion.ui.surfaces import club
from companion.ui.surfaces.sign.handoff import (
    fresh_batch_rows, latest_signing_handoff, open_signed_club, plan_signed_players,
    recently_added_ids, signing_batches, verified_batch_ids,
)


@pytest.fixture
def svc(tmp_path):
    clock = FakeClock()
    paths = TempAppPaths(tmp_path)
    result = Services(paths, clock, InlineExecutor(), FakeTransport(), Store())
    result.store.dispatch(E.SquadSynced(save_uid="SAVE", session_id="SESSION", teamid=11,
                                       players=({"playerid": 1, "name": "Existing"},), taken=clock.now()))
    return result


def receipt(svc, *, ids=(701, 702), verified=True, teamid=11, save="SAVE", job_id="sign-job",
            outcome="applied"):
    return JobResult.from_wire({
        "job_id": job_id, "state": "done", "outcome": outcome, "ok": outcome == "applied",
        "finished_at": svc.clock.now_ts(), "session_id": "SESSION", "env": {"save_uid": save},
        "counts": {"fields_written": 20, "ops_ok": 1},
        "ops": [{"id": "add", "op": "add_to_team", "ok": True,
                 "counts": {"fields_written": 20, "fields_requested": 20},
                 "data": {"verified": verified, "teamid": teamid, "playerids": list(ids)}}],
    })


def queue(svc, *, count=2, job_id="sign-job"):
    svc.store.dispatch(E.JobSubmitted(job_id, f"Sign {count}: Library cards", svc.clock.now()))
    svc.store.dispatch(E.SigningBatchQueued((job_id,), count, "SAVE", 11, "SESSION", svc.clock.now()))
    return svc.store.snapshot().signing.batches[0]


def refresh(svc, *, ids=(1, 701, 702), save="SAVE", teamid=11, taken=None):
    svc.store.dispatch(E.SquadSynced(
        save_uid=save, session_id="SESSION", teamid=teamid,
        players=tuple({"playerid": pid, "name": f"Career {pid}", "position": "CM",
                       "overallrating": 88, "teamname": "Current club"} for pid in ids),
        taken=taken or svc.clock.now(),
    ))


def test_queue_has_truthful_count_and_no_guessed_plan_ids(svc):
    batch = queue(svc, count=20)
    message, current, rows = latest_signing_handoff(svc)
    assert "20 players reviewed" in message and "waiting" in message and "0 verified" in message
    assert current == batch and not rows
    assert verified_batch_ids(batch) == () and recently_added_ids(svc) == ()


def test_verified_actual_ids_wait_for_refresh_then_plan_entire_bag(svc, monkeypatch):
    queue(svc)
    svc.clock.advance(5)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc)))
    batch = svc.store.snapshot().signing.batches[0]
    assert verified_batch_ids(batch) == (701, 702)
    assert not fresh_batch_rows(svc, batch)
    assert "Refresh" in latest_signing_handoff(svc)[0]
    refresh(svc)
    assert recently_added_ids(svc) == (701, 702)
    seen = []
    monkeypatch.setattr("companion.ui.surfaces.planner.open_planner", lambda *args, **kwargs: seen.append((args, kwargs)))
    assert plan_signed_players(svc, batch)
    rows = seen[0][0][1]
    assert [row["playerid"] for row in rows] == [701, 702]
    assert [row["name"] for row in rows] == ["Career 701", "Career 702"]
    assert seen[0][1]["mode"] == "per_player"


@pytest.mark.parametrize("kwargs", [
    {"verified": False}, {"teamid": 12}, {"teamid": "bad"}, {"save": "OTHER"},
    {"ids": (701, 701)}, {"ids": (True, 702)},
])
def test_unverified_incomplete_or_other_context_receipt_never_enables_plan(svc, kwargs):
    queue(svc)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc, **kwargs)))
    refresh(svc)
    assert not latest_signing_handoff(svc)[2]
    assert not recently_added_ids(svc)


def test_departed_players_are_excluded_and_othercareer_cannot_handoff(svc):
    queue(svc)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc)))
    batch = svc.store.snapshot().signing.batches[0]
    refresh(svc, ids=(1, 701))
    assert [row["playerid"] for row in fresh_batch_rows(svc, batch)] == [701]
    assert "1 verified players absent" in latest_signing_handoff(svc)[0]
    refresh(svc, save="OTHER")
    assert not fresh_batch_rows(svc, batch) and not latest_signing_handoff(svc)[2]
    refresh(svc, teamid=12)
    assert not fresh_batch_rows(svc, batch)


def test_stale_and_precompletion_exports_do_not_authorize_handoff(svc):
    before = svc.clock.now()
    queue(svc)
    svc.clock.advance(5)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc)))
    batch = svc.store.snapshot().signing.batches[0]
    refresh(svc, taken=before)
    assert not fresh_batch_rows(svc, batch)
    refresh(svc)
    svc.store.dispatch(E.SquadInvalidated("write pending"))
    assert not fresh_batch_rows(svc, batch)
    refresh(svc)
    svc.clock.advance(3600)
    assert not fresh_batch_rows(svc, batch)


def test_verified_subset_can_plan_while_other_signing_jobs_wait_or_fail(svc):
    svc.store.dispatch(E.SigningBatchQueued(("good", "waiting", "failed"), 3,
                                          "SAVE", 11, "SESSION", svc.clock.now()))
    svc.store.dispatch(E.JobFinished("good", receipt(svc, ids=(701,), job_id="good")))
    # No verified operation in the failed job; no target IDs may be inferred.
    failure = JobResult.from_wire({"job_id": "failed", "state": "failed", "ok": False, "ops": []})
    svc.store.dispatch(E.JobFinished("failed", failure))
    refresh(svc, ids=(1, 701))
    message, batch, rows = latest_signing_handoff(svc)
    assert "3 players reviewed" in message and "1 verified" in message and "1 signing job waiting" in message
    assert [row["playerid"] for row in rows] == [701]
    assert verified_batch_ids(batch) == (701,)
    svc.store.dispatch(E.JobFinished("waiting", replace(failure, job_id="waiting")))
    message, _, rows = latest_signing_handoff(svc)
    assert "2 player results unconfirmed" in message and len(rows) == 1


def test_partial_job_can_hand_off_individually_verified_successful_operations(svc):
    queue(svc)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc, ids=(701,), outcome="partial")))
    refresh(svc, ids=(1, 701))
    message, _, rows = latest_signing_handoff(svc)
    assert len(rows) == 1 and "1 player results unconfirmed" in message


def test_fast_result_before_batch_registration_and_history_clear_keep_context(svc):
    svc.store.dispatch(E.JobSubmitted("sign-job", "Sign 2: Cards", svc.clock.now()))
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc)))
    svc.store.dispatch(E.SigningBatchQueued(("sign-job",), 2, "SAVE", 11, "SESSION", svc.clock.now()))
    svc.store.dispatch(E.JobHistoryCleared())
    refresh(svc)
    assert recently_added_ids(svc) == (701, 702)


def test_restart_restores_batch_from_durable_worker_receipt_not_preview_ids(svc):
    db = Database(svc.paths.state_db)
    svc = replace(svc, db=db)
    now = svc.clock.stamp()
    job = {"requires": {"session_id": "SESSION"}, "ops": [{"id": "add", "op": "add_to_team",
           "teamid": 11, "batch": [{"dummy_pool": [800], "player": {"playerid": 100}},
                                      {"dummy_pool": [801], "player": {"playerid": 101}}]}]}
    try:
        db.upsert_job_record(JobRecord("sign-job", now, ApplyOutcome.APPLIED, save_uid="SAVE",
                                        finished_utc=now, job_json=job, result_json=receipt(svc).raw))
        refresh(svc)
        batches = signing_batches(svc)
        assert len(batches) == 1 and verified_batch_ids(batches[0]) == (701, 702)
        assert recently_added_ids(svc) == (701, 702)
    finally:
        db.close()


def test_return_to_club_selects_actual_ids_and_preserves_recent_filter(svc):
    queue(svc)
    svc.store.dispatch(E.JobFinished("sign-job", receipt(svc)))
    refresh(svc)
    batch = svc.store.snapshot().signing.batches[0]
    visited = []
    svc.ui.bind_navigation(visited.append)
    assert open_signed_club(svc, batch)
    view = {"checked_ids": [1], "squad_query": "old"}
    assert club.refresh_club_selection(svc, view)
    assert visited == ["club"] and view["checked_ids"] == [701, 702]
    assert view["recently_added"] and view["squad_query"] == ""
    refresh(svc, save="OTHER")
    club.refresh_club_selection(svc, view)
    assert not view["checked_ids"] and not view["recently_added"]


def test_openclub_waits_for_refresh_and_rejects_crosscareer_ids(svc):
    svc.ui.bind_navigation(lambda *_: None)
    svc.store.dispatch(E.SquadInvalidated("applied plan"))
    assert svc.ui.open_club((1, 701), "SAVE", 11)
    view = {}
    assert not club.refresh_club_selection(svc, view)
    assert "pending_club_selection" in view and not view["checked_ids"]
    refresh(svc)
    assert club.refresh_club_selection(svc, view)
    assert view["checked_ids"] == [1, 701]
    svc.ui.open_club((701,), "OTHER", 11)
    assert not club.refresh_club_selection(svc, view)
    assert not view["checked_ids"]


def test_club_passes_every_selected_player_without_silent_limit(svc, monkeypatch):
    rows = [{"playerid": pid, "name": f"P{pid}"} for pid in range(1, 41)]
    seen = []
    monkeypatch.setattr("companion.ui.surfaces.planner.open_planner", lambda *args, **kwargs: seen.append(args[1]))
    club._ask_squad_grok(svc, "improve", "Club", rows)
    assert seen == [rows] and len(seen[0]) == 40
