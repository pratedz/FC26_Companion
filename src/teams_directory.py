"""Team / league / nation directory — look an EA ``teamid`` up by name.

Every career operation in this app (transfer, loan, "given team" profiles)
takes a numeric EA ``teamid``, and until now nothing could tell the user what
that number is. This module is the lookup: a read-only query API over
``card_db/teams.sqlite``, built by ``tools/build_teams_directory.py``.

The database is *optional*. Every entry point returns ``None`` / an empty
result when it has not been built yet, and nothing here raises because of a
missing, half-written or damaged file — a missing directory must never break
an apply.

Typical use::

    from src import teams_directory as td

    if td.available():
        for hit in td.search_teams("man city"):
            print(hit["team_id"], hit["name"], hit["league_name"])

    hit = td.resolve_team("10")            # id or name, both work
    hit = td.resolve_team("bayern munchen")   # diacritics folded

Ids are stable: ``team_id``, ``league_id`` and ``nation_id`` have not moved
between FIFA 18 and FC 26, which is why a club that last appeared in FIFA 22
still has a usable id today.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import paths

# --------------------------------------------------------------------------
# Constants shared with the builder
# --------------------------------------------------------------------------

DB_FILENAME = "teams.sqlite"
SCHEMA_VERSION = 1

#: The free-agent pool. Not a club — a club lookup must skip it.
FREE_AGENT_TEAM_ID = 111592
FREE_AGENT_NAME = "Free Agents"

LEAGUE_REST_OF_WORLD = 76
LEAGUE_INTERNATIONAL = 78
LEAGUE_INTERNATIONAL_WOMEN = 2136
LEAGUE_CREATE_PLAYER = 383

#: Leagues that hold no real clubs (LE ``lua/DOC.MD``, GetTeamIdFromPlayerId).
PSEUDO_LEAGUES: Dict[int, str] = {
    LEAGUE_REST_OF_WORLD: "Rest of World",
    LEAGUE_INTERNATIONAL: "International",
    LEAGUE_CREATE_PLAYER: "Create Player League",
    LEAGUE_INTERNATIONAL_WOMEN: "International Women",
}

#: FUT-only constructs FUT.GG reports as if they were clubs/leagues. They have
#: no career-mode squad, so a transfer to one of them is meaningless.
FUT_ONLY_TEAMS: Dict[int, str] = {112658: "ICON", 114605: "HERO"}
FUT_ONLY_LEAGUES: Dict[int, str] = {2118: "Icons"}

#: Everything a "pick a club" box must skip.
NON_CLUB_LEAGUE_IDS = frozenset(PSEUDO_LEAGUES) | frozenset(FUT_ONLY_LEAGUES)

#: Where ``generate_list_teams_lua`` tells LE to write the live save dump.
LIVE_TEAMS_FILENAME = "live_teams.json"

_TEAM_COLUMNS = (
    "team_id", "name", "name_norm", "league_id", "country_id", "is_club",
    "first_seen_year", "last_seen_year", "player_count",
)

_LOCAL = threading.local()


# --------------------------------------------------------------------------
# Normalization — same rule as src.card_catalog._norm
# --------------------------------------------------------------------------


def normalize_name(value: object) -> str:
    """NFKD-fold, drop combining marks, lowercase, collapse whitespace."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _squash(value: object) -> str:
    """``normalize_name`` with punctuation dropped ("Nott'm" -> "nottm")."""
    return re.sub(r"[^a-z0-9]+", "", normalize_name(value))


# --------------------------------------------------------------------------
# Connection handling — never raises
# --------------------------------------------------------------------------


def db_path() -> Path:
    """Location of the directory database (built or not)."""
    return paths.card_db_dir() / DB_FILENAME


def _connect() -> Optional[sqlite3.Connection]:
    """Cached read-only connection, or ``None`` when the DB is unusable.

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
        conn.execute("SELECT 1 FROM team LIMIT 1").fetchone()
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
    _LOCAL.leagues = None
    _LOCAL.nations = None


def _lookup(table: str, id_column: str, attr: str) -> Dict[int, str]:
    """Whole-table id -> name cache (73 leagues / 174 nations — trivially small).

    Without it every search hit costs two extra round trips just to label the
    league and country columns.
    """
    cached = getattr(_LOCAL, attr, None)
    if cached is not None:
        return cached
    conn = _connect()
    out: Dict[int, str] = {}
    if conn is not None:
        try:
            out = {
                int(r[0]): r[1] for r in conn.execute(
                    "SELECT {}, name FROM {}".format(id_column, table)
                )
            }
        except Exception:  # noqa: BLE001
            out = {}
    setattr(_LOCAL, attr, out)
    return out


def close() -> None:
    """Drop this thread's cached connection (tests / after a rebuild)."""
    _close_cached()


def available() -> bool:
    """True when the team directory exists and is queryable."""
    return _connect() is not None


# --------------------------------------------------------------------------
# Row shaping
# --------------------------------------------------------------------------


def _to_id(value: object) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _team_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(row)
    out["is_club"] = bool(out.get("is_club"))
    league_id = out.get("league_id")
    out["league_name"] = league_name(league_id) if league_id is not None else None
    country_id = out.get("country_id")
    out["country"] = nation_name(country_id) if country_id is not None else None
    if out.get("team_id") == FREE_AGENT_TEAM_ID:
        out["note"] = "free-agent pool, not a club"
    elif out.get("team_id") in FUT_ONLY_TEAMS:
        out["note"] = "FUT.GG construct, no career squad"
    elif league_id in NON_CLUB_LEAGUE_IDS:
        out["note"] = "{} — not a real club".format(
            PSEUDO_LEAGUES.get(league_id) or FUT_ONLY_LEAGUES.get(league_id)
        )
    try:
        out["aliases"] = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT name FROM team_alias WHERE team_id = ? "
                "AND name_norm <> ? ORDER BY name",
                (out["team_id"], out.get("name_norm") or ""),
            )
        ]
    except Exception:  # noqa: BLE001
        out["aliases"] = []
    return out


def _hit(row: sqlite3.Row, score: int, matched_on: str) -> Dict[str, Any]:
    """The compact shape search results are documented to return."""
    league_id = row["league_id"]
    country_id = row["country_id"]
    return {
        "team_id": row["team_id"],
        "name": row["name"],
        "league_id": league_id,
        "league_name": league_name(league_id) if league_id is not None else None,
        "country": nation_name(country_id) if country_id is not None else None,
        "is_club": bool(row["is_club"]),
        "player_count": row["player_count"],
        "first_seen_year": row["first_seen_year"],
        "last_seen_year": row["last_seen_year"],
        "score": score,
        "matched_on": matched_on,
    }


# --------------------------------------------------------------------------
# Team lookup
# --------------------------------------------------------------------------


def get_team(team_id: Union[int, str]) -> Optional[Dict[str, Any]]:
    """Full row for one team id, or ``None``."""
    conn = _connect()
    if conn is None:
        return None
    tid = _to_id(team_id)
    if tid is None:
        return None
    try:
        row = conn.execute(
            "SELECT {} FROM team WHERE team_id = ?".format(", ".join(_TEAM_COLUMNS)),
            (tid,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _team_dict(conn, row) if row is not None else None


def team_name(team_id: Union[int, str]) -> Optional[str]:
    """Display name for a team id, or ``None`` when it is unknown."""
    conn = _connect()
    if conn is None:
        return None
    tid = _to_id(team_id)
    if tid is None:
        return None
    try:
        row = conn.execute("SELECT name FROM team WHERE team_id = ?", (tid,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return row[0] if row is not None else None


def _fts_query(norm_query: str) -> str:
    """Prefix-matching FTS5 MATCH expression from a normalized query."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", norm_query) if t]
    return " ".join('"{}"*'.format(t) for t in tokens)


def search_teams(
    query: str,
    limit: int = 25,
    clubs_only: bool = True,
) -> List[Dict[str, Any]]:
    """Rank teams matching ``query``.

    Match quality, best first: exact name, exact alias, name prefix, alias
    prefix, then FTS (which folds diacritics, so "Munchen" finds "München"
    and a middle word like "forest" finds "Nottingham Forest"). Ties break on
    club-before-non-club, then squad size, then name.

    ``clubs_only`` hides the free-agent pool, national teams and everything in
    the four pseudo-leagues — which is what a "pick a club" box wants.
    """
    conn = _connect()
    if conn is None:
        return []
    norm_query = normalize_name(query)
    if not norm_query:
        return []
    try:
        cap = max(1, int(limit))
    except (TypeError, ValueError):
        cap = 25
    pool = max(cap * 8, 200)

    score: Dict[int, Tuple[int, str]] = {}

    def add(team_id: int, value: int, label: str) -> None:
        current = score.get(team_id)
        if current is None or current[0] < value:
            score[team_id] = (value, label)

    squashed = _squash(query)
    steps = (
        ("SELECT team_id FROM team WHERE name_norm = ? LIMIT ?",
         (norm_query, pool), 10, "exact"),
        ("SELECT team_id FROM team_alias WHERE name_norm = ? AND source <> 'acronym' LIMIT ?",
         (norm_query, pool), 8, "alias"),
        ("SELECT team_id FROM team_alias WHERE name_norm = ? AND source = 'acronym' LIMIT ?",
         (squashed, pool), 7, "acronym"),
        ("SELECT team_id FROM team WHERE name_norm LIKE ? LIMIT ?",
         (norm_query + "%", pool), 6, "prefix"),
        ("SELECT team_id FROM team_alias WHERE name_norm LIKE ? LIMIT ?",
         (norm_query + "%", pool), 4, "alias-prefix"),
    )
    for sql, args, value, label in steps:
        try:
            for row in conn.execute(sql, args):
                add(row[0], value, label)
        except Exception:  # noqa: BLE001
            pass

    match = _fts_query(norm_query if norm_query else squashed)
    if match:
        try:
            for row in conn.execute(
                "SELECT rowid FROM team_fts WHERE team_fts MATCH ? LIMIT ?",
                (match, pool),
            ):
                add(row[0], 2, "fts")
        except Exception:  # noqa: BLE001
            pass

    if not score:
        return []

    ids = list(score)
    holes = ",".join("?" * len(ids))
    sql = "SELECT {} FROM team WHERE team_id IN ({})".format(
        ", ".join(_TEAM_COLUMNS), holes
    )
    if clubs_only:
        sql += " AND is_club = 1"
    try:
        rows = conn.execute(sql, ids).fetchall()
    except Exception:  # noqa: BLE001
        return []

    hits = [_hit(r, score[r["team_id"]][0], score[r["team_id"]][1]) for r in rows]
    hits.sort(
        key=lambda h: (
            -h["score"],
            0 if h["is_club"] else 1,
            -(h["player_count"] or 0),
            -(h["last_seen_year"] or 0),
            normalize_name(h["name"]),
        )
    )
    return hits[:cap]


def resolve_team(query: object, limit: int = 10) -> Dict[str, Any]:
    """Accept a numeric id *or* a name and return one best match.

    The app's search boxes take either, so this is what they should call::

        resolve_team(10)            -> Manchester City
        resolve_team("man city")    -> Manchester City
        resolve_team("real")        -> best match + alternatives

    Always returns a dict (never raises). ``match`` is ``None`` when nothing
    was found; ``alternatives`` holds the runners-up; ``ambiguous`` is True
    when the runner-up scored as well as the winner.
    """
    text = "" if query is None else str(query).strip()
    out: Dict[str, Any] = {
        "query": text,
        "kind": "empty",
        "match": None,
        "alternatives": [],
        "ambiguous": False,
        "available": available(),
    }
    if not text:
        return out

    if re.fullmatch(r"[0-9]{1,12}", text):
        out["kind"] = "id"
        team = get_team(int(text))
        if team is not None:
            out["match"] = {
                "team_id": team["team_id"],
                "name": team["name"],
                "league_id": team.get("league_id"),
                "league_name": team.get("league_name"),
                "country": team.get("country"),
                "is_club": team.get("is_club"),
                "player_count": team.get("player_count"),
                "first_seen_year": team.get("first_seen_year"),
                "last_seen_year": team.get("last_seen_year"),
                "score": 100,
                "matched_on": "id",
            }
            return out
        # Unknown id — fall through and try it as a name (some clubs are digits).
        out["kind"] = "id-unknown"
    else:
        out["kind"] = "name"

    hits = search_teams(text, limit=max(1, int(limit or 1)), clubs_only=False)
    if not hits:
        return out
    out["match"] = hits[0]
    out["alternatives"] = hits[1:]
    if len(hits) > 1 and hits[1]["score"] == hits[0]["score"]:
        out["ambiguous"] = True
    return out


def teams_in_league(
    league_id: Union[int, str],
    clubs_only: bool = True,
) -> List[Dict[str, Any]]:
    """Every team in one league, biggest squad first."""
    conn = _connect()
    if conn is None:
        return []
    lid = _to_id(league_id)
    if lid is None:
        return []
    sql = "SELECT {} FROM team WHERE league_id = ?".format(", ".join(_TEAM_COLUMNS))
    if clubs_only:
        sql += " AND is_club = 1"
    sql += " ORDER BY player_count DESC, name"
    try:
        rows = conn.execute(sql, (lid,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_hit(r, 0, "league") for r in rows]


# --------------------------------------------------------------------------
# League / nation lookup
# --------------------------------------------------------------------------


def league_name(league_id: Union[int, str, None]) -> Optional[str]:
    """Display name for a league id, or ``None``."""
    lid = _to_id(league_id)
    if lid is None:
        return None
    if _connect() is None:
        return PSEUDO_LEAGUES.get(lid)
    name = _lookup("league", "league_id", "leagues").get(lid)
    return name if name is not None else PSEUDO_LEAGUES.get(lid)


def get_league(league_id: Union[int, str]) -> Optional[Dict[str, Any]]:
    """Full league row, or ``None``."""
    conn = _connect()
    if conn is None:
        return None
    lid = _to_id(league_id)
    if lid is None:
        return None
    try:
        row = conn.execute(
            "SELECT league_id, name, name_norm, country_id, is_real_competition "
            "FROM league WHERE league_id = ?",
            (lid,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    out = dict(row)
    out["is_real_competition"] = bool(out.get("is_real_competition"))
    out["country"] = nation_name(out.get("country_id"))
    try:
        out["team_count"] = conn.execute(
            "SELECT COUNT(*) FROM team WHERE league_id = ?", (lid,)
        ).fetchone()[0]
    except Exception:  # noqa: BLE001
        out["team_count"] = 0
    return out


def search_leagues(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Rank leagues matching ``query`` (sponsor names are matched too)."""
    conn = _connect()
    if conn is None:
        return []
    norm_query = normalize_name(query)
    if not norm_query:
        return []
    try:
        cap = max(1, int(limit))
    except (TypeError, ValueError):
        cap = 25
    # league_id -> (score, label, length of the name that matched)
    score: Dict[int, Tuple[int, str, int]] = {}

    def add(league_id: int, value: int, label: str, matched: str) -> None:
        current = score.get(league_id)
        entry = (value, label, len(matched or ""))
        if current is None or (entry[0], -entry[2]) > (current[0], -current[2]):
            score[league_id] = entry

    steps = (
        ("SELECT league_id, name_norm FROM league WHERE name_norm = ?",
         (norm_query,), 4, "exact"),
        ("SELECT league_id, name_norm FROM league_alias WHERE name_norm = ?",
         (norm_query,), 3, "alias"),
        ("SELECT league_id, name_norm FROM league WHERE name_norm LIKE ?",
         ("%" + norm_query + "%",), 2, "contains"),
        ("SELECT league_id, name_norm FROM league_alias WHERE name_norm LIKE ?",
         ("%" + norm_query + "%",), 1, "alias-contains"),
    )
    for sql, args, value, label in steps:
        try:
            for row in conn.execute(sql, args):
                add(row[0], value, label, row[1])
        except Exception:  # noqa: BLE001
            pass
    if not score:
        return []

    ids = list(score)
    holes = ",".join("?" * len(ids))
    try:
        rows = conn.execute(
            "SELECT l.league_id, l.name, l.country_id, l.is_real_competition, "
            "  (SELECT COUNT(*) FROM team t WHERE t.league_id = l.league_id) AS team_count "
            "FROM league l WHERE l.league_id IN ({})".format(holes),
            ids,
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for row in rows:
        value, label, matched_len = score[row["league_id"]]
        out.append({
            "league_id": row["league_id"],
            "name": row["name"],
            "country": nation_name(row["country_id"]),
            "is_real_competition": bool(row["is_real_competition"]),
            "team_count": row["team_count"],
            "score": value,
            "matched_on": label,
            "_matched_len": matched_len,
        })
    # Closest match first: a shorter matched string means less unmatched text.
    out.sort(key=lambda h: (
        -h["score"], h["_matched_len"], -(h["team_count"] or 0), normalize_name(h["name"])
    ))
    for hit in out:
        hit.pop("_matched_len", None)
    return out[:cap]


def nation_name(nation_id: Union[int, str, None]) -> Optional[str]:
    """Display name for a nation id, or ``None``."""
    nid = _to_id(nation_id)
    if nid is None or _connect() is None:
        return None
    return _lookup("nation", "nation_id", "nations").get(nid)


def search_nations(query: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Rank nations matching ``query``."""
    conn = _connect()
    if conn is None:
        return []
    norm_query = normalize_name(query)
    if not norm_query:
        return []
    try:
        rows = conn.execute(
            "SELECT nation_id, name FROM nation "
            "WHERE name_norm = ? OR name_norm LIKE ? "
            "ORDER BY CASE WHEN name_norm = ? THEN 0 ELSE 1 END, name LIMIT ?",
            (norm_query, "%" + norm_query + "%", norm_query, max(1, int(limit))),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"nation_id": r["nation_id"], "name": r["name"]} for r in rows]


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------


def stats() -> Optional[Dict[str, Any]]:
    """Row counts and build metadata, or ``None`` when not built."""
    conn = _connect()
    if conn is None:
        return None
    out: Dict[str, Any] = {"db_path": str(db_path())}
    try:
        out["teams"] = conn.execute("SELECT COUNT(*) FROM team").fetchone()[0]
        out["clubs"] = conn.execute(
            "SELECT COUNT(*) FROM team WHERE is_club = 1"
        ).fetchone()[0]
        out["non_clubs"] = out["teams"] - out["clubs"]
        out["leagues"] = conn.execute("SELECT COUNT(*) FROM league").fetchone()[0]
        out["real_competitions"] = conn.execute(
            "SELECT COUNT(*) FROM league WHERE is_real_competition = 1"
        ).fetchone()[0]
        out["nations"] = conn.execute("SELECT COUNT(*) FROM nation").fetchone()[0]
        out["team_aliases"] = conn.execute("SELECT COUNT(*) FROM team_alias").fetchone()[0]
        out["teams_by_last_seen"] = {
            int(r[0]): int(r[1])
            for r in conn.execute(
                "SELECT last_seen_year, COUNT(*) FROM team "
                "WHERE last_seen_year IS NOT NULL GROUP BY last_seen_year "
                "ORDER BY last_seen_year"
            )
        }
        out["teams_without_league"] = conn.execute(
            "SELECT COUNT(*) FROM team WHERE league_id IS NULL"
        ).fetchone()[0]
        out["teams_without_country"] = conn.execute(
            "SELECT COUNT(*) FROM team WHERE country_id IS NULL"
        ).fetchone()[0]
        out["meta"] = {str(r[0]): r[1] for r in conn.execute("SELECT key, value FROM meta")}
        out["sources"] = [
            dict(r) for r in conn.execute(
                "SELECT source_file, source, year, rows_read, rows_kept, rows_dropped, "
                "drop_reasons, status, note FROM source_ledger "
                "ORDER BY status, source_file"
            )
        ]
    except Exception:  # noqa: BLE001
        return out
    return out


# --------------------------------------------------------------------------
# Live-save reconciliation (LE Lua export)
# --------------------------------------------------------------------------


def live_teams_path() -> Path:
    """Where the generated Lua writes the live save's team list."""
    return paths.generated_dir() / LIVE_TEAMS_FILENAME


def _lua_escape(value: object) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )


def generate_list_teams_lua(out_path: Union[str, Path, None] = None) -> str:
    """Lua that dumps the LIVE save's ``teams`` table to JSON.

    The shipped CSVs describe a stock FC 26 database. A modded save can rename
    clubs, add them or renumber leagues, so the directory is only ever a good
    guess until it is reconciled against what the user actually has loaded.

    Reads the table with ``GetDBTableRows("teams")`` — the project's
    established freeze-safe pattern (full record walks via
    ``GetFirstRecord``/``GetNextValidRecord`` freeze FC 26 on big tables).
    Every global is guarded with ``type(x) == "function"`` and every call is
    wrapped in ``pcall``, so the script cannot throw and cannot fail a bridge
    run. Follows the conventions in ``src/squad_export.py`` /
    ``bridge/export_user_squad.lua``: absolute ``OUT_PATH`` baked in by Python,
    silent ``Log`` prefix, plain ``io.open`` write, no asserts.
    """
    target = Path(out_path) if out_path else live_teams_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    out = _lua_escape(str(target.resolve()).replace("\\", "/"))
    return _LIST_TEAMS_LUA.replace("__OUT_PATH__", out)


_LIST_TEAMS_LUA = '''--[[ Export the LIVE save's teams table -> JSON (silent, read-only).
     Generated by src/teams_directory.generate_list_teams_lua().
     Never asserts and never errors: the bridge treats a raised error as a
     failed run, and this job must never be able to break an apply.
]]

local OUT_PATH = "__OUT_PATH__"

local function log(msg)
    if type(Log) == "function" then pcall(Log, "[list_teams] " .. tostring(msg)) end
end

local function json_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\\\", "\\\\\\\\")
    s = s:gsub('"', '\\\\"')
    s = s:gsub("\\r", "\\\\r")
    s = s:gsub("\\n", "\\\\n")
    s = s:gsub("\\t", "\\\\t")
    return s
end

local function write_file(path, data)
    local ok, f = pcall(io.open, path, "w+")
    if not ok or not f then return false end
    pcall(function() f:write(data) end)
    pcall(function() f:close() end)
    return true
end

-- DBRow fields arrive as { value = "..." } tables; plain values happen too.
local function field(row, key)
    if type(row) ~= "table" then return nil end
    local cell = row[key]
    if cell == nil then return nil end
    if type(cell) == "table" then return cell.value end
    return cell
end

local teams = {}
local count = 0
local source = "none"

if type(GetDBTableRows) == "function" then
    local ok, rows = pcall(GetDBTableRows, "teams")
    if ok and type(rows) == "table" then
        source = "GetDBTableRows"
        for _, row in pairs(rows) do
            local tid = tonumber(field(row, "teamid"))
            if tid and tid > 0 then
                local name = field(row, "teamname")
                if (name == nil or name == "") and type(GetTeamName) == "function" then
                    local okn, resolved = pcall(GetTeamName, tid)
                    if okn and resolved then name = resolved end
                end
                count = count + 1
                teams[count] = string.format(
                    '{"team_id":%d,"name":"%s"}', tid, json_escape(name)
                )
            end
        end
    else
        log("GetDBTableRows('teams') failed")
    end
else
    log("GetDBTableRows unavailable in this LE build")
end

local parts = {}
parts[#parts + 1] = '{"source":"' .. json_escape(source) .. '"'
parts[#parts + 1] = ',"count":' .. tostring(count)
parts[#parts + 1] = ',"teams":['
parts[#parts + 1] = table.concat(teams, ",")
parts[#parts + 1] = "]}"

if write_file(OUT_PATH, table.concat(parts)) then
    log("wrote " .. tostring(count) .. " teams -> " .. OUT_PATH)
else
    log("write failed -> " .. OUT_PATH)
end
'''


def load_live_teams(path: Union[str, Path, None] = None) -> Optional[Dict[int, str]]:
    """Read the JSON written by ``generate_list_teams_lua``, or ``None``."""
    target = Path(path) if path else live_teams_path()
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out: Dict[int, str] = {}
    for entry in data.get("teams") or []:
        if not isinstance(entry, dict):
            continue
        tid = _to_id(entry.get("team_id"))
        if tid is None:
            continue
        out[tid] = str(entry.get("name") or "")
    return out


def reconcile_live_teams(path: Union[str, Path, None] = None) -> Optional[Dict[str, Any]]:
    """Compare the live save's teams against the directory.

    Returns ``None`` when either side is missing. ``renamed`` catches mods that
    change a club's name, ``only_in_save`` catches added teams, and
    ``only_in_directory`` catches teams the save does not have (so the app can
    stop offering an id the user cannot transfer to).
    """
    live = load_live_teams(path)
    conn = _connect()
    if live is None or conn is None:
        return None
    try:
        known = {r[0]: r[1] for r in conn.execute("SELECT team_id, name FROM team")}
    except Exception:  # noqa: BLE001
        return None
    renamed: List[Dict[str, Any]] = []
    for tid, name in live.items():
        other = known.get(tid)
        if other is None or not name:
            continue
        if normalize_name(name) != normalize_name(other):
            renamed.append({"team_id": tid, "save_name": name, "directory_name": other})
    renamed.sort(key=lambda r: r["team_id"])
    return {
        "live_teams": len(live),
        "directory_teams": len(known),
        "matched": len(set(live) & set(known)),
        "only_in_save": sorted(set(live) - set(known)),
        "only_in_directory": sorted(set(known) - set(live)),
        "renamed": renamed,
    }
