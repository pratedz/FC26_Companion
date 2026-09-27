"""Map human / FUT-style stat names to Live Editor players-table field names."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

# Canonical LE field names for player attributes (players table).
ATTR_FIELDS: Tuple[str, ...] = (
    # GK
    "gkdiving",
    "gkhandling",
    "gkkicking",
    "gkpositioning",
    "gkreflexes",
    # Attack
    "crossing",
    "finishing",
    "headingaccuracy",
    "shortpassing",
    "volleys",
    # Defending
    "defensiveawareness",
    "standingtackle",
    "slidingtackle",
    # Skill
    "dribbling",
    "curve",
    "freekickaccuracy",
    "longpassing",
    "ballcontrol",
    # Power
    "shotpower",
    "jumping",
    "stamina",
    "strength",
    "longshots",
    # Movement
    "acceleration",
    "sprintspeed",
    "agility",
    "reactions",
    "balance",
    # Mentality
    "aggression",
    "composure",
    "interceptions",
    "positioning",
    "vision",
    "penalties",
)

# Hard players-table bounds, measured over all 22,348 rows of
# player_presets/base_players.csv. skillmoves is 0-indexed (0 = 1★ … 4 = 5★)
# while weakfootabilitytypecode is 1-indexed (1 = 1★ … 5 = 5★) — they are NOT
# the same range. Source dumps (SoFIFA "skill_moves", FUT.GG "skillMoves",
# FutBin "Skills") publish skill moves as a STAR COUNT, so a raw 5 arriving here
# is a 5★ player, not a legal column value. Clamping stops the illegal write;
# use player_schema.stars_to_skillmoves() at the import boundary to convert.
_FIELD_BOUNDS: Dict[str, Tuple[int, int]] = {
    "skillmoves": (0, 4),
    "weakfootabilitytypecode": (1, 5),
}

META_FIELDS: Tuple[str, ...] = (
    "overallrating",
    "potential",
    "skillmoves",
    "weakfootabilitytypecode",
    "modifier",
    "preferredposition1",
    "preferredposition2",
    "preferredposition3",
    "preferredposition4",
    "playerid",
    "name",
)

# Aliases (lowercase, no spaces/underscores) -> LE field name.
_ALIASES: Dict[str, str] = {
    # exact LE
    **{f: f for f in ATTR_FIELDS},
    **{f: f for f in META_FIELDS},
    # common display names
    "acceleration": "acceleration",
    "sprintspeed": "sprintspeed",
    "sprint_speed": "sprintspeed",
    "agility": "agility",
    "balance": "balance",
    "jumping": "jumping",
    "stamina": "stamina",
    "strength": "strength",
    "reactions": "reactions",
    "aggression": "aggression",
    "composure": "composure",
    "interceptions": "interceptions",
    "positioning": "positioning",
    "vision": "vision",
    "ballcontrol": "ballcontrol",
    "ball_control": "ballcontrol",
    "crossing": "crossing",
    "dribbling": "dribbling",
    "finishing": "finishing",
    "freekickaccuracy": "freekickaccuracy",
    "free_kick_accuracy": "freekickaccuracy",
    "fkaccuracy": "freekickaccuracy",
    "headingaccuracy": "headingaccuracy",
    "heading": "headingaccuracy",
    "longpassing": "longpassing",
    "long_passing": "longpassing",
    "shortpassing": "shortpassing",
    "short_passing": "shortpassing",
    "defensiveawareness": "defensiveawareness",
    "def_awareness": "defensiveawareness",
    "marking": "defensiveawareness",
    "shotpower": "shotpower",
    "shot_power": "shotpower",
    "longshots": "longshots",
    "long_shots": "longshots",
    "standingtackle": "standingtackle",
    "standing_tackle": "standingtackle",
    "slidingtackle": "slidingtackle",
    "sliding_tackle": "slidingtackle",
    "volleys": "volleys",
    "curve": "curve",
    "penalties": "penalties",
    "gkdiving": "gkdiving",
    "gkhandling": "gkhandling",
    "gkkicking": "gkkicking",
    "gkreflexes": "gkreflexes",
    "gkpositioning": "gkpositioning",
    # composite / face-card style
    "pace": "acceleration",  # primary PAC component; sprintspeed handled separately
    "pac": "acceleration",
    "shooting": "finishing",
    "sho": "finishing",
    "passing": "shortpassing",
    "pas": "shortpassing",
    "dri": "dribbling",
    "defending": "defensiveawareness",
    "def": "defensiveawareness",
    "physical": "strength",
    "phy": "strength",
    # SoFIFA stefanoleone-style column prefixes (players_18..22, etc.)
    "movement_acceleration": "acceleration",
    "movement_sprint_speed": "sprintspeed",
    "movement_agility": "agility",
    "movement_reactions": "reactions",
    "movement_balance": "balance",
    "attacking_crossing": "crossing",
    "attacking_finishing": "finishing",
    "attacking_heading_accuracy": "headingaccuracy",
    "attacking_short_passing": "shortpassing",
    "attacking_volleys": "volleys",
    "skill_dribbling": "dribbling",
    "skill_curve": "curve",
    "skill_fk_accuracy": "freekickaccuracy",
    "skill_long_passing": "longpassing",
    "skill_ball_control": "ballcontrol",
    "power_shot_power": "shotpower",
    "power_jumping": "jumping",
    "power_stamina": "stamina",
    "power_strength": "strength",
    "power_long_shots": "longshots",
    "mentality_aggression": "aggression",
    "mentality_interceptions": "interceptions",
    "mentality_positioning": "positioning",
    "mentality_vision": "vision",
    "mentality_penalties": "penalties",
    "mentality_composure": "composure",
    "defending_marking": "defensiveawareness",
    "defending_standing_tackle": "standingtackle",
    "defending_sliding_tackle": "slidingtackle",
    "goalkeeping_diving": "gkdiving",
    "goalkeeping_handling": "gkhandling",
    "goalkeeping_kicking": "gkkicking",
    "goalkeeping_positioning": "gkpositioning",
    "goalkeeping_reflexes": "gkreflexes",
    "gk_diving": "gkdiving",
    "gk_handling": "gkhandling",
    "gk_kicking": "gkkicking",
    "gk_reflexes": "gkreflexes",
    "gk_positioning": "gkpositioning",
    "physic": "strength",
    "short_name": "name",
    "long_name": "name",
    "player_positions": "preferredposition1",
    "weak_foot": "weakfootabilitytypecode",
    "skill_moves": "skillmoves",
    # FC24/25 style (some dumps use short truncated headers)
    "acceleration": "acceleration",
    "sprint": "sprintspeed",
    "sprint speed": "sprintspeed",
    "pacetotal": "acceleration",
    "shootingtotal": "finishing",
    "passingtotal": "shortpassing",
    "dribblingtotal": "dribbling",
    "defendingtotal": "defensiveawareness",
    "physicalitytotal": "strength",
    "player": "name",
    "overall_score": "overallrating",
    "overallscore": "overallrating",
    "potential_score": "potential",
    "potentialscore": "potential",
    "player_id": "playerid",
    "best_overall": "overallrating",
    "best_position": "preferredposition1",
    "fk_accuracy": "freekickaccuracy",
    "heading_accuracy": "headingaccuracy",
    "short_passing": "shortpassing",
    "long_passing": "longpassing",
    "ball_control": "ballcontrol",
    "shot_power": "shotpower",
    "long_shots": "longshots",
    "standing_tackle": "standingtackle",
    "sliding_tackle": "slidingtackle",
    "preferred_foot": "preferredfoot",
    "sprint_speed": "sprintspeed",
    # truncated FC24 columns from MarceloBuch dump
    "shot": "shotpower",
    "long": "longshots",
    "free": "freekickaccuracy",
    "ball": "ballcontrol",
    "heading": "headingaccuracy",
    "standing": "standingtackle",
    "sliding": "slidingtackle",
    "physicality": "strength",
    # meta aliases
    "ovr": "overallrating",
    "overall": "overallrating",
    "rating": "overallrating",
    "overallrating": "overallrating",
    "pot": "potential",
    "potential": "potential",
    "sm": "skillmoves",
    "skillmoves": "skillmoves",
    "skill_moves": "skillmoves",
    "wf": "weakfootabilitytypecode",
    "weakfoot": "weakfootabilitytypecode",
    "weak_foot": "weakfootabilitytypecode",
    "weakfootabilitytypecode": "weakfootabilitytypecode",
    "pos": "preferredposition1",
    "position": "preferredposition1",
    "preferredposition1": "preferredposition1",
    "modifier": "modifier",
}


def _normalize_key(name: str) -> str:
    s = name.strip().lower()
    for ch in (" ", "-", "."):
        s = s.replace(ch, "_")
    return s


def _parse_stat_int(val: object) -> Optional[int]:
    """Parse ints; SoFIFA chemistry strings like '72+2' → 72. Full playerids preserved."""
    if val is None or val == "":
        return None
    s = str(val).strip()
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return None
    # chemistry / boost suffixes on short attrs only
    m = re.match(r"^(\d{1,3})([+-]\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    m = re.match(r"^(-?\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def to_le_field(name: str) -> Optional[str]:
    """Return LE players-table field name for a common/alias name, or None."""
    if not name:
        return None
    key = _normalize_key(name)
    # also try without underscores
    compact = key.replace("_", "")
    if key in _ALIASES:
        return _ALIASES[key]
    if compact in _ALIASES:
        return _ALIASES[compact]
    # title-case display already lowercased
    return None


def map_card_keys(card: Dict[str, object]) -> Dict[str, object]:
    """
    Return a new dict with keys remapped to LE field names where possible.
    Unknown keys are kept as-is (lowercased).
    """
    out: Dict[str, object] = {}
    for k, v in card.items():
        le = to_le_field(str(k))
        out[le if le else str(k).strip().lower()] = v
    return out


# Face-stat expansion for older FutHead dumps (PAC/SHO/PAS/DRI/DEF/PHY only).
_FACE_EXPAND: Dict[str, Tuple[str, ...]] = {
    "pace": (
        "acceleration",
        "sprintspeed",
    ),
    "pac": (
        "acceleration",
        "sprintspeed",
    ),
    "shooting": (
        "finishing",
        "shotpower",
        "longshots",
        "volleys",
        "penalties",
        "positioning",
    ),
    "sho": (
        "finishing",
        "shotpower",
        "longshots",
        "volleys",
        "penalties",
        "positioning",
    ),
    "passing": (
        "vision",
        "crossing",
        "freekickaccuracy",
        "shortpassing",
        "longpassing",
        "curve",
    ),
    "pas": (
        "vision",
        "crossing",
        "freekickaccuracy",
        "shortpassing",
        "longpassing",
        "curve",
    ),
    "dribbling": (
        "agility",
        "balance",
        "reactions",
        "ballcontrol",
        "dribbling",
        "composure",
    ),
    "dri": (
        "agility",
        "balance",
        "reactions",
        "ballcontrol",
        "dribbling",
        "composure",
    ),
    "defending": (
        "interceptions",
        "headingaccuracy",
        "defensiveawareness",
        "standingtackle",
        "slidingtackle",
    ),
    "def": (
        "interceptions",
        "headingaccuracy",
        "defensiveawareness",
        "standingtackle",
        "slidingtackle",
    ),
    "physical": (
        "jumping",
        "stamina",
        "strength",
        "aggression",
    ),
    "physicality": (
        "jumping",
        "stamina",
        "strength",
        "aggression",
    ),
    "phy": (
        "jumping",
        "stamina",
        "strength",
        "aggression",
    ),
    "phyiscality": (  # kafagy typo
        "jumping",
        "stamina",
        "strength",
        "aggression",
    ),
}


def extract_attr_updates(card: Dict[str, object]) -> List[Tuple[str, int]]:
    """
    From a unified card dict, produce (le_field, int_value) pairs for attributes
    and selected meta fields that should be written to the players table.

    Face-only cards (PACE/SHOOTING/...) expand into LE sub-attributes when
    detailed keys are missing — needed for FIFA 18–20 FutHead dumps.
    """
    # Work from original keys so we still see "pace" before alias collapse.
    raw_lower = {str(k).strip().lower(): v for k, v in card.items()}
    mapped = map_card_keys(card)
    values: Dict[str, int] = {}

    write_fields = list(ATTR_FIELDS) + [
        "overallrating",
        "potential",
        "skillmoves",
        "weakfootabilitytypecode",
        "preferredposition1",
    ]
    for field in write_fields:
        if field not in mapped or mapped[field] in (None, ""):
            continue
        iv = _parse_stat_int(mapped[field])
        if iv is None:
            continue
        if field in ATTR_FIELDS or field in ("overallrating", "potential"):
            values[field] = max(1, min(99, iv))
        elif field in _FIELD_BOUNDS:
            lo, hi = _FIELD_BOUNDS[field]
            values[field] = max(lo, min(hi, iv))
        else:
            values[field] = iv

    # Expand face stats into missing sub-attrs only
    for face_key, targets in _FACE_EXPAND.items():
        if face_key not in raw_lower or raw_lower[face_key] in (None, ""):
            continue
        fv = _parse_stat_int(raw_lower[face_key])
        if fv is None:
            continue
        fv = max(1, min(99, fv))
        for t in targets:
            values.setdefault(t, fv)

    # Preferred position codes like ST/CM if still string
    pos = mapped.get("preferredposition1")
    if isinstance(pos, str) and pos and not str(pos).isdigit():
        pos_map = {
            "GK": 0, "CB": 5, "LB": 7, "RB": 3, "LWB": 8, "RWB": 2,
            "CDM": 10, "CM": 14, "CAM": 18, "LM": 16, "RM": 12,
            "LW": 27, "RW": 23, "CF": 21, "ST": 25, "LF": 22, "RF": 20,
        }
        code = pos_map.get(pos.upper().split(",")[0].split("/")[0].strip())
        if code is not None:
            values["preferredposition1"] = code

    return [(f, values[f]) for f in write_fields if f in values]


def known_fields() -> Iterable[str]:
    return list(ATTR_FIELDS) + list(META_FIELDS)
