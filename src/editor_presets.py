"""Editor form presets (max 99, pace monster, etc.)."""

from __future__ import annotations

from typing import Any, Dict, List

from . import field_map


def preset_ids() -> List[str]:
    return [
        "max_99",
        "pace_monster",
        "cb_wall",
        "playmaker",
        "finisher",
        "balanced_85",
    ]


def preset_label(pid: str) -> str:
    return {
        "max_99": "Max 99 (all attrs)",
        "pace_monster": "Pace monster",
        "cb_wall": "CB wall",
        "playmaker": "Playmaker CAM",
        "finisher": "Finisher ST",
        "balanced_85": "Balanced 85",
    }.get(pid, pid)


def _sm(stars: int) -> int:
    """Presets are authored in stars; players.skillmoves is 0-indexed (0 = 1 star).

    Assigning the star count directly made every preset one skill-move star too
    generous — "playmaker" was writing 5 stars, not the 4 it advertises.
    weakfootabilitytypecode is genuinely 1-5 and needs no conversion.
    """
    try:
        from .player_schema import stars_to_skillmoves

        return stars_to_skillmoves(stars)
    except Exception:  # noqa: BLE001
        return max(0, min(4, int(stars) - 1))


def apply_preset(pid: str) -> Dict[str, Any]:
    """Return card-like dict with fields set for editor form."""
    card: Dict[str, Any] = {}
    attrs = list(field_map.ATTR_FIELDS)

    def fill(val: int, only: List[str] | None = None) -> None:
        for f in only or attrs:
            card[f] = val

    if pid == "max_99":
        fill(99)
        card["overallrating"] = 99
        card["potential"] = 99
        card["skillmoves"] = _sm(5)
        card["weakfootabilitytypecode"] = 5
    elif pid == "pace_monster":
        fill(70)
        card["acceleration"] = 99
        card["sprintspeed"] = 99
        card["agility"] = 95
        card["balance"] = 90
        card["stamina"] = 95
        card["overallrating"] = 88
        card["potential"] = 92
    elif pid == "cb_wall":
        fill(65)
        for f in (
            "defensiveawareness",
            "standingtackle",
            "slidingtackle",
            "headingaccuracy",
            "strength",
            "jumping",
            "aggression",
            "interceptions",
            "composure",
        ):
            card[f] = 95
        card["acceleration"] = 75
        card["sprintspeed"] = 78
        card["overallrating"] = 90
        card["potential"] = 92
        card["skillmoves"] = _sm(2)
        card["weakfootabilitytypecode"] = 3
    elif pid == "playmaker":
        fill(72)
        for f in (
            "shortpassing",
            "longpassing",
            "vision",
            "ballcontrol",
            "dribbling",
            "curve",
            "composure",
            "reactions",
        ):
            card[f] = 94
        card["overallrating"] = 91
        card["potential"] = 93
        card["skillmoves"] = _sm(4)
        card["weakfootabilitytypecode"] = 4
    elif pid == "finisher":
        fill(70)
        for f in (
            "finishing",
            "positioning",
            "shotpower",
            "longshots",
            "volleys",
            "headingaccuracy",
            "composure",
            "reactions",
        ):
            card[f] = 95
        card["acceleration"] = 90
        card["sprintspeed"] = 88
        card["overallrating"] = 92
        card["potential"] = 94
        card["skillmoves"] = _sm(4)
        card["weakfootabilitytypecode"] = 4
    elif pid == "balanced_85":
        fill(85)
        card["overallrating"] = 85
        card["potential"] = 88
        card["skillmoves"] = _sm(3)
        card["weakfootabilitytypecode"] = 3
    else:
        raise ValueError(f"Unknown preset {pid}")
    card["name"] = preset_label(pid)
    return card
