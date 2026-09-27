"""Safe, reviewable card-to-current-player import choices.

Card observations identify a *source*.  They are never allowed to choose the
Career Mode destination.  The app supplies the already locked target and this
module returns only editable player-table fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

from .builds import BuildProposal, proposal
from .catalog import KIT_IMPORT_FIELDS, LocalCatalog
from .player import CATEGORIES, FIELD_SPECS, PlayerValidationError


@dataclass(frozen=True, slots=True)
class CardImportTopics:
    stats: bool = True
    name: bool = False
    age: bool = False
    face: bool = False
    kit: bool = False
    forced_age: int | None = None

    def any_selected(self) -> bool:
        return (
            self.stats
            or self.name
            or self.age
            or self.face
            or self.kit
            or self.forced_age is not None
        )

    def labels(self) -> tuple[str, ...]:
        return tuple(
            label
            for enabled, label in (
                (self.stats, "Stats"),
                (self.kit, "Kit"),
                (self.name, "Name"),
                (self.age, "Age"),
                (self.forced_age is not None, f"Age ({self.forced_age}, forced)"),
                (self.face, "Face"),
            )
            if enabled
        )


def split_card_import_fields(
    stats_fields: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate card-build fields from opt-in kit/boot fields."""
    build: dict[str, Any] = {}
    kit: dict[str, Any] = {}
    for key, value in stats_fields.items():
        if key in KIT_IMPORT_FIELDS:
            kit[key] = value
        else:
            build[key] = value
    return build, kit


def changed_fields_by_category(
    proposed_fields: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[tuple[str, tuple[tuple[str, str, Any, Any], ...]], ...]:
    """Group changed proposed fields by ``FIELD_SPECS.category``.

    Uses :meth:`LocalCatalog.compare` for importable numeric fields so Library
    compare and the Player preview share the same before/after arithmetic.
    Fields outside that set (name, age, face) still appear when they differ.
    Unchanged fields stay hidden. A missing live ``before`` is ``None``.
    """
    proposed = dict(proposed_fields)
    pending: dict[str, tuple[Any, Any]] = {}
    compared: set[str] = set()
    for row in LocalCatalog.compare(current, proposed):
        field = str(row["field"])
        compared.add(field)
        if field not in proposed or not row["changed"]:
            continue
        pending[field] = (row["before"], row["after"])
    for field, after in proposed.items():
        if field in pending or field in compared:
            continue
        before = current.get(field) if field in current else None
        if before != after:
            pending[field] = (before, after)

    spec_order = {name: index for index, name in enumerate(FIELD_SPECS)}
    buckets: dict[str, list[tuple[str, str, Any, Any]]] = {}
    for field, (before, after) in pending.items():
        spec = FIELD_SPECS.get(field)
        category = spec.category if spec is not None else "Other"
        label = spec.label if spec is not None else field
        buckets.setdefault(category, []).append((field, label, before, after))
    for rows in buckets.values():
        rows.sort(key=lambda item: spec_order.get(item[0], 10_000))

    ordered = [category for category in CATEGORIES if category in buckets]
    ordered.extend(category for category in buckets if category not in CATEGORIES)
    return tuple((category, tuple(buckets[category])) for category in ordered)


FC26_SEASON_START = date(2025, 7, 1)
EA_DATE_EPOCH = date(1582, 10, 14)


def forced_birthdate_for_age(age: int) -> int:
    """Return an FC 26 birthdate that makes a player ``age`` at season start."""
    try:
        requested_age = int(age)
    except (TypeError, ValueError) as exc:
        raise PlayerValidationError("Forced age must be a whole number.") from exc
    if not 16 <= requested_age <= 40:
        raise PlayerValidationError("Forced age must be between 16 and 40.")
    birthday = FC26_SEASON_START.replace(year=FC26_SEASON_START.year - requested_age)
    return (birthday - EA_DATE_EPOCH).days


@dataclass(frozen=True, slots=True)
class BasePlayerProfile:
    """Authoritative FC26 base-player values resolved outside the domain."""

    source_playerid: int
    identity: Mapping[str, int]
    appearance: Mapping[str, int]
    face_verified: bool = False


NAME_FIELDS = frozenset(
    {"firstnameid", "lastnameid", "commonnameid", "playerjerseynameid"}
)
FORBIDDEN_SOURCE_FIELDS = frozenset(
    {
        "playerid", "person_id", "variant_id", "obs_id", "teamid", "team_id",
        "club", "clubid", "contractvaliduntil", "isretiring", "loandateend",
    }
)


def card_source_playerid(card: Mapping[str, Any]) -> int | None:
    """Resolve the base person represented by a card, never its variant id."""
    for key in ("person_id", "base_player_id", "baseid", "baseId", "playerid"):
        raw = card.get(key)
        if raw in (None, "") or isinstance(raw, bool):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if not number.is_integer():
            continue
        value = int(number)
        if 0 < value <= 499_999:
            return value
    return None


def build_card_proposal(
    *,
    card: Mapping[str, Any],
    target_playerid: int,
    topics: CardImportTopics,
    stats_fields: Mapping[str, Any],
    source: Mapping[str, Any],
    base_profile: BasePlayerProfile | None,
    stats_warnings: tuple[str, ...] = (),
) -> BuildProposal:
    """Compose selected topics without allowing source identity to retarget writes."""
    if int(target_playerid) <= 0:
        raise PlayerValidationError("Select a Career Mode player first.")
    if not topics.any_selected():
        raise PlayerValidationError("Choose at least one card topic to copy.")

    fields: dict[str, Any] = {}
    warnings: list[str] = list(stats_warnings if (topics.stats or topics.kit) else ())
    build_fields, kit_fields = split_card_import_fields(stats_fields)
    if topics.stats:
        fields.update(build_fields)
    if topics.kit:
        if kit_fields:
            fields.update(kit_fields)
        else:
            warnings.append("This card has no kit or boot fields; Kit was skipped.")

    if topics.name:
        available_name = {
            key: value
            for key, value in (base_profile.identity.items() if base_profile else ())
            if key in NAME_FIELDS and value not in (None, "")
        }
        # An authoritative row may contain four zero sentinels. Applying that
        # set can blank the destination's resolved name, so require at least
        # one real dictionary id before copying the complete available set.
        copied = (
            available_name
            if any(int(value) > 0 for value in available_name.values())
            else {}
        )
        fields.update(copied)
        if not copied:
            warnings.append("Verified name IDs are unavailable for this card; Name was skipped.")

    if topics.forced_age is not None:
        fields["birthdate"] = forced_birthdate_for_age(topics.forced_age)
    elif topics.age:
        birthdate = base_profile.identity.get("birthdate") if base_profile else None
        if birthdate not in (None, "") and int(birthdate) > 0:
            fields["birthdate"] = birthdate
        else:
            warnings.append("A verified birthdate is unavailable for this card; Age was skipped.")

    if topics.face and base_profile is not None and base_profile.face_verified:
        copied_face = {
            key: value
            for key, value in (base_profile.appearance.items() if base_profile else ())
            if key in FIELD_SPECS
            and FIELD_SPECS[key].category == "Appearance"
            and value not in (None, "")
        }
        fields.update(copied_face)
    elif topics.face:
        copied_face = {}
    if topics.face and not copied_face:
        warnings.append("A verified real FC26 face is unavailable for this card; Face was skipped.")

    for forbidden in FORBIDDEN_SOURCE_FIELDS:
        fields.pop(forbidden, None)
    if not fields:
        raise PlayerValidationError(
            "The selected card topics contain no verified editable fields. Nothing was staged."
        )

    card_name = str(source.get("name") or card.get("name") or "Card")
    year = str(source.get("year") or card.get("year") or "")
    variant = str(source.get("variant") or card.get("variant") or "Base")
    topic_text = ", ".join(topics.labels())
    return proposal(
        fields,
        source="card",
        label=" · ".join(part for part in (card_name, year, variant) if part),
        summary=f"Copy {topic_text} from {card_name} to player {int(target_playerid)}.",
        warnings=tuple(dict.fromkeys(warnings)),
    )
