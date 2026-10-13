"""Read-only game asset requests. Paths refer to LE's legacy game namespace."""
from __future__ import annotations

import re
from typing import Iterable

DATABASE_ASSET = "data/db/fifa_ng_db.db"
ASSET_CORE_MIN = "2.6.30"
MAX_CHECK_ASSETS = 8
MAX_EXPORT_ASSETS = 2
_SEGMENT = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def legacy_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 240:
        raise ValueError("Enter a game asset path beginning with data/.")
    parts = value.split("/")
    if len(parts) < 3 or parts[0] != "data" or any(
        not _SEGMENT.fullmatch(p) or p.endswith(".") or _DEVICE.match(p) for p in parts
    ):
        raise ValueError("Use a relative data/... game path, without traversal or Windows paths.")
    return value


def asset_paths(values: Iterable[str], *, export: bool = False) -> tuple[str, ...]:
    result = tuple(legacy_path(v) for v in values)
    limit = MAX_EXPORT_ASSETS if export else MAX_CHECK_ASSETS
    if not result or len(result) > limit:
        raise ValueError(f"Choose between 1 and {limit} assets per request.")
    if len({p.casefold() for p in result}) != len(result):
        raise ValueError("The same asset was requested twice.")
    return result


def portrait_paths(playerid: int) -> tuple[str, str]:
    # Official FC26 miniface guides: 180px DDS/BC3, heads and youthheads.
    if isinstance(playerid, bool) or not isinstance(playerid, int) or not 0 < playerid < 1000000:
        raise ValueError("Enter a player ID between 1 and 999999.")
    return tuple(f"data/ui/imgAssets/{kind}/p{playerid}.DDS" for kind in ("heads", "youthheads"))


def portrait_id(path: str) -> int | None:
    match = re.fullmatch(r"data/ui/imgAssets/(?:heads|youthheads)/p([1-9][0-9]{0,5})\.DDS", path, re.I)
    return int(match[1]) if match else None
