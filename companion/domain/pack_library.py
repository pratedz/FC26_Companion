"""Safe v2 equivalents for v1 product packs (``src/product.py`` PACKS).

Packs are multi-step convenience wrappers over profiles. Each pack is either
a typed multi-op Job, a single profile job, or honestly ``pending`` when any
required step still lacks a verified v3 contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .job import (
    Job,
    JobValidationError,
    Op,
    op_career_set,
    op_export_squad,
    op_growth_sync,
    op_ping,
)
from .profile_library import build_job as build_profile_job


@dataclass(frozen=True, slots=True)
class PackAction:
    id: str
    label: str
    description: str
    category: str
    mode: str  # native | pending
    reason: str = ""
    profile_ids: tuple[str, ...] = ()


# Packs whose steps are all natively available (profile ids).
_NATIVE_CHAIN: Mapping[str, tuple[str, ...]] = {
    "matchday": ("matchday_pack",),
    "squad_boost": ("squad_boost_pack",),
    "morale_day": ("full_form_morale_sharpness",),
    "never_retire": ("never_retire",),
    "youth_regen_farm": ("ovr_1_pot_99",),
    "max_growth": ("pot_99_all",),
    "export_squad": ("export_user_squad",),
    # Partial visual pass (heads/socks still pending) — still useful.
    "mass_visual": ("untuck_shirts", "tight_shirts"),
    # Partial cleanup without fix_heads.
    "career_cleanup": ("never_retire", "modifier_zero"),
}

# Workflow packs that are pure career.set + optional export, not profile chains.
_WORKFLOW: Mapping[str, Mapping[str, Any]] = {
    "match_ready": {
        "label": "Match Ready",
        "description": "Export squad snapshot + match-day fitness/sharpness.",
        "category": "workflow",
        "export": True,
        "career": {"fitness": 100, "sharpness": 100},
    },
    "signing_settle": {
        "label": "New Signing Settle",
        "description": "Full squad fitness + form + morale + sharpness.",
        "category": "workflow",
        "career": {"fitness": 100, "form": 100, "morale": 120, "sharpness": 100},
    },
    "pre_match_full": {
        "label": "Pre-Match Full",
        "description": "Match day pack then form/morale/sharpness blast.",
        "category": "workflow",
        "career": {"fitness": 100, "form": 100, "morale": 120, "sharpness": 100},
    },
    "club_refresh": {
        "label": "Club Refresh",
        "description": "Export squad + full squad boost (board + fitness).",
        "category": "workflow",
        "export": True,
        "career": {"fitness": 100, "form": 100, "morale": 120, "sharpness": 100},
    },
    "season_kickoff": {
        "label": "Season Kickoff",
        "description": "Fitness/form baseline for the user team (contracts/unlocks still pending).",
        "category": "workflow",
        "career": {"fitness": 100, "form": 50, "morale": 80, "sharpness": 70},
        "note": "v1 also ran never_retire + contracts + unlocks; only the career readiness half is native here.",
    },
    # Alias used by the Automations quick rail (same job as pre_match_full).
    "pre_match": {
        "label": "Pre-Match Full",
        "description": "Match day pack then form/morale/sharpness blast.",
        "category": "workflow",
        "career": {"fitness": 100, "form": 100, "morale": 120, "sharpness": 100},
    },
}

# Companion-only quick-rail extras (not in v1 product.PACKS).
_COMPANION_EXTRAS: Mapping[str, Mapping[str, Any]] = {
    "growth_sync": {
        "label": "Resync Growth Plans",
        "description": "Copy current player attributes into each squad member's development plan.",
        "category": "growth",
        "mode": "native",
        "reason": "Typed growth_sync v2 for the user team.",
    },
    "ping": {
        "label": "Ping worker",
        "description": "Zero-side-effect liveness check (diag.ping).",
        "category": "diag",
        "mode": "native",
        "reason": "Typed diag.ping.",
    },
    "recurring_fitness": {
        "label": "Auto Full Fitness (Daily)",
        "description": "Recurring fitness top-up on career events.",
        "category": "recurring",
        "mode": "pending",
        "reason": (
            "Recurring host-side event scheduling is not a one-shot queue job. "
            "Use Match Ready or full_fitness for a one-shot pass."
        ),
    },
}

# Ordered Automations quick rail — every id resolves via build_job / actions.
QUICK_RAIL_IDS: tuple[str, ...] = (
    "match_ready",
    "squad_boost",
    "growth_sync",
    "club_refresh",
    "season_kickoff",
    "signing_settle",
    "pre_match",
    "recurring_fitness",
    "export_squad",
    "ping",
)

_PENDING: Mapping[str, str] = {
    "contracts": "Needs a verified contract-date operation.",
    "contracts_plus_cpu": "Needs a verified contract-date operation.",
    "shoe_random": "Random visual writes need a reproducible safe contract.",
    "export_season": "Needs dedicated season-stats and transfer-history export ops.",
    "unlocks_all": "Needs verified unlock operations for boots and manager clothes.",
}

_MASS_PROFILE_IDS = frozenset({
    "never_retire", "force_retire", "modifier_zero", "pot_99_all",
    "ovr_99_pot_99", "ovr_1_pot_99", "untuck_shirts", "tight_shirts",
})

_MASS_PENDING_REASON = (
    "This pack includes a whole-save player edit. It is disabled until its "
    "write is implemented as a resumable, per-tick batch."
)


def actions(raw_packs: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[PackAction, ...]:
    """Inventory product packs with truthful migration status."""
    packs = dict(raw_packs or {})
    if not packs:
        # Fall back to the known set so the UI still lists v1 ids when product
        # module is unavailable (frozen builds always ship profiles; packs live in code).
        packs = {pid: {"id": pid, "label": pid, "description": "", "category": "other",
                        "profile_ids": list(chain)}
                 for pid, chain in _NATIVE_CHAIN.items()}
        for pid, meta in _WORKFLOW.items():
            packs[pid] = {
                "id": pid, "label": meta["label"], "description": meta["description"],
                "category": meta["category"], "profile_ids": [],
            }
        for pid, reason in _PENDING.items():
            packs[pid] = {"id": pid, "label": pid, "description": reason, "category": "other",
                          "profile_ids": []}

    out: list[PackAction] = []
    for pid, raw in packs.items():
        pack_id = str(raw.get("id") or pid)
        label = str(raw.get("label") or pack_id)
        description = str(raw.get("description") or "")
        category = str(raw.get("category") or "other")
        profile_ids = tuple(str(x) for x in (raw.get("profile_ids") or ()))

        if pack_id in _PENDING:
            out.append(PackAction(
                id=pack_id, label=label, description=description, category=category,
                mode="pending", reason=_PENDING[pack_id], profile_ids=profile_ids,
            ))
            continue
        if pack_id in _WORKFLOW:
            meta = _WORKFLOW[pack_id]
            note = str(meta.get("note") or "Runs as typed Career Mode operation(s).")
            out.append(PackAction(
                id=pack_id, label=str(meta.get("label") or label),
                description=str(meta.get("description") or description),
                category=str(meta.get("category") or category),
                mode="native", reason=note, profile_ids=profile_ids,
            ))
            continue
        chain = _NATIVE_CHAIN.get(pack_id) or profile_ids
        if not chain:
            out.append(PackAction(
                id=pack_id, label=label, description=description, category=category,
                mode="pending", reason="No safe v2 pack mapping is defined yet.",
                profile_ids=profile_ids,
            ))
            continue
        mass = any(p in _MASS_PROFILE_IDS for p in chain)
        out.append(PackAction(
            id=pack_id, label=label, description=description, category=category,
            mode="pending" if mass else "native",
            reason=_MASS_PENDING_REASON if mass else "Runs as typed v3 job(s).",
            profile_ids=tuple(chain),
        ))
    # Workflow aliases (e.g. pre_match) and companion extras may not exist in
    # product.PACKS. Merge them so CLI packs / run-pack and UI share one inventory.
    seen = {p.id for p in out}
    for wid, meta in _WORKFLOW.items():
        if wid in seen:
            continue
        note = str(meta.get("note") or "Runs as typed Career Mode operation(s).")
        out.append(PackAction(
            id=wid,
            label=str(meta.get("label") or wid),
            description=str(meta.get("description") or ""),
            category=str(meta.get("category") or "workflow"),
            mode="native",
            reason=note,
            profile_ids=(),
        ))
        seen.add(wid)
    for eid, meta in _COMPANION_EXTRAS.items():
        if eid in seen:
            continue
        out.append(PackAction(
            id=eid,
            label=str(meta["label"]),
            description=str(meta["description"]),
            category=str(meta["category"]),
            mode=str(meta["mode"]),
            reason=str(meta["reason"]),
        ))
        seen.add(eid)
    return tuple(sorted(out, key=lambda p: (p.category, p.label.lower())))


def quick_rail(raw_packs: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[PackAction, ...]:
    """Ordered top-of-Automations packs — single registry, no dual field maps."""
    by_id = {p.id: p for p in actions(raw_packs if raw_packs is not None else load_v1_packs())}
    ordered: list[PackAction] = []
    for pid in QUICK_RAIL_IDS:
        item = by_id.get(pid)
        if item is not None:
            ordered.append(item)
    return tuple(ordered)


def build_job(pack_id: str, *, teamid: int | None = None) -> Job:
    """Build one protocol-v3 Job for a pack, or raise JobValidationError."""
    if pack_id in _PENDING:
        raise JobValidationError(_PENDING[pack_id])
    if pack_id == "recurring_fitness":
        raise JobValidationError(_COMPANION_EXTRAS["recurring_fitness"]["reason"])
    if pack_id == "ping":
        return Job(
            ops=(op_ping(note="automations.ping"),),
            label="Ping worker",
            origin="ui.packs.ping",
            require_cm=False,
        )
    if pack_id == "growth_sync":
        return Job(
            ops=(op_growth_sync("growth", scope="user_team", source="from_players_table"),),
            label="Resync Growth Plans",
            origin="ui.packs.growth_sync",
            require_cm=True,
            budget_ms=5000,
        )

    if pack_id in _WORKFLOW:
        meta = _WORKFLOW[pack_id]
        ops: list[Op] = []
        if meta.get("export"):
            ops.append(op_export_squad("export"))
        career_fields = dict(meta.get("career") or {})
        if career_fields:
            ops.append(op_career_set(
                "career", scope="user_senior_team", teamid=teamid,
                **career_fields,
            ))
        if not ops:
            raise JobValidationError(f"workflow pack {pack_id!r} has no ops")
        return Job(
            ops=tuple(ops),
            label=str(meta.get("label") or pack_id),
            origin=f"ui.packs.{pack_id}",
            require_cm=True,
            budget_ms=5000 if len(ops) > 1 else 200,
        )

    chain = _NATIVE_CHAIN.get(pack_id)
    if not chain:
        raise JobValidationError(f"No safe v2 mapping for pack {pack_id!r}")

    ops_list: list[Op] = []
    for profile_id in chain:
        job = build_profile_job(profile_id, teamid=teamid)
        ops_list.extend(job.ops)
    if not ops_list:
        raise JobValidationError(f"pack {pack_id!r} produced no ops")
    return Job(
        ops=tuple(ops_list),
        label=pack_id.replace("_", " ").title(),
        origin=f"ui.packs.{pack_id}",
        require_cm=True,
        budget_ms=5000 if len(ops_list) > 1 else 200,
    )


def load_v1_packs() -> Mapping[str, Mapping[str, Any]]:
    """Best-effort import of v1 product.PACKS without hard-failing offline tests."""
    try:
        from src.product import PACKS  # type: ignore[import-not-found]
        return {str(k): dict(v) for k, v in PACKS.items()}
    except Exception:
        return {}
