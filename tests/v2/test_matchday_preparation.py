"""Matchday targeting, recipe persistence, duplicate protection and receipts."""
from dataclasses import replace

import pytest

from companion.app import events as E
from companion.app.commands import matchday
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.matchday import ORIGIN, Routine, build_preparation
from companion.domain.outcome import ApplyOutcome, JobResult


@pytest.fixture
def svc(tmp_path):
    paths = TempAppPaths(tmp_path)
    transport = FakeTransport()
    transport.set_liveness(replace(transport.liveness(), core_version="2.6.31", save_uid="SAVE_A"))
    db = Database(paths.state_db)
    service = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=db)
    service.store.dispatch(E.LivenessChanged(transport.liveness()))
    yield service
    db.close()


def finish(svc, job_id, *, state="done", ok=True, written=100, failed=0):
    wire = {"job_id": job_id, "state": state, "ok": ok,
            "counts": {"fields_written": written, "fields_failed": failed},
            "env": {"save_uid": "SAVE_A"}}
    result = JobResult.from_wire(wire)
    svc.store.dispatch(E.JobFinished(job_id=job_id, result=result))
    svc.db.record_finished(job_id, outcome=result.outcome, result=wire, save_uid="SAVE_A")
    return result


def test_default_is_the_existing_prepare_squad_values():
    from companion.domain.pack_library import build_job
    wire = build_preparation(Routine()).to_wire()
    old = build_job("squad_boost").to_wire()["ops"][0]
    assert len(wire["ops"]) == 1
    for field in ("scope", "v", "fitness", "sharpness", "form", "morale"):
        assert wire["ops"][0][field] == old[field]
    assert wire["budget_ms"] == 5000
    assert wire["require_cm"] is True


def test_selected_steps_only_and_optional_recovery():
    recipe = Routine(form=False, morale=False, injuries=True)
    job = build_preparation(recipe, save_uid="SAVE_A", teamid=21)
    wire = job.to_wire()
    boost, cure = wire["ops"]
    assert boost["fitness"] == 100 and boost["sharpness"] == 100
    assert "form" not in boost and "morale" not in boost
    assert boost["teamid"] == 21
    assert cure["op"] == "injury.cure" and cure["playerids"] == []
    assert wire["requires"] == {"save_uid": "SAVE_A", "core_version": "2.6.31"}
    assert wire["atomic"] is False


def test_recovery_only_has_no_career_value_writes():
    job = build_preparation(Routine(False, False, False, False, True))
    assert [op.op for op in job.ops] == ["injury.cure"]


def test_corrupt_preference_does_not_enable_injury_recovery():
    assert Routine.from_pref({"fitness": "false", "injuries": "yes", "unknown": True}) == Routine()
    assert Routine.from_pref(None) == Routine()


def test_remembers_recipe_after_reopening_database(svc):
    routine = Routine(form=False, morale=False)
    matchday.save_routine(svc, routine)
    assert matchday.load_routine(svc) == routine
    other = Database(svc.paths.state_db)
    try:
        assert Routine.from_pref(other.get_pref("matchday.routine")) == routine
    finally:
        other.close()


def test_no_read_required_and_duplicate_submission_blocked(svc):
    first = matchday.prepare_squad(svc, Routine())
    assert first and len(svc.transport.submitted) == 1
    job = svc.transport.submitted[0]
    assert job.requires["save_uid"] == "SAVE_A"
    assert "teamid" not in job.ops[0].body
    with pytest.raises(matchday.MatchdayError, match="already waiting"):
        matchday.prepare_squad(svc, Routine())
    assert len(svc.transport.submitted) == 1
    assert matchday.last_preparation(svc)["title"] == "Preparation queued"


def test_pending_guard_survives_app_restart(svc):
    job_id = matchday.prepare_squad(svc, Routine())
    reopened = replace(svc, store=Store())
    reopened.store.dispatch(E.LivenessChanged(svc.transport.liveness()))
    assert matchday.pending_preparation(reopened) == job_id


def test_old_save_cache_never_supplies_team_even_in_same_worker_session(svc):
    svc.store.dispatch(E.SquadSynced(save_uid="OLD_SAVE", session_id=svc.transport.liveness().session_id,
                                    teamid=21, players=[{"playerid": 1}], taken=svc.clock.now()))
    matchday.prepare_squad(svc, Routine())
    assert "teamid" not in svc.transport.submitted[0].ops[0].body


def test_submission_refreshes_live_save_and_drops_prior_team_hint(svc):
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE_A", session_id=svc.transport.liveness().session_id,
                                    teamid=21, players=[{"playerid": 1}], taken=svc.clock.now()))
    svc.transport.set_liveness(replace(svc.transport.liveness(), save_uid="SAVE_B"))
    matchday.prepare_squad(svc, Routine())
    job = svc.transport.submitted[0]
    assert job.requires["save_uid"] == "SAVE_B"
    assert "teamid" not in job.ops[0].body


def test_fresh_same_save_team_hint_is_a_guard(svc):
    svc.store.dispatch(E.SquadSynced(save_uid="SAVE_A", session_id=svc.transport.liveness().session_id,
                                    teamid=21, players=[{"playerid": 1}], taken=svc.clock.now()))
    matchday.prepare_squad(svc, Routine())
    assert svc.transport.submitted[0].ops[0].body["teamid"] == 21


@pytest.mark.parametrize("outcome,ok,written,failed,title", [
    ("done", True, 100, 0, "Preparation applied"),
    ("partial", False, 10, 1, "Preparation partly applied"),
    ("failed", False, 0, 1, "Preparation needs attention"),
    ("no_op", True, 0, 0, "No changes needed"),
])
def test_receipt_uses_exact_result_and_allows_next_match(svc, outcome, ok, written, failed, title):
    job_id = matchday.prepare_squad(svc, Routine())
    finish(svc, job_id, state=outcome, ok=ok, written=written, failed=failed)
    receipt = matchday.last_preparation(svc)
    assert receipt["title"] == title and receipt["busy"] is False
    assert receipt["steps"] == ["Fitness 100", "Sharpness 100", "Form 100", "Morale 120"]
    assert "verified" not in receipt["detail"].lower()
    assert matchday.disabled_reason(svc, Routine()) == ""
    assert matchday.prepare_squad(svc, Routine()) != job_id


def test_finish_event_is_visible_before_database_persistence(svc):
    job_id = matchday.prepare_squad(svc, Routine())
    result = JobResult.from_wire({"job_id": job_id, "state": "done", "ok": True})
    svc.store.dispatch(E.JobFinished(job_id=job_id, result=result))
    assert svc.db.job_record(job_id).outcome is ApplyOutcome.QUEUED
    assert matchday.pending_preparation(svc) == ""
    assert matchday.last_preparation(svc)["title"] == "Preparation applied"


def test_completed_receipt_does_not_follow_user_to_another_save(svc):
    job_id = matchday.prepare_squad(svc, Routine())
    finish(svc, job_id)
    svc.store.dispatch(E.LivenessChanged(replace(svc.transport.liveness(), save_uid="SAVE_B")))
    assert matchday.last_preparation(svc)["job_id"] == ""


def test_injuries_require_new_worker_but_default_boost_still_works(svc):
    svc.transport.set_liveness(replace(svc.transport.liveness(), core_version="2.6.30"))
    with pytest.raises(matchday.MatchdayError, match="Restart"):
        matchday.prepare_squad(svc, Routine(injuries=True))
    assert not svc.transport.submitted
    assert matchday.prepare_squad(svc, Routine())


def test_offline_and_empty_routines_do_not_queue(svc):
    with pytest.raises(matchday.MatchdayError, match="at least one"):
        matchday.prepare_squad(svc, Routine(False, False, False, False))
    svc.transport.set_liveness(replace(svc.transport.liveness(), armed=False))
    with pytest.raises(matchday.MatchdayError):
        matchday.prepare_squad(svc, Routine())
    assert not svc.transport.submitted


def test_retry_rebuilds_current_save_and_preserves_original_steps(svc):
    routine = Routine(form=False, morale=False)
    job_id = matchday.prepare_squad(svc, routine)
    finish(svc, job_id, state="failed", ok=False, written=0, failed=1)
    svc.transport.set_liveness(replace(svc.transport.liveness(), save_uid="SAVE_B"))
    new_id = matchday.retry_preparation(svc, job_id)
    job = svc.transport.submitted[-1]
    assert new_id != job_id and job.origin == ORIGIN
    assert job.requires["save_uid"] == "SAVE_B"
    assert "morale" not in job.ops[0].body and "form" not in job.ops[0].body


def test_unrelated_activity_does_not_replace_preparation_receipt(svc):
    from companion.app.commands.apply import submit_job
    from companion.domain.job import job_ping
    job_id = matchday.prepare_squad(svc, Routine())
    finish(svc, job_id)
    submit_job(svc, job_ping(), follow=False)
    assert matchday.last_preparation(svc)["job_id"] == job_id
