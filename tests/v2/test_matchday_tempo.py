"""Club filter and Library card-build staging."""

from __future__ import annotations

import inspect

from companion.ui.surfaces import club, library
from companion.ui.surfaces._common import filter_squad_rows, squad_board_row


def test_filter_squad_rows_matches_name_position_and_id():
    rows = (
        squad_board_row({"playerid": 7, "name": "Ronaldo", "position": "ST", "overallrating": 91}),
        squad_board_row({"playerid": 10, "name": "Messi", "position": "RW", "overallrating": 93}),
    )
    assert [row["name"] for row in filter_squad_rows(rows, "st")] == ["Ronaldo"]
    assert [row["name"] for row in filter_squad_rows(rows, "10")] == ["Messi"]
    assert [row["name"] for row in filter_squad_rows(rows, "")] == ["Ronaldo", "Messi"]
    assert "id" in rows[0]
    assert rows[0]["id"] == 7


def test_club_roster_filters_and_hides_id_column():
    source = inspect.getsource(club._build_squad)
    assert "Filter by name, position, or ID" in source
    assert 'Column("id", "ID"' not in source
    assert "checked_ids" in source
    assert "squad_query" in inspect.getsource(club.build)


def test_library_inspector_copy_is_card_build_not_stats_only():
    source = inspect.getsource(library.build)
    assert "Stage card build" in source
    assert "stats and skills only" not in source
    assert "Catalog tools" in inspect.getsource(library._sources_panel)
    panel_src = inspect.getsource(library._sources_panel)
    assert 'shown = {"value": False}' in panel_src
    stage_src = inspect.getsource(library._stage_card)
    assert "open_player_card" in stage_src
    assert "CardImportTopics(stats=True)" in stage_src
