"""Add player to Career user team from a card/AI payload.

SAFE v15 default path (game stability first):
  1) Select worst free-agent dummy ids (never FUT catalog / protected squad)
  2) Overwrite that player with import-style field writes + editedplayernames
  3) TransferPlayer into user teamid
  4) Optional real-face apply on the existing id

CreatePlayer is OPT-IN only (mode=create) — freezes FC26 on many saves.
Lua emitter: add_team_lua.py.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import paths
from . import player_schema
from .card_types import CardDict

GENERATED_ID_MIN = 460000
# Hard ceiling: CreatePlayer with a playerid >= 500000 terminates the game
# process. Evidence (queue/_add_team_crash.log vs Logs/live_editor_*.log):
# ids 463139..494215 all completed; 506153..546619 all killed FC26.
GENERATED_ID_CEILING = 500000
GENERATED_ID_MAX = GENERATED_ID_CEILING - 1
DUMMY_POOL_LIMIT = 20  # free-agent overwrite candidates, keep small
FREE_AGENT_TEAM_ID = 111592

# Default age used when card has no birthdate (Gregorian days computed in Lua)
DEFAULT_CREATE_AGE = 28

# Fields safe to send into CreatePlayer (LE DOC: string values)
_CREATE_SAFE_FIELDS: frozenset[str] = frozenset(
    {
        "overallrating",
        "potential",
        "preferredposition1",
        "preferredposition2",
        "preferredposition3",
        "preferredposition4",
        "preferredfoot",
        "skillmoves",
        "weakfootabilitytypecode",
        "gender",
        "isretiring",
        "modifier",
        "internationalrep",
        "nationality",
        "birthdate",
        "acceleration",
        "sprintspeed",
        "agility",
        "balance",
        "jumping",
        "stamina",
        "strength",
        "reactions",
        "aggression",
        "composure",
        "interceptions",
        "positioning",
        "vision",
        "ballcontrol",
        "crossing",
        "dribbling",
        "finishing",
        "headingaccuracy",
        "longpassing",
        "shortpassing",
        "curve",
        "freekickaccuracy",
        "longshots",
        "penalties",
        "shotpower",
        "volleys",
        "marking",
        "standingtackle",
        "slidingtackle",
        "defensiveawareness",
        "gkdiving",
        "gkhandling",
        "gkkicking",
        "gkpositioning",
        "gkreflexes",
        "height",
        "weight",
        "bodytypecode",
        "headassetid",
        "headclasscode",
        "hashighqualityhead",
        "headtypecode",
        "headvariation",
        "hairtypecode",
        "haircolorcode",
        "hairstylecode",
        "facialhairtypecode",
        "facialhaircolorcode",
        "skintonecode",
        "skintypecode",
        "eyecolorcode",
        "eyebrowcode",
        "sideburnscode",
        "jerseystylecode",
        "jerseyfit",
        "shoetypecode",
        "socklengthcode",
    }
)

# Visual/body defaults when catalog omits them (prevents Age 442 / 4'3" / mannequin).
# Do NOT default nationality — England (14) was wrong for Icons; omit until known.
_VISUAL_DEFAULTS: Dict[str, str] = {
    "height": "180",
    "weight": "75",
    "bodytypecode": "5",
    "skintonecode": "3",
    "skintypecode": "0",
    "hairtypecode": "10",
    "haircolorcode": "1",
    "hairstylecode": "0",
    "facialhairtypecode": "0",
    "facialhaircolorcode": "1",
    "eyecolorcode": "2",
    "eyebrowcode": "0",
    "sideburnscode": "0",
    "headclasscode": "1",
    "hashighqualityhead": "0",
    "headtypecode": "0",
    "headvariation": "0",
    "jerseystylecode": "0",
    "jerseyfit": "0",
    "shoetypecode": "1",
    "socklengthcode": "0",
    "gender": "0",
    "isretiring": "0",
    "modifier": "0",
    "internationalrep": "3",
    "preferredfoot": "1",
    "skillmoves": "3",
    "weakfootabilitytypecode": "3",
}

# Common FIFA nation ids when FUT only gives a name (best-effort)
_NATION_NAME_TO_ID: Dict[str, int] = {
    "france": 18,
    "england": 14,
    "spain": 45,
    "germany": 21,
    "italy": 27,
    "portugal": 38,
    "brazil": 54,
    "argentina": 52,
    "netherlands": 34,
    "belgium": 7,
    "croatia": 10,
    "uruguay": 60,
    "colombia": 56,
    "mexico": 83,
    "usa": 95,
    "united states": 95,
    "wales": 50,
    "scotland": 42,
    "ireland": 25,
    "republic of ireland": 25,
    "nigeria": 27,  # may vary by DB; better than blank
    "ghana": 117,
    "senegal": 136,
    "morocco": 129,
    "japan": 163,
    "korea republic": 167,
    "south korea": 167,
}

# When FUT omits nationEaId, map well-known base player ids → nation
_BASE_PLAYER_NATION: Dict[int, int] = {
    1397: 18,  # Zidane → France
    190043: 18,  # Zidane alt / icon variants often near base
    20801: 38,  # CR7 → Portugal
    158023: 52,  # Messi → Argentina
    190871: 54,  # Neymar → Brazil
    237067: 54,  # Pelé (FUT base) → Brazil
    166149: 54,  # Ronaldinho common base
    28130: 54,  # Ronaldo Nazário common
    41: 45,  # Iniesta-ish / ensure maps grow via FUT
}

_CORE_DEFAULTS: Dict[str, str] = {
    "isretiring": "0",
    "modifier": "0",
    "gender": "0",
    "internationalrep": "3",
    "preferredfoot": "1",
    "skillmoves": "3",
    "weakfootabilitytypecode": "3",
}

# CreatePlayer payload allowlist: body/face/nation + core ratings + key attrs.
# Full 80+ dumps are unnecessary; name dictionary ids must never be included.
_CREATE_PAYLOAD_FIELDS: frozenset[str] = frozenset(
    {
        "overallrating",
        "potential",
        "preferredposition1",
        "preferredposition2",
        "preferredposition3",
        "preferredposition4",
        "preferredfoot",
        "skillmoves",
        "weakfootabilitytypecode",
        "gender",
        "isretiring",
        "modifier",
        "internationalrep",
        "nationality",
        "birthdate",
        "height",
        "weight",
        "bodytypecode",
        "headassetid",
        "headclasscode",
        "hashighqualityhead",
        "headtypecode",
        "headvariation",
        "hairtypecode",
        "haircolorcode",
        "hairstylecode",
        "facialhairtypecode",
        "facialhaircolorcode",
        "skintonecode",
        "skintypecode",
        "eyecolorcode",
        "eyebrowcode",
        "sideburnscode",
        "jerseystylecode",
        "jerseyfit",
        "shoetypecode",
        "socklengthcode",
        "acceleration",
        "sprintspeed",
        "stamina",
        "strength",
        "reactions",
        "composure",
        "ballcontrol",
        "dribbling",
        "shortpassing",
        "finishing",
        "standingtackle",
        "gkdiving",
        "gkhandling",
        "gkkicking",
        "gkpositioning",
        "gkreflexes",
    }
)

_NAME_ID_FIELDS = (
    "firstnameid",
    "lastnameid",
    "commonnameid",
    "playerjerseynameid",
    "usercaneditname",
)


def minimize_create_row(row: Mapping[str, str]) -> Dict[str, str]:
    """Filter CreatePlayer row to the allowlist; strip name-dictionary fields."""
    out: Dict[str, str] = {}
    for k, v in (row or {}).items():
        if k in _CREATE_PAYLOAD_FIELDS and v is not None and str(v) != "":
            out[str(k)] = str(v)
    for ban in _NAME_ID_FIELDS:
        out.pop(ban, None)
    return out


# Back-compat alias (older tests / callers)
def ultra_create_row(row: Mapping[str, str]) -> Dict[str, str]:
    return minimize_create_row(row)


def user_team_id_from_squad(squad: Optional[Mapping[str, Any]] = None) -> int:
    """Team id from export_user_squad / current_squad.json (0 if unknown)."""
    if squad is None:
        from . import target_players

        squad = target_players.load_squad()
    try:
        tid = int(squad.get("teamid") or 0)
    except (TypeError, ValueError):
        tid = 0
    return tid if tid > 0 else 0


def protected_squad_ids(squad: Optional[Mapping[str, Any]] = None) -> Set[int]:
    """Playerids currently on the exported user squad (never reuse as dummies)."""
    if squad is None:
        from . import target_players

        squad = target_players.load_squad()
    out: Set[int] = set()
    for p in squad.get("players") or []:
        try:
            pid = int(p.get("playerid") or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            out.add(pid)
    return out


def allocate_generated_playerid(
    *,
    seed: Optional[str] = None,
    preferred: Optional[int] = None,
) -> int:
    """Pick a create id in LE generated range (≥ GENERATED_ID_MIN)."""
    if preferred is not None:
        try:
            p = int(preferred)
            if GENERATED_ID_MIN <= p <= GENERATED_ID_MAX:
                return p
        except (TypeError, ValueError):
            pass
    span = GENERATED_ID_MAX - GENERATED_ID_MIN - 1000
    base = int(time.time()) % span
    if seed:
        h = abs(hash(seed)) % 90000
        base = (base + h) % span
    pid = GENERATED_ID_MIN + 1000 + base
    # Never hand back an id in the process-killing range.
    return min(pid, GENERATED_ID_MAX)


def select_worst_dummies(
    pool: Sequence[Mapping[str, Any]],
    *,
    protected_ids: Optional[Iterable[int]] = None,
    limit: int = DUMMY_POOL_LIMIT,
) -> List[int]:
    """Offline dummy pick — ONLY accept free-agent / explicit low-OVR rows.

    Never trust FUT catalog specials (Messi/Ronaldo ids freeze career when overwritten).
    Rows need playerid; optional overallrating, teamid, source.
    """
    prot = {int(x) for x in (protected_ids or []) if x is not None}
    lim = max(0, min(int(limit), DUMMY_POOL_LIMIT))
    scored: List[Tuple[int, int, int]] = []
    for raw in pool:
        try:
            pid = int(raw.get("playerid") or raw.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in prot:
            continue
        # Never touch generated range or iconic base ids under 500 without free flag
        try:
            ovr = int(raw.get("overallrating") or raw.get("ovr") or 50)
        except (TypeError, ValueError):
            ovr = 50
        team_hint = raw.get("teamid")
        try:
            tid = int(team_hint) if team_hint not in (None, "") else -1
        except (TypeError, ValueError):
            tid = -1
        src = str(raw.get("source") or "").lower()
        is_free = (
            tid in (0, FREE_AGENT_TEAM_ID)
            or src in ("free", "free_agent", "dummy", "fa")
            or bool(raw.get("free_agent"))
        )
        # Generated ids are not free-agent dummies
        if pid >= GENERATED_ID_MIN:
            continue
        # Reject high-OVR without free-agent flag (catalog specials)
        if not is_free and ovr > 60:
            continue
        if not is_free and pid < 50000 and ovr >= 70:
            continue
        free_rank = 0 if is_free else 1
        scored.append((free_rank, ovr, pid))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    seen: Set[int] = set()
    out: List[int] = []
    for _fr, _ovr, pid in scored:
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        if len(out) >= lim:
            break
    return out


def _split_name(name: str) -> Tuple[str, str, str]:
    n = re.sub(r"\s+", " ", (name or "New Player").strip()) or "New Player"
    parts = n.split(" ")
    if len(parts) == 1:
        first, sur = parts[0], parts[0]
    else:
        first, sur = parts[0], " ".join(parts[1:])
    jersey = sur[:15] if sur else first[:15]
    return first[:20], sur[:20], jersey


def resolve_player_names(card: Mapping[str, Any]) -> Tuple[str, str, str]:
    """Prefer FUT firstName/lastName; fall back to splitting display name."""
    first = str(card.get("firstName") or card.get("firstname") or "").strip()
    last = str(card.get("lastName") or card.get("lastname") or card.get("surname") or "").strip()
    nick = str(card.get("nickname") or card.get("commonname") or card.get("commonName") or "").strip()
    if first or last:
        f = (first or last)[:20]
        s = (last or first)[:20]
        j = (nick or s or f)[:15]
        return f, s, j
    return _split_name(str(card.get("name") or "New Player"))


def resolve_base_face_id(card: Mapping[str, Any]) -> int:
    """Base EA player id for real-face headassetid (Icons/heroes), else 0."""
    for key in (
        "basePlayerEaId",
        "base_id",
        "baseId",
        "baseid",
        "base_playerid",
        "basePlayerId",
        "headassetid",  # explicit face asset when already on card
    ):
        try:
            v = int(card.get(key) or 0)
            if 1 <= v < GENERATED_ID_MIN:
                return v
        except (TypeError, ValueError):
            continue
    # FUT often stores base id in playerid when eaId is the card id
    try:
        pid = int(card.get("playerid") or 0)
        if 1 <= pid < GENERATED_ID_MIN:
            return pid
    except (TypeError, ValueError):
        pass
    return 0


def resolve_nationality_id(card: Mapping[str, Any]) -> Optional[int]:
    """Nation table id from card; None if unknown (do not invent England)."""
    for key in ("nationality", "nationEaId", "nation_id", "nationId"):
        try:
            v = int(card.get(key))  # type: ignore[arg-type]
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    name = str(
        card.get("nation_name") or card.get("nation") or card.get("country") or ""
    ).strip().lower()
    if name in _NATION_NAME_TO_ID:
        return _NATION_NAME_TO_ID[name]
    base = resolve_base_face_id(card)
    if base in _BASE_PLAYER_NATION:
        return _BASE_PLAYER_NATION[base]
    return None


def enrich_card_for_add(card: Mapping[str, Any]) -> Dict[str, Any]:
    """Fill base face id / height from FUT fields so stale index rows still work."""
    c = dict(card or {})
    if not resolve_base_face_id(c):
        try:
            pid = int(c.get("playerid") or 0)
            if 1 <= pid < GENERATED_ID_MIN:
                c["basePlayerEaId"] = pid
                c["base_id"] = pid
        except (TypeError, ValueError):
            pass
    # Live re-map from FUT.GG jsonl by slug when nation/height missing
    need = (
        c.get("nationality") in (None, "")
        or c.get("height") in (None, "")
        or not c.get("firstName")
    )
    slug = str(c.get("slug") or "").strip()
    if need and slug:
        try:
            from . import futgg_client as _fg
            from . import paths as _paths

            y = str(c.get("year") or "26")
            p = _paths.card_db_dir() / "futgg" / f"futgg_{y}.jsonl"
            if p.is_file():
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if slug not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        if str(obj.get("slug") or "") != slug and slug not in str(
                            obj.get("slug") or ""
                        ):
                            continue
                        full = _fg.definition_to_le_row(obj)
                        for k, v in full.items():
                            if v in (None, "") and c.get(k) not in (None, ""):
                                continue
                            if c.get(k) in (None, "") and v not in (None, ""):
                                c[k] = v
                        break
        except Exception:
            pass
    return c


def _pos_to_code(pos: Any) -> int:
    """Map position name/code to LE preferredposition1 int."""
    if pos is None or pos == "":
        return 14  # CM default
    try:
        n = int(pos)
        if 0 <= n <= 27:
            return n
    except (TypeError, ValueError):
        pass
    p = str(pos).strip().upper()
    # Common FC position codes (LE)
    table = {
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
    return table.get(p, 14)


def _clamp_int_str(val: Any, lo: int, hi: int, default: int) -> str:
    try:
        return str(max(lo, min(hi, int(val))))
    except (TypeError, ValueError):
        return str(default)


def to_gregorian_days(year: int, month: int, day: int) -> int:
    """FIFA players.birthdate encoding (matches lua DATE:ToGregorianDays)."""
    a = (14 - month) // 12
    m = month + 12 * a - 3
    y = year + 4800 - a
    return (
        day
        + (153 * m + 2) // 5
        + y * 365
        + y // 4
        - y // 100
        + y // 400
        - 2331205
    )


def default_birthdate_days(*, age: int = DEFAULT_CREATE_AGE) -> str:
    """Offline fallback birthdate (~age years before 2025-07-01)."""
    age = max(16, min(40, int(age or DEFAULT_CREATE_AGE)))
    # FC careers often sit around mid-2020s
    return str(to_gregorian_days(2025 - age, 6, 15))


def card_to_players_row_data(
    card: CardDict,
    *,
    use_real_face: bool = True,
) -> Dict[str, str]:
    """Map card/AI payload → CreatePlayer string table (safe field subset).

    Always fills body/birthdate defaults. Nation only when known (never England by default).
    Real face: optional headassetid = base player id (FUT Icons) when use_real_face.
    """
    raw = dict(card or {})
    # Capture FUT meta before normalize (may drop unknown keys)
    base_face = resolve_base_face_id(raw)
    nat_pre = resolve_nationality_id(raw)
    names_pre = resolve_player_names(raw)
    height_pre = raw.get("height")
    weight_pre = raw.get("weight")
    card = player_schema.normalize_player_card(raw)
    # Restore FUT meta onto normalized card
    if base_face:
        card["basePlayerEaId"] = base_face
        card["base_id"] = base_face
    if nat_pre is not None:
        card["nationality"] = nat_pre
    if height_pre not in (None, ""):
        card["height"] = height_pre
    if weight_pre not in (None, ""):
        card["weight"] = weight_pre
    if names_pre[0]:
        card["firstName"] = names_pre[0]
    if names_pre[1]:
        card["lastName"] = names_pre[1]
    cats = {c["id"] for c in player_schema.EDIT_CATEGORIES}
    updates = player_schema.card_to_field_updates(card, enabled_categories=cats)
    row: Dict[str, str] = dict(_CORE_DEFAULTS)
    row.update(_VISUAL_DEFAULTS)
    for field, val in updates:
        f = str(field)
        if f not in _CREATE_SAFE_FIELDS:
            continue
        # headassetid applied below (real face vs generic)
        if f == "headassetid":
            continue
        try:
            row[f] = str(int(val))
        except (TypeError, ValueError):
            try:
                row[f] = str(int(float(str(val))))
            except (TypeError, ValueError):
                continue
    for key in ("height", "weight", "bodytypecode", "skintonecode"):
        if card.get(key) not in (None, ""):
            try:
                row[key] = str(int(card[key]))
            except (TypeError, ValueError):
                pass
    # Nation — only if card/FUT provides it (or name map)
    nat = resolve_nationality_id(card)
    if nat is not None:
        row["nationality"] = str(nat)
    else:
        row.pop("nationality", None)

    pos_src = card.get("preferredposition1") or card.get("position")
    if pos_src not in (None, ""):
        row["preferredposition1"] = str(_pos_to_code(pos_src))
    elif "preferredposition1" in row:
        row["preferredposition1"] = str(_pos_to_code(row.get("preferredposition1")))
    else:
        row["preferredposition1"] = "14"
    if card.get("overallrating") is not None:
        row["overallrating"] = _clamp_int_str(card["overallrating"], 1, 99, 75)
    if "potential" not in row or not row.get("potential"):
        row["potential"] = row.get("overallrating", "75")
    for k in ("overallrating", "potential"):
        row[k] = _clamp_int_str(row.get(k), 1, 99, 75)
    row["height"] = _clamp_int_str(row.get("height"), 160, 210, 180)
    row["weight"] = _clamp_int_str(row.get("weight"), 55, 110, 75)
    # bodytypecode reaches 437 in base_players.csv (164 distinct values): the low
    # ids are the generic builds, the high ids are per-player body scans (Pele is
    # 294, Zidane 85). Clamping to 20 silently replaced a real physique with a
    # generic one.
    row["bodytypecode"] = _clamp_int_str(row.get("bodytypecode"), 1, 437, 5)
    row["skillmoves"] = _clamp_int_str(row.get("skillmoves"), 0, 4, 3)
    row["weakfootabilitytypecode"] = _clamp_int_str(
        row.get("weakfootabilitytypecode"), 1, 5, 3
    )
    row["preferredposition2"] = "-1"
    row["preferredposition3"] = "-1"
    row["preferredposition4"] = "-1"
    age_hint = card.get("age") or card.get("player_age")
    try:
        age_i = int(age_hint) if age_hint not in (None, "") else DEFAULT_CREATE_AGE
    except (TypeError, ValueError):
        age_i = DEFAULT_CREATE_AGE
    if card.get("birthdate") not in (None, ""):
        try:
            row["birthdate"] = str(int(card["birthdate"]))
        except (TypeError, ValueError):
            row["birthdate"] = default_birthdate_days(age=age_i)
    else:
        row["birthdate"] = default_birthdate_days(age=age_i)
    for k, v in _VISUAL_DEFAULTS.items():
        row.setdefault(k, v)

    # Never put Icon head on CreatePlayer payload (native freeze). Lua forces generic.
    _ = (use_real_face, base_face)
    row.pop("headassetid", None)
    row["hashighqualityhead"] = "0"
    row["headclasscode"] = "1"
    # Never send dictionary name ids (explicit 0 freezes CreatePlayer).
    for _nid in (
        "firstnameid",
        "lastnameid",
        "commonnameid",
        "playerjerseynameid",
        "usercaneditname",
    ):
        row.pop(_nid, None)
    return row


def build_import_style_field_updates(
    card: Mapping[str, Any],
    *,
    use_real_face: bool = True,
) -> List[Tuple[str, int]]:
    """Same field list import/apply would write — for dummy overwrite."""
    raw = enrich_card_for_add(dict(card or {}))
    norm = player_schema.normalize_player_card(raw)
    # Prefer all LE categories so the free agent becomes a full card clone
    cats = {c["id"] for c in player_schema.EDIT_CATEGORIES}
    updates = player_schema.card_to_field_updates(norm, enabled_categories=cats)
    # Ensure body/skill defaults from create row when catalog omitted them
    row = card_to_players_row_data(raw, use_real_face=use_real_face)
    have = {f for f, _ in updates}
    for k, v in row.items():
        if k in ("headassetid", "hashighqualityhead", "headclasscode"):
            continue  # face applied separately via deferred id
        if k in have:
            continue
        try:
            updates.append((k, int(v)))
        except (TypeError, ValueError):
            continue
    return updates


def generate_add_to_team_lua(
    card: CardDict,
    *,
    teamid: int = 0,
    mode: str = "auto",
    preferred_playerid: Optional[int] = None,
    dummy_candidate_ids: Optional[Sequence[int]] = None,
    transfersum: int = 0,
    wage: int = 5000,
    contract_months: int = 60,
    use_real_face: bool = True,
) -> str:
    """Emit SAFE v15 LE Lua (see add_team_lua.render).

    mode:
      auto | dummy — free-agent overwrite + transfer (NO CreatePlayer)
      create — CreatePlayer opt-in only (freeze risk)
    """
    from . import add_team_lua

    raw_card = enrich_card_for_add(dict(card or {}))
    name = str(raw_card.get("name") or "New Player")
    first, sur, jersey = resolve_player_names(raw_card)
    base_face = resolve_base_face_id(raw_card) if use_real_face else 0
    nation = resolve_nationality_id(raw_card)
    row = card_to_players_row_data(raw_card, use_real_face=use_real_face)
    if nation is not None:
        row["nationality"] = str(nation)
    payload = minimize_create_row(row)
    if nation is not None:
        payload["nationality"] = str(nation)
    field_updates = build_import_style_field_updates(
        raw_card, use_real_face=use_real_face
    )
    gen_id = allocate_generated_playerid(seed=name, preferred=preferred_playerid)
    tid = int(teamid or 0)
    dummies = [
        int(x)
        for x in (dummy_candidate_ids or [])
        if int(x) > 0 and int(x) < GENERATED_ID_MIN
    ][:DUMMY_POOL_LIMIT]
    mode_s = (mode or "auto").strip().lower()
    if mode_s not in ("auto", "create", "dummy"):
        mode_s = "auto"
    # auto is dummy-first
    if mode_s == "auto":
        mode_s = "auto"  # lua treats auto as primary_dummy

    age_for_lua = DEFAULT_CREATE_AGE
    try:
        if raw_card.get("age") not in (None, ""):
            age_for_lua = max(16, min(40, int(raw_card["age"])))
    except (TypeError, ValueError):
        pass
    fallback_bday = row.get("birthdate") or default_birthdate_days(age=age_for_lua)
    status_path = str((paths.queue_dir() / "_job_status.txt").resolve()).replace("\\", "/")
    crash_log_path = str((paths.queue_dir() / "_add_team_crash.log").resolve()).replace(
        "\\", "/"
    )

    return add_team_lua.render_add_to_team_lua(
        name=name,
        first=first,
        sur=sur,
        jersey=jersey,
        teamid=tid,
        mode=mode_s,
        preferred_create_id=gen_id,
        player_row=payload,
        field_updates=field_updates,
        dummy_ids=dummies,
        transfersum=transfersum,
        wage=wage,
        contract_months=contract_months,
        age=age_for_lua,
        fallback_birthdate=fallback_bday,
        use_real_face=bool(use_real_face and base_face > 0),
        base_face_id=int(base_face or 0),
        nation_id=int(nation) if nation is not None else None,
        gen_min=GENERATED_ID_MIN,
        gen_max=GENERATED_ID_MAX,
        crash_log_path=crash_log_path,
        status_path=status_path,
    )


def describe_add_plan(
    card: CardDict,
    *,
    teamid: int = 0,
    mode: str = "auto",
    dummy_pool: Optional[Sequence[Mapping[str, Any]]] = None,
    protected_ids: Optional[Iterable[int]] = None,
) -> Dict[str, Any]:
    """Offline plan summary for CLI/UI (no game)."""
    tid = int(teamid or 0)
    mode_s = (mode or "auto").strip().lower()
    if mode_s not in ("auto", "create", "dummy"):
        mode_s = "auto"
    gen = allocate_generated_playerid(seed=str(card.get("name") or ""))
    dummies: List[int] = []
    if dummy_pool is not None:
        dummies = select_worst_dummies(
            dummy_pool, protected_ids=protected_ids, limit=DUMMY_POOL_LIMIT
        )
    row = card_to_players_row_data(card)
    fields = build_import_style_field_updates(card)
    path = "create" if mode_s == "create" else "dummy"
    visual_keys = [
        k
        for k in (
            "headassetid",
            "headclasscode",
            "hashighqualityhead",
            "bodytypecode",
            "height",
            "weight",
            "hairtypecode",
            "skintonecode",
        )
        if k in row
    ]
    notes = (
        "v15 create opt-in: CreatePlayer + transfer (freeze risk)"
        if path == "create"
        else "v15 default: free-agent dummy overwrite + import fields + TransferPlayer (no CreatePlayer)"
    )
    return {
        "mode": mode_s,
        "path": path,
        "teamid": tid,
        "preferred_create_id": gen,
        "dummy_candidates": dummies,
        "dummy_count": len(dummies),
        "visual_fields": visual_keys,
        "field_count": len(fields),
        "name": card.get("name"),
        "height": row.get("height"),
        "weight": row.get("weight"),
        "bodytypecode": row.get("bodytypecode"),
        "birthdate": row.get("birthdate"),
        "generated_id_min": GENERATED_ID_MIN,
        "safe": path == "dummy",
        "notes": notes,
    }
