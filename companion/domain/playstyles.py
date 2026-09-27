"""FC26 named PlayStyles from Live Editor's playstyles_enum.lua.

The remaining bank-2 bits describe CPU/Career traits, not PlayStyle+.
Keep numeric encoding in Python instead of asking an AI to guess bit positions.
"""
from __future__ import annotations

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


def encode_names(names: Any, *, plus: bool = False) -> dict[str, int]:
    if not isinstance(names, list):
        raise ValueError("PlayStyles must be a list of names.")
    banks = [0, 0]
    lookup = {name.casefold(): (bank, 1 << bit)
              for bank, items in enumerate((OUTFIELD, GOALKEEPER))
              for bit, name in enumerate(items)}
    for name in names:
        key = str(name).strip().removesuffix("+").strip().casefold()
        if key not in lookup:
            raise ValueError(f"Unknown PlayStyle: {name}.")
        bank, bit = lookup[key]
        banks[bank] |= bit
    prefix = "icontrait" if plus else "trait"
    return {f"{prefix}1": banks[0], f"{prefix}2": banks[1]}


def plus_names(fields: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(name for bank, names in enumerate((OUTFIELD, GOALKEEPER), 1)
                 for bit, name in enumerate(names)
                 if int(fields.get(f"icontrait{bank}") or 0) & (1 << bit))


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
    is_gk = str(position).strip().upper() in {"GK", "0"}
    if is_gk and not second:
        raise ValueError("Goalkeeper needs goalkeeper PlayStyle+.")
    if not is_gk and (not first or second):
        raise ValueError("Outfield player needs outfield PlayStyle+, without goalkeeper bits.")
