"""Product engine tests — packs, match, snapshots, turbo apply path (no FC26)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import card_match
from src import product
from src import snapshot_store
from src import snippets


def test_packs_defined() -> None:
    packs = product.list_packs()
    ids = {p["id"] for p in packs}
    assert "matchday" in ids
    assert "squad_boost" in ids
    assert "export_squad" in ids
    # Synergy workflow packs (multi-feature chains)
    assert "match_ready" in ids
    assert "signing_settle" in ids
    assert "season_kickoff" in ids
    assert "pre_match_full" in ids
    assert "club_refresh" in ids
    workflows = [p for p in packs if p.get("category") == "workflow"]
    assert len(workflows) >= 4
    mr = product.PACKS["match_ready"]
    assert "export_user_squad" in mr["profile_ids"]
    assert "matchday_pack" in mr["profile_ids"]


def test_matchday_snippet_exists() -> None:
    lua = snippets.get_snippet("matchday_pack")
    assert "UserTeamSetPlayersFitness" in lua
    assert "Sharpness" in lua or "sharpness" in lua.lower()


def test_card_match_ranks_name() -> None:
    # Synthetic scoring only
    a = {"name": "Lionel Messi", "overallrating": 93, "preferredposition1": 23, "playerid": 158023}
    b = {"name": "Some Other", "overallrating": 70, "preferredposition1": 10}
    sa = card_match.score_match(a, name="Messi", ovr=91, position=23, playerid=158023)
    sb = card_match.score_match(b, name="Messi", ovr=91, position=23)
    assert sa > sb


def test_snapshot_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshot_store.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr("src.undo_apply.paths.app_root", lambda: tmp_path)
    snap = snapshot_store.save_fields_snapshot(
        target_id=158023,
        fields=[("overallrating", 91), ("acceleration", 90)],
        label="test-messi",
        kind="card",
    )
    assert snap["id"]
    rows = snapshot_store.list_snapshots()
    assert rows and rows[0]["target_id"] == 158023
    lua = snapshot_store.undo_lua_for(snap["id"])
    assert "158023" in lua
    assert "overallrating" in lua


def test_write_job_meta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product.paths, "app_root", lambda: tmp_path)
    monkeypatch.setattr(product.paths, "queue_dir", lambda: tmp_path / "queue")
    q = tmp_path / "queue"
    q.mkdir()
    job = q / "job_test.lua"
    job.write_text("-- x\n", encoding="utf-8")
    mp = product.write_job_meta(
        str(job), kind="test", detail="d", target_id=1, card_name="X"
    )
    assert mp.is_file()
    assert (q / "results").is_dir()


def test_arm_status_shape() -> None:
    st = product.arm_status()
    assert "live" in st
    assert "state" in st
    assert st.get("le_owns_anticheat") is True
    assert "Go LIVE" in str(st.get("hint") or "") or "LIVE" in str(st.get("hint") or "")


def test_go_live_returns_ux_contract(tmp_path: Path, monkeypatch) -> None:
    """go_live must always return title/body/state for a single dialog."""
    # go_live persists arm state; keep it out of the user's real config.
    from src import companion_config

    monkeypatch.setattr(
        companion_config, "config_path", lambda: tmp_path / companion_config.CONFIG_NAME
    )
    # Offline-safe: don't require real LE install
    out = product.go_live(prefer_autoarm=False)
    assert "state" in out
    assert out["state"] in ("already_live", "need_paste", "failed")
    assert out.get("title")
    assert out.get("body")
    # When not live, clipboard attempt should be reported
    if out["state"] == "need_paste":
        assert "clipboard_ok" in out or "clipboard_bridge" in out
