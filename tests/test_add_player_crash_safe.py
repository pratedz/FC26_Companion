"""Crash-safety contracts for add_to_team Lua (SAFE v15 dummy-first).

Root-cause evidence (queue/_add_team_crash.log):
  create_enter … silence (CreatePlayer freezes FC26).
Default path must never call CreatePlayer.
"""

from __future__ import annotations

import re

from src import add_player


def _lua_auto(card=None, **kwargs) -> str:
    c = card or {
        "name": "Zinedine Zidane",
        "overallrating": 94,
        "potential": 94,
        "skillmoves": 5,
        "weakfootabilitytypecode": 5,
        "preferredposition1": 18,
        "acceleration": 84,
        "dribbling": 96,
        "headassetid": 158023,
        "nationality": 18,
    }
    dummies = kwargs.pop("dummy_candidate_ids", [11, 13, 99])
    return add_player.generate_add_to_team_lua(
        c, teamid=243, mode="auto", dummy_candidate_ids=dummies, **kwargs
    )


def _lua_create(card=None, **kwargs) -> str:
    c = card or {"name": "Create Only", "overallrating": 80}
    return add_player.generate_add_to_team_lua(c, teamid=243, mode="create", **kwargs)


class TestDummyFirstDefault:
    def test_auto_has_no_createplayer(self) -> None:
        lua = _lua_auto()
        # Real call sites only (comments may mention CreatePlayer)
        assert "pcall(CreatePlayer" not in lua
        assert "function try_create" not in lua
        assert "try_dummy_overwrite" in lua
        assert "TransferPlayer" in lua
        assert "SAFE v15" in lua
        assert "PRIMARY_DUMMY = true" in lua
        assert "finish" in lua
        assert "FIELD_UPDATES" in lua
        assert "EditDBTableField" in lua
        assert "fields_enter" in lua or "apply_fields_to_pid" in lua

    def test_auto_has_ordered_crash_steps(self) -> None:
        lua = _lua_auto()
        assert "log_step" in lua
        assert "_add_team_crash.log" in lua
        assert "dummy_enter" in lua or "dummy_pick" in lua
        assert "transfer_enter" in lua
        assert "finish" in lua
        assert "abort" in lua

    def test_auto_includes_hint_dummies(self) -> None:
        lua = _lua_auto(dummy_candidate_ids=[111, 222, 333])
        assert "111" in lua and "222" in lua
        assert "HINT_DUMMIES" in lua

    def test_create_mode_still_has_createplayer(self) -> None:
        lua = _lua_create()
        assert "CreatePlayer" in lua
        assert "try_create" in lua
        assert "PRIMARY_DUMMY = false" in lua
        assert re.search(r'\["overallrating"\]\s*=\s*"\d+"', lua)

    def test_skillmoves_clamped_to_db_range(self) -> None:
        row = add_player.card_to_players_row_data(
            {"name": "X", "overallrating": 90, "skillmoves": 5, "weakfootabilitytypecode": 5}
        )
        sm = int(row["skillmoves"])
        wf = int(row["weakfootabilitytypecode"])
        assert 0 <= sm <= 4
        assert 1 <= wf <= 5

    def test_no_foreign_headassetid_on_create_row(self) -> None:
        row = add_player.card_to_players_row_data(
            {"name": "X", "overallrating": 90, "headassetid": 158023}
        )
        assert "headassetid" not in row

    def test_no_dangerous_scan_patterns_on_auto(self) -> None:
        lua = _lua_auto()
        for bad in (
            "collect_runtime_dummies",
            "GetPlayerIDSForTeam",
            "ReloadPlayersManager",
            "GetFirstRecord",
            "GetNextValidRecord",
        ):
            assert bad not in lua, f"dangerous pattern still present: {bad}"

    def test_transfer_presign_guard(self) -> None:
        lua = _lua_auto()
        assert "TransferPlayer" in lua
        assert "IsPlayerPresigned" in lua or "DeletePresignedContract" in lua

    def test_import_style_field_updates_nonempty(self) -> None:
        fields = add_player.build_import_style_field_updates(
            {"name": "X", "overallrating": 88, "acceleration": 90, "preferredposition1": 25}
        )
        names = {f for f, _ in fields}
        assert "overallrating" in names or "acceleration" in names
        assert len(fields) >= 3
