"""Tests for the cross-year Player Universe (src/universe.py).

Everything that needs the database is skipped when it has not been built, so a
fresh checkout still runs green:

    python tools/build_universe.py
    python -m pytest tests/test_universe.py -q
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from src import universe

MESSI = 158023
MBAPPE = 231747
ODEGAARD = 222665

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _db_exists() -> bool:
    try:
        return universe.db_path().is_file()
    except OSError:
        return False


requires_db = pytest.mark.skipif(
    not _db_exists(),
    reason="universe.sqlite not built — run `python tools/build_universe.py`",
)


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    """Direct read-only handle, for invariants the public API cannot express."""
    if not _db_exists():
        pytest.skip("universe.sqlite not built")
    connection = sqlite3.connect("file:{}?mode=ro".format(universe.db_path()), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Degrading gracefully without a database
# ---------------------------------------------------------------------------


def test_missing_database_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every entry point must return an empty answer, not blow up."""
    from src import paths

    empty = tmp_path / "no_card_db"
    empty.mkdir()
    monkeypatch.setattr(paths, "card_db_dir", lambda: empty)
    universe.close()
    try:
        assert universe.available() is False
        assert universe.search("messi") == []
        assert universe.search("", year=26, min_ovr=90) == []
        assert universe.get_person(MESSI) is None
        assert universe.versions_of(MESSI) == []
        assert universe.stats() is None
        assert universe.sources() == []
        assert list(universe.iter_persons()) == []
    finally:
        universe.close()


def test_corrupt_database_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src import paths

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / universe.DB_FILENAME).write_bytes(b"this is not a sqlite file at all")
    monkeypatch.setattr(paths, "card_db_dir", lambda: broken)
    universe.close()
    try:
        assert universe.available() is False
        assert universe.search("messi") == []
        assert universe.get_person(MESSI) is None
    finally:
        universe.close()


def test_never_targets_the_existing_catalog() -> None:
    """The universe lives in its own file; catalog.sqlite is not ours to touch."""
    assert universe.db_path().name == "universe.sqlite"
    assert universe.db_path().name != "catalog.sqlite"


# ---------------------------------------------------------------------------
# Pure helpers (no database needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Mbappé", "mbappe"),
        ("MBAPPE", "mbappe"),
        ("Ødegaard", "odegaard"),  # Ø does not decompose under NFKD
        ("Ibrahimović", "ibrahimovic"),
        ("N'Golo Kanté", "ngolo kante"),
        ("İlkay Gündoğan", "ilkay gundogan"),
        ("Müller", "muller"),
        ("  Rodri  ", "rodri"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_name(raw: object, expected: str) -> None:
    assert universe.normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("ST", 25), ("st", 25), (25, 25), ("25", 25), ("GK", 0), (0, 0),
     ("CM", 14), ("RW", 23), ("nonsense", None), (99, None), ("", None)],
)
def test_position_code(raw: object, expected: object) -> None:
    assert universe.position_code(raw) == expected


# ---------------------------------------------------------------------------
# Identity: Messi across the years
# ---------------------------------------------------------------------------


@requires_db
def test_available() -> None:
    assert universe.available() is True


@requires_db
def test_messi_resolves_across_multiple_years() -> None:
    person = universe.get_person(MESSI)
    assert person is not None
    assert "Messi" in person["display_name"]
    assert person["dob"] == "1987-06-24"
    assert person["first_seen_year"] == 18
    assert person["last_seen_year"] == 26
    # One person row, many years — the whole point of the schema.
    assert len(person["years"]) >= 8, person["years"]
    assert person["exists_in_fc26"] is True

    versions = universe.versions_of(MESSI)
    years = {v["year"] for v in versions}
    assert years >= {18, 19, 20, 21, 22, 23, 24, 25, 26}
    assert len(versions) > len(years), "FC 26 FUT variants should be present"
    assert {v["source"] for v in versions} >= {"sofifa", "ea_ratings", "futgg"}
    for version in versions:
        assert version["person_id"] == MESSI
        assert version["year"] in universe.YEARS


@requires_db
def test_messi_accepts_string_and_int_ids() -> None:
    assert universe.get_person(str(MESSI)) == universe.get_person(MESSI)
    assert universe.get_person("not-a-number") is None
    assert universe.versions_of("not-a-number") == []


@requires_db
def test_versions_of_can_filter_to_one_year() -> None:
    only_18 = universe.versions_of(MESSI, year=18)
    assert only_18 and all(v["year"] == 18 for v in only_18)


@requires_db
def test_person_attributes_are_the_le_field_names() -> None:
    """Every source is normalized onto LE names, incl. marking -> defensiveawareness."""
    from src.field_map import ATTR_FIELDS

    allowed = set(ATTR_FIELDS)
    for version in universe.versions_of(MESSI):
        attrs = version["attrs"]
        assert isinstance(attrs, dict)
        assert set(attrs) <= allowed, set(attrs) - allowed
        assert "marking" not in attrs
        assert all(1 <= value <= 99 for value in attrs.values()), attrs
    # FIFA <= 21 called it `marking`; it must have landed on the FC 26 name.
    legacy = [v for v in universe.versions_of(MESSI) if v["year"] <= 21]
    assert legacy
    assert all("defensiveawareness" in v["attrs"] for v in legacy)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@requires_db
@pytest.mark.parametrize("query", ["Mbappé", "Mbappe", "mbappe", "MBAPPE", "mbappé"])
def test_diacritic_insensitive_search(query: str) -> None:
    hits = universe.search(query, limit=10)
    assert MBAPPE in {h["person_id"] for h in hits}, query
    assert hits[0]["person_id"] == MBAPPE, "the famous one should rank first"


@requires_db
@pytest.mark.parametrize("query", ["Ødegaard", "odegaard", "ODEGAARD"])
def test_stroked_letters_fold_too(query: str) -> None:
    """Ø survives NFKD, so it needs explicit transliteration."""
    hits = universe.search(query, limit=10)
    assert ODEGAARD in {h["person_id"] for h in hits}, query


@requires_db
def test_search_returns_best_observation_per_year() -> None:
    hits = universe.search("Messi", limit=5)
    messi = next(h for h in hits if h["person_id"] == MESSI)
    observations: List[Dict[str, Any]] = messi["observations"]
    years = [o["year"] for o in observations]
    assert years == sorted(years, reverse=True), "newest year first"
    assert len(years) == len(set(years)), "exactly one observation per year"
    assert messi["best_observation"]["overall"] == max(
        o["overall"] for o in observations if o["overall"] is not None
    )


@requires_db
def test_search_year_filter_narrows_observations() -> None:
    hits = universe.search("Messi", year=18, limit=5)
    assert hits
    messi = next(h for h in hits if h["person_id"] == MESSI)
    assert [o["year"] for o in messi["observations"]] == [18]


@requires_db
def test_search_min_ovr_and_position_filters() -> None:
    keepers = universe.search("", year=26, position="GK", min_ovr=90, limit=20)
    assert keepers
    for hit in keepers:
        best = hit["best_observation"]
        assert best["overall"] >= 90
        assert 0 in [
            best["preferredposition{}".format(i)] for i in range(1, 5)
        ], best["positions"]
    # An impossible filter must come back empty rather than raise.
    assert universe.search("", year=18, min_ovr=100, limit=5) == []
    assert universe.search("Messi", position="not-a-position") == []


@requires_db
def test_search_browse_mode_is_ranked() -> None:
    hits = universe.search("", year=18, min_ovr=90, limit=10)
    assert hits
    overalls = [h["best_observation"]["overall"] for h in hits]
    assert overalls == sorted(overalls, reverse=True)


@requires_db
def test_search_handles_hostile_input() -> None:
    for query in ('"', "*", "AND OR NOT", "a" * 500, "'; DROP TABLE person;--", "()"):
        assert isinstance(universe.search(query, limit=3), list)
    assert universe.search("zzzz-no-such-player-zzzz") == []


@requires_db
def test_search_limit_is_respected() -> None:
    assert len(universe.search("", limit=7)) == 7
    assert len(universe.search("silva", limit=3)) <= 3


# ---------------------------------------------------------------------------
# Data-defect regressions
# ---------------------------------------------------------------------------


@requires_db
def test_fc24_ids_are_ea_ids_not_row_indices(conn: sqlite3.Connection) -> None:
    """fc24.csv's `id` column counts rows; the real id is the URL's last segment."""
    rows = conn.execute(
        "SELECT person_id, id_provenance FROM observation WHERE source_file = 'fc24.csv'"
    ).fetchall()
    assert len(rows) == 15845, "every fc24 row should be recoverable"
    ids = {r["person_id"] for r in rows}
    assert all(r["id_provenance"] == "url_recovered" for r in rows)

    # A row-index id space would be exactly 0..15844.
    assert ids != set(range(len(rows)))
    assert min(ids) > 15845, "no id may fall inside the row-index range"

    # Mbappé is row 0 in that file; his real id is 231747.
    mbappe = conn.execute(
        "SELECT person_id, display_name FROM observation "
        "WHERE source_file = 'fc24.csv' AND person_id = ?", (MBAPPE,)
    ).fetchone()
    assert mbappe is not None and "Mbapp" in mbappe["display_name"]

    # The ids must live in the same space as the SoFIFA dumps.
    shared = conn.execute(
        "SELECT COUNT(DISTINCT person_id) FROM observation WHERE source_file='fc24.csv' "
        "AND person_id IN (SELECT person_id FROM observation WHERE source='sofifa')"
    ).fetchone()[0]
    assert shared / len(ids) > 0.85, "fc24 ids should overlap the sofifa id space"


@requires_db
def test_no_duplicate_person_year_variant_source(conn: sqlite3.Connection) -> None:
    for grouping in (
        "person_id, year, source, variant",
        "person_id, year, source_file, variant",
    ):
        dupes = conn.execute(
            "SELECT COUNT(*) FROM (SELECT {} FROM observation "
            "GROUP BY {} HAVING COUNT(*) > 1)".format(grouping, grouping)
        ).fetchone()[0]
        assert dupes == 0, "duplicate rows for ({})".format(grouping)


@requires_db
def test_fc25_double_export_was_deduped(conn: sqlite3.Connection) -> None:
    """fc25.csv ships 37158 rows for 18579 players."""
    total, people = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT person_id) FROM observation "
        "WHERE source_file = 'fc25.csv'"
    ).fetchone()
    assert total == people == 18579
    ledger = conn.execute(
        "SELECT rows_read, rows_kept, drop_reasons FROM source_ledger "
        "WHERE source_file = 'fc25.csv'"
    ).fetchone()
    assert ledger["rows_read"] == 37158 and ledger["rows_kept"] == 18579
    assert "combined" in ledger["drop_reasons"]


@requires_db
def test_fifa23_duplicate_ids_were_dropped(conn: sqlite3.Connection) -> None:
    ledger = conn.execute(
        "SELECT rows_read, rows_kept, drop_reasons FROM source_ledger "
        "WHERE source_file = 'fifa23.csv'"
    ).fetchone()
    assert ledger["rows_read"] - ledger["rows_kept"] == 119
    assert "duplicate_id" in ledger["drop_reasons"]


@requires_db
def test_idless_and_duplicate_sources_are_documented(conn: sqlite3.Connection) -> None:
    """Every excluded file must say, in the database, why it was excluded."""
    excluded = {
        row["source_file"]: row
        for row in conn.execute(
            "SELECT source_file, status, rows_read, rows_kept, note, drop_reasons "
            "FROM source_ledger WHERE status = 'excluded'"
        )
    }
    expected = {
        "fifa22_sofifa.csv",       # byte-identical duplicate
        "fifa18_futhead.csv",      # no id column
        "fifa19_futhead.csv",
        "fifa20_futhead.csv",
        "fifa19_futbin_cards.csv",     # ID is a row index
        "fifa19_futbin_detailed.csv",  # row index + corrupt attribute alignment
    }
    assert expected <= set(excluded), expected - set(excluded)
    for name in expected:
        row = excluded[name]
        assert row["rows_kept"] == 0
        assert row["rows_read"] > 0
        assert row["note"], "an exclusion must be explained"
    assert "identical" in excluded["fifa22_sofifa.csv"]["note"]
    assert "corrupt" in excluded["fifa19_futbin_detailed.csv"]["note"]
    # ...and none of them contributed a single observation.
    for name in expected:
        assert conn.execute(
            "SELECT COUNT(*) FROM observation WHERE source_file = ?", (name,)
        ).fetchone()[0] == 0


@requires_db
def test_jersey_number_prefix_stripped_from_names(conn: sqlite3.Connection) -> None:
    """fifa23_official.csv glues the shirt number onto the name with U+00A0."""
    bad = conn.execute(
        r"SELECT COUNT(*) FROM person WHERE display_name GLOB '[0-9]*'"
    ).fetchone()[0]
    assert bad == 0


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


@requires_db
def test_fts_index_folds_diacritics(conn: sqlite3.Connection) -> None:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'person_fts'"
    ).fetchone()[0]
    assert "unicode61" in sql
    assert "remove_diacritics 2" in sql
    indexed = conn.execute("SELECT COUNT(*) FROM person_fts").fetchone()[0]
    people = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
    assert indexed == people


@requires_db
def test_every_observation_points_at_a_person(conn: sqlite3.Connection) -> None:
    orphans = conn.execute(
        "SELECT COUNT(*) FROM observation o "
        "LEFT JOIN person p ON p.person_id = o.person_id WHERE p.person_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM person WHERE obs_count = 0"
    ).fetchone()[0] == 0


@requires_db
def test_attr_fidelity_is_consistent(conn: sqlite3.Connection) -> None:
    values = {
        row[0] for row in conn.execute("SELECT DISTINCT attr_fidelity FROM observation")
    }
    assert values <= {"granular", "partial", "expanded_from_face", "none"}, values
    # The label must agree with the counters it summarizes.
    for label, condition in (
        ("granular", "attr_granular_n > 0 AND attr_expanded_n = 0"),
        ("partial", "attr_granular_n > 0 AND attr_expanded_n > 0"),
        ("expanded_from_face", "attr_granular_n = 0 AND attr_expanded_n > 0"),
        ("none", "attr_granular_n = 0 AND attr_expanded_n = 0"),
    ):
        wrong = conn.execute(
            "SELECT COUNT(*) FROM observation WHERE attr_fidelity = ? "
            "AND NOT ({})".format(condition), (label,)
        ).fetchone()[0]
        assert wrong == 0, label


@requires_db
def test_face_expansion_only_fills_gaps(conn: sqlite3.Connection) -> None:
    """Sources with all 34 granular columns must never be face-expanded."""
    for source_file in ("fifa18.csv", "fifa22.csv", "fc25.csv", "fc26_datahub.csv",
                        "cards.csv", "base_players.csv", "futgg_26.jsonl"):
        expanded = conn.execute(
            "SELECT COUNT(*) FROM observation WHERE source_file = ? "
            "AND attr_expanded_n > 0", (source_file,)
        ).fetchone()[0]
        assert expanded == 0, source_file
    # fc24 has no shortpassing/longpassing columns, so it must be partial.
    kinds = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT attr_fidelity FROM observation WHERE source_file='fc24.csv'"
        )
    }
    assert kinds == {"partial"}


@requires_db
def test_fifa20_21_empty_marking_column_is_backfilled(conn: sqlite3.Connection) -> None:
    """defending_marking is blank in every fifa20/fifa21 row — face stats cover it."""
    for source_file in ("fifa20.csv", "fifa21.csv"):
        note = conn.execute(
            "SELECT note FROM source_ledger WHERE source_file = ?", (source_file,)
        ).fetchone()[0]
        assert "defending_marking is empty" in note, source_file
        # Outfield players still end up with a defensiveawareness value.
        blob = conn.execute(
            "SELECT attrs FROM observation WHERE source_file = ? AND person_id = ?",
            (source_file, MESSI),
        ).fetchone()[0]
        attrs = json.loads(blob)
        assert attrs["defensiveawareness"] > 0
        assert len(attrs) == 34


@requires_db
def test_attrs_json_is_well_formed(conn: sqlite3.Connection) -> None:
    from src.field_map import ATTR_FIELDS

    allowed = set(ATTR_FIELDS)
    rows = conn.execute(
        "SELECT attrs FROM observation WHERE attr_fidelity != 'none' "
        "ORDER BY obs_id LIMIT 3000"
    ).fetchall()
    assert rows
    for (blob,) in rows:
        attrs = json.loads(blob)
        assert attrs, "a non-'none' row must carry attributes"
        assert set(attrs) <= allowed
        assert all(isinstance(v, int) and 1 <= v <= 99 for v in attrs.values())


@requires_db
def test_exists_in_fc26_tracks_base_players(conn: sqlite3.Connection) -> None:
    flagged = conn.execute(
        "SELECT COUNT(*) FROM person WHERE exists_in_fc26 = 1"
    ).fetchone()[0]
    in_base = conn.execute(
        "SELECT COUNT(DISTINCT person_id) FROM observation WHERE source = 'le_base'"
    ).fetchone()[0]
    assert flagged == in_base > 20000
    assert universe.get_person(MESSI)["exists_in_fc26"] is True


@requires_db
def test_first_and_last_seen_years_match_observations(conn: sqlite3.Connection) -> None:
    wrong = conn.execute(
        "SELECT COUNT(*) FROM person p JOIN ("
        "  SELECT person_id, MIN(year) lo, MAX(year) hi FROM observation GROUP BY person_id"
        ") o ON o.person_id = p.person_id "
        "WHERE p.first_seen_year != o.lo OR p.last_seen_year != o.hi"
    ).fetchone()[0]
    assert wrong == 0


@requires_db
def test_years_span_fifa18_to_fc26(conn: sqlite3.Connection) -> None:
    years = {
        row[0] for row in conn.execute("SELECT DISTINCT year FROM observation")
    }
    assert years == set(universe.YEARS) == set(range(18, 27))


@requires_db
def test_stats_reports_the_build() -> None:
    stats = universe.stats()
    assert stats is not None
    assert stats["persons"] > 20000
    assert stats["observations"] > 200000
    assert set(stats["by_year"]) == set(universe.YEARS)
    assert stats["meta"]["schema_version"] == str(universe.SCHEMA_VERSION)
    assert stats["meta"]["built_at"]
    assert stats["by_source"]["sofifa"] > 0
    assert stats["by_id_provenance"]["url_recovered"] == 15845
    assert len(stats["sources"]) >= 15


@requires_db
def test_sources_ledger_accounts_for_every_row() -> None:
    for entry in universe.sources():
        assert entry["rows_read"] == entry["rows_kept"] + entry["rows_dropped"], entry
        if entry["rows_dropped"]:
            assert entry["drop_reasons"], entry
