"""Library-card selection helpers for the V2 Add Player workflow.

AI may suggest footballers, but only a concrete row from the local catalog can
be added.  This module owns that boundary and deterministically resolves each
suggestion to one real card variant.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class CardRecommendation:
    name: str
    reason: str = ""
    variant_hint: str = ""
    club_hint: str = ""
    position_hint: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedCardRecommendation:
    card: Mapping[str, Any]
    reason: str = ""
    requested_name: str = ""


@dataclass(frozen=True, slots=True)
class FormationSlot:
    """One visible role in a squad draft formation."""

    key: str
    label: str
    role: str


@dataclass(frozen=True, slots=True)
class LineupSlot:
    """A formation role and the locally verified card currently assigned to it."""

    slot: FormationSlot
    card: Mapping[str, Any] | None = None
    native_positions: tuple[str, ...] = ()

    @property
    def filled(self) -> bool:
        return self.card is not None


@dataclass(frozen=True, slots=True)
class LineupReview:
    """Pure, review-first result for an Add Player squad draft."""

    formation: str
    slots: tuple[LineupSlot, ...]
    extra_cards: tuple[Mapping[str, Any], ...] = ()
    duplicate_people: tuple[str, ...] = ()

    @property
    def filled_count(self) -> int:
        return sum(1 for slot in self.slots if slot.filled)

    @property
    def missing_slots(self) -> tuple[FormationSlot, ...]:
        return tuple(slot.slot for slot in self.slots if not slot.filled)

    @property
    def valid(self) -> bool:
        if self.formation == INDIVIDUAL_PLAYERS:
            return not self.duplicate_people and bool(self.extra_cards)
        return (
            not self.duplicate_people
            and not self.extra_cards
            and self.filled_count == len(self.slots)
        )


@dataclass(frozen=True, slots=True)
class SignListChange:
    """Result of adding cards to the Sign shopping list without queueing."""

    items: tuple[dict[str, Any], ...]
    added: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    overflow: tuple[str, ...] = ()


# ``Individual players`` intentionally has no roles. It keeps the original
# one-player workflow available while formations turn a selection into a real
# XI draft rather than an unstructured list of cards.
INDIVIDUAL_PLAYERS = "Individual players"
SIGN_LIST_MAX = 25

_FORMATIONS: Mapping[str, tuple[FormationSlot, ...]] = {
    "4-4-1-1": (
        FormationSlot("gk", "GK", "GK"),
        FormationSlot("rb", "RB", "RB"),
        FormationSlot("rcb", "RCB", "CB"),
        FormationSlot("lcb", "LCB", "CB"),
        FormationSlot("lb", "LB", "LB"),
        FormationSlot("rm", "RM", "RM"),
        FormationSlot("rcm", "RCM", "CM"),
        FormationSlot("lcm", "LCM", "CM"),
        FormationSlot("lm", "LM", "LM"),
        FormationSlot("cam", "CAM / support striker", "CAM"),
        FormationSlot("st", "ST", "ST"),
    ),
    "4-3-3": (
        FormationSlot("gk", "GK", "GK"),
        FormationSlot("rb", "RB", "RB"),
        FormationSlot("rcb", "RCB", "CB"),
        FormationSlot("lcb", "LCB", "CB"),
        FormationSlot("lb", "LB", "LB"),
        FormationSlot("rcm", "RCM", "CM"),
        FormationSlot("cm", "CM", "CM"),
        FormationSlot("lcm", "LCM", "CM"),
        FormationSlot("rw", "RW", "RW"),
        FormationSlot("st", "ST", "ST"),
        FormationSlot("lw", "LW", "LW"),
    ),
    "4-2-3-1": (
        FormationSlot("gk", "GK", "GK"),
        FormationSlot("rb", "RB", "RB"),
        FormationSlot("rcb", "RCB", "CB"),
        FormationSlot("lcb", "LCB", "CB"),
        FormationSlot("lb", "LB", "LB"),
        FormationSlot("rcdm", "RCDM", "CDM"),
        FormationSlot("lcdm", "LCDM", "CDM"),
        FormationSlot("ram", "RAM", "CAM"),
        FormationSlot("cam", "CAM", "CAM"),
        FormationSlot("lam", "LAM", "CAM"),
        FormationSlot("st", "ST", "ST"),
    ),
    "4-4-2": (
        FormationSlot("gk", "GK", "GK"),
        FormationSlot("rb", "RB", "RB"),
        FormationSlot("rcb", "RCB", "CB"),
        FormationSlot("lcb", "LCB", "CB"),
        FormationSlot("lb", "LB", "LB"),
        FormationSlot("rm", "RM", "RM"),
        FormationSlot("rcm", "RCM", "CM"),
        FormationSlot("lcm", "LCM", "CM"),
        FormationSlot("lm", "LM", "LM"),
        FormationSlot("rst", "RST", "ST"),
        FormationSlot("lst", "LST", "ST"),
    ),
}

# The first position is native; later positions are intentional role shifts.
# This keeps the lineup useful for real football squads without pretending a
# winger or a fullback is an exact central midfielder.
_ROLE_POSITIONS: Mapping[str, tuple[str, ...]] = {
    "GK": ("GK",),
    "RB": ("RB", "RWB"),
    "CB": ("CB",),
    "LB": ("LB", "LWB"),
    "RM": ("RM", "RW", "RWB"),
    "CM": ("CM", "CDM", "CAM"),
    "LM": ("LM", "LW", "LWB"),
    "CDM": ("CDM", "CM"),
    "CAM": ("CAM", "CF", "CM"),
    "RW": ("RW", "RM"),
    "LW": ("LW", "LM"),
    "ST": ("ST", "CF"),
}

_POSITION_TOKEN = re.compile(
    r"\b(?:GK|RB|RWB|CB|LB|LWB|CDM|CM|CAM|RM|LM|RW|LW|CF|ST)\b",
    flags=re.IGNORECASE,
)

@dataclass(frozen=True, slots=True)
class RecommendationResolveReport:
    """Local Library outcome for a Grok name list, including rejected variants."""

    resolved: tuple[ResolvedCardRecommendation, ...]
    missing: tuple[str, ...] = ()
    rejected_out_of_band: tuple[str, ...] = ()


def resolve_card_recommendations(
    catalog: Any,
    recommendations: Sequence[CardRecommendation],
    *,
    limit: int = 11,
    year: int | None = None,
    ovr_min: int | None = None,
    ovr_max: int | None = None,
    exclude_people: Sequence[str] = (),
) -> tuple[ResolvedCardRecommendation, ...]:
    """Resolve AI names to distinct, real local cards; never invent a row."""
    return resolve_recommendation_report(
        catalog,
        recommendations,
        limit=limit,
        year=year,
        ovr_min=ovr_min,
        ovr_max=ovr_max,
        exclude_people=exclude_people,
    ).resolved


def resolve_recommendation_report(
    catalog: Any,
    recommendations: Sequence[CardRecommendation],
    *,
    limit: int = 11,
    year: int | None = None,
    ovr_min: int | None = None,
    ovr_max: int | None = None,
    exclude_people: Sequence[str] = (),
) -> RecommendationResolveReport:
    """Same resolve as the draft, plus names the Library could not keep in-band."""
    resolved: list[ResolvedCardRecommendation] = []
    missing: list[str] = []
    rejected: list[str] = []
    seen_people: set[str] = {str(item) for item in exclude_people if str(item)}
    year_text = "" if year is None else str(year)
    band = _ovr_band_text(ovr_min, ovr_max)
    for recommendation in recommendations[: max(1, min(int(limit), 11))]:
        name = recommendation.name.strip()
        if not name:
            continue
        candidates = [
            dict(card)
            for card in catalog.search(name, year=year_text, limit=100)
        ]
        if not candidates:
            missing.append(name)
            continue
        eligible = [
            card
            for card in candidates
            if _matches_constraints(card, year=year, ovr_min=ovr_min, ovr_max=ovr_max)
        ]
        if not eligible:
            best = max(_card_overall(card) for card in candidates)
            rejected.append(
                f"{name} only has OVR {best} — outside {band}."
                if band
                else f"{name} has no Library card that matches the requested filters."
            )
            continue
        wanted = _norm(name)
        exact = [
            card for card in eligible
            if _norm(card.get("name") or card.get("playername") or "") == wanted
        ]
        if not exact:
            missing.append(name)
            continue
        people = {
            str(
                card.get("person_id")
                or card.get("base_player_id")
                or card.get("name")
                or ""
            )
            for card in exact
        }
        people.discard("")
        if len(people) > 1:
            missing.append(name)
            continue
        ranked = sorted(
            exact,
            key=lambda card: _candidate_score(card, recommendation),
            reverse=True,
        )
        chosen = None
        for card in ranked:
            person = str(
                card.get("person_id")
                or card.get("base_player_id")
                or card.get("name")
                or ""
            )
            if not person or person in seen_people:
                continue
            seen_people.add(person)
            chosen = ResolvedCardRecommendation(
                card=card,
                reason=recommendation.reason,
                requested_name=name,
            )
            break
        if chosen is None:
            missing.append(name)
            continue
        resolved.append(chosen)
    return RecommendationResolveReport(
        resolved=tuple(resolved),
        missing=tuple(dict.fromkeys(missing)),
        rejected_out_of_band=tuple(dict.fromkeys(rejected)),
    )


def _card_overall(card: Mapping[str, Any]) -> int:
    try:
        return int(card.get("overallrating") or card.get("overall") or 0)
    except (TypeError, ValueError):
        return 0


def _ovr_band_text(ovr_min: int | None, ovr_max: int | None) -> str:
    if ovr_min is not None and ovr_max is not None:
        return f"{ovr_min}-{ovr_max}"
    if ovr_min is not None:
        return f"{ovr_min}+"
    if ovr_max is not None:
        return f"up to {ovr_max}"
    return ""


def formation_names() -> tuple[str, ...]:
    """Supported player-facing formations, including the individual workflow."""
    return (INDIVIDUAL_PLAYERS, *_FORMATIONS.keys())


def formation_slots(formation: str) -> tuple[FormationSlot, ...]:
    """Return immutable formation roles or fail before a misleading draft opens."""
    name = str(formation or INDIVIDUAL_PLAYERS).strip() or INDIVIDUAL_PLAYERS
    if name == INDIVIDUAL_PLAYERS:
        return ()
    try:
        return _FORMATIONS[name]
    except KeyError as exc:
        choices = ", ".join(_FORMATIONS)
        raise ValueError(
            f"{name!r} is not a supported formation. Choose one of: {choices}."
        ) from exc


def review_lineup(
    cards: Sequence[Mapping[str, Any]],
    *,
    formation: str = INDIVIDUAL_PLAYERS,
) -> LineupReview:
    """Assign distinct selected cards to formation slots without inventing roles.

    The review is deliberately pure. It gives the UI an honest answer before a
    destructive add is even possible: every slot is filled by a native or
    clearly-labelled compatible position, duplicate people are exposed, and
    surplus/wrong-role cards remain visible rather than being silently dropped.
    """
    name = str(formation or INDIVIDUAL_PLAYERS).strip() or INDIVIDUAL_PLAYERS
    slots = formation_slots(name)
    unique_cards, duplicate_people = _distinct_cards(cards)
    if not slots:
        return LineupReview(
            formation=name,
            slots=(),
            extra_cards=tuple(unique_cards),
            duplicate_people=tuple(duplicate_people),
        )

    positions = tuple(_card_positions(card) for card in unique_cards)
    assignment = _best_assignment(slots, positions)
    used = {idx for idx in assignment if idx is not None}
    reviewed = tuple(
        LineupSlot(
            slot=slot,
            card=unique_cards[idx] if idx is not None else None,
            native_positions=positions[idx] if idx is not None else (),
        )
        for slot, idx in zip(slots, assignment, strict=True)
    )
    extras = tuple(card for idx, card in enumerate(unique_cards) if idx not in used)
    return LineupReview(
        formation=name,
        slots=reviewed,
        extra_cards=extras,
        duplicate_people=tuple(duplicate_people),
    )


def _distinct_cards(
    cards: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[str]]:
    unique: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        identity = _card_identity(card)
        if identity in seen:
            duplicates.append(_card_name(card))
            continue
        seen.add(identity)
        unique.append(card)
    return unique, duplicates


def _card_identity(card: Mapping[str, Any]) -> str:
    return str(
        card.get("person_id")
        or card.get("base_player_id")
        or card.get("playerid")
        or card.get("_card_key")
        or card.get("obs_id")
        or _norm(card.get("name") or card.get("playername") or "")
    )


def _card_name(card: Mapping[str, Any]) -> str:
    return str(card.get("name") or card.get("playername") or "Player")


def sign_list_key(card: Mapping[str, Any]) -> str:
    return _card_identity(card)


def sign_list_items(raw: Sequence[Any] | None) -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in (raw or ()) if isinstance(item, Mapping))


def cards_from_display_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Unwrap DataGrid rows (``_raw``) back into Library cards."""
    cards: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not row:
            continue
        raw = row.get("_raw")
        cards.append(dict(raw if isinstance(raw, Mapping) else row))
    return tuple(cards)


def add_to_sign_list(
    items: Sequence[Mapping[str, Any]],
    cards: Sequence[Mapping[str, Any]],
    *,
    limit: int = SIGN_LIST_MAX,
) -> SignListChange:
    """Append unique people to the shopping list. Nothing is queued."""
    current = [dict(item) for item in items if isinstance(item, Mapping)]
    seen = {sign_list_key(item) for item in current}
    added: list[str] = []
    skipped: list[str] = []
    overflow: list[str] = []
    cap = max(1, int(limit))
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        incoming = dict(card)
        incoming.pop("_raw", None)
        name = _card_name(incoming)
        key = sign_list_key(incoming)
        if not key:
            skipped.append(name)
            continue
        if key in seen:
            skipped.append(name)
            continue
        if len(current) >= cap:
            overflow.append(name)
            continue
        current.append(incoming)
        seen.add(key)
        added.append(name)
    return SignListChange(
        items=tuple(current),
        added=tuple(added),
        skipped=tuple(skipped),
        overflow=tuple(overflow),
    )


def drop_from_sign_list(
    items: Sequence[Mapping[str, Any]],
    key: str,
) -> tuple[dict[str, Any], ...]:
    wanted = str(key or "")
    return tuple(
        dict(item)
        for item in items
        if isinstance(item, Mapping) and sign_list_key(item) != wanted
    )


def sign_list_change_message(change: SignListChange) -> str:
    parts: list[str] = []
    if change.added:
        parts.append("Added " + ", ".join(change.added) + " to your bag.")
    if change.skipped:
        parts.append("Already in the bag: " + ", ".join(change.skipped) + ".")
    if change.overflow:
        parts.append(
            f"Bag is full ({len(change.items)}). Not added: "
            + ", ".join(change.overflow)
            + "."
        )
    if not parts:
        return "Select a Library card to add it to your bag."
    return " ".join(parts)


def _card_positions(card: Mapping[str, Any]) -> tuple[str, ...]:
    text = str(card.get("positions_text") or card.get("positions") or "")
    tokens = tuple(dict.fromkeys(token.upper() for token in _POSITION_TOKEN.findall(text)))
    return tokens


def _role_score(slot: FormationSlot, positions: Sequence[str]) -> int:
    allowed = _ROLE_POSITIONS.get(slot.role, (slot.role,))
    for index, position in enumerate(allowed):
        if position in positions:
            # Native role choices win ties. A compatible secondary position is
            # still valid but cannot displace an exact position elsewhere.
            return 100 - (index * 10)
    return 0


def _best_assignment(
    slots: Sequence[FormationSlot], positions: Sequence[Sequence[str]]
) -> tuple[int | None, ...]:
    """Maximum-cardinality, maximum-fit assignment for at most 11 roles."""

    @lru_cache(maxsize=None)
    def solve(slot_index: int, used_mask: int) -> tuple[int, int, tuple[int | None, ...]]:
        if slot_index >= len(slots):
            return 0, 0, ()
        filled, quality, tail = solve(slot_index + 1, used_mask)
        best = (filled, quality, (None, *tail))
        for card_index, card_positions in enumerate(positions):
            if used_mask & (1 << card_index):
                continue
            score = _role_score(slots[slot_index], card_positions)
            if not score:
                continue
            next_filled, next_quality, next_tail = solve(
                slot_index + 1, used_mask | (1 << card_index)
            )
            candidate = (
                next_filled + 1,
                next_quality + score,
                (card_index, *next_tail),
            )
            if candidate[:2] > best[:2]:
                best = candidate
        return best

    return solve(0, 0)[2]


def _matches_constraints(
    card: Mapping[str, Any], *, year: int | None, ovr_min: int | None, ovr_max: int | None
) -> bool:
    try:
        card_year = int(card.get("year") or 0)
        overall = int(card.get("overallrating") or card.get("overall") or 0)
    except (TypeError, ValueError):
        return False
    if year is not None and card_year != int(year):
        return False
    if ovr_min is not None and overall < int(ovr_min):
        return False
    if ovr_max is not None and overall > int(ovr_max):
        return False
    return True


def _candidate_score(card: Mapping[str, Any], rec: CardRecommendation) -> tuple[Any, ...]:
    card_name = _norm(card.get("name") or card.get("playername") or "")
    wanted_name = _norm(rec.name)
    exact_name = int(bool(card_name) and card_name == wanted_name)
    partial_name = int(bool(wanted_name) and wanted_name in card_name)
    variant = _norm(card.get("variant") or card.get("revision") or "")
    club = _norm(card.get("club") or card.get("club_name") or "")
    positions = _norm(card.get("positions_text") or "")
    variant_match = _hint_score(variant, rec.variant_hint)
    club_match = _hint_score(club, rec.club_hint)
    legend_match = _hint_score(f"{variant} {club}", rec.variant_hint) + _hint_score(
        f"{variant} {club} {rec.reason}", rec.club_hint
    )
    position_match = _position_score(positions, rec.position_hint)
    try:
        year = int(card.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    try:
        overall = int(card.get("overallrating") or card.get("overall") or 0)
    except (TypeError, ValueError):
        overall = 0
    # Exact identity dominates. Club/legend hints beat raw OVR inside the band.
    return (
        exact_name,
        partial_name,
        legend_match,
        variant_match,
        club_match,
        position_match,
        int(year == 26),
        overall,
        str(card.get("_card_key") or card.get("obs_id") or ""),
    )


def _hint_score(haystack: str, hint: str) -> int:
    text = _norm(haystack)
    tokens = [token for token in _norm(hint).split() if len(token) > 1]
    aliases = {
        "man": ("manchester", "manutd", "united"),
        "utd": ("united", "manchester"),
        "united": ("manchester",),
        "legend": ("icon", "hero"),
        "icon": ("icon",),
        "hero": ("hero",),
        "special": ("special", "promo"),
    }
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        expanded.extend(aliases.get(token, ()))
    return sum(1 for token in dict.fromkeys(expanded) if token in text)


def _position_score(positions: str, hint: str) -> int:
    wanted = _norm(hint)
    if not wanted:
        return 0
    aliases = {
        "goalkeeper": ("gk",),
        "defender": ("cb", "lb", "rb", "lwb", "rwb"),
        "midfielder": ("cm", "cam", "cdm", "lm", "rm"),
        "forward": ("st", "cf", "lw", "rw"),
        "striker": ("st", "cf"),
    }
    tokens = aliases.get(wanted, tuple(wanted.upper().split()))
    present = set(re.findall(r"[a-z]+", positions))
    return sum(1 for token in tokens if token.casefold() in present)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(ch for ch in text if not unicodedata.combining(ch)).split()
    )


__all__ = [
    "CardRecommendation",
    "RecommendationResolveReport",
    "ResolvedCardRecommendation",
    "SIGN_LIST_MAX",
    "SignListChange",
    "add_to_sign_list",
    "cards_from_display_rows",
    "drop_from_sign_list",
    "resolve_card_recommendations",
    "resolve_recommendation_report",
    "sign_list_change_message",
    "sign_list_items",
    "sign_list_key",
]
