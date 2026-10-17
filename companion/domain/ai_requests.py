"""Reusable AI requests shared by Club's "Plan these players" and Player's AI mode.

Built-in requests ship in two wordings: Club's speaks about a group of players
and may arrange shirts; Player's speaks about one player and leaves out shirt
work, because a single-player proposal cannot change squad numbers. Requests
the user saves land in one file (``planner_requests.json`` in the app root) and
show up on both surfaces, whichever one saved them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SAVED_REQUESTS_FILE = "planner_requests.json"

CLUB_REQUESTS: dict[str, str] = {
    "Skills and quick step": "Make all players 5 star skill moves and Quick Step+. Arrange jerseys as recommended.",
    "Improve playstyles": "Improve these players by adding role-appropriate PlayStyles and PlayStyle+ with no ceiling. Keep existing playstyles.",
    "Shirts only": "Arrange these players by jersey number as recommended. Do not change ratings, age or playstyles.",
    "Young prospects": "Improve these prospects to an overall range of 75-85 and set age to 19. Improve role-appropriate playstyles.",
}

# The Club requests, worded for one player. "Shirts only" has no Player form.
PLAYER_REQUESTS: dict[str, str] = {
    "Skills and quick step": "Make him 5 star skill moves and add Quick Step+. Keep his other ratings and playstyles.",
    "Improve playstyles": "Improve him by adding role-appropriate PlayStyles and PlayStyle+ with no ceiling. Keep existing playstyles.",
    "Young prospect": "Improve him to an overall in the 75-85 range and set his age to 19. Add role-appropriate playstyles.",
}

# Single-player build ideas (the old "Try" suggestions on the Player page).
PLAYER_BUILD_IDEAS: dict[str, str] = {
    "Faster, still realistic": "Make him faster but keep him realistic",
    "Deep-lying playmaker": "Improve passing for a deep-lying playmaker",
    "Box-to-box midfielder": "Turn him into a box-to-box midfielder",
    "Defensive boost": "Boost defending without killing pace",
}


@dataclass(frozen=True)
class AIRequest:
    name: str
    text: str
    group: str  # "club", "idea" or "saved"


def player_requests(saved: dict[str, str] | None = None) -> tuple[AIRequest, ...]:
    """Everything Player's AI mode offers, in display order: Club, ideas, saved."""
    items = [AIRequest(name, text, "club") for name, text in PLAYER_REQUESTS.items()]
    items += [AIRequest(name, text, "idea") for name, text in PLAYER_BUILD_IDEAS.items()]
    items += [AIRequest(name, text, "saved") for name, text in (saved or {}).items()]
    return tuple(items)


def saved_requests_path(root: Path) -> Path:
    return Path(root) / SAVED_REQUESTS_FILE


def load_requests(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if isinstance(v, str) and v.strip()}
    except (OSError, ValueError):
        return {}


def save_request(path: Path, name: str, request: str) -> None:
    if not name.strip() or not request.strip():
        raise ValueError("Give the saved request a name and some changes.")
    saved = load_requests(path)
    saved[name.strip()] = request.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
