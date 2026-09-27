"""Card intelligence: best-match FUT card to a CM/squad target."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import card_catalog
from .card_catalog import _norm
from .card_types import CardDict, CardRow


def _ovr(card: CardDict) -> int:
    try:
        return int(card.get("overallrating") or card.get("ovr") or 0)
    except (TypeError, ValueError):
        return 0


def _pos(card: CardDict) -> Optional[int]:
    for k in ("preferredposition1", "position", "pos"):
        v = card.get(k)
        if v is None or v == "":
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def score_match(
    card: CardDict,
    *,
    name: str,
    ovr: Optional[int] = None,
    position: Optional[int] = None,
    playerid: Optional[int] = None,
) -> float:
    """Higher = better. Name match dominates; OVR/pos refine."""
    cn = _norm(str(card.get("name") or ""))
    qn = _norm(name)
    if not qn or not cn:
        return -1.0
    score = 0.0
    if cn == qn:
        score += 100.0
    elif qn in cn or cn in qn:
        score += 70.0
    else:
        # token overlap
        qt = set(qn.split())
        ct = set(cn.split())
        if qt and ct:
            score += 40.0 * (len(qt & ct) / max(len(qt), 1))
        else:
            return -1.0

    cid = card.get("playerid")
    if playerid is not None and cid is not None:
        try:
            if int(cid) == int(playerid):
                score += 50.0
        except (TypeError, ValueError):
            pass

    if ovr is not None:
        co = _ovr(card)
        if co:
            score += max(0.0, 25.0 - abs(co - int(ovr)) * 1.5)

    if position is not None:
        cp = _pos(card)
        if cp is not None and cp == int(position):
            score += 15.0

    # prefer higher OVR slightly when tied
    score += _ovr(card) * 0.01
    return score


def best_matches(
    name: str,
    *,
    year: Optional[str] = "26",
    ovr: Optional[int] = None,
    position: Optional[int] = None,
    playerid: Optional[int] = None,
    limit: int = 8,
) -> List[Tuple[float, CardRow]]:
    hits = card_catalog.search_cards(name, year=year, limit=max(limit * 6, 40))
    ranked: List[Tuple[float, CardRow]] = []
    for c in hits:
        s = score_match(
            c, name=name, ovr=ovr, position=position, playerid=playerid
        )
        if s >= 0:
            ranked.append((s, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[:limit]


def best_match_for_target(
    target: CardDict,
    *,
    year: Optional[str] = "26",
    limit: int = 5,
) -> List[CardRow]:
    """Rank cards for a squad/catalog target row."""
    name = str(target.get("name") or target.get("playername") or "")
    ovr = None
    for k in ("overallrating", "ovr", "rating"):
        if target.get(k) is not None:
            try:
                ovr = int(target[k])
                break
            except (TypeError, ValueError):
                pass
    pos = None
    for k in ("preferredposition1", "position"):
        if target.get(k) is not None:
            try:
                pos = int(target[k])
                break
            except (TypeError, ValueError):
                pass
    pid = None
    if target.get("playerid") is not None:
        try:
            pid = int(target["playerid"])
        except (TypeError, ValueError):
            pass
    ranked = best_matches(
        name, year=year, ovr=ovr, position=pos, playerid=pid, limit=limit
    )
    out: List[CardRow] = []
    for sc, card in ranked:
        row: CardRow = dict(card)  # type: ignore[assignment]
        row["_match_score"] = round(sc, 2)
        out.append(row)
    return out


def format_match_line(card: CardDict, idx: int = 0) -> str:
    sc = card.get("_match_score", "")
    name = card.get("name") or "?"
    ovr = card.get("overallrating") or card.get("ovr") or "?"
    year = card.get("year") or "?"
    rev = card.get("revision") or card.get("origin") or ""
    return f"[{idx}] score={sc}  {year} OVR {ovr}  {name}  {rev}".strip()
