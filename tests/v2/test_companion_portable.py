"""Relocating Companion keeps its own data and updates executable paths."""
import argparse
import json
import sqlite3

import pytest

from companion.core.db import Database
from companion.domain.outcome import ApplyOutcome
from tools.create_companion_portable import create, rebase


def seed(tmp_path):
    source = tmp_path / "old LE" / "LE_Profile_Executor"
    source.mkdir(parents=True)
    (source / "FC26_Companion.exe").write_bytes(b"app")
    runtime = source / "_internal_v2" / "ingame" / "le_companion"
    runtime.mkdir(parents=True)
    (runtime / "init.lua").write_text("return {}")
    (source / "profiles.json").write_text('{"profiles": []}')
    (source / "card_db").mkdir()
    with sqlite3.connect(source / "card_db" / "universe.sqlite") as conn:
        conn.execute("CREATE TABLE cards (name TEXT)")
        conn.execute("INSERT INTO cards VALUES ('Maradona')")
    return source, tmp_path / "new LE" / "FC26_Companion"


def test_copy_is_companion_only_preserves_wal_and_rebases_recovery_data(tmp_path):
    source, destination = seed(tmp_path)
    db = Database(source / "state.sqlite")
    job = {"snapshot_to": str(source / "snapshots" / "one.json"),
           "ops": [{"id": "add", "op": "add_to_team", "teamid": 11,
                    "player": {"names": {"commonname": "Maradona"}}}]}
    db.record_submitted(job_id="one", job=job)
    db.record_finished("one", outcome=ApplyOutcome.APPLIED,
                       result={"env": {"queue_dir": str(source / "queue")}}, save_uid="SAVE")
    db.set_pref("wallpaper", False)
    db.put_save_snapshot(save_uid="SAVE", kind="squad", payload={"players": [{"playerid": 701}]},
                         taken_ts=1, taken_utc="2026-01-01T00:00:00Z")
    (source / "snapshots").mkdir()
    snapshot = {"playerid": 701, "save_uid": "SAVE", "out_path": str(source / "snapshots/one.json")}
    (source / "snapshots/one.json").write_text(json.dumps(snapshot))
    (source / "snapshots/old_backup.sqlite").write_bytes(b"backup")
    (source / "queue/state").mkdir(parents=True)
    consumed = {"save_uid": "SAVE", "playerids": {"701": {"consumed_at": 1}}}
    (source / "queue/state/consumed_free_agent_dummies_SAVE.json").write_text(json.dumps(consumed))
    (source / "queue/session.json").write_text('{"armed": true}')
    (source / "queue/drain.json").write_text('{}')
    (source / "queue/legacy.lua").write_text("old bridge")
    for name in ("FCLiveEditor.DLL", "Launcher.exe", "LE_Profile_Executor.exe", "build.log"):
        (source / name).write_bytes(b"unrelated")
    report = create(source, destination)
    moved = Database(destination / "state.sqlite")
    assert moved.get_pref("wallpaper") is False
    assert moved.job_record("one").job_json["snapshot_to"] == str(destination / "snapshots/one.json").replace("\\", "/")
    assert db.job_record("one").job_json == job  # original history is retained
    assert moved.job_record("one").outcome is ApplyOutcome.APPLIED
    assert moved.latest_save_snapshot("squad").stale
    assert json.loads((destination / "queue/state/consumed_free_agent_dummies_SAVE.json").read_text()) == consumed
    assert json.loads((destination / "snapshots/one.json").read_text())["playerid"] == 701
    assert not (destination / "snapshots/old_backup.sqlite").exists()
    assert not (destination / "queue/session.json").exists()
    assert not (destination / "queue/drain.json").exists()
    assert not (destination / "queue/legacy.lua").exists()
    assert not (destination / "Launcher.exe").exists()
    assert not (destination / "FCLiveEditor.DLL").exists()
    assert report["source_preserved"]
    moved.close()
    db.close()


def test_rebase_respects_path_boundaries_and_external_paths(tmp_path):
    source = tmp_path / "old"
    destination = tmp_path / "new"
    raw = [str(source / "snapshots/one.json").upper().replace("\\", "/"),
           str(tmp_path / "old-other/one.json"), "Maradona", 701, None]
    moved = rebase(raw, source, destination)
    assert moved[0].casefold() == str(destination / "snapshots/one.json").replace("\\", "/").casefold()
    assert moved[1:] == raw[1:]


def test_existing_destination_is_never_overwritten(tmp_path):
    source, destination = seed(tmp_path)
    destination.mkdir(parents=True)
    keep = destination / "keep.txt"
    keep.write_text("keep")
    with pytest.raises(FileExistsError):
        create(source, destination)
    assert keep.read_text() == "keep"


def test_destination_cannot_be_nested_in_source(tmp_path):
    source, _ = seed(tmp_path)
    with pytest.raises(ValueError):
        create(source, source / "nested")


def test_legacy_result_paths_are_repaired_and_unreadable_history_is_retained(tmp_path):
    source, destination = seed(tmp_path)
    (source / "queue/results").mkdir(parents=True)
    bad = '{"snapshot_to": "' + str(source / "snapshots/one.json") + '"}'
    (source / "queue/results/old.json").write_text(bad)
    (source / "queue/results/broken.json").write_text('{incomplete')
    report = create(source, destination)
    result = json.loads((destination / "queue/results/old.json").read_text())
    assert result["snapshot_to"] == str(destination / "snapshots/one.json").replace("\\", "/")
    assert (destination / "migration_unreadable/queue/results/broken.json").read_text() == '{incomplete'
    assert not (destination / "queue/results/broken.json").exists()
    assert len(report["unreadable_history_preserved"]) == 1


@pytest.mark.parametrize("ok,code", [(True, 0), (False, 71)])
def test_cli_install_reports_real_outcome(monkeypatch, ok, code):
    from companion import cli
    from companion.platform import le_install
    from types import SimpleNamespace

    svc = SimpleNamespace(paths=SimpleNamespace(queue="queue"))
    monkeypatch.setattr(cli, "build_services", lambda *args, **kwargs: svc)
    monkeypatch.setattr(le_install, "install", lambda **kwargs: {"ok": ok})
    assert cli.cmd_install(argparse.Namespace(root=None, dry_run=False)) == code
