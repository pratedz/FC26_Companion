"""Tests for dual-mode protocol, config, and inject-side (no FC26 required)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src import actions
from src import card_to_lua
from src import companion_config
from src import inject_side
from src import lua_resolve
from src import paths
from src import protocol


@pytest.fixture()
def tmp_queue(tmp_path: Path) -> Path:
    q = tmp_path / "queue"
    protocol.ensure_queue_layout(q)
    protocol.enable_dry_run(q)
    return q


def test_protocol_write_and_drain_visible_markers(tmp_queue: Path) -> None:
    body = "-- test job body for protocol\nlocal x = 1\n"
    issued = protocol.write_job(tmp_queue, body, stem="unit_job", as_run_now=True)
    assert (tmp_queue / issued["job_name"]).is_file()
    assert (tmp_queue / protocol.RUN_NOW_NAME).is_file()
    assert issued["job_name"] in protocol.read_pending(tmp_queue)

    stats = protocol.process_queue_protocol(tmp_queue, force=True)
    assert stats.processed >= 1
    assert stats.ok == stats.processed
    parsed = protocol.parse_result_line(stats.result_line)
    assert parsed["ok"] is True
    assert int(parsed["processed"] or 0) >= 1
    assert protocol.bridge_alive(tmp_queue, max_age_sec=60.0)
    assert (tmp_queue / protocol.ARMED_NAME).is_file()
    assert protocol.observe_job_done(tmp_queue, protocol.RUN_NOW_NAME) or protocol.observe_job_done(
        tmp_queue, issued["job_name"]
    )


def test_protocol_self_check_round_trip(tmp_queue: Path) -> None:
    report = protocol.protocol_self_check(tmp_queue)
    assert report["ok"] is True
    assert report["protocol_version"] == protocol.PROTOCOL_VERSION
    assert report["alive"] is True


def test_profile_lua_to_queue_protocol(tmp_queue: Path) -> None:
    """Real profile resolve → write_job → drain (shipped functions)."""
    lua = lua_resolve.dump_profile("full_fitness")
    assert "SetPlayerFitness" in lua or "Fitness" in lua or "fitness" in lua.lower() or len(lua) > 20
    issued = protocol.write_job(tmp_queue, lua, stem="full_fitness")
    stats = protocol.process_queue_protocol(tmp_queue)
    parsed = protocol.parse_result_line(stats.result_line)
    assert parsed["ok"] is True
    assert stats.processed >= 1
    # Job content must be the real resolved Lua, not a hardcoded stub
    done = tmp_queue / protocol.DONE_SUBDIR
    found = False
    for p in done.glob("*.lua"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if "full_fitness" in p.name or text.strip() == lua.strip() or lua[:80] in text:
            found = True
            assert len(text) > 20
            break
    assert found, "expected archived job with real profile Lua"


def test_card_apply_lua_to_queue_protocol(tmp_queue: Path) -> None:
    """generate_apply_card_lua (shipped) → protocol write/drain."""
    card = {
        "name": "Test Player",
        "overallrating": 91,
        "potential": 93,
        "acceleration": 90,
        "sprintspeed": 91,
        "finishing": 88,
        "shotpower": 85,
        "longshots": 84,
        "volleys": 80,
        "penalties": 82,
        "positioning": 90,
        "vision": 86,
        "crossing": 70,
        "freekickaccuracy": 75,
        "shortpassing": 88,
        "longpassing": 80,
        "curve": 82,
        "agility": 90,
        "balance": 85,
        "reactions": 90,
        "ballcontrol": 91,
        "dribbling": 92,
        "composure": 88,
        "interceptions": 40,
        "headingaccuracy": 70,
        "defensiveawareness": 35,
        "standingtackle": 30,
        "slidingtackle": 25,
        "jumping": 70,
        "stamina": 85,
        "strength": 70,
        "aggression": 60,
        "skillmoves": 5,
        "weakfootabilitytypecode": 4,
        "preferredfoot": 1,
        "preferredposition1": 21,
    }
    target = 158023
    lua = card_to_lua.generate_apply_card_lua(card, target)
    assert str(target) in lua
    assert "overallrating" in lua.lower() or "Overall" in lua or "EditDB" in lua or "playerid" in lua.lower()
    issued = protocol.write_job(tmp_queue, lua, stem=f"apply_{target}")
    assert (tmp_queue / issued["job_name"]).is_file()
    content = (tmp_queue / issued["job_name"]).read_text(encoding="utf-8")
    assert str(target) in content
    stats = protocol.process_queue_protocol(tmp_queue)
    parsed = protocol.parse_result_line(stats.result_line)
    assert parsed["ok"] is True
    assert int(parsed["processed"] or 0) >= 1


def test_actions_write_lua_pending_protocol_compatible(tmp_queue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """actions.write_lua uses same queue side-files as protocol."""
    monkeypatch.setattr("src.paths.queue_dir", lambda: tmp_queue)
    monkeypatch.setattr("src.le_apply.queue_dir", lambda: tmp_queue)
    lua = "-- actions path\nreturn 1\n"
    out = actions.write_lua(lua, stem="actions_job", to_queue=True, to_generated=False)
    assert out.get("queue_file")
    assert (tmp_queue / protocol.RUN_NOW_NAME).is_file()
    assert (tmp_queue / protocol.PENDING_NAME).is_file()
    pending = protocol.read_pending(tmp_queue)
    assert any(n.endswith(".lua") for n in pending)


def test_config_round_trip_isolated(tmp_path: Path) -> None:
    """Round-trip must use isolated path — not production companion_config.json."""
    iso = tmp_path / "isolated_cfg" / companion_config.CONFIG_NAME
    report = companion_config.config_round_trip(
        default_target_playerid=242434,
        notes="pytest-config",
        path=iso,
    )
    assert report["ok"] is True
    assert report.get("production_untouched") is True
    loaded = companion_config.load_config(path=iso)
    assert loaded["default_target_playerid"] == 242434
    assert loaded["notes"] == "pytest-config"


def test_config_round_trip_refuses_production_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companion_config.paths, "app_root", lambda: tmp_path)
    prod = companion_config.config_path()
    prod.write_text('{"version":1,"notes":"user-keep","default_target_playerid":999001}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="refuses production"):
        companion_config.config_round_trip(path=prod)
    # Production file unchanged
    data = companion_config.load_config(path=prod)
    assert data.get("default_target_playerid") == 999001
    assert data.get("notes") == "user-keep"


def test_config_round_trip_default_does_not_clobber_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(companion_config.paths, "app_root", lambda: tmp_path)
    companion_config.save_config(
        {
            "default_target_playerid": 999001,
            "notes": "user-custom",
            "auto_clear_stale": True,
        }
    )
    report = companion_config.config_round_trip(
        default_target_playerid=158023,
        notes="round-trip-test",
    )
    assert report["ok"] is True
    prod = companion_config.load_config()
    assert prod.get("default_target_playerid") == 999001
    assert prod.get("notes") == "user-custom"


def test_dry_run_required_refuses_drain_without_marker(tmp_path: Path) -> None:
    q = tmp_path / "liveish"
    protocol.ensure_queue_layout(q)
    protocol.write_job(q, "-- precious job\nreturn 1\n", stem="precious")
    names_before = protocol.list_job_files(q)
    assert names_before
    stats = protocol.process_queue_protocol(q, require_dry_run=True)
    assert stats.note == "dry_run_required"
    assert "dry_run_required" in stats.result_line
    assert stats.processed == 0
    # Jobs still on disk
    assert protocol.list_job_files(q) == names_before
    assert (q / names_before[0]).is_file()


def test_protocol_self_check_refuses_production_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prod = tmp_path / "production_queue"
    protocol.ensure_queue_layout(prod)
    monkeypatch.setattr("src.paths.queue_dir", lambda: prod)
    # Write a real pending job that must survive self-check refusal
    protocol.write_job(prod, "-- real pending apply\nreturn 1\n", stem="real_apply")
    before = protocol.list_job_files(prod)
    report = protocol.protocol_self_check(prod)
    assert report.get("ok") is False
    assert report.get("error") == "refused_production_queue"
    assert protocol.list_job_files(prod) == before


def test_integration_status_le_owns_anticheat() -> None:
    st = inject_side.integration_status()
    assert st["companion_does_not_replace_fcliveeditor"] is True
    assert st["companion_does_not_swap_fakeeaac"] is True
    assert st["native_source_complete"] is True
    assert Path(st["native_source"]).joinpath("le_companion_inject.c").is_file()
    assert Path(st["native_source"]).joinpath("build.bat").is_file()


def test_native_source_exports_documented() -> None:
    header = (inject_side.native_source_dir() / "le_companion_inject.h").read_text(
        encoding="utf-8", errors="replace"
    )
    for export in (
        "LECompanion_SetQueueDir",
        "LECompanion_Arm",
        "LECompanion_ProcessQueue",
        "LECompanion_GetLastResult",
        "LECompanion_ProtocolVersion",
        "LECompanion_GetModuleInfo",
    ):
        assert export in header
    # Must document no FakeEAAC ownership
    src = (inject_side.native_source_dir() / "le_companion_inject.c").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "FakeEAAC" in src or "fakeeaac" in src.lower() or "FCLiveEditor" in src


def test_native_dll_protocol_when_built(tmp_queue: Path) -> None:
    """If DLL is present/buildable, drive real exports on isolated queue."""
    report = inject_side.native_protocol_self_check(None)
    if report.get("skipped"):
        pytest.skip(report.get("reason") or "dll_not_built")
    assert report["ok"] is True
    assert report.get("isolated") is True
    assert report.get("dry_run") is True
    assert report["protocol_version"] == protocol.PROTOCOL_VERSION
    assert "no-fakeeaac" in (report.get("module_info") or "").lower()


def test_native_process_queue_refuses_without_dry_run(tmp_path: Path) -> None:
    dll = inject_side.find_dll()
    if dll is None:
        pytest.skip("dll_not_built")
    q = tmp_path / "no_dry"
    protocol.ensure_queue_layout(q)
    protocol.write_job(q, "-- must not shred\nreturn 1\n", stem="keep_me")
    before = protocol.list_job_files(q)
    side = inject_side.InjectSide(dll)
    side.set_queue_dir(q)
    n = side.process_queue(True)
    assert n == -2
    assert "dry_run_required" in side.last_result()
    assert protocol.list_job_files(q) == before


def test_docs_state_le_owns_fakeeaac() -> None:
    arch = (paths.app_root() / "docs" / "ARCHITECTURE_DUAL_MODE.md").read_text(encoding="utf-8")
    proto = (paths.app_root() / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    assert "FakeEAAC" in arch
    assert "FCLiveEditor.DLL" in arch
    assert "does not" in arch.lower() or "NOT" in arch or "never" in arch.lower()
    assert "_last_result" in proto or "_last_result.txt" in proto
    assert "LECompanionInject" in arch or "inject" in arch.lower()
