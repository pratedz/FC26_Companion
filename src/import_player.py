"""LE-parity Import Player for Cards (Apply / Create + optional Name/Head/Birth)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from . import add_player
from . import paths
from . import player_apply
from . import player_schema
from .card_types import CardDict

# LE Advanced Import topics (stats + body/playstyles). Face/career added by Copy toggles.
LE_IMPORT_CATEGORIES: Tuple[str, ...] = (
    "attributes",
    "ratings",
    "skills",
    "positions",
    "playstyles",
    "body",
)

_HEAD_FIELDS = (
    "headassetid",
    "hashighqualityhead",
    "headclasscode",
    "headtypecode",
    "headvariation",
)

_NAME_SPLIT_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NameParts:
    first: str = ""
    surname: str = ""
    common: str = ""
    jersey: str = ""

    @property
    def has_any(self) -> bool:
        return bool(self.first or self.surname or self.common or self.jersey)


@dataclass
class EnrichBits:
    """Resolved identity bits for Import Copy toggles."""

    name: NameParts = field(default_factory=NameParts)
    head: Dict[str, int] = field(default_factory=dict)
    birthdate: Optional[int] = None
    nationality: Optional[int] = None
    sources: Dict[str, str] = field(default_factory=dict)  # bit -> where

    def available(self) -> Dict[str, bool]:
        # base_id fallback is a guess (often generic) — never auto-enable Copy Head.
        head_src = self.sources.get("head") or ""
        head_ok = bool(self.head.get("headassetid")) and head_src not in ("", "base_id")
        return {
            "name": self.name.has_any,
            "head": head_ok,
            "birthdate": self.birthdate is not None and int(self.birthdate) > 0,
        }


@dataclass(frozen=True)
class ImportRequest:
    mode: str  # "apply" | "create"
    card: CardDict
    target_playerid: Optional[int] = None
    copy_name: bool = True
    copy_head: bool = True
    copy_birthdate: bool = True
    categories: Optional[Sequence[str]] = None  # None => LE Advanced
    team_id: Optional[int] = None  # create only; 0 = user team


@dataclass
class ImportPlan:
    mode: str
    card: CardDict
    target_playerid: Optional[int]
    categories: List[str]
    enrich: EnrichBits
    copy_name: bool
    copy_head: bool
    copy_birthdate: bool
    preflight: str
    warnings: List[str] = field(default_factory=list)

    def field_categories(self) -> Set[str]:
        cats = set(self.categories)
        if self.copy_head:
            cats.add("face")
        # Only Copy Birthdate opens the career category (birthdate, nationality,
        # contract, …). Copy Name writes editedplayernames + usercaneditname via
        # name_parts in player_apply — not a bulk career-field dump.
        if self.copy_birthdate:
            cats.add("career")
        return cats


def split_display_name(name: str) -> NameParts:
    raw = (name or "").strip()
    if not raw:
        return NameParts()
    # Drop trailing (role) / ★ noise
    raw = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", raw).strip()
    parts = [p for p in _NAME_SPLIT_RE.split(raw) if p]
    if not parts:
        return NameParts()
    if len(parts) == 1:
        return NameParts(first=parts[0], surname=parts[0], common=parts[0], jersey=parts[0][:15])
    first = parts[0]
    surname = parts[-1]
    common = raw if len(raw) <= 24 else surname
    jersey = surname[:15] if surname else first[:15]
    return NameParts(first=first, surname=surname, common=common, jersey=jersey)


def _intish(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def _base_players_index() -> Dict[int, Dict[str, str]]:
    """playerid -> selected columns from LE base_players.csv (lazy, cached)."""
    path = paths.base_players_csv_path()
    out: Dict[int, Dict[str, str]] = {}
    if not path.is_file():
        return out
    want = {
        "playerid",
        "firstname",
        "surname",
        "commonname",
        "playerjerseyname",
        "birthdate",
        "nationality",
        "headassetid",
        "hashighqualityhead",
        "headclasscode",
        "headtypecode",
        "headvariation",
    }
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                pid = _intish(row.get("playerid"))
                if pid is None or pid <= 0:
                    continue
                slim = {k: (row.get(k) or "").strip() for k in want if k in row}
                out[pid] = slim
    except OSError:
        return {}
    return out


def lookup_base_player(playerid: int) -> Optional[Dict[str, str]]:
    if playerid <= 0:
        return None
    return _base_players_index().get(int(playerid))


def card_base_id(card: Mapping[str, Any]) -> Optional[int]:
    for key in ("baseId", "baseid", "eaId", "eaid", "playerid", "base_playerid"):
        v = _intish(card.get(key))
        if v is not None and v > 0:
            # Prefer true base if present
            if key in ("baseId", "baseid", "base_playerid"):
                return v
    # Fall through last numeric id
    for key in ("baseId", "baseid", "base_playerid", "eaId", "eaid", "playerid"):
        v = _intish(card.get(key))
        if v is not None and v > 0:
            return v
    return None


def resolve_enrich(card: Mapping[str, Any]) -> EnrichBits:
    """Card fields first, then LE base_players by base/playerid, name from display string."""
    bits = EnrichBits()
    c = dict(card)

    # --- name ---
    first = str(c.get("firstname") or c.get("firstName") or "").strip()
    sur = str(c.get("surname") or c.get("lastName") or c.get("lastname") or "").strip()
    common = str(c.get("commonname") or c.get("commonName") or "").strip()
    jersey = str(c.get("playerjerseyname") or c.get("jerseyName") or "").strip()
    src_name = "card"
    if not (first or sur or common):
        split = split_display_name(str(c.get("name") or ""))
        first, sur, common, jersey = split.first, split.surname, split.common, split.jersey
        src_name = "card_name"
    if first or sur or common or jersey:
        bits.name = NameParts(
            first=first or common or sur,
            surname=sur or common or first,
            common=common or sur or first,
            jersey=(jersey or sur or common or first)[:15],
        )
        bits.sources["name"] = src_name

    # --- base row ---
    base_id = card_base_id(c)
    row = lookup_base_player(base_id) if base_id else None
    if row is None and _intish(c.get("playerid")):
        row = lookup_base_player(int(c["playerid"]))

    # Fill name from base_players text columns if still empty
    if not bits.name.has_any and row:
        bf = (row.get("firstname") or "").strip()
        bs = (row.get("surname") or "").strip()
        bc = (row.get("commonname") or "").strip()
        bj = (row.get("playerjerseyname") or "").strip()
        if bf or bs or bc:
            bits.name = NameParts(
                first=bf or bc or bs,
                surname=bs or bc or bf,
                common=bc or bs or bf,
                jersey=(bj or bs or bc or bf)[:15],
            )
            bits.sources["name"] = "base_players"

    # --- head ---
    head: Dict[str, int] = {}
    for fk in _HEAD_FIELDS:
        v = _intish(c.get(fk))
        if v is not None:
            head[fk] = v
    if head.get("headassetid"):
        bits.sources["head"] = "card"
    elif row:
        for fk in _HEAD_FIELDS:
            v = _intish(row.get(fk))
            if v is not None:
                head[fk] = v
        if head.get("headassetid"):
            bits.sources["head"] = "base_players"
    elif base_id:
        # Resolve the real face quartet from base_players.csv rather than
        # guessing. hashighqualityhead is a mesh-pipeline selector, NOT an
        # "is real face" flag, and it is 0 for ~86% of players (Zidane, Pele and
        # van Basten among them) — hardcoding it to 1 pointed the game at an HQ
        # head that does not exist, which is why imports came out faceless.
        try:
            from . import base_players as _bp

            profile = _bp.face_profile(int(base_id))
        except Exception:  # noqa: BLE001
            profile = None
        if profile and profile.get("real"):
            head = {
                "headassetid": int(profile["headassetid"]),
                "hashighqualityhead": int(profile["hashighqualityhead"]),
                "headclasscode": 0,
                "headvariation": int(profile.get("headvariation") or 0),
            }
            ht = profile.get("headtypecode")
            if ht is not None:
                head["headtypecode"] = int(ht)
            bits.sources["head"] = "base_players_face"
        else:
            # No scanned head for this player — the always-safe generic combo.
            head = {
                "headassetid": 0,
                "hashighqualityhead": 0,
                "headclasscode": 1,
                "headvariation": 0,
            }
            bits.sources["head"] = "generic_safe"
    if head:
        bits.head = head

    # --- birthdate ---
    bd = _intish(c.get("birthdate"))
    if bd and bd > 0:
        bits.birthdate = bd
        bits.sources["birthdate"] = "card"
    elif row:
        bd = _intish(row.get("birthdate"))
        if bd and bd > 0:
            bits.birthdate = bd
            bits.sources["birthdate"] = "base_players"

    # --- nationality (always useful for create) ---
    nat = _intish(c.get("nationality") or c.get("nationEaId") or c.get("nation_id"))
    if nat is None and row:
        nat = _intish(row.get("nationality"))
        if nat is not None:
            bits.sources["nationality"] = "base_players"
    elif nat is not None:
        bits.sources["nationality"] = "card"
    bits.nationality = nat

    return bits


def le_import_categories(extra: Optional[Iterable[str]] = None) -> List[str]:
    cats = list(LE_IMPORT_CATEGORIES)
    if extra:
        for c in extra:
            if c and c not in cats:
                cats.append(str(c))
    return cats


def build_import_plan(req: ImportRequest) -> ImportPlan:
    mode = (req.mode or "apply").strip().lower()
    if mode not in ("apply", "create"):
        raise ValueError("mode must be 'apply' or 'create'")

    # Enrich from raw card first — normalize strips baseId / alt keys.
    raw = dict(req.card or {})
    enrich = resolve_enrich(raw)
    avail = enrich.available()
    card = player_schema.normalize_player_card(raw)

    copy_name = bool(req.copy_name) and avail["name"]
    copy_head = bool(req.copy_head) and avail["head"]
    copy_birth = bool(req.copy_birthdate) and avail["birthdate"]

    warnings: List[str] = []
    if req.copy_name and not avail["name"]:
        warnings.append("Name: no data (card/base empty)")
    if req.copy_head and not avail["head"]:
        if enrich.sources.get("head") == "base_id" or (
            not enrich.head.get("headassetid")
            and _intish(raw.get("baseId") or raw.get("base_id") or raw.get("basePlayerEaId"))
        ):
            warnings.append(
                "Head: only base_id guess (not treated as real face; pick a card with headassetid)"
            )
        else:
            warnings.append("Head: no headassetid found")
    if req.copy_birthdate and not avail["birthdate"]:
        warnings.append("Birthdate: not found")

    if mode == "create" and copy_head:
        warnings.append("Create + Head can freeze FC26 — prefer generic head")

    cats = list(req.categories) if req.categories is not None else le_import_categories()
    # Strip face/career unless copy needs them — kept via field_categories()
    cats = [c for c in cats if c in set(LE_IMPORT_CATEGORIES) or c in ("face", "career")]

    target = req.target_playerid
    if mode == "apply":
        if target is None or int(target) <= 0:
            raise ValueError("Apply mode needs a target playerid")
        target = int(target)

    # Merge identity into working card for field emission
    work = dict(card)
    if copy_head:
        work.update(enrich.head)
    if copy_birth and enrich.birthdate is not None:
        work["birthdate"] = int(enrich.birthdate)
    else:
        # Do not emit catalog/normalized birthdate when Birthdate toggle is off
        work["birthdate"] = ""
    # Nationality only with Copy Birthdate (same career identity gate)
    if copy_birth and enrich.nationality is not None and not _intish(work.get("nationality")):
        work["nationality"] = int(enrich.nationality)
    elif not copy_birth:
        work["nationality"] = ""
    if copy_name:
        work["usercaneditname"] = 1
    else:
        work["usercaneditname"] = ""

    plan = ImportPlan(
        mode=mode,
        card=work,
        target_playerid=target,
        categories=cats,
        enrich=enrich,
        copy_name=copy_name,
        copy_head=copy_head,
        copy_birthdate=copy_birth,
        preflight="",
        warnings=warnings,
    )
    plan.preflight = _preflight_line(plan)
    return plan


def _preflight_line(plan: ImportPlan) -> str:
    name = str(plan.card.get("name") or "card")
    bits = []
    if plan.copy_name:
        bits.append("name")
    if plan.copy_head:
        bits.append("head")
    if plan.copy_birthdate:
        bits.append("birth")
    copy_s = "+".join(bits) if bits else "stats-only"
    cats = ",".join(sorted(plan.field_categories()))
    if plan.mode == "apply":
        return f"Apply {name} -> id {plan.target_playerid} · {copy_s} · cats={cats}"
    return f"Create {name} · {copy_s} · cats={cats}"


def generate_import_apply_lua(plan: ImportPlan) -> str:
    if plan.mode != "apply" or not plan.target_playerid:
        raise ValueError("apply plan required")
    cats = plan.field_categories()
    name_parts = plan.enrich.name if plan.copy_name else None
    return player_apply.generate_apply_player_lua(
        plan.card,
        int(plan.target_playerid),
        enabled_categories=cats,
        name_parts=(
            {
                "firstname": name_parts.first,
                "surname": name_parts.surname,
                "commonname": name_parts.common,
                "playerjerseyname": name_parts.jersey,
            }
            if name_parts and name_parts.has_any
            else None
        ),
    )


def prepare_create_card(plan: ImportPlan) -> CardDict:
    """Card payload for add_player path with Copy flags applied."""
    c = dict(plan.card)
    if plan.copy_name and plan.enrich.name.has_any:
        n = plan.enrich.name
        c["firstname"] = n.first
        c["surname"] = n.surname
        c["commonname"] = n.common
        c["playerjerseyname"] = n.jersey
        # Prefer common as display name for Lua FIRST/SUR split helpers
        if n.common:
            c["name"] = n.common if not (n.first and n.surname) else f"{n.first} {n.surname}"
    if plan.copy_birthdate and plan.enrich.birthdate:
        c["birthdate"] = int(plan.enrich.birthdate)
    # Head: only pass real face if explicitly requested
    if plan.copy_head and plan.enrich.head.get("headassetid"):
        c.update(plan.enrich.head)
        c["_import_use_real_face"] = True
    else:
        c.pop("headassetid", None)
        c["hashighqualityhead"] = 0
        c["headclasscode"] = 1
        c["_import_use_real_face"] = False
    return c
