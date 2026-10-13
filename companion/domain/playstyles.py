"""FC26 named PlayStyles from Live Editor's playstyles_enum.lua.

The remaining bank-2 bits describe CPU/Career traits, not PlayStyle+.
Keep numeric encoding in Python instead of asking an AI to guess bit positions.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Mapping

OUTFIELD = (
    "Finesse Shot", "Chip Shot", "Power Shot", "Dead Ball", "Precision Header",
    "Acrobatic", "Low Driven Shot", "Game Changer", "Incisive Pass", "Pinged Pass",
    "Long Ball Pass", "Tiki Taka", "Whipped Pass", "Inventive", "Jockey", "Block",
    "Intercept", "Anticipate", "Slide Tackle", "Aerial Fortress", "Technical",
    "Rapid", "First Touch", "Trickster", "Press Proven", "Quick Step", "Relentless",
    "Long Throw", "Bruiser", "Enforcer",
)
GOALKEEPER = (
    "GK Far Throw", "GK Footwork", "GK Cross Claimer", "GK Rush Out",
    "GK Far Reach", "GK Deflector",
)
TRAIT_FIELDS = ("trait1", "trait2", "icontrait1", "icontrait2")


def resolve_playstyle_name(name: str) -> str:
    """Map a rough or misspelled PlayStyle onto the catalog spelling."""
    raw = str(name or "").strip().removesuffix("+").strip()
    folded = raw.casefold()
    catalog = (*OUTFIELD, *GOALKEEPER)
    exact = {item.casefold(): item for item in catalog}
    if folded in exact:
        return exact[folded]
    compact = {re.sub(r"[^a-z0-9]", "", item.casefold()): item for item in catalog}
    squashed = re.sub(r"[^a-z0-9]", "", folded)
    if squashed in compact:
        return compact[squashed]
    match = difflib.get_close_matches(folded, list(exact), n=1, cutoff=0.72)
    if match:
        return exact[match[0]]
    raise ValueError(f"Unknown PlayStyle: {name}.")


def encode_names(names: Any, *, plus: bool = False) -> dict[str, int]:
    if not isinstance(names, list):
        raise ValueError("PlayStyles must be a list of names.")
    banks = [0, 0]
    lookup = {name.casefold(): (bank, 1 << bit)
              for bank, items in enumerate((OUTFIELD, GOALKEEPER))
              for bit, name in enumerate(items)}
    for name in names:
        official = resolve_playstyle_name(str(name))
        bank, bit = lookup[official.casefold()]
        banks[bank] |= bit
    prefix = "icontrait" if plus else "trait"
    return {f"{prefix}1": banks[0], f"{prefix}2": banks[1]}


def plus_names(fields: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(name for bank, names in enumerate((OUTFIELD, GOALKEEPER), 1)
                 for bit, name in enumerate(names)
                 if int(fields.get(f"icontrait{bank}") or 0) & (1 << bit))


def no_ceiling_style_floor(prompt: str, *, unlimited: bool, position: Any,
                           target_ovr: int) -> tuple[int, int]:
    """Strong defaults for an explicit unlimited improvement, never a ceiling.

    Removing a ceiling alone does not request additions. An explicit style
    count remains the user's choice, so it disables these default floors.
    """
    text = str(prompt or "").casefold()
    improvement = re.search(r"\b(?:improv\w*|add\w*|enhanc\w*|boost\w*|upgrad\w*)\b", text)
    count = re.search(
        r"\b\d+\s+(?:regular\s+)?play\s*styles?\s*(?:\+|plus)?"
        r"|\bplay\s*styles?\s*(?:\+|plus)?\s*(?:count\s*|[:=]\s*)\d+\b"
        r"|\bplay\s*styles?\s*(?:\+|plus)?\s*\d+\b(?!\s*[.)])",
        text,
    )
    if (not unlimited or not improvement or not re.search(r"\bplay\s*styles?\b", text)
            or count or re.search(r"\bonly\b", text)):
        return 0, 0
    from .squad_session import position_group
    ovr = int(target_ovr or 0)
    if position_group(position) == "gk":
        return (6, 6) if ovr >= 93 else (6, 5) if ovr >= 88 else (4, 3)
    return (14, 10) if ovr >= 93 else (12, 8) if ovr >= 88 else (8, 6)


_ROLE_STYLES = {
    "cb": ("Anticipate", "Intercept", "Jockey", "Block", "Aerial Fortress", "Bruiser",
           "Slide Tackle", "Enforcer", "Relentless", "Long Ball Pass", "Pinged Pass",
           "Press Proven", "First Touch", "Precision Header", "Quick Step", "Tiki Taka"),
    "back": ("Jockey", "Anticipate", "Intercept", "Whipped Pass", "Relentless", "Quick Step",
             "Slide Tackle", "Rapid", "Block", "Long Ball Pass", "First Touch", "Pinged Pass",
             "Bruiser", "Press Proven", "Technical", "Tiki Taka"),
    "dm": ("Intercept", "Anticipate", "Jockey", "Long Ball Pass", "Pinged Pass", "Press Proven",
           "Relentless", "Tiki Taka", "Block", "First Touch", "Bruiser", "Incisive Pass",
           "Slide Tackle", "Technical", "Quick Step", "Enforcer"),
    "mid": ("Incisive Pass", "Tiki Taka", "Pinged Pass", "Long Ball Pass", "First Touch",
            "Press Proven", "Technical", "Relentless", "Quick Step", "Inventive", "Intercept",
            "Finesse Shot", "Dead Ball", "Rapid", "Whipped Pass", "Game Changer"),
    "wing": ("Technical", "Quick Step", "Rapid", "First Touch", "Finesse Shot", "Whipped Pass",
             "Trickster", "Incisive Pass", "Press Proven", "Inventive", "Low Driven Shot",
             "Tiki Taka", "Relentless", "Game Changer", "Dead Ball", "Acrobatic"),
    "striker": ("First Touch", "Low Driven Shot", "Finesse Shot", "Quick Step", "Power Shot",
                "Precision Header", "Acrobatic", "Rapid", "Technical", "Press Proven",
                "Chip Shot", "Game Changer", "Bruiser", "Tiki Taka", "Relentless", "Inventive"),
}
_STYLE_ATTRIBUTES = {
    "Finesse Shot": ("finishing", "curve"), "Chip Shot": ("finishing",),
    "Power Shot": ("shotpower", "longshots"), "Low Driven Shot": ("finishing", "shotpower"),
    "Dead Ball": ("freekickaccuracy", "curve"), "Precision Header": ("headingaccuracy",),
    "Acrobatic": ("volleys", "agility"), "Game Changer": ("composure", "finishing"),
    "Incisive Pass": ("vision", "shortpassing"), "Pinged Pass": ("shortpassing",),
    "Long Ball Pass": ("longpassing",), "Tiki Taka": ("shortpassing", "ballcontrol"),
    "Whipped Pass": ("crossing",), "Inventive": ("vision", "ballcontrol"),
    "Jockey": ("defensiveawareness",), "Block": ("defensiveawareness", "strength"),
    "Intercept": ("interceptions",), "Anticipate": ("standingtackle",),
    "Slide Tackle": ("slidingtackle",), "Aerial Fortress": ("jumping", "headingaccuracy"),
    "Technical": ("dribbling",), "Rapid": ("sprintspeed",),
    "First Touch": ("ballcontrol",), "Trickster": ("agility", "dribbling"),
    "Press Proven": ("composure", "balance"), "Quick Step": ("acceleration",),
    "Relentless": ("stamina",), "Bruiser": ("strength",), "Enforcer": ("aggression",),
    "GK Far Throw": ("gkkicking",), "GK Footwork": ("gkreflexes",),
    "GK Cross Claimer": ("gkhandling",), "GK Rush Out": ("gkpositioning",),
    "GK Far Reach": ("gkdiving",), "GK Deflector": ("gkreflexes", "gkhandling"),
}


def recommended_style_names(position: Any, current: Mapping[str, Any]) -> tuple[str, ...]:
    """Role-specific candidates ordered by the player's strongest live skills."""
    from .squad_session import _pos_token, position_group
    pos = _pos_token(position)
    if position_group(position) == "gk":
        candidates = GOALKEEPER
    elif pos in {"cb", "lcb", "rcb", "sw"}:
        candidates = _ROLE_STYLES["cb"]
    elif pos in {"lb", "rb", "lwb", "rwb", "wb"}:
        candidates = _ROLE_STYLES["back"]
    elif pos in {"cdm", "ldm", "rdm", "dm"}:
        candidates = _ROLE_STYLES["dm"]
    elif pos in {"lw", "rw", "lm", "rm", "lf", "rf"}:
        candidates = _ROLE_STYLES["wing"]
    elif pos in {"st", "cf", "ls", "rs"}:
        candidates = _ROLE_STYLES["striker"]
    else:
        candidates = _ROLE_STYLES["mid"]
    def score(name: str) -> float:
        values = []
        for field in _STYLE_ATTRIBUTES.get(name, ()):
            try:
                values.append(float(current[field]))
            except (KeyError, TypeError, ValueError):
                continue
        # A small role preference breaks close attributes, without turning a
        # passing centre-back into a striker or copying one set to the squad.
        return (sum(values) / len(values) if values else 0) + (len(candidates) - candidates.index(name)) * 0.5
    return tuple(sorted(candidates, key=score, reverse=True))


def repair_style_compatibility(fields: Mapping[str, Any], position: Any) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Remove incompatible gameplay styles; preserve ordinary Career traits."""
    from .player import FIELD_SPECS
    from .squad_session import position_group
    result = dict(fields)
    removed = []
    is_gk = position_group(position) == "gk"
    for bank, catalog in enumerate((OUTFIELD, GOALKEEPER), 1):
        applicable = is_gk if bank == 2 else not is_gk
        style_mask = (1 << len(catalog)) - 1
        for prefix in ("trait", "icontrait"):
            key = f"{prefix}{bank}"
            if key not in result:
                continue
            value = FIELD_SPECS[key].coerce(result[key])
            rejected = value & style_mask if not applicable else 0
            removed.extend(name + ("+" if prefix == "icontrait" else "")
                           for bit, name in enumerate(catalog) if rejected & (1 << bit))
            # CPU/Career bits are valid regular traits, never Plus styles.
            if prefix == "icontrait":
                rejected |= value & ~style_mask
            result[key] = value & ~rejected
    return result, tuple(removed)


def complete_additive_styles(patch: Mapping[str, Any], *, current: Mapping[str, Any],
                             position: Any, target_ovr: int, prompt: str,
                             unlimited: bool) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Complete sparse unlimited improvement drafts without another AI call.

    Call after proposal validation: this does not excuse a missing player or an
    invalid style bank. Existing regular Career/CPU traits survive the union,
    but count toward neither PlayStyle nor PlayStyle+ density.
    """
    result = dict(patch)
    regular_min, plus_min = no_ceiling_style_floor(
        prompt, unlimited=unlimited, position=position, target_ovr=target_ovr,
    )
    if not unlimited:
        return result, ()
    from .squad_session import position_group
    is_gk = position_group(position) == "gk"
    bank = 2 if is_gk else 1
    catalog = GOALKEEPER if is_gk else OUTFIELD
    mask = (1 << len(catalog)) - 1
    # Preserve the other banks too; named additions stay in the correct bank.
    for key in TRAIT_FIELDS:
        result[key] = int(result.get(key) or 0) | int(current.get(key) or 0)
    # Existing invalid styles must not poison every otherwise valid rebuild.
    result, _removed = repair_style_compatibility(result, position)
    if not regular_min:
        return result, ()
    regular_key, plus_key = f"trait{bank}", f"icontrait{bank}"
    prior_regular, prior_plus = result[regular_key], result[plus_key]
    candidates = recommended_style_names(position, current)
    for name in candidates:
        if (result[plus_key] & mask).bit_count() >= plus_min:
            break
        result[plus_key] |= 1 << catalog.index(name)
    result[regular_key] |= result[plus_key] & mask
    for name in candidates:
        if (result[regular_key] & mask).bit_count() >= regular_min:
            break
        result[regular_key] |= 1 << catalog.index(name)
    added = tuple(name + ("+" if result[plus_key] & (1 << bit) and not prior_plus & (1 << bit) else "")
                  for bit, name in enumerate(catalog)
                  if (result[regular_key] & ~prior_regular | result[plus_key] & ~prior_plus) & (1 << bit))
    return result, added


def validate_ai_styles(patch: Mapping[str, Any], *, require_plus: bool, position: str) -> None:
    from .player import FIELD_SPECS

    for key in TRAIT_FIELDS:
        if key in patch:
            FIELD_SPECS[key].coerce(patch[key])
    if not require_plus:
        return
    first = int(patch.get("icontrait1") or 0)
    second = int(patch.get("icontrait2") or 0)
    if second & ~63:
        raise ValueError("CPU/Career traits are not PlayStyle+; choose named PlayStyles.")
    from .squad_session import position_group
    is_gk = position_group(position) == "gk"
    if is_gk and (not second or first):
        raise ValueError("Goalkeeper needs goalkeeper PlayStyle+, without outfield bits.")
    if not is_gk and (not first or second):
        raise ValueError("Outfield player needs outfield PlayStyle+, without goalkeeper bits.")
