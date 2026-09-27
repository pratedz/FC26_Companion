"""Safe player-build proposals shared by cards, presets, manual edits and AI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .player import ATTRIBUTE_GROUPS, FIELD_SPECS, normalize_patch

# Ratings-only AI replies used to stage overallrating alone. Career Mode then
# shows a new OVR while Pace/Shooting/etc. stay on the old card — the bug the
# Player-tab "AI assistant" path must never leave unfixed.
_RATING_ONLY_FIELDS: frozenset[str] = frozenset(
    {"overallrating", "potential", "modifier", "internationalrep"}
)


def outfield_attribute_names() -> tuple[str, ...]:
    """Canonical 1–99 outfield attributes (no goalkeeping)."""
    names: list[str] = []
    for group, items in ATTRIBUTE_GROUPS:
        if group == "Goalkeeping":
            continue
        for name, _label in items:
            names.append(name)
    return tuple(names)


def attribute_names(*, include_gk: bool = True) -> tuple[str, ...]:
    names: list[str] = list(outfield_attribute_names())
    if include_gk:
        for group, items in ATTRIBUTE_GROUPS:
            if group != "Goalkeeping":
                continue
            for name, _label in items:
                names.append(name)
    return tuple(names)


def is_ratings_only_patch(fields: Mapping[str, Any], *, min_attrs: int = 1) -> bool:
    """True when a patch sets overall/potential but no real attributes.

    ``min_attrs`` is the minimum attribute-field count that counts as a real
    build. Default 1: any attribute means we trust the proposal; only pure
    overall/potential replies are expanded (the Neymar OVR-only bug).
    """
    keys = {str(k).lower() for k in fields}
    if "overallrating" not in keys and "potential" not in keys:
        return False
    attr_set = set(attribute_names(include_gk=True))
    attr_hits = sum(1 for k in keys if k in attr_set)
    return attr_hits < min_attrs


def expand_overall_patch(
    current: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    min_attrs: int = 1,
) -> dict[str, int]:
    """Fill attribute fields when a patch only (or mostly) sets overall.

    Shape is taken from the player's current attributes so a Neymar stays a
    Neymar-shaped build; values are scaled so the mean tracks the target OVR.
    When current attributes are already inflated-OVR leftovers (mean far below
    overall), scaling from the attribute mean still lifts the in-stats.
    """
    base = {str(k).lower(): v for k, v in dict(patch).items()}
    if not is_ratings_only_patch(base, min_attrs=min_attrs):
        return normalize_patch(base)

    target_raw = base.get("overallrating", current.get("overallrating"))
    try:
        target = int(target_raw)
    except (TypeError, ValueError):
        return normalize_patch(base)
    target = max(1, min(99, target))

    # Prefer outfield shape; only touch GK attrs when the player looks like a GK.
    pref = current.get("preferredposition1", base.get("preferredposition1"))
    try:
        is_gk = int(pref) == 0
    except (TypeError, ValueError):
        is_gk = False
    attrs = attribute_names(include_gk=True) if is_gk else outfield_attribute_names()

    present: dict[str, int] = {}
    for name in attrs:
        raw = current.get(name)
        if raw in (None, ""):
            continue
        try:
            present[name] = max(1, min(99, int(raw)))
        except (TypeError, ValueError):
            continue

    expanded = dict(base)
    expanded["overallrating"] = target
    if present:
        avg = sum(present.values()) / len(present)
        # Aim attribute mean near the target OVR. EA's overall is not the mean,
        # but proportional scaling keeps the player's profile honest.
        factor = (target / avg) if avg > 0 else 1.0
        # Soft clamp so a tiny OVR bump does not flatten every stat to identical.
        factor = max(0.55, min(1.85, factor))
        for name, value in present.items():
            expanded[name] = max(1, min(99, int(round(value * factor))))
    else:
        # A squad row with no in-stats must not become a flat card at the
        # target overall. The caller reads the live player first.
        return normalize_patch(base)

    pot_raw = expanded.get("potential", current.get("potential", target))
    try:
        pot = int(pot_raw)
    except (TypeError, ValueError):
        pot = target
    expanded["potential"] = max(target, max(1, min(99, pot)))
    # Zero the in-game OVR modifier so the written overallrating actually sticks.
    if "modifier" not in expanded:
        expanded["modifier"] = 0
    return normalize_patch(expanded)


@dataclass(frozen=True, slots=True)
class BuildProposal:
    fields: Mapping[str, int]
    source: str
    label: str
    summary: str = ""
    warnings: tuple[str, ...] = ()


def revision(playerid: int, values: Mapping[str, Any]) -> str:
    """Stable token used to reject an AI reply for an older target/draft."""
    body = json.dumps(
        {"playerid": int(playerid), "values": dict(sorted(values.items()))},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def proposal(
    fields: Mapping[str, Any],
    *,
    source: str,
    label: str,
    summary: str = "",
    warnings: tuple[str, ...] = (),
    current: Mapping[str, Any] | None = None,
    expand_thin_overall: bool = True,
) -> BuildProposal:
    patch = dict(fields)
    extra_warnings: list[str] = []
    if expand_thin_overall and current is not None and is_ratings_only_patch(patch):
        before = len(patch)
        patch = expand_overall_patch(current, patch)
        if len(patch) > before:
            extra_warnings.append(
                "Overall-only AI reply was expanded to a full attribute suite "
                "so Career Mode in-stats match the requested rating."
            )
    return BuildProposal(
        fields=normalize_patch(patch),
        source=source,
        label=label,
        summary=summary,
        warnings=tuple(dict.fromkeys((*warnings, *extra_warnings))),
    )


def preset(preset_id: str) -> BuildProposal:
    """Improved v1 presets: focused patches, never a blanket hidden overwrite."""
    common: dict[str, dict[str, int]] = {
        "balanced_85": {
            **{name: 85 for name, spec in FIELD_SPECS.items()
               if spec.category in {
                   "Pace", "Shooting", "Passing", "Dribbling", "Defending", "Physical"
               }},
            "overallrating": 85, "potential": 88,
            "skillmoves": 2, "weakfootabilitytypecode": 3,
        },
        "pace_monster": {
            "acceleration": 99, "sprintspeed": 99, "agility": 95,
            "balance": 90, "stamina": 95,
        },
        "cb_wall": {
            "defensiveawareness": 95, "standingtackle": 95,
            "slidingtackle": 95, "headingaccuracy": 95,
            "strength": 95, "jumping": 95, "aggression": 95,
            "interceptions": 95, "composure": 95,
        },
        "playmaker": {
            "shortpassing": 94, "longpassing": 94, "vision": 94,
            "ballcontrol": 94, "dribbling": 94, "curve": 94,
            "composure": 94, "reactions": 94,
        },
        "finisher": {
            "finishing": 95, "positioning": 95, "shotpower": 95,
            "longshots": 95, "volleys": 95, "headingaccuracy": 95,
            "composure": 95, "reactions": 95,
        },
        "max_99": {
            **{name: 99 for name, spec in FIELD_SPECS.items()
               if spec.minimum == 1 and spec.maximum == 99},
            "skillmoves": 4, "weakfootabilitytypecode": 5,
        },
    }
    labels = {
        "balanced_85": "Balanced 85",
        "pace_monster": "Pace monster",
        "cb_wall": "CB wall",
        "playmaker": "Playmaker",
        "finisher": "Finisher",
        "max_99": "Max 99",
    }
    if preset_id not in common:
        raise ValueError(f"Unknown preset {preset_id}")
    warning = (
        ("Max 99 touches every 1-99 field; inspect the full review carefully.",)
        if preset_id == "max_99" else ()
    )
    return proposal(
        common[preset_id],
        source="preset",
        label=labels[preset_id],
        warnings=warning,
    )


PRESETS: tuple[tuple[str, str], ...] = (
    ("balanced_85", "Balanced 85"),
    ("pace_monster", "Pace monster"),
    ("cb_wall", "CB wall"),
    ("playmaker", "Playmaker"),
    ("finisher", "Finisher"),
    ("max_99", "Max 99"),
)
