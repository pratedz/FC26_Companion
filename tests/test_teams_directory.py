"""Tests for the team / league / nation directory.

The database is a build artefact, not a source file, so every test that needs
it skips cleanly when ``card_db/teams.sqlite`` has not been built. The
degradation tests run either way — a missing directory must never raise.

Build it with::

    python tools/build_teams_directory.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src import paths
from src import teams_directory as td


requires_db = pytest.mark.skipif(
    not td.db_path().is_file(),
    reason="card_db/teams.sqlite not built — run tools/build_teams_directory.py",
)


@pytest.fixture(autouse=True)
def _fresh_connection():
    """Drop the cached read-only handle so a test never inherits one."""
    td.close()
    yield
    td.close()


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def test_normalize_name_folds_diacritics_and_case():
    assert td.normalize_name("FC Bayern München") == "fc bayern munchen"
    assert td.normalize_name("  Atlético   de  Madrid ") == "atletico de madrid"
    assert td.normalize_name(None) == ""
    assert td.normalize_name(123) == "123"


# --------------------------------------------------------------------------
# Graceful degradation — the DB is optional
# --------------------------------------------------------------------------


@pytest.fixture()
def missing_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    empty = tmp_path / "card_db"
    empty.mkdir()
    monkeypatch.setattr(paths, "card_db_dir", lambda: empty)
    td.close()
    return empty


def test_everything_degrades_when_not_built(missing_db: Path):
    assert td.available() is False
    assert td.stats() is None
    assert td.get_team(10) is None
    assert td.team_name(10) is None
    assert td.get_league(13) is None
    assert td.nation_name(14) is None
    assert td.search_teams("chelsea") == []
    assert td.search_leagues("premier") == []
    assert td.search_nations("england") == []
    assert td.teams_in_league(13) == []
    assert td.reconcile_live_teams(missing_db / "nope.json") is None
    assert td.load_live_teams(missing_db / "nope.json") is None

    resolved = td.resolve_team("chelsea")
    assert resolved["match"] is None
    assert resolved["alternatives"] == []
    assert resolved["available"] is False

    # The four pseudo-leagues are constants, so they still name themselves.
    assert td.league_name(78) == "International"


def test_damaged_db_degrades_without_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    card_db = tmp_path / "card_db"
    card_db.mkdir()
    (card_db / td.DB_FILENAME).write_bytes(b"this is not a sqlite database at all")
    monkeypatch.setattr(paths, "card_db_dir", lambda: card_db)
    td.close()
    assert td.available() is False
    assert td.search_teams("chelsea") == []
    assert td.get_team(10) is None


def test_resolve_team_never_raises_on_junk_input():
    for value in (None, "", "   ", 0, -1, "!!!", "'; DROP TABLE team; --", "🙂"):
        out = td.resolve_team(value)
        assert isinstance(out, dict)
        assert set(out) >= {"query", "kind", "match", "alternatives", "ambiguous"}


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


@requires_db
def test_schema_has_the_documented_columns():
    conn = sqlite3.connect("file:{}?mode=ro".format(td.db_path()), uri=True)
    try:
        def columns(table):
            return [r[1] for r in conn.execute("PRAGMA table_info({})".format(table))]

        assert columns("team") == [
            "team_id", "name", "name_norm", "league_id", "country_id", "is_club",
            "first_seen_year", "last_seen_year", "player_count",
        ]
        assert columns("league") == [
            "league_id", "name", "name_norm", "country_id", "is_real_competition",
        ]
        assert columns("nation") == ["nation_id", "name", "name_norm"]
    finally:
        conn.close()


@requires_db
def test_fts_index_uses_the_diacritic_folding_tokenizer():
    conn = sqlite3.connect("file:{}?mode=ro".format(td.db_path()), uri=True)
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'team_fts'"
        ).fetchone()
    finally:
        conn.close()
    assert ddl is not None, "team_fts virtual table is missing"
    assert "fts5" in ddl[0].lower()
    assert "unicode61 remove_diacritics 2" in ddl[0]


@requires_db
def test_stats_reports_row_counts():
    stats = td.stats()
    assert stats is not None
    assert stats["teams"] > 500
    assert stats["clubs"] > 500
    assert stats["leagues"] > 40
    assert stats["nations"] > 100
    assert stats["clubs"] + stats["non_clubs"] == stats["teams"]
    assert stats["meta"]["schema_version"] == str(td.SCHEMA_VERSION)
    # Every source we read is accounted for.
    files = {row["source_file"] for row in stats["sources"]}
    assert "fc26_datahub.csv" in files
    assert "futgg_26.jsonl" in files
    assert "fifa22.csv" in files


# --------------------------------------------------------------------------
# Well-known clubs
#
# The ids below were read back out of the shipped data, not taken on trust:
# fc26_datahub.csv, fifa22.csv and futgg_26.jsonl independently agree, and
# LE's own lua/DOC.MD documents Arsenal 1 / Everton 7 / FC Barcelona 241.
# --------------------------------------------------------------------------


@requires_db
@pytest.mark.parametrize(
    ("team_id", "name", "league_id"),
    [
        (10, "Manchester City", 13),
        (5, "Chelsea", 13),
        (1, "Arsenal", 13),
        (7, "Everton", 13),
        (241, "FC Barcelona", 53),
        (243, "Real Madrid", 53),
    ],
)
def test_known_club_resolves_both_ways(team_id: int, name: str, league_id: int):
    team = td.get_team(team_id)
    assert team is not None, "team {} missing from the directory".format(team_id)
    assert team["name"] == name
    assert team["league_id"] == league_id
    assert team["is_club"] is True
    assert td.team_name(team_id) == name

    hits = td.search_teams(name, limit=5)
    assert hits, "searching {!r} found nothing".format(name)
    assert hits[0]["team_id"] == team_id
    assert hits[0]["league_name"] == td.league_name(league_id)


@requires_db
def test_search_ranks_the_senior_mens_club_first():
    # "Manchester City" is also the name of team 116017 (the women's side).
    hits = td.search_teams("manchester city", limit=5)
    assert hits[0]["team_id"] == 10
    assert hits[0]["player_count"] > 1


@requires_db
def test_broadcast_short_names_are_searchable():
    """FUT.GG's short forms are kept as aliases, never as display names."""
    for query, team_id, full_name in (
        ("spurs", 18, "Tottenham Hotspur"),
        ("man utd", 11, "Manchester United"),
        ("qpr", 15, "Queens Park Rangers"),
    ):
        hits = td.search_teams(query, limit=5)
        assert hits, "no hit for {!r}".format(query)
        assert hits[0]["team_id"] == team_id
        assert hits[0]["name"] == full_name


# --------------------------------------------------------------------------
# Non-clubs
# --------------------------------------------------------------------------


@requires_db
def test_free_agents_is_not_a_club():
    team = td.get_team(td.FREE_AGENT_TEAM_ID)
    assert td.FREE_AGENT_TEAM_ID == 111592
    assert team is not None
    assert team["is_club"] is False
    assert "free" in (team.get("note") or "").lower()
    # A club picker must never offer it.
    assert all(
        hit["team_id"] != td.FREE_AGENT_TEAM_ID
        for hit in td.search_teams("free agents", limit=25, clubs_only=True)
    )
    # ... but it is still findable when non-clubs are allowed.
    assert any(
        hit["team_id"] == td.FREE_AGENT_TEAM_ID
        for hit in td.search_teams("free agents", limit=25, clubs_only=False)
    )


@requires_db
@pytest.mark.parametrize("league_id", [76, 78, 2136, 383])
def test_pseudo_leagues_hold_no_clubs(league_id: int):
    league = td.get_league(league_id)
    assert league is not None, "pseudo-league {} missing".format(league_id)
    assert league["is_real_competition"] is False
    assert league["name"] == td.PSEUDO_LEAGUES[league_id]
    # Nothing inside them may be offered as a club.
    assert td.teams_in_league(league_id, clubs_only=True) == []
    for team in td.teams_in_league(league_id, clubs_only=False):
        assert team["is_club"] is False


@requires_db
def test_national_teams_are_flagged_non_club():
    england = td.get_team(1318)
    assert england is not None
    assert england["is_club"] is False
    assert england["league_id"] == td.LEAGUE_INTERNATIONAL


@requires_db
def test_fut_only_constructs_are_flagged_non_club():
    """FUT.GG reports ICON/HERO as clubs; they have no career squad."""
    for team_id in td.FUT_ONLY_TEAMS:
        team = td.get_team(team_id)
        assert team is not None
        assert team["is_club"] is False
        assert team["league_id"] is None


# --------------------------------------------------------------------------
# Search behaviour
# --------------------------------------------------------------------------


@requires_db
def test_diacritic_insensitive_search():
    plain = td.search_teams("munchen", limit=10)
    accented = td.search_teams("München", limit=10)
    assert plain, "'munchen' should find the Munich clubs"
    ids_plain = [h["team_id"] for h in plain]
    ids_accented = [h["team_id"] for h in accented]
    assert ids_plain == ids_accented
    assert 21 in ids_plain, "FC Bayern München (21) not found"
    assert any("München" in h["name"] for h in plain)

    # And the other direction: an accented query must find the same rows.
    assert td.search_teams("atletico")[0]["team_id"] == \
        td.search_teams("Atlético")[0]["team_id"]


@requires_db
def test_middle_of_name_search_via_fts():
    hits = td.search_teams("forest", limit=10)
    assert 14 in [h["team_id"] for h in hits], "Nottingham Forest not matched on 'forest'"


@requires_db
def test_search_teams_respects_limit_and_returns_the_documented_shape():
    hits = td.search_teams("real", limit=3)
    assert 0 < len(hits) <= 3
    for hit in hits:
        assert set(hit) >= {"team_id", "name", "league_name", "country"}
        assert isinstance(hit["team_id"], int)
        assert isinstance(hit["name"], str) and hit["name"]
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


@requires_db
def test_clubs_only_is_the_default():
    assert all(h["is_club"] for h in td.search_teams("a", limit=50))


@requires_db
def test_empty_query_returns_nothing():
    assert td.search_teams("") == []
    assert td.search_teams("   ") == []
    assert td.search_leagues("") == []


# --------------------------------------------------------------------------
# resolve_team — id or name
# --------------------------------------------------------------------------


@requires_db
def test_resolve_team_accepts_a_numeric_id():
    for value in (10, "10", " 10 "):
        out = td.resolve_team(value)
        assert out["kind"] == "id"
        assert out["match"]["team_id"] == 10
        assert out["match"]["name"] == "Manchester City"
        assert out["match"]["matched_on"] == "id"


@requires_db
def test_resolve_team_accepts_a_name():
    out = td.resolve_team("chelsea")
    assert out["kind"] == "name"
    assert out["match"]["team_id"] == 5
    assert isinstance(out["alternatives"], list)


@requires_db
def test_resolve_team_offers_alternatives():
    out = td.resolve_team("real")
    assert out["match"] is not None
    assert out["alternatives"], "an ambiguous name should offer alternatives"
    ids = {out["match"]["team_id"]} | {a["team_id"] for a in out["alternatives"]}
    assert len(ids) == 1 + len(out["alternatives"]), "alternatives repeat the match"


@requires_db
def test_resolve_team_reports_an_unknown_id():
    out = td.resolve_team("999999999")
    assert out["kind"] == "id-unknown"
    assert out["match"] is None


@requires_db
def test_resolve_team_finds_non_clubs_too():
    """A transfer *from* the free-agent pool has to be resolvable."""
    out = td.resolve_team(str(td.FREE_AGENT_TEAM_ID))
    assert out["match"] is not None
    assert out["match"]["is_club"] is False


# --------------------------------------------------------------------------
# Leagues and nations
# --------------------------------------------------------------------------


@requires_db
def test_league_lookup():
    assert td.league_name(13) == "Premier League"
    league = td.get_league(13)
    assert league["is_real_competition"] is True
    assert league["country"] == "England"
    assert league["team_count"] >= 20


@requires_db
def test_search_leagues_matches_sponsor_names():
    """The datahub says "La Liga", FUT.GG says "LALIGA EA SPORTS"."""
    hits = td.search_leagues("laliga")
    assert hits and hits[0]["league_id"] == 53
    assert any(h["league_id"] == 54 for h in hits)


@requires_db
def test_teams_in_league_is_a_full_squad_list():
    teams = td.teams_in_league(13)
    assert len(teams) == 20, "the Premier League should have exactly 20 clubs"
    assert all(t["is_club"] for t in teams)
    counts = [t["player_count"] for t in teams]
    assert counts == sorted(counts, reverse=True)


@requires_db
def test_nation_lookup_matches_the_le_ids():
    assert td.nation_name(14) == "England"
    assert td.nation_name(45) == "Spain"
    assert td.nation_name(-1) is None
    assert td.search_nations("england")[0]["nation_id"] == 14


@requires_db
def test_ids_are_stable_across_years():
    """A FIFA 22 era club keeps a usable id even though FC 26 dropped it."""
    stats = td.stats()
    assert stats["teams_by_last_seen"].get(22, 0) > 0, "no historical-only clubs kept"
    conn = sqlite3.connect("file:{}?mode=ro".format(td.db_path()), uri=True)
    try:
        row = conn.execute(
            "SELECT team_id, name, first_seen_year FROM team "
            "WHERE last_seen_year = 22 AND is_club = 1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert td.get_team(row[0])["last_seen_year"] == 22


# --------------------------------------------------------------------------
# Live-save reconciliation Lua
# --------------------------------------------------------------------------


def test_generated_lua_reads_the_live_teams_table(tmp_path: Path):
    out = tmp_path / "live_teams.json"
    lua = td.generate_list_teams_lua(out)

    assert 'GetDBTableRows, "teams"' in lua
    assert "__OUT_PATH__" not in lua
    assert str(out.resolve()).replace("\\", "/") in lua
    # Forward slashes only: a backslash would start an escape inside the string.
    assert "\\U" not in lua and "\\D" not in lua


def test_generated_lua_passes_the_project_lua_linter(tmp_path: Path):
    """Reuse tests/lua_lint.py when it is present — no Lua runtime here."""
    lua_lint = pytest.importorskip("tests.lua_lint")
    issues = lua_lint.lint(td.generate_list_teams_lua(tmp_path / "live_teams.json"))
    assert not issues, lua_lint.format_issues(issues)


def test_generated_lua_guards_every_global(tmp_path: Path):
    lua = td.generate_list_teams_lua(tmp_path / "live_teams.json")
    for name in ("GetDBTableRows", "GetTeamName", "Log"):
        assert 'type({}) == "function"'.format(name) in lua, name
    # It must not be able to fail a bridge run.
    assert "assert(" not in lua
    assert "error(" not in lua
    assert lua.count("pcall") >= 4
    # Balanced block comment / do-end structure, cheaply checked.
    assert lua.count("--[[") == lua.count("]]")


def test_generated_lua_defaults_under_the_app_root(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path):
    monkeypatch.setattr(paths, "app_root", lambda: tmp_path)
    assert td.live_teams_path() == tmp_path / "generated" / td.LIVE_TEAMS_FILENAME
    lua = td.generate_list_teams_lua()
    assert "live_teams.json" in lua


def test_load_live_teams_round_trip(tmp_path: Path):
    out = tmp_path / "live_teams.json"
    out.write_text(
        '{"source":"GetDBTableRows","count":2,'
        '"teams":[{"team_id":10,"name":"Man City FC"},'
        '{"team_id":999999,"name":"Modded United"}]}',
        encoding="utf-8",
    )
    live = td.load_live_teams(out)
    assert live == {10: "Man City FC", 999999: "Modded United"}


def test_load_live_teams_survives_garbage(tmp_path: Path):
    bad = tmp_path / "live_teams.json"
    bad.write_text("{not json at all", encoding="utf-8")
    assert td.load_live_teams(bad) is None
    assert td.load_live_teams(tmp_path / "does_not_exist.json") is None


@requires_db
def test_reconcile_flags_modded_names(tmp_path: Path):
    out = tmp_path / "live_teams.json"
    out.write_text(
        '{"teams":[{"team_id":10,"name":"Man City FC"},'
        '{"team_id":5,"name":"Chelsea"},'
        '{"team_id":987654,"name":"Modded United"}]}',
        encoding="utf-8",
    )
    report = td.reconcile_live_teams(out)
    assert report is not None
    assert report["live_teams"] == 3
    assert report["matched"] == 2
    assert 987654 in report["only_in_save"]
    renamed = {r["team_id"]: r for r in report["renamed"]}
    assert 10 in renamed and renamed[10]["directory_name"] == "Manchester City"
    assert 5 not in renamed
