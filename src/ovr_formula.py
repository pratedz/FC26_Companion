"""CE-derived position OVR weights (FC25 CT OVR_FORMULA_2) for LE editor.

Pure Python - no CE memory. Used by editor recalculate / Best-At and apply path.
Weights map LE player field names.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

OVR_FORMULA: Dict[str, Dict[str, float]] = {
    "0": {
        "reactions": 0.11,
        "gkdiving": 0.21,
        "gkhandling": 0.21,
        "gkkicking": 0.05,
        "gkreflexes": 0.21,
        "gkpositioning": 0.21
    },
    "2": {
        "acceleration": 0.04,
        "sprintspeed": 0.06,
        "stamina": 0.1,
        "reactions": 0.08,
        "interceptions": 0.12,
        "ballcontrol": 0.08,
        "crossing": 0.12,
        "dribbling": 0.04,
        "shortpassing": 0.1,
        "defensiveawareness": 0.07,
        "standingtackle": 0.08,
        "slidingtackle": 0.11
    },
    "3": {
        "acceleration": 0.05,
        "sprintspeed": 0.07,
        "stamina": 0.08,
        "reactions": 0.08,
        "interceptions": 0.12,
        "ballcontrol": 0.07,
        "crossing": 0.09,
        "headingaccuracy": 0.04,
        "shortpassing": 0.07,
        "defensiveawareness": 0.08,
        "standingtackle": 0.11,
        "slidingtackle": 0.14
    },
    "4": {
        "sprintspeed": 0.02,
        "jumping": 0.03,
        "strength": 0.1,
        "reactions": 0.05,
        "aggression": 0.07,
        "interceptions": 0.13,
        "ballcontrol": 0.04,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "defensiveawareness": 0.14,
        "standingtackle": 0.17,
        "slidingtackle": 0.1
    },
    "5": {
        "sprintspeed": 0.02,
        "jumping": 0.03,
        "strength": 0.1,
        "reactions": 0.05,
        "aggression": 0.07,
        "interceptions": 0.13,
        "ballcontrol": 0.04,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "defensiveawareness": 0.14,
        "standingtackle": 0.17,
        "slidingtackle": 0.1
    },
    "6": {
        "sprintspeed": 0.02,
        "jumping": 0.03,
        "strength": 0.1,
        "reactions": 0.05,
        "aggression": 0.07,
        "interceptions": 0.13,
        "ballcontrol": 0.04,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "defensiveawareness": 0.14,
        "standingtackle": 0.17,
        "slidingtackle": 0.1
    },
    "7": {
        "acceleration": 0.05,
        "sprintspeed": 0.07,
        "stamina": 0.08,
        "reactions": 0.08,
        "interceptions": 0.12,
        "ballcontrol": 0.07,
        "crossing": 0.09,
        "headingaccuracy": 0.04,
        "shortpassing": 0.07,
        "defensiveawareness": 0.08,
        "standingtackle": 0.11,
        "slidingtackle": 0.14
    },
    "8": {
        "acceleration": 0.04,
        "sprintspeed": 0.06,
        "stamina": 0.1,
        "reactions": 0.08,
        "interceptions": 0.12,
        "ballcontrol": 0.08,
        "crossing": 0.12,
        "dribbling": 0.04,
        "shortpassing": 0.1,
        "defensiveawareness": 0.07,
        "standingtackle": 0.08,
        "slidingtackle": 0.11
    },
    "9": {
        "stamina": 0.06,
        "strength": 0.04,
        "reactions": 0.07,
        "aggression": 0.05,
        "interceptions": 0.14,
        "vision": 0.04,
        "ballcontrol": 0.1,
        "longpassing": 0.1,
        "shortpassing": 0.14,
        "defensiveawareness": 0.09,
        "standingtackle": 0.12,
        "slidingtackle": 0.05
    },
    "10": {
        "stamina": 0.06,
        "strength": 0.04,
        "reactions": 0.07,
        "aggression": 0.05,
        "interceptions": 0.14,
        "vision": 0.04,
        "ballcontrol": 0.1,
        "longpassing": 0.1,
        "shortpassing": 0.14,
        "defensiveawareness": 0.09,
        "standingtackle": 0.12,
        "slidingtackle": 0.05
    },
    "11": {
        "stamina": 0.06,
        "strength": 0.04,
        "reactions": 0.07,
        "aggression": 0.05,
        "interceptions": 0.14,
        "vision": 0.04,
        "ballcontrol": 0.1,
        "longpassing": 0.1,
        "shortpassing": 0.14,
        "defensiveawareness": 0.09,
        "standingtackle": 0.12,
        "slidingtackle": 0.05
    },
    "12": {
        "acceleration": 0.07,
        "sprintspeed": 0.06,
        "stamina": 0.05,
        "reactions": 0.07,
        "positioning": 0.08,
        "vision": 0.07,
        "ballcontrol": 0.13,
        "crossing": 0.1,
        "dribbling": 0.15,
        "finishing": 0.06,
        "longpassing": 0.05,
        "shortpassing": 0.11
    },
    "13": {
        "stamina": 0.06,
        "reactions": 0.08,
        "interceptions": 0.05,
        "positioning": 0.06,
        "vision": 0.13,
        "ballcontrol": 0.14,
        "dribbling": 0.07,
        "finishing": 0.02,
        "longpassing": 0.13,
        "shortpassing": 0.17,
        "longshots": 0.04,
        "standingtackle": 0.05
    },
    "14": {
        "stamina": 0.06,
        "reactions": 0.08,
        "interceptions": 0.05,
        "positioning": 0.06,
        "vision": 0.13,
        "ballcontrol": 0.14,
        "dribbling": 0.07,
        "finishing": 0.02,
        "longpassing": 0.13,
        "shortpassing": 0.17,
        "longshots": 0.04,
        "standingtackle": 0.05
    },
    "15": {
        "stamina": 0.06,
        "reactions": 0.08,
        "interceptions": 0.05,
        "positioning": 0.06,
        "vision": 0.13,
        "ballcontrol": 0.14,
        "dribbling": 0.07,
        "finishing": 0.02,
        "longpassing": 0.13,
        "shortpassing": 0.17,
        "longshots": 0.04,
        "standingtackle": 0.05
    },
    "16": {
        "acceleration": 0.07,
        "sprintspeed": 0.06,
        "stamina": 0.05,
        "reactions": 0.07,
        "positioning": 0.08,
        "vision": 0.07,
        "ballcontrol": 0.13,
        "crossing": 0.1,
        "dribbling": 0.15,
        "finishing": 0.06,
        "longpassing": 0.05,
        "shortpassing": 0.11
    },
    "17": {
        "acceleration": 0.04,
        "sprintspeed": 0.03,
        "agility": 0.03,
        "reactions": 0.07,
        "positioning": 0.09,
        "vision": 0.14,
        "ballcontrol": 0.15,
        "dribbling": 0.13,
        "finishing": 0.07,
        "longpassing": 0.04,
        "shortpassing": 0.16,
        "longshots": 0.05
    },
    "18": {
        "acceleration": 0.04,
        "sprintspeed": 0.03,
        "agility": 0.03,
        "reactions": 0.07,
        "positioning": 0.09,
        "vision": 0.14,
        "ballcontrol": 0.15,
        "dribbling": 0.13,
        "finishing": 0.07,
        "longpassing": 0.04,
        "shortpassing": 0.16,
        "longshots": 0.05
    },
    "19": {
        "acceleration": 0.04,
        "sprintspeed": 0.03,
        "agility": 0.03,
        "reactions": 0.07,
        "positioning": 0.09,
        "vision": 0.14,
        "ballcontrol": 0.15,
        "dribbling": 0.13,
        "finishing": 0.07,
        "longpassing": 0.04,
        "shortpassing": 0.16,
        "longshots": 0.05
    },
    "20": {
        "acceleration": 0.05,
        "sprintspeed": 0.05,
        "reactions": 0.09,
        "positioning": 0.13,
        "vision": 0.08,
        "ballcontrol": 0.15,
        "dribbling": 0.14,
        "finishing": 0.11,
        "headingaccuracy": 0.02,
        "shortpassing": 0.09,
        "shotpower": 0.05,
        "longshots": 0.04
    },
    "21": {
        "acceleration": 0.05,
        "sprintspeed": 0.05,
        "reactions": 0.09,
        "positioning": 0.13,
        "vision": 0.08,
        "ballcontrol": 0.15,
        "dribbling": 0.14,
        "finishing": 0.11,
        "headingaccuracy": 0.02,
        "shortpassing": 0.09,
        "shotpower": 0.05,
        "longshots": 0.04
    },
    "22": {
        "acceleration": 0.05,
        "sprintspeed": 0.05,
        "reactions": 0.09,
        "positioning": 0.13,
        "vision": 0.08,
        "ballcontrol": 0.15,
        "dribbling": 0.14,
        "finishing": 0.11,
        "headingaccuracy": 0.02,
        "shortpassing": 0.09,
        "shotpower": 0.05,
        "longshots": 0.04
    },
    "23": {
        "acceleration": 0.07,
        "sprintspeed": 0.06,
        "agility": 0.03,
        "reactions": 0.07,
        "positioning": 0.09,
        "vision": 0.06,
        "ballcontrol": 0.14,
        "crossing": 0.09,
        "dribbling": 0.16,
        "finishing": 0.1,
        "shortpassing": 0.09,
        "longshots": 0.04
    },
    "24": {
        "acceleration": 0.04,
        "sprintspeed": 0.05,
        "strength": 0.05,
        "reactions": 0.08,
        "positioning": 0.13,
        "ballcontrol": 0.1,
        "dribbling": 0.07,
        "finishing": 0.18,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "shotpower": 0.1,
        "longshots": 0.03,
        "volleys": 0.02
    },
    "25": {
        "acceleration": 0.04,
        "sprintspeed": 0.05,
        "strength": 0.05,
        "reactions": 0.08,
        "positioning": 0.13,
        "ballcontrol": 0.1,
        "dribbling": 0.07,
        "finishing": 0.18,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "shotpower": 0.1,
        "longshots": 0.03,
        "volleys": 0.02
    },
    "26": {
        "acceleration": 0.04,
        "sprintspeed": 0.05,
        "strength": 0.05,
        "reactions": 0.08,
        "positioning": 0.13,
        "ballcontrol": 0.1,
        "dribbling": 0.07,
        "finishing": 0.18,
        "headingaccuracy": 0.1,
        "shortpassing": 0.05,
        "shotpower": 0.1,
        "longshots": 0.03,
        "volleys": 0.02
    },
    "27": {
        "acceleration": 0.07,
        "sprintspeed": 0.06,
        "agility": 0.03,
        "reactions": 0.07,
        "positioning": 0.09,
        "vision": 0.06,
        "ballcontrol": 0.14,
        "crossing": 0.09,
        "dribbling": 0.16,
        "finishing": 0.1,
        "shortpassing": 0.09,
        "longshots": 0.04
    }
}

def _attr_int(card: Mapping[str, Any], field: str) -> Optional[int]:
    v = card.get(field)
    if v in (None, ""):
        return None
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None


def calculate_position_ovr(card: Mapping[str, Any], pos_id: int) -> Optional[int]:
    """Weighted OVR for a position (0-27). None if formula missing or attrs incomplete."""
    weights = OVR_FORMULA.get(str(int(pos_id)))
    if not weights:
        return None
    total = 0.0
    for field, w in weights.items():
        val = _attr_int(card, field)
        if val is None:
            return None
        total += val * float(w)
    ovr = int(round(total))
    return max(1, min(99, ovr))


def calculate_all_position_ovrs(card: Mapping[str, Any]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for key in OVR_FORMULA:
        pid = int(key)
        if pid == 1:
            continue
        o = calculate_position_ovr(card, pid)
        if o is not None:
            out[pid] = o
    return out


def preferred_position_ovr(
    card: Mapping[str, Any],
    *,
    preferred_pos: Optional[int] = None,
    modifier: int = 0,
) -> Optional[int]:
    """OVR for preferred position plus optional modifier, clamped 1-99."""
    if preferred_pos is None:
        preferred_pos = _attr_int(card, "preferredposition1")
    if preferred_pos is None:
        return None
    base = calculate_position_ovr(card, int(preferred_pos))
    if base is None:
        return None
    try:
        mod = int(modifier if modifier is not None else (_attr_int(card, "modifier") or 0))
    except (TypeError, ValueError):
        mod = 0
    return max(1, min(99, base + mod))


def best_at_positions(
    card: Mapping[str, Any],
    *,
    top_n: int = 3,
    pos_names: Optional[Mapping[int, str]] = None,
) -> List[Dict[str, Any]]:
    """Top-N positions by calculated OVR (CE Best At)."""
    if pos_names is None:
        try:
            from .player_schema import POS_CODE_TO_NAME

            pos_names = POS_CODE_TO_NAME
        except Exception:
            pos_names = {}
    scored = calculate_all_position_ovrs(card)
    top = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[: max(1, int(top_n))]
    return [
        {"pos_id": pid, "name": pos_names.get(pid, str(pid)), "ovr": ovr}
        for pid, ovr in top
    ]


def apply_calculated_ovr_to_card(
    card: Mapping[str, Any],
    *,
    preferred_pos: Optional[int] = None,
    use_modifier: bool = True,
) -> Dict[str, Any]:
    """Shallow copy with overallrating set from formula when possible."""
    out = dict(card)
    mod = 0
    if use_modifier:
        mod = _attr_int(out, "modifier") or 0
    ovr = preferred_position_ovr(out, preferred_pos=preferred_pos, modifier=mod)
    if ovr is not None:
        out["overallrating"] = ovr
    return out


def format_best_at_line(card: Mapping[str, Any], top_n: int = 3) -> str:
    rows = best_at_positions(card, top_n=top_n)
    if not rows:
        return "Best At: n/a"
    parts = [f"{r['name']} {r['ovr']}" for r in rows]
    return "Best At: " + ", ".join(parts)
