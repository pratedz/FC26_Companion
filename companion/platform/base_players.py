"""Adapter for Live Editor's authoritative ``base_players.csv`` lookup."""

from __future__ import annotations

from typing import Any, Mapping

from ..domain.card_import import BasePlayerProfile, card_source_playerid
from ..domain.player import FIELD_SPECS


def resolve_card_base_profile(card: Mapping[str, Any]) -> BasePlayerProfile | None:
    """Return exact base-player identity/appearance, or None without guessing."""
    source_playerid = card_source_playerid(card)
    if source_playerid is None:
        return None
    try:
        from src import base_players

        row = base_players.get_row(source_playerid)
        if row is None:
            return None
        identity = base_players.identity(source_playerid)
    except Exception:
        return None

    # Read Appearance straight from the authoritative base row.  The legacy
    # ``appearance()`` helper intentionally omitted the five head fields and
    # also included body/kit fields.  A Player-page "Face" copy must do the
    # opposite: include the exact shipped head values, but never broaden into
    # body, team, identity, contract or source-id data.
    appearance: dict[str, int] = {}
    for key, spec in FIELD_SPECS.items():
        if spec.category != "Appearance":
            continue
        value = _integer(row.get(key))
        if value is not None:
            appearance[key] = value
    face_verified = (
        int(appearance.get("headassetid", 0)) > 0
        and int(appearance.get("headclasscode", 1)) == 0
    )
    return BasePlayerProfile(
        source_playerid=source_playerid,
        identity={
            str(key): value
            for key, raw in identity.items()
            if (value := _integer(raw)) is not None
        },
        appearance=appearance,
        face_verified=face_verified,
    )


def _integer(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not number.is_integer():
        return None
    return int(number)


def generic_signing_appearance(
    profile: BasePlayerProfile | None, *, gender: int = 0,
) -> dict[str, int]:
    """Copy native generic cosmetics or a same-gender template, then clear real assets."""
    if profile is not None and not profile.face_verified:
        appearance = dict(profile.appearance)
    else:
        from src import base_players
        row = base_players.generic_template(gender)
        if row is None:
            return {}
        appearance = {
            key: value for key, spec in FIELD_SPECS.items()
            if spec.category == "Appearance" and (value := _integer(row.get(key))) is not None
        }
    # The generic pipeline cannot reference an absent FUT/FC27 real-head asset.
    appearance.update(headassetid=0, headclasscode=1, hashighqualityhead=0, headvariation=0)
    return appearance
