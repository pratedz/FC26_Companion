#!/usr/bin/env python3
"""Build card_db/universe.sqlite — the cross-year Player Universe.

Reads every player dump under ``card_db/`` plus the two LE ``player_presets``
CSVs and produces one clean, searchable database spanning FIFA 18 … FC 26.

    python tools/build_universe.py                # build with defaults
    python tools/build_universe.py --dry-run      # profile sources, write nothing
    python tools/build_universe.py --include-name-matched   # opt-in, see below

The existing ``card_db/catalog.sqlite`` is never opened or modified.

Data defects handled (each verified against the shipped files)
-------------------------------------------------------------
1.  ``fc24.csv``'s ``id`` column is a row index (0…15844), not an EA id. The
    real id is the last path segment of ``URL``; recoverable for 15845/15845
    rows with zero collisions.
2.  ``fc25.csv`` is exactly 2x duplicated (37158 rows / 18579 distinct
    ``Player ID``). The ``combined_*`` ``Source_File`` holds a full copy, so we
    keep those rows and drop the per-league re-exports.
3.  ``fifa22_sofifa.csv`` is byte-identical to ``fifa22.csv`` (md5
    d4e790640f118f43491548c166fbc5e6) — excluded outright.
4.  The FutHead dumps (18/19/20, 49189 rows) carry no player id, and so do
    *both* FUTBIN 19 files: their ``ID`` column is also a row index. Excluded
    by default; see ``--include-name-matched``.
5.  ``fifa23.csv`` has 119 duplicate ids — first occurrence wins.
6.  ``fifa20.csv`` and ``fifa21.csv`` have a 100% empty ``defending_marking``
    column, so defensiveawareness for outfield players is filled from the
    ``defending`` face stat and those rows are marked ``attr_fidelity='partial'``.
7.  ``fifa19_futbin_detailed.csv`` is structurally corrupt on top of having no
    id: adjacent columns hold duplicated values (Ball Control == Dribbling,
    Heading Accuracy == Marking, Standing Tackle == Sliding Tackle). Verified
    against fifa19 sofifa base cards (CR7 standing tackle 89 vs a true 31).
    Always excluded — even ``--include-name-matched`` will not load it.
8.  ``player_presets/base_players.csv`` has *blank* name columns for all 22348
    rows (only ``firstnameid`` / ``lastnameid`` are populated). It is loaded as
    an attribute + ``exists_in_fc26`` source; names come from other sources via
    the shared playerid.

Every decision above is recorded in the ``source_ledger`` table so it can be
audited from the app with ``src.universe.sources()``.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import universe  # noqa: E402
from src.field_map import ATTR_FIELDS, to_le_field  # noqa: E402

csv.field_size_limit(10_000_000)

ATTR_SET = frozenset(ATTR_FIELDS)
assert len(ATTR_SET) == 34, "expected the 34 LE player attributes"

BATCH = 4000

# --------------------------------------------------------------------------
# Alias layer on top of src.field_map (which we must not edit)
# --------------------------------------------------------------------------

#: Columns src.field_map does not know about yet. Keys are already normalized
#: the way :func:`_col_key` normalizes them.
EXTRA_ALIASES: Dict[str, str] = {
    # FIFA 22+ renamed `marking` -> `marking_awareness`; FC 26 calls it
    # defensiveawareness. FIFA <= 21 / 23 keep plain `marking`, which
    # src.field_map already maps.
    "defending_marking_awareness": "defensiveawareness",
    "marking_awareness": "defensiveawareness",
    # FC 25 dump names the mentality positioning column "Attack Position".
    "attack_position": "positioning",
    # FUTBIN detail pages label the GK block without a gk prefix.
    "diving": "gkdiving",
    "handling": "gkhandling",
    "kicking": "gkkicking",
    "reflexes": "gkreflexes",
}

#: Face stat -> the granular LE attributes it stands in for. Mirrors the
#: private ``src.field_map._FACE_EXPAND`` table (copied rather than imported so
#: this tool does not depend on another module's private name).
FACE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "pace": ("acceleration", "sprintspeed"),
    "shooting": (
        "finishing", "shotpower", "longshots", "volleys", "penalties", "positioning",
    ),
    "passing": (
        "vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve",
    ),
    "dribbling": (
        "agility", "balance", "reactions", "ballcontrol", "dribbling", "composure",
    ),
    "defending": (
        "interceptions", "headingaccuracy", "defensiveawareness",
        "standingtackle", "slidingtackle",
    ),
    "physical": ("jumping", "stamina", "strength", "aggression"),
}

#: The six face columns as they are spelled in the SoFIFA family.
SOFIFA_FACE: Dict[str, str] = {
    "pace": "pace",
    "shooting": "shooting",
    "passing": "passing",
    "dribbling": "dribbling",
    "defending": "defending",
    "physic": "physical",
}

FOOT_CODES = {"right": 1, "left": 2, "r": 1, "l": 2}

#: EA stores birthdates as days since the Gregorian epoch. Verified:
#: 147811 -> 1987-06-24 (Messi), 152008 -> 1998-12-20 (Mbappé).
EA_DATE_EPOCH = datetime.date(1582, 10, 14)

_TAGS = re.compile(r"<[^>]*>")
_NULLISH = frozenset({"", "nan", "none", "null", "n/a", "na", "-", "--"})


# --------------------------------------------------------------------------
# Scalar parsing
# --------------------------------------------------------------------------


def _col_key(name: str) -> str:
    """Normalize a header cell the way src.field_map normalizes its aliases."""
    key = str(name).strip().lower()
    for ch in (" ", "-", "."):
        key = key.replace(ch, "_")
    return key


def to_int(value: object) -> Optional[int]:
    """Tolerant int parse: ``"74+1"`` -> 74, ``"177cm / 5'10\\""`` -> 177."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if value != value else int(value)
    text = str(value).strip()
    if text.lower() in _NULLISH:
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    match = re.match(r"^(-?\d+)\s*[+\-]\s*\d+\s*$", text)  # SoFIFA "74+1"
    if match:
        return int(match.group(1))
    match = re.match(r"^\s*(-?\d+)", text)  # "177cm / ...", "33 years old"
    if match:
        return int(match.group(1))
    try:
        num = float(text)
    except ValueError:
        return None
    return None if num != num else int(num)


def clamp_attr(value: Optional[int]) -> Optional[int]:
    return None if value is None else max(1, min(99, value))


def clean_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = _TAGS.sub(" ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return None if text.lower() in _NULLISH else text


#: sofifa's HTML scrape glues the shirt number onto the name with a
#: non-breaking space ("22\xa0S. Sané"). 6901 of fifa23_official.csv's 17660
#: rows are affected; no other source shows it.
_JERSEY_PREFIX = re.compile(r"^\d{1,2}\s+(?=\D)")

#: "S. Sané" / "M. Ødegaard" — an initial, not a usable display name.
_ABBREVIATED = re.compile(r"^\S{1,2}\.\s")


def clean_person_name(value: object) -> Optional[str]:
    text = clean_text(value)
    if not text:
        return None
    return _JERSEY_PREFIX.sub("", text).strip() or None


def name_quality(name: Optional[str]) -> int:
    """0 for an abbreviated name, 1 for a real one. Ranks above recency."""
    return 0 if not name or _ABBREVIATED.match(name) else 1


def parse_dob(value: object) -> Optional[str]:
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    try:
        return datetime.date(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).isoformat()
    except ValueError:
        return None


def parse_ea_birthdate(value: object) -> Optional[str]:
    days = to_int(value)
    if days is None or not (100_000 <= days <= 200_000):
        return None
    try:
        return (EA_DATE_EPOCH + datetime.timedelta(days=days)).isoformat()
    except (OverflowError, ValueError):
        return None


def parse_foot(value: object) -> Optional[int]:
    """LE codes: 1 = right, 2 = left (confirmed against base_players.csv)."""
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in FOOT_CODES:
        return FOOT_CODES[text]
    code = to_int(value)
    return code if code in (1, 2) else None


def parse_positions(*values: object) -> List[int]:
    """Ordered, de-duplicated LE position codes from any positional text."""
    out: List[int] = []
    for value in values:
        if value is None:
            continue
        text = _TAGS.sub(" ", str(value))
        for token in re.split(r"[,/|;]+", text):
            code = universe.position_code(token.strip())
            if code is not None and code not in out:
                out.append(code)
    return out[:4]


def normalize_name(value: object) -> str:
    return universe.normalize_name(value)


def split_name(long_name: object, short_name: object) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Best-effort (firstname, surname, commonname) from free-text names."""
    full = clean_text(long_name)
    short = clean_text(short_name)
    first = surname = None
    if full:
        parts = full.split(" ")
        if len(parts) == 1:
            surname = parts[0]
        else:
            first, surname = parts[0], " ".join(parts[1:])
    common = short if (short and name_quality(short)) else None
    # "L. Messi" is an abbreviation, not a common name; "Rodri" is.
    return first, surname, common


# --------------------------------------------------------------------------
# Source specification
# --------------------------------------------------------------------------


@dataclass
class Spec:
    """One input file and how to read it."""

    filename: str
    year: int
    source: str
    kind: str  # career | ea_official | fut | le_base
    root: str = "card_db"  # card_db | presets
    id_col: Optional[str] = None
    id_from_url: Optional[str] = None
    #: obs column -> source column name
    fields: Dict[str, str] = dc_field(default_factory=dict)
    #: lowercased face column -> FACE_GROUPS key
    face: Dict[str, str] = dc_field(default_factory=dict)
    #: lowercased columns that must never feed the granular attribute pass
    ignore: Set[str] = dc_field(default_factory=set)
    #: True -> accept only columns literally named after an LE attribute
    attrs_exact: bool = False
    variant_from: Sequence[str] = ("",)
    variant_id_col: Optional[str] = None
    dob_is_ea_days: bool = False
    note: str = ""


SOFIFA_LEGACY_FIELDS = {
    "display_name": "short_name",
    "long_name": "long_name",
    "overall": "overall",
    "potential": "potential",
    "positions_text": "player_positions",
    "club_name": "club_name",
    "league_name": "league_name",
    "nationality_name": "nationality",
    "height": "height_cm",
    "weight": "weight_kg",
    "preferredfoot": "preferred_foot",
    "skillmoves": "skill_moves",
    "weakfoot": "weak_foot",
    "dob": "dob",
}

SOFIFA_MODERN_FIELDS = dict(
    SOFIFA_LEGACY_FIELDS,
    nationality_name="nationality_name",
    nationality_id="nationality_id",
    club_id="club_team_id",
)

# gk_* duplicates goalkeeping_* in the legacy dumps; keep the explicit block.
SOFIFA_IGNORE = {
    "gk_diving", "gk_handling", "gk_kicking", "gk_reflexes",
    "gk_positioning", "gk_speed", "goalkeeping_speed",
}


#: fifa20.csv and fifa21.csv ship a completely empty `defending_marking`
#: column (18483/18483 and 18944/18944 rows blank). Outfield players therefore
#: take defensiveawareness from the `defending` face stat and are marked
#: attr_fidelity='partial'; goalkeepers have blank face stats too, so they
#: simply carry 33 of the 34 attributes.
MARKING_EMPTY_YEARS = frozenset({20, 21})


def _sofifa_spec(year: int, filename: str, modern: bool) -> Spec:
    note = "SoFIFA career dump; sofifa_id is the EA playerid. " + (
        "defending_marking_awareness -> defensiveawareness" if modern
        else "defending_marking -> defensiveawareness"
    )
    if year in MARKING_EMPTY_YEARS:
        note += (
            ". WARNING: defending_marking is empty in every row of this file, so "
            "defensiveawareness falls back to the `defending` face stat"
        )
    return Spec(
        filename=filename,
        year=year,
        source="sofifa",
        kind="career",
        id_col="sofifa_id",
        fields=dict(SOFIFA_MODERN_FIELDS if modern else SOFIFA_LEGACY_FIELDS),
        face=dict(SOFIFA_FACE),
        ignore=set(SOFIFA_IGNORE),
        note=note,
    )


SPECS: List[Spec] = [
    _sofifa_spec(18, "fifa18.csv", modern=False),
    _sofifa_spec(19, "fifa19.csv", modern=False),
    _sofifa_spec(20, "fifa20.csv", modern=False),
    _sofifa_spec(21, "fifa21.csv", modern=False),
    _sofifa_spec(22, "fifa22.csv", modern=True),
    Spec(
        filename="fifa23.csv",
        year=23,
        source="sofifa",
        kind="career",
        id_col="ID",
        fields={
            "display_name": "Name",
            "long_name": "FullName",
            "overall": "Overall",
            "potential": "Potential",
            "positions_text": "Positions",
            "positions_fallback": "BestPosition",
            "club_name": "Club",
            "nationality_name": "Nationality",
            "height": "Height",
            "weight": "Weight",
            "preferredfoot": "PreferredFoot",
            "skillmoves": "SkillMoves",
            "weakfoot": "WeakFoot",
        },
        face={
            "pacetotal": "pace",
            "shootingtotal": "shooting",
            "passingtotal": "passing",
            "dribblingtotal": "dribbling",
            "defendingtotal": "defending",
            "physicalitytotal": "physical",
        },
        ignore={"basestats", "totalstats", "growth"},
        note="Kaggle FIFA 23 dump; ID is the sofifa/EA id. 119 duplicate ids.",
    ),
    Spec(
        filename="fifa23_official.csv",
        year=23,
        source="sofifa_official",
        kind="ea_official",
        id_col="ID",
        fields={
            "display_name": "Name",
            "overall": "Overall",
            "potential": "Potential",
            "positions_text": "Position",
            "club_name": "Club",
            "nationality_name": "Nationality",
            "height": "Height",
            "weight": "Weight",
            "preferredfoot": "Preferred Foot",
            "skillmoves": "Skill Moves",
            "weakfoot": "Weak Foot",
        },
        note=(
            "Meta only — carries no attributes at all (attr_fidelity='none'), "
            "and Position is raw HTML. Kept because 7251 of its ids are absent "
            "from fifa23.csv."
        ),
    ),
    Spec(
        filename="fc24.csv",
        year=24,
        source="ea_ratings",
        kind="ea_official",
        id_from_url="URL",
        fields={
            "display_name": "Name",
            "overall": "Overall",
            "positions_text": "Position",
            "club_name": "Club",
            "nationality_name": "Nation",
            "preferredfoot": "Preferred_foot",
            "skillmoves": "Skill_moves",
            "weakfoot": "Weak_foot",
        },
        face={
            "pace": "pace",
            "shooting": "shooting",
            "passing": "passing",
            "dribbling": "dribbling",
            "defending": "defending",
            "physicality": "physical",
        },
        ignore={"gk", "gender", "age", "att_work_rate", "def_work_rate"},
        note=(
            "ea.com ratings scrape. `id` is a row index — the real EA id is the "
            "last URL path segment. No potential column; no shortpassing / "
            "longpassing / GK columns, so those come from face expansion."
        ),
    ),
    Spec(
        filename="fc25.csv",
        year=25,
        source="sofifa_fc25",
        kind="career",
        id_col="Player ID",
        fields={
            "display_name": "Player",
            "overall": "Overall Score",
            "potential": "Potential Score",
            "positions_text": "Position",
            "positions_fallback": "Best Position",
            "league_name": "League",
            "height": "Height",
            "weight": "Weight",
            "preferredfoot": "Preferred Foot",
            "skillmoves": "Skill Moves",
            "weakfoot": "Weak Foot",
        },
        # All 34 granular attributes are present, so no face expansion is
        # wanted: the "Pace/Diving" style columns are GK/outfield unions and
        # would corrupt goalkeepers.
        face={},
        ignore={
            "total_attacking_score", "total_skill", "total_movement",
            "total_power", "total_mentality", "total_defending",
            "total_goalkeeping", "total_stats", "base_stats", "best_overall",
            "pace/diving", "shooting/handling", "passing/kicking",
            "dribbling/reflexes", "defending/pace", "physical/positioning",
        },
        note="~2x duplicated; the combined_* Source_File copy is authoritative.",
    ),
    Spec(
        filename="fc26_datahub.csv",
        year=26,
        source="sofifa_datahub",
        kind="career",
        id_col="player_id",
        fields=dict(SOFIFA_MODERN_FIELDS, league_id="league_id"),
        face=dict(SOFIFA_FACE),
        ignore=set(SOFIFA_IGNORE),
        note="FC 26 career dump (fifa_version 26, update 4).",
    ),
    Spec(
        filename="base_players.csv",
        root="presets",
        year=26,
        source="le_base",
        kind="le_base",
        id_col="playerid",
        fields={
            "overall": "overallrating",
            "potential": "potential",
            "height": "height",
            "weight": "weight",
            "preferredfoot": "preferredfoot",
            "skillmoves": "skillmoves",
            "weakfoot": "weakfootabilitytypecode",
            "nationality_id": "nationality",
            "dob": "birthdate",
            "trait1": "trait1",
            "trait2": "trait2",
            "icontrait1": "icontrait1",
            "icontrait2": "icontrait2",
        },
        attrs_exact=True,
        dob_is_ea_days=True,
        note=(
            "LE base roster — defines exists_in_fc26. All name columns are "
            "blank in this file; names are joined in from other sources."
        ),
    ),
    Spec(
        filename="cards.csv",
        root="presets",
        year=26,
        source="le_cards",
        kind="fut",
        id_col="playerid",
        fields={
            "display_name": "name",
            "overall": "overallrating",
            "skillmoves": "skillmoves",
            "weakfoot": "weakfootabilitytypecode",
            "trait1": "trait1",
            "trait2": "trait2",
            "icontrait1": "icontrait1",
            "icontrait2": "icontrait2",
        },
        attrs_exact=True,
        variant_from=("revision", "origin"),
        variant_id_col="uid",
        note="LE FUT preset cards (uid is unique; playerid repeats per variant).",
    ),
]

#: Files deliberately not loaded, with the reason recorded in source_ledger.
EXCLUDED: List[Tuple[str, str, Optional[int], str]] = [
    (
        "fifa22_sofifa.csv", "sofifa", 22,
        "byte-identical duplicate of fifa22.csv (md5 d4e790640f118f43491548c166fbc5e6)",
    ),
    (
        "fifa19_futbin_detailed.csv", "futbin", 19,
        "no player id (ID is a row index) AND corrupt attribute alignment: "
        "Ball Control==Dribbling, Heading Accuracy==Marking, "
        "Standing Tackle==Sliding Tackle; contradicts fifa19 sofifa base cards",
    ),
]

#: Id-less face-stat dumps. Skipped unless --include-name-matched.
NAME_MATCH_SPECS: List[Spec] = [
    Spec(
        filename="fifa18_futhead.csv", year=18, source="futhead", kind="fut",
        fields={"display_name": "NAME", "overall": "RATING",
                "club_name": "CLUB", "league_name": "LEAGUE",
                "positions_text": "POSITION"},
        face={"pace": "pace", "shooting": "shooting", "passing": "passing",
              "dribbling": "dribbling", "defending": "defending",
              "physical": "physical"},
        variant_from=("TIER",),
        note="No player id at all — attached by unambiguous same-year name match.",
    ),
    Spec(
        filename="fifa19_futhead.csv", year=19, source="futhead", kind="fut",
        fields={"display_name": "NAME", "overall": "RATING",
                "club_name": "CLUB", "league_name": "LEAGUE",
                "positions_text": "POSITION"},
        face={"pace": "pace", "shooting": "shooting", "passing": "passing",
              "dribbling": "dribbling", "defending": "defending",
              "physical": "physical"},
        variant_from=("TIER",),
        note="No player id at all — attached by unambiguous same-year name match.",
    ),
    Spec(
        filename="fifa20_futhead.csv", year=20, source="futhead", kind="fut",
        fields={"display_name": "NAME", "overall": "RATING",
                "club_name": "CLUB", "league_name": "LEAGUE",
                "positions_text": "POSITION"},
        face={"pace": "pace", "shooting": "shooting", "passing": "passing",
              "dribbling": "dribbling", "defending": "defending",
              "physical": "physical"},
        variant_from=("TIER",),
        note="No player id at all — attached by unambiguous same-year name match.",
    ),
    Spec(
        filename="fifa19_futbin_cards.csv", year=19, source="futbin", kind="fut",
        fields={"display_name": "Name", "overall": "Rating",
                "club_name": "Club", "league_name": "League",
                "positions_text": "Position", "skillmoves": "SkillsMoves",
                "weakfoot": "WeakFoot", "height": "Height",
                "nationality_name": "Country"},
        face={"pace": "pace", "shooting": "shooting", "passing": "passing",
              "dribbling": "dribbling", "defending": "defending",
              "phyiscality": "physical"},
        ignore={"id", "price", "popularity", "basestats", "ingamestats"},
        variant_from=("Revision",),
        note=("ID column is a row index, not an EA id — attached by "
              "unambiguous same-year name match."),
    ),
]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE person (
    person_id        INTEGER PRIMARY KEY,   -- EA playerid
    display_name     TEXT    NOT NULL,
    name_norm        TEXT    NOT NULL,      -- diacritic-folded lowercase
    firstname        TEXT,
    surname          TEXT,
    commonname       TEXT,
    dob              TEXT,                  -- ISO yyyy-mm-dd
    nationality_id   INTEGER,
    nationality_name TEXT,
    exists_in_fc26   INTEGER NOT NULL DEFAULT 0,
    first_seen_year  INTEGER,
    last_seen_year   INTEGER,
    best_overall     INTEGER,
    obs_count        INTEGER NOT NULL DEFAULT 0,
    year_mask        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE observation (
    obs_id             INTEGER PRIMARY KEY,
    person_id          INTEGER NOT NULL REFERENCES person(person_id),
    year               INTEGER NOT NULL,    -- 18 .. 26
    source             TEXT    NOT NULL,    -- logical source key
    source_file        TEXT    NOT NULL,
    source_kind        TEXT    NOT NULL,    -- career|ea_official|fut|le_base
    variant            TEXT    NOT NULL,    -- 'base', 'TOTY', ...
    variant_id         TEXT,                -- eaId / uid / slug
    display_name       TEXT,
    overall            INTEGER,
    potential          INTEGER,
    preferredposition1 INTEGER,
    preferredposition2 INTEGER,
    preferredposition3 INTEGER,
    preferredposition4 INTEGER,
    positions_text     TEXT,
    club_id            INTEGER,
    club_name          TEXT,
    league_id          INTEGER,
    league_name        TEXT,
    nationality_id     INTEGER,
    nationality_name   TEXT,
    height             INTEGER,
    weight             INTEGER,
    preferredfoot      INTEGER,             -- 1 right, 2 left
    skillmoves         INTEGER,
    weakfoot           INTEGER,
    trait1             INTEGER,
    trait2             INTEGER,
    icontrait1         INTEGER,
    icontrait2         INTEGER,
    roles              TEXT,                -- JSON array
    playstyles         TEXT,                -- JSON object
    attrs              TEXT NOT NULL,       -- JSON of the 34 LE attributes
    attr_fidelity      TEXT NOT NULL,       -- granular|partial|expanded_from_face|none
    attr_granular_n    INTEGER NOT NULL DEFAULT 0,
    attr_expanded_n    INTEGER NOT NULL DEFAULT 0,
    id_provenance      TEXT NOT NULL,       -- source_id|url_recovered|name_matched
    UNIQUE (person_id, year, source_file, variant)
);

CREATE TABLE source_ledger (
    source_file  TEXT PRIMARY KEY,
    source       TEXT,
    source_kind  TEXT,
    year         INTEGER,
    rows_read    INTEGER NOT NULL DEFAULT 0,
    rows_kept    INTEGER NOT NULL DEFAULT 0,
    rows_dropped INTEGER NOT NULL DEFAULT 0,
    drop_reasons TEXT,
    status       TEXT NOT NULL,             -- loaded | excluded
    note         TEXT
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

INDEXES = """
CREATE INDEX idx_person_name_norm ON person(name_norm);
CREATE INDEX idx_person_best      ON person(best_overall DESC);
CREATE INDEX idx_person_fc26      ON person(exists_in_fc26);
CREATE INDEX idx_obs_person_year  ON observation(person_id, year);
CREATE INDEX idx_obs_year_ovr     ON observation(year, overall DESC);
CREATE INDEX idx_obs_ovr          ON observation(overall DESC);
CREATE INDEX idx_obs_source       ON observation(source);
CREATE INDEX idx_obs_pos1         ON observation(preferredposition1);
"""

OBS_COLUMNS = (
    "person_id", "year", "source", "source_file", "source_kind", "variant",
    "variant_id", "display_name", "overall", "potential",
    "preferredposition1", "preferredposition2", "preferredposition3",
    "preferredposition4", "positions_text", "club_id", "club_name",
    "league_id", "league_name", "nationality_id", "nationality_name",
    "height", "weight", "preferredfoot", "skillmoves", "weakfoot",
    "trait1", "trait2", "icontrait1", "icontrait2", "roles", "playstyles",
    "attrs", "attr_fidelity", "attr_granular_n", "attr_expanded_n",
    "id_provenance",
)

INSERT_OBS = "INSERT OR IGNORE INTO observation ({}) VALUES ({})".format(
    ", ".join(OBS_COLUMNS), ", ".join("?" * len(OBS_COLUMNS))
)


# --------------------------------------------------------------------------
# Attribute extraction
# --------------------------------------------------------------------------


def build_column_map(header: Sequence[str], spec: Spec) -> Dict[str, str]:
    """source column -> LE attribute, for the granular pass only."""
    mapping: Dict[str, str] = {}
    claimed = {v for v in spec.fields.values()}
    for column in header:
        key = _col_key(column)
        if key in spec.ignore or key in spec.face or column in claimed:
            continue
        if spec.attrs_exact:
            if key in ATTR_SET:
                mapping[column] = key
            continue
        target = EXTRA_ALIASES.get(key) or to_le_field(column)
        if target in ATTR_SET:
            mapping.setdefault(column, target)
    return mapping


def extract_attrs(
    row: Dict[str, Any], column_map: Dict[str, str], spec: Spec
) -> Tuple[Dict[str, int], int, int]:
    """Return (attrs, granular_count, expanded_count).

    Face stats are only ever expanded into attributes the granular pass did not
    already fill, so a mostly-granular source keeps its real numbers.
    """
    attrs: Dict[str, int] = {}
    granular = 0
    for column, target in column_map.items():
        value = clamp_attr(to_int(row.get(column)))
        if value is not None and target not in attrs:
            attrs[target] = value
            granular += 1

    expanded = 0
    if spec.face:
        lowered = {_col_key(k): v for k, v in row.items()}
        for column, group in spec.face.items():
            value = clamp_attr(to_int(lowered.get(column)))
            if value is None:
                continue
            for target in FACE_GROUPS[group]:
                if target not in attrs:
                    attrs[target] = value
                    expanded += 1
    return attrs, granular, expanded


def fidelity_of(granular: int, expanded: int) -> str:
    if granular and expanded:
        return "partial"
    if granular:
        return "granular"
    if expanded:
        return "expanded_from_face"
    return "none"


# --------------------------------------------------------------------------
# Identity accumulation
# --------------------------------------------------------------------------


class Identity:
    """Best-so-far identity fields for one person, resolved by source rank."""

    __slots__ = ("name", "name_rank", "parts", "parts_rank", "dob", "dob_rank",
                 "nat_id", "nat_id_rank", "nat_name", "nat_name_rank")

    def __init__(self) -> None:
        self.name: Optional[str] = None
        self.name_rank: Tuple[int, int, int] = (-1, -1, -1)
        self.parts: Tuple[Optional[str], Optional[str], Optional[str]] = (None, None, None)
        self.parts_rank = -1
        self.dob: Optional[str] = None
        self.dob_rank = -1
        self.nat_id: Optional[int] = None
        self.nat_id_rank = -1
        self.nat_name: Optional[str] = None
        self.nat_name_rank = -1

    def offer_name(self, name: Optional[str], year: int, prio: int) -> None:
        """A real name always beats an initial; then newest year, then source."""
        if not name:
            return
        rank = (name_quality(name), year, prio)
        if rank > self.name_rank:
            self.name, self.name_rank = name, rank

    def offer_parts(self, parts: Tuple[Optional[str], Optional[str], Optional[str]],
                    prio: int) -> None:
        if not any(parts):
            return
        if prio > self.parts_rank:
            merged = tuple(new or old for new, old in zip(parts, self.parts))
            self.parts = merged  # type: ignore[assignment]
            self.parts_rank = prio

    def offer_dob(self, dob: Optional[str], prio: int) -> None:
        if dob and prio > self.dob_rank:
            self.dob, self.dob_rank = dob, prio

    def offer_nat_id(self, value: Optional[int], prio: int) -> None:
        if value is not None and value >= 0 and prio > self.nat_id_rank:
            self.nat_id, self.nat_id_rank = value, prio

    def offer_nat_name(self, value: Optional[str], prio: int) -> None:
        if value and prio > self.nat_name_rank:
            self.nat_name, self.nat_name_rank = value, prio


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------


class Builder:
    def __init__(self, conn: sqlite3.Connection, card_db: Path, presets: Path,
                 collect_names: bool, verbose: bool = True) -> None:
        self.conn = conn
        self.card_db = card_db
        self.presets = presets
        self.verbose = verbose
        self.identities: Dict[int, Identity] = defaultdict(Identity)
        self.seen_variants: Set[Tuple[int, int, str, str]] = set()
        self.pending: List[Tuple[Any, ...]] = []
        self.ledger: List[Tuple[Any, ...]] = []
        self.collect_names = collect_names
        self.year_names: Dict[int, Dict[str, Set[int]]] = defaultdict(lambda: defaultdict(set))

    # -- plumbing ---------------------------------------------------------

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def path_for(self, spec: Spec) -> Path:
        base = self.presets if spec.root == "presets" else self.card_db
        return base / spec.filename

    def flush(self) -> None:
        if self.pending:
            self.conn.executemany(INSERT_OBS, self.pending)
            self.pending.clear()

    def record(self, filename: str, source: str, kind: Optional[str], year: Optional[int],
               read: int, kept: int, reasons: Counter, status: str, note: str) -> None:
        dropped = sum(reasons.values())
        self.ledger.append((
            filename, source, kind, year, read, kept, dropped,
            json.dumps(dict(reasons.most_common()), ensure_ascii=False) if reasons else None,
            status, note,
        ))
        detail = " ".join("{}={}".format(k, v) for k, v in reasons.most_common())
        self.log(
            "  {:<32} read={:>6} kept={:>6} dropped={:>6} {}".format(
                filename, read, kept, dropped, detail
            )
        )

    def unique_variant(self, pid: int, year: int, source_file: str, variant: str) -> str:
        key = (pid, year, source_file, variant)
        if key not in self.seen_variants:
            self.seen_variants.add(key)
            return variant
        for suffix in range(2, 200):
            candidate = "{} #{}".format(variant, suffix)
            key = (pid, year, source_file, candidate)
            if key not in self.seen_variants:
                self.seen_variants.add(key)
                return candidate
        return variant  # pathological; INSERT OR IGNORE drops it

    def emit(self, obs: Dict[str, Any]) -> None:
        self.pending.append(tuple(obs.get(col) for col in OBS_COLUMNS))
        if len(self.pending) >= BATCH:
            self.flush()

    # -- generic row -> observation ---------------------------------------

    def make_obs(self, spec: Spec, row: Dict[str, Any], pid: int,
                 column_map: Dict[str, str], id_provenance: str,
                 variant: Optional[str] = None) -> Dict[str, Any]:
        get = row.get
        fields = spec.fields
        prio = universe.SOURCE_PRIORITY.get(spec.source, 0)

        display = clean_person_name(get(fields.get("display_name", ""), None))
        long_name = clean_person_name(get(fields.get("long_name", ""), None))
        positions = parse_positions(
            get(fields.get("positions_text", ""), None),
            get(fields.get("positions_fallback", ""), None),
        )
        if spec.attrs_exact:
            positions = [
                c for c in (
                    to_int(get("preferredposition{}".format(i))) for i in range(1, 5)
                ) if c is not None and c >= 0
            ]

        if spec.dob_is_ea_days:
            dob = parse_ea_birthdate(get(fields.get("dob", "")))
        else:
            dob = parse_dob(get(fields.get("dob", "")))

        nat_id = to_int(get(fields.get("nationality_id", ""), None))
        nat_name = clean_text(get(fields.get("nationality_name", ""), None))

        attrs, granular, expanded = extract_attrs(row, column_map, spec)

        roles = [
            v for v in (to_int(get("role{}".format(i))) for i in range(1, 6))
            if v is not None
        ]

        if variant is None:
            parts = [clean_text(get(c)) for c in spec.variant_from if c]
            label = " / ".join(p for p in parts if p and p.upper() != "N/A") or "base"
            variant = label
        variant = self.unique_variant(pid, spec.year, spec.filename, variant)

        identity = self.identities[pid]
        # Prefer the spelled-out name over "M. Ødegaard" when both are present.
        identity.offer_name(
            long_name if (long_name and not name_quality(display)) else display,
            spec.year, prio,
        )
        identity.offer_parts(split_name(long_name, display), prio)
        identity.offer_dob(dob, prio)
        identity.offer_nat_id(nat_id, prio)
        identity.offer_nat_name(nat_name, prio)

        if self.collect_names:
            for candidate in (display, long_name):
                norm = normalize_name(candidate)
                if norm:
                    self.year_names[spec.year][norm].add(pid)

        return {
            "person_id": pid,
            "year": spec.year,
            "source": spec.source,
            "source_file": spec.filename,
            "source_kind": spec.kind,
            "variant": variant,
            "variant_id": clean_text(get(spec.variant_id_col)) if spec.variant_id_col else None,
            "display_name": display or long_name,
            "overall": to_int(get(fields.get("overall", ""), None)),
            "potential": to_int(get(fields.get("potential", ""), None)),
            "preferredposition1": positions[0] if len(positions) > 0 else None,
            "preferredposition2": positions[1] if len(positions) > 1 else None,
            "preferredposition3": positions[2] if len(positions) > 2 else None,
            "preferredposition4": positions[3] if len(positions) > 3 else None,
            "positions_text": clean_text(get(fields.get("positions_text", ""), None)),
            "club_id": to_int(get(fields.get("club_id", ""), None)),
            "club_name": clean_text(get(fields.get("club_name", ""), None)),
            "league_id": to_int(get(fields.get("league_id", ""), None)),
            "league_name": clean_text(get(fields.get("league_name", ""), None)),
            "nationality_id": nat_id,
            "nationality_name": nat_name,
            "height": to_int(get(fields.get("height", ""), None)),
            "weight": to_int(get(fields.get("weight", ""), None)),
            "preferredfoot": parse_foot(get(fields.get("preferredfoot", ""), None)),
            "skillmoves": to_int(get(fields.get("skillmoves", ""), None)),
            "weakfoot": to_int(get(fields.get("weakfoot", ""), None)),
            "trait1": to_int(get(fields.get("trait1", ""), None)),
            "trait2": to_int(get(fields.get("trait2", ""), None)),
            "icontrait1": to_int(get(fields.get("icontrait1", ""), None)),
            "icontrait2": to_int(get(fields.get("icontrait2", ""), None)),
            "roles": json.dumps(roles) if roles else None,
            "playstyles": None,
            "attrs": json.dumps(attrs, sort_keys=True, separators=(",", ":")),
            "attr_fidelity": fidelity_of(granular, expanded),
            "attr_granular_n": granular,
            "attr_expanded_n": expanded,
            "id_provenance": id_provenance,
        }

    # -- per-source loaders ------------------------------------------------

    def load_csv_spec(self, spec: Spec, keep: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
                      dedupe_ids: bool = False) -> None:
        """Generic CSV loader. ``keep`` returns a drop-reason or None to keep."""
        path = self.path_for(spec)
        if not path.is_file():
            self.record(spec.filename, spec.source, spec.kind, spec.year, 0, 0,
                        Counter(), "missing", "file not found at " + str(path))
            return
        reasons: Counter = Counter()
        read = kept = 0
        seen_ids: Dict[int, str] = {}
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            column_map = build_column_map(reader.fieldnames or [], spec)
            for row in reader:
                read += 1
                if keep is not None:
                    reason = keep(row)
                    if reason:
                        reasons[reason] += 1
                        continue
                pid, provenance = self.resolve_id(spec, row)
                if pid is None:
                    reasons["unresolvable_player_id"] += 1
                    continue
                if dedupe_ids:
                    digest = hashlib.md5(
                        "\x1f".join(str(v) for v in row.values()).encode(
                            "utf-8", "replace")
                    ).hexdigest()
                    previous = seen_ids.get(pid)
                    if previous is not None:
                        reasons["duplicate_id_identical_row" if previous == digest
                                else "duplicate_id_conflicting_row"] += 1
                        continue
                    seen_ids[pid] = digest
                self.emit(self.make_obs(spec, row, pid, column_map, provenance))
                kept += 1
        self.flush()
        self.record(spec.filename, spec.source, spec.kind, spec.year, read, kept,
                    reasons, "loaded", spec.note)

    @staticmethod
    def resolve_id(spec: Spec, row: Dict[str, Any]) -> Tuple[Optional[int], str]:
        if spec.id_from_url:
            url = str(row.get(spec.id_from_url) or "").strip().rstrip("/")
            segment = url.rsplit("/", 1)[-1] if url else ""
            if segment.isdigit():
                return int(segment), "url_recovered"
            return None, "url_recovered"
        if spec.id_col:
            pid = to_int(row.get(spec.id_col))
            if pid is not None and pid > 0:
                return pid, "source_id"
        return None, "source_id"

    def load_fc25(self, spec: Spec) -> None:
        """fc25 is 2x duplicated; prefer the combined_* export."""
        path = self.path_for(spec)
        if not path.is_file():
            self.record(spec.filename, spec.source, spec.kind, spec.year, 0, 0,
                        Counter(), "missing", "file not found")
            return
        combined: Set[int] = set()
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("Source_File") or "").startswith("combined_"):
                    pid = to_int(row.get("Player ID"))
                    if pid is not None:
                        combined.add(pid)
        emitted: Set[int] = set()

        def keep(row: Dict[str, Any]) -> Optional[str]:
            pid = to_int(row.get("Player ID"))
            if pid is None:
                return None  # handled by resolve_id
            is_combined = str(row.get("Source_File") or "").startswith("combined_")
            if pid in combined and not is_combined:
                return "duplicate_prefer_combined_source_file"
            if pid in emitted:
                return "duplicate_player_id"
            emitted.add(pid)
            return None

        self.load_csv_spec(spec, keep=keep)

    def load_futgg(self) -> None:
        """FUT.GG bulk dumps (futgg_*.jsonl for any year) — LE field names."""
        root = self.card_db / "futgg"
        paths = sorted(root.glob("futgg_*.jsonl")) if root.is_dir() else []
        # Skip in-progress partials
        paths = [p for p in paths if ".partial" not in p.name]
        if not paths:
            self.record(
                "futgg_*.jsonl", "futgg", "fut", 0, 0, 0, Counter(), "missing",
                "no futgg_YY.jsonl under card_db/futgg/",
            )
            return
        for path in paths:
            self._load_futgg_file(path)

    def _load_futgg_file(self, path: Path) -> None:
        """Ingest one year dump (futgg_26.jsonl → year 26)."""
        year = 26
        stem = path.stem  # futgg_26
        if "_" in stem:
            try:
                year = int(stem.rsplit("_", 1)[-1])
            except ValueError:
                year = 26
        spec = Spec(
            filename=path.name, year=year, source="futgg", kind="fut",
            fields={}, attrs_exact=True,
            note="FUT.GG card dump; LE field names (promos/Icons/Heroes).",
        )
        if not path.is_file():
            self.record(spec.filename, spec.source, spec.kind, spec.year, 0, 0,
                        Counter(), "missing", "file not found at " + str(path))
            return
        reasons: Counter = Counter()
        read = kept = 0
        prio = universe.SOURCE_PRIORITY["futgg"]
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                read += 1
                try:
                    card = json.loads(line)
                except ValueError:
                    reasons["malformed_json"] += 1
                    continue
                pid = to_int(card.get("playerid")) or to_int(card.get("basePlayerEaId"))
                if pid is None or pid <= 0:
                    reasons["unresolvable_player_id"] += 1
                    continue
                # Prefer year from row when present (cross-year by_player dumps).
                row_year = to_int(card.get("year") or card.get("game") or card.get("_year")) or year
                attrs = {
                    field: clamp_attr(to_int(card.get(field)))
                    for field in ATTR_FIELDS
                    if to_int(card.get(field)) is not None
                }
                positions = [
                    v for v in (
                        to_int(card.get("preferredposition{}".format(i)))
                        for i in range(1, 5)
                    ) if v is not None and v >= 0
                ]
                label = clean_text(card.get("revision")) or clean_text(card.get("rarity")) or "base"
                variant = self.unique_variant(pid, row_year, spec.filename, label)
                display = clean_person_name(card.get("name"))
                first = clean_person_name(card.get("firstName"))
                last = clean_person_name(card.get("lastName"))
                nickname = clean_person_name(card.get("nickname"))
                dob = parse_dob(card.get("dateOfBirth"))
                nat_id = to_int(card.get("nationality"))
                nat_name = clean_text(card.get("nation_name")) or clean_text(card.get("nation"))

                identity = self.identities[pid]
                identity.offer_name(display or nickname, row_year, prio)
                identity.offer_parts((first, last, nickname), prio)
                identity.offer_dob(dob, prio)
                identity.offer_nat_id(nat_id, prio)
                identity.offer_nat_name(nat_name, prio)
                if self.collect_names:
                    for candidate in (display, nickname,
                                      " ".join(p for p in (first, last) if p)):
                        norm = normalize_name(candidate)
                        if norm:
                            self.year_names[row_year][norm].add(pid)

                playstyles = {
                    "playstyles": card.get("playstyles") or [],
                    "playstyles_plus": card.get("playstyles_plus")
                    or card.get("playstylesPlus") or [],
                }
                self.emit({
                    "person_id": pid, "year": row_year, "source": "futgg",
                    "source_file": spec.filename, "source_kind": "fut",
                    "variant": variant,
                    "variant_id": clean_text(card.get("eaId")) or clean_text(card.get("slug")),
                    "display_name": display,
                    "overall": to_int(card.get("overallrating")) or to_int(card.get("overall")),
                    "potential": to_int(card.get("potential")),
                    "preferredposition1": positions[0] if len(positions) > 0 else None,
                    "preferredposition2": positions[1] if len(positions) > 1 else None,
                    "preferredposition3": positions[2] if len(positions) > 2 else None,
                    "preferredposition4": positions[3] if len(positions) > 3 else None,
                    "positions_text": ", ".join(
                        universe.POSITION_NAMES.get(c, str(c)) for c in positions
                    ) or None,
                    "club_id": to_int(card.get("club_ea_id")),
                    "club_name": clean_text(card.get("club")),
                    "league_id": to_int(card.get("league_ea_id")),
                    "league_name": clean_text(card.get("league")),
                    "nationality_id": nat_id, "nationality_name": nat_name,
                    "height": to_int(card.get("height")),
                    "weight": to_int(card.get("weight")),
                    "preferredfoot": parse_foot(card.get("preferredfoot")),
                    "skillmoves": to_int(card.get("skillmoves")),
                    "weakfoot": to_int(card.get("weakfootabilitytypecode")),
                    "trait1": to_int(card.get("trait1")),
                    "trait2": to_int(card.get("trait2")),
                    "icontrait1": to_int(card.get("icontrait1")),
                    "icontrait2": to_int(card.get("icontrait2")),
                    "roles": None,
                    "playstyles": json.dumps(playstyles, separators=(",", ":")),
                    "attrs": json.dumps(attrs, sort_keys=True, separators=(",", ":")),
                    "attr_fidelity": fidelity_of(len(attrs), 0),
                    "attr_granular_n": len(attrs), "attr_expanded_n": 0,
                    "id_provenance": "source_id",
                })
                kept += 1
        self.flush()
        self.record(spec.filename, spec.source, spec.kind, spec.year, read, kept,
                    reasons, "loaded", spec.note)

    def load_name_matched(self, spec: Spec) -> None:
        """Attach an id-less dump to persons via unambiguous same-year name match."""
        path = self.path_for(spec)
        if not path.is_file():
            self.record(spec.filename, spec.source, spec.kind, spec.year, 0, 0,
                        Counter(), "missing", "file not found")
            return
        index = self.year_names.get(spec.year, {})
        reasons: Counter = Counter()
        read = kept = 0
        name_col = spec.fields.get("display_name", "")
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            column_map = build_column_map(reader.fieldnames or [], spec)
            for row in reader:
                read += 1
                matches = index.get(normalize_name(row.get(name_col)))
                if not matches:
                    reasons["name_not_found_in_year"] += 1
                    continue
                if len(matches) > 1:
                    reasons["name_ambiguous"] += 1
                    continue
                pid = next(iter(matches))
                self.emit(self.make_obs(spec, row, pid, column_map, "name_matched"))
                kept += 1
        self.flush()
        self.record(spec.filename, spec.source, spec.kind, spec.year, read, kept,
                    reasons, "loaded", spec.note)

    # -- person table ------------------------------------------------------

    def build_persons(self) -> None:
        self.log("  aggregating person rows ...")
        base_year = universe.YEARS[0]
        aggregates: Dict[int, Dict[str, Any]] = {}
        for pid, first, last, best, count, mask in self.conn.execute(
            "SELECT person_id, MIN(year), MAX(year), MAX(overall), COUNT(*), "
            "       SUM(DISTINCT 1 << (year - ?)) "
            "FROM observation GROUP BY person_id",
            (base_year,),
        ):
            aggregates[pid] = {
                "first": first, "last": last, "best": best,
                "count": count, "mask": mask or 0,
            }
        fc26 = {
            row[0] for row in self.conn.execute(
                "SELECT DISTINCT person_id FROM observation WHERE source = 'le_base'"
            )
        }
        rows = []
        for pid, agg in aggregates.items():
            identity = self.identities.get(pid) or Identity()
            first_name, surname, common = identity.parts
            display = (
                identity.name or common
                or " ".join(p for p in (first_name, surname) if p)
                or "Player {}".format(pid)
            )
            rows.append((
                pid, display, normalize_name(display), first_name, surname, common,
                identity.dob, identity.nat_id, identity.nat_name,
                1 if pid in fc26 else 0,
                agg["first"], agg["last"], agg["best"], agg["count"], agg["mask"],
            ))
        self.conn.executemany(
            "INSERT INTO person (person_id, display_name, name_norm, firstname, "
            "surname, commonname, dob, nationality_id, nationality_name, "
            "exists_in_fc26, first_seen_year, last_seen_year, best_overall, "
            "obs_count, year_mask) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.log("  person rows: {}".format(len(rows)))

    def build_fts(self) -> None:
        self.log("  building FTS5 name index ...")
        self.conn.execute(
            'CREATE VIRTUAL TABLE person_fts USING fts5('
            "  names, "
            "  tokenize=\"unicode61 remove_diacritics 2\""
            ")"
        )
        self.conn.execute(
            "INSERT INTO person_fts (rowid, names) "
            "SELECT person_id, "
            "  TRIM(COALESCE(display_name,'') || ' ' || COALESCE(name_norm,'') || ' ' "
            "    || COALESCE(firstname,'') || ' ' || COALESCE(surname,'') || ' ' "
            "    || COALESCE(commonname,'')) "
            "FROM person"
        )
        self.conn.execute("INSERT INTO person_fts(person_fts) VALUES ('optimize')")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def dry_run(card_db: Path, presets: Path) -> int:
    """Report what each source looks like without writing anything."""
    print("Sources under {} and {}".format(card_db, presets))
    for spec in SPECS + NAME_MATCH_SPECS:
        base = presets if spec.root == "presets" else card_db
        path = base / spec.filename
        if not path.is_file():
            print("  {:<32} MISSING".format(spec.filename))
            continue
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            rows = sum(1 for _ in reader)
        column_map = build_column_map(header, spec)
        print("  {:<32} year={} rows={:<7} cols={:<4} granular_attrs={:<3} face={}".format(
            spec.filename, spec.year, rows, len(header), len(column_map),
            len(spec.face)))
    for filename, _source, _year, reason in EXCLUDED:
        print("  {:<32} EXCLUDED — {}".format(filename, reason))
    return 0


def summary_rows(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Pre-roll the counts src.universe.stats() reports.

    Recomputing them live is a handful of full scans over ~240k rows (~0.5s),
    which is far too slow for a status panel.
    """
    scalars = {
        "persons": "SELECT COUNT(*) FROM person",
        "observations": "SELECT COUNT(*) FROM observation",
        "persons_in_fc26": "SELECT COUNT(*) FROM person WHERE exists_in_fc26 = 1",
    }
    groups = {
        "by_year": "SELECT year, COUNT(*) FROM observation GROUP BY year ORDER BY year",
        "persons_by_year": "SELECT year, COUNT(DISTINCT person_id) FROM observation "
                           "GROUP BY year ORDER BY year",
        "by_source": "SELECT source, COUNT(*) FROM observation GROUP BY source "
                     "ORDER BY COUNT(*) DESC",
        "by_fidelity": "SELECT attr_fidelity, COUNT(*) FROM observation "
                       "GROUP BY attr_fidelity ORDER BY COUNT(*) DESC",
        "by_id_provenance": "SELECT id_provenance, COUNT(*) FROM observation "
                            "GROUP BY id_provenance ORDER BY COUNT(*) DESC",
    }
    rows: List[Tuple[str, str]] = []
    for key, sql in scalars.items():
        rows.append(("summary_" + key, json.dumps(conn.execute(sql).fetchone()[0])))
    for key, sql in groups.items():
        rows.append((
            "summary_" + key,
            json.dumps({str(r[0]): int(r[1]) for r in conn.execute(sql)}),
        ))
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=None, help="output .sqlite path")
    parser.add_argument("--card-db", default=None, help="card_db directory")
    parser.add_argument("--presets", default=None, help="player_presets directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="profile the sources and exit")
    parser.add_argument(
        "--include-name-matched", action="store_true",
        help=("also load the id-less FutHead 18/19/20 and FUTBIN 19 card dumps, "
              "attaching each row to a person only when its name matches exactly "
              "one player in the same game year. Off by default: ~28%% of those "
              "rows match nothing and ~2.5%% are ambiguous."),
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    card_db = Path(args.card_db) if args.card_db else APP_ROOT / "card_db"
    presets = (
        Path(args.presets) if args.presets
        else APP_ROOT.parent / "player_presets"
    )
    if args.dry_run:
        return dry_run(card_db, presets)

    out = Path(args.out) if args.out else card_db / universe.DB_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".sqlite.tmp")
    for stale in (tmp, tmp.with_name(tmp.name + "-journal")):
        if stale.exists():
            stale.unlink()

    started = time.time()
    verbose = not args.quiet
    conn = sqlite3.connect(str(tmp))
    try:
        conn.executescript(SCHEMA)
        builder = Builder(conn, card_db, presets,
                          collect_names=args.include_name_matched, verbose=verbose)
        builder.log("Building {}".format(out))
        builder.log("Loading sources:")

        for spec in SPECS:
            if spec.filename == "fc25.csv":
                builder.load_fc25(spec)
            elif spec.filename == "fifa23.csv":
                builder.load_csv_spec(spec, dedupe_ids=True)
            else:
                builder.load_csv_spec(spec, dedupe_ids=(spec.kind != "fut"))
        builder.load_futgg()

        if args.include_name_matched:
            builder.log("Loading id-less dumps by unambiguous name match:")
            for spec in NAME_MATCH_SPECS:
                builder.load_name_matched(spec)
        else:
            for spec in NAME_MATCH_SPECS:
                path = builder.path_for(spec)
                rows = 0
                if path.is_file():
                    with path.open("r", encoding="utf-8", errors="replace",
                                   newline="") as handle:
                        rows = max(0, sum(1 for _ in handle) - 1)
                builder.record(
                    spec.filename, spec.source, spec.kind, spec.year, rows, 0,
                    Counter({"no_player_id_column": rows}), "excluded",
                    spec.note + " Load with --include-name-matched.",
                )
        for filename, source, year, reason in EXCLUDED:
            path = card_db / filename
            rows = 0
            if path.is_file():
                with path.open("r", encoding="utf-8", errors="replace",
                               newline="") as handle:
                    rows = max(0, sum(1 for _ in handle) - 1)
            builder.record(filename, source, None, year, rows, 0,
                           Counter({"excluded": rows}), "excluded", reason)

        builder.flush()
        builder.build_persons()
        conn.executescript(INDEXES)
        builder.build_fts()

        elapsed = time.time() - started
        persons = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        observations = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            summary_rows(conn) + [
                ("schema_version", str(universe.SCHEMA_VERSION)),
                ("built_at", datetime.datetime.now().astimezone().isoformat(timespec="seconds")),
                ("build_seconds", "{:.1f}".format(elapsed)),
                ("persons", str(persons)),
                ("observations", str(observations)),
                ("include_name_matched", "1" if args.include_name_matched else "0"),
                ("card_db", str(card_db)),
                ("presets", str(presets)),
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO source_ledger (source_file, source, source_kind, "
            "year, rows_read, rows_kept, rows_dropped, drop_reasons, status, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            builder.ledger,
        )
        conn.commit()
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()

    if out.exists():
        out.unlink()
    tmp.replace(out)
    universe.close()

    if verbose:
        report(out, elapsed)
    return 0


def report(out: Path, elapsed: float) -> None:
    conn = sqlite3.connect(str(out))
    conn.row_factory = sqlite3.Row
    try:
        persons = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        observations = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        print("\n{}  ({:.1f} MB, built in {:.1f}s)".format(
            out, out.stat().st_size / 1e6, elapsed))
        print("  persons      : {:>7,}".format(persons))
        print("  observations : {:>7,}".format(observations))
        print("  in FC 26     : {:>7,}".format(conn.execute(
            "SELECT COUNT(*) FROM person WHERE exists_in_fc26 = 1").fetchone()[0]))
        print("\n  per year:  year   observations   distinct persons")
        for row in conn.execute(
            "SELECT year, COUNT(*) o, COUNT(DISTINCT person_id) p "
            "FROM observation GROUP BY year ORDER BY year"
        ):
            print("             {:>4}   {:>12,}   {:>16,}".format(
                row["year"], row["o"], row["p"]))
        print("\n  per source:")
        for row in conn.execute(
            "SELECT source, source_file, COUNT(*) n FROM observation "
            "GROUP BY source, source_file ORDER BY n DESC"
        ):
            print("    {:<18} {:<28} {:>8,}".format(
                row["source"], row["source_file"], row["n"]))
        print("\n  attribute fidelity:")
        for row in conn.execute(
            "SELECT attr_fidelity, COUNT(*) n FROM observation "
            "GROUP BY attr_fidelity ORDER BY n DESC"
        ):
            print("    {:<20} {:>8,}".format(row["attr_fidelity"], row["n"]))
        print("\n  dropped rows:")
        total_dropped = 0
        for row in conn.execute(
            "SELECT source_file, rows_read, rows_kept, rows_dropped, drop_reasons, "
            "status FROM source_ledger WHERE rows_dropped > 0 "
            "ORDER BY rows_dropped DESC"
        ):
            total_dropped += row["rows_dropped"]
            print("    {:<30} {:>7,} dropped  {}".format(
                row["source_file"], row["rows_dropped"], row["drop_reasons"]))
        print("    {:<30} {:>7,} total".format("", total_dropped))
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
