"""Compare squad player (or empty) vs card for before/after UI."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import field_map
from .card_types import CardDict

# Compact face for UI
FACE = (
    "overallrating",
    "acceleration",
    "sprintspeed",
    "finishing",
    "shortpassing",
    "dribbling",
    "defensiveawareness",
    "standingtackle",
    "stamina",
    "strength",
    "composure",
    "reactions",
)


def _i(card: CardDict, key: str) -> Optional[int]:
    v = card.get(key)
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def compare(
    before: Optional[CardDict],
    after: CardDict,
    *,
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return list of {field, before, after, delta} for changed or face fields."""
    keys = fields or list(FACE)
    # include any attr present on after
    for f in field_map.ATTR_FIELDS:
        if f not in keys and after.get(f) is not None:
            keys.append(f)
    rows: List[Dict[str, Any]] = []
    before = before or {}
    for f in keys:
        a = _i(after, f)
        b = _i(before, f)
        if a is None and b is None:
            continue
        delta = None
        if a is not None and b is not None:
            delta = a - b
        rows.append({"field": f, "before": b, "after": a, "delta": delta})
    return rows


def format_compare_lines(
    before: Optional[CardDict],
    after: CardDict,
    *,
    limit: int = 18,
) -> str:
    rows = compare(before, after)
    # prefer big deltas first
    rows.sort(key=lambda r: abs(r["delta"] or 0), reverse=True)
    lines = []
    for r in rows[:limit]:
        b = r["before"]
        a = r["after"]
        d = r["delta"]
        ds = f"{d:+d}" if d is not None else ""
        bs = "—" if b is None else str(b)
        as_ = "—" if a is None else str(a)
        lines.append(f"{r['field'][:18]:18}  {bs:>3} → {as_:>3}  {ds}")
    return "\n".join(lines) if lines else "(no comparable fields)"
