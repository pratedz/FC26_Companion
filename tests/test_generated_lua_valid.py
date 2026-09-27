"""Structural validation of every Lua generator in the project.

Generated Lua is shipped into the game and executed with `load()` + `pcall()`.
A syntax error there is close to invisible: the load fails, the bridge reports a
generic failure, and nothing points at the offending template. Six modules build
Lua by f-string templating, and until now none of their output was ever checked.

These tests balance block structure (the failure mode template edits actually
produce — a stray or missing `end`) and enforce a few LE safety rules learned
from real freezes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.lua_lint import format_issues, lint  # noqa: E402

from src import (  # noqa: E402
    add_player,
    add_team_lua,
    career_ops,
    card_to_lua,
    growth_xp,
    player_apply,
    squad_snapshot,
    undo_apply,
)

CARD = {
    "name": "Test Player",
    "playerid": 158023,
    "overallrating": 91,
    "potential": 93,
    "acceleration": 95,
    "sprintspeed": 93,
    "finishing": 90,
    "skillmoves": 3,
    "weakfootabilitytypecode": 4,
    "height": 170,
    "weight": 72,
    "preferredposition1": 25,
    "trait1": 6357188,
    # face / kit / tattoo fields so those categories have something to write
    "headtypecode": 1547,
    "hairtypecode": 10,
    "shoetypecode": 20,
    "jerseyfit": 1,
    "tattoohead": 5,
    "tattoofront": 3,
}


def assert_valid(lua: str, what: str) -> None:
    assert lua and lua.strip(), f"{what}: generated empty Lua"
    issues = lint(lua)
    assert not issues, f"{what}:\n{format_issues(issues)}\n--- lua ---\n{lua}"


# ── the linter itself ────────────────────────────────────────────────


def test_linter_accepts_valid_lua() -> None:
    assert not lint("if x then\n  local y = 1\nend\n")
    assert not lint("for i = 1, 10 do\n  print(i)\nend\n")
    assert not lint("local f = function() return 1 end\n")
    assert not lint("repeat\n  x = x + 1\nuntil x > 3\n")
    assert not lint("if a then\nelseif b then\nelse\nend\n")


def test_linter_catches_missing_end() -> None:
    issues = lint("if x then\n  local y = 1\n")
    assert any(i.kind == "unbalanced" for i in issues)


def test_linter_catches_extra_end() -> None:
    issues = lint("local x = 1\nend\n")
    assert any(i.kind == "unbalanced" for i in issues)


def test_linter_ignores_keywords_inside_strings_and_comments() -> None:
    assert not lint('local s = "if then end end end"\n')
    assert not lint("-- if then end end\nlocal x = 1\n")
    assert not lint("local s = [[ if then end ]]\n")
    assert not lint("--[[ if then end\nstill comment end ]]\nlocal x = 1\n")


def test_linter_flags_forbidden_apis() -> None:
    assert any(i.kind == "forbidden_api" for i in lint("ReloadPlayersManager()\n"))
    assert any(i.kind == "forbidden_api" for i in lint('os.execute("dir")\n'))


# ── real generators ──────────────────────────────────────────────────


def test_player_apply_lua_is_valid() -> None:
    assert_valid(
        player_apply.generate_apply_player_lua(CARD, 158023), "player_apply (all cats)"
    )


def test_player_apply_with_names_is_valid() -> None:
    assert_valid(
        player_apply.generate_apply_player_lua(
            CARD,
            158023,
            name_parts={"firstname": "Test", "surname": "Player", "playerjerseyname": "Player"},
        ),
        "player_apply (with names)",
    )


@pytest.mark.parametrize(
    "cats",
    [
        {"attributes"},
        {"attributes", "ratings", "skills"},
        {"positions", "playstyles"},
        {"body", "movement"},
        {"face", "kit", "tattoos"},
    ],
)
def test_player_apply_each_category_is_valid(cats) -> None:
    assert_valid(
        player_apply.generate_apply_player_lua(CARD, 158023, enabled_categories=cats),
        f"player_apply {sorted(cats)}",
    )


def test_card_apply_lua_is_valid() -> None:
    assert_valid(card_to_lua.generate_apply_card_lua(CARD, 158023), "card_to_lua")


def test_card_apply_with_name_copy_is_valid() -> None:
    assert_valid(
        card_to_lua.generate_apply_card_lua(CARD, 158023, copy_name=True),
        "card_to_lua (copy_name)",
    )


@pytest.mark.parametrize("mode", ["auto", "dummy", "create"])
def test_add_to_team_lua_is_valid(mode: str) -> None:
    lua = add_team_lua.render_add_to_team_lua(
        name="Test Player",
        first="Test",
        sur="Player",
        jersey="Player",
        teamid=5,
        mode=mode,
        preferred_create_id=470000,
        player_row={"overallrating": "91"},
        field_updates=[("overallrating", 91), ("acceleration", 95)],
        dummy_ids=[100, 101, 102],
        transfersum=0,
        wage=5000,
        contract_months=36,
        age=27,
        fallback_birthdate="150000",
        use_real_face=True,
        base_face_id=158023,
        nation_id=52,
        gen_min=add_player.GENERATED_ID_MIN,
        gen_max=add_player.GENERATED_ID_MAX,
        crash_log_path="C:/tmp/crash.log",
        status_path="C:/tmp/status.txt",
    )
    assert_valid(lua, f"add_team_lua ({mode})")


def test_career_ops_lua_is_valid() -> None:
    for label, lua in [
        ("transfer", career_ops.generate_transfer_lua(playerid=1, to_teamid=5, transfersum=0)),
        ("release", career_ops.generate_release_lua(playerid=1)),
        ("set_budget", career_ops.generate_set_transfer_budget_lua(amount=1000000)),
        ("get_budget", career_ops.generate_get_transfer_budget_lua()),
    ]:
        assert_valid(lua, f"career_ops.{label}")


def test_undo_lua_is_valid(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(undo_apply.paths, "app_root", lambda: tmp_path)
    undo_apply.save_snapshot(
        target_id=158023, fields=[("acceleration", 88), ("finishing", 80)], label="t"
    )
    assert_valid(undo_apply.generate_undo_lua(), "undo_apply")


def test_growth_sync_lua_is_valid() -> None:
    fields = [("finishing", 90), ("acceleration", 95), ("skillmoves", 3)]
    assert_valid(growth_xp.growth_sync_block(158023, fields), "growth_xp block (raw)")
    assert_valid(
        growth_xp.growth_sync_block(158023, fields, mode="xp"), "growth_xp block (xp)"
    )
    assert_valid(growth_xp.generate_growth_sync_lua(158023, fields), "growth_xp standalone")


def test_growth_block_splices_cleanly_into_apply() -> None:
    """The block is embedded in another chunk — the seam must stay balanced."""
    lua = player_apply.generate_apply_player_lua(
        CARD, 158023, enabled_categories={"attributes", "ratings", "skills"}
    )
    assert "PlayerSetValueInDevelopementPlan" in lua, "growth mirror was not spliced in"
    assert_valid(lua, "player_apply + growth block")


def test_squad_snapshot_lua_is_valid(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(squad_snapshot.paths, "app_root", lambda: tmp_path)
    assert_valid(
        squad_snapshot.generate_squad_capture_lua([158023, 20801]), "squad capture"
    )


def test_shipped_bridge_worker_is_valid() -> None:
    src = APP_ROOT / "bridge" / "le_profile_bridge.lua"
    if not src.is_file():
        pytest.skip("bridge source missing")
    assert_valid(src.read_text(encoding="utf-8", errors="replace"), "bridge worker")


def test_installed_bridge_worker_is_valid() -> None:
    from src import le_apply

    dest = le_apply.le_scripts_bridge_path()
    if not dest.is_file():
        pytest.skip("bridge not installed")
    assert_valid(dest.read_text(encoding="utf-8", errors="replace"), "installed bridge")


def test_autorun_stub_is_valid() -> None:
    from src import le_apply

    stub = le_apply._AUTORUN_STUB.replace("{bridge_path}", "C:/x/bridge.lua")
    assert_valid(stub, "autorun stub")
