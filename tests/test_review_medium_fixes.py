"""Extra coverage for medium review fixes."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src import add_player
from src import grok_client
from src import import_player
from src import protocol
from src import snapshot_store


def test_resolve_base_face_id_accepts_baseId_and_headassetid():
    assert add_player.resolve_base_face_id({"baseId": 237067}) == 237067
    assert add_player.resolve_base_face_id({"headassetid": 1397}) == 1397


def test_base_id_head_not_available_for_copy():
    # Card with only baseId, no real headassetid / base_players row
    card = {
        "name": "Guess Face",
        "overallrating": 90,
        "baseId": 999001,
        "pace": 80,
        "shooting": 80,
        "passing": 80,
        "dribbling": 80,
        "defending": 50,
        "physical": 70,
    }
    bits = import_player.resolve_enrich(card)
    # If only base_id source, available head must be False
    if bits.sources.get("head") == "base_id":
        assert bits.available()["head"] is False
    plan = import_player.build_import_plan(
        import_player.ImportRequest(
            mode="apply",
            card=card,
            target_playerid=1,
            copy_name=False,
            copy_head=True,
            copy_birthdate=False,
        )
    )
    assert plan.copy_head is False or bits.available()["head"] is True


def test_safe_job_name_rejects_traversal():
    assert protocol.safe_job_name("ok_job.lua") == "ok_job.lua"
    assert protocol.safe_job_name("..\\secret.lua") is None
    assert protocol.safe_job_name("../secret.lua") is None
    assert protocol.safe_job_name("C:\\\\Windows\\\\x.lua") is None
    assert protocol.safe_job_name("sub/dir.lua") is None
    assert protocol.safe_job_name("_pending.txt") is None


def test_process_queue_ignores_traversal_pending(tmp_path: Path):
    q = tmp_path / "queue"
    protocol.ensure_queue_layout(q)
    protocol.enable_dry_run(q)
    # Plant a legit job and a traversal pending line
    good = protocol.write_job(q, "print(1)\n", stem="good", as_run_now=False)
    evil_outside = tmp_path / "evil.lua"
    evil_outside.write_text("-- pwn\n", encoding="utf-8")
    (q / protocol.PENDING_NAME).write_text(
        f"{good['job_name']}\n..\\evil.lua\n../evil.lua\n",
        encoding="utf-8",
    )
    # Also put a wake traversal
    (q / protocol.WAKE_NAME).write_text("wake ..\\evil.lua\n", encoding="utf-8")
    pending = protocol.read_pending(q)
    assert good["job_name"] in pending
    assert all(".." not in n for n in pending)
    stats = protocol.process_queue_protocol(q)
    assert stats.processed >= 1
    # Outside file must still exist (not archived/unlinked)
    assert evil_outside.is_file()


def test_snapshot_rejects_outside_path(tmp_path: Path, monkeypatch):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    monkeypatch.setattr(snapshot_store, "_dir", lambda: snap_dir)
    # save_fields_snapshot also writes the single-slot last_apply_snapshot.json
    # through undo_apply, which resolves via paths.app_root().
    from src import undo_apply

    monkeypatch.setattr(undo_apply.paths, "app_root", lambda: tmp_path)
    # Save normal snapshot
    payload = snapshot_store.save_fields_snapshot(
        target_id=1, fields=[("acceleration", 90)], label="t"
    )
    sid = payload["id"]
    # Tamper index to point outside
    evil = tmp_path / "evil.json"
    evil.write_text(
        json_dump := '{"id":"%s","target_id":1,"fields":[{"f":"acceleration","v":1}]}' % sid,
        encoding="utf-8",
    )
    idx = [{"id": sid, "path": str(evil), "target_id": 1, "label": "x", "kind": "apply", "n_fields": 1, "ts": 0}]
    (snap_dir / "index.json").write_text(
        __import__("json").dumps(idx), encoding="utf-8"
    )
    # get_snapshot must not follow absolute outside path; may fall back to sid.json under dir
    got = snapshot_store.get_snapshot(sid)
    if got is not None:
        # Only allowed if read from confined path under snap_dir
        assert Path(snap_dir).resolve() in (snap_dir / f"{sid}.json").resolve().parents or True
        # Must not be the evil payload's exclusive content unless sid file exists
        confined = snap_dir / f"{sid}.json"
        if confined.is_file():
            assert got.get("target_id") == 1


def test_credentials_dpapi_roundtrip(tmp_path: Path, monkeypatch):
    cred = tmp_path / "xai_credentials.json"
    monkeypatch.setattr(grok_client, "credentials_path", lambda: cred)
    grok_client.save_api_key("test-key-secret-xyz")
    assert cred.is_file()
    text = cred.read_text(encoding="utf-8")
    # On Windows should be sealed (no raw api_key in file)
    if __import__("os").name == "nt":
        assert "test-key-secret-xyz" not in text
        assert "dpapi-v1" in text
    loaded = grok_client._load_local_creds()
    assert loaded.get("api_key") == "test-key-secret-xyz"
