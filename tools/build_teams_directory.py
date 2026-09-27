#!/usr/bin/env python3
"""Build card_db/teams.sqlite — the club / league / nation directory.

The companion asks the user for a numeric EA ``teamid`` in every career
operation (transfer, loan, "given team" profiles) but had no way to *find* one.
This builder turns the shipped player dumps into a small searchable directory:

    python tools/build_teams_directory.py             # build with defaults
    python tools/build_teams_directory.py --dry-run   # profile sources only

``card_db/catalog.sqlite`` and ``card_db/universe.sqlite`` are never opened.

Sources and what each one actually contributes
----------------------------------------------
``fc26_datahub.csv`` (year 26, primary)
    ``club_team_id`` / ``club_name`` / ``league_id`` / ``league_name`` /
    ``nationality_id`` / ``nationality_name`` / ``nation_team_id``. 18405 rows,
    all ``fifa_version=26 fifa_update=4``. 89 rows have a blank club — blank
    club *and* blank league, i.e. free agents; they are counted against team
    111592 rather than dropped.

``futgg/futgg_26.jsonl`` (year 26, gap filler)
    The keys are ``club_ea_id`` / ``league_ea_id`` / ``nationality`` with
    ``club`` / ``league`` / ``nation`` holding the names — *not* the camelCase
    ``clubEaId`` spelling. Adds 64 team ids the datahub does not have.

    DEFECT (verified): FUT.GG files women's players under the *men's* club id.
    Chelsea Women is ``club_ea_id=5, league_ea_id=2216``, which would move the
    men's Chelsea into the Barclays WSL. So the league for an id seen in both
    men's and women's rows is taken from the men's rows only; women-only ids
    (e.g. 116017 Manchester City W, 132176 London City) keep their own league.

    Names are broadcast short forms ("Man Utd", "Spurs", "QPR", "Nott'm
    Forest") — kept as *aliases* so they are searchable, never as display names
    when the datahub has the full one.

``fifa22.csv`` (year 22, historical ids)
    Has ``club_team_id`` + ``nationality_id`` but **no** ``league_id`` — only
    ``league_name`` / ``league_level``. Adds 189 clubs that no longer exist in
    FC 26; their league is recovered by name where that is unambiguous.

``fifa18/19/20/21.csv`` (years 18-21) and ``fifa23.csv`` / ``fc24.csv``
    Names only (``club_name`` / ``Club``, no numeric club id). Used purely to
    widen ``first_seen_year`` / ``last_seen_year`` on clubs whose name matches
    exactly one known team id. Ambiguous and unmatched names are dropped and
    counted in ``source_ledger``.

``player_presets/base_players.csv``
    Column 107 ``nationality`` holds LE nation ids. Used only to validate the
    nation map — all 162 distinct ids covering 22348/22348 rows resolve.

Known-stable facts baked in (LE lua/DOC.MD + the task brief)
------------------------------------------------------------
``club_team_id`` / ``league_id`` / ``nationality_id`` do not move between
FIFA 18 and FC 26, so ids from a FIFA 22 dump are still valid today. Team
111592 is the free-agent pool. Leagues 76 / 78 / 2136 / 383 are not real
competitions, so everything in them is flagged ``is_club = 0``.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src import teams_directory  # noqa: E402

# csv fields hold long release-clause strings on some dumps
csv.field_size_limit(min(sys.maxsize, 2147483647))


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

FREE_AGENT_TEAM_ID = teams_directory.FREE_AGENT_TEAM_ID
PSEUDO_LEAGUES: Dict[int, str] = dict(teams_directory.PSEUDO_LEAGUES)
FUT_ONLY_LEAGUES: Dict[int, str] = dict(teams_directory.FUT_ONLY_LEAGUES)
FUT_ONLY_TEAMS: Dict[int, str] = dict(teams_directory.FUT_ONLY_TEAMS)
NON_CLUB_LEAGUE_IDS = frozenset(teams_directory.NON_CLUB_LEAGUE_IDS)

#: name -> (source key, year). Display-name priority is the list order.
NAME_PRIORITY = ("datahub", "futgg", "fifa22")

#: Placeholder club names FUT.GG emits for ids it has no string for.
_PLACEHOLDER_RE = re.compile(r"^unknown\s+\d+$")

#: Minimum evidence before a league is given an inferred country.
COUNTRY_MIN_PLAYERS = 5
COUNTRY_MIN_SHARE = 0.18
COUNTRY_MIN_MARGIN = 1.3


SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE team (
    team_id         INTEGER PRIMARY KEY,   -- EA teamid, stable FIFA 18 -> FC 26
    name            TEXT    NOT NULL,
    name_norm       TEXT    NOT NULL,      -- diacritic-folded lowercase
    league_id       INTEGER,
    country_id      INTEGER,               -- LE nation id (inferred, see meta)
    is_club         INTEGER NOT NULL DEFAULT 1,
    first_seen_year INTEGER,
    last_seen_year  INTEGER,
    player_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE league (
    league_id           INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL,
    name_norm           TEXT    NOT NULL,
    country_id          INTEGER,
    is_real_competition INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE nation (
    nation_id INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    name_norm TEXT NOT NULL
);

CREATE TABLE team_alias (
    team_id   INTEGER NOT NULL,
    name      TEXT    NOT NULL,
    name_norm TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (team_id, name_norm)
);

CREATE TABLE league_alias (
    league_id INTEGER NOT NULL,
    name      TEXT    NOT NULL,
    name_norm TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (league_id, name_norm)
);

CREATE TABLE source_ledger (
    source_file  TEXT PRIMARY KEY,
    source       TEXT,
    year         INTEGER,
    rows_read    INTEGER NOT NULL DEFAULT 0,
    rows_kept    INTEGER NOT NULL DEFAULT 0,
    rows_dropped INTEGER NOT NULL DEFAULT 0,
    drop_reasons TEXT,
    status       TEXT NOT NULL,             -- loaded | excluded | missing
    note         TEXT
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

INDEXES = """
CREATE INDEX idx_team_name_norm    ON team(name_norm);
CREATE INDEX idx_team_league       ON team(league_id);
CREATE INDEX idx_team_club_players ON team(is_club, player_count DESC);
CREATE INDEX idx_team_alias_norm   ON team_alias(name_norm);
CREATE INDEX idx_league_name_norm  ON league(name_norm);
CREATE INDEX idx_league_alias_norm ON league_alias(name_norm);
CREATE INDEX idx_nation_name_norm  ON nation(name_norm);
"""

#: Files deliberately not read, with the reason recorded in source_ledger.
EXCLUDED: Tuple[Tuple[str, str], ...] = (
    ("fifa22_sofifa.csv", "byte-identical duplicate of fifa22.csv"),
    ("fc25.csv", "has a League column but no club column at all"),
    ("fifa23_official.csv", "Club names only, already covered by fifa23.csv"),
    ("fifa18_futhead.csv", "FUT card dump: no club id and no career club column"),
    ("fifa19_futhead.csv", "FUT card dump: no club id and no career club column"),
    ("fifa20_futhead.csv", "FUT card dump: no club id and no career club column"),
    ("fifa19_futbin_cards.csv", "FUT card dump: no usable club column"),
    ("fifa19_futbin_detailed.csv", "structurally corrupt (see build_universe.py)"),
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def norm(value: object) -> str:
    """NFKD-fold, drop combining marks, lowercase, collapse whitespace.

    Same rule as ``src.card_catalog._norm`` so the two search paths agree.
    """
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def squash(value: object) -> str:
    """``norm`` with punctuation removed — "Nott'm Forest" -> "nottmforest"."""
    return re.sub(r"[^a-z0-9]+", "", norm(value))


def acronym(value: object) -> str:
    """Initials of a multi-word club name — "Paris Saint-Germain" -> "psg".

    Users type "PSG", "OM", "LAFC"; none of the shipped sources carry those.
    Emitted as a low-priority alias so it can lose to a real name match.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", norm(value)) if t and not t.isdigit()]
    if len(tokens) < 2:
        return ""
    letters = "".join(t[0] for t in tokens)
    return letters if 2 <= len(letters) <= 5 else ""


def to_int(value: object) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def clean_name(value: object) -> str:
    """Collapse whitespace, normalise curly quotes, reject FUT.GG placeholders."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = text.replace("’", "'").replace("‘", "'")
    if not text or _PLACEHOLDER_RE.match(text.lower()):
        return ""
    return text


class Ledger:
    """One row per source file, mirroring build_universe.py's audit table."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}

    def record(
        self,
        source_file: str,
        source: str,
        year: Optional[int],
        *,
        read: int = 0,
        kept: int = 0,
        drops: Optional[Counter] = None,
        status: str = "loaded",
        note: str = "",
    ) -> None:
        drops = drops or Counter()
        self.rows[source_file] = {
            "source_file": source_file,
            "source": source,
            "year": year,
            "rows_read": read,
            "rows_kept": kept,
            "rows_dropped": sum(drops.values()),
            "drop_reasons": json.dumps(dict(drops.most_common()), ensure_ascii=False),
            "status": status,
            "note": note,
        }


# --------------------------------------------------------------------------
# Accumulator
# --------------------------------------------------------------------------


class Directory:
    """Everything the sources agree (or disagree) about, before it is written."""

    def __init__(self) -> None:
        # team_id -> source -> name
        self.team_names: Dict[int, Dict[str, str]] = defaultdict(dict)
        self.team_years: Dict[int, Set[int]] = defaultdict(set)
        self.team_players: Dict[int, Set[str]] = defaultdict(set)
        # team_id -> source -> Counter(league_id)
        self.team_leagues: Dict[int, Dict[str, Counter]] = defaultdict(
            lambda: defaultdict(Counter)
        )
        self.team_country: Dict[int, int] = {}
        self.national_team_ids: Set[int] = set()
        # league_id -> source -> name
        self.league_names: Dict[int, Dict[str, str]] = defaultdict(dict)
        self.league_nations: Dict[int, Counter] = defaultdict(Counter)
        # fifa22 league names that have no id anywhere
        self.orphan_league_names: Counter = Counter()
        self.nations: Dict[int, Dict[str, str]] = defaultdict(dict)

    # -- ingest -----------------------------------------------------------

    def add_team_name(self, team_id: int, name: str, source: str) -> None:
        name = clean_name(name)
        if name:
            self.team_names[team_id].setdefault(source, name)

    def add_league_name(self, league_id: int, name: str, source: str) -> None:
        name = clean_name(name)
        if name:
            self.league_names[league_id].setdefault(source, name)

    def add_nation(self, nation_id: int, name: str, source: str) -> None:
        name = clean_name(name)
        if name:
            self.nations[nation_id].setdefault(source, name)

    # -- resolution -------------------------------------------------------

    def display_name(self, team_id: int) -> str:
        by_source = self.team_names.get(team_id, {})
        for key in NAME_PRIORITY:
            if by_source.get(key):
                return by_source[key]
        for value in by_source.values():
            if value:
                return value
        return ""

    def league_display_name(self, league_id: int) -> str:
        if league_id in PSEUDO_LEAGUES:
            return PSEUDO_LEAGUES[league_id]
        by_source = self.league_names.get(league_id, {})
        for key in NAME_PRIORITY:
            if by_source.get(key):
                return by_source[key]
        for value in by_source.values():
            if value:
                return value
        return ""

    def resolve_league(self, team_id: int) -> Optional[int]:
        by_source = self.team_leagues.get(team_id, {})
        for key in ("datahub", "futgg_men", "futgg_women", "fifa22"):
            counter = by_source.get(key)
            if counter:
                return counter.most_common(1)[0][0]
        return None

    def is_women_only(self, team_id: int) -> bool:
        """True when the only league evidence for this id is a women's league.

        FUT.GG files women's players under the men's club id, so a women's team
        only gets its own id when it never appears in a men's row. Those ids
        (116017 Manchester City W, 115995 FC Bayern München W, 132589 Union
        Berlin W ...) duplicate a men's club *name* and would otherwise make
        every historical name lookup for that club ambiguous. Women's club
        football did not exist in the FIFA 18-22 dumps at all.
        """
        sources = set(self.team_leagues.get(team_id, {}))
        return bool(sources) and sources <= {"futgg_women"}

    def name_index(self, exclude_women_only: bool = False) -> Dict[str, Set[int]]:
        """normalized name (and squashed form) -> team ids that answer to it."""
        index: Dict[str, Set[int]] = defaultdict(set)
        for team_id, by_source in self.team_names.items():
            if exclude_women_only and self.is_women_only(team_id):
                continue
            for value in by_source.values():
                for key in (norm(value), squash(value)):
                    if key:
                        index[key].add(team_id)
        return index


# --------------------------------------------------------------------------
# Loaders
# --------------------------------------------------------------------------


def _open_csv(path: Path):
    return path.open("r", encoding="utf-8", errors="replace", newline="")


def load_datahub(path: Path, directory: Directory, ledger: Ledger) -> None:
    source = "datahub"
    year = 26
    read = kept = 0
    drops: Counter = Counter()
    if not path.is_file():
        ledger.record(path.name, source, year, status="missing", note="file not found")
        return
    with _open_csv(path) as handle:
        for row in csv.DictReader(handle):
            read += 1
            player_id = (row.get("player_id") or "").strip()
            nation_id = to_int(row.get("nationality_id"))
            if nation_id is not None:
                directory.add_nation(nation_id, row.get("nationality_name", ""), source)

            league_id = to_int(row.get("league_id"))
            if league_id is not None:
                directory.add_league_name(league_id, row.get("league_name", ""), source)
                if nation_id is not None:
                    directory.league_nations[league_id][nation_id] += 1

            # A national-team id maps 1:1 onto the player's nationality.
            nation_team_id = to_int(row.get("nation_team_id"))
            if nation_team_id and nation_team_id > 0:
                directory.national_team_ids.add(nation_team_id)
                directory.team_years[nation_team_id].add(year)
                directory.add_team_name(
                    nation_team_id, row.get("nationality_name", ""), source
                )
                if nation_id is not None:
                    directory.team_country[nation_team_id] = nation_id

            team_id = to_int(row.get("club_team_id"))
            club_name = clean_name(row.get("club_name"))
            if team_id is None:
                # Blank club *and* blank league is sofifa's free-agent marker.
                if not club_name and not (row.get("league_id") or "").strip():
                    directory.team_years[FREE_AGENT_TEAM_ID].add(year)
                    if player_id:
                        directory.team_players[FREE_AGENT_TEAM_ID].add(player_id)
                    kept += 1
                else:
                    drops["club_team_id missing"] += 1
                continue
            if not club_name:
                drops["club_name blank"] += 1
                continue
            directory.add_team_name(team_id, club_name, source)
            directory.team_years[team_id].add(year)
            if player_id:
                directory.team_players[team_id].add(player_id)
            if league_id is not None:
                directory.team_leagues[team_id]["datahub"][league_id] += 1
            kept += 1
    ledger.record(
        path.name, source, year, read=read, kept=kept, drops=drops,
        note="primary source: club_team_id/league_id/nationality_id",
    )


def load_futgg(path: Path, directory: Directory, ledger: Ledger) -> None:
    source = "futgg"
    year = 26
    read = kept = 0
    drops: Counter = Counter()
    if not path.is_file():
        ledger.record(path.name, source, year, status="missing", note="file not found")
        return

    men: Dict[int, Counter] = defaultdict(Counter)
    women: Dict[int, Counter] = defaultdict(Counter)

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            read += 1
            try:
                card = json.loads(line)
            except ValueError:
                drops["bad json"] += 1
                continue
            if not isinstance(card, dict):
                drops["not an object"] += 1
                continue

            is_men = to_int(card.get("gender")) != 2
            nation_id = to_int(card.get("nationality"))
            if nation_id:
                directory.add_nation(
                    nation_id, card.get("nation") or card.get("nation_name") or "", source
                )

            league_id = to_int(card.get("league_ea_id"))
            if league_id:
                directory.add_league_name(league_id, card.get("league") or "", source)
                if nation_id and is_men:
                    directory.league_nations[league_id][nation_id] += 1

            team_id = to_int(card.get("club_ea_id"))
            if not team_id:
                drops["club_ea_id missing"] += 1
                continue
            name = clean_name(card.get("club"))
            if not name:
                drops["club name blank or placeholder"] += 1
                continue
            directory.add_team_name(team_id, name, source)
            directory.team_years[team_id].add(year)
            base_id = card.get("basePlayerEaId") or card.get("playerid")
            if base_id:
                directory.team_players[team_id].add(str(base_id))
            if league_id:
                (men if is_men else women)[team_id][league_id] += 1
            kept += 1

    # Women's rows reuse the men's club id, so the men's league always wins.
    for team_id, counter in men.items():
        directory.team_leagues[team_id]["futgg_men"].update(counter)
    shadowed = 0
    for team_id, counter in women.items():
        if team_id in men:
            shadowed += 1
            continue
        directory.team_leagues[team_id]["futgg_women"].update(counter)

    ledger.record(
        path.name, source, year, read=read, kept=kept, drops=drops,
        note=(
            "keys are club_ea_id/league_ea_id/nationality; {} club ids carried a "
            "women's league that was discarded in favour of the men's league"
        ).format(shadowed),
    )


def load_fifa22(path: Path, directory: Directory, ledger: Ledger) -> None:
    source = "fifa22"
    year = 22
    read = kept = 0
    drops: Counter = Counter()
    if not path.is_file():
        ledger.record(path.name, source, year, status="missing", note="file not found")
        return
    league_names: Dict[int, Counter] = defaultdict(Counter)
    with _open_csv(path) as handle:
        for row in csv.DictReader(handle):
            read += 1
            nation_id = to_int(row.get("nationality_id"))
            if nation_id is not None:
                directory.add_nation(nation_id, row.get("nationality_name", ""), source)
            team_id = to_int(row.get("club_team_id"))
            if team_id is None:
                drops["club_team_id missing (free agent)"] += 1
                continue
            name = clean_name(row.get("club_name"))
            if not name:
                drops["club_name blank"] += 1
                continue
            directory.add_team_name(team_id, name, source)
            directory.team_years[team_id].add(year)
            player_id = (row.get("sofifa_id") or "").strip()
            if player_id:
                directory.team_players[team_id].add(player_id)
            league_name = clean_name(row.get("league_name"))
            if league_name:
                league_names[team_id][league_name] += 1
            kept += 1
    ledger.record(
        path.name, source, year, read=read, kept=kept, drops=drops,
        note="has club_team_id + nationality_id but NO league_id (league_name only)",
    )
    # Deferred: league ids for these names only exist once the 26 sources are in.
    directory._fifa22_league_names = league_names  # type: ignore[attr-defined]


def resolve_fifa22_leagues(directory: Directory) -> Dict[str, Any]:
    """Map fifa22's league *names* onto league ids learnt from the 26 sources.

    fifa22 carries ``league_name`` only — legacy sofifa strings like "English
    Premier League" or "Saudi Abdul L. Jameel League" that share no tokens with
    the FC 26 names ("Premier League", "Pro League"). Two passes:

    1. exact normalized match against a league name or one of its aliases;
    2. majority vote — clubs that appear in *both* fifa22 and a 26 source carry
       a known league_id, so the id most of a name's clubs sit in today is that
       name's id. Guarded so a thin or split vote is refused rather than
       guessed: it is a unanimous vote over >= 2 clubs, or >= 5 clubs with a
       >= 35% plurality. Without that guard "Italian Serie B" resolves to
       Serie A off two promoted clubs, and "English National League" resolves
       to the Championship off one.
    """
    by_name: Dict[str, Set[int]] = defaultdict(set)
    for league_id, names in directory.league_names.items():
        for value in names.values():
            key = norm(value)
            if key:
                by_name[key].add(league_id)

    pending: Dict[int, Counter] = getattr(directory, "_fifa22_league_names", {})
    # league_name -> clubs, and league_name -> votes for a current league id
    clubs_by_name: Dict[str, Set[int]] = defaultdict(set)
    votes: Dict[str, Counter] = defaultdict(Counter)
    for team_id, counter in pending.items():
        name = counter.most_common(1)[0][0]
        clubs_by_name[name].add(team_id)
        known = directory.resolve_league(team_id)
        if known is not None:
            votes[name][known] += 1

    mapping: Dict[str, Tuple[int, str]] = {}
    unresolved: Dict[str, int] = {}
    for name, clubs in clubs_by_name.items():
        hits = by_name.get(norm(name), set())
        if len(hits) == 1:
            mapping[name] = (next(iter(hits)), "exact-name")
            continue
        counter = votes.get(name)
        if counter:
            total = sum(counter.values())
            league_id, top = counter.most_common(1)[0]
            unanimous = top == total and top >= 2
            plurality = top >= 5 and top / total >= 0.35
            if unanimous or plurality:
                mapping[name] = (league_id, "club-vote {}/{}".format(top, total))
                continue
        unresolved[name] = len(clubs)

    applied = 0
    for team_id, counter in pending.items():
        name = counter.most_common(1)[0][0]
        hit = mapping.get(name)
        if hit is not None:
            directory.team_leagues[team_id]["fifa22"][hit[0]] += 1
            applied += 1
    for name, count in unresolved.items():
        directory.orphan_league_names[name] += count

    return {
        "league_names": len(clubs_by_name),
        "league_names_resolved": len(mapping),
        "clubs_given_a_league": applied,
        "unresolved": dict(sorted(unresolved.items(), key=lambda kv: -kv[1])),
        "mapping": {k: {"league_id": v[0], "how": v[1]} for k, v in sorted(mapping.items())},
    }


def load_name_only(
    path: Path,
    column: str,
    year: int,
    directory: Directory,
    ledger: Ledger,
    index: Dict[str, Set[int]],
) -> None:
    """Widen first/last seen for clubs whose name matches exactly one team id."""
    source = "names_{}".format(year)
    read = kept = 0
    drops: Counter = Counter()
    if not path.is_file():
        ledger.record(path.name, source, year, status="missing", note="file not found")
        return
    seen: Counter = Counter()
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        if column not in (reader.fieldnames or []):
            ledger.record(
                path.name, source, year, status="excluded",
                note="no {!r} column".format(column),
            )
            return
        for row in reader:
            read += 1
            name = clean_name(row.get(column))
            if not name:
                drops["club name blank"] += 1
                continue
            seen[name] += 1
    for name, count in seen.items():
        hits = index.get(norm(name)) or index.get(squash(name)) or set()
        if len(hits) == 1:
            team_id = next(iter(hits))
            directory.team_years[team_id].add(year)
            directory.add_team_name(team_id, name, source)
            kept += count
        elif len(hits) > 1:
            drops["ambiguous name ({} ids)".format(len(hits))] += count
        else:
            drops["name matches no known team id"] += count
    ledger.record(
        path.name, source, year, read=read, kept=kept, drops=drops,
        note="names only (no club id) — matched on normalized name, {} distinct clubs".format(
            len(seen)
        ),
    )


def validate_nations(path: Path, directory: Directory, ledger: Ledger) -> Dict[str, Any]:
    """Cross-check the nation map against LE's own base_players.csv ids."""
    result = {"rows": 0, "distinct": 0, "missing_ids": [], "row_coverage": None}
    if not path.is_file():
        ledger.record(
            path.name, "le_base", None, status="missing", note="file not found"
        )
        return result
    counts: Counter = Counter()
    read = 0
    with _open_csv(path) as handle:
        reader = csv.DictReader(handle)
        if "nationality" not in (reader.fieldnames or []):
            ledger.record(
                path.name, "le_base", None, status="excluded",
                note="no 'nationality' column",
            )
            return result
        for row in reader:
            read += 1
            value = to_int(row.get("nationality"))
            if value is not None:
                counts[value] += 1
    missing = sorted(set(counts) - set(directory.nations))
    total = sum(counts.values())
    covered = total - sum(counts[i] for i in missing)
    result = {
        "rows": read,
        "distinct": len(counts),
        "missing_ids": missing,
        "row_coverage": (covered / total) if total else None,
    }
    ledger.record(
        path.name, "le_base", None, read=read, kept=total,
        drops=Counter({"nation id unknown to the directory": total - covered}),
        note="validation only: {}/{} distinct nation ids resolve".format(
            len(counts) - len(missing), len(counts)
        ),
    )
    return result


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------


def infer_league_countries(directory: Directory) -> Dict[int, int]:
    """Modal player nationality per league, with a plurality guard.

    Verified by hand against all 51 datahub leagues: the modal nationality is
    the league's country every time (lowest share 28%, Cypriot 1. Division).
    """
    out: Dict[int, int] = {}
    for league_id, counter in directory.league_nations.items():
        if league_id in PSEUDO_LEAGUES:
            continue
        total = sum(counter.values())
        if total < COUNTRY_MIN_PLAYERS:
            continue
        ranked = counter.most_common(2)
        top_id, top_n = ranked[0]
        if top_n / total < COUNTRY_MIN_SHARE:
            continue
        if len(ranked) > 1 and ranked[1][1] and top_n < ranked[1][1] * COUNTRY_MIN_MARGIN:
            continue
        out[league_id] = top_id
    return out


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------


def write_db(
    out_path: Path,
    directory: Directory,
    ledger: Ledger,
    league_countries: Dict[int, int],
    extra_meta: Dict[str, Any],
    log,
) -> sqlite3.Connection:
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(out_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # -- nation ----------------------------------------------------------
    nation_rows = []
    for nation_id, by_source in sorted(directory.nations.items()):
        name = ""
        for key in NAME_PRIORITY:
            if by_source.get(key):
                name = by_source[key]
                break
        if not name:
            name = next((v for v in by_source.values() if v), "")
        if not name:
            continue
        nation_rows.append((nation_id, name, norm(name)))
    conn.executemany("INSERT INTO nation VALUES (?,?,?)", nation_rows)
    log("  nations : {}".format(len(nation_rows)))

    # -- league ----------------------------------------------------------
    league_ids = set(directory.league_names) | set(PSEUDO_LEAGUES)
    league_rows = []
    alias_rows: List[Tuple[int, str, str, str]] = []
    seen_alias: Set[Tuple[int, str]] = set()
    for league_id in sorted(league_ids):
        name = directory.league_display_name(league_id)
        if not name:
            continue
        is_real = 0 if league_id in NON_CLUB_LEAGUE_IDS else 1
        league_rows.append(
            (league_id, name, norm(name), league_countries.get(league_id), is_real)
        )
        for source, value in sorted(directory.league_names.get(league_id, {}).items()):
            key = (league_id, norm(value))
            if norm(value) and key not in seen_alias:
                seen_alias.add(key)
                alias_rows.append((league_id, value, norm(value), source))
        key = (league_id, norm(name))
        if key not in seen_alias:
            seen_alias.add(key)
            alias_rows.append((league_id, name, norm(name), "directory"))
    conn.executemany("INSERT INTO league VALUES (?,?,?,?,?)", league_rows)
    conn.executemany("INSERT INTO league_alias VALUES (?,?,?,?)", alias_rows)
    log("  leagues : {} ({} aliases)".format(len(league_rows), len(alias_rows)))

    # -- team ------------------------------------------------------------
    team_ids = set(directory.team_names) | set(directory.team_years)
    team_ids.add(FREE_AGENT_TEAM_ID)
    team_rows = []
    team_alias_rows: List[Tuple[int, str, str, str]] = []
    seen_team_alias: Set[Tuple[int, str]] = set()
    dropped_nameless = 0
    for team_id in sorted(team_ids):
        name = directory.display_name(team_id)
        if team_id == FREE_AGENT_TEAM_ID:
            name = teams_directory.FREE_AGENT_NAME
        if not name:
            dropped_nameless += 1
            continue
        league_id = directory.resolve_league(team_id)
        is_national = team_id in directory.national_team_ids
        if is_national and league_id is None:
            league_id = teams_directory.LEAGUE_INTERNATIONAL
        if team_id in FUT_ONLY_TEAMS:
            # FUT.GG files HERO under the Premier League; it has no squad.
            league_id = None
        is_club = 1
        if team_id == FREE_AGENT_TEAM_ID or is_national:
            is_club = 0
        elif team_id in FUT_ONLY_TEAMS or league_id in NON_CLUB_LEAGUE_IDS:
            is_club = 0
        country_id = directory.team_country.get(team_id)
        if country_id is None and league_id is not None:
            country_id = league_countries.get(league_id)
        years = sorted(directory.team_years.get(team_id, ()))
        team_rows.append((
            team_id,
            name,
            norm(name),
            league_id,
            country_id,
            is_club,
            years[0] if years else None,
            years[-1] if years else None,
            len(directory.team_players.get(team_id, ())),
        ))
        names = dict(directory.team_names.get(team_id, {}))
        names.setdefault("directory", name)
        for source, value in sorted(names.items()):
            key = (team_id, norm(value))
            if norm(value) and key not in seen_team_alias:
                seen_team_alias.add(key)
                team_alias_rows.append((team_id, value, norm(value), source))
    conn.executemany("INSERT INTO team VALUES (?,?,?,?,?,?,?,?,?)", team_rows)

    # Acronyms last, and never where a real club already answers to that string.
    real_names = {row[2] for row in team_rows} | {r[2] for r in team_alias_rows}
    acronym_rows: List[Tuple[int, str, str, str]] = []
    for team_id, _name, _norm_name, *_rest in team_rows:
        for value in set(directory.team_names.get(team_id, {}).values()):
            short = acronym(value)
            key = (team_id, short)
            if short and short not in real_names and key not in seen_team_alias:
                seen_team_alias.add(key)
                acronym_rows.append((team_id, short.upper(), short, "acronym"))
    team_alias_rows.extend(acronym_rows)
    conn.executemany("INSERT INTO team_alias VALUES (?,?,?,?)", team_alias_rows)
    log("  teams   : {} ({} aliases)".format(len(team_rows), len(team_alias_rows)))
    if dropped_nameless:
        log("  teams dropped for having no usable name: {}".format(dropped_nameless))

    conn.executescript(INDEXES)

    # -- FTS -------------------------------------------------------------
    conn.execute(
        'CREATE VIRTUAL TABLE team_fts USING fts5('
        "  names, "
        '  tokenize="unicode61 remove_diacritics 2"'
        ")"
    )
    fts_text: Dict[int, List[str]] = defaultdict(list)
    for team_id, value, value_norm, _source in team_alias_rows:
        fts_text[team_id].extend([value, value_norm, squash(value)])
    conn.executemany(
        "INSERT INTO team_fts (rowid, names) VALUES (?,?)",
        [(tid, " ".join(dict.fromkeys(p for p in parts if p)))
         for tid, parts in fts_text.items()],
    )
    conn.execute("INSERT INTO team_fts(team_fts) VALUES ('optimize')")

    # -- ledger + meta ---------------------------------------------------
    conn.executemany(
        "INSERT OR REPLACE INTO source_ledger (source_file, source, year, rows_read, "
        "rows_kept, rows_dropped, drop_reasons, status, note) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                r["source_file"], r["source"], r["year"], r["rows_read"],
                r["rows_kept"], r["rows_dropped"], r["drop_reasons"],
                r["status"], r["note"],
            )
            for r in ledger.rows.values()
        ],
    )
    meta = {
        "schema_version": str(teams_directory.SCHEMA_VERSION),
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "builder": "tools/build_teams_directory.py",
        "free_agent_team_id": str(FREE_AGENT_TEAM_ID),
        "pseudo_leagues": json.dumps(PSEUDO_LEAGUES, ensure_ascii=False),
        "fut_only_leagues": json.dumps(FUT_ONLY_LEAGUES, ensure_ascii=False),
        "fut_only_teams": json.dumps(FUT_ONLY_TEAMS, ensure_ascii=False),
        "country_rule": (
            "league.country_id is INFERRED: modal player nationality, needs "
            ">= {} players, >= {:.0%} share and a {:.1f}x margin over the "
            "runner-up. team.country_id inherits it (national teams use their "
            "own nation)."
        ).format(COUNTRY_MIN_PLAYERS, COUNTRY_MIN_SHARE, COUNTRY_MIN_MARGIN),
        "name_priority": ",".join(NAME_PRIORITY),
    }
    meta.update({k: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                 for k, v in extra_meta.items()})
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", sorted(meta.items())
    )
    conn.commit()
    return conn


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


NAME_ONLY_SOURCES: Tuple[Tuple[str, str, int], ...] = (
    ("fifa18.csv", "club_name", 18),
    ("fifa19.csv", "club_name", 19),
    ("fifa20.csv", "club_name", 20),
    ("fifa21.csv", "club_name", 21),
    ("fifa23.csv", "Club", 23),
    ("fc24.csv", "Club", 24),
)


def dry_run(card_db: Path, presets: Path) -> int:
    print("Sources under {}".format(card_db))
    checks: List[Tuple[str, Iterable[str]]] = [
        ("fc26_datahub.csv", ("club_team_id", "club_name", "league_id", "league_name",
                              "nationality_id", "nationality_name", "nation_team_id")),
        ("fifa22.csv", ("club_team_id", "club_name", "league_name", "league_level",
                        "nationality_id", "nationality_name")),
    ]
    for filename, wanted in checks:
        path = card_db / filename
        if not path.is_file():
            print("  {:<24} MISSING".format(filename))
            continue
        with _open_csv(path) as handle:
            header = csv.DictReader(handle).fieldnames or []
        have = [c for c in wanted if c in header]
        missing = [c for c in wanted if c not in header]
        print("  {:<24} cols={:<4} have={} missing={}".format(
            filename, len(header), have, missing or "-"))
    for filename, column, year in NAME_ONLY_SOURCES:
        path = card_db / filename
        status = "ok" if path.is_file() else "MISSING"
        print("  {:<24} year={} name-column={!r} {}".format(filename, year, column, status))
    jsonl = card_db / "futgg" / "futgg_26.jsonl"
    if jsonl.is_file():
        with jsonl.open("r", encoding="utf-8", errors="replace") as handle:
            first = json.loads(handle.readline())
        print("  {:<24} keys: {}".format(
            "futgg/futgg_26.jsonl",
            [k for k in first if k in ("club_ea_id", "league_ea_id", "nationality",
                                       "club", "league", "nation", "gender")],
        ))
    else:
        print("  {:<24} MISSING".format("futgg/futgg_26.jsonl"))
    print("  {:<24} {}".format(
        "base_players.csv", "ok" if (presets / "base_players.csv").is_file() else "MISSING"))
    for filename, reason in EXCLUDED:
        print("  {:<24} EXCLUDED — {}".format(filename, reason))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=None, help="output .sqlite path")
    parser.add_argument("--card-db", default=None, help="card_db directory")
    parser.add_argument("--presets", default=None, help="player_presets directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="profile the sources and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    card_db = Path(args.card_db) if args.card_db else APP_ROOT / "card_db"
    if args.presets:
        presets = Path(args.presets)
    else:
        presets = APP_ROOT.parent / "player_presets"
    out_path = Path(args.out) if args.out else card_db / teams_directory.DB_FILENAME

    def log(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    if args.dry_run:
        return dry_run(card_db, presets)

    started = time.time()
    directory = Directory()
    ledger = Ledger()

    log("Reading sources ...")
    load_datahub(card_db / "fc26_datahub.csv", directory, ledger)
    load_futgg(card_db / "futgg" / "futgg_26.jsonl", directory, ledger)
    load_fifa22(card_db / "fifa22.csv", directory, ledger)
    fifa22_leagues = resolve_fifa22_leagues(directory)
    log("  fifa22 league-name -> id: {}/{} names resolved, {} clubs given a league"
        " ({} names refused: {})".format(
            fifa22_leagues["league_names_resolved"], fifa22_leagues["league_names"],
            fifa22_leagues["clubs_given_a_league"], len(fifa22_leagues["unresolved"]),
            ", ".join(sorted(fifa22_leagues["unresolved"])) or "-"))

    index = directory.name_index(exclude_women_only=True)
    for filename, column, year in NAME_ONLY_SOURCES:
        load_name_only(card_db / filename, column, year, directory, ledger, index)

    nation_check = validate_nations(presets / "base_players.csv", directory, ledger)

    for filename, reason in EXCLUDED:
        ledger.record(filename, "-", None, status="excluded", note=reason)

    league_countries = infer_league_countries(directory)
    log("  league country inferred for {}/{} leagues".format(
        len(league_countries), len(directory.league_names)))

    extra_meta: Dict[str, Any] = {
        "fifa22_league_resolution": json.dumps(fifa22_leagues, ensure_ascii=False),
        "nation_validation": json.dumps(nation_check),
        "orphan_league_names": json.dumps(
            dict(directory.orphan_league_names.most_common(40)), ensure_ascii=False
        ),
    }

    log("Writing {} ...".format(out_path))
    conn = write_db(out_path, directory, ledger, league_countries, extra_meta, log)

    teams = conn.execute("SELECT COUNT(*) FROM team").fetchone()[0]
    clubs = conn.execute("SELECT COUNT(*) FROM team WHERE is_club = 1").fetchone()[0]
    leagues = conn.execute("SELECT COUNT(*) FROM league").fetchone()[0]
    nations = conn.execute("SELECT COUNT(*) FROM nation").fetchone()[0]
    size_mb = out_path.stat().st_size / 1024 / 1024
    log("\n{}  ({:.2f} MB, built in {:.1f}s)".format(out_path, size_mb, time.time() - started))
    log("  teams        : {:>6,}  ({:,} clubs, {:,} non-club)".format(
        teams, clubs, teams - clubs))
    log("  leagues      : {:>6,}".format(leagues))
    log("  nations      : {:>6,}".format(nations))
    log("\n  per year (teams seen):")
    for row in conn.execute(
        "SELECT y, COUNT(*) FROM ("
        "  SELECT team_id, first_seen_year AS y FROM team WHERE first_seen_year IS NOT NULL"
        ") GROUP BY y ORDER BY y"
    ):
        log("    first seen {:>4} : {:>6,}".format(row[0], row[1]))
    log("\n  dropped rows:")
    total_dropped = 0
    for row in conn.execute(
        "SELECT source_file, rows_read, rows_dropped, drop_reasons, status "
        "FROM source_ledger ORDER BY rows_dropped DESC, source_file"
    ):
        if row["status"] != "loaded":
            continue
        total_dropped += row["rows_dropped"]
        if row["rows_dropped"]:
            log("    {:<24} {:>7,} / {:>7,}  {}".format(
                row["source_file"], row["rows_dropped"], row["rows_read"],
                row["drop_reasons"]))
    log("    {:<24} {:>7,} total".format("", total_dropped))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
