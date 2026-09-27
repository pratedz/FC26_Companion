"""Career squad Grok session — Python owns filters, ladder, and cohesion.

Grok may suggest who belongs where and how a player should feel. This module
re-applies numeric scope, ranks Face/Star/Rotation/Prospect, clamps target
OVRs, and sanitizes per-player patches before anything is staged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any, Mapping, Sequence

from .builds import expand_overall_patch, is_ratings_only_patch
from .card_import import EA_DATE_EPOCH, FC26_SEASON_START, forced_birthdate_for_age
from .job import JobValidationError
from .player import FIELD_SPECS, normalize_patch, player_id
from .squad_plan import MAX_OVERRIDE_FIELDS, MAX_PLAYERS, SquadMember, SquadPlan


LIVE_VALUE_FLOOR = 10
PROPOSE_CHUNK = 5
RUNGS: tuple[str, ...] = ("face", "star", "rotation", "prospect")
RUNG_RANK = {name: index for index, name in enumerate(RUNGS)}
INTENTS = frozenset({"lift_band", "prime", "overstat", "relative", "custom"})
FAMILIES = frozenset({"attrs", "playstyles", "age", "skills", "identity", "body"})

IDENTITY_FIELDS = frozenset(
    {
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid",
        "headassetid", "headclasscode", "headtypecode", "headvariation",
        "hashighqualityhead", "hairtypecode", "haircolorcode", "hairstylecode",
        "facialhairtypecode", "facialhaircolorcode", "eyebrowcode",
        "eyecolorcode", "eyedetail", "skintonecode", "skintypecode",
        "skincomplexion", "skinmakeup", "skinsurfacepack", "sideburnscode",
        "lipcolor", "faceposerpreset", "facepsdlayer0", "facepsdlayer1",
        "nationality", "gender", "usercaneditname", "avatarpomid",
        "tattoohead", "tattoofront", "tattooback", "tattooleftarm",
        "tattoorightarm", "tattooleftleg", "tattoorightleg",
    }
)
GK_FIELDS = frozenset(
    {
        "gkdiving", "gkhandling", "gkkicking", "gkpositioning", "gkreflexes",
        "gkkickstyle", "gksavetype", "gkglovetypecode",
    }
)
CONTRACT_FIELDS = frozenset(
    {"contractvaliduntil", "isretiring", "iscustomized", "playerid"}
)
PLAYSTYLE_PLUS_FIELDS = ("icontrait1", "icontrait2")
DEFENDER_FINISHING = frozenset(
    {"finishing", "volleys", "shotpower", "longshots", "penalties", "positioning"}
)

_FC_POS: dict[int, str] = {
    0: "GK", 2: "RWB", 3: "RB", 4: "RCB", 5: "CB", 6: "LCB", 7: "LB", 8: "LWB",
    9: "RDM", 10: "CDM", 11: "LDM", 12: "RM", 13: "RCM", 14: "CM", 15: "LCM",
    16: "LM", 17: "RAM", 18: "CAM", 19: "LAM", 20: "RF", 21: "CF", 22: "LF",
    23: "RW", 24: "RS", 25: "ST", 26: "LS", 27: "LW",
}
_GROUP_POS: dict[str, frozenset[str]] = {
    "gk": frozenset({"gk"}),
    "def": frozenset({"cb", "lb", "rb", "lwb", "rwb", "lcb", "rcb", "sw", "wb"}),
    "mid": frozenset(
        {
            "cm", "cdm", "cam", "lm", "rm", "lcm", "rcm", "ldm", "rdm",
            "lam", "ram", "dm", "am",
        }
    ),
    "fwd": frozenset({"st", "cf", "lw", "rw", "lf", "rf", "ls", "rs"}),
}
_ATTR_ALIASES: dict[str, tuple[str, ...]] = {
    "pace": ("acceleration", "sprintspeed"),
    "speed": ("acceleration", "sprintspeed"),
    "shooting": ("finishing", "shotpower", "longshots"),
    "passing": ("shortpassing", "longpassing", "vision"),
    "dribbling": ("dribbling", "ballcontrol", "agility"),
    "defending": ("defensiveawareness", "standingtackle", "interceptions"),
    "physical": ("strength", "stamina"),
}

_RANGE_RE = re.compile(
    r"(?:between\s+)?(\d{2})\s*(?:-|–|to|and)\s*(\d{2})",
    re.IGNORECASE,
)
_TO_RE = re.compile(
    r"\b(?:into|to|at|of)\s+(\d{2})\b",
    re.IGNORECASE,
)
_AROUND_RE = re.compile(r"\b(around|about|approx)\b", re.IGNORECASE)
_REL_RE = re.compile(
    r"\+(\d{1,2})\s*([a-zA-Z]+)",
)
_UNDER_RE = re.compile(r"\bunder\s+(\d{2})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NAME_WORD = r"(?!(?:overall|ovr)\b)[a-z0-9][a-z0-9.'’\-]{0,24}"
_NAMED_OVR_BEFORE = re.compile(
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})"
    r"\s+(?:overall|ovr)\s+(?P<ovr>\d{2})\b",
    re.IGNORECASE,
)
_NAMED_OVR_AFTER = re.compile(
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})"
    r"\s+(?P<ovr>\d{2})\s*(?:overall|ovr)\b",
    re.IGNORECASE,
)
_JERSEY_NAME_FIRST = re.compile(
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})"
    r"\s+(?:jersey|shirt)(?:\s+(?:no\.?|number))?\s*(?P<num>\d{1,2})\b",
    re.IGNORECASE,
)
_JERSEY_NUMBER_WORD = re.compile(
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})"
    r"\s+(?:number|#)\s*(?P<num>\d{1,2})\b",
    re.IGNORECASE,
)
_JERSEY_NUM_FIRST = re.compile(
    r"(?:jersey|shirt)(?:\s+(?:no\.?|number))?\s*(?P<num>\d{1,2})\s+(?:for|to)\s+"
    rf"(?P<name>{_NAME_WORD}(?:\s+{_NAME_WORD}){{0,3}})",
    re.IGNORECASE,
)
# Well-known squad shirts. Used only when the ask is "arrange / recommend
# jersey numbers" and the user did not type a number for that player.
_FAMOUS_SHIRTS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("salah",), 11),
    (("gerrard",), 8),
    (("torres",), 9),
    (("dijk",), 4),
    (("robertson",), 26),
    (("trent",), 66),
    (("arnold",), 66),
    (("messi",), 10),
    (("maldini",), 3),
    (("ronaldinho",), 10),
    (("buffon",), 1),
    (("pele",), 10),
    (("maradona",), 10),
)
_NAME_FILLER = frozenset(
    {
        "make", "set", "give", "put", "bump", "raise", "get", "turn", "change",
        "edit", "and", "then", "to", "a", "an", "the", "our", "him", "her",
        "his", "player", "players", "into", "at", "of", "overall", "ovr",
        "with", "for", "please", "want", "let", "also", "plus", "them", "all",
        "everyone", "every", "both", "each", "these", "those", "this", "that",
        "squad", "team", "whole", "their", "my",
        "arrange", "assign", "recommend", "recommended", "recomended",
        "improve", "playstyle", "jersey", "shirt", "number", "no",
    }
)
# Tokens that are not substrings of the career name.
_NICKNAMES: dict[str, tuple[str, ...]] = {
    "cr7": ("cristiano", "ronaldo"),
    "lm10": ("messi",),
    "m10": ("messi",),
    "kdb": ("bruyne",),
    "vini": ("vinicius",),
    "vinijr": ("vinicius",),
}


@dataclass(frozen=True, slots=True)
class RosterPlayer:
    playerid: int
    name: str
    position: str
    ovr: int | None = None
    pot: int | None = None
    jersey: int | None = None

    @property
    def is_gk(self) -> bool:
        return position_group(self.position) == "gk"

    @property
    def pos_key(self) -> str:
        return _pos_token(self.position)


@dataclass(frozen=True, slots=True)
class RelativeOp:
    fields: tuple[str, ...]
    delta: int
    below: int | None = None


@dataclass(frozen=True, slots=True)
class SquadRecipe:
    prompt: str
    intent: str = "custom"
    club_name: str = ""
    ovr_min: int | None = None
    ovr_max: int | None = None
    target_ovr: int | None = None
    band_lo: int | None = None
    band_hi: int | None = None
    soft_band: bool = False
    positions: tuple[str, ...] = ()
    include_gk: bool = True
    families: tuple[str, ...] = ("attrs",)
    protect_top11: bool = False
    allow_icon_traits: bool = False
    # "no limit" — do not trim PlayStyle+ down to the overall ladder.
    unlimited_playstyles: bool = False
    allow_nerf: bool = False
    relative_ops: tuple[RelativeOp, ...] = ()
    names: tuple[str, ...] = ()
    # Explicit "cr7 overall 88" pairs. Python pins these; Codex cannot collapse them.
    named_overalls: tuple[tuple[str, int], ...] = ()
    # Explicit "messi jersey 10" pairs. Python assigns these and moves clashes.
    named_jerseys: tuple[tuple[str, int], ...] = ()

    @property
    def wants_age(self) -> bool:
        return "age" in self.families

    @property
    def wants_playstyles(self) -> bool:
        return "playstyles" in self.families

    @property
    def wants_playstyle_plus(self) -> bool:
        return self.wants_playstyles and bool(
            self.unlimited_playstyles or self.allow_icon_traits
            or re.search(r"play\s*styles?\s*(?:\+|plus)", self.prompt, re.I)
        )

    @property
    def wants_identity(self) -> bool:
        return "identity" in self.families

    @property
    def wants_body(self) -> bool:
        return "body" in self.families

    @property
    def wants_skills(self) -> bool:
        return "skills" in self.families

    @property
    def effective_band(self) -> tuple[int, int] | None:
        if self.band_lo is not None and self.band_hi is not None:
            lo, hi = sorted((int(self.band_lo), int(self.band_hi)))
            return lo, hi
        if self.target_ovr is not None:
            target = int(self.target_ovr)
            if self.soft_band:
                return max(1, target - 1), min(99, target + 1)
            return target, target
        return None


@dataclass(frozen=True, slots=True)
class SquadTarget:
    playerid: int
    name: str
    position: str
    ovr: int | None
    pot: int | None
    target_ovr: int
    rung: str
    why: str
    pinned: bool = False
    library_hint: str = ""

    @property
    def is_gk(self) -> bool:
        return position_group(self.position) == "gk"


@dataclass(frozen=True, slots=True)
class SquadSession:
    recipe: SquadRecipe
    targets: tuple[SquadTarget, ...]
    chips: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    truncated: bool = False
    protected_ids: tuple[int, ...] = ()

    @property
    def playerids(self) -> tuple[int, ...]:
        return tuple(item.playerid for item in self.targets)

    def target_for(self, playerid: int) -> SquadTarget | None:
        pid = int(playerid)
        for item in self.targets:
            if item.playerid == pid:
                return item
        return None


def _pos_token(raw: Any) -> str:
    if raw in (None, ""):
        return ""
    try:
        as_int = int(raw)
    except (TypeError, ValueError):
        text = str(raw).strip()
    else:
        text = _FC_POS.get(as_int, str(as_int))
    return text.casefold().replace(" ", "")


def position_group(raw: Any) -> str:
    token = _pos_token(raw)
    if not token:
        return ""
    if token in _GROUP_POS["gk"] or token.startswith("gk"):
        return "gk"
    for group, names in _GROUP_POS.items():
        if token in names:
            return group
    return ""


def is_defender(raw: Any) -> bool:
    return position_group(raw) == "def"


def roster_from_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[RosterPlayer, ...]:
    out: list[RosterPlayer] = []
    seen: set[int] = set()
    for row in rows:
        raw = dict(row.get("_raw") or row)
        try:
            pid = player_id(raw.get("playerid") or raw.get("id") or row.get("id"))
        except Exception:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        ovr = _int_or_none(raw.get("overallrating") or raw.get("ovr") or row.get("ovr"))
        pot = _int_or_none(raw.get("potential") or raw.get("pot") or row.get("pot"))
        jersey = _int_or_none(raw.get("jerseynumber") or raw.get("jersey") or row.get("jerseynumber"))
        out.append(
            RosterPlayer(
                playerid=pid,
                name=str(raw.get("name") or raw.get("playername") or row.get("name") or pid),
                position=str(
                    raw.get("position")
                    or raw.get("pos")
                    or row.get("pos")
                    or raw.get("preferredposition1")
                    or ""
                ),
                ovr=ovr,
                pot=pot,
                jersey=jersey if jersey and 1 <= jersey <= 99 else None,
            )
        )
    return tuple(out)


def roster_from_members(members: Sequence[SquadMember]) -> tuple[RosterPlayer, ...]:
    out: list[RosterPlayer] = []
    for member in members:
        pot = _int_or_none(member.base.get("potential"))
        out.append(
            RosterPlayer(
                playerid=member.playerid,
                name=member.name,
                position=member.position,
                ovr=member.ovr,
                pot=pot,
            )
        )
    return tuple(out)


def _fold_name(text: str) -> str:
    table = str.maketrans(
        {
            "á": "a", "à": "a", "â": "a", "ä": "a", "ã": "a",
            "é": "e", "è": "e", "ê": "e", "ë": "e",
            "í": "i", "ì": "i", "î": "i", "ï": "i",
            "ó": "o", "ò": "o", "ô": "o", "ö": "o", "õ": "o",
            "ú": "u", "ù": "u", "û": "u", "ü": "u",
            "ñ": "n", "ç": "c", "ø": "o", "æ": "ae",
            "'": "", "’": "", "-": " ", ".": " ",
        }
    )
    return " ".join(str(text or "").casefold().translate(table).split())


def _clean_name_label(raw: str) -> str:
    tokens: list[str] = []
    for tok in _fold_name(raw).split():
        if tok in _NAME_FILLER or tok.isdigit():
            if tokens:
                break
            continue
        tokens.append(tok)
    return " ".join(tokens)


def extract_named_overalls(prompt: str) -> tuple[tuple[str, int], ...]:
    """Pull ``name overall 88`` / ``name 88 overall`` pairs out of a Career ask."""
    text = prompt or ""
    found: list[tuple[int, str, int]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in (_NAMED_OVR_BEFORE, _NAMED_OVR_AFTER):
        for match in pattern.finditer(text):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            label = _clean_name_label(match.group("name"))
            if not label:
                continue
            try:
                ovr = int(match.group("ovr"))
            except (TypeError, ValueError):
                continue
            if not 1 <= ovr <= 99:
                continue
            occupied.append(span)
            found.append((span[0], label, ovr))
    found.sort(key=lambda item: item[0])
    return tuple((label, ovr) for _start, label, ovr in found)


def extract_named_jerseys(prompt: str) -> tuple[tuple[str, int], ...]:
    """Pull ``messi jersey 10`` / ``jersey 10 for messi`` pairs out of a Career ask."""
    text = prompt or ""
    found: list[tuple[int, str, int]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in (_JERSEY_NUM_FIRST, _JERSEY_NAME_FIRST, _JERSEY_NUMBER_WORD):
        for match in pattern.finditer(text):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            label = _clean_name_label(match.group("name"))
            if not label:
                continue
            try:
                number = int(match.group("num"))
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= 99:
                continue
            occupied.append(span)
            found.append((span[0], label, number))
    found.sort(key=lambda item: item[0])
    return tuple((label, number) for _start, label, number in found)


def assign_jersey_numbers(
    current: Mapping[int, int | None],
    requests: Sequence[tuple[int, int]],
    *,
    names: Mapping[int, str] | None = None,
) -> tuple[dict[int, int], tuple[str, ...]]:
    """Give requested shirts. A player already wearing one moves to the vacated number, or the lowest free shirt."""
    numbers: dict[int, int] = {}
    for pid, raw in current.items():
        try:
            num = int(raw or 0)
        except (TypeError, ValueError):
            num = 0
        numbers[int(pid)] = num if 1 <= num <= 99 else 0
    holder: dict[int, int] = {}
    for pid, num in numbers.items():
        if num:
            holder.setdefault(num, pid)
    locked: set[int] = set()
    changes: dict[int, int] = {}
    warnings: list[str] = []
    labels = dict(names or {})

    def label(pid: int) -> str:
        return labels.get(pid) or str(pid)

    def lowest_free() -> int | None:
        for number in range(1, 100):
            if number not in holder:
                return number
        return None

    def move(pid: int, new: int) -> None:
        old = numbers.get(pid, 0)
        if old and holder.get(old) == pid:
            holder.pop(old, None)
        numbers[pid] = new
        if new:
            holder[new] = pid
        if old != new:
            changes[pid] = new

    for pid, wanted in requests:
        if not 1 <= int(wanted) <= 99:
            warnings.append(f"{label(pid)}: shirt numbers must be 1–99.")
            continue
        wanted = int(wanted)
        numbers.setdefault(pid, 0)
        if numbers.get(pid) == wanted:
            locked.add(pid)
            continue
        occupant = holder.get(wanted)
        if occupant is not None and occupant != pid and occupant in locked:
            warnings.append(
                f"#{wanted} stays with {label(occupant)}; {label(pid)} was asked for it later."
            )
            continue
        vacated = numbers.get(pid, 0)
        if occupant is not None and occupant != pid:
            # Unknown shirts are stored as 0. Do not invent #1, #2, #3 for the
            # rest of the squad when the live list was never read.
            if not numbers.get(occupant):
                occupant = None
        if occupant is not None and occupant != pid:
            if holder.get(wanted) == occupant:
                holder.pop(wanted, None)
            numbers[occupant] = 0
            replacement = (
                vacated
                if vacated and vacated != wanted and holder.get(vacated) in (None, pid)
                else lowest_free()
            )
            if replacement is None:
                holder[wanted] = occupant
                numbers[occupant] = wanted
                warnings.append(f"No spare shirt for {label(occupant)}; {label(pid)} keeps their number.")
                continue
            move(occupant, replacement)
            warnings.append(
                f"{label(occupant)} moves from #{wanted} to #{replacement} so {label(pid)} can wear #{wanted}."
            )
        move(pid, wanted)
        locked.add(pid)
    return changes, tuple(warnings)


def _name_needles(label: str) -> tuple[str, ...]:
    folded = _fold_name(label)
    if folded in _NICKNAMES:
        return _NICKNAMES[folded]
    tokens = [tok for tok in folded.split() if tok not in _NAME_FILLER and len(tok) >= 2]
    if len(tokens) == 1 and tokens[0] in _NICKNAMES:
        return _NICKNAMES[tokens[0]]
    return tuple(tokens)


def _needle_hits(needle: str, hay_tokens: Sequence[str]) -> bool:
    if needle in hay_tokens:
        return True
    if len(needle) < 4:
        return False
    return any(tok.startswith(needle) or needle.startswith(tok) for tok in hay_tokens)


def resolve_named_overalls(
    pairs: Sequence[tuple[str, int]],
    roster: Sequence[RosterPlayer],
) -> tuple[dict[int, int], tuple[str, ...]]:
    """Map named overalls onto roster ids. Ambiguous names are left unmatched."""
    chosen: dict[int, int] = {}
    unmatched: list[str] = []
    for label, ovr in pairs:
        needles = _name_needles(label)
        if not needles:
            unmatched.append(label)
            continue
        hits: list[tuple[int, RosterPlayer]] = []
        for player in roster:
            hay_tokens = _fold_name(player.name).split()
            if all(_needle_hits(needle, hay_tokens) for needle in needles):
                exact = sum(1 for needle in needles if needle in hay_tokens)
                hits.append((exact, player))
        if not hits:
            unmatched.append(label)
            continue
        best = max(score for score, _player in hits)
        winners = [player for score, player in hits if score == best]
        if len(winners) > 1:
            last = needles[-1]
            surnames = [
                player for player in winners
                if _fold_name(player.name).split()[-1:] == [last]
            ]
            if len(surnames) == 1:
                winners = surnames
            else:
                unmatched.append(label)
                continue
        chosen[winners[0].playerid] = max(1, min(99, int(ovr)))
    return chosen, tuple(unmatched)


def plan_hint(
    rows: Sequence[Mapping[str, Any]],
    prompt: str,
    *,
    squad_count: int = 0,
) -> str:
    """One plain line for the Club box: who is ticked, and which overall stuck."""
    roster = roster_from_rows(rows)
    names = [player.name for player in roster]
    shown = ", ".join(names[:4])
    if len(names) > 4:
        shown += f" +{len(names) - 4}"
    text = (prompt or "").strip()
    shirts = extract_named_jerseys(text)
    if shirts and roster:
        chosen, unmatched = resolve_named_overalls(shirts, roster)
        by_id = {player.playerid: player.name for player in roster}
        bits = [f"{by_id[pid]} → #{number}" for pid, number in chosen.items() if pid in by_id]
        if bits:
            line = ", ".join(bits)
            if unmatched:
                line += ". Couldn't match " + ", ".join(unmatched)
            return (
                f"{line}. If that shirt is already taken, that player gets another number."
            )
    pairs = extract_named_overalls(text)
    if pairs and roster:
        chosen, unmatched = resolve_named_overalls(pairs, roster)
        by_id = {player.playerid: player.name for player in roster}
        bits = [f"{by_id[pid]} → {ovr}" for pid, ovr in chosen.items() if pid in by_id]
        if bits:
            line = ", ".join(bits)
            if unmatched:
                line += ". Couldn't match " + ", ".join(unmatched)
            return f"{line}. Plan writes each player's attributes and playstyles."
        if unmatched:
            return (
                "Couldn't match "
                + ", ".join(unmatched)
                + ". Use a name from the ticked players."
            )
    if text and names:
        return f"Codex will plan {shown} from that ask."
    if text:
        count = squad_count or 0
        return (
            f"Codex will plan the whole squad ({count}). "
            "Tick players to choose who."
        )
    if len(names) >= 2:
        return (
            f"{shown}. Plan opens Codex for these players. "
            "Type each overall here or in the plan. "
            "Example: cr7 overall 88, mahrez overall 85."
        )
    if len(names) == 1:
        return f"{shown} is ticked. Type an overall to plan them, or open the player."
    return "Tick the players you want. Then type each overall in the box."


def parse_recipe(prompt: str, club_name: str = "") -> SquadRecipe:
    text = (prompt or "").strip()
    folded = text.casefold()
    intent = "custom"
    if any(word in folded for word in ("overstat", "insane", "god squad")) or re.search(
        r"\b99\s*(ovr|overall)\b", folded
    ):
        intent = "overstat"
    elif any(word in folded for word in ("prime", "peak")) or re.search(r"\bera\b", folded) or _YEAR_RE.search(text):
        intent = "prime"
    elif _REL_RE.search(text) and "under" in folded:
        intent = "relative"
    elif _RANGE_RE.search(text) or "into" in folded:
        intent = "lift_band"

    ovr_min = ovr_max = target = band_lo = band_hi = None
    ranges = [(int(a), int(b)) for a, b in _RANGE_RE.findall(text)]
    into_hits = [int(m.group(1)) for m in _TO_RE.finditer(text)]
    around_hits = [
        int(m.group(1))
        for m in re.finditer(r"\b(?:around|about)\s+(\d{2})\b", folded)
    ]
    if ranges:
        first = ranges[0]
        lo, hi = sorted(first)
        # "75-80 into 84" vs "around 85-95"
        rest = ranges[1:]
        if rest:
            band_lo, band_hi = sorted(rest[0])
            ovr_min, ovr_max = lo, hi
        elif into_hits and into_hits[-1] not in {lo, hi}:
            ovr_min, ovr_max = lo, hi
            target = into_hits[-1]
        elif any(word in folded for word in ("prime", "around", "about", "overall")):
            band_lo, band_hi = lo, hi
            if intent == "custom":
                intent = "prime"
        else:
            ovr_min, ovr_max = lo, hi
            if into_hits:
                target = into_hits[-1]
    elif into_hits:
        target = into_hits[-1]
    elif around_hits:
        target = around_hits[-1]

    soft = bool(_AROUND_RE.search(text) or "prime" in folded or "around" in folded)
    if target is not None and not soft and band_lo is None:
        band_lo = band_hi = target
    if target is not None and soft and band_lo is None:
        band_lo, band_hi = max(1, target - 1), min(99, target + 1)
    if band_lo is not None and band_hi is not None and intent == "custom":
        intent = "prime" if "prime" in folded else "lift_band"

    named_overalls = extract_named_overalls(text)
    named_jerseys = extract_named_jerseys(text)
    families = ["attrs"]
    if named_overalls or any(
        word in folded for word in ("playstyle", "play style", "trait", "icon")
    ):
        families.append("playstyles")
    if (
        re.search(r"\b(age|younger|older)\b", folded)
        or re.search(r"\bera\b", folded)
        or _YEAR_RE.search(text)
    ):
        families.append("age")
    if (
        re.search(r"\b(physique|body|height|weight)\b", folded)
        or re.search(r"\bera\b", folded)
        or _YEAR_RE.search(text)
    ):
        families.append("body")
    if any(word in folded for word in ("skill move", "skillmoves", "weak foot", "5 star", "5*")):
        families.append("skills")
    if any(word in folded for word in ("face", "identity", "rename", "nationality", "name him")):
        families.append("identity")

    positions: list[str] = []
    if any(word in folded for word in ("midfield", "midfielder", "mids")):
        positions.append("mid")
    if any(word in folded for word in ("defence", "defense", "defender", "back line", "backline")):
        positions.append("def")
    if any(word in folded for word in ("attack", "forward", "striker", "front line")):
        positions.append("fwd")
    for token in ("st", "cf", "lw", "rw", "cm", "cam", "cdm", "cb", "lb", "rb"):
        if re.search(rf"\b{token}s?\b", folded):
            positions.append(token)

    # This roster is the user's selection. Keep its keepers unless excluded.
    include_gk = not bool(re.search(
        r"\b(exclude|skip|without|except|no)\s+(?:the\s+)?(?:gks?|keepers?|goalkeepers?)\b",
        folded,
    ))
    protect = bool(
        re.search(
            r"don'?t touch (the )?starters|protect( the)? starters|leave (the )?starters",
            folded,
        )
    )
    allow_icons = bool(re.search(r"all playstyle\+|icons|four playstyle", folded))
    # "no limit" is a spoken ceiling removal. The overall ladder stays on
    # unless this phrase is in the same ask.
    unlimited_playstyles = bool(
        re.search(
            r"\bno\s+limits?\b"
            r"|\bno\s+ceilings?\b"
            r"|\bunlimited\s+play\s*styles?\b"
            r"|\bno\s+limit\s+ceilings?\b",
            folded,
        )
    )
    if unlimited_playstyles and "playstyles" not in families:
        families.append("playstyles")
    allow_nerf = bool(re.search(r"\b(nerf|lower|downgrade|decrease)\b", folded))

    relative: list[RelativeOp] = []
    under = None
    under_match = _UNDER_RE.search(text)
    if under_match:
        under = int(under_match.group(1))
    for match in _REL_RE.finditer(text):
        delta = int(match.group(1))
        label = match.group(2).casefold()
        fields = _ATTR_ALIASES.get(label) or (
            (label,) if label in FIELD_SPECS else ()
        )
        if fields:
            relative.append(RelativeOp(fields=fields, delta=delta, below=under))
    if relative and intent == "custom":
        intent = "relative"

    return SquadRecipe(
        prompt=text,
        intent=intent if intent in INTENTS else "custom",
        club_name=str(club_name or "").strip(),
        ovr_min=ovr_min,
        ovr_max=ovr_max,
        target_ovr=target,
        band_lo=band_lo,
        band_hi=band_hi,
        soft_band=soft,
        positions=tuple(dict.fromkeys(positions)),
        include_gk=include_gk,
        families=tuple(dict.fromkeys(families)),
        protect_top11=protect,
        allow_icon_traits=allow_icons,
        unlimited_playstyles=unlimited_playstyles,
        allow_nerf=allow_nerf,
        relative_ops=tuple(relative),
        named_overalls=named_overalls,
        named_jerseys=named_jerseys,
    )


def merge_recipe(base: SquadRecipe, grok_recipe: Mapping[str, Any] | None) -> SquadRecipe:
    """Grok may fill taste fields; Python keeps numeric filters it already parsed."""
    if not grok_recipe:
        return base
    raw = dict(grok_recipe)
    intent = str(raw.get("intent") or base.intent).strip().casefold()
    if intent not in INTENTS:
        intent = base.intent
    positions = tuple(
        str(item).strip().casefold()
        for item in (raw.get("positions") or base.positions)
        if str(item).strip()
    ) or base.positions
    families = tuple(
        str(item).strip().casefold()
        for item in (raw.get("families") or base.families)
        if str(item).strip().casefold() in FAMILIES
    ) or base.families
    return replace(
        base,
        intent=intent,
        ovr_min=_int_or_none(raw.get("ovr_min"), base.ovr_min),
        ovr_max=_int_or_none(raw.get("ovr_max"), base.ovr_max),
        target_ovr=_int_or_none(raw.get("target_ovr"), base.target_ovr),
        band_lo=_int_or_none(raw.get("band_lo"), base.band_lo),
        band_hi=_int_or_none(raw.get("band_hi"), base.band_hi),
        soft_band=bool(raw.get("soft_band", base.soft_band)),
        positions=positions,
        include_gk=bool(raw.get("include_gk", base.include_gk)),
        families=families,
        protect_top11=bool(raw.get("protect_top11", base.protect_top11)),
        allow_icon_traits=bool(raw.get("allow_icon_traits", base.allow_icon_traits)),
        unlimited_playstyles=base.unlimited_playstyles,
        allow_nerf=bool(raw.get("allow_nerf", base.allow_nerf)),
    )


def lock_python_scope(original: SquadRecipe, merged: SquadRecipe) -> SquadRecipe:
    """Keep numeric filters and position scope that Python parsed from the prompt."""
    updates: dict[str, Any] = {}
    if original.ovr_min is not None:
        updates["ovr_min"] = original.ovr_min
        updates["ovr_max"] = original.ovr_max
    if original.target_ovr is not None and not original.soft_band:
        updates["target_ovr"] = original.target_ovr
        updates["band_lo"] = original.band_lo
        updates["band_hi"] = original.band_hi
        updates["soft_band"] = False
    if original.positions:
        updates["positions"] = original.positions
    if original.relative_ops:
        updates["relative_ops"] = original.relative_ops
    if original.protect_top11:
        updates["protect_top11"] = True
    updates["include_gk"] = original.include_gk
    # Python owns families. Grok must not add age/body/identity from "prime".
    updates["families"] = original.families
    updates["unlimited_playstyles"] = original.unlimited_playstyles
    if not updates:
        return merged
    return replace(merged, **updates)


def filter_roster(
    roster: Sequence[RosterPlayer],
    recipe: SquadRecipe,
) -> tuple[tuple[RosterPlayer, ...], tuple[int, ...], bool]:
    """Apply Python scope. Returns (kept, protected_ids, truncated)."""
    kept = list(roster)
    if recipe.ovr_min is not None:
        kept = [p for p in kept if p.ovr is not None and p.ovr >= recipe.ovr_min]
    if recipe.ovr_max is not None:
        kept = [p for p in kept if p.ovr is not None and p.ovr <= recipe.ovr_max]
    if recipe.positions:
        want = set(recipe.positions)
        kept = [
            p for p in kept
            if p.pos_key in want or position_group(p.position) in want
        ]
    if recipe.names:
        needles = tuple(name.casefold() for name in recipe.names)
        kept = [
            p for p in kept
            if any(needle in p.name.casefold() for needle in needles)
        ]
    if not recipe.include_gk:
        kept = [p for p in kept if not p.is_gk]

    protected: list[int] = []
    if recipe.protect_top11:
        ranked = sorted(roster, key=lambda p: (-(p.ovr or 0), p.name.casefold()))
        protected = [p.playerid for p in ranked[:11]]
        block = set(protected)
        kept = [p for p in kept if p.playerid not in block]

    truncated = False
    if len(kept) > MAX_PLAYERS:
        kept = sorted(kept, key=lambda p: (-(p.ovr or 0), p.name.casefold()))[:MAX_PLAYERS]
        truncated = True
    return tuple(kept), tuple(protected), truncated


def assign_rungs(players: Sequence[RosterPlayer]) -> dict[int, str]:
    if not players:
        return {}
    ovrs = sorted(p.ovr or 0 for p in players)
    median = ovrs[len(ovrs) // 2]
    max_ovr = ovrs[-1]
    rungs: dict[int, str] = {}
    for player in players:
        ovr = player.ovr or 0
        gap = (player.pot or ovr) - ovr
        if ovr == max_ovr:
            rungs[player.playerid] = "face"
        elif gap >= 6 and ovr <= median:
            rungs[player.playerid] = "prospect"
        elif ovr >= max_ovr - 4:
            rungs[player.playerid] = "star"
        else:
            rungs[player.playerid] = "rotation"
    if "face" not in rungs.values() and players:
        top = max(players, key=lambda p: (p.ovr or 0, p.pot or 0))
        rungs[top.playerid] = "face"
    return rungs


def spread_target(rung: str, recipe: SquadRecipe, current: int | None) -> int:
    band = recipe.effective_band
    if band is None:
        return max(1, min(99, int(current or 75)))
    lo, hi = band
    if lo == hi:
        return lo
    frac = {"face": 1.0, "star": 0.66, "rotation": 0.33, "prospect": 0.0}.get(rung, 0.5)
    return max(lo, min(hi, lo + int(round((hi - lo) * frac))))


def build_session(
    roster: Sequence[RosterPlayer],
    recipe: SquadRecipe,
    *,
    grok_targets: Sequence[Mapping[str, Any]] | None = None,
) -> SquadSession:
    named_map, unmatched = resolve_named_overalls(recipe.named_overalls, roster)
    if named_map:
        families = tuple(dict.fromkeys((*recipe.families, "playstyles")))
        lowered = any(
            (player.ovr or 0) > named_map[player.playerid]
            for player in roster
            if player.playerid in named_map
        )
        recipe = replace(
            recipe,
            families=families,
            target_ovr=None,
            band_lo=None,
            band_hi=None,
            soft_band=False,
            ovr_min=None,
            ovr_max=None,
            positions=(),
            allow_nerf=bool(recipe.allow_nerf or lowered),
            include_gk=bool(
                recipe.include_gk
                or any(player.is_gk for player in roster if player.playerid in named_map)
            ),
        )
        kept = tuple(player for player in roster if player.playerid in named_map)
        protected: tuple[int, ...] = ()
        truncated = False
    else:
        kept, protected, truncated = filter_roster(roster, recipe)
    rungs = assign_rungs(kept)
    grok_by_id: dict[int, Mapping[str, Any]] = {}
    for item in grok_targets or ():
        if not isinstance(item, Mapping):
            continue
        try:
            grok_by_id[int(item.get("playerid"))] = item
        except (TypeError, ValueError):
            continue

    targets: list[SquadTarget] = []
    for player in kept:
        rung = rungs.get(player.playerid, "rotation")
        hint = grok_by_id.get(player.playerid, {})
        grok_rung = str(hint.get("rung") or "").strip().casefold()
        if grok_rung in RUNG_RANK and grok_rung == rung:
            rung = grok_rung
        pinned = player.playerid in named_map
        if pinned:
            target = named_map[player.playerid]
            why = f"{player.name}: overall pinned at {target}"
        else:
            target = spread_target(rung, recipe, player.ovr)
            grok_ovr = _int_or_none(hint.get("target_ovr"))
            if grok_ovr is not None:
                target = _clamp_to_band(grok_ovr, recipe)
            why = str(hint.get("why") or "").strip() or _default_why(player, rung, target, recipe)
        targets.append(
            SquadTarget(
                playerid=player.playerid,
                name=player.name,
                position=player.position,
                ovr=player.ovr,
                pot=player.pot,
                target_ovr=target,
                rung=rung,
                why=why,
                pinned=pinned,
            )
        )
    targets = ladder_lock(targets, recipe)
    chips = recipe_chips(recipe, targets, truncated=truncated, protected=protected)
    warnings: list[str] = []
    if unmatched:
        warnings.append("Couldn't match: " + ", ".join(unmatched))
    if not targets:
        warnings.append("Nobody in this squad matches that ask.")
    if truncated:
        warnings.append(f"Capped at {MAX_PLAYERS} players (highest overall first).")
    if protected:
        warnings.append("Protected the highest-OVR 11 as a stand-in starting XI.")
    return SquadSession(
        recipe=recipe,
        targets=tuple(targets),
        chips=tuple(chips),
        warnings=tuple(warnings),
        truncated=truncated,
        protected_ids=protected,
    )


def refine_session(
    session: SquadSession,
    *,
    excluded_ids: Sequence[int] = (),
    pins: Mapping[int, int] | None = None,
) -> SquadSession:
    block = {int(pid) for pid in excluded_ids}
    pin_map = {int(pid): _clamp_to_band(int(ovr), session.recipe) for pid, ovr in (pins or {}).items()}
    kept: list[SquadTarget] = []
    for item in session.targets:
        if item.playerid in block:
            continue
        if item.playerid in pin_map:
            kept.append(
                replace(
                    item,
                    target_ovr=pin_map[item.playerid],
                    pinned=True,
                    why=item.why + " (pinned)",
                )
            )
        else:
            kept.append(item)
    locked = ladder_lock(kept, session.recipe)
    chips = recipe_chips(
        session.recipe,
        locked,
        truncated=session.truncated,
        protected=session.protected_ids,
        extra=(f"Pinned {len(pin_map)}" if pin_map else "",),
    )
    return replace(
        session,
        targets=tuple(locked),
        chips=tuple(chip for chip in chips if chip),
    )


def ladder_lock(
    targets: Sequence[SquadTarget],
    recipe: SquadRecipe,
) -> list[SquadTarget]:
    """Face cannot sit below Prospect. Python re-spreads if Grok inverted the ladder."""
    items = list(targets)
    if len(items) < 2:
        return items
    by_rung: dict[str, list[SquadTarget]] = {rung: [] for rung in RUNGS}
    for item in items:
        by_rung.setdefault(item.rung if item.rung in RUNG_RANK else "rotation", []).append(item)
    inverted = False
    last_min = 99
    for rung in RUNGS:
        group = by_rung.get(rung) or []
        if not group:
            continue
        group_max = max(item.target_ovr for item in group)
        if group_max > last_min:
            inverted = True
            break
        last_min = min(item.target_ovr for item in group)
    if not inverted:
        return items
    rebuilt: list[SquadTarget] = []
    for item in items:
        if item.pinned:
            rebuilt.append(item)
            continue
        rebuilt.append(
            replace(item, target_ovr=spread_target(item.rung, recipe, item.ovr))
        )
    return rebuilt


def recipe_chips(
    recipe: SquadRecipe,
    targets: Sequence[SquadTarget],
    *,
    truncated: bool = False,
    protected: Sequence[int] = (),
    extra: Sequence[str] = (),
) -> list[str]:
    chips: list[str] = []
    if recipe.ovr_min is not None and recipe.ovr_max is not None and recipe.target_ovr:
        chips.append(f"Lift {recipe.ovr_min}–{recipe.ovr_max} → {recipe.target_ovr}")
    elif recipe.effective_band:
        lo, hi = recipe.effective_band
        label = "Around" if recipe.soft_band else "Band"
        if lo == hi:
            chips.append(f"{'Around' if recipe.soft_band else 'To'} {lo}")
        else:
            chips.append(f"{label} {lo}–{hi}")
    if recipe.positions:
        chips.append(" · ".join(pos.upper() if len(pos) <= 3 else pos.title() for pos in recipe.positions))
    if not recipe.include_gk:
        chips.append("GK skipped")
    families = [name for name in recipe.families if name != "attrs"]
    chips.append("Attrs" + ((" + " + " + ".join(families)) if families else ""))
    if "identity" not in recipe.families:
        chips.append("No identity")
    if recipe.club_name and recipe.club_name.casefold() not in {"squad", ""}:
        chips.append(f"{recipe.club_name} identity")
    if recipe.protect_top11 or protected:
        chips.append("Protected: top 11 OVR")
    if truncated:
        chips.append(f"Capped at {MAX_PLAYERS}")
    if recipe.relative_ops:
        first = recipe.relative_ops[0]
        chips.append(f"+{first.delta} {first.fields[0]}")
    if recipe.unlimited_playstyles:
        chips.append("PlayStyle+ no ceiling")
    elif "playstyles" in recipe.families:
        chips.append("PlayStyle+ standard ceiling")
    if recipe.named_overalls:
        chips.append(
            " · ".join(f"{name} {ovr}" for name, ovr in recipe.named_overalls[:6])
        )
    chips.append(f"Will edit {len(targets)}")
    chips.extend(item for item in extra if item)
    return chips


def sanitize_patch(
    patch: Mapping[str, Any],
    *,
    recipe: SquadRecipe,
    target: SquadTarget,
    current: Mapping[str, Any],
) -> dict[str, int]:
    working = {str(k).lower(): v for k, v in dict(patch).items()}
    if not recipe.wants_identity:
        for name in IDENTITY_FIELDS:
            working.pop(name, None)
    if not recipe.wants_body:
        for name in ("height", "weight", "bodytypecode", "muscularitycode", "runstylecode"):
            working.pop(name, None)
    if not recipe.wants_playstyles:
        for name in ("trait1", "trait2", "icontrait1", "icontrait2"):
            working.pop(name, None)
    if not recipe.wants_skills:
        for name in ("skillmoves", "weakfootabilitytypecode", "skillmoveslikelihood"):
            working.pop(name, None)
    if not recipe.wants_age:
        working.pop("birthdate", None)
    if "internationalrep" in working and "rep" not in recipe.prompt.casefold():
        working.pop("internationalrep", None)
    for name in CONTRACT_FIELDS:
        working.pop(name, None)
    if not target.is_gk:
        for name in GK_FIELDS:
            working.pop(name, None)
    if is_defender(target.position) and recipe.intent != "overstat":
        asked_finishing = any(
            word in recipe.prompt.casefold()
            for word in ("finishing", "striker", "forward")
        )
        if not asked_finishing:
            live_fin = _int_or_none(current.get("finishing"))
            for name in DEFENDER_FINISHING:
                value = _int_or_none(working.get(name))
                if value is None:
                    continue
                cap = 82 if live_fin is None else max(live_fin, 82)
                if value > cap:
                    working[name] = cap

    working.setdefault("overallrating", target.target_ovr)
    if target.pinned:
        working["overallrating"] = target.target_ovr
    else:
        working["overallrating"] = _clamp_to_band(int(working["overallrating"]), recipe)

    if is_ratings_only_patch(working) and current:
        working = expand_overall_patch(current, working)

    if not recipe.allow_nerf:
        for name, value in list(working.items()):
            live = _int_or_none(current.get(name))
            if live is None or name in IDENTITY_FIELDS or name in GK_FIELDS:
                continue
            if name in FIELD_SPECS and FIELD_SPECS[name].maximum == 99 and int(value) < live:
                working[name] = live

    pot_live = _int_or_none(current.get("potential"))
    ovr_new = _int_or_none(working.get("overallrating")) or target.target_ovr
    pot_new = _int_or_none(working.get("potential")) or pot_live or ovr_new
    if target.rung == "prospect" and pot_live is not None and pot_live > (target.ovr or 0):
        pot_new = max(pot_new, ovr_new + 2, pot_live)
    working["potential"] = max(ovr_new, min(99, pot_new))
    working.setdefault("overallrating", target.target_ovr)

    if recipe.wants_age:
        working["birthdate"] = _peak_birthdate(target, current)

    if recipe.relative_ops:
        for op in recipe.relative_ops:
            for name in op.fields:
                live = _int_or_none(current.get(name))
                if live is None:
                    continue
                if op.below is not None and live >= op.below:
                    continue
                working[name] = max(1, min(99, live + op.delta))

    # Validate before a count cap can accidentally hide invalid high bits.
    from .playstyles import TRAIT_FIELDS
    for name in TRAIT_FIELDS:
        if name in working:
            working[name] = FIELD_SPECS[name].coerce(working[name])
    if not recipe.allow_icon_traits and not recipe.unlimited_playstyles:
        working = cap_playstyle_plus(
            working, max_plus=playstyle_plus_cap(target.target_ovr)
        )

    cleaned = normalize_patch(working)
    if len(cleaned) > MAX_OVERRIDE_FIELDS:
        # Keep ratings + the largest attribute deltas so overall rebuilds still apply.
        preferred = ["overallrating", "potential", "modifier"]
        if recipe.wants_playstyles:
            preferred.extend(["trait1", "trait2", "icontrait1", "icontrait2"])
        rest = [k for k in cleaned if k not in preferred]
        rest.sort(key=lambda k: abs(int(cleaned[k]) - (_int_or_none(current.get(k)) or int(cleaned[k]))), reverse=True)
        keep = preferred + rest
        cleaned = {k: cleaned[k] for k in keep[:MAX_OVERRIDE_FIELDS] if k in cleaned}
        cleaned = normalize_patch(cleaned)
    return cleaned


def playstyle_plus_cap(target_ovr: int) -> int:
    """PlayStyle+ bits a player may keep. Better overalls get more room."""
    ovr = max(1, min(99, int(target_ovr)))
    if ovr >= 91:
        return 5
    if ovr >= 88:
        return 4
    if ovr >= 85:
        return 3
    return 2


def cap_playstyle_plus(patch: Mapping[str, Any], *, max_plus: int = 2) -> dict[str, Any]:
    out = dict(patch)
    bits: list[tuple[str, int, int]] = []
    for field_name in PLAYSTYLE_PLUS_FIELDS:
        raw = _int_or_none(out.get(field_name))
        if raw is None or raw == 0:
            continue
        for bit in range(32):
            mask = 1 << bit
            if raw & mask:
                bits.append((field_name, mask, bit))
    if len(bits) <= max_plus:
        return out
    keep = bits[:max_plus]
    rebuilt = {name: 0 for name in PLAYSTYLE_PLUS_FIELDS if name in out}
    for field_name, mask, _bit in keep:
        rebuilt[field_name] = int(rebuilt.get(field_name, 0)) | mask
    out.update(rebuilt)
    return out


def cohesion_clamp(
    overrides: Mapping[int, Mapping[str, int]],
    session: SquadSession,
    *,
    current_by_id: Mapping[int, Mapping[str, Any]] | None = None,
) -> tuple[dict[int, dict[str, int]], tuple[str, ...]]:
    """Stop a prospect jumping the Face of the squad on a prime/lift ask."""
    out = {int(pid): dict(patch) for pid, patch in overrides.items()}
    warnings: list[str] = []
    if session.recipe.intent == "overstat":
        return out, tuple(warnings)
    face_targets = [item for item in session.targets if item.rung == "face"]
    face_ovr = 0
    for item in face_targets:
        staged = _int_or_none((out.get(item.playerid) or {}).get("overallrating"))
        face_ovr = max(face_ovr, staged or item.target_ovr)
    if not face_ovr:
        return out, tuple(warnings)
    for item in session.targets:
        patch = out.get(item.playerid)
        if not patch or item.pinned:
            continue
        staged = _int_or_none(patch.get("overallrating")) or item.target_ovr
        if item.rung in {"prospect", "rotation"} and staged > face_ovr:
            capped = max(1, face_ovr - 1)
            band = session.recipe.effective_band
            if band is not None:
                capped = max(band[0], min(band[1], capped))
            patch["overallrating"] = capped
            pot = _int_or_none(patch.get("potential")) or staged
            patch["potential"] = max(face_ovr, min(99, pot))
            warnings.append(
                f"{item.name}: overall clamped to {capped} so they stay below the Face of the squad."
            )
            if current_by_id and item.playerid in current_by_id:
                if is_ratings_only_patch(patch) or "acceleration" in patch:
                    patch.update(
                        expand_overall_patch(current_by_id[item.playerid], patch)
                    )
    return out, tuple(warnings)


def stage_per_player_plan(
    plan: SquadPlan,
    session: SquadSession,
    grok_patches: Mapping[int, Mapping[str, Any]],
    *,
    reasons: Mapping[int, str] | None = None,
    library_hints: Mapping[int, str] | None = None,
) -> SquadPlan:
    current_by_id = {m.playerid: dict(m.base) for m in plan.members}
    overrides: dict[int, dict[str, int]] = {}
    warnings: list[str] = list(session.warnings)
    blocking_issues: list[str] = []
    roster = roster_from_members(plan.members)
    current_shirts: dict[int, int | None] = dict(getattr(plan, "shirt_book", {}) or {})
    for member in plan.members:
        if member.playerid not in current_shirts:
            current_shirts[member.playerid] = _int_or_none(member.base.get("jerseynumber"))
    extra: list[tuple[int, int]] = []
    for pid, patch in grok_patches.items():
        number = _int_or_none(patch.get("jerseynumber"))
        try:
            target = session.target_for(int(pid))
        except (TypeError, ValueError):
            target = None
        if target is None:
            warnings.append(f"Ignored proposal for unselected player {pid}.")
            continue
        target_ovr = target.target_ovr if target is not None else None
        # Codex often copies the overall into the shirt. A typed "jersey 10"
        # still wins later; a suggested 87–90 does not.
        if number and 1 <= number <= 99 and number != target_ovr and number < 80:
            extra.append((int(pid), number))
    jersey_changes, jersey_notes = plan_jersey_changes(
        session.recipe,
        roster,
        current_shirts,
        names=dict(getattr(plan, "shirt_names", {}) or {}),
        extra_requests=extra,
    )
    warnings.extend(jersey_notes)
    if jersey_only(session.recipe):
        if not jersey_changes:
            detail = " ".join(warnings) or "No shirt numbers to change."
            raise JobValidationError(detail)
        return plan.with_overrides(
            {},
            source="grok",
            label="Shirt numbers",
            summary=session.recipe.prompt,
            warnings=tuple(dict.fromkeys(warnings)),
            reasons={int(k): str(v) for k, v in (reasons or {}).items() if v},
            library_hints={int(k): str(v) for k, v in (library_hints or {}).items() if v},
            chips=session.chips + tuple(
                f"#{number}" for number in list(jersey_changes.values())[:4]
            ),
            jersey_numbers=jersey_changes,
        )
    for member in plan.members:
        target = session.target_for(member.playerid)
        if target is None:
            continue
        raw = dict(grok_patches.get(member.playerid) or {})
        # Shirt proposals are already validated/resolved as squad-link writes.
        # They are not player attributes and must not reject the whole patch.
        raw.pop("jerseynumber", None)
        if not raw:
            raw = {"overallrating": target.target_ovr, "potential": max(target.target_ovr, target.pot or target.target_ovr)}
        try:
            cleaned = sanitize_patch(
                raw,
                recipe=session.recipe,
                target=target,
                current=member.base,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{member.name}: {exc}")
            if session.recipe.wants_playstyle_plus:
                blocking_issues.append(f"{member.name}: rebuild the rejected PlayStyle+ proposal ({exc}).")
            continue
        overrides[member.playerid] = cleaned
        if session.recipe.wants_playstyle_plus:
            from .playstyles import plus_names
            if not plus_names(cleaned):
                blocking_issues.append(f"{member.name}: no PlayStyle+ proposed. Rebuild this plan.")
        if session.recipe.wants_playstyles and not any(
            key in cleaned for key in ("trait1", "trait2", "icontrait1", "icontrait2")
        ):
            warnings.append(
                f"{member.name}: attributes updated; Codex did not return playstyles."
            )
    overrides, cohesion_notes = cohesion_clamp(
        overrides, session, current_by_id=current_by_id
    )
    warnings.extend(cohesion_notes)
    identity_n = sum(
        1 for patch in overrides.values() for key in patch if key in IDENTITY_FIELDS
    )
    hint_map = {int(k): str(v) for k, v in (library_hints or {}).items() if v}
    reason_map = {int(k): str(v) for k, v in (reasons or {}).items() if v}
    for item in session.targets:
        if item.playerid not in reason_map:
            reason_map[item.playerid] = item.why
        if item.library_hint:
            hint_map.setdefault(item.playerid, item.library_hint)
    return plan.with_overrides(
        overrides,
        source="grok",
        label="Per-player Grok session",
        summary=session.recipe.prompt,
        warnings=tuple(dict.fromkeys(warnings)),
        reasons=reason_map,
        library_hints=hint_map,
        chips=session.chips,
        identity_count=identity_n,
        jersey_numbers=jersey_changes,
        blocking_issues=blocking_issues,
    )


def compact_current(values: Mapping[str, Any]) -> dict[str, Any]:
    keep = {
        "overallrating", "potential", "modifier", "preferredposition1",
        "acceleration", "sprintspeed", "positioning", "finishing", "shotpower",
        "longshots", "volleys", "penalties", "vision", "crossing",
        "freekickaccuracy", "shortpassing", "longpassing", "curve", "agility",
        "balance", "reactions", "ballcontrol", "dribbling", "composure",
        "interceptions", "headingaccuracy", "defensiveawareness",
        "standingtackle", "slidingtackle", "jumping", "stamina", "strength",
        "aggression", "gkdiving", "gkhandling", "gkkicking", "gkpositioning",
        "gkreflexes", "trait1", "trait2", "icontrait1", "icontrait2",
        "jerseynumber",
        "skillmoves", "weakfootabilitytypecode", "birthdate", "height",
        "weight", "bodytypecode", "runstylecode", "internationalrep",
    }
    out: dict[str, Any] = {}
    for key, value in values.items():
        name = str(key).lower()
        if name in keep and value not in (None, ""):
            out[name] = value
    return out


def club_identity_note(club_name: str) -> str:
    club = (club_name or "").strip()
    if not club or club.casefold() == "squad":
        return ""
    return (
        f"Keep {club}'s football identity in the relative attribute shape "
        "(a possession side biases passing/dribbling up from each live snapshot; "
        "a direct side biases pace/physical). Defenders stay defenders."
    )


def needs_live_base(member: SquadMember) -> bool:
    return len(member.base) < LIVE_VALUE_FLOOR


def relative_only(recipe: SquadRecipe) -> bool:
    return recipe.intent == "relative" and bool(recipe.relative_ops) and recipe.effective_band is None


def jersey_only(recipe: SquadRecipe) -> bool:
    """A shirt-number ask should not invent overalls for the rest of the squad."""
    return (
        bool(recipe.named_jerseys)
        and not recipe.wants_playstyles
        and not recipe.wants_age
        and not recipe.wants_body
        and not recipe.wants_skills
        and not recipe.wants_identity
        and recipe.intent == "custom"
        and not recipe.named_overalls
        and recipe.effective_band is None
        and not recipe.relative_ops
    )


def wants_recommended_shirts(prompt: str) -> bool:
    """True when the ask is to arrange shirts, not to type one number."""
    folded = (prompt or "").casefold()
    if not re.search(r"\bjerseys?\b|\bshirts?\b", folded):
        return False
    return bool(re.search(r"\b(arrange|assign|recommend|recommended|recomended|famous|pick)\b", folded))


def recommended_shirt_requests(
    roster: Sequence[RosterPlayer],
    locked: set[int],
) -> list[tuple[int, int]]:
    """Famous squad shirts for selected players who were not given an explicit number."""
    requests: list[tuple[int, int]] = []
    for needles, number in _FAMOUS_SHIRTS:
        hits = [
            player for player in roster
            if player.playerid not in locked
            and all(_needle_hits(needle, _fold_name(player.name).split()) for needle in needles)
        ]
        if len(hits) != 1:
            continue
        requests.append((hits[0].playerid, number))
        locked.add(hits[0].playerid)
    return requests


def plan_jersey_changes(
    recipe: SquadRecipe,
    roster: Sequence[RosterPlayer],
    current: Mapping[int, int | None],
    *,
    names: Mapping[int, str] | None = None,
    extra_requests: Sequence[tuple[int, int]] = (),
) -> tuple[dict[int, int], tuple[str, ...]]:
    """Resolve typed shirt requests, then any extra requests that do not override them."""
    chosen, unmatched = resolve_named_overalls(recipe.named_jerseys, roster)
    warnings = [f"Couldn't match shirt for {label}." for label in unmatched]
    labels = {player.playerid: player.name for player in roster}
    labels.update(dict(names or {}))
    requests = list(chosen.items())
    locked = {pid for pid, _number in requests}
    for pid, number in extra_requests:
        if pid not in locked and 1 <= int(number) <= 99:
            requests.append((int(pid), int(number)))
            locked.add(int(pid))
    if wants_recommended_shirts(recipe.prompt):
        requests.extend(recommended_shirt_requests(roster, locked))
    changes, moved = assign_jersey_numbers(current, requests, names=labels)
    return changes, tuple(warnings) + moved


def propose_payload(
    plan: SquadPlan,
    session: SquadSession,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member in plan.members:
        target = session.target_for(member.playerid)
        if target is None:
            continue
        rows.append(
            {
                "playerid": member.playerid,
                "name": member.name,
                "position": member.position,
                "rung": target.rung,
                "target_ovr": target.target_ovr,
                "pinned": target.pinned,
                "playstyle_plus_max": (
                    None if session.recipe.unlimited_playstyles
                    else 8 if session.recipe.allow_icon_traits
                    else playstyle_plus_cap(target.target_ovr)
                ),
                "current_values": compact_current(member.base),
                "library_hint": target.library_hint,
            }
        )
    return rows


def parse_grok_targets(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            out.append(dict(item))
    return out


def _peak_birthdate(target: SquadTarget, current: Mapping[str, Any]) -> int:
    rung_age = {"face": 28, "star": 27, "rotation": 26, "prospect": 19}.get(target.rung, 27)
    live_age = _age_from_birthdate(current.get("birthdate"))
    if target.rung == "prospect":
        age = min(rung_age, live_age or rung_age)
    elif live_age is not None and 26 <= live_age <= 30:
        age = live_age
    else:
        age = rung_age
    return forced_birthdate_for_age(max(16, min(40, age)))


def _age_from_birthdate(raw: Any) -> int | None:
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    born = EA_DATE_EPOCH + timedelta(days=days)
    return max(16, min(45, int((FC26_SEASON_START - born).days / 365.25)))


def _clamp_to_band(value: int, recipe: SquadRecipe) -> int:
    number = max(1, min(99, int(value)))
    band = recipe.effective_band
    if band is None:
        return number
    lo, hi = band
    return max(lo, min(hi, number))


def _default_why(player: RosterPlayer, rung: str, target: int, recipe: SquadRecipe) -> str:
    current = player.ovr if player.ovr is not None else "?"
    if rung == "face":
        return f"{player.name}: club face, {current}→{target}"
    if rung == "prospect":
        return f"{player.name}: prospect floor of the band, {current}→{target}"
    return f"{player.name}: {rung} {current}→{target}"


def _int_or_none(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
