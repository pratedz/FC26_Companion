"""Signing source identity, export recovery, guarded installation and restore."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from companion.app import events as E
from companion.app.commands import signed_portraits as portraits, legacy_assets
from companion.app.services import Services
from companion.app.store import Store
from companion.core.clock import FakeClock
from companion.core.db import Database
from companion.core.executor import InlineExecutor
from companion.core.paths import TempAppPaths
from companion.core.transport.fake import FakeTransport
from companion.domain.ids import new_ulid
from companion.domain.job import Job, Op
from companion.domain.legacy_assets import portrait_paths
from companion.domain.outcome import JobResult
from companion.domain.signing_identity import signed_player_identities

SOURCE, TARGET = 190045, 73669


def receipt(*, target=TARGET, source=SOURCE, save="SAVE-A", job_id=None):
    jid = job_id or new_ulid()
    raw = {"job_id": jid, "requires": {"save_uid": save}, "ops": [{"op": "add_to_team", "id": "add", "teamid": 241,
        "batch": [{"dummy_pool": [99, target], "player": {"source_playerid": source, "names": {"commonname": "Maradona"}}}]}]}
    wire = {"job_id": jid, "ok": True, "state": "done", "env": {"save_uid": save},
        "ops": [{"op": "add_to_team", "id": "add", "ok": True, "counts": {"fields_written": 20},
                 "data": {"verified": True, "teamid": 241, "playerids": [target]}}]}
    return raw, wire


@pytest.fixture
def svc(tmp_path, monkeypatch):
    paths = TempAppPaths(tmp_path)
    transport = FakeTransport()
    transport.set_liveness(replace(transport.liveness(), save_uid="SAVE-A", core_version="2.6.31",
        capabilities={"hub_ready": True, "legacy_file_check": True, "legacy_file_export": True}))
    service = Services(paths, FakeClock(), InlineExecutor(), transport, Store(), db=Database(paths.state_db))
    service.store.dispatch(E.SquadSynced(save_uid="SAVE-A", session_id=transport.liveness().session_id,
        teamid=241, players=[{"playerid": TARGET, "name": "Original dummy"}], taken=service.clock.now()))
    raw, wire = receipt()
    service.db.record_submitted(job_id=raw["job_id"], job=raw)
    service.db.record_finished(raw["job_id"], outcome=JobResult.from_wire(wire).outcome, result=wire, save_uid="SAVE-A")
    monkeypatch.setattr(portraits, "le_mods_dir", lambda: tmp_path / "le-mods")
    yield service
    service.db.close()


def cached(svc, *, color="red"):
    base = portraits._source_dir(svc, SOURCE)
    base.mkdir(parents=True, exist_ok=True)
    dds = base / "portrait.dds"
    Image.new("RGBA", (180, 180), color).save(dds, pixel_format="DXT5")
    Image.new("RGBA", (180, 180), color).save(base / "preview.png")
    portraits._json(base / "state.json", {"state": "ready", "source_playerid": SOURCE, "sha256": portraits._hash(dds)})
    return dds


def exported(svc):
    job = legacy_assets.prepare_job(svc, portrait_paths(SOURCE), export=True)
    dds = svc.paths.assets / job.job_id / "1" / f"p{SOURCE}.DDS"
    Image.new("RGBA", (180, 180), "blue").save(dds, pixel_format="DXT5")
    wire = {"job_id": job.job_id, "ok": True, "state": "done", "counts": {"side_effects": 1},
        "ops": [{"op": "assets.legacy", "id": "assets", "ok": True, "counts": {"side_effects": 1}, "data": {"assets": [
            {"path": portrait_paths(SOURCE)[0], "exported": True, "exists": True, "bytes": dds.stat().st_size, "output": str(dds)},
            {"path": portrait_paths(SOURCE)[1], "exported": False, "exists": False}]}}]}
    svc.db.record_submitted(job_id=job.job_id, job=job.to_wire())
    svc.transport.complete(job.job_id, wire)
    return job


def test_receipt_maps_source_to_actual_backup_dummy_and_rejects_dry_run():
    raw, wire = receipt()
    identity = signed_player_identities(raw, JobResult.from_wire(wire), save_uid="SAVE-A")[TARGET]
    assert identity["source_playerid"] == SOURCE and identity["name"] == "Maradona"
    assert 99 not in signed_player_identities(raw, JobResult.from_wire(wire), save_uid="SAVE-A")
    raw["dry_run"] = True
    assert not signed_player_identities(raw, JobResult.from_wire(wire), save_uid="SAVE-A")


def test_old_receipt_requires_name_ids_and_real_face_to_recover_source(svc, monkeypatch):
    native = {key: i for i, key in enumerate(("firstnameid", "lastnameid", "commonnameid", "playerjerseynameid"), 1)}
    raw, wire = receipt(job_id=new_ulid())
    player = raw["ops"][0]["batch"][0]["player"]
    player.pop("source_playerid")
    player.update(face={"headassetid": SOURCE}, fields=native)
    svc.db.record_submitted(job_id=raw["job_id"], job=raw, created_utc="2099-01-01T00:00:00Z")
    svc.db.record_finished(raw["job_id"], outcome=JobResult.from_wire(wire).outcome, result=wire, save_uid="SAVE-A")
    monkeypatch.setattr(portraits, "resolve_card_base_profile", lambda card: SimpleNamespace(
        face_verified=True, appearance={"headassetid": SOURCE}, identity=native, source_playerid=SOURCE))
    assert portraits.identities(svc, "SAVE-A")[TARGET]["source_playerid"] == SOURCE
    monkeypatch.setattr(portraits, "resolve_card_base_profile", lambda card: SimpleNamespace(
        face_verified=True, appearance={"headassetid": SOURCE}, identity={**native, "commonnameid": 99}, source_playerid=SOURCE))
    assert portraits.identities(svc, "SAVE-A")[TARGET]["source_playerid"] is None


def test_recovers_completed_native_export_without_duplicate_job(svc):
    job = exported(svc)
    state = portraits.ensure_source(svc, SOURCE)
    assert state["state"] == "ready" and state["job_id"] == job.job_id
    assert not svc.transport.submitted
    assert portraits.preview_path(svc, portraits.portrait_state(svc, TARGET)).is_file()
    assert portraits.ensure_source(svc, SOURCE) == state


def test_pending_export_is_deduplicated_and_survives_restart(svc):
    state = portraits.ensure_source(svc, SOURCE)
    assert state["state"] == "queued"
    assert len(svc.transport.submitted) == 1
    assert portraits.ensure_source(svc, SOURCE) == state
    assert len(svc.transport.submitted) == 1
    # A fresh service object shares only durable state, not callback closures.
    other = replace(svc, executor=InlineExecutor())
    assert portraits.ensure_source(other, SOURCE) == state
    assert len(svc.transport.submitted) == 1


def test_install_uses_actual_id_and_restores_existing_override(svc):
    dds = cached(svc)
    target = portraits._destination(TARGET)
    target.parent.mkdir(parents=True)
    original = b"existing portrait override"
    target.write_bytes(original)
    state = portraits.install(svc, TARGET)
    assert target.read_bytes() == dds.read_bytes()
    assert state["playerid"] == TARGET and state["source_playerid"] == SOURCE
    assert Path(state["backup"]).read_bytes() == original
    assert portraits.install(svc, TARGET) == state  # Never back up our own installed image.
    portraits.restore(svc, TARGET)
    assert target.read_bytes() == original and not portraits.installed(svc, TARGET)


def test_restore_removes_only_our_override_when_no_previous_file(svc):
    cached(svc)
    portraits.install(svc, TARGET)
    portraits.restore(svc, TARGET)
    assert not portraits._destination(TARGET).exists()


@pytest.mark.parametrize("change", ["save", "session", "removed", "stale", "unarmed"])
def test_refuses_changed_career_or_roster(svc, change):
    cached(svc)
    if change in ("save", "session", "unarmed"):
        updates = {"save_uid": "OTHER"} if change == "save" else {"session_id": "OTHER"} if change == "session" else {"armed": False}
        svc.transport.set_liveness(replace(svc.transport.liveness(), **updates))
    elif change == "stale":
        svc.store.dispatch(E.SquadInvalidated(reason="changed"))
    else:
        svc.store.dispatch(E.SquadSynced(save_uid="SAVE-A", session_id=svc.transport.liveness().session_id,
            teamid=241, players=[], taken=svc.clock.now()))
    with pytest.raises(ValueError):
        portraits.install(svc, TARGET)
    assert not portraits._destination(TARGET).exists()


def test_preserves_external_mod_edits_and_does_not_restore_over_them(svc):
    cached(svc)
    portraits.install(svc, TARGET)
    target = portraits._destination(TARGET)
    target.write_bytes(b"another mod changed this image")
    with pytest.raises(ValueError, match="externally"):
        portraits.restore(svc, TARGET)
    with pytest.raises(ValueError, match="externally"):
        portraits.install(svc, TARGET)
    assert target.read_bytes() == b"another mod changed this image"


def test_truncated_wrong_format_and_wrong_dimensions_are_rejected(svc):
    path = cached(svc)
    portraits.validate_portrait(path)
    Image.new("RGBA", (64, 64)).save(path, pixel_format="DXT5")
    with pytest.raises(ValueError):
        portraits.install(svc, TARGET)
    Image.new("RGBA", (180, 180)).save(path)
    with pytest.raises(ValueError):
        portraits.validate_portrait(path)
    path.write_bytes(b"DDS " + b"\0" * 124)
    with pytest.raises(ValueError):
        portraits.validate_portrait(path)


def test_cache_never_uses_another_players_report(svc):
    job = exported(svc)
    result = svc.transport.result(job.job_id)
    report = legacy_assets.process_result(svc, job, result)
    state = portraits._cache_locked(svc, 20801, report)
    assert state["state"] == "missing"
    assert not (portraits._source_dir(svc, 20801) / "portrait.dds").exists()


def test_prepared_receipt_after_crash_keeps_original_backup(svc):
    cached(svc)
    target = portraits._destination(TARGET)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous")
    record = portraits.install(svc, TARGET)
    record.update(active=False, status="prepared")
    portraits._json(portraits._ledger(svc, TARGET), record)
    assert portraits.install(svc, TARGET)["backup"] == record["backup"]
    portraits.restore(svc, TARGET)
    assert target.read_bytes() == b"previous"


def test_export_recovery_uses_durable_receipt_after_queue_rotation(svc):
    job = exported(svc)
    result = svc.transport.result(job.job_id)
    svc.db.record_finished(job.job_id, outcome=result.outcome, result=dict(result.raw))
    svc.transport._results.clear()
    assert portraits.ensure_source(svc, SOURCE)["state"] == "ready"
    assert not svc.transport.submitted


def test_newest_signing_source_wins_without_cross_save_bleed(svc):
    raw, wire = receipt(source=20801)
    svc.db.record_submitted(job_id=raw["job_id"], job=raw, created_utc="2099-01-01T00:00:00Z")
    svc.db.record_finished(raw["job_id"], outcome=JobResult.from_wire(wire).outcome, result=wire, save_uid="SAVE-A")
    assert portraits.identities(svc, "SAVE-A")[TARGET]["source_playerid"] == 20801
    assert not portraits.identities(svc, "OTHER-SAVE")


def test_install_failure_restores_original_image(svc, monkeypatch):
    cached(svc)
    target = portraits._destination(TARGET)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous image")
    original_json = portraits._json
    def fail_commit(path, data):
        if data.get("status") == "installed":
            raise OSError("fixture disk failure")
        return original_json(path, data)
    monkeypatch.setattr(portraits, "_json", fail_commit)
    with pytest.raises(OSError, match="disk"):
        portraits.install(svc, TARGET)
    assert target.read_bytes() == b"previous image"


def test_restore_preserves_original_when_backup_is_corrupt(svc):
    cached(svc)
    target = portraits._destination(TARGET)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous image")
    record = portraits.install(svc, TARGET)
    Path(record["backup"]).write_bytes(b"corrupt backup")
    with pytest.raises(ValueError, match="backup"):
        portraits.restore(svc, TARGET)
    assert portraits._hash(target) == record["sha256"]


def test_queued_export_becomes_ready_when_result_arrives_later(svc):
    state = portraits.ensure_source(svc, SOURCE)
    job = svc.transport.submitted[0]
    output = svc.paths.assets / job.job_id / "1" / f"p{SOURCE}.DDS"
    Image.new("RGBA", (180, 180), "blue").save(output, pixel_format="DXT5")
    raw = {"job_id": job.job_id, "ok": True, "state": "done", "counts": {"side_effects": 1},
        "ops": [{"op": "assets.legacy", "id": "assets", "ok": True, "counts": {"side_effects": 1}, "data": {"assets": [
            {"path": portrait_paths(SOURCE)[0], "exists": True, "exported": True, "output": str(output), "bytes": output.stat().st_size},
            {"path": portrait_paths(SOURCE)[1], "exists": False, "exported": False}]}}]}
    svc.transport.complete(job.job_id, raw)
    ready = portraits.ensure_source(svc, SOURCE)
    assert ready["state"] == "ready" and ready["job_id"] == state["job_id"]
    assert len(svc.transport.submitted) == 1


def test_sync_only_fetches_current_save_signed_players_without_installing(svc):
    portraits.sync(svc)
    assert len(svc.transport.submitted) == 1
    assert svc.transport.submitted[0].ops[0].body["paths"] == list(portrait_paths(SOURCE))
    assert not portraits._destination(TARGET).exists()
    svc.transport.set_liveness(replace(svc.transport.liveness(), save_uid="OTHER"))
    portraits.sync(svc)
    assert len(svc.transport.submitted) == 1


def test_injury_commands_require_new_resident_worker_and_squad_read_budget(svc):
    from companion.app.commands.injury import scan_injuries, InjuryError
    scan_injuries(svc)
    job = svc.transport.submitted[-1]
    assert job.budget_ms == 5000 and job.requires["core_version"] == "2.6.31"
    svc.transport.set_liveness(replace(svc.transport.liveness(), core_version="2.6.30"))
    with pytest.raises(InjuryError, match="restart"):
        scan_injuries(svc)
