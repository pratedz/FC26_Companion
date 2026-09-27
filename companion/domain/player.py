"""Canonical FC 26 player record and editor schema.

The desktop app must never invent a player's values.  A :class:`PlayerRecord`
is built from a squad/catalog row or from a live ``snapshot`` operation, and
only fields declared here may enter a players-table write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class PlayerValidationError(ValueError):
    """A player id or staged field is not safe to send to Live Editor."""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    label: str
    category: str
    minimum: int | None = None
    maximum: int | None = None

    def coerce(self, value: Any) -> int:
        if isinstance(value, bool):
            result = int(value)
        else:
            try:
                result = int(str(value).strip())
            except (TypeError, ValueError) as exc:
                raise PlayerValidationError(f"{self.label} must be a whole number.") from exc
        if self.minimum is not None and result < self.minimum:
            raise PlayerValidationError(
                f"{self.label} must be at least {self.minimum}."
            )
        if self.maximum is not None and result > self.maximum:
            raise PlayerValidationError(
                f"{self.label} must be at most {self.maximum}."
            )
        return result


def _specs(
    category: str,
    fields: tuple[tuple[str, str], ...],
    lo: int | None = None,
    hi: int | None = None,
) -> tuple[FieldSpec, ...]:
    return tuple(FieldSpec(name, label, category, lo, hi) for name, label in fields)


ATTRIBUTE_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Pace", (("acceleration", "Acceleration"), ("sprintspeed", "Sprint speed"))),
    ("Shooting", (
        ("positioning", "Attacking position"), ("finishing", "Finishing"),
        ("shotpower", "Shot power"), ("longshots", "Long shots"),
        ("volleys", "Volleys"), ("penalties", "Penalties"),
    )),
    ("Passing", (
        ("vision", "Vision"), ("crossing", "Crossing"),
        ("freekickaccuracy", "Free-kick accuracy"), ("shortpassing", "Short passing"),
        ("longpassing", "Long passing"), ("curve", "Curve"),
    )),
    ("Dribbling", (
        ("agility", "Agility"), ("balance", "Balance"), ("reactions", "Reactions"),
        ("ballcontrol", "Ball control"), ("dribbling", "Dribbling"),
        ("composure", "Composure"),
    )),
    ("Defending", (
        ("interceptions", "Interceptions"), ("headingaccuracy", "Heading accuracy"),
        ("defensiveawareness", "Defensive awareness"),
        ("standingtackle", "Standing tackle"), ("slidingtackle", "Sliding tackle"),
    )),
    ("Physical", (
        ("jumping", "Jumping"), ("stamina", "Stamina"), ("strength", "Strength"),
        ("aggression", "Aggression"),
    )),
    ("Goalkeeping", (
        ("gkdiving", "GK diving"), ("gkhandling", "GK handling"),
        ("gkkicking", "GK kicking"), ("gkpositioning", "GK positioning"),
        ("gkreflexes", "GK reflexes"),
    )),
)

_FIELDS: list[FieldSpec] = []
_FIELDS += list(_specs("Ratings", (
    ("overallrating", "Overall"), ("potential", "Potential"),
), 1, 99))
_FIELDS += [
    FieldSpec("modifier", "Modifier", "Ratings"),
    FieldSpec("internationalrep", "International reputation", "Ratings", 1, 5),
]
for _group, _items in ATTRIBUTE_GROUPS:
    _FIELDS += list(_specs(_group, _items, 1, 99))
_FIELDS += [
    FieldSpec("skillmoves", "Skill moves (raw; 0 = 1 star)", "Skills & foot", 0, 4),
    FieldSpec("weakfootabilitytypecode", "Weak foot", "Skills & foot", 1, 5),
    FieldSpec("preferredfoot", "Preferred foot (1 right, 2 left)", "Skills & foot", 1, 2),
    FieldSpec("skillmoveslikelihood", "Skill moves likelihood", "Skills & foot"),
]
_FIELDS += list(_specs("Positions & roles", tuple(
    [(f"preferredposition{i}", f"Preferred position {i}") for i in range(1, 8)]
    + [(f"role{i}", f"Role {i}") for i in range(1, 6)]
), -1, 27))
# Bank 1 has 30 defined bits; the live FC26 bank-2 column has 17 bits.
# These storage bounds still apply when the session's plus-count cap is off.
for _prefix, _label in (("trait", "PlayStyles"), ("icontrait", "PlayStyle+")):
    _FIELDS += [
        FieldSpec(f"{_prefix}1", f"{_label} bank 1", "PlayStyles", 0, (1 << 30) - 1),
        FieldSpec(f"{_prefix}2", f"{_label} bank 2", "PlayStyles", 0, (1 << 17) - 1),
    ]
_FIELDS += [
    FieldSpec("height", "Height (cm)", "Body", 120, 230),
    FieldSpec("weight", "Weight (kg)", "Body", 35, 160),
    FieldSpec("bodytypecode", "Body type", "Body", 1, 437),
    FieldSpec("muscularitycode", "Muscularity", "Body"),
    FieldSpec("gender", "Gender", "Body", 0, 1),
]
_FIELDS += list(_specs("Movement & animation", (
    ("runstylecode", "Run style"), ("runningcode1", "Running code 1"),
    ("runningcode2", "Running code 2"),
    ("animfreekickstartposcode", "Free-kick animation"),
    ("animpenaltiesstartposcode", "Penalty animation"),
    ("gkkickstyle", "GK kick style"), ("gksavetype", "GK save type"),
    ("personality", "Personality"), ("emotion", "Emotion"),
)))
_FIELDS += list(_specs("Appearance", (
    ("headassetid", "Head asset ID"), ("headclasscode", "Head class"),
    ("headtypecode", "Head type"), ("headvariation", "Head variation"),
    ("hashighqualityhead", "High-quality head"), ("hairtypecode", "Hair type"),
    ("haircolorcode", "Hair color"), ("hairstylecode", "Hair style"),
    ("facialhairtypecode", "Facial hair type"),
    ("facialhaircolorcode", "Facial hair color"), ("eyebrowcode", "Eyebrow"),
    ("eyecolorcode", "Eye color"), ("eyedetail", "Eye detail"),
    ("skintonecode", "Skin tone"), ("skintypecode", "Skin type"),
    ("skincomplexion", "Skin complexion"), ("skinmakeup", "Skin makeup"),
    ("skinsurfacepack", "Skin surface pack"), ("sideburnscode", "Sideburns"),
    ("lipcolor", "Lip color"), ("faceposerpreset", "Face poser preset"),
    ("facepsdlayer0", "Face PSD layer 0"), ("facepsdlayer1", "Face PSD layer 1"),
)))
_FIELDS += list(_specs("Kit & accessories", (
    ("jerseystylecode", "Jersey style"), ("jerseysleevelengthcode", "Sleeve length"),
    ("jerseyfit", "Jersey fit"), ("socklengthcode", "Sock length"),
    ("sockstylecode", "Sock style"), ("undershortstyle", "Undershort style"),
    ("shoetypecode", "Boot type"), ("shoedesigncode", "Boot design"),
    ("shoecolorcode1", "Boot color 1"), ("shoecolorcode2", "Boot color 2"),
    ("smallsidedshoetypecode", "Small-sided boot"), ("gkglovetypecode", "GK gloves"),
    ("hasseasonaljersey", "Seasonal jersey"), ("accessorycode1", "Accessory 1"),
    ("accessorycode2", "Accessory 2"), ("accessorycode3", "Accessory 3"),
    ("accessorycode4", "Accessory 4"), ("accessorycolourcode1", "Accessory color 1"),
    ("accessorycolourcode2", "Accessory color 2"),
    ("accessorycolourcode3", "Accessory color 3"),
    ("accessorycolourcode4", "Accessory color 4"), ("shortstyle", "Short style"),
)))
_FIELDS += list(_specs("Tattoos", tuple(
    (name, label) for name, label in (
        ("tattoohead", "Head tattoo"), ("tattoofront", "Front tattoo"),
        ("tattooback", "Back tattoo"), ("tattooleftarm", "Left-arm tattoo"),
        ("tattoorightarm", "Right-arm tattoo"), ("tattooleftleg", "Left-leg tattoo"),
        ("tattoorightleg", "Right-leg tattoo"),
    )
)))
_FIELDS += list(_specs("Career & identity", (
    ("nationality", "Nationality ID"), ("birthdate", "Birthdate"),
    ("isretiring", "Retiring"), ("contractvaliduntil", "Contract valid until"),
    ("usercaneditname", "User can edit name"), ("iscustomized", "Customized"),
    ("firstnameid", "First-name ID"), ("lastnameid", "Last-name ID"),
    ("commonnameid", "Common-name ID"), ("playerjerseynameid", "Jersey-name ID"),
    ("avatarpomid", "Avatar POM ID"),
)))
_FIELDS += list(_specs("Composites", (
    ("pacdiv", "PAC composite"), ("shohan", "SHO composite"),
    ("paskic", "PAS composite"), ("driref", "DRI composite"),
    ("defspe", "DEF composite"), ("phypos", "PHY composite"),
), 0, 99))

FIELD_SPECS: dict[str, FieldSpec] = {field.name: field for field in _FIELDS}
EDITABLE_FIELDS: tuple[str, ...] = tuple(FIELD_SPECS)
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(f.category for f in _FIELDS))

ALIASES: dict[str, str] = {
    "ovr": "overallrating",
    "overall": "overallrating",
    "pot": "potential",
    "position": "preferredposition1",
    "pos": "preferredposition1",
    "weakfoot": "weakfootabilitytypecode",
    "weak_foot": "weakfootabilitytypecode",
    "skill_moves": "skillmoves",
}


def player_id(value: Any) -> int:
    try:
        pid = int(value)
    except (TypeError, ValueError) as exc:
        raise PlayerValidationError("Player ID must be a whole number.") from exc
    if pid <= 0:
        raise PlayerValidationError("Player ID must be greater than zero.")
    if pid > 499_999:
        raise PlayerValidationError(
            "Player ID must be 499999 or lower; larger IDs are not safe in FC 26."
        )
    return pid


def normalize_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep canonical, known values without coercing data merely read for display."""
    result: dict[str, Any] = {}
    raw_pid = row.get("playerid", row.get("id"))
    if raw_pid not in (None, ""):
        result["playerid"] = player_id(raw_pid)
    result["name"] = str(row.get("name") or row.get("playername") or "")
    for raw_name, value in row.items():
        name = ALIASES.get(str(raw_name).lower(), str(raw_name).lower())
        if name in FIELD_SPECS and value not in (None, ""):
            result[name] = value
    return result


def normalize_patch(fields: Mapping[str, Any]) -> dict[str, int]:
    """Validate a write patch. Unknown/read-only fields are rejected, never dropped."""
    clean: dict[str, int] = {}
    for raw_name, value in fields.items():
        name = ALIASES.get(str(raw_name).lower(), str(raw_name).lower())
        spec = FIELD_SPECS.get(name)
        if spec is None:
            raise PlayerValidationError(f"{raw_name} is not an editable player field.")
        clean[name] = spec.coerce(value)
    if not clean:
        raise PlayerValidationError("No editable player fields were provided.")
    return clean


def categories_for(row: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        FIELD_SPECS[name].category for name in row if name in FIELD_SPECS
    )


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    playerid: int
    name: str
    fields: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "PlayerRecord":
        normalized = normalize_record(row)
        if "playerid" not in normalized:
            raise PlayerValidationError("Player record has no player ID.")
        return cls(
            playerid=int(normalized["playerid"]),
            name=str(normalized.get("name", "")),
            fields=normalized,
        )
