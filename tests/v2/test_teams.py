import sqlite3

from companion.domain.teams import search_clubs


def _directory(tmp_path):
    path = tmp_path / "teams.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE league (league_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE team (
              team_id INTEGER PRIMARY KEY, name TEXT, name_norm TEXT,
              league_id INTEGER, is_club INTEGER, player_count INTEGER
            );
            INSERT INTO league VALUES (53, 'LaLiga');
            INSERT INTO team VALUES (241, 'FC Barcelona', 'fc barcelona', 53, 1, 24);
            INSERT INTO team VALUES (999, 'Not a Club', 'not a club', 53, 0, 1);
            """
        )
    return path


def test_search_clubs_returns_only_explicit_destination_choices(tmp_path):
    db = _directory(tmp_path)
    assert search_clubs(db, "fc bar") == [
        {"team_id": 241, "name": "FC Barcelona", "league_name": "LaLiga"}
    ]
    assert search_clubs(db, "241")[0]["name"] == "FC Barcelona"
    assert search_clubs(db, "999") == []


def test_search_clubs_degrades_safely_when_directory_is_missing(tmp_path):
    assert search_clubs(tmp_path / "missing.sqlite", "Barcelona") == []
