"""Player Universe — cross-year (FIFA 18 … FC 26) player database.

Read-only query API over ``card_db/universe.sqlite``, built by
``tools/build_universe.py``. The database is *optional*: every entry point
returns ``None`` / an empty result when it has not been built yet, and no
function in this module raises because of a missing or damaged file.

Two levels, identity separated from observation:

``person``
    One row per real footballer, keyed by the EA ``playerid``.

``observation``
    One row per (person, year, source, card variant). A player who appears in
    FIFA 18–23, FC 24–26 *and* has eleven FUT cards in FC 26 has one ``person``
    row and ~20 ``observation`` rows.

Typical use::

    from src import universe
    if universe.available():
        for hit in universe.search("mbappe", min_ovr=88):
            print(hit["display_name"], hit["first_seen_year"], hit["last_seen_year"])
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import paths

# --------------------------------------------------------------------------
# Constants shared with the builder
# --------------------------------------------------------------------------

DB_FILENAME = "universe.sqlite"
SCHEMA_VERSION = 1

#: Game years covered, oldest first.
YEARS: Tuple[int, ...] = (18, 19, 20, 21, 22, 23, 24, 25, 26)

#: EA / LE preferred-position codes. Verified against player_presets:
#: Messi RW=23, De Bruyne CM=14, Ronaldo ST=25.
POSITION_CODES: Dict[str, int] = {
    "GK": 0, "SW": 1, "RWB": 2, "RB": 3, "RCB": 4, "CB": 5, "LCB": 6,
    "LB": 7, "LWB": 8, "RDM": 9, "CDM": 10, "LDM": 11, "RM": 12, "RCM": 13,
    "CM": 14, "LCM": 15, "LM": 16, "RAM": 17, "CAM": 18, "LAM": 19,
    "RF": 20, "CF": 21, "LF": 22, "RW": 23, "RS": 24, "ST": 25, "LS": 26,
    "LW": 27,
}
POSITION_NAMES: Dict[int, str] = {v: k for k, v in POSITION_CODES.items()}

#: How much a source is trusted for identity fields and for "best observation"
#: tie-breaks. Higher wins.
SOURCE_PRIORITY: Dict[str, int] = {
    "futgg": 100,
    "sofifa_datahub": 90,
    "le_base": 85,
    "sofifa": 75,
    "sofifa_fc25": 70,
    "ea_ratings": 65,
    "le_cards": 60,
    "sofifa_official": 35,
    "futhead": 10,
    "futbin": 10,
}

#: Values the builder writes to ``observation.attr_fidelity``.
ATTR_FIDELITY = ("granular", "partial", "expanded_from_face", "none")

_PERSON_COLUMNS = (
    "person_id", "display_name", "name_norm", "firstname", "surname",
    "commonname", "dob", "nationality_id", "nationality_name",
    "exists_in_fc26", "first_seen_year", "last_seen_year", "best_overall",
    "obs_count", "year_mask",
)

_OBS_COLUMNS = (
    "obs_id", "person_id", "year", "source", "source_file", "source_kind",
    "variant", "variant_id", "display_name", "overall", "potential",
    "preferredposition1", "preferredposition2", "preferredposition3",
    "preferredposition4", "positions_text", "club_id", "club_name",
    "league_id", "league_name", "nationality_id", "nationality_name",
    "height", "weight", "preferredfoot", "skillmoves", "weakfoot",
    "trait1", "trait2", "icontrait1", "icontrait2", "roles", "playstyles",
    "attrs", "attr_fidelity", "attr_granular_n", "attr_expanded_n",
    "id_provenance",
)

_JSON_OBS_FIELDS = ("roles", "playstyles", "attrs")

_LOCAL = threading.local()


# --------------------------------------------------------------------------
# Text helpers (shared with the builder)
# --------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_APOSTROPHE = re.compile("['’ʼ`´]")

#: Latin letters NFKD does *not* decompose — stroked, barred and ligatured
#: forms. Without these "Ødegaard" folds to "degaard" and a plain "odegaard"
#: query finds nothing (SQLite's remove_diacritics=2 does not fold them either).
_TRANSLITERATE = str.maketrans({
    "ø": "o",   # ø
    "æ": "ae",  # æ
    "œ": "oe",  # œ
    "ð": "d",   # ð
    "đ": "d",   # đ
    "ł": "l",   # ł
    "ß": "ss",  # ß
    "þ": "th",  # þ
    "ı": "i",   # ı
    "ŋ": "ng",  # ŋ
    "ħ": "h",   # ħ
    "ĳ": "ij",  # ĳ
    "ŧ": "t",   # ŧ
    "ƶ": "z",   # ƶ
    "ə": "e",   # ə
    "ơ": "o",   # ơ
    "ư": "u",   # ư
})


def normalize_name(value: object) -> str:
    """Fold to lowercase ASCII words: ``"Mbappé"`` -> ``"mbappe"``.

    Used for both the stored ``name_norm`` and for query normalization, so an
    unaccented query always reaches an accented name even without FTS.
    Apostrophes are removed rather than split on, so "N'Golo" -> "ngolo".
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _APOSTROPHE.sub("", text.lower()).translate(_TRANSLITERATE)
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def position_code(value: object) -> Optional[int]:
    """Accept ``"ST"``, ``"st"``, ``25`` or ``"25"`` and return the LE code."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in POSITION_NAMES else None
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        code = int(text)
        return code if code in POSITION_NAMES else None
    return POSITION_CODES.get(text.upper())


# --------------------------------------------------------------------------
# Connection handling — never raises
# --------------------------------------------------------------------------


def db_path() -> Path:
    """Location of the universe database (built or not)."""
    return paths.card_db_dir() / DB_FILENAME


def _connect() -> Optional[sqlite3.Connection]:
    """Return a cached read-only connection, or ``None`` if unusable.

    Cached per thread and invalidated when the file's (mtime, size) changes so
    a rebuild is picked up without restarting the app.
    """
    path = db_path()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        _close_cached()
        return None

    cached = getattr(_LOCAL, "conn", None)
    if cached is not None and getattr(_LOCAL, "stamp", None) == stamp:
        return cached
    _close_cached()

    try:
        uri = "file:{}?mode=ro".format(str(path).replace("?", "%3f").replace("#", "%23"))
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Cheap smoke test: a damaged / half-written file fails here, not later.
        conn.execute("SELECT 1 FROM person LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 - a broken DB must degrade, never raise
        return None
    _LOCAL.conn = conn
    _LOCAL.stamp = stamp
    return conn


def _close_cached() -> None:
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    _LOCAL.conn = None
    _LOCAL.stamp = None


def close() -> None:
    """Drop this thread's cached connection (tests / after a rebuild)."""
    _close_cached()


def available() -> bool:
    """True when the universe database exists and is queryable."""
    return _connect() is not None


# --------------------------------------------------------------------------
# Row shaping
# --------------------------------------------------------------------------


def _obs_dict(row: sqlite3.Row) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(row)
    for key in _JSON_OBS_FIELDS:
        raw = out.get(key)
        if isinstance(raw, str) and raw:
            try:
                out[key] = json.loads(raw)
            except ValueError:
                out[key] = None
    codes = [
        out.get("preferredposition{}".format(i)) for i in range(1, 5)
    ]
    out["positions"] = [
        POSITION_NAMES.get(c, str(c)) for c in codes if c is not None and c >= 0
    ]
    return out


def _person_dict(row: sqlite3.Row) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(row)
    out["exists_in_fc26"] = bool(out.get("exists_in_fc26"))
    mask = out.pop("year_mask", 0) or 0
    out["years"] = [y for y in YEARS if mask & (1 << (y - YEARS[0]))]
    return out


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def get_person(playerid: Union[int, str]) -> Optional[Dict[str, Any]]:
    """Identity row for one EA playerid, or ``None``."""
    conn = _connect()
    if conn is None:
        return None
    try:
        pid = int(str(playerid).strip())
    except (TypeError, ValueError):
        return None
    try:
        row = conn.execute(
            "SELECT {} FROM person WHERE person_id = ?".format(", ".join(_PERSON_COLUMNS)),
            (pid,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _person_dict(row) if row is not None else None


def versions_of(
    playerid: Union[int, str],
    year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Every year/source/variant this player appears in, newest year first."""
    conn = _connect()
    if conn is None:
        return []
    try:
        pid = int(str(playerid).strip())
    except (TypeError, ValueError):
        return []
    sql = "SELECT {} FROM observation WHERE person_id = ?".format(", ".join(_OBS_COLUMNS))
    args: List[Any] = [pid]
    if year is not None:
        sql += " AND year = ?"
        args.append(int(year))
    sql += " ORDER BY year DESC, overall DESC, source, variant"
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_obs_dict(r) for r in rows]


def _best_per_year(
    conn: sqlite3.Connection,
    person_ids: Sequence[int],
    year: Optional[int],
) -> Dict[int, List[Dict[str, Any]]]:
    """Highest-rated observation per (person, year), source priority breaking ties."""
    if not person_ids:
        return {}
    holes = ",".join("?" * len(person_ids))
    sql = (
        "SELECT {cols} FROM observation "
        "WHERE person_id IN ({holes})".format(
            cols=", ".join(_OBS_COLUMNS), holes=holes
        )
    )
    args: List[Any] = list(person_ids)
    if year is not None:
        sql += " AND year = ?"
        args.append(int(year))
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:  # noqa: BLE001
        return {}

    best: Dict[Tuple[int, int], Tuple[Tuple[int, int], Dict[str, Any]]] = {}
    for row in rows:
        key = (row["person_id"], row["year"])
        rank = (
            row["overall"] if row["overall"] is not None else -1,
            SOURCE_PRIORITY.get(row["source"], 0),
        )
        current = best.get(key)
        if current is None or rank > current[0]:
            best[key] = (rank, _obs_dict(row))

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for (pid, _y), (_rank, obs) in best.items():
        grouped.setdefault(pid, []).append(obs)
    for obs_list in grouped.values():
        obs_list.sort(key=lambda o: o["year"], reverse=True)
    return grouped


def _fts_query(norm_query: str, prefix: bool) -> str:
    """Build an FTS5 MATCH expression from an already-normalized query."""
    tokens = [t for t in norm_query.split(" ") if t]
    star = "*" if prefix else ""
    return " ".join('"{}"{}'.format(t.replace('"', '""'), star) for t in tokens)


def _candidate_ids(
    conn: sqlite3.Connection, query: str, pool: int
) -> Tuple[List[int], Dict[int, int]]:
    """Resolve a text query to candidate person ids plus a match-quality score.

    Tiers, best first: whole name is exactly the query; the query matches whole
    name *words* (so "sane" reaches "Leroy Sané" rather than "Sanel Bojadžić");
    the whole name starts with the query; any word starts with the query;
    finally a substring scan. Word tiers run through FTS5, so they stay indexed.
    """
    norm = normalize_name(query)
    if not norm:
        return [], {}

    score: Dict[int, int] = {}
    ordered: List[int] = []

    def add(pid: int, value: int) -> None:
        if pid not in score:
            ordered.append(pid)
        if score.get(pid, -1) < value:
            score[pid] = value

    def run(sql: str, args: Sequence[Any], value: int) -> None:
        try:
            for row in conn.execute(sql, args):
                add(row[0], value)
        except Exception:  # noqa: BLE001
            # A malformed MATCH expression is a query problem, not a DB problem.
            pass

    run("SELECT person_id FROM person WHERE name_norm = ? LIMIT ?",
        (norm, pool), 4)
    run("SELECT rowid FROM person_fts WHERE person_fts MATCH ? LIMIT ?",
        (_fts_query(norm, prefix=False), pool), 3)
    run("SELECT person_id FROM person WHERE name_norm LIKE ? LIMIT ?",
        (norm + "%", pool), 2)
    if len(ordered) < pool:
        run("SELECT rowid FROM person_fts WHERE person_fts MATCH ? LIMIT ?",
            (_fts_query(norm, prefix=True), pool), 1)
    if not ordered:
        run("SELECT person_id FROM person WHERE name_norm LIKE ? LIMIT ?",
            ("%" + norm + "%", pool), 0)
    return ordered, score


def _obs_filters(
    alias: str,
    year: Optional[int],
    min_ovr: Optional[int],
    pos: Optional[int],
) -> Tuple[List[str], List[Any]]:
    """SQL predicates restricting ``observation`` rows, and their arguments."""
    clauses: List[str] = []
    args: List[Any] = []
    if year is not None:
        clauses.append("{}year = ?".format(alias))
        args.append(year)
    if min_ovr is not None:
        clauses.append("{}overall >= ?".format(alias))
        args.append(min_ovr)
    if pos is not None:
        clauses.append(
            "({a}preferredposition1 = ? OR {a}preferredposition2 = ? "
            "OR {a}preferredposition3 = ? OR {a}preferredposition4 = ?)".format(a=alias)
        )
        args.extend([pos] * 4)
    return clauses, args


def _browse(
    conn: sqlite3.Connection,
    year: Optional[int],
    min_ovr: Optional[int],
    pos: Optional[int],
    limit: int,
) -> List[Dict[str, Any]]:
    """No-query mode: rank by the best *matching* observation.

    Scanning ``observation`` on its (year, overall) index and de-duplicating is
    far cheaper than walking ``person`` and probing each one — a player with 20
    FUT variants would otherwise be re-tested 20 times.
    """
    clauses, args = _obs_filters("", year, min_ovr, pos)
    if not clauses:
        # Nothing to filter: person.best_overall is already the answer, indexed.
        try:
            rows = conn.execute(
                "SELECT {} FROM person ORDER BY best_overall DESC, person_id "
                "LIMIT ?".format(", ".join(_PERSON_COLUMNS)),
                (limit,),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        return [_person_dict(r) for r in rows]

    sql = "SELECT person_id FROM observation"
    sql += " WHERE " + " AND ".join(clauses)
    # Oversample: one person can own many observations in the scanned range.
    sql += " ORDER BY overall DESC LIMIT ?"
    args = list(args) + [min(max(limit * 40, 400), 40000)]
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception:  # noqa: BLE001
        return []

    ids: List[int] = []
    seen: set = set()
    for row in rows:
        pid = row[0]
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
            if len(ids) >= limit:
                break
    if not ids:
        return []
    order = {pid: i for i, pid in enumerate(ids)}
    try:
        people = conn.execute(
            "SELECT {} FROM person WHERE person_id IN ({})".format(
                ", ".join(_PERSON_COLUMNS), ",".join("?" * len(ids))
            ),
            ids,
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    result = [_person_dict(r) for r in people]
    result.sort(key=lambda p: order.get(p["person_id"], len(order)))
    return result


def search(
    query: str = "",
    year: Optional[int] = None,
    min_ovr: Optional[int] = None,
    position: Optional[Union[str, int]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Ranked persons matching ``query``, each with its best observation per year.

    Args:
        query: Free text; diacritic-insensitive ("Mbappe" finds "Mbappé").
            Empty means "no name filter" — browse by the other filters.
        year: Restrict to players present in that game year (18…26). The
            returned ``observations`` are then limited to that year too.
        min_ovr: Player must have at least one observation at or above this
            overall (within ``year`` when given).
        position: ``"ST"`` or ``25``; matched against preferredposition1..4.
        limit: Maximum persons returned.

    Returns:
        A list of person dicts with an added ``observations`` list (best card
        per year, newest first) and ``best_observation``. Empty list when the
        database has not been built.
    """
    conn = _connect()
    if conn is None:
        return []
    try:
        limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        limit = 50

    pos = position_code(position) if position is not None else None
    if position is not None and pos is None:
        return []
    year_i = int(year) if year is not None else None
    min_ovr_i = int(min_ovr) if min_ovr is not None else None

    if not str(query or "").strip():
        people = _browse(conn, year_i, min_ovr_i, pos, limit)
    else:
        pool = min(limit * 8, 4000)
        candidates, score = _candidate_ids(conn, query, pool)
        if not candidates:
            return []
        where = ["p.person_id IN ({})".format(",".join("?" * len(candidates)))]
        args: List[Any] = list(candidates)
        clauses, filter_args = _obs_filters("o.", year_i, min_ovr_i, pos)
        if clauses:
            where.append(
                "EXISTS (SELECT 1 FROM observation o WHERE o.person_id = p.person_id "
                "AND {})".format(" AND ".join(clauses))
            )
            args.extend(filter_args)
        try:
            rows = conn.execute(
                "SELECT {} FROM person p WHERE {}".format(
                    ", ".join("p." + c for c in _PERSON_COLUMNS), " AND ".join(where)
                ),
                args,
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        people = [_person_dict(r) for r in rows]
        people.sort(
            key=lambda p: (
                -score.get(p["person_id"], 0),
                -(p["best_overall"] or 0),
                0 if p["exists_in_fc26"] else 1,
                p["person_id"],
            )
        )
        people = people[:limit]

    grouped = _best_per_year(conn, [p["person_id"] for p in people], year_i)
    for person in people:
        obs = grouped.get(person["person_id"], [])
        person["observations"] = obs
        person["best_observation"] = (
            max(obs, key=lambda o: (o["overall"] or -1)) if obs else None
        )
    return people


def stats() -> Optional[Dict[str, Any]]:
    """Build metadata and row counts, or ``None`` when not built."""
    conn = _connect()
    if conn is None:
        return None
    out: Dict[str, Any] = {"db_path": str(db_path())}
    #: Rolled up at build time — recomputing them costs ~0.5s over 240k rows.
    live = {
        "persons": "SELECT COUNT(*) FROM person",
        "observations": "SELECT COUNT(*) FROM observation",
        "persons_in_fc26": "SELECT COUNT(*) FROM person WHERE exists_in_fc26 = 1",
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
    try:
        year_keyed = ("by_year", "persons_by_year")
        meta = {str(r[0]): r[1] for r in conn.execute("SELECT key, value FROM meta")}
        for key, sql in live.items():
            cached = meta.pop("summary_" + key, None)
            value: Any = None
            if cached is not None:
                try:
                    value = json.loads(cached)
                except ValueError:
                    value = None
            if value is None:
                rows = conn.execute(sql).fetchall()
                value = rows[0][0] if len(rows[0]) == 1 else {
                    r[0]: int(r[1]) for r in rows
                }
            if key in year_keyed and isinstance(value, dict):
                # JSON object keys are strings; years are ints everywhere else.
                value = {int(k): int(v) for k, v in value.items()}
            out[key] = value
        out["meta"] = meta
        out["sources"] = [dict(r) for r in conn.execute(
            "SELECT source_file, source, source_kind, year, rows_read, rows_kept, "
            "rows_dropped, drop_reasons, status, note FROM source_ledger "
            "ORDER BY year, source_file"
        )]
    except Exception:  # noqa: BLE001
        return out or None
    return out


def sources() -> List[Dict[str, Any]]:
    """The per-file ETL ledger: what was read, kept, dropped and why."""
    conn = _connect()
    if conn is None:
        return []
    try:
        return [dict(r) for r in conn.execute(
            "SELECT source_file, source, source_kind, year, rows_read, rows_kept, "
            "rows_dropped, drop_reasons, status, note FROM source_ledger "
            "ORDER BY year, source_file"
        )]
    except Exception:  # noqa: BLE001
        return []


def iter_persons(limit: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    """Stream every person row (helper for exports / bulk tooling)."""
    conn = _connect()
    if conn is None:
        return []
    sql = "SELECT {} FROM person ORDER BY person_id".format(", ".join(_PERSON_COLUMNS))
    if limit is not None:
        sql += " LIMIT {:d}".format(int(limit))
    try:
        return [_person_dict(r) for r in conn.execute(sql)]
    except Exception:  # noqa: BLE001
        return []
