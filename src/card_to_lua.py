"""Generate silent LE Lua to apply FUT card stats onto a Career playerid.

Supports full editor topics (attributes, ratings, skills, positions, playstyles,
body, movement, …) via enabled_categories — same as Editor apply.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import field_map
from . import paths
from . import player_apply
from . import player_schema
from .card_types import CardDict, CardRow


def _int_or_none(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None


def card_to_editor_card(card: CardDict) -> CardRow:
    """Normalize FUT/catalog row into editor-ready player card (attrs + meta + PS)."""
    return player_schema.normalize_player_card(dict(card or {}))  # type: ignore[return-value]


def card_field_updates(
    card: CardDict,
    *,
    enabled_categories: Optional[Iterable[str]] = None,
) -> List[Tuple[str, int]]:
    """Legacy helper: attributes-only when categories is None; else full topics."""
    if enabled_categories is None:
        updates = field_map.extract_attr_updates(card)
        fields_present = {f for f, _ in updates}
        if "potential" not in fields_present and "overallrating" in fields_present:
            ovr = next(v for f, v in updates if f == "overallrating")
            updates.append(("potential", ovr))
        # include SM/WF if present even in "attrs-only" path (common card fields)
        for f in ("skillmoves", "weakfootabilitytypecode", "preferredfoot"):
            v = _int_or_none(card.get(f))
            if v is not None and f not in fields_present:
                updates.append((f, v))
        updates = [(f, v) for f, v in updates if f != "modifier"]
        updates.append(("modifier", 0))
        return updates
    ed = card_to_editor_card(card)
    return player_schema.card_to_field_updates(ed, enabled_categories=enabled_categories)


def _lua_escape_name(name: str) -> str:
    return str(name or "card").replace("\\", "\\\\").replace('"', "'").replace("\n", " ")


def card_name_parts(card: CardDict) -> Dict[str, str]:
    """Resolve first/surname/common/jersey for a card.

    Uses the same enrichment chain as Import (card fields, then LE
    base_players.csv, then the display-name string), so a card apply can rename
    the target consistently with the Import flow.
    """
    # Start from the display name. base_players.csv stores names as dictionary
    # ids, not strings (all 22,348 rows have empty name columns), so the display
    # string is usually the only real source of a first name.
    out: Dict[str, str] = {}
    raw = str(card.get("name") or "").strip()
    if raw:
        chunks = raw.split()
        if len(chunks) == 1:
            out["surname"] = chunks[0]
            out["playerjerseyname"] = chunks[0]
        else:
            out["firstname"] = chunks[0]
            out["surname"] = " ".join(chunks[1:])
            out["playerjerseyname"] = chunks[-1]

    # Overlay anything the enrichment chain resolved more precisely.
    try:
        from . import import_player

        bits = import_player.resolve_enrich(card)
        parts = getattr(bits, "name", None) or getattr(bits, "name_parts", None)
        if parts is not None:
            for key in ("firstname", "surname", "commonname", "playerjerseyname"):
                val = getattr(parts, key, None)
                if val is None and isinstance(parts, dict):
                    val = parts.get(key)
                if val and str(val).strip():
                    out[key] = str(val).strip()
    except Exception:  # noqa: BLE001
        pass

    # A commonname identical to the surname adds nothing and shows twice.
    if out.get("commonname") and out.get("commonname") == out.get("surname"):
        out.pop("commonname", None)
    return out


def generate_apply_card_lua(
    card: CardDict,
    target_playerid: int,
    *,
    message_box: bool = False,
    enabled_categories: Optional[Iterable[str]] = None,
    copy_name: bool = False,
) -> str:
    """Apply card onto target playerid.

    enabled_categories: same topic ids as Editor (attributes, ratings, skills,
    positions, playstyles, body, movement, …). Default = all editor defaults
    plus body when height/weight present.

    copy_name: also rename the target to the card's player. Defaults to False —
    the plumbing for this existed in player_apply but was never wired up, so
    every card apply silently logged names=false. It stays opt-in because
    renaming a career player who was only meant to get a stat boost is not
    something to do by surprise.
    """
    del message_box
    target = int(target_playerid)
    ed = card_to_editor_card(card)

    cats: Optional[Set[str]]
    if enabled_categories is None:
        cats = set(player_schema.default_enabled_categories())
        # auto-enable body if FUT card has physical data
        if any(ed.get(k) not in (None, "") for k in ("height", "weight", "bodytypecode")):
            cats.add("body")
        if any(ed.get(k) not in (None, "", 0) for k in ("trait1", "trait2", "icontrait1", "icontrait2")):
            cats.add("playstyles")
        if ed.get("runstylecode") not in (None, ""):
            cats.add("movement")
    else:
        cats = set(enabled_categories)
        if not cats:
            raise ValueError("No topics checked for card apply")

    parts = card_name_parts(card) if copy_name else None
    return player_apply.generate_apply_player_lua(
        ed,
        target,
        enabled_categories=cats,
        title=f"Card apply · {_lua_escape_name(card.get('name') or 'card')}",
        name_parts=parts or None,
    )


def apply_card_from_search(
    cards: Sequence[CardDict],
    target_playerid: int,
    *,
    index: int = 0,
    message_box: bool = False,
    enabled_categories: Optional[Iterable[str]] = None,
    copy_name: bool = False,
) -> str:
    if not cards:
        raise ValueError("No cards to apply")
    if index < 0 or index >= len(cards):
        raise IndexError(f"Card index {index} out of range 0..{len(cards) - 1}")
    return generate_apply_card_lua(
        cards[index],
        target_playerid,
        message_box=False,
        enabled_categories=enabled_categories,
        copy_name=copy_name,
    )


def format_card_info_panel(card: CardDict) -> str:
    from . import card_enrich

    return card_enrich.card_info_summary(card)
