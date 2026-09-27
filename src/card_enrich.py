"""Enrich catalog cards with full FUT.GG definition (meta + playstyles) on demand."""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import futgg_client
from .card_types import CardDict, CardRow


def enrich_card_from_futgg(card: CardDict) -> CardRow:
    """Return card merged with live FUT.GG definition when slug/year available.

    Safe offline: if network fails, returns original card unchanged.
    """
    out = dict(card)
    slug = str(card.get("slug") or "")
    if not slug:
        # try build from eaId + year
        year = str(card.get("year") or card.get("game") or card.get("_year") or "")
        ea = card.get("eaId") or card.get("item_id")
        if year and ea:
            slug = f"{year}-{ea}"
    if not slug:
        return out
    try:
        defs = futgg_client.fetch_definitions([slug])
        if not defs:
            return out
        row = futgg_client.definition_to_le_row(defs[0])
        # Prefer enriched meta; keep search identity
        for k, v in row.items():
            if v is not None and v != "":
                out[k] = v
        out["_enriched"] = True
        out["_source"] = out.get("_source") or "futgg"
    except Exception:
        out["_enriched"] = False
    return out


def card_info_summary(card: CardDict) -> str:
    """Human multi-line info panel (FUT.GG style)."""
    lines = [
        f"Name: {card.get('name') or '—'}",
        f"Club: {card.get('club') or '—'}",
        f"League: {card.get('league') or '—'}",
        f"Nation: {card.get('nation') or card.get('nation_name') or '—'}",
        f"Rarity: {card.get('revision') or card.get('rarity') or card.get('origin') or '—'}",
        f"Height: {card.get('height') or '—'} cm",
        f"Weight: {card.get('weight') or '—'} kg",
        f"Foot: {_foot_label(card)}",
        f"Skill Moves: {card.get('skillmoves') or '—'}",
        f"Weak Foot: {card.get('weakfootabilitytypecode') or '—'}",
        f"AcceleRATE: {card.get('accelerateType') or card.get('acceleRATE') or '—'}",
        f"Body Type: {card.get('bodytypecode') or '—'}",
        f"Real Face: {card.get('isRealFace') if card.get('isRealFace') is not None else '—'}",
        f"Shirt Number: {card.get('shirtnumber') or '—'}",
        f"Player ID: {card.get('playerid') or card.get('basePlayerEaId') or '—'}",
        f"Item ID: {card.get('eaId') or card.get('item_id') or '—'}",
        f"OVR: {card.get('overallrating') or card.get('overall') or '—'}",
        f"PlayStyles: {_ps_labels(card, plus=False)}",
        f"PlayStyle+: {_ps_labels(card, plus=True)}",
        f"trait1/2: {card.get('trait1') or 0} / {card.get('trait2') or 0}",
        f"icontrait1/2: {card.get('icontrait1') or 0} / {card.get('icontrait2') or 0}",
    ]
    return "\n".join(lines)


def _foot_label(card: Dict[str, Any]) -> str:
    f = card.get("preferredfoot")
    if f == 1:
        return "Right"
    if f == 2:
        return "Left"
    raw = card.get("foot")
    return str(raw) if raw not in (None, "") else "—"


def _ps_ids(v: Any) -> str:
    if not v:
        return "—"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    return str(v)


def _ps_labels(card: Dict[str, Any], *, plus: bool) -> str:
    try:
        from . import player_schema

        reg, plus_names = player_schema.card_playstyle_labels(card)
        names = plus_names if plus else reg
        return ", ".join(names) if names else "—"
    except Exception:
        key = "playstyles_plus" if plus else "playstyles"
        return _ps_ids(card.get(key) or (card.get("playstylesPlus") if plus else None))


def ai_prompt_from_card(card: Dict[str, Any]) -> str:
    """Prompt for Grok: recommend a CM build based on this FUT card."""
    return (
        "Create an FC 26 Career Mode player edit recommendation based on this FUT card. "
        "Keep overall near the card OVR. Fill attributes, skill moves, weak foot, "
        "preferred foot, body height/weight if present, and playstyle names if known.\n\n"
        f"{card_info_summary(card)}\n\n"
        "Return a realistic in-game build for Career Mode (not pure Ultimate Team chem)."
    )
