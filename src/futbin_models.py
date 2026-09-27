"""
Futbin player data client for LE Profile Executor.

Live Futbin (www.futbin.com) is protected by Cloudflare Managed Challenge.
As of 2026-07, plain requests / httpx / cloudscraper / curl_cffi (all common
browser impersonations) receive HTTP 403 "Just a moment..." for HTML and
historical price APIs.

What works without a browser session:
  - Player face CDN images (no JSON):
      https://cdn.futbin.com/content/fifa{YY}/img/players/{resourceId}.png
  - Wayback Machine snapshots of older player / list pages (when available)
  - Offline import of user-saved HTML or a JSON dump matching FUTBIN_JSON_SCHEMA

What can work with user help:
  - Browser cookie paste (cf_clearance + session cookies) into
    FutbinClient(cookies=...) or load_cookies_from_file()

Primary path for LE integration: parse_player_html() / import_json() /
fetch_player() (live attempt then fall back) → to_le_card_row().
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence, Union
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants / field maps
# ---------------------------------------------------------------------------

FUTBIN_HOST = "https://www.futbin.com"
CDN_HOST = "https://cdn.futbin.com"

# FIFA / FC preferredposition* integer codes (Live Editor players table)
POSITION_TO_CODE: dict[str, int] = {
    "GK": 0,
    "SW": 1,
    "RWB": 2,
    "RB": 3,
    "RCB": 4,
    "CB": 5,
    "LCB": 6,
    "LB": 7,
    "LWB": 8,
    "RDM": 9,
    "CDM": 10,
    "LDM": 11,
    "RM": 12,
    "RCM": 13,
    "CM": 14,
    "LCM": 15,
    "LM": 16,
    "RAM": 17,
    "CAM": 18,
    "LAM": 19,
    "RF": 20,
    "CF": 21,
    "LF": 22,
    "RW": 23,
    "RS": 24,
    "ST": 25,
    "LS": 26,
    "LW": 27,
}
CODE_TO_POSITION: dict[int, str] = {v: k for k, v in POSITION_TO_CODE.items()}

# Human / Futbin label → LE cards.csv column (detailed attrs)
# Values from player_stats JSON ids, tooltip names, and info_content table.
FUTBIN_STAT_TO_LE: dict[str, str] = {
    # Pace
    "acceleration": "acceleration",
    "sprintspeed": "sprintspeed",
    "sprint speed": "sprintspeed",
    # Shooting
    "positioning": "positioning",
    "finishing": "finishing",
    "shotpower": "shotpower",
    "shot power": "shotpower",
    "longshotsaccuracy": "longshots",
    "long shots": "longshots",
    "longshots": "longshots",
    "volleys": "volleys",
    "penalties": "penalties",
    # Passing
    "vision": "vision",
    "crossing": "crossing",
    "freekickaccuracy": "freekickaccuracy",
    "fk. accuracy": "freekickaccuracy",
    "fk accuracy": "freekickaccuracy",
    "shortpassing": "shortpassing",
    "short passing": "shortpassing",
    "longpassing": "longpassing",
    "long passing": "longpassing",
    "curve": "curve",
    # Dribbling
    "agility": "agility",
    "balance": "balance",
    "reactions": "reactions",
    "ballcontrol": "ballcontrol",
    "ball control": "ballcontrol",
    "dribbling": "dribbling",  # face attr (not the main PAC/DRI composite)
    "dribblingp": "dribbling",
    "composure": "composure",
    # Defending
    "interceptions": "interceptions",
    "headingaccuracy": "headingaccuracy",
    "heading accuracy": "headingaccuracy",
    "heading": "headingaccuracy",
    "marking": "defensiveawareness",
    "defensive awareness": "defensiveawareness",
    "defensiveawareness": "defensiveawareness",
    "standingtackle": "standingtackle",
    "standing tackle": "standingtackle",
    "slidingtackle": "slidingtackle",
    "sliding tackle": "slidingtackle",
    # Physical
    "jumping": "jumping",
    "stamina": "stamina",
    "strength": "strength",
    "aggression": "aggression",
    # GK (main composites + common labels)
    "gkdiving": "gkdiving",
    "diving": "gkdiving",
    "gkhandling": "gkhandling",
    "handling": "gkhandling",
    "gkkicking": "gkkicking",
    "kicking": "gkkicking",
    "gkreflexes": "gkreflexes",
    "reflexes": "gkreflexes",
    "gkpositioning": "gkpositioning",
    # note: GK "positioning" collides with outfield; handled in parser via context
}

# Main face composites (not always written to cards.csv detailed columns)
COMPOSITE_STATS = {
    "pace",
    "shooting",
    "passing",
    "dribbling",  # composite when type=main
    "defending",
    "physicality",
    "physical",
    "heading",  # physicality main id on some pages
}

# LE cards.csv headers (v26.3.5)
LE_CARD_HEADERS: tuple[str, ...] = (
    "uid",
    "name",
    "revision",
    "origin",
    "playerid",
    "overallrating",
    "preferredposition1",
    "preferredposition2",
    "preferredposition3",
    "preferredposition4",
    "trait1",
    "trait2",
    "icontrait1",
    "icontrait2",
    "role1",
    "role2",
    "role3",
    "role4",
    "role5",
    "skillmoves",
    "weakfootabilitytypecode",
    "acceleration",
    "sprintspeed",
    "agility",
    "balance",
    "jumping",
    "stamina",
    "strength",
    "reactions",
    "aggression",
    "composure",
    "interceptions",
    "positioning",
    "vision",
    "ballcontrol",
    "crossing",
    "dribbling",
    "finishing",
    "freekickaccuracy",
    "headingaccuracy",
    "longpassing",
    "shortpassing",
    "defensiveawareness",
    "shotpower",
    "longshots",
    "standingtackle",
    "slidingtackle",
    "volleys",
    "curve",
    "penalties",
    "gkdiving",
    "gkhandling",
    "gkkicking",
    "gkreflexes",
    "gkpositioning",
)

# Schema for offline JSON dumps (importer accepts this or loose aliases)
FUTBIN_JSON_SCHEMA: dict[str, Any] = {
    "playerid": "int — EA resource / base id (maps to LE playerid)",
    "futbin_id": "int — Futbin card page id (optional)",
    "name": "str",
    "fullname": "str optional",
    "rating": "int overall",
    "position": "str e.g. ST/CAM",
    "positions": "list[str] optional secondary",
    "revision": "str card version",
    "origin": "str optional",
    "club": "str optional",
    "nation": "str optional",
    "league": "str optional",
    "skills": "int 1-5 skill moves",
    "weak_foot": "int 1-5",
    "foot": "str Left/Right",
    "height_cm": "int optional",
    "weight_kg": "int optional",
    "att_wr": "str High/Med/Low",
    "def_wr": "str High/Med/Low",
    "year": "int 19-26",
    "stats": {
        "acceleration": 0,
        "sprintspeed": 0,
        "…all LE detailed attrs…": 0,
    },
    "composites": {
        "pace": 0,
        "shooting": 0,
        "passing": 0,
        "dribbling": 0,
        "defending": 0,
        "physicality": 0,
    },
    "source_url": "str optional",
}


PathLike = Union[str, Path]


@dataclass
class FutbinPlayer:
    """Normalized Futbin player card payload."""

    name: str = ""
    fullname: str = ""
    rating: Optional[int] = None
    position: str = ""
    positions: list[str] = field(default_factory=list)
    revision: str = ""
    origin: str = ""
    club: str = ""
    nation: str = ""
    league: str = ""
    skills: Optional[int] = None
    weak_foot: Optional[int] = None
    foot: str = ""
    height_cm: Optional[int] = None
    weight_kg: Optional[int] = None
    att_wr: str = ""
    def_wr: str = ""
    year: Optional[int] = None
    playerid: Optional[int] = None  # EA resource id
    futbin_id: Optional[int] = None
    club_id: Optional[int] = None
    league_id: Optional[int] = None
    stats: dict[str, int] = field(default_factory=dict)  # LE column names
    composites: dict[str, int] = field(default_factory=dict)
    raw_info: dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    source: str = ""  # live | html | json | archive | cookies

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FutbinError(RuntimeError):
    """Base client error."""


class FutbinBlockedError(FutbinError):
    """Cloudflare or other anti-bot block."""


class FutbinParseError(FutbinError):
    """HTML/JSON could not be parsed into a player."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _as_int(value: Any) -> Optional[int]:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    s = str(value).strip().replace(",", "")
    if not s or s.upper() in {"N/A", "NA", "N\\A", "-", "—"}:
        return None
    m = re.search(r"-?\d+", s)
    return int(m.group(0)) if m else None


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def position_to_code(pos: str) -> int:
    p = (pos or "").strip().upper()
    if not p:
        return -1
    if p.isdigit():
        return int(p)
    return POSITION_TO_CODE.get(p, -1)


def year_to_cdn_folder(year: int | str) -> str:
    """Map game year (26, '26', 2026) to CDN folder name fifa26."""
    y = int(str(year).strip()[-2:])
    return f"fifa{y:02d}"


def player_image_url(playerid: int, year: int | str = 26) -> str:
    """CDN face URL — works without Cloudflare cookies."""
    return f"{CDN_HOST}/content/{year_to_cdn_folder(year)}/img/players/{int(playerid)}.png"


def parse_futbin_url(url: str) -> dict[str, Any]:
    """
    Parse URLs like:
      https://www.futbin.com/26/player/18979/pele
      /26/player/18979/pele
    """
    path = urlparse(url).path if "://" in url else url
    m = re.search(r"/(\d{2})/player/(\d+)(?:/([^/?#]+))?", path)
    if not m:
        return {}
    return {
        "year": int(m.group(1)),
        "futbin_id": int(m.group(2)),
        "slug": m.group(3) or "",
    }


def _is_cloudflare_block(status: int, text: str) -> bool:
    if status in (403, 503, 429):
        low = (text or "").lower()
        if any(
            x in low
            for x in (
                "just a moment",
                "cloudflare",
                "cf-browser-verification",
                "challenge-platform",
                "attention required",
                "cf-ray",
            )
        ):
            return True
    low = (text or "").lower()
    return "just a moment" in low and "cloudflare" in low


def map_stat_name(name: str, *, is_gk_main: bool = False) -> Optional[str]:
    """Map a Futbin stat label/id to an LE column, or None if composite/unknown."""
    k = _norm_key(name)
    if k in COMPOSITE_STATS and k != "dribbling":
        return None
    if is_gk_main and k in {
        "pace",
        "shooting",
        "passing",
        "dribbling",
        "defending",
        "physical",
        "physicality",
    }:
        # main composites already mapped via gk_* names in JSON when gk_stat_name set
        return None
    return FUTBIN_STAT_TO_LE.get(k)


# ---------------------------------------------------------------------------
# HTML parser (classic Futbin player page, FC 19–25 style; resilient to minor drift)
# ---------------------------------------------------------------------------

