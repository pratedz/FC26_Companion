"""Full Live Editor players-table editor schema.

Mirrors fields available in LE / typical CE player editors, grouped into
topics. The UI exposes checkboxes per topic; only checked topics are written
on Apply.

Field list sourced from player_presets/base_players.csv (FC 26 LE).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ── Attribute groups (1–99) ──────────────────────────────────────────

ATTR_GROUPS: List[Tuple[str, List[Tuple[str, str]]]] = [
    (
        "Pace",
        [
            ("acceleration", "Acceleration"),
            ("sprintspeed", "Sprint Speed"),
        ],
    ),
    (
        "Shooting",
        [
            ("positioning", "Att. Position"),
            ("finishing", "Finishing"),
            ("shotpower", "Shot Power"),
            ("longshots", "Long Shots"),
            ("volleys", "Volleys"),
            ("penalties", "Penalties"),
        ],
    ),
    (
        "Passing",
        [
            ("vision", "Vision"),
            ("crossing", "Crossing"),
            ("freekickaccuracy", "FK Accuracy"),
            ("shortpassing", "Short Pass"),
            ("longpassing", "Long Pass"),
            ("curve", "Curve"),
        ],
    ),
    (
        "Dribbling",
        [
            ("agility", "Agility"),
            ("balance", "Balance"),
            ("reactions", "Reactions"),
            ("ballcontrol", "Ball Control"),
            ("dribbling", "Dribbling"),
            ("composure", "Composure"),
        ],
    ),
    (
        "Defending",
        [
            ("interceptions", "Interceptions"),
            ("headingaccuracy", "Heading Acc."),
            ("defensiveawareness", "Def. Aware"),
            ("standingtackle", "Stand Tackle"),
            ("slidingtackle", "Slide Tackle"),
        ],
    ),
    (
        "Physical",
        [
            ("jumping", "Jumping"),
            ("stamina", "Stamina"),
            ("strength", "Strength"),
            ("aggression", "Aggression"),
        ],
    ),
    (
        "Goalkeeping",
        [
            ("gkdiving", "GK Diving"),
            ("gkhandling", "GK Handling"),
            ("gkkicking", "GK Kicking"),
            ("gkpositioning", "GK Positioning"),
            ("gkreflexes", "GK Reflexes"),
        ],
    ),
]

ATTR_FIELDS: Tuple[str, ...] = tuple(f for _, items in ATTR_GROUPS for f, _ in items)

# Composite face-stat columns (written only if user sets).
# There are exactly SIX, and every one is a real players-table column in
# player_presets/base_players.csv. The SHO/HAN slot is spelled "shohan" — there
# is no "sho" column, so the old entry silently dropped SHO from every card.
# Observed ranges in base_players.csv (22,348 rows):
#   pacdiv 1-97  shohan 1-94  paskic 1-96  driref 1-96  defspe 1-95  phypos 2-93
COMPOSITE_FIELDS: List[Tuple[str, str]] = [
    ("pacdiv", "PAC composite"),
    ("shohan", "SHO composite"),
    ("paskic", "PAS composite"),
    ("driref", "DRI composite"),
    ("defspe", "DEF composite"),
    ("phypos", "PHY composite"),
]
# Real DB columns from base_players.csv — identical to COMPOSITE_FIELDS now that
# the phantom "sho" column is gone. Kept as a separate name for import back-compat.
COMPOSITE_DB_FIELDS: List[Tuple[str, str]] = list(COMPOSITE_FIELDS)

# ── Topic categories (checkbox groups) ───────────────────────────────
# Each field entry: (le_field, label, hint)

FieldDef = Tuple[str, str, str]


def _f(field: str, label: str, hint: str = "") -> FieldDef:
    return (field, label, hint)


EDIT_CATEGORIES: List[Dict[str, Any]] = [
    {
        "id": "attributes",
        "label": "Attributes",
        "description": "Pace / shooting / passing / dribbling / defending / physical / GK (1–99)",
        "default": True,
        "dev_plan": True,
        "fields": [_f(f, lab, "1–99") for _, items in ATTR_GROUPS for f, lab in items],
        "groups": ATTR_GROUPS,  # for UI sub-headers
    },
    {
        "id": "ratings",
        "label": "Ratings",
        "description": "Overall, potential, modifier, reputation",
        "default": True,
        "dev_plan": True,  # ovr only
        "fields": [
            _f("overallrating", "Overall", "1–99"),
            _f("potential", "Potential", "1–99"),
            _f("modifier", "Modifier", "0 = clear chem boosts"),
            _f("internationalrep", "Int. Reputation", "1–5"),
        ],
    },
    {
        "id": "skills",
        "label": "Skills & Foot",
        "description": "Skill moves ★, weak foot ★, preferred foot",
        "default": True,
        "dev_plan": False,
        "fields": [
            # players.skillmoves is 0-indexed (0 = 1★ … 4 = 5★) while
            # weakfootabilitytypecode is 1-indexed. Use stars_to_skillmoves() /
            # skillmoves_to_stars() to convert — never assume they match.
            _f("skillmoves", "Skill Moves (raw)", "0–4 raw = 1–5★"),
            _f("weakfootabilitytypecode", "Weak Foot ★", "1–5"),
            _f("preferredfoot", "Preferred Foot", "1=Right  2=Left"),
            _f("skillmoveslikelihood", "Skill Moves Likelihood", "game code"),
        ],
    },
    {
        "id": "positions",
        "label": "Positions & Roles",
        "description": "Preferred positions 1–7 and role slots",
        "default": True,
        "dev_plan": False,
        "fields": [
            _f("preferredposition1", "Main Position", "0–27 or ST/CM/…"),
            _f("preferredposition2", "Position 2", "or -1 unused"),
            _f("preferredposition3", "Position 3", ""),
            _f("preferredposition4", "Position 4", ""),
            _f("preferredposition5", "Position 5", ""),
            _f("preferredposition6", "Position 6", ""),
            _f("preferredposition7", "Position 7", ""),
            _f("role1", "Role 1", "LE role id"),
            _f("role2", "Role 2", "LE role id"),
            _f("role3", "Role 3", "LE role id"),
            _f("role4", "Role 4", "LE role id"),
            _f("role5", "Role 5", "LE role id"),
        ],
    },
    {
        "id": "playstyles",
        "label": "PlayStyles / PlayStyle+",
        "description": "trait1/2 + icontrait1/2 bitmasks (same as LE PlayStyles editor)",
        "default": True,
        "dev_plan": False,
        "fields": [
            _f("trait1", "PlayStyles bank1 (trait1)", "bitmask int"),
            _f("trait2", "PlayStyles bank2 (trait2)", "bitmask int"),
            _f("icontrait1", "PlayStyle+ bank1 (icontrait1)", "bitmask int"),
            _f("icontrait2", "PlayStyle+ bank2 (icontrait2)", "bitmask int"),
        ],
        "playstyle_ui": True,
    },
    {
        "id": "body",
        "label": "Body",
        "description": "Height, weight, body type, muscularity, gender",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("height", "Height (cm)", "e.g. 187"),
            _f("weight", "Weight (kg)", "e.g. 83"),
            _f("bodytypecode", "Body Type Code", "LE bodytypecode"),
            _f("muscularitycode", "Muscularity Code", ""),
            _f("gender", "Gender", "0 male / 1 female (game)"),
        ],
    },
    {
        "id": "movement",
        "label": "Running & Anim Styles",
        "description": "Run style, free-kick/penalty anims, GK styles, personality",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("runstylecode", "Run Style Code", "main run animation"),
            _f("runningcode1", "Running Code 1", ""),
            _f("runningcode2", "Running Code 2", ""),
            _f("animfreekickstartposcode", "Free Kick Anim", ""),
            _f("animpenaltiesstartposcode", "Penalty Anim", ""),
            _f("gkkickstyle", "GK Kick Style", ""),
            _f("gksavetype", "GK Save Type", ""),
            _f("personality", "Personality", ""),
            _f("emotion", "Emotion", ""),
        ],
    },
    {
        "id": "face",
        "label": "Head / Face / Hair",
        "description": "Head model, hair, skin, eyes, facial hair (LE appearance)",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("headassetid", "Head Asset ID", "often = playerid"),
            _f("headclasscode", "Head Class", "0/1 high quality"),
            _f("headtypecode", "Head Type", ""),
            _f("headvariation", "Head Variation", ""),
            _f("hashighqualityhead", "High Quality Head", "0/1"),
            _f("hairtypecode", "Hair Type", ""),
            _f("haircolorcode", "Hair Color", ""),
            _f("hairstylecode", "Hair Style", ""),
            _f("facialhairtypecode", "Facial Hair Type", ""),
            _f("facialhaircolorcode", "Facial Hair Color", ""),
            _f("eyebrowcode", "Eyebrow", ""),
            _f("eyecolorcode", "Eye Color", ""),
            _f("eyedetail", "Eye Detail", ""),
            _f("skintonecode", "Skin Tone", ""),
            _f("skintypecode", "Skin Type", ""),
            _f("skincomplexion", "Skin Complexion", ""),
            _f("skinmakeup", "Skin Makeup", ""),
            _f("skinsurfacepack", "Skin Surface Pack", ""),
            _f("sideburnscode", "Sideburns", ""),
            _f("lipcolor", "Lip Color", ""),
            _f("faceposerpreset", "Face Poser Preset", ""),
            _f("facepsdlayer0", "Face PSD Layer 0", ""),
            _f("facepsdlayer1", "Face PSD Layer 1", ""),
        ],
    },
    {
        "id": "kit",
        "label": "Kit / Boots / Accessories",
        "description": "Jersey tuck, socks, boots, gloves, accessories",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("jerseystylecode", "Jersey Style (tuck)", "0 tucked / 1 untucked often"),
            _f("jerseysleevelengthcode", "Sleeve Length", ""),
            _f("jerseyfit", "Jersey Fit", ""),
            _f("socklengthcode", "Sock Length", ""),
            _f("sockstylecode", "Sock Style", ""),
            _f("undershortstyle", "Under-short Style", ""),
            _f("shoetypecode", "Boot Type", ""),
            _f("shoedesigncode", "Boot Design", ""),
            _f("shoecolorcode1", "Boot Color 1", ""),
            _f("shoecolorcode2", "Boot Color 2", ""),
            _f("smallsidedshoetypecode", "Small-sided Boot", ""),
            _f("gkglovetypecode", "GK Glove Type", ""),
            _f("hasseasonaljersey", "Seasonal Jersey", "0/1"),
            _f("accessorycode1", "Accessory 1", ""),
            _f("accessorycode2", "Accessory 2", ""),
            _f("accessorycode3", "Accessory 3", ""),
            _f("accessorycode4", "Accessory 4", ""),
            _f("accessorycolourcode1", "Acc. Color 1", ""),
            _f("accessorycolourcode2", "Acc. Color 2", ""),
            _f("accessorycolourcode3", "Acc. Color 3", ""),
            _f("accessorycolourcode4", "Acc. Color 4", ""),
            _f("shortstyle", "Short Style", ""),
        ],
    },
    {
        "id": "tattoos",
        "label": "Tattoos",
        "description": "Tattoo region codes (LE tattoo editor)",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("tattoohead", "Tattoo Head", ""),
            _f("tattoofront", "Tattoo Front", ""),
            _f("tattooback", "Tattoo Back", ""),
            _f("tattooleftarm", "Tattoo Left Arm", ""),
            _f("tattoorightarm", "Tattoo Right Arm", ""),
            _f("tattooleftleg", "Tattoo Left Leg", ""),
            _f("tattoorightleg", "Tattoo Right Leg", ""),
        ],
    },
    {
        "id": "career",
        "label": "Career / Identity",
        "description": "Nationality, birthdate, retiring, contract year, name IDs",
        "default": False,
        "dev_plan": False,
        "fields": [
            _f("nationality", "Nationality ID", "nation table id"),
            _f("birthdate", "Birthdate", "FIFA date int"),
            _f("isretiring", "Is Retiring", "0/1"),
            _f("contractvaliduntil", "Contract Valid Until", "year e.g. 2030"),
            _f("usercaneditname", "User Can Edit Name", "0/1"),
            _f("iscustomized", "Is Customized", "0/1"),
            _f("firstnameid", "First Name ID", "dictionary"),
            _f("lastnameid", "Last Name ID", "dictionary"),
            _f("commonnameid", "Common Name ID", "dictionary"),
            _f("playerjerseynameid", "Jersey Name ID", "dictionary"),
            _f("avatarpomid", "Avatar POM ID", ""),
        ],
    },
    {
        "id": "composites",
        "label": "Face-stat Composites",
        "description": "Internal PAC/PAS/DRI/DEF/PHY composite columns (advanced)",
        "default": False,
        "dev_plan": False,
        "fields": [_f(f, lab, "advanced") for f, lab in COMPOSITE_DB_FIELDS],
    },
]

CATEGORY_BY_ID: Dict[str, Dict[str, Any]] = {c["id"]: c for c in EDIT_CATEGORIES}

DEFAULT_CATEGORY_IDS: Tuple[str, ...] = tuple(
    c["id"] for c in EDIT_CATEGORIES if c.get("default")
)

# Flat field → category id
FIELD_TO_CATEGORY: Dict[str, str] = {}
ALL_EDIT_FIELDS: List[str] = []
for _cat in EDIT_CATEGORIES:
    for _field, _lab, _hint in _cat["fields"]:
        FIELD_TO_CATEGORY[_field] = _cat["id"]
        ALL_EDIT_FIELDS.append(_field)

# Playstyle bitmasks (lua/libs/v2/imports/other/playstyles_enum.lua)
PLAYSTYLE1: List[Tuple[str, int, str]] = [
    ("Finesse Shot", 1, "ps1"),
    ("Chip Shot", 2, "ps1"),
    ("Power Shot", 4, "ps1"),
    ("Dead Ball", 8, "ps1"),
    ("Precision Header", 16, "ps1"),
    ("Acrobatic", 32, "ps1"),
    ("Low Driven Shot", 64, "ps1"),
    ("Game Changer", 128, "ps1"),
    ("Incisive Pass", 256, "ps1"),
    ("Pinged Pass", 512, "ps1"),
    ("Long Ball Pass", 1024, "ps1"),
    ("Tiki Taka", 2048, "ps1"),
    ("Whipped Pass", 4096, "ps1"),
    ("Inventive", 8192, "ps1"),
    ("Jockey", 16384, "ps1"),
    ("Block", 32768, "ps1"),
    ("Intercept", 65536, "ps1"),
    ("Anticipate", 131072, "ps1"),
    ("Slide Tackle", 262144, "ps1"),
    ("Aerial Fortress", 524288, "ps1"),
    ("Technical", 1048576, "ps1"),
    ("Rapid", 2097152, "ps1"),
    ("First Touch", 4194304, "ps1"),
    ("Trickster", 8388608, "ps1"),
    ("Press Proven", 16777216, "ps1"),
    ("Quick Step", 33554432, "ps1"),
    ("Relentless", 67108864, "ps1"),
    ("Long Throw", 134217728, "ps1"),
    ("Bruiser", 268435456, "ps1"),
    ("Enforcer", 536870912, "ps1"),
]

# trait2 / icontrait2 is DENSE: 14 consecutive bits, no gaps.
# Source of truth: lua/libs/v2/imports/other/playstyles_enum.lua
# (ENUM_PLAYSTYLE2_* = 1,2,4,8,16,32,64,128,256,512,1024,2048,4096,8192).
# This table used to skip bits 6 (64 CPU AI Long Shot Taker) and 7 (128 CPU AI
# Early Crosser), jumping 32 -> 256. Two things broke:
#   * FUT.GG round-trip: futgg_client maps index i>=30 to bit 1<<(i-30), so
#     ids 36/37 produced bits 64/128 that no name could ever decode, and
#     futgg_playstyle_id_to_name(36) answered "Solid Player" instead of
#     "CPU AI Long Shot Taker" (every id >= 36 was shifted by two).
#   * lua/scripts/pap_all_playstyles.lua writes trait2 = icontrait2 = 1535,
#     which sets bits 0-8 and 10 — including 64 and 128. Decoding 1535 with the
#     sparse table dropped both and re-encoded to 1343.
PLAYSTYLE2: List[Tuple[str, int, str]] = [
    ("GK Far Throw", 1, "ps2"),
    ("GK Footwork", 2, "ps2"),
    ("GK Cross Claimer", 4, "ps2"),
    ("GK Rush Out", 8, "ps2"),
    ("GK Far Reach", 16, "ps2"),
    ("GK Deflector", 32, "ps2"),
    ("CPU AI Long Shot Taker", 64, "ps2"),
    ("CPU AI Early Crosser", 128, "ps2"),
    ("Solid Player", 256, "ps2"),
    ("Team Player", 512, "ps2"),
    ("One Club Player", 1024, "ps2"),
    ("Injury Prone", 2048, "ps2"),
    ("Leadership", 4096, "ps2"),
    ("Super Sub", 8192, "ps2"),
]

# All-bits-set masks, i.e. what pap_all_playstyles.lua-style "give everything"
# writes. PLAYSTYLE1 is 30 dense bits -> 2**30 - 1 == 1073741823 (the exact
# constant that script uses), PLAYSTYLE2 is 14 dense bits -> 2**14 - 1 == 16383.
PLAYSTYLE1_ALL_MASK: int = (1 << len(PLAYSTYLE1)) - 1
PLAYSTYLE2_ALL_MASK: int = (1 << len(PLAYSTYLE2)) - 1

# Keep old META_EDIT_FIELDS name for any external imports
META_EDIT_FIELDS: List[FieldDef] = [
    f
    for cat in EDIT_CATEGORIES
    if cat["id"] in ("ratings", "skills", "positions", "body")
    for f in cat["fields"]
]

POS_NAME_TO_CODE: Dict[str, int] = {
    "GK": 0,
    "SW": 1,
    "RWB": 2,
    "RB": 3,
    "RCB": 4,
    "CB": 5,
    "LCB": 6,
    "LB": 7,
    "LWB": 8,
    "RDM": 9,
    "CDM": 10,
    "LDM": 11,
    "RM": 12,
    "RCM": 13,
    "CM": 14,
    "LCM": 15,
    "LM": 16,
    "RAM": 17,
    "CAM": 18,
    "LAM": 19,
    "RF": 20,
    "CF": 21,
    "LF": 22,
    "RW": 23,
    "RS": 24,
    "ST": 25,
    "LS": 26,
    "LW": 27,
}
POS_CODE_TO_NAME: Dict[int, str] = {v: k for k, v in POS_NAME_TO_CODE.items()}

_PS_LOOKUP: Dict[str, Tuple[str, int]] = {}
for name, bit, bank in PLAYSTYLE1 + PLAYSTYLE2:
    key = name.casefold().replace(" ", "").replace("-", "").replace("_", "")
    _PS_LOOKUP[key] = (bank, bit)
    _PS_LOOKUP[name.casefold()] = (bank, bit)


# ── Verified players-table value ranges ──────────────────────────────
# Every bound below was measured over all 22,348 rows of
# player_presets/base_players.csv. Do NOT "tidy" these into a shared range:
# skillmoves and weakfootabilitytypecode genuinely disagree by one.
SKILLMOVES_MIN, SKILLMOVES_MAX = 0, 4  # observed 0..4 — 0-indexed (0 = 1★, 4 = 5★)
WEAKFOOT_MIN, WEAKFOOT_MAX = 1, 5  # observed 1..5 — 1-indexed (1 = 1★, 5 = 5★)
INTERNATIONALREP_MIN, INTERNATIONALREP_MAX = 1, 5  # observed 1..5; 0 is not a legal rep
# Observed 1..437 across 164 distinct codes. The old 1..20 clamp mangled the
# physique of every player above 20 (e.g. 437 was rewritten to 20).
BODYTYPECODE_MIN, BODYTYPECODE_MAX = 1, 437


def stars_to_skillmoves(stars: Any) -> int:
    """Skill-move ★ (1–5, as shown in-game / by FUT.GG) → raw players.skillmoves.

    players.skillmoves is 0-indexed, so 5★ is stored as 4. Callers that assign a
    star count straight into the field silently create a player one star short.
    """
    try:
        s = int(stars)
    except (TypeError, ValueError):
        s = 1
    s = max(1, min(5, s))
    return s - 1


def skillmoves_to_stars(v: Any) -> int:
    """Raw players.skillmoves (0–4) → skill-move ★ (1–5). Inverse of the above."""
    try:
        raw = int(v)
    except (TypeError, ValueError):
        raw = SKILLMOVES_MIN
    return max(SKILLMOVES_MIN, min(SKILLMOVES_MAX, raw)) + 1


def category_ids() -> List[str]:
    return [c["id"] for c in EDIT_CATEGORIES]


def default_enabled_categories() -> Set[str]:
    return {c["id"] for c in EDIT_CATEGORIES if c.get("default")}


def fields_for_categories(enabled: Iterable[str]) -> Set[str]:
    en = set(enabled)
    out: Set[str] = set()
    for cat in EDIT_CATEGORIES:
        if cat["id"] in en:
            for f, _, _ in cat["fields"]:
                out.add(f)
    return out


def empty_player_card() -> Dict[str, Any]:
    card: Dict[str, Any] = {
        "name": "",
        "year": "editor",
        "origin": "editor",
        "playstyles": [],
        "playstyles_plus": [],
    }
    for f in ALL_EDIT_FIELDS:
        card[f] = ""
    # Sensible defaults for common skill meta (still empty attrs).
    # Playstyle bitmasks stay blank (not 0): writing trait*=0 would wipe CM
    # playstyles when applying a catalog card that has no PS data.
    # Both mean "4 stars", but the encodings differ: skillmoves is 0-indexed so
    # 4★ is raw 3, while weakfootabilitytypecode is 1-indexed so 4★ is raw 4.
    # Seeding both with 4 used to hand out 5★ skills on a "4 star" default.
    card["skillmoves"] = stars_to_skillmoves(4)  # 3
    card["weakfootabilitytypecode"] = 4
    card["preferredfoot"] = 1
    card["modifier"] = 0
    card["trait1"] = ""
    card["trait2"] = ""
    card["icontrait1"] = ""
    card["icontrait2"] = ""
    return card


def clamp_field(field: str, value: int) -> int:
    v = int(value)
    if field in ATTR_FIELDS or field in ("overallrating", "potential"):
        return max(1, min(99, v))
    # These three look alike but do NOT share a range (base_players.csv, 22,348
    # rows). Clamping all of them to 0..5 let raw skillmoves=5 through (illegal:
    # the column tops out at 4) and let internationalrep=0 through (illegal: the
    # column starts at 1).
    if field == "skillmoves":
        return max(SKILLMOVES_MIN, min(SKILLMOVES_MAX, v))
    if field == "weakfootabilitytypecode":
        return max(WEAKFOOT_MIN, min(WEAKFOOT_MAX, v))
    if field == "internationalrep":
        return max(INTERNATIONALREP_MIN, min(INTERNATIONALREP_MAX, v))
    if field == "bodytypecode":
        # Observed 1..437 — a narrow clamp here rewrites real physiques.
        return max(BODYTYPECODE_MIN, min(BODYTYPECODE_MAX, v))
    if field == "preferredfoot":
        return 1 if v not in (1, 2) else v
    if field.startswith("preferredposition"):
        # -1 sometimes means unused
        if v < 0:
            return -1
        return max(0, min(32, v))
    if field == "height":
        return max(140, min(220, v))
    if field == "weight":
        return max(45, min(130, v))
    if field in ("trait1", "trait2", "icontrait1", "icontrait2"):
        return max(0, v)
    if field in ("isretiring", "hashighqualityhead", "usercaneditname", "iscustomized", "hasseasonaljersey", "gender"):
        return 0 if v == 0 else 1
    if field == "modifier":
        return v
    # Generic game codes — allow wide range
    return v


# Back-compat alias
clamp_attr = clamp_field


def parse_position(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip().upper()
    if s in ("-1", "NONE", "N/A"):
        return -1
    if re_fullmatch_int(s):
        return int(s)
    s = s.split(",")[0].split("/")[0].strip()
    return POS_NAME_TO_CODE.get(s)


def re_fullmatch_int(s: str) -> bool:
    if not s:
        return False
    if s[0] in "+-":
        return s[1:].isdigit()
    return s.isdigit()


def playstyle_names_to_masks(names: Sequence[str]) -> Tuple[int, int]:
    t1 = t2 = 0
    for raw in names:
        if not raw:
            continue
        key = str(raw).strip()
        compact = key.casefold().replace(" ", "").replace("-", "").replace("_", "")
        hit = _PS_LOOKUP.get(compact) or _PS_LOOKUP.get(key.casefold())
        if not hit:
            continue
        bank, bit = hit
        if bank == "ps1":
            t1 |= bit
        else:
            t2 |= bit
    return t1, t2


def masks_to_playstyle_names(trait1: int, trait2: int) -> List[str]:
    out: List[str] = []
    for name, bit, _ in PLAYSTYLE1:
        if trait1 & bit:
            out.append(name)
    for name, bit, _ in PLAYSTYLE2:
        if trait2 & bit:
            out.append(name)
    return out


def futgg_playstyle_id_to_name(pid: int) -> Optional[str]:
    """FUT.GG sequential playstyle index → LE display name (0=Finesse Shot, …)."""
    try:
        i = int(pid)
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(PLAYSTYLE1):
        return PLAYSTYLE1[i][0]
    j = i - len(PLAYSTYLE1)
    if 0 <= j < len(PLAYSTYLE2):
        return PLAYSTYLE2[j][0]
    return None


def playstyle_list_to_names(seq: Any) -> List[str]:
    """Normalize list of names or FUT.GG index IDs to display names."""
    if not seq:
        return []
    if not isinstance(seq, (list, tuple)):
        seq = [seq]
    out: List[str] = []
    for x in seq:
        if x is None or x == "":
            continue
        if isinstance(x, int) or (isinstance(x, str) and str(x).strip().lstrip("+-").isdigit()):
            nm = futgg_playstyle_id_to_name(int(x))
            if nm:
                out.append(nm)
            else:
                out.append(str(x))
        else:
            out.append(str(x))
    return out


def card_playstyle_labels(card: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(regular names, plus names) from masks and/or list fields."""
    try:
        t1 = int(card.get("trait1") or 0)
        t2 = int(card.get("trait2") or 0)
        i1 = int(card.get("icontrait1") or 0)
        i2 = int(card.get("icontrait2") or 0)
    except (TypeError, ValueError):
        t1 = t2 = i1 = i2 = 0
    reg = masks_to_playstyle_names(t1, t2)
    plus = masks_to_playstyle_names(i1, i2)
    if not reg:
        reg = playstyle_list_to_names(card.get("playstyles") or card.get("play_styles"))
    if not plus:
        plus = playstyle_list_to_names(
            card.get("playstyles_plus")
            or card.get("playstylesPlus")
            or card.get("playstyle_plus")
        )
    return reg, plus


def normalize_player_card(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize AI / manual dict into editor card with all known LE fields."""
    from . import field_map

    card = empty_player_card()
    if not isinstance(raw, dict):
        return card

    for k in ("name", "label", "description", "prompt"):
        if raw.get(k):
            card["name"] = str(raw.get(k))
            break

    mapped = field_map.map_card_keys(raw)
    for k, v in raw.items():
        lk = str(k).strip().lower()
        mapped.setdefault(lk, v)

    for f in ALL_EDIT_FIELDS:
        if f.startswith("preferredposition"):
            code = parse_position(mapped.get(f, raw.get(f)))
            if code is None and f == "preferredposition1":
                code = parse_position(raw.get("position") or mapped.get("position"))
            if code is not None:
                card[f] = code
            continue
        v = mapped.get(f, raw.get(f))
        if v in (None, ""):
            continue
        try:
            card[f] = clamp_field(f, int(float(str(v))))
        except (TypeError, ValueError):
            pass

    # empty_player_card seeds SM/WF/foot for blank editor forms. Those defaults
    # must not survive normalize when the source omitted them — otherwise Import
    # Apply (skills category) overwrites real career SM/WF/foot with 4/4/Right.
    for f in ("skillmoves", "weakfootabilitytypecode"):
        if mapped.get(f, raw.get(f)) in (None, ""):
            card[f] = ""

    foot = raw.get("preferredfoot") or raw.get("preferred_foot") or raw.get("foot")
    if isinstance(foot, str):
        fl = foot.casefold()
        if "left" in fl or fl in ("l", "2"):
            card["preferredfoot"] = 2
        elif "right" in fl or fl in ("r", "1"):
            card["preferredfoot"] = 1
        else:
            card["preferredfoot"] = ""
    elif foot not in (None, ""):
        try:
            card["preferredfoot"] = clamp_field("preferredfoot", int(foot))
        except (TypeError, ValueError):
            card["preferredfoot"] = ""
    else:
        card["preferredfoot"] = ""

    reg = raw.get("playstyles") or raw.get("play_styles") or []
    plus = (
        raw.get("playstyles_plus")
        or raw.get("playstyle_plus")
        or raw.get("playstyles+")
        or raw.get("playstylesPlus")
        or []
    )
    if isinstance(reg, str):
        reg = [x.strip() for x in reg.replace(";", ",").split(",") if x.strip()]
    if isinstance(plus, str):
        plus = [x.strip() for x in plus.replace(";", ",").split(",") if x.strip()]

    # FUT.GG numeric IDs vs name strings
    def _ps_masks(items: Any) -> Tuple[int, int]:
        if not items:
            return 0, 0
        seq = list(items)
        if seq and all(isinstance(x, (int, float)) or str(x).isdigit() for x in seq):
            try:
                from . import futgg_client

                return futgg_client.futgg_playstyle_ids_to_masks(seq)
            except Exception:
                return 0, 0
        return playstyle_names_to_masks([str(x) for x in seq])

    t1, t2 = _ps_masks(reg)
    i1, i2 = _ps_masks(plus)

    for src, dst in (
        ("trait1", "trait1"),
        ("trait2", "trait2"),
        ("icontrait1", "icontrait1"),
        ("icontrait2", "icontrait2"),
    ):
        if raw.get(src) not in (None, ""):
            try:
                card[dst] = int(raw[src])
            except (TypeError, ValueError):
                pass

    if reg:
        card["trait1"] = int(card.get("trait1") or 0) | t1
        card["trait2"] = int(card.get("trait2") or 0) | t2
        card["playstyles"] = list(reg)
    if plus:
        card["icontrait1"] = int(card.get("icontrait1") or 0) | i1
        card["icontrait2"] = int(card.get("icontrait2") or 0) | i2
        card["playstyles_plus"] = list(plus)
    # Prefer explicit masks from FUT.GG definition_to_le_row when already set
    for k in ("trait1", "trait2", "icontrait1", "icontrait2"):
        if raw.get(k) not in (None, "") and not reg and not plus:
            try:
                card[k] = int(raw[k])
            except (TypeError, ValueError):
                pass

    if card.get("overallrating") not in (None, "") and card.get("potential") in (
        None,
        "",
    ):
        card["potential"] = card["overallrating"]
    if card.get("modifier") in (None, ""):
        card["modifier"] = 0
    return card


def card_to_field_updates(
    card: Dict[str, Any],
    *,
    enabled_categories: Optional[Iterable[str]] = None,
    only_nonempty: bool = True,
) -> List[Tuple[str, int]]:
    """
    Build ordered (field, value) list for Lua.

    enabled_categories: if set, only fields belonging to those topics are written.
    only_nonempty: skip blank/None values (except bitmasks 0 when playstyles enabled
                   and user cleared all — we still write 0 if key is present as int).
    """
    if enabled_categories is None:
        allowed = set(ALL_EDIT_FIELDS)
    else:
        allowed = fields_for_categories(enabled_categories)

    updates: List[Tuple[str, int]] = []
    # Preserve category order from EDIT_CATEGORIES
    for cat in EDIT_CATEGORIES:
        for field, _, _ in cat["fields"]:
            if field not in allowed:
                continue
            v = card.get(field)
            if v in (None, "") and only_nonempty:
                continue
            if v in (None, ""):
                continue
            try:
                updates.append((field, clamp_field(field, int(v))))
            except (TypeError, ValueError):
                continue

    # If ratings category active and modifier never set, force 0 when ovr written
    if enabled_categories is None or "ratings" in set(enabled_categories):
        has_mod = any(f == "modifier" for f, _ in updates)
        has_ovr = any(f == "overallrating" for f, _ in updates)
        if has_ovr and not has_mod:
            updates.append(("modifier", 0))
    return updates


def format_card_summary(card: Dict[str, Any]) -> str:
    name = card.get("name") or "Custom"
    ovr = card.get("overallrating") or "?"
    pot = card.get("potential") or "?"
    # skillmoves is stored raw (0-4) but displayed in stars. `or "?"` also hid
    # raw 0, which is a legitimate value meaning 1★.
    sm_raw = card.get("skillmoves")
    sm: Any = "?" if sm_raw in (None, "") else skillmoves_to_stars(sm_raw)
    wf = card.get("weakfootabilitytypecode") or "?"
    foot = card.get("preferredfoot")
    foot_s = {1: "R", 2: "L"}.get(int(foot), "?") if foot not in (None, "") else "?"
    pos = card.get("preferredposition1")
    if pos not in (None, ""):
        try:
            pos_s = POS_CODE_TO_NAME.get(int(pos), str(pos))
        except (TypeError, ValueError):
            pos_s = str(pos)
    else:
        pos_s = "?"
    h = card.get("height")
    w = card.get("weight")
    body = f" | {h}cm/{w}kg" if h not in (None, "") else ""
    run = card.get("runstylecode")
    run_s = f" | run={run}" if run not in (None, "") else ""
    ps = ", ".join(card.get("playstyles_plus") or card.get("playstyles") or [])[:50]
    return (
        f"{name} | OVR {ovr}/{pot} | {pos_s} | {sm}★SM {wf}★WF | Foot {foot_s}"
        f"{body}{run_s} | {ps}"
    )
