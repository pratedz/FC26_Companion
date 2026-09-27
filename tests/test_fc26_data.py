"""Tests for the FC 26 data oracle (``src/fc26_data.py``).

Two jobs: prove the generated tables actually resolve real players and real
nations, and prove the module still answers safely when those tables are absent.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import Any

import pytest

from src import fc26_data as fc

needs_tables = pytest.mark.skipif(
    not fc.tables_loaded(),
    reason="run `python tools/build_fc26_tables.py` to generate the tables",
)


# --------------------------------------------------------------------------
# generated tables
# --------------------------------------------------------------------------
@needs_tables
def test_tables_have_the_expected_shape() -> None:
    # Lower bounds, not equality: the game data grows with every title update.
    assert len(fc.FC26_REAL_HEADS) >= 5800
    assert len(fc.FC26_HEAD_NAMES) >= 5700
    assert len(fc.NATION_NAMES) >= 150
    assert len(fc.LEAGUE_NAMES) >= 40
    assert len(fc.TEAM_NAMES) >= 600
    assert len(fc.GENERIC_HEADTYPES) >= 150
    assert fc.FC26_HEAD_NAMES.keys() <= fc.FC26_REAL_HEADS


@needs_tables
def test_sources_are_recorded_with_checksums() -> None:
    sources = fc.source_stats()
    assert {"headmodels_lua", "base_players_csv", "fc26_datahub_csv"} <= set(sources)
    for info in sources.values():
        assert len(str(info["sha256"])) == 64
        assert int(info["bytes"]) > 0


@needs_tables
def test_tables_are_read_only() -> None:
    """The oracle is shared state; a caller must not be able to corrupt it."""
    with pytest.raises(TypeError):
        fc.FC26_HEAD_NAMES[1] = "nope"  # type: ignore[index]
    with pytest.raises(TypeError):
        fc.NATION_NAMES[1] = "nope"  # type: ignore[index]


# --------------------------------------------------------------------------
# real heads
# --------------------------------------------------------------------------
@needs_tables
@pytest.mark.parametrize(
    "playerid, expected_name",
    [
        (1397, "Zidane"),
        (158023, "Messi"),
        (20801, "Ronaldo"),
        (1109, "Maldini"),
        (190871, "Neymar"),
    ],
)
def test_known_players_have_real_heads(playerid: int, expected_name: str) -> None:
    assert fc.has_real_head(playerid) is True
    name = fc.head_name(playerid)
    assert name is not None and expected_name.lower() in name.lower()


@needs_tables
def test_has_real_head_accepts_sloppy_ids() -> None:
    assert fc.has_real_head("1397") is True
    assert fc.has_real_head(1397.0) is True


@needs_tables
def test_unknown_ids_are_not_claimed_as_real() -> None:
    """A wrong "real head" produces an invisible player, so the default is False."""
    assert fc.has_real_head(999_999_999) is False
    assert fc.has_real_head(0) is False
    assert fc.has_real_head(None) is False
    assert fc.has_real_head("not a number") is False
    assert fc.head_name(999_999_999) is None
    assert fc.head_name(None) is None


# --------------------------------------------------------------------------
# nations - the add_player._NATION_NAME_TO_ID bug
# --------------------------------------------------------------------------
@needs_tables
def test_spain_is_45() -> None:
    assert fc.nation_id_for("Spain") == 45
    assert fc.nation_id_for("spain") == 45
    assert fc.nation_name(45) == "Spain"


@needs_tables
def test_nigeria_is_not_italy() -> None:
    """Regression for the hand-written table where ``"nigeria": 27`` = Italy.

    Every FUT card that arrived with a nation name instead of an id used to turn
    Nigerian players Italian.
    """
    nigeria = fc.nation_id_for("Nigeria")
    italy = fc.nation_id_for("Italy")
    assert nigeria == 133
    assert italy == 27
    assert nigeria != italy
    assert fc.nation_name(nigeria) == "Nigeria"


@needs_tables
@pytest.mark.parametrize(
    "spelling, expected",
    [
        ("USA", 95),
        ("United States", 95),
        ("united states of america", 95),
        ("Holland", 34),
        ("Ivory Coast", 108),
        ("Cote d'Ivoire", 108),
        ("Côte d'Ivoire", 108),
        ("Turkey", 48),
        ("Türkiye", 48),
        ("South Korea", 167),
        ("Korea Republic", 167),
        ("Czech Republic", 12),
        ("Ireland", 25),
        ("Republic of Ireland", 25),
        ("DR Congo", 110),
        ("China", 155),
    ],
)
def test_nation_aliases_and_accents(spelling: str, expected: int) -> None:
    assert fc.nation_id_for(spelling) == expected


@needs_tables
def test_nation_names_round_trip() -> None:
    """Every nation resolves back to its own id - no silent collisions."""
    assert dict(fc.NATION_ID_COLLISIONS) == {}
    for nation_id, name in fc.NATION_NAMES.items():
        assert fc.nation_id_for(name) == nation_id


@needs_tables
def test_nation_ids_pass_through() -> None:
    assert fc.nation_id_for(45) == 45
    assert fc.nation_id_for("45") == 45


def test_unknown_nation_is_none_not_a_guess() -> None:
    assert fc.nation_id_for("Wakanda") is None
    assert fc.nation_id_for("") is None
    assert fc.nation_id_for(None) is None
    assert fc.nation_name(9_999) is None
    assert fc.nation_name("nonsense") is None


# --------------------------------------------------------------------------
# leagues and clubs
# --------------------------------------------------------------------------
@needs_tables
def test_league_lookups() -> None:
    assert fc.league_name(13) == "Premier League"
    assert fc.league_name(53) == "La Liga"
    assert fc.league_id_for("La Liga") == 53
    assert fc.league_name(999_999) is None


@needs_tables
def test_ambiguous_league_names_are_documented() -> None:
    """"Premier League" is England (13) and Russia (332); the biggest one wins."""
    assert fc.league_id_for("Premier League") == 13
    assert fc.LEAGUE_ID_COLLISIONS["premier league"] == (13, 332)
    assert fc.LEAGUE_ID_COLLISIONS["bundesliga"] == (19, 80)


@needs_tables
@pytest.mark.parametrize(
    "name, expected",
    [
        ("Real Madrid", 243),
        ("real madrid", 243),
        ("FC Barcelona", 241),
        ("Manchester United", 11),
        ("Man Utd", 11),
        ("FC Bayern München", 21),
        ("Bayern Munich", 21),
        ("PSG", 73),
        ("Paris Saint-Germain", 73),
        ("Benfica", 234),
        ("Wolves", 110),
        # noise-word index: "CD Leganés" without the "CD", accents folded
        ("Leganes", 100888),
    ],
)
def test_team_lookups(name: str, expected: int) -> None:
    assert fc.team_id_for(name) == expected
    assert fc.team_name(expected) is not None


@needs_tables
def test_every_alias_resolves() -> None:
    """A dead alias is worse than none: it looks handled and returns None."""
    for alias in fc._NATION_ALIASES:
        assert fc.nation_id_for(alias) is not None, alias
    for alias in fc._TEAM_ALIASES:
        assert fc.team_id_for(alias) is not None, alias


@needs_tables
def test_team_names_round_trip() -> None:
    for team_id, name in fc.TEAM_NAMES.items():
        assert fc.team_id_for(name) == team_id


def test_unknown_team_is_none() -> None:
    assert fc.team_id_for("Hogwarts United") is None
    assert fc.team_name(None) is None
    assert fc.league_id_for("") is None


# --------------------------------------------------------------------------
# roles (not generated - always available)
# --------------------------------------------------------------------------
def test_role_plus_and_plus_plus() -> None:
    assert fc.role_name(21) == "CM Playmaker+"
    assert fc.role_name(121) == "CM Playmaker++"
    assert fc.role_name(0) == "None"
    assert fc.role_name(1) == "GK Goalkeeper+"
    assert fc.role_name(152) == "LW False Winger++"


def test_role_table_covers_every_id() -> None:
    assert len(fc.BASE_ROLE_NAMES) == 52
    assert sorted(fc.BASE_ROLE_NAMES) == list(range(1, 53))
    # 0 plus 52 "+" plus 52 "++"
    assert len(fc.ROLE_NAMES) == 105
    for role_id, label in fc.BASE_ROLE_NAMES.items():
        assert fc.role_name(role_id) == label + "+"
        assert fc.role_name(role_id + 100) == label + "++"
    assert len(set(fc.BASE_ROLE_NAMES.values())) == 52


def test_impossible_role_ids_are_none() -> None:
    """53..100 and >152 cannot be stored by the game; surface them, don't render them."""
    for bad in (-1, 53, 99, 100, 153, 999):
        assert fc.role_name(bad) is None
    assert fc.role_name(None) is None
    assert fc.role_name("banana") is None


def test_role_name_accepts_string_ids() -> None:
    assert fc.role_name("21") == "CM Playmaker+"
    assert fc.role_name(21.0) == "CM Playmaker+"


# --------------------------------------------------------------------------
# generic heads
# --------------------------------------------------------------------------
@needs_tables
def test_generic_headtype_is_valid_for_the_nation() -> None:
    pool = fc.generic_headtypes("Spain")
    assert len(pool) > 10
    picked = fc.pick_generic_headtype("Spain", "player-1")
    assert picked in pool


@needs_tables
def test_generic_headtype_is_deterministic() -> None:
    first = fc.pick_generic_headtype("Brazil", 4242)
    for _ in range(5):
        assert fc.pick_generic_headtype("Brazil", 4242) == first
    # id and name are the same nation
    assert fc.pick_generic_headtype(54, 4242) == first


@needs_tables
def test_generic_headtype_varies_with_the_seed() -> None:
    picks = {fc.pick_generic_headtype("England", seed) for seed in range(50)}
    assert len(picks) > 1


@needs_tables
def test_generic_headtype_falls_back_for_unknown_nations() -> None:
    """Unknown nation still yields a code EA ships, never an invented one."""
    every_code = {code for code, _ in fc.GENERIC_HEADTYPES_ANY}
    for nation in ("Wakanda", None, 0, ""):
        picked = fc.pick_generic_headtype(nation, "seed")
        assert picked in every_code
    assert fc.generic_headtypes("Wakanda") == ()


# --------------------------------------------------------------------------
# graceful degradation
# --------------------------------------------------------------------------
def _load_without_tables(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import a private copy of fc26_data with the generated tables unreachable."""
    for name in ("src.generated.fc26_tables", "generated.fc26_tables"):
        monkeypatch.setitem(sys.modules, name, None)  # makes `import` raise
    spec = importlib.util.spec_from_file_location("fc26_data_no_tables", fc.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_imports_and_answers_without_generated_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare: Any = _load_without_tables(monkeypatch)
    assert bare.tables_loaded() is False
    assert bare.has_real_head(1397) is False
    assert bare.head_name(1397) is None
    assert bare.nation_id_for("Spain") is None
    assert bare.nation_name(45) is None
    assert bare.league_name(13) is None
    assert bare.team_id_for("Real Madrid") is None
    assert bare.pick_generic_headtype("Spain", 1) is None
    assert bare.generic_headtypes("Spain") == ()
    assert dict(bare.source_stats()) == {}
    # roles are hand-maintained, so they survive a missing data file
    assert bare.role_name(121) == "CM Playmaker++"


def test_normalize_name_is_stable() -> None:
    assert fc.normalize_name("  FC Bayern MÜNCHEN ") == "fc bayern munchen"
    assert fc.normalize_name("Brighton & Hove Albion") == "brighton and hove albion"
    assert fc.normalize_name("Côte d'Ivoire") == "cote d ivoire"
    assert fc.normalize_name("Borussia Mönchengladbach") == "borussia monchengladbach"
    assert fc.normalize_name(None) == ""
    assert fc.normalize_name(123) == "123"
