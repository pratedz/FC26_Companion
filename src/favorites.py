"""Favorite cards (local JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import paths
from .card_types import CardDict, CardRow

MAX = 80


def _path() -> Path:
    return paths.app_root() / "favorites.json"


def _key(card: CardDict) -> str:
    pid = card.get("playerid") or card.get("uid") or ""
    year = card.get("year") or ""
    name = card.get("name") or ""
    rev = card.get("revision") or card.get("origin") or ""
    return f"{year}|{pid}|{name}|{rev}"


def load() -> List[CardRow]:
    p = _path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def save(rows: List[CardDict]) -> None:
    try:
        _path().write_text(json.dumps(rows[:MAX], indent=2), encoding="utf-8")
    except OSError:
        pass


def is_favorite(card: CardDict) -> bool:
    k = _key(card)
    return any(_key(c) == k for c in load())


def toggle(card: CardDict) -> bool:
    """Add or remove. Returns True if now favorited."""
    rows = load()
    k = _key(card)
    for i, c in enumerate(rows):
        if _key(c) == k:
            rows.pop(i)
            save(rows)
            return False
    slim = {
        "name": card.get("name"),
        "playerid": card.get("playerid"),
        "year": card.get("year"),
        "overallrating": card.get("overallrating"),
        "revision": card.get("revision") or card.get("origin"),
        "source": card.get("source"),
        "preferredposition1": card.get("preferredposition1"),
    }
    # keep attrs for re-apply if present
    from . import field_map

    for f in field_map.ATTR_FIELDS:
        if card.get(f) is not None:
            slim[f] = card.get(f)
    for f in ("potential", "skillmoves", "weakfootabilitytypecode"):
        if card.get(f) is not None:
            slim[f] = card.get(f)
    rows.insert(0, slim)
    save(rows)
    return True
