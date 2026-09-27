"""Authoritative lookup over LE's ``player_presets/base_players.csv``.

This file is the ground truth for every FC 26 base player: 22,348 rows x 149
columns, including the identity and appearance data that decides whether an
added player ends up with a working name and a real face.

Before this module existed the add/import path guessed at that data — it
hardcoded ``hashighqualityhead = 1``, zeroed ``headtypecode``, clamped
``bodytypecode`` to 1..20, and carried a hand-written nation table with
colliding ids. All of it is available here for free.

Key facts the callers depend on:

* A **real face** needs the matching quartet from the player's own row:
  ``headassetid`` (= playerid for 99.6% of rows), ``headclasscode = 0``, plus
  that player's own ``headtypecode`` and ``hashighqualityhead``.
  ``hashighqualityhead`` is NOT an "is real face" flag — it selects between two
  mesh/texture pipelines, and is 0 for 19,237 of 22,348 players (Zidane, Pele
  and van Basten among them).
* A **generic head** is always safe: ``hashighqualityhead = 0``,
  ``headclasscode = 1``, ``headassetid = 0``.
* ``gender`` is 0 = male / 1 = female here, which is the EA encoding. FUT.GG
  uses 1 = male, so never copy FUT.GG's value through.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from typing import Any, Dict, Mapping, Optional

from . import paths

# Columns that describe a face. Copy the whole set or none of it.
FACE_FIELDS = (
    "headassetid",
    "hashighqualityhead",
    "headclasscode",
    "headtypecode",
    "headvariation",
)

# Appearance columns worth carrying over so an imported player is not a
# mannequin. Every field omitted from a CreatePlayer payload is set to its
# lowest possible value by LE, which is what produced the blank-doll results.
APPEARANCE_FIELDS = (
    "skintonecode",
    "skintypecode",
    "skinsurfacepack",
    "skincomplexion",
    "skinmakeup",
    "lipcolor",
    "eyecolorcode",
    "eyebrowcode",
    "eyedetail",
    "hairtypecode",
    "hairstylecode",
    "haircolorcode",
    "facialhairtypecode",
    "facialhaircolorcode",
    "sideburnscode",
    "faceposerpreset",
    "bodytypecode",
    "muscularitycode",
    "height",
    "weight",
    "jerseyfit",
    "jerseystylecode",
    "jerseysleevelengthcode",
    "socklengthcode",
    "shoetypecode",
    "shoedesigncode",
    "shoecolorcode1",
    "shoecolorcode2",
    "gkglovetypecode",
    "tattoohead",
    "tattoofront",
    "tattooback",
    "tattooleftarm",
    "tattoorightarm",
    "tattooleftleg",
    "tattoorightleg",
    "accessorycode1",
    "accessorycode2",
    "accessorycode3",
    "accessorycode4",
    "accessorycolourcode1",
    "accessorycolourcode2",
    "accessorycolourcode3",
    "accessorycolourcode4",
)

# Identity columns. The four *nameid values point into EA's static name
# dictionary; reusing a real player's ids makes a name resolve natively with no
# editedplayernames row at all.
IDENTITY_FIELDS = (
    "firstname",
    "surname",
    "commonname",
    "playerjerseyname",
    "firstnameid",
    "lastnameid",
    "commonnameid",
    "playerjerseynameid",
    "nationality",
    "birthdate",
    "gender",
    "usercaneditname",
)


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001
        return default
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def _index() -> Dict[int, Dict[str, str]]:
    """playerid -> full row. Empty dict when the CSV is missing."""
    try:
        path = paths.base_players_csv_path()
    except Exception:  # noqa: BLE001
        return {}
    if not path or not path.exists():
        return {}
    out: Dict[int, Dict[str, str]] = {}
    try:
        with path.open("r", encoding="utf-8", newline="", errors="replace") as fh:
            for row in csv.DictReader(fh):
                pid = _as_int(row.get("playerid"))
                if pid is not None and pid > 0:
                    out[pid] = row
    except Exception:  # noqa: BLE001
        return {}
    return out


def available() -> bool:
    return bool(_index())


def count() -> int:
    return len(_index())


def get_row(playerid: Any) -> Optional[Dict[str, str]]:
    """Full 149-column row for a base playerid, or None."""
    pid = _as_int(playerid)
    if pid is None:
        return None
    return _index().get(pid)


def face_profile(playerid: Any) -> Dict[str, Any]:
    """Resolve the face for a base playerid.

    Returns a dict with ``real`` plus the exact field values to write. When the
    player is unknown, returns the always-safe generic-head combination rather
    than guessing — a wrong head is a crash risk, a generic head never is.
    """
    row = get_row(playerid)
    if not row:
        return {
            "real": False,
            "reason": "unknown_playerid",
            "headassetid": 0,
            "hashighqualityhead": 0,
            "headclasscode": 1,
            "headtypecode": None,
            "headvariation": 0,
        }

    asset = _as_int(row.get("headassetid"), 0) or 0
    headclass = _as_int(row.get("headclasscode"), 1)
    # headclasscode 0 means the player has a scanned/real head asset.
    real = asset > 0 and headclass == 0
    if not real:
        return {
            "real": False,
            "reason": "generic_in_base_data",
            "headassetid": 0,
            "hashighqualityhead": 0,
            "headclasscode": 1,
            "headtypecode": _as_int(row.get("headtypecode")),
            "headvariation": _as_int(row.get("headvariation"), 0) or 0,
        }
    return {
        "real": True,
        "reason": "base_row",
        "headassetid": asset,
        # Never hardcode this to 1 — it is 0 for the majority of players.
        "hashighqualityhead": _as_int(row.get("hashighqualityhead"), 0) or 0,
        "headclasscode": 0,
        "headtypecode": _as_int(row.get("headtypecode")),
        "headvariation": _as_int(row.get("headvariation"), 0) or 0,
    }


def identity(playerid: Any) -> Dict[str, Any]:
    """Name parts, dictionary name ids, nationality, birthdate and gender."""
    row = get_row(playerid)
    if not row:
        return {}
    out: Dict[str, Any] = {}
    for key in IDENTITY_FIELDS:
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        if key in ("firstname", "surname", "commonname", "playerjerseyname"):
            out[key] = str(raw).strip()
        else:
            val = _as_int(raw)
            if val is not None:
                out[key] = val
    return out


def appearance(playerid: Any) -> Dict[str, int]:
    """Every cosmetic column we know how to carry over."""
    row = get_row(playerid)
    if not row:
        return {}
    out: Dict[str, int] = {}
    for key in APPEARANCE_FIELDS:
        val = _as_int(row.get(key))
        if val is not None:
            out[key] = val
    return out


def full_payload(playerid: Any) -> Dict[str, str]:
    """The complete row as string values, ready to seed a CreatePlayer payload.

    LE replaces every missing field with its lowest possible value, so seeding
    from a real row and overlaying card stats on top is far safer than sending
    a partial payload.
    """
    row = get_row(playerid)
    if not row:
        return {}
    return {k: str(v).strip() for k, v in row.items() if v is not None and str(v).strip() != ""}


def gender_of(playerid: Any, default: int = 0) -> int:
    """EA encoding: 0 = male, 1 = female."""
    row = get_row(playerid)
    if not row:
        return default
    val = _as_int(row.get("gender"), default)
    return 1 if val == 1 else 0


def nationality_of(playerid: Any) -> Optional[int]:
    row = get_row(playerid)
    if not row:
        return None
    return _as_int(row.get("nationality"))


def resolve(playerid: Any) -> Dict[str, Any]:
    """Everything known about a base player, in one call."""
    row = get_row(playerid)
    if not row:
        return {"found": False, "playerid": _as_int(playerid)}
    return {
        "found": True,
        "playerid": _as_int(row.get("playerid")),
        "identity": identity(playerid),
        "face": face_profile(playerid),
        "appearance": appearance(playerid),
    }


def clear_cache() -> None:
    _index.cache_clear()
