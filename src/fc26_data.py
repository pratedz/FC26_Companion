"""FC 26 data oracle: authoritative lookup tables, none of them guessed.

Everything here replaces a hand-written table that was wrong, or a value the
companion used to invent. The data itself lives in ``src/generated/fc26_tables.py``
and is produced by ``tools/build_fc26_tables.py`` from files that ship with the
game / Live Editor. This module only wraps it with a forgiving API, so a missing
or corrupt generated file degrades to "I don't know" instead of an ImportError at
startup.

Why each table exists
---------------------

``FC26_REAL_HEADS`` / ``FC26_HEAD_NAMES``
    Which playerids actually ship with a scanned head model. The add/import path
    cannot tell from the card alone, and the two obvious flags lie: as
    ``src/base_players.py`` documents, ``hashighqualityhead`` is 0 for 19,237 of
    22,348 base players (Zidane and Pele included), so treating it as an
    "is real face" flag hides most real faces. Setting ``headclasscode = 0`` and
    ``headassetid = <playerid>`` for a player EA never shipped a head for is the
    mirror-image bug: an invisible or default-doll head in game.
    The set is the union of two authorities — the 5,737 ids in Live Editor's own
    ``lua/scripts/fix_players_headmodels.lua`` (EA's shipped head-model list,
    which the stock script uses for exactly this decision) and every row in
    ``player_presets/base_players.csv`` with ``hashighqualityhead = 1`` or
    ``headclasscode = 0``.

``NATION_NAMES`` / ``NATION_IDS`` / :func:`nation_id_for`
    ``src/add_player.py`` carries ``_NATION_NAME_TO_ID``, a 26-entry hand-written
    table in which ``"nigeria": 27`` — but 27 is **Italy**. Every FUT card that
    arrived with a nation name instead of a numeric id turned Nigerian players
    Italian, silently and irreversibly (the value is written straight into the
    players table). The same table covers 26 of the game's 160 nations, so the
    other 134 resolved to ``None`` and the player kept whatever nationality the
    template row had. The generated table is derived from
    ``card_db/fc26_datahub.csv``, which carries both id and name for every nation
    (nigeria = 133, italy = 27, spain = 45).

``LEAGUE_NAMES`` / ``TEAM_NAMES`` / :func:`team_id_for`
    Same source, same problem class: club and league ids were previously typed by
    hand into generated Lua. Note that league *names* are not unique — "Premier
    League" is both 13 (England) and 332 (Russia), "Bundesliga" is 19 and 80 —
    so the reverse map records a deterministic winner (most players, then lowest
    id) and the losers stay reachable through :data:`LEAGUE_ID_COLLISIONS`.

``ROLE_NAMES`` / :func:`role_name`
    ``src/player_schema.py`` exposes ``role1``/``role2`` as bare "LE role id"
    integers, so the UI showed numbers and nobody could tell 21 from 121. FC 26
    stores role familiarity as ``id`` for "+" and ``id + 100`` for "++"; an id of
    121 is not out of range, it is CM Playmaker++. Ids above 52 that are not in
    the 101..152 band are invalid — the real player rows only ever contain
    0, 1..52 and 101..149.

``GENERIC_HEADTYPES`` / :func:`pick_generic_headtype`
    A generic head is only safe if the ``headtypecode`` exists for that
    nationality's head set; ``add_player`` defaults it to ``"0"`` for everyone.
    The map is built empirically from the 17,968 base rows that actually use a
    generic head (``headclasscode = 1``), so a pick can only ever return a code
    EA already ships for that nation, weighted by how often it is used.

All accessors accept sloppy input (``None``, strings, floats) and return ``None``
or a safe default rather than raising.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import unicodedata
from types import MappingProxyType
from typing import Any, Final, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "BASE_ROLE_NAMES",
    "FC26_HEAD_NAMES",
    "FC26_REAL_HEADS",
    "GENERIC_HEADTYPES",
    "GENERIC_HEADTYPES_ANY",
    "LEAGUE_ID_COLLISIONS",
    "LEAGUE_IDS",
    "LEAGUE_NAMES",
    "NATION_ID_COLLISIONS",
    "NATION_IDS",
    "NATION_NAMES",
    "ROLE_NAMES",
    "TEAM_ID_COLLISIONS",
    "TEAM_IDS",
    "TEAM_IDS_LOOSE",
    "TEAM_NAMES",
    "generic_headtypes",
    "has_real_head",
    "head_name",
    "league_id_for",
    "league_name",
    "nation_id_for",
    "nation_name",
    "normalize_name",
    "pick_generic_headtype",
    "role_name",
    "source_stats",
    "tables_loaded",
    "team_id_for",
    "team_name",
]

WeightedCodes = Tuple[Tuple[int, int], ...]


# --------------------------------------------------------------------------
# generated tables (optional at runtime)
# --------------------------------------------------------------------------
def _load_tables() -> Any:
    """Import the generated tables, or return None if they are unusable.

    The sibling-package import is written out in full rather than resolved
    through importlib alone, because PyInstaller's dependency analysis only sees
    literal imports — a purely dynamic load would silently ship an .exe with no
    tables in it. The importlib attempts cover the flat layouts (module imported
    without its package, or generated/ sitting next to the app). A corrupt table
    file (SyntaxError, half-written) is treated exactly like a missing one.
    """
    try:
        from .generated import fc26_tables as sibling  # noqa: PLC0415

        return sibling
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - corrupt table file must not kill import
        return None
    for name in ("src.generated.fc26_tables", "generated.fc26_tables"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
        except Exception:  # noqa: BLE001
            return None
    return None


_TABLES: Final[Any] = _load_tables()


def _int_map(name: str) -> Mapping[int, str]:
    raw = getattr(_TABLES, name, None) or {}
    try:
        return MappingProxyType({int(k): str(v) for k, v in dict(raw).items()})
    except Exception:  # noqa: BLE001
        return MappingProxyType({})


def _name_map(name: str) -> Mapping[str, int]:
    raw = getattr(_TABLES, name, None) or {}
    try:
        return MappingProxyType({str(k): int(v) for k, v in dict(raw).items()})
    except Exception:  # noqa: BLE001
        return MappingProxyType({})


def _collision_map(name: str) -> Mapping[str, Tuple[int, ...]]:
    raw = getattr(_TABLES, name, None) or {}
    try:
        return MappingProxyType(
            {str(k): tuple(int(i) for i in v) for k, v in dict(raw).items()}
        )
    except Exception:  # noqa: BLE001
        return MappingProxyType({})


def _weighted_map(name: str) -> Mapping[int, WeightedCodes]:
    raw = getattr(_TABLES, name, None) or {}
    try:
        return MappingProxyType(
            {
                int(k): tuple((int(c), int(w)) for c, w in v)
                for k, v in dict(raw).items()
            }
        )
    except Exception:  # noqa: BLE001
        return MappingProxyType({})


def _weighted_seq(name: str) -> WeightedCodes:
    raw = getattr(_TABLES, name, None) or ()
    try:
        return tuple((int(c), int(w)) for c, w in raw)
    except Exception:  # noqa: BLE001
        return ()


#: playerids that own a real (scanned) head model.
FC26_REAL_HEADS: Final[frozenset[int]] = frozenset(
    int(pid) for pid in (getattr(_TABLES, "REAL_HEADS", None) or ())
)
#: playerid -> display name, for the ids in :data:`FC26_REAL_HEADS`.
FC26_HEAD_NAMES: Final[Mapping[int, str]] = _int_map("HEAD_NAMES")

NATION_NAMES: Final[Mapping[int, str]] = _int_map("NATION_NAMES")
NATION_IDS: Final[Mapping[str, int]] = _name_map("NATION_IDS")
NATION_ID_COLLISIONS: Final[Mapping[str, Tuple[int, ...]]] = _collision_map(
    "NATION_ID_COLLISIONS"
)
LEAGUE_NAMES: Final[Mapping[int, str]] = _int_map("LEAGUE_NAMES")
LEAGUE_IDS: Final[Mapping[str, int]] = _name_map("LEAGUE_IDS")
LEAGUE_ID_COLLISIONS: Final[Mapping[str, Tuple[int, ...]]] = _collision_map(
    "LEAGUE_ID_COLLISIONS"
)
TEAM_NAMES: Final[Mapping[int, str]] = _int_map("TEAM_NAMES")
TEAM_IDS: Final[Mapping[str, int]] = _name_map("TEAM_IDS")
TEAM_IDS_LOOSE: Final[Mapping[str, int]] = _name_map("TEAM_IDS_LOOSE")
TEAM_ID_COLLISIONS: Final[Mapping[str, Tuple[int, ...]]] = _collision_map(
    "TEAM_ID_COLLISIONS"
)

#: nationality id -> ((headtypecode, times used), ...) for generic heads only.
GENERIC_HEADTYPES: Final[Mapping[int, WeightedCodes]] = _weighted_map(
    "GENERIC_HEADTYPES"
)
#: Same distribution across every nationality, used when the nation is unknown.
GENERIC_HEADTYPES_ANY: Final[WeightedCodes] = _weighted_seq("GENERIC_HEADTYPES_ANY")


def tables_loaded() -> bool:
    """True when the generated tables imported and carry data."""
    return _TABLES is not None and bool(FC26_REAL_HEADS) and bool(NATION_NAMES)


def source_stats() -> Mapping[str, Any]:
    """Provenance recorded by the generator (source file, sha256, row counts)."""
    raw = getattr(_TABLES, "SOURCES", None) or {}
    try:
        return MappingProxyType(dict(raw))
    except Exception:  # noqa: BLE001
        return MappingProxyType({})


# --------------------------------------------------------------------------
# name normalisation
# --------------------------------------------------------------------------
# NFKD leaves these alone, so they are folded by hand before decomposition.
_TRANSLITERATE: Final[Mapping[int, str]] = str.maketrans(
    {
        "&": " and ",
        "ß": "ss",
        "æ": "ae",
        "ð": "d",
        "ø": "o",
        "þ": "th",
        "đ": "d",
        "ı": "i",
        "ł": "l",
        "œ": "oe",
    }
)
_NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


def normalize_name(value: object) -> str:
    """Fold a nation/league/club name to its lookup key.

    Lowercase, accents stripped, punctuation collapsed to single spaces:
    ``"FC Bayern München"`` and ``"fc bayern munchen"`` land on the same key.
    The generator imports this function so the emitted keys can never drift
    from the ones looked up here.
    """
    if value is None:
        return ""
    try:
        text = str(value).strip().lower()
    except Exception:  # noqa: BLE001
        return ""
    if not text:
        return ""
    text = text.translate(_TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _as_int(value: object) -> Optional[int]:
    """Coerce ``7``/``"7"``/``"7.0"``/``7.9`` to an int, anything else to None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


# Names users and scrapers actually type, mapped to the datahub spelling.
_NATION_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "america": "united states",
        "bosnia": "bosnia and herzegovina",
        "bosnia herzegovina": "bosnia and herzegovina",
        "cape verde": "cabo verde",
        "china": "china pr",
        "czech republic": "czechia",
        "democratic republic of the congo": "congo dr",
        "dr congo": "congo dr",
        "eire": "republic of ireland",
        "holland": "netherlands",
        "ireland": "republic of ireland",
        "ivory coast": "cote d ivoire",
        "korea south": "korea republic",
        "macedonia": "north macedonia",
        "south korea": "korea republic",
        "taiwan": "chinese taipei",
        "turkey": "turkiye",
        "uae": "united arab emirates",
        "united states of america": "united states",
        "usa": "united states",
        "us": "united states",
    }
)

# Common club shorthand. Kept deliberately small: the loose index in the
# generated tables already handles "FC"/"CF"/"AFC" style noise.
_TEAM_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "barca": "fc barcelona",
        "barcelona": "fc barcelona",
        "bayern munich": "fc bayern munchen",
        "bayern": "fc bayern munchen",
        "benfica": "sl benfica",
        "inter milan": "inter",
        "internazionale": "inter",
        "juve": "juventus",
        "man city": "manchester city",
        "man utd": "manchester united",
        "man united": "manchester united",
        "marseille": "olympique de marseille",
        "psg": "paris saint germain",
        "spurs": "tottenham hotspur",
        "wolves": "wolverhampton wanderers",
    }
)


def _lookup(
    name: object,
    index: Mapping[str, int],
    aliases: Mapping[str, str],
    known_ids: Mapping[int, str],
    loose: Optional[Mapping[str, int]] = None,
) -> Optional[int]:
    """Resolve a name (or an id that is already valid) through one index."""
    direct = _as_int(name)
    if direct is not None and direct in known_ids:
        return direct
    key = normalize_name(name)
    if not key:
        return None
    found = index.get(key)
    if found is not None:
        return found
    alias = aliases.get(key)
    if alias is not None:
        found = index.get(alias)
        if found is not None:
            return found
    if loose:
        return loose.get(key)
    return None


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def has_real_head(playerid: object) -> bool:
    """True when this playerid ships with a scanned head model.

    False for unknown ids and whenever the tables are missing, because the
    caller's fallback (a generic head) is always safe while a wrongly claimed
    real head is not.
    """
    pid = _as_int(playerid)
    return pid is not None and pid in FC26_REAL_HEADS


def head_name(playerid: object) -> Optional[str]:
    """Display name recorded for a real-head playerid, else None."""
    pid = _as_int(playerid)
    if pid is None:
        return None
    return FC26_HEAD_NAMES.get(pid)


def nation_id_for(name: object) -> Optional[int]:
    """Nation id for a name (or a valid id), else None — never a guess.

    This is the replacement for ``add_player._NATION_NAME_TO_ID``:
    ``nation_id_for("Nigeria")`` is 133, not Italy's 27.
    """
    return _lookup(name, NATION_IDS, _NATION_ALIASES, NATION_NAMES)


def nation_name(nation_id: object) -> Optional[str]:
    """Canonical nation name for an id, else None."""
    nid = _as_int(nation_id)
    if nid is None:
        return None
    return NATION_NAMES.get(nid)


def league_id_for(name: object) -> Optional[int]:
    """League id for a name (or a valid id), else None.

    Ambiguous names resolve to the league with the most players; see
    :data:`LEAGUE_ID_COLLISIONS` for the full candidate list.
    """
    return _lookup(name, LEAGUE_IDS, {}, LEAGUE_NAMES)


def league_name(league_id: object) -> Optional[str]:
    """League name for an id, else None."""
    lid = _as_int(league_id)
    if lid is None:
        return None
    return LEAGUE_NAMES.get(lid)


def team_id_for(name: object) -> Optional[int]:
    """Club id for a name (or a valid id), else None."""
    return _lookup(name, TEAM_IDS, _TEAM_ALIASES, TEAM_NAMES, TEAM_IDS_LOOSE)


def team_name(team_id: object) -> Optional[str]:
    """Club name for an id, else None."""
    tid = _as_int(team_id)
    if tid is None:
        return None
    return TEAM_NAMES.get(tid)


# --------------------------------------------------------------------------
# roles
# --------------------------------------------------------------------------
#: Base role id -> role name. Hand-transcribed from the in-game role list; this
#: one is not derived from a data file, so it lives here rather than in the
#: generated module.
BASE_ROLE_NAMES: Final[Mapping[int, str]] = MappingProxyType(
    {
        1: "GK Goalkeeper",
        2: "GK Sweeper Keeper",
        3: "RB Fullback",
        4: "RB Falseback",
        5: "RB Wingback",
        6: "RB Attacking Wingback",
        7: "LB Fullback",
        8: "LB Falseback",
        9: "LB Wingback",
        10: "LB Attacking Wingback",
        11: "CB Defender",
        12: "CB Stopper",
        13: "CB Ball-Playing Defender",
        14: "CDM Holding",
        15: "CDM Centre Half",
        16: "CDM Deep-Lying Playmaker",
        17: "CDM Wide Half",
        18: "CM Box-to-Box",
        19: "CM Holding",
        20: "CM Deep-Lying Playmaker",
        21: "CM Playmaker",
        22: "CM Half-Winger",
        23: "RM Winger",
        24: "RM Wide Midfielder",
        25: "RM Wide Playmaker",
        26: "RM Inside Forward",
        27: "LM Winger",
        28: "LM Wide Midfielder",
        29: "LM Wide Playmaker",
        30: "LM Inside Forward",
        31: "CAM Playmaker",
        32: "CAM Shadow Striker",
        33: "CAM Half Winger",
        34: "CAM Classic 10",
        35: "RW Winger",
        36: "RW Inside Forward",
        37: "RW Wide Playmaker",
        38: "LW Winger",
        39: "LW Inside Forward",
        40: "LW Wide Playmaker",
        41: "ST Advanced Forward",
        42: "ST Poacher",
        43: "ST False 9",
        44: "ST Target Forward",
        45: "GK Ball-Playing Keeper",
        46: "RB Inverted Wingback",
        47: "LB Inverted Wingback",
        48: "CB Wide Back",
        49: "CDM Box Crasher",
        50: "ST Roaming Striker",
        51: "RW False Winger",
        52: "LW False Winger",
    }
)

#: ``id + 100`` is the "++" familiarity of the same role.
ROLE_PLUS_PLUS_OFFSET: Final[int] = 100


def _build_role_names() -> Mapping[int, str]:
    out: dict[int, str] = {0: "None"}
    for role_id, label in BASE_ROLE_NAMES.items():
        out[role_id] = label + "+"
        out[role_id + ROLE_PLUS_PLUS_OFFSET] = label + "++"
    return MappingProxyType(out)


#: Every valid role id -> rendered name (0, 1..52 as "+", 101..152 as "++").
ROLE_NAMES: Final[Mapping[int, str]] = _build_role_names()


def role_name(role_id: object) -> Optional[str]:
    """Rendered role name: 0 -> "None", 21 -> "CM Playmaker+", 121 -> "CM Playmaker++".

    Returns None for ids the game cannot store (53..100, >152, negatives), so a
    corrupt value shows up instead of being rendered as a plausible role.
    """
    rid = _as_int(role_id)
    if rid is None:
        return None
    return ROLE_NAMES.get(rid)


# --------------------------------------------------------------------------
# generic heads
# --------------------------------------------------------------------------
def generic_headtypes(nationality: object) -> Tuple[int, ...]:
    """Every ``headtypecode`` observed on generic heads for this nationality.

    Most-used first. Empty tuple when the nation is unknown or data is missing.
    """
    nid = nation_id_for(nationality)
    if nid is None:
        return ()
    return tuple(code for code, _ in GENERIC_HEADTYPES.get(nid, ()))


def _stable_hash(text: str) -> int:
    """Hash that survives interpreter restarts (``hash()`` does not)."""
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _weighted_pick(pool: Sequence[Tuple[int, int]], draw: int) -> int:
    total = sum(weight for _, weight in pool if weight > 0)
    if total <= 0:
        return pool[draw % len(pool)][0]
    offset = draw % total
    running = 0
    for code, weight in pool:
        if weight <= 0:
            continue
        running += weight
        if offset < running:
            return code
    return pool[-1][0]


def pick_generic_headtype(
    nationality: object, seed: Union[int, str, None] = 0
) -> Optional[int]:
    """A plausible ``headtypecode`` for a generic head, chosen deterministically.

    The code is drawn from the ones EA actually ships for that nationality,
    weighted by how many base players use each, so the result is always valid
    in game. Unknown nations fall back to the global distribution; the same
    ``(nationality, seed)`` always yields the same code, across machines and
    interpreter restarts. Returns None only when no data is loaded at all.
    """
    nid = nation_id_for(nationality)
    pool: Sequence[Tuple[int, int]] = GENERIC_HEADTYPES.get(nid, ()) if nid else ()
    if not pool:
        pool = GENERIC_HEADTYPES_ANY
    if not pool:
        return None
    return _weighted_pick(pool, _stable_hash(str(nid) + ":" + str(seed)))
