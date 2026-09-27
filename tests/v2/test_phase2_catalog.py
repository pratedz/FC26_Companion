from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion.app import events as E
from companion.app.store import Store
from companion.app.ui_actions import UiActions
from companion.core.db import Database
from companion.domain.catalog import CatalogError, CatalogQuery, LocalCatalog
from companion.ui.surfaces.library import _stage_card


def _universe(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE person(
          person_id INTEGER PRIMARY KEY, display_name TEXT, name_norm TEXT,
          exists_in_fc26 INTEGER
        );
        CREATE TABLE observation(
          obs_id INTEGER PRIMARY KEY, person_id INTEGER, year INTEGER,
          source TEXT, source_file TEXT, source_kind TEXT, variant TEXT,
          variant_id TEXT, display_name TEXT, overall INTEGER, potential INTEGER,
          preferredposition1 INTEGER, preferredposition2 INTEGER,
          preferredposition3 INTEGER, preferredposition4 INTEGER,
          positions_text TEXT, club_id INTEGER, club_name TEXT, league_id INTEGER,
          league_name TEXT, nationality_id INTEGER, nationality_name TEXT,
          height INTEGER, weight INTEGER, preferredfoot INTEGER, skillmoves INTEGER,
          weakfoot INTEGER, trait1 INTEGER, trait2 INTEGER, icontrait1 INTEGER,
          icontrait2 INTEGER, roles TEXT, playstyles TEXT, attrs TEXT,
          attr_fidelity TEXT, attr_granular_n INTEGER, attr_expanded_n INTEGER,
          id_provenance TEXT
        );
        """
    )
    con.execute("INSERT INTO person VALUES(190871,'Neymar Jr','neymar jr',1)")
    rows = [
        (1, 190871, 26, "futgg", "f26", "fut", "Rare", "999001",
         "Neymar Jr", 83, 83, 18, 14, 25, 27, "CAM, CM", 1, "Brazil",
         2, "Men", 54, "Brazil", 175, 68, 1, 5, 5, 0, 0, 0, 0, None,
         "{}", json.dumps({"acceleration": 80, "finishing": 81}), "granular", 2, 0,
         "source_id"),
        (2, 190871, 25, "sofifa", "f25", "career", "Base", "190871",
         "Neymar Jr", 87, 87, 27, 18, None, None, "LW, CAM", 3, "Al Hilal",
         4, "Saudi", 54, "Brazil", 175, 68, 1, 5, 5, 0, 0, 0, 0, None,
         "{}", json.dumps({"acceleration": 88, "finishing": 84}), "granular", 2, 0,
         "source_id"),
    ]
    con.executemany(
        "INSERT INTO observation VALUES(" + ",".join("?" for _ in range(38)) + ")",
        rows,
    )
    con.commit()
    con.close()
    return path


def test_search_filters_and_preserves_career_identity(tmp_path: Path) -> None:
    catalog = LocalCatalog(universe_path=_universe(tmp_path / "universe.sqlite"))
    hits = catalog.search(
        CatalogQuery(text="Neymar", year="26", ovr_min=80, ovr_max=85)
    )
    assert len(hits) == 1
    card = hits[0]
    assert card["playerid"] == 190871
    assert card["person_id"] == 190871
    assert card["variant_id"] == "999001"
    assert card["overallrating"] == 83
    assert card["acceleration"] == 80


def test_variants_best_match_and_compare(tmp_path: Path) -> None:
    catalog = LocalCatalog(universe_path=_universe(tmp_path / "universe.sqlite"))
    card = catalog.search("Neymar", year="26")[0]
    assert [row["year"] for row in catalog.variants(card)] == [26, 25]
    ranked = catalog.best_matches(
        {"name": "Neymar Jr", "playerid": 190871, "overallrating": 86}
    )
    assert ranked[0]["person_id"] == 190871
    assert ranked[0]["_match_score"] > 150
    comparison = catalog.compare(
        {"overallrating": 80, "acceleration": 70}, card
    )
    assert next(r for r in comparison if r["field"] == "acceleration")["delta"] == 10


def test_cross_year_import_is_reviewable_and_never_copies_identity() -> None:
    plan = LocalCatalog.prepare_cross_year_import(
        {
            "name": "Historic Player", "year": "19", "playerid": 123,
            "variant_id": "999", "teamid": 55, "contractvaliduntil": 2020,
            "overall": 91, "weakfoot": 5,
            "attrs": '{"finishing": 94, "acceleration": 101}',
        },
        target_playerid=777,
    )
    assert plan.target_playerid == 777
    assert plan.fields == {
        "overallrating": 91, "weakfootabilitytypecode": 5,
        "finishing": 94, "acceleration": 99,
    }
    assert "playerid" not in plan.fields
    assert "teamid" not in plan.fields
    assert plan.warnings
    with pytest.raises(ValueError):
        LocalCatalog.prepare_cross_year_import({}, target_playerid=0)


def test_cross_year_import_copies_body_and_kit_without_clamping_codes() -> None:
    plan = LocalCatalog.prepare_cross_year_import(
        {
            "overall": 94,
            "height": 185,
            "weight": 80,
            "bodytypecode": 187,
            "muscularitycode": 2,
            "runstylecode": 120,
            "runningcode1": 150,
            "jerseyfit": 2,
            "shoetypecode": 150,
        },
        target_playerid=777,
    )
    assert plan.fields["height"] == 185
    assert plan.fields["weight"] == 80
    assert plan.fields["bodytypecode"] == 187
    assert plan.fields["runstylecode"] == 120
    assert plan.fields["runningcode1"] == 150
    assert plan.fields["jerseyfit"] == 2
    assert plan.fields["shoetypecode"] == 150
    assert plan.fields["bodytypecode"] != 99
    assert plan.fields["runstylecode"] != 99


def test_external_neymar_five_star_card_is_converted_to_fc_raw_storage(tmp_path: Path) -> None:
    """Universe FUT/career rows use 1..5 display stars, not FC's 0..4 field."""
    catalog = LocalCatalog(universe_path=_universe(tmp_path / "universe.sqlite"))
    fut_neymar = catalog.search("Neymar", year="26")[0]
    career_neymar = catalog.search("Neymar", year="25")[0]

    assert fut_neymar["source_kind"] == "fut"
    assert fut_neymar["skillmoves"] == 5
    assert LocalCatalog.prepare_cross_year_import(
        fut_neymar, target_playerid=777
    ).fields["skillmoves"] == 4
    assert LocalCatalog.prepare_cross_year_import(
        career_neymar, target_playerid=777
    ).fields["skillmoves"] == 4


def test_skillmove_import_preserves_raw_le_data_and_skips_invalid_external_values() -> None:
    raw = LocalCatalog.prepare_cross_year_import(
        {"source_kind": "le_base", "skillmoves": 0}, target_playerid=777
    )
    assert raw.fields["skillmoves"] == 0

    broken = LocalCatalog.prepare_cross_year_import(
        {"source_kind": "fut", "skillmoves": 0, "overall": 80}, target_playerid=777
    )
    assert "skillmoves" not in broken.fields
    assert any("skill moves were skipped" in warning for warning in broken.warnings)

    unknown = LocalCatalog.prepare_cross_year_import(
        {"skillmoves": 7, "overall": 80}, target_playerid=777
    )
    assert "skillmoves" not in unknown.fields
    assert any("outside FC's 0-4" in warning for warning in unknown.warnings)


def test_database_favorites_round_trip(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    catalog = LocalCatalog(state_db=db)
    card = {
        "name": "Neymar Jr", "year": 26, "person_id": 190871,
        "playerid": 190871, "variant_id": "999001", "variant": "Rare",
        "overallrating": 83,
    }
    assert catalog.toggle_favorite(card) is True
    assert catalog.is_favorite(card) is True
    assert catalog.favorites()[0]["variant_id"] == "999001"
    assert catalog.toggle_favorite(card) is False
    assert catalog.favorites() == ()
    db.close()


def test_favorite_without_state_db_is_honest() -> None:
    with pytest.raises(CatalogError):
        LocalCatalog().toggle_favorite(
            {"year": 26, "playerid": 1, "variant": "Base"}
        )


def test_staging_variant_keeps_live_target_and_navigates_to_player() -> None:
    store = Store()
    store.dispatch(E.TargetLocked(playerid=777, name="Career Target", source="squad"))
    store.dispatch(E.EditorLoaded(base={"overallrating": 70, "acceleration": 65}))
    destinations: list[str] = []
    ui = UiActions()
    ui.bind_navigation(destinations.append)
    svc = SimpleNamespace(store=store, catalog=LocalCatalog(), ui=ui)
    _stage_card(
        svc,
        {
            "_raw": {
                "name": "Neymar Jr", "year": 26, "person_id": 190871,
                "playerid": 190871, "variant_id": "50522519",
                "overall": 97, "attrs": '{"acceleration":97}',
            }
        },
    )
    state = store.snapshot()
    assert state.target.playerid == 777
    assert state.target.name == "Career Target"
    assert state.editor.dirty["overallrating"] == 97
    assert state.editor.dirty["acceleration"] == 97
    assert destinations == ["player"]


def test_staging_without_target_never_uses_catalog_identity_as_destination() -> None:
    store = Store()
    destinations: list[str] = []
    ui = UiActions()
    ui.bind_navigation(destinations.append)
    svc = SimpleNamespace(store=store, catalog=LocalCatalog(), ui=ui)
    _stage_card(
        svc,
        {
            "_raw": {
                "name": "Neymar Jr", "year": 26, "exists_in_fc26": 1,
                "person_id": 190871, "playerid": 190871,
                "variant_id": "50522519", "overall": 97,
            }
        },
    )
    state = store.snapshot()
    assert state.target.playerid is None
    assert not state.editor.dirty
    assert destinations == []
    assert "verified live squad" in state.status
