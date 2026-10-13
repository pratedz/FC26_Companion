"""Refresh must show the signed card identity on its actual Career row."""
import pytest

from companion.app import events as E
from companion.app.commands.squad import ingest_result
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.outcome import JobResult
from companion.domain.signing_identity import signed_player_names


def signing(*, job_id="sign", save="SAVE-A", names=("Diego Maradona",), ids=(701,)):
    members = [{"dummy_pool": [100 + i, pid],
                "player": {"names": {"commonname": name}}}
               for i, (pid, name) in enumerate(zip(ids, names))]
    job = {"job_id": job_id, "requires": {"save_uid": save},
           "ops": [{"id": "add", "op": "add_to_team", "teamid": 241, "batch": members}]}
    result = {"job_id": job_id, "state": "done", "ok": True, "session_id": "OLD-SESSION",
              "env": {"save_uid": save}, "counts": {"fields_written": 20, "ops_ok": 1},
              "ops": [{"id": "add", "op": "add_to_team", "ok": True,
                       "counts": {"fields_written": 20},
                       "data": {"teamid": 241, "verified": True, "playerids": list(ids)}}]}
    return job, result


def record(db, job, result, *, created="2026-01-01T00:00:00Z"):
    db.record_submitted(job_id=job["job_id"], job=job, created_utc=created)
    if result is not None:
        db.record_finished(job["job_id"], outcome=JobResult.from_wire(result).outcome,
                           result=result, save_uid=job["requires"]["save_uid"])


def service(tmp_path):
    paths = TempAppPaths(tmp_path)
    return Services(paths, FakeClock(), InlineExecutor(), FakeTransport(), Store(),
                    db=Database(paths.state_db))


def refresh(svc, *, save="SAVE-A", teamid=241):
    players = [{"playerid": 701, "name": "Original free agent", "overallrating": 95},
               {"playerid": 702, "name": "Another free agent", "overallrating": 92},
               {"playerid": 100, "name": "Unused primary dummy", "overallrating": 50},
               {"playerid": 99, "name": "Existing teammate", "overallrating": 85}]
    result = JobResult.from_wire({
        "job_id": "refresh", "state": "done", "ok": True, "env": {"save_uid": save},
        "ops": [{"id": "squad", "op": "export_squad", "ok": True,
                 "counts": {"targets": len(players)},
                 "data": {"teamid": teamid, "players": players}}],
    })
    assert ingest_result(svc, result)
    return svc.store.snapshot().squad.players


def test_refresh_uses_signed_name_and_preserves_actual_id_stats_and_teammates(tmp_path):
    svc = service(tmp_path)
    record(svc.db, *signing())
    svc.store.dispatch(E.TargetLocked(701, "Original free agent", "squad", 241))
    rows = refresh(svc)
    assert rows[0] == {"playerid": 701, "name": "Diego Maradona", "overallrating": 95}
    assert rows[1]["name"] == "Another free agent"
    assert rows[2]["name"] == "Unused primary dummy"
    assert rows[3]["name"] == "Existing teammate"
    assert svc.store.snapshot().target.name == "Diego Maradona"
    snapshot = svc.db.latest_save_snapshot("squad")
    assert snapshot.save_uid == "SAVE-A"
    assert snapshot.payload["players"][0]["name"] == "Diego Maradona"
    assert refresh(svc)[0]["name"] == "Diego Maradona"
    svc.db.close()


def test_batch_signing_survives_restart_and_more_than_200_recent_jobs(tmp_path):
    svc = service(tmp_path)
    record(svc.db, *signing(names=("Diego Maradona", "Pelé"), ids=(701, 702)))
    for i in range(205):
        job = {"job_id": f"read-{i}", "requires": {"save_uid": "SAVE-A"},
               "ops": [{"id": "squad", "op": "export_squad"}]}
        record(svc.db, job, None, created="2026-02-01T00:00:00Z")
    svc.db.close()
    restarted = service(tmp_path)
    rows = refresh(restarted)
    assert [r["name"] for r in rows[:2]] == ["Diego Maradona", "Pelé"]
    assert [r["playerid"] for r in rows[:2]] == [701, 702]
    restarted.db.close()


def test_same_dummy_in_another_save_keeps_that_saves_own_name(tmp_path):
    svc = service(tmp_path)
    record(svc.db, *signing())
    assert refresh(svc, save="OTHER")[0]["name"] == "Original free agent"
    record(svc.db, *signing(job_id="other-sign", save="OTHER", names=("Zico",)))
    assert refresh(svc, save="OTHER")[0]["name"] == "Zico"
    assert refresh(svc)[0]["name"] == "Diego Maradona"
    svc.db.close()


def test_newest_completed_signing_wins_and_transferred_player_keeps_name(tmp_path):
    svc = service(tmp_path)
    record(svc.db, *signing())
    record(svc.db, *signing(job_id="new-sign", names=("Pelé",)),
           created="2026-02-01T00:00:00Z")
    assert refresh(svc, teamid=5)[0]["name"] == "Pelé"
    svc.db.close()


@pytest.mark.parametrize("change", [
    "queued", "dry_run", "op_dry_run", "unverified", "failed_op", "wrong_save",
    "wrong_team", "wrong_op_id", "short_batch", "duplicate_ids", "bool_id",
    "unexpected_dummy", "wrong_job_save", "wrong_session",
])
def test_incomplete_or_mismatched_signing_never_renames_a_row(change):
    job, wire = signing(names=("Diego Maradona", "Pelé"), ids=(701, 702))
    request, receipt = job["ops"][0], wire["ops"][0]
    if change == "queued":
        wire["state"] = "queued"
    elif change == "dry_run":
        job["dry_run"] = True
    elif change == "op_dry_run":
        request["dry_run"] = True
    elif change == "unverified":
        receipt["data"]["verified"] = False
    elif change == "failed_op":
        receipt["ok"] = False
    elif change == "wrong_save":
        wire["env"]["save_uid"] = "OTHER"
    elif change == "wrong_job_save":
        job["requires"]["save_uid"] = "OTHER"
    elif change == "wrong_session":
        job["requires"]["session_id"] = "DIFFERENT"
    elif change == "wrong_team":
        receipt["data"]["teamid"] = 5
    elif change == "wrong_op_id":
        receipt["id"] = "other"
    elif change == "short_batch":
        receipt["data"]["playerids"] = [701]
    elif change == "duplicate_ids":
        receipt["data"]["playerids"] = [701, 701]
    elif change == "bool_id":
        receipt["data"]["playerids"] = [True, 702]
    elif change == "unexpected_dummy":
        receipt["data"]["playerids"] = [999, 702]
    assert signed_player_names(job, JobResult.from_wire(wire), save_uid="SAVE-A") == {}


def test_legacy_single_signing_uses_actual_receipt_id_and_full_name():
    job, wire = signing()
    request = job["ops"][0]
    request.update(request.pop("batch")[0])
    request["player"]["names"] = {"firstname": "Diego", "surname": "Maradona"}
    wire["ops"][0]["data"].pop("playerids")
    wire["ops"][0]["data"]["playerid"] = 701
    assert signed_player_names(job, JobResult.from_wire(wire), save_uid="SAVE-A") == {701: "Diego Maradona"}


def test_verified_op_in_partial_job_keeps_its_identity():
    job, wire = signing()
    wire["state"], wire["ok"] = "partial", False
    wire["counts"]["ops_failed"] = 1
    wire["ops"].append({"id": "other", "op": "set_fields", "ok": False})
    assert signed_player_names(job, JobResult.from_wire(wire), save_uid="SAVE-A") == {701: "Diego Maradona"}
