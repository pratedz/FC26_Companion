"""Squad-full recovery is explicit (Retry / Restore) and the names check diffs
in-game names against verified signing receipts."""

from __future__ import annotations

from pathlib import Path

import pytest

from companion import CORE_VERSION
from companion.app import events as E
from companion.app.commands import apply as apply_cmd
from companion.app.commands import names_check, sign_recovery
from companion.app.commands.squad import ingest_result
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.core.transport.v3 import Liveness, Pill
from companion.domain.job import (
    Grants, Job, JobValidationError, op_names_sync, op_ping, op_restore_partial_add,
)
from companion.domain.outcome import ApplyOutcome, JobResult
from companion.ui.surfaces import club
from companion.ui.surfaces.sign import handoff

ROOT = Path(__file__).resolve().parents[2]
SAVE = "SAVE-A"
SESSION = "01SESSION00000000000000000"
TEAM = 241


def _liveness(**caps) -> Liveness:
    return Liveness(
        pill=Pill.ARMED, message="ready", armed=True, session_id=SESSION,
        core_version=CORE_VERSION, save_uid=SAVE,
        capabilities={"add_to_team": True, "hub_ready": True, "in_career": True, **caps},
    )


@pytest.fixture
def svc(tmp_path):
    paths = TempAppPaths(tmp_path)
    (paths.queue / "state").mkdir(parents=True, exist_ok=True)
    service = Services(paths, FakeClock(), InlineExecutor(), FakeTransport(), Store(),
                       db=Database(paths.state_db))
    service.store.dispatch(E.PrefsLoaded(values={"core_ops": ["add_to_team"]}))
    service.store.dispatch(E.LivenessChanged(_liveness(restore_partial_add=True, names_sync=True)))
    _squad(service)
    yield service
    service.db.close()


def _squad(svc, players=None, *, free=(85308, 91234)):
    rows = players or [{"playerid": 1, "name": "Existing", "overallrating": 80}]
    svc.store.dispatch(E.SquadSynced(
        save_uid=SAVE, session_id=SESSION, teamid=TEAM, players=tuple(rows),
        taken=svc.clock.now(),
        free_agents=tuple({"playerid": pid, "overallrating": 50, "source": "free_agent_template"}
                          for pid in free),
    ))


def _member(pid: int, name: str, **player) -> dict:
    first, _, sur = name.partition(" ")
    body = {"fields": {"overallrating": 90}, "face": {},
            "names": {"firstname": first, "surname": sur, "commonname": name,
                      "playerjerseyname": sur},
            "name_strategy": "custom", "detach_name_dictionary": True}
    body.update(player)
    return {"dummy_pool": [pid], "dummy_filter": {"expected_overallrating": 50},
            "player": body, "contract": {"wage": 5000}}


def _sign_job(job_id: str, *members: dict, label: str = "Sign: Lionel Messi") -> dict:
    return {"job_id": job_id, "label": label, "requires": {"save_uid": SAVE},
            "ops": [{"id": "add", "op": "add_to_team", "v": 4, "teamid": TEAM,
                     "strategy": "dummy_overwrite", "batch": list(members)}]}


def _refusal(job_id: str, *, progress: dict | None = None, detail: str | None = None,
             state: str = "failed") -> dict:
    room = {"readable": True, "present": 42, "loaned": 10, "total": 52, "limit": 52,
            "free": 0, "needed": 1}
    data = {"progress": progress if progress is not None else {"squad_room": room, "members": [{}]}}
    return {"job_id": job_id, "state": state, "ok": False, "session_id": SESSION,
            "env": {"save_uid": SAVE},
            "ops": [{"id": "add", "op": "add_to_team", "ok": False, "data": data,
                     "reason": "squad_full"}],
            "failures": [{"reason": "squad_full", "op": "add_to_team",
                          "detail": detail or "42 senior + 10 on loan = 52 of 52"}]}


def _record(svc, job: dict, result: dict | None, *, created="2026-10-15T10:00:00Z") -> None:
    svc.db.record_submitted(job_id=job["job_id"], job=job, label=job.get("label", ""),
                            created_utc=created)
    if result is not None:
        svc.db.record_finished(job["job_id"], outcome=JobResult.from_wire(result).outcome,
                               result=result, save_uid=SAVE)


def _partial_progress(*, originals: bool = True) -> dict:
    member = {"playerid": 85308, "fields_written": True, "transferred": False,
              "written_values": {"overallrating": 90, "acceleration": 88}}
    if originals:
        member["original_values"] = {"overallrating": 52, "acceleration": 41}
    return {"squad_room": {"readable": True, "present": 52, "loaned": 0, "total": 52,
                           "limit": 52, "free": 0, "needed": 1},
            "members": [member]}


# ---------------------------------------------------------------------------
# no automatic relink chain
# ---------------------------------------------------------------------------


def test_squad_full_result_never_queues_another_job_by_itself(svc):
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")),
            _refusal("SIGN-1", progress=_partial_progress()))
    for _ in range(3):
        apply_cmd.collect_finished(svc)
    assert svc.transport.submitted == []
    source = (ROOT / "companion/app/commands/apply.py").read_text(encoding="utf-8")
    assert "queue_squad_full_relink" not in source
    assert "queue_squad_full_relink" not in (ROOT / "companion/app/commands/team.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# message + handoff
# ---------------------------------------------------------------------------


def test_message_uses_the_worker_counts_and_asks_for_retry():
    result = JobResult.from_wire(_refusal("SIGN-1"))
    assert sign_recovery.squad_full_message(result) == (
        "Squad full: 42 + 10 on loan = 52/52. Release or loan out a player, then press Retry."
    )
    # Older results only carry the detail string.
    detail_only = JobResult.from_wire(_refusal("SIGN-2", progress={}))
    assert sign_recovery.squad_room_from_result(detail_only).total == 52
    assert sign_recovery.squad_full_message(detail_only).startswith("Squad full: 42 + 10 on loan = 52/52.")


def test_batch_message_says_how_many_places_are_missing():
    room = {"readable": True, "present": 40, "loaned": 10, "total": 50, "limit": 52,
            "free": 2, "needed": 3}
    result = JobResult.from_wire(_refusal("SIGN-1", progress={"squad_room": room},
                                          detail="40 senior + 10 on loan = 50 of 52; room for 2 of 3 players"))
    assert sign_recovery.squad_full_message(result) == (
        "Squad has room for 2 of 3 players: 40 + 10 on loan = 50/52. "
        "Release or loan out 1 player, then press Retry."
    )


def test_partial_message_offers_restore_only_when_originals_were_captured():
    restorable = JobResult.from_wire(_refusal("A", progress=_partial_progress(), state="partial"))
    assert "Restore free agent puts its original values back" in sign_recovery.squad_full_message(restorable)
    assert sign_recovery.has_restorable_members(restorable)
    old = JobResult.from_wire(_refusal("B", progress=_partial_progress(originals=False), state="partial"))
    assert "cannot be restored automatically" in sign_recovery.squad_full_message(old)
    assert not sign_recovery.has_restorable_members(old)


def test_handoff_shows_counts_and_exposes_explicit_recovery(svc):
    svc.store.dispatch(E.JobSubmitted("SIGN-1", "Sign: Lionel Messi", svc.clock.now()))
    svc.store.dispatch(E.SigningBatchQueued(("SIGN-1",), 1, SAVE, TEAM, SESSION, svc.clock.now()))
    svc.store.dispatch(E.JobFinished("SIGN-1", JobResult.from_wire(_refusal("SIGN-1"))))
    message, batch, rows = handoff.latest_signing_handoff(svc)
    assert message == "Squad full: 42 + 10 on loan = 52/52. Release or loan out a player, then press Retry."
    assert rows == ()
    recovery = handoff.signing_recovery(batch)
    assert recovery is not None and recovery.job_id == "SIGN-1" and recovery.can_restore is False
    assert svc.transport.submitted == []


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_retry_requeues_the_reviewed_signing_only_when_pressed(svc):
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")), _refusal("SIGN-1"))
    assert svc.transport.submitted == []
    new_id = handoff.retry_refused_signing(svc, "SIGN-1")
    assert new_id and len(svc.transport.submitted) == 1
    wire = svc.transport.submitted[0].to_wire(now=1)
    op = wire["ops"][0]
    assert op["op"] == "add_to_team" and op["v"] == 4
    assert op["batch"][0]["dummy_pool"] == [85308]
    assert op["batch"][0]["player"]["names"]["commonname"] == "Lionel Messi"
    assert wire["grants"]["allow_add_to_team"] is True
    assert wire["requires"]["save_uid"] == SAVE
    assert svc.store.snapshot().signing.batches[-1].job_ids == (new_id,) or \
        svc.store.snapshot().signing.batches[0].job_ids == (new_id,)
    # A second press for the same refusal is refused, not chained.
    with pytest.raises(JobValidationError, match="already retried"):
        sign_recovery.retry_signing(svc, "SIGN-1")
    assert len(svc.transport.submitted) == 1


def test_retry_refuses_non_squad_failures_and_unavailable_dummies(svc):
    other = _refusal("SIGN-1")
    other["failures"] = [{"reason": "dummy_retiring", "detail": "x"}]
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")), other)
    with pytest.raises(JobValidationError, match="squad was full"):
        sign_recovery.retry_signing(svc, "SIGN-1")
    _record(svc, _sign_job("SIGN-2", _member(77777, "Zico Coimbra")), _refusal("SIGN-2"))
    with pytest.raises(JobValidationError, match="no longer available"):
        sign_recovery.retry_signing(svc, "SIGN-2")
    assert svc.transport.submitted == []


def test_retry_of_an_old_partial_moves_the_written_free_agent(svc):
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")),
            _refusal("SIGN-1", progress=_partial_progress(originals=False), state="partial"))
    sign_recovery.retry_signing(svc, "SIGN-1")
    job = svc.transport.submitted[0]
    member = job.to_wire(now=1)["ops"][0]["batch"][0]
    assert member["relink_only"] is True and member["playerid"] == 85308
    assert job.origin == "ui.sign.retry_relink"


# ---------------------------------------------------------------------------
# Restore free agent
# ---------------------------------------------------------------------------


def test_restore_plan_covers_exactly_the_written_fields(svc):
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")),
            _refusal("SIGN-1", progress=_partial_progress(), state="partial"))
    plan = sign_recovery.restore_plan(svc, "SIGN-1")
    assert plan.members == ({"playerid": 85308,
                             "expected": {"overallrating": 90, "acceleration": 88},
                             "restore": {"overallrating": 52, "acceleration": 41}},)
    assert plan.names == ("Lionel Messi",)
    lines = sign_recovery.restore_review_lines(plan)
    assert "  overallrating: 90 → 52" in lines
    job = sign_recovery.build_restore_job(svc, plan)
    wire = job.to_wire(now=1)
    assert wire["ops"][0]["op"] == "restore_partial_add"
    assert wire["ops"][0]["source_job_id"] == "SIGN-1"
    assert wire["grants"]["allow_add_to_team"] is True
    assert wire["requires"] == {"save_uid": SAVE}
    # Reviewed: the UI path asks first, and Cancel queues nothing.
    assert handoff.restore_refused_signing(svc, "SIGN-1", confirm=lambda *_: False) == ""
    assert svc.transport.submitted == []
    assert handoff.restore_refused_signing(svc, "SIGN-1", confirm=lambda *_: True)
    assert len(svc.transport.submitted) == 1


def test_restore_refuses_old_worker_results_and_signed_or_foreign_players(svc):
    _record(svc, _sign_job("OLD", _member(85308, "Lionel Messi")),
            _refusal("OLD", progress=_partial_progress(originals=False), state="partial"))
    with pytest.raises(JobValidationError, match="older worker"):
        sign_recovery.restore_plan(svc, "OLD")
    _record(svc, _sign_job("NEW", _member(85308, "Lionel Messi")),
            _refusal("NEW", progress=_partial_progress(), state="partial"))
    _squad(svc, [{"playerid": 85308, "name": "Lionel Messi"}])
    with pytest.raises(JobValidationError, match="on your club now"):
        sign_recovery.restore_plan(svc, "NEW")
    _squad(svc)
    svc.store.dispatch(E.SquadSynced(save_uid="OTHER", session_id=SESSION, teamid=TEAM,
                                     players=({"playerid": 1},), taken=svc.clock.now()))
    with pytest.raises(JobValidationError, match="another Career save"):
        sign_recovery.restore_plan(svc, "NEW")
    _squad(svc)
    # Nothing to restore after a clean precheck refusal.
    _record(svc, _sign_job("CLEAN", _member(85308, "Lionel Messi")), _refusal("CLEAN"))
    with pytest.raises(JobValidationError, match="did not leave a changed free agent"):
        sign_recovery.restore_plan(svc, "CLEAN")


def test_restore_refuses_after_an_applied_restore_and_without_the_new_worker(svc):
    _record(svc, _sign_job("SIGN-1", _member(85308, "Lionel Messi")),
            _refusal("SIGN-1", progress=_partial_progress(), state="partial"))
    plan = sign_recovery.restore_plan(svc, "SIGN-1")
    svc.store.dispatch(E.LivenessChanged(_liveness()))
    with pytest.raises(JobValidationError, match="updated worker"):
        sign_recovery.build_restore_job(svc, plan)
    restore = {"job_id": "RESTORE-1", "requires": {"save_uid": SAVE},
               "ops": [{"id": "restore", "op": "restore_partial_add", "source_job_id": "SIGN-1"}]}
    _record(svc, restore, {"job_id": "RESTORE-1", "state": "done", "ok": True,
                           "counts": {"fields_written": 2, "ops_ok": 1},
                           "ops": [{"id": "restore", "op": "restore_partial_add", "ok": True,
                                    "counts": {"fields_written": 2}}]},
            created="2026-10-16T10:00:00Z")
    with pytest.raises(JobValidationError, match="already restored"):
        sign_recovery.restore_plan(svc, "SIGN-1")


def test_restore_and_names_ops_are_save_bound_single_op_jobs():
    restore = op_restore_partial_add("r", source_job_id="S", members=[
        {"playerid": 85308, "expected": {"overallrating": 90}, "restore": {"overallrating": 52}}])
    names = op_names_sync("n", teamid=TEAM, players=[
        {"playerid": 701, "names": {"commonname": "Diego Maradona"}}])
    for op in (restore, names):
        with pytest.raises(JobValidationError, match="allow_add_to_team"):
            Job(ops=(op,), requires={"save_uid": SAVE}).to_wire()
        with pytest.raises(JobValidationError, match="save_uid"):
            Job(ops=(op,), grants=Grants(allow_add_to_team=True)).to_wire()
        with pytest.raises(JobValidationError, match="only operation"):
            Job(ops=(op, op_ping()), grants=Grants(allow_add_to_team=True),
                requires={"save_uid": SAVE}).to_wire()
    with pytest.raises(JobValidationError, match="same written and original"):
        op_restore_partial_add("r", source_job_id="S", members=[
            {"playerid": 85308, "expected": {"overallrating": 90}, "restore": {"potential": 52}}])
    with pytest.raises(JobValidationError, match="native"):
        op_names_sync("n", teamid=TEAM, players=[
            {"playerid": 701, "names": {"commonname": "X"}, "name_strategy": "native_ids"}])


# ---------------------------------------------------------------------------
# Names check
# ---------------------------------------------------------------------------


def _verified(job_id: str, ids: list[int]) -> dict:
    return {"job_id": job_id, "state": "done", "ok": True, "session_id": SESSION,
            "env": {"save_uid": SAVE}, "counts": {"fields_written": 20, "ops_ok": 1},
            "ops": [{"id": "add", "op": "add_to_team", "ok": True,
                     "counts": {"fields_written": 20},
                     "data": {"teamid": TEAM, "verified": True, "playerids": ids}}]}


def _export(svc, rows: list[dict]) -> None:
    result = JobResult.from_wire({
        "job_id": "refresh", "state": "done", "ok": True, "session_id": SESSION,
        "env": {"save_uid": SAVE},
        "ops": [{"id": "squad", "op": "export_squad", "ok": True,
                 "counts": {"targets": len(rows)}, "data": {"teamid": TEAM, "players": rows}}],
    })
    assert ingest_result(svc, result)


def _names_fixture(svc) -> None:
    native = _member(702, "Lionel Messi", name_strategy="native_ids",
                     fields={"overallrating": 90, "firstnameid": 11, "lastnameid": 12,
                             "commonnameid": 13, "playerjerseynameid": 0},
                     detach_name_dictionary=False)
    native["player"]["names"]["playerjerseyname"] = "Messi"
    _record(svc, _sign_job("SIGN-1", _member(701, "Diego Maradona"), native,
                           label="Sign 2: Diego Maradona, Lionel Messi"),
            _verified("SIGN-1", [701, 702]))
    _export(svc, [{"playerid": 701, "name": "Dummy Freeagent"},
                  {"playerid": 702, "name": "Messi"},
                  {"playerid": 703, "name": "Homegrown"}])


def test_names_check_diffs_in_game_names_against_receipts(svc):
    _names_fixture(svc)
    # The squad view still shows signed names; the in-game name is kept aside.
    squad = svc.store.snapshot().squad
    assert squad.players[0]["name"] == "Diego Maradona"
    assert dict(squad.game_names)[701] == "Dummy Freeagent"
    check = names_check.check_signed_names(svc)
    assert [row.playerid for row in check.mismatches] == [701]
    assert check.mismatches[0].game_name == "Dummy Freeagent"
    assert check.mismatches[0].signed_name == "Diego Maradona"
    assert [row.playerid for row in check.matched] == [702]   # native jersey name
    assert names_check.review_lines(check) == ["• Dummy Freeagent → Diego Maradona"]
    job = names_check.build_names_sync_job(svc, check)
    wire = job.to_wire(now=1)
    assert len(wire["ops"]) == 1 and wire["ops"][0]["op"] == "names.sync"
    assert wire["ops"][0]["teamid"] == TEAM
    assert wire["ops"][0]["players"] == [{
        "playerid": 701, "name_strategy": "custom", "detach_name_dictionary": True,
        "names": {"firstname": "Diego", "surname": "Maradona", "commonname": "Diego Maradona",
                  "playerjerseyname": "Maradona"}}]
    assert wire["requires"] == {"save_uid": SAVE}
    assert wire["grants"]["allow_add_to_team"] is True


def test_names_check_queues_one_job_only_after_review(svc):
    _names_fixture(svc)
    assert club._check_signed_names(svc, confirm=lambda *_: False) == ""
    assert svc.transport.submitted == []
    job_id = club._check_signed_names(svc, confirm=lambda *_: True)
    assert job_id and len(svc.transport.submitted) == 1
    assert svc.transport.submitted[0].ops[0].op == "names.sync"


def test_names_check_with_all_names_correct_queues_nothing(svc):
    _record(svc, _sign_job("SIGN-1", _member(701, "Diego Maradona")), _verified("SIGN-1", [701]))
    _export(svc, [{"playerid": 701, "name": "Diego Maradona"}])
    check = names_check.check_signed_names(svc)
    assert not check.mismatches and check.checked == 1
    with pytest.raises(JobValidationError, match="already shows"):
        names_check.build_names_sync_job(svc, check)
    assert club._check_signed_names(svc, confirm=lambda *_: True) == ""
    assert svc.transport.submitted == []
    assert "All 1 signed player" in svc.store.snapshot().status


def test_names_check_ignores_other_saves_unverified_receipts_and_needs_new_worker(svc):
    failed = _verified("SIGN-X", [701])
    failed["ops"][0]["ok"] = False
    failed["state"], failed["ok"] = "failed", False
    _record(svc, _sign_job("SIGN-X", _member(701, "Diego Maradona")), failed)
    _export(svc, [{"playerid": 701, "name": "Dummy Freeagent"}])
    assert names_check.check_signed_names(svc).checked == 0
    _names_fixture(svc)
    check = names_check.check_signed_names(svc)
    svc.store.dispatch(E.LivenessChanged(_liveness()))
    with pytest.raises(JobValidationError, match="updated worker"):
        names_check.build_names_sync_job(svc, check)
    svc.store.dispatch(E.SquadInvalidated("hub exit"))
    with pytest.raises(JobValidationError, match="Refresh squad"):
        names_check.check_signed_names(svc)


@pytest.mark.parametrize(("actual", "expected", "ok"), [
    ("Andrés Iniesta", "Iniesta", True),
    ("Iniesta", "Andrés Iniesta", False),
    ("Ben", "Benedict", False),
    ("Luka Modrić", "luka modrić", True),
    ("Dummy Freeagent", "Diego Maradona", False),
    ("", "Diego Maradona", False),
])
def test_visible_name_matching_mirrors_the_worker(actual, expected, ok):
    assert names_check.visible_name_matches(actual, expected) is ok
