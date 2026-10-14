"""Card collections: which campaign a card belongs to, and which card is "base".

Every Library card carries a free-text label (FUT.GG rarity name, Live Editor
card type, or ``base`` for Career rows).  The same campaign is spelt many ways
across sources and years ("Team of the Year ICON", "TOTY Icon", "TOTY HM
Heroes"), so the Sign surface groups labels into named collections here.

Pure functions only: no I/O, no UI.  The Library turns a collection into SQL
label lists; Sign and Suggestions use :func:`choose_card` to pick one card per
player for the Best and Base modes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

#: The FC26 promo campaigns, in release order.
PROMO_CAMPAIGNS: tuple[str, ...] = (
    "Cornerstones",
    "Ratings Reload",
    "Ultimate Scream",
    "UEFA Primetime",
    "FUT Captains",
    "FC Pro Live",
    "Black Friday / Thunderstruck",
    "Joga Bonito",
    "Unbreakables",
    "Winter Wildcards",
    "Time Warp",
    "Team of the Year",
    "Future Stars",
    "Knockout Royalty",
    "Fantasy FC",
    "FUT Birthday",
    "FOF: Answer the Call",
    "Road to the Final",
    "Trophy Titans",
    "Team of the Season",
    "FOF: Path to Glory",
    "FOF: Greats of the Game",
    "FOF: Glory Hunters",
    "FOF: Phenoms",
    "FOF: Summer Stars",
    "Shapeshifters",
    "FUTTIES",
    "Ultimate Rewind",
    "Pre-Season",
)

BASE_GOLD, BASE_SILVER, BASE_BRONZE = "Base Gold", "Base Silver", "Base Bronze"
BASE_ICONS, BASE_HEROES = "Base Icons", "Base & Prime Heroes"

#: Weekly and core collections that exist all year.
CORE_COLLECTIONS: tuple[str, ...] = (
    "Team of the Week",
    BASE_ICONS,
    BASE_HEROES,
    BASE_GOLD,
    BASE_SILVER,
    BASE_BRONZE,
    "Libertadores & Sudamericana",
    "Flashback",
    "Moments",
    "Showdown",
    "End of an Era",
    "Player of the Month",
    "Squad Foundations",
)

#: Smaller campaigns and FC27 launch collections.
OTHER_COLLECTIONS: tuple[str, ...] = (
    "FOF: National Pride",
    "FOF: Star Performer",
    "Star Performer",
    "World Tour",
    "Champions",
    "FC Pro Champions",
    "Icon Swaps",
    "Journey of Nations",
    "Classic XI",
    "Festival of Football",
    "Ones to Watch",
    "Destined for Glory",
    "Debut Icons",
    "Hall of FUT",
    "Partnerships",
    "Evolutions",
)

KIND_ICON, KIND_HERO, KIND_PLAYER = "Icon", "Hero", "Player"
#: FUT clubs that mark an Icon or Hero card whose label does not say so.
ICON_CLUBS = ("ICON", "ICONS")
HERO_CLUBS = ("HERO", "HEROES", "HALL OF FUT")
#: Type filter values, in the order Sign offers them.
KINDS: tuple[str, ...] = (KIND_PLAYER, KIND_ICON, KIND_HERO)

PICK_BEST, PICK_BASE, PICK_ALL = "best", "base", "all"
PICKS: tuple[str, ...] = (PICK_BEST, PICK_BASE, PICK_ALL)

_BASE_PLAYER = "__base_player__"


@dataclass(frozen=True, slots=True)
class LabelClass:
    """What a card label alone says.  Base players still need an OVR tier."""

    family: str
    kind: str = ""          # Icon / Hero when the label says so, else unknown
    base: bool = False      # the plain card of its kind, not a promo
    mapped: bool = True     # False when the label fell through to its own name
    tier: str = ""          # Gold / Silver / Bronze when the label says so


@dataclass(frozen=True, slots=True)
class CardClass:
    collection: str
    kind: str
    base: bool


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.replace("’", "'").split())


def clean_label(label: Any) -> str:
    """Folded label without duplicate counters, finishes or SBC/token tags."""
    text = _fold(label)
    text = re.sub(r"\s*#\d+$", "", text)
    text = re.sub(r"\((pristine )?holographic\)", "", text)
    text = re.sub(r"\b(sbc|1 token)\b", "", text)
    return " ".join(text.split())


def is_holographic(label: Any) -> bool:
    return "holographic" in _fold(label)


_KIND_ICON_RE = re.compile(r"\bicons?\b")
_KIND_HERO_RE = re.compile(r"\bhero(es)?\b")

# Base cards.  ``base`` is the Career/EA/SoFIFA row of a real player.
_BASE_PLAYER_RE = re.compile(
    r"^(base|common|rare|non-rare|(gold|silver|bronze)( (non-rare|rare|common))?)$"
)
_BASE_ICON_RE = re.compile(r"^(base icons?|icons?|all icons)$")
_BASE_HERO_RE = re.compile(r"^(base hero(es)?|heroes|hero)$")
_PRIME_HERO_RE = re.compile(r"^prime hero(es)?$")

# Ordered: more specific phrases first.
_FAMILY_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), family)
    for pattern, family in (
        # Campaign tags added by :func:`campaign_label` win over the reused promo name.
        (r"\b(ultimate )?rewind\b", "Ultimate Rewind"),
        (r"\bpre-? ?season\b", "Pre-Season"),
        (r"\bteam of the week\b|\btotw\b", "Team of the Week"),
        (r"\bteam of the year\b|\btoty\b", "Team of the Year"),
        (r"\bteam of the season\b|\btots\b", "Team of the Season"),
        (r"\bcornerstones?\b", "Cornerstones"),
        (r"\bratings? reload\b", "Ratings Reload"),
        (r"\bultimate scream\b", "Ultimate Scream"),
        (r"\bprimetime\b", "UEFA Primetime"),
        (r"\broad to the finals?\b|\brttf\b", "Road to the Final"),
        (r"\banswer the call\b", "FOF: Answer the Call"),
        (r"\bpath to glory\b", "FOF: Path to Glory"),
        (r"\bgreats of the game\b", "FOF: Greats of the Game"),
        (r"\bglory hunters\b", "FOF: Glory Hunters"),
        (r"\bphenoms?\b", "FOF: Phenoms"),
        (r"\bsummer stars\b", "FOF: Summer Stars"),
        (r"\bnational pride\b", "FOF: National Pride"),
        (r"\b(festival of football|fof):? star performer\b", "FOF: Star Performer"),
        (r"\bcaptains?\b", "FUT Captains"),
        (r"\bshowdown\b", "Showdown"),
        (r"\bfc pro (open|world) champion\b|\becl champion\b", "FC Pro Champions"),
        (r"\bfc pro\b", "FC Pro Live"),
        (r"\bthunderstruck\b|\bblack friday\b", "Black Friday / Thunderstruck"),
        (r"\bjoga bonito\b", "Joga Bonito"),
        (r"\bunbreakables?\b", "Unbreakables"),
        (r"\bwinter wildcards?\b", "Winter Wildcards"),
        (r"\btime warp\b", "Time Warp"),
        (r"\bfuture stars?\b", "Future Stars"),
        (r"\bknockout royalty\b", "Knockout Royalty"),
        (r"\bfantasy (fc|premier league)\b", "Fantasy FC"),
        (r"\bbirthday\b", "FUT Birthday"),
        (r"\btrophy titans?\b", "Trophy Titans"),
        (r"\bshape ?shifters?\b", "Shapeshifters"),
        (r"\bfutties\b", "FUTTIES"),
        (r"\blibertadores\b|\bsudamericana\b|\bconmebol\b", "Libertadores & Sudamericana"),
        (r"\bflashback\b", "Flashback"),
        (r"\bmoments?\b", "Moments"),
        (r"\bend of (an )?era\b", "End of an Era"),
        (r"\bpotm\b|\bplayer of the month\b", "Player of the Month"),
        (r"\bsquad foundations?\b", "Squad Foundations"),
        (r"\bworld tour\b", "World Tour"),
        (r"\bstar performer\b", "Star Performer"),
        (r"\bicon swaps?\b", "Icon Swaps"),
        (r"\bchampions?\b", "Champions"),
        (r"\bjourney of nations\b", "Journey of Nations"),
        (r"\bclassic xi\b", "Classic XI"),
        (r"\bones to watch\b|\botw\b", "Ones to Watch"),
        (r"\bdestined for glory\b", "Destined for Glory"),
        (r"\bdebut (international )?icons?\b", "Debut Icons"),
        (r"\bhall of fut\b", "Hall of FUT"),
        (r"\bpartnerships?\b", "Partnerships"),
        (r"\bevo(lution)?s?\b", "Evolutions"),
        (r"^festival of football\b", "Festival of Football"),
    )
)


@lru_cache(maxsize=8192)
def label_class(label: str) -> LabelClass:
    """Classify a card label without looking at the rest of the card."""
    text = clean_label(label)
    kind = KIND_ICON if _KIND_ICON_RE.search(text) else KIND_HERO if _KIND_HERO_RE.search(text) else ""
    if not text or _BASE_PLAYER_RE.match(text):
        # The kind comes from the club: FUT Icons and Heroes play for ICON/HERO.
        tier = next((t.title() for t in ("gold", "silver", "bronze") if text.startswith(t)), "")
        return LabelClass(_BASE_PLAYER, "", base=True, tier=tier)
    if _BASE_ICON_RE.match(text):
        return LabelClass(BASE_ICONS, KIND_ICON, base=True)
    if _BASE_HERO_RE.match(text):
        return LabelClass(BASE_HEROES, KIND_HERO, base=True)
    if _PRIME_HERO_RE.match(text):
        return LabelClass(BASE_HEROES, KIND_HERO)
    if re.match(r"^base hall of fut$", text):
        # FC27's retired fan favourites; the Type filter groups them with Heroes.
        return LabelClass("Hall of FUT", KIND_HERO, base=True)
    for pattern, family in _FAMILY_RULES:
        if pattern.search(text):
            if family == "Debut Icons":
                kind = KIND_ICON
            elif family == "Hall of FUT":
                kind = KIND_HERO
            return LabelClass(family, kind)
    own = " ".join(word if word.isupper() else word.capitalize() for word in str(label or "").split())
    own = re.sub(r"\s*#\d+$", "", own).strip() or "Other"
    return LabelClass(own, kind, mapped=False)


def base_tier(overall: Any, label_tier: str = "") -> str:
    if label_tier:
        return label_tier
    try:
        value = int(overall)
    except (TypeError, ValueError):
        return "Bronze"
    return "Gold" if value >= 75 else "Silver" if value >= 65 else "Bronze"


def card_label(card: Mapping[str, Any]) -> str:
    return str(card.get("variant") or card.get("revision") or card.get("rarity") or "base")


def card_overall(card: Mapping[str, Any]) -> int:
    for key in ("overallrating", "overall", "ovr"):
        try:
            return int(card.get(key))
        except (TypeError, ValueError):
            continue
    return -1


def card_year(card: Mapping[str, Any]) -> int:
    for key in ("year", "game_year", "game"):
        match = re.search(r"\d{2,4}", str(card.get(key) or ""))
        if match:
            value = int(match.group(0))
            return value % 100 if value >= 2000 else value
    return -1


def classify(card: Mapping[str, Any]) -> CardClass:
    """Collection, kind and base flag for one Library card."""
    label = card_label(card)
    info = label_class(label)
    club = str(card.get("club") or card.get("club_name") or "").strip().upper()
    kind = info.kind or (KIND_ICON if club in ICON_CLUBS else KIND_HERO if club in HERO_CLUBS else KIND_PLAYER)
    if info.family == _BASE_PLAYER:
        if kind == KIND_ICON:
            return CardClass(BASE_ICONS, kind, True)
        if kind == KIND_HERO:
            return CardClass(BASE_HEROES, kind, True)
        return CardClass(f"Base {base_tier(card_overall(card), info.tier)}", kind, True)
    base = info.base and not is_holographic(label)
    return CardClass(info.family, kind, base)


def collection_rank(name: str) -> tuple[int, int, str]:
    """Sort key: core collections, then FC26 promos in release order, then others."""
    for group, names in enumerate((CORE_COLLECTIONS, PROMO_CAMPAIGNS, OTHER_COLLECTIONS)):
        if name in names:
            return group, names.index(name), name
    return 3, 0, name.casefold()


def ordered_collections(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(n for n in names if n), key=collection_rank))


def collections_for_labels(labels: Iterable[str]) -> tuple[str, ...]:
    """Every collection a set of labels can produce (base players fan out to tiers)."""
    found: set[str] = set()
    for label in labels:
        info = label_class(str(label))
        if info.family == _BASE_PLAYER:
            found.update((BASE_GOLD, BASE_SILVER, BASE_BRONZE) if not info.tier else (f"Base {info.tier}",))
        else:
            found.add(info.family)
    return ordered_collections(found)


def matches(card: Mapping[str, Any], *, collection: str = "", kind: str = "", base_only: bool = False) -> bool:
    info = classify(card)
    if collection and info.collection != collection:
        return False
    if kind and info.kind != kind:
        return False
    if base_only and not info.base:
        return False
    return True


# Higher is preferred when several base rows describe the same player-year.
_SOURCE_PRIORITY = {"fut": 3, "futgg": 3, "le_cards": 2, "le_base": 1}


def _source_rank(card: Mapping[str, Any]) -> int:
    for key in ("source", "source_kind"):
        value = str(card.get(key) or "").casefold()
        if value.startswith("futgg"):
            return 3
        if value in _SOURCE_PRIORITY:
            return _SOURCE_PRIORITY[value]
    return 0


def person_key(card: Mapping[str, Any]) -> str:
    for field in ("person_id", "playerid", "base_player_id", "basePlayerEaId"):
        value = card.get(field)
        if value not in (None, "", 0):
            return f"id:{value}"
    return "name:" + _fold(card.get("name") or card.get("playername") or "")


def pick_rank(card: Mapping[str, Any], pick: str) -> tuple[Any, ...]:
    """Larger is better.  Best: highest OVR, newer year.  Base: newest base card."""
    if pick == PICK_BASE:
        info = classify(card)
        return (int(info.base), card_year(card), _source_rank(card), -card_overall(card))
    return (card_overall(card), card_year(card), _source_rank(card))


def choose_cards(rows: Sequence[Mapping[str, Any]], pick: str) -> tuple[dict[str, Any], ...]:
    """One card per player for Best/Base; every card for All.  Keeps first-seen order."""
    items = [dict(row) for row in rows if isinstance(row, Mapping)]
    if pick == PICK_ALL:
        return tuple(items)
    if pick == PICK_BASE:
        items = [row for row in items if classify(row).base]
    chosen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for card in items:
        key = person_key(card)
        current = chosen.get(key)
        if current is None:
            chosen[key] = card
            order.append(key)
        elif pick_rank(card, pick) > pick_rank(current, pick):
            chosen[key] = card
    return tuple(chosen[key] for key in order)


_FILLER_WORDS = {"fof", "festival", "of", "football", "the", "fut", "uefa", "&", "/"}


def describe(card: Mapping[str, Any]) -> str:
    """Short row caption: the collection, plus what the card label adds to it.

    "Team of the Year ICON" reads "Team of the Year · ICON"; a label that adds
    nothing ("Cornerstones", "Rare") reads as the collection alone.
    """
    info = classify(card)
    label = re.sub(r"\s*#\d+$", "", card_label(card)).strip()
    if info.base or not label:
        return info.collection
    if info.collection == BASE_HEROES:
        return f"{BASE_HEROES} · Prime"

    def stem(word: str) -> str:
        return re.sub(r"(es|s)$", "", _fold(word).strip(":()"))

    known = {stem(word) for word in info.collection.split()} | _FILLER_WORDS
    extra = [word for word in label.split() if stem(word) not in known]
    detail = " ".join(extra).replace("(", "").replace(")", "").strip(" :-")
    return f"{info.collection} · {detail}" if detail else info.collection


# FC26 re-release windows.  FUT.GG keeps the original promo name on these
# cards, so only the release date tells them apart (verified against the
# complete FC26 dump: squad 572 on 2026-08-21, Pre-Season from 2026-08-28).
_REWIND_DAYS = ("2026-08-21", "2026-08-27")
_PRE_SEASON_FROM = "2026-08-28"


def campaign_label(row: Mapping[str, Any]) -> str:
    """FUT.GG label with the FC26 Ultimate Rewind / Pre-Season campaign made explicit.

    Ultimate Rewind re-released past promo designs with upgraded stats and
    Pre-Season continued the FUTTIES design, so the raw rarity name alone
    would file them under the older campaign.
    """
    label = str(row.get("revision") or row.get("rarity") or "")
    created = str(row.get("createdAt") or "")[:10]
    if card_year(row) != 26 or not created or not label:
        return label
    family = label_class(label).family
    if family == "FUTTIES" and created >= _PRE_SEASON_FROM:
        return f"Pre-Season {label}"
    if family in PROMO_CAMPAIGNS and family != "FUTTIES" and (
        _REWIND_DAYS[0] <= created <= _REWIND_DAYS[1] or str(row.get("raritySquadId")) == "572"
    ):
        return f"Ultimate Rewind {label}"
    return label


def _marks(values: Sequence[Any]) -> str:
    return ",".join("?" for _ in values) or "NULL"


def _quoted(values: Iterable[str]) -> tuple[str, list[str]]:
    items = sorted(set(values))
    return _marks(items), items




def sql_filter(
    labels: Iterable[str],
    *,
    collection: str = "",
    kind: str = "",
    base_only: bool = False,
    label_col: str,
    ovr_col: str,
    club_col: str,
) -> tuple[str, list[Any]]:
    """SQL predicate equal to :func:`matches` over the Library's distinct labels.

    ``labels`` is every label present in the table, so the IN lists are exact
    and the row limit applies after filtering, not before it.
    """
    infos = {label: label_class(label) for label in labels}
    clauses: list[str] = []
    args: list[Any] = []
    club = f"upper(COALESCE({club_col},''))"

    def label_in(predicate: Any) -> str:
        marks, values = _quoted(label for label, info in infos.items() if predicate(label, info))
        args.extend(values)
        return f"{label_col} IN ({marks})"

    def kind_clause(wanted: str) -> str:
        # Placeholders are appended in the order the SQL text uses them.
        if wanted == KIND_PLAYER:
            unknown = label_in(lambda _l, info: not info.kind)
            clubs = ICON_CLUBS + HERO_CLUBS
            args.extend(clubs)
            return f"({unknown} AND {club} NOT IN ({_marks(clubs)}))"
        explicit = label_in(lambda _l, info: info.kind == wanted)
        unknown = label_in(lambda _l, info: not info.kind)
        clubs = ICON_CLUBS if wanted == KIND_ICON else HERO_CLUBS
        args.extend(clubs)
        return f"({explicit} OR ({unknown} AND {club} IN ({_marks(clubs)})))"

    if base_only:
        clauses.append(label_in(lambda label, info: info.base and not is_holographic(label)))
    if collection in (BASE_GOLD, BASE_SILVER, BASE_BRONZE):
        tier = collection.split()[-1]
        labelled = label_in(lambda _l, info: info.family == _BASE_PLAYER and info.tier == tier)
        untiered = label_in(lambda _l, info: info.family == _BASE_PLAYER and not info.tier)
        low, high = {"Gold": (75, 99), "Silver": (65, 74), "Bronze": (0, 64)}[tier]
        args.extend((low, high))
        clauses.append(f"({labelled} OR ({untiered} AND COALESCE({ovr_col},0) BETWEEN ? AND ?))")
        clauses.append(kind_clause(KIND_PLAYER))
    elif collection in (BASE_ICONS, BASE_HEROES):
        own = label_in(lambda _l, info: info.family == collection)
        plain = label_in(lambda _l, info: info.family == _BASE_PLAYER)
        clubs = ICON_CLUBS if collection == BASE_ICONS else HERO_CLUBS
        args.extend(clubs)
        clauses.append(f"({own} OR ({plain} AND {club} IN ({_marks(clubs)})))")
    elif collection:
        clauses.append(label_in(lambda _l, info: info.family == collection))
    if kind:
        clauses.append(kind_clause(kind))
    return " AND ".join(clauses), args
