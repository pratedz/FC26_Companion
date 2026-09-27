"""Product engine — packs, turbo apply, batch, arm status, catalog rebuild.

Performance-first defaults: clear stale, short poll, metadata jobs, sticky cats.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from . import actions
from . import apply_service
from . import card_match
from . import companion_config
from . import job_history
from . import le_apply
from . import lua_resolve
from . import paths
from . import profiles as profiles_mod
from . import protocol
from . import snapshot_store
from . import target_players
from .card_types import CardDict

OnTick = Optional[Callable[[float, str], None]]

# ── Product packs (one-shot + multi-snippet) ─────────────────────────

PACKS: Dict[str, Dict[str, Any]] = {
    # One-shot profile ids — multi-id packs used clear_stale between jobs and
    # dropped intermediate Lua (fitness never applied). Use combined snippets.
    "matchday": {
        "id": "matchday",
        "label": "Match Day Pack",
        "description": "Max fitness + sharpness for user senior team (pre-match).",
        "profile_ids": ["matchday_pack"],
        "category": "boost",
    },
    "squad_boost": {
        "id": "squad_boost",
        "label": "Full Squad Boost",
        "description": "Fitness + form + morale + sharpness in one blast.",
        "profile_ids": ["squad_boost_pack"],
        "category": "boost",
    },
    "morale_day": {
        "id": "morale_day",
        "label": "Morale Day",
        "description": "Form + morale + sharpness combined.",
        "profile_ids": ["full_form_morale_sharpness"],
        "category": "boost",
    },
    "contracts": {
        "id": "contracts",
        "label": "Contracts 4y",
        "description": "Extend user team contracts.",
        "profile_ids": ["extend_contracts"],
        "category": "career",
    },
    "contracts_plus_cpu": {
        "id": "contracts_plus_cpu",
        "label": "User + CPU Contracts",
        "description": "Extend user then CPU team contracts.",
        "profile_ids": ["extend_contracts", "extend_cpu_contracts"],
        "category": "career",
    },
    "never_retire": {
        "id": "never_retire",
        "label": "Never Retire",
        "description": "isretiring=0 all players.",
        "profile_ids": ["never_retire"],
        "category": "mass",
    },
    "career_cleanup": {
        "id": "career_cleanup",
        "label": "Career Cleanup",
        "description": "Never retire + clear modifiers + fix heads.",
        "profile_ids": ["never_retire", "modifier_zero", "fix_heads"],
        "category": "career",
    },
    "mass_visual": {
        "id": "mass_visual",
        "label": "Mass Visual",
        "description": "Fix heads + untuck + tight shirts + medium socks.",
        "profile_ids": ["fix_heads", "untuck_shirts", "tight_shirts", "medium_socks"],
        "category": "visual",
    },
    "shoe_random": {
        "id": "shoe_random",
        "label": "Randomize Shoes",
        "description": "Random shoe models for basic boots (CE randomize_shoe_models).",
        "profile_ids": ["randomize_shoe_models"],
        "category": "visual",
    },
    "youth_regen_farm": {
        "id": "youth_regen_farm",
        "label": "Regen Farm (1 ovr / 99 pot)",
        "description": "CE-style 1 ovr + 99 pot mass edit.",
        "profile_ids": ["ovr_1_pot_99"],
        "category": "mass",
    },
    "max_growth": {
        "id": "max_growth",
        "label": "Max Growth (99 pot)",
        "description": "99 potential for all players.",
        "profile_ids": ["pot_99_all"],
        "category": "mass",
    },
    "export_squad": {
        "id": "export_squad",
        "label": "Export Squad",
        "description": "Refresh current_squad.json for target search.",
        "profile_ids": ["export_user_squad"],
        "category": "squad",
    },
    "export_season": {
        "id": "export_season",
        "label": "Export Season Bundle",
        "description": "Season stats + transfer history exports.",
        "profile_ids": ["export_season_stats", "export_transfer_history"],
        "category": "export",
    },
    "unlocks_all": {
        "id": "unlocks_all",
        "label": "Unlock Boots + Clothes",
        "description": "Unlock boots and manager clothes.",
        "profile_ids": ["unlock_boots", "unlock_mgr_clothes"],
        "category": "unlocks",
    },
    # ── Synergy workflows (multi-feature chains) ────────────────────
    "match_ready": {
        "id": "match_ready",
        "label": "Match Ready",
        "description": "Export squad snapshot + Match Day fitness/sharpness.",
        "profile_ids": ["export_user_squad", "matchday_pack"],
        "category": "workflow",
    },
    "signing_settle": {
        "id": "signing_settle",
        "label": "New Signing Settle",
        "description": "After Add team: full squad fitness + form + morale + sharpness.",
        "profile_ids": ["squad_boost_pack"],
        "category": "workflow",
    },
    "season_kickoff": {
        "id": "season_kickoff",
        "label": "Season Kickoff",
        "description": "Never retire + user contracts + unlock boots/clothes.",
        "profile_ids": [
            "never_retire",
            "extend_contracts",
            "unlock_boots",
            "unlock_mgr_clothes",
        ],
        "category": "workflow",
    },
    "pre_match_full": {
        "id": "pre_match_full",
        "label": "Pre-Match Full",
        "description": "Match Day pack then form/morale/sharpness blast.",
        "profile_ids": ["matchday_pack", "full_form_morale_sharpness"],
        "category": "workflow",
    },
    "club_refresh": {
        "id": "club_refresh",
        "label": "Club Refresh",
        "description": "Export squad + full squad boost (fresh board + match fitness).",
        "profile_ids": ["export_user_squad", "squad_boost_pack"],
        "category": "workflow",
    },
}


def list_packs() -> List[Dict[str, Any]]:
    return [dict(p) for p in PACKS.values()]


_turbo_defaults_cache: Optional[tuple[float, Dict[str, Any]]] = None
_TURBO_DEFAULTS_TTL = 2.0  # seconds — avoid re-reading companion_config each boost


def sticky_categories() -> Optional[List[str]]:
    cfg = companion_config.load_config()
    cats = cfg.get("apply_categories")
    if isinstance(cats, list) and cats:
        return [str(c) for c in cats]
    return None


def set_sticky_categories(cats: Optional[Sequence[str]]) -> Dict[str, Any]:
    return companion_config.update_config(
        apply_categories=list(cats) if cats else None
    )


def turbo_defaults(*, force: bool = False) -> Dict[str, Any]:
    global _turbo_defaults_cache
    now = time.time()
    if (
        not force
        and _turbo_defaults_cache is not None
        and (now - _turbo_defaults_cache[0]) < _TURBO_DEFAULTS_TTL
    ):
        return dict(_turbo_defaults_cache[1])
    cfg = companion_config.load_config()
    td = {
        "clear_stale": bool(cfg.get("auto_clear_stale", True)),
        # Floor 45s — old companion_config.json often has 20s which false-timeouts add_to_team
        "timeout_live": max(45.0, float(cfg.get("timeout_live") or 55.0)),
        "timeout_offline": max(2.0, float(cfg.get("timeout_offline") or 3.0)),
        "poll_sec": float(cfg.get("poll_sec") or 0.06),
        "wait": bool(cfg.get("apply_wait", True)),
    }
    _turbo_defaults_cache = (now, td)
    return dict(td)


def write_job_meta(
    queue_file: str,
    *,
    kind: str,
    detail: str = "",
    target_id: Optional[int] = None,
    pack_id: str = "",
    card_name: str = "",
) -> Path:
    """Protocol v3 side-car metadata next to job (performance: no DB)."""
    qf = Path(queue_file)
    meta = {
        "version": 3,
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "kind": kind,
        "detail": detail,
        "target_id": target_id,
        "pack_id": pack_id,
        "card_name": card_name,
        "job": qf.name,
    }
    mp = qf.with_suffix(qf.suffix + ".meta.json")
    payload = json.dumps(meta, separators=(",", ":"), ensure_ascii=False)
    try:
        mp.write_text(payload, encoding="utf-8")
    except OSError:
        pass
    # also results index
    try:
        rdir = paths.queue_dir() / "results"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / f"{meta['id']}.json").write_text(payload, encoding="utf-8")
    except OSError:
        pass
    return mp


def turbo_apply_lua(
    lua: str,
    *,
    stem: str,
    detail: str = "",
    target_id: Optional[int] = None,
    kind: str = "apply",
    pack_id: str = "",
    card_name: str = "",
    wait: Optional[bool] = None,
    on_tick: OnTick = None,
    snapshot_fields: Optional[List] = None,
    clear_stale: Optional[bool] = None,
    to_generated: bool = False,
) -> apply_service.ApplyResult:
    """Fast apply: optional clear stale, write meta, short poll, history + snapshot.

    clear_stale: None = use turbo_defaults; False = keep sibling jobs (batch/pack).
    to_generated: default False for turbo (skip generated/ copy — boosts are tiny).
    """
    td = turbo_defaults()
    if wait is None:
        wait = td["wait"]
    do_clear = td["clear_stale"] if clear_stale is None else bool(clear_stale)
    snapshot_error: Optional[str] = None
    if snapshot_fields and target_id is not None:
        try:
            snapshot_store.save_fields_snapshot(
                target_id=int(target_id),
                fields=snapshot_fields,
                label=detail or stem,
                kind=kind,
            )
        except Exception as exc:  # noqa: BLE001
            # Losing the pre-apply snapshot means losing the only rollback for
            # this edit. Swallowing it silently let users apply irreversible
            # changes believing Undo would work. Surface it on the result.
            snapshot_error = str(exc)

    # Single clear inside apply_service when do_clear — avoid double wipe
    result = apply_service.apply_lua(
        lua,
        stem=stem,
        detail=detail,
        clear_stale=do_clear,
        wait=bool(wait),
        timeout_live=td["timeout_live"],
        timeout_offline=td["timeout_offline"],
        on_tick=on_tick,
        to_generated=to_generated,
        poll_sec=td["poll_sec"],
    )
    if snapshot_error:
        try:
            if result.meta is None:
                result.meta = {}
            result.meta["snapshot_error"] = snapshot_error
            result.meta["undo_available"] = False
        except Exception:  # noqa: BLE001
            pass
    if result.queue_file:
        write_job_meta(
            result.queue_file,
            kind=kind,
            detail=detail,
            target_id=target_id,
            pack_id=pack_id,
            card_name=card_name,
        )
        # rewake only if the job file is still queued. Re-waking an already
        # archived job resurrected consumed names and produced "miss" events.
        if not result.applied and Path(result.queue_file).is_file():
            try:
                q = paths.queue_dir()
                # The bridge only recognises the token "force_drain"; the old
                # "force 1" line was a dead signal.
                (q / "_wake.txt").write_text(
                    f"wake {Path(result.queue_file).name}\nforce_drain 1\n",
                    encoding="utf-8",
                )
            except OSError:
                pass

    job_history.add(
        kind=kind,
        detail=detail or stem,
        outcome=result.outcome,
        reason=result.reason,
        target_id=target_id,
        queue_file=result.queue_file or "",
        last_result=result.last_result,
    )
    return result


def run_profile_turbo(
    profile_id: str,
    *,
    wait: Optional[bool] = None,
    clear_stale: Optional[bool] = None,
) -> apply_service.ApplyResult:
    prof = profiles_mod.get_profile(profile_id)
    # Export must use absolute OUT_PATH (raw stock path is relative to LE CWD)
    if profile_id == "export_user_squad":
        from . import squad_export

        lua = squad_export.prepare_export_lua()
    else:
        lua = lua_resolve.resolve_profile_lua(prof)
    # Boosts / one-shot snippets → kind=boost (history + skip generated/)
    kind = "boost" if getattr(prof, "kind", None) in ("snippet",) else "profile"
    return turbo_apply_lua(
        lua,
        stem=prof.id,
        detail=prof.label,
        kind=kind,
        pack_id="",
        wait=wait,
        clear_stale=clear_stale,
        to_generated=False,
    )


def run_pack(pack_id: str, *, wait: bool = True) -> Dict[str, Any]:
    """Run a product pack. Prefer single one-shot profiles; multi-id keeps all jobs."""
    pack = PACKS.get(pack_id)
    if not pack:
        raise KeyError(f"Unknown pack {pack_id!r}. Known: {', '.join(PACKS)}")
    results: List[Dict[str, Any]] = []
    ids: List[str] = list(pack["profile_ids"])
    for i, pid in enumerate(ids):
        is_last = i == len(ids) - 1
        # clear_stale only on first job so multi-profile packs don't wipe siblings
        r = run_profile_turbo(
            pid,
            wait=wait and is_last,
            clear_stale=(i == 0),
        )
        results.append(
            {
                "profile_id": pid,
                "outcome": r.outcome,
                "applied": r.applied,
                "reason": r.reason,
                "queue_file": r.queue_file,
            }
        )
    # Require every step ok when multi-job; single job same as before
    ok_steps = [
        bool(x.get("applied") or x.get("outcome") == "queued_live") for x in results
    ]
    return {
        "pack_id": pack_id,
        "label": pack["label"],
        "ok": bool(ok_steps) and all(ok_steps),
        "results": results,
    }


def batch_apply_card(
    card: CardDict,
    target_ids: Sequence[int],
    *,
    enabled_categories: Optional[Sequence[str]] = None,
    wait_each: bool = False,
    wait_last: bool = True,
) -> Dict[str, Any]:
    """Apply one card onto many playerids (bench/starters blast)."""
    from . import card_to_lua
    from . import player_schema

    # None → sticky; [] stays [] (do not fall through to sticky)
    if enabled_categories is None:
        cats = sticky_categories()
    else:
        cats = list(enabled_categories)
    results = []
    ids = [int(t) for t in target_ids]
    if not ids:
        return {
            "card": card.get("name"),
            "count": 0,
            "results": [],
            "ok": False,
            "reason": "empty target_ids",
        }
    for i, tid in enumerate(ids):
        is_last = i == len(ids) - 1
        fields = player_schema.card_to_field_updates(
            player_schema.normalize_player_card(dict(card)),
            enabled_categories=cats,
        )
        lua = card_to_lua.generate_apply_card_lua(
            card, tid, enabled_categories=cats
        )
        r = turbo_apply_lua(
            lua,
            stem=f"batch_{tid}",
            detail=f"batch {card.get('name')} → {tid}",
            target_id=tid,
            kind="batch_card",
            card_name=str(card.get("name") or ""),
            wait=(wait_last and is_last) or wait_each,
            snapshot_fields=fields,
            # Keep all batch jobs in queue — only clear once on first write
            clear_stale=(i == 0),
        )
        results.append(
            {
                "target_id": tid,
                "outcome": r.outcome,
                "applied": r.applied,
                "reason": r.reason,
                "queue_file": r.queue_file,
            }
        )
    ok_steps = [
        bool(x.get("applied") or x.get("outcome") == "queued_live") for x in results
    ]
    return {
        "card": card.get("name"),
        "count": len(ids),
        "results": results,
        "ok": bool(ok_steps) and all(ok_steps),
    }


def add_card_to_user_team(
    card: Dict[str, Any],
    *,
    teamid: Optional[int] = None,
    mode: str = "auto",
    wait: Optional[bool] = None,
    dummy_pool: Optional[Sequence[Dict[str, Any]]] = None,
    use_real_face: bool = True,
) -> apply_service.ApplyResult:
    """Add card to Career user team via free-agent dummy overwrite (default).

    Default (auto/dummy): worst free-agent overwrite + TransferPlayer — NO CreatePlayer.
    mode=create: CreatePlayer opt-in only (freeze risk on many FC26 saves).

    teamid defaults to current_squad.json teamid from export_user_squad.
    """
    from . import add_player as add_mod
    from . import target_players

    mode_s = (mode or "auto").strip().lower()
    if mode_s not in ("auto", "create", "dummy"):
        mode_s = "auto"

    squad = target_players.load_squad()
    tid = int(teamid or 0) or add_mod.user_team_id_from_squad(squad)
    if tid <= 0:
        return apply_service.ApplyResult(
            applied=False,
            queued=False,
            live=le_apply.bridge_alive(90),
            reason="No teamid — Export squad first (Career Mode).",
            outcome="error",
            detail="add_to_team",
        )
    protected = add_mod.protected_squad_ids(squad)
    dummies: List[int] = []
    # NEVER seed dummies from FUT catalog — superstar base ids freeze Career.
    # Pool must be free-agent marked (team 111592 / free_agent flag).
    # free_agent_pool() re-reads current_squad.json if keys were stripped (FA · 0 bug).
    pool_list: List[Dict[str, Any]] = list(dummy_pool or [])
    if not pool_list:
        pool_list = list(target_players.free_agent_pool(squad))
    if mode_s in ("auto", "dummy") and pool_list:
        dummies = add_mod.select_worst_dummies(
            pool_list, protected_ids=protected, limit=add_mod.DUMMY_POOL_LIMIT
        )
    if mode_s in ("auto", "dummy") and not dummies:
        fa_on_disk = target_players.free_agent_count()
        if fa_on_disk <= 0:
            reason = (
                "No free-agent pool in current_squad.json — Export squad while LIVE "
                "in Career Mode (needs FA team 111592). Then try Add team again. "
                "CreatePlayer is Advanced-only (freeze risk)."
            )
        else:
            reason = (
                f"Free-agent pool has {fa_on_disk} ids but none passed safety filters "
                "(protected / not FA). Re-export squad. CreatePlayer is Advanced-only."
            )
        return apply_service.ApplyResult(
            applied=False,
            queued=False,
            live=le_apply.bridge_alive(90),
            reason=reason,
            outcome="error",
            detail="add_to_team",
        )
    card = add_mod.enrich_card_for_add(card)
    lua = add_mod.generate_add_to_team_lua(
        card,
        teamid=tid,
        mode=mode_s,
        dummy_candidate_ids=dummies,
        use_real_face=use_real_face,
    )
    face_id = add_mod.resolve_base_face_id(card) if use_real_face else 0
    nat = add_mod.resolve_nationality_id(card)
    path = "create" if mode_s == "create" else "dummy"
    detail = (
        f"Add to team {tid} · {card.get('name') or 'player'} · path={path} · mode={mode_s} "
        f"· dummies={len(dummies)} · face={'base '+str(face_id) if face_id else 'generic'} "
        f"· nation={nat if nat is not None else 'unset'}"
    )
    # Always clear_stale so a poison old add_to_team / _run_now cannot re-run
    return turbo_apply_lua(
        lua,
        stem=f"add_team_{tid}",
        detail=detail,
        kind="add_to_team",
        pack_id="",
        card_name=str(card.get("name") or ""),
        wait=wait,
        to_generated=True,
        clear_stale=True,
    )


def apply_card_to_target(
    card: CardDict,
    target_id: int,
    *,
    enabled_categories: Optional[Sequence[str]] = None,
    wait: Optional[bool] = None,
) -> apply_service.ApplyResult:
    from . import card_to_lua
    from . import player_schema

    cats = enabled_categories or sticky_categories()
    fields = player_schema.card_to_field_updates(
        player_schema.normalize_player_card(dict(card)),
        enabled_categories=cats,
    )
    lua = card_to_lua.generate_apply_card_lua(
        card, int(target_id), enabled_categories=cats
    )
    return turbo_apply_lua(
        lua,
        stem=f"apply_{target_id}",
        detail=str(card.get("name") or "card"),
        target_id=int(target_id),
        kind="card",
        card_name=str(card.get("name") or ""),
        wait=wait,
        snapshot_fields=fields,
    )


def import_card(
    card: CardDict,
    *,
    mode: str = "apply",
    target_playerid: Optional[int] = None,
    copy_name: bool = True,
    copy_head: bool = True,
    copy_birthdate: bool = True,
    categories: Optional[Sequence[str]] = None,
    team_id: Optional[int] = None,
    wait: Optional[bool] = None,
) -> apply_service.ApplyResult:
    """LE-style Import Player: Apply onto target id, or Create via add-to-team.

    Categories default to LE Advanced Import topics. Copy toggles pull Name/Head/
    Birthdate from card fields → base_players.csv → display name split.
    """
    from . import import_player
    from . import player_schema

    req = import_player.ImportRequest(
        mode=mode,
        card=dict(card),
        target_playerid=target_playerid,
        copy_name=bool(copy_name),
        copy_head=bool(copy_head),
        copy_birthdate=bool(copy_birthdate),
        categories=categories,
        team_id=team_id,
    )
    plan = import_player.build_import_plan(req)

    if plan.mode == "create":
        create_card = import_player.prepare_create_card(plan)
        return add_card_to_user_team(
            create_card,
            teamid=int(team_id) if team_id is not None else 0,
            mode="auto",
            use_real_face=bool(plan.copy_head),
            wait=wait,
        )

    tid = int(plan.target_playerid or 0)
    if tid <= 0:
        raise ValueError("Apply import needs a target playerid")

    cats = plan.field_categories()
    fields = player_schema.card_to_field_updates(
        player_schema.normalize_player_card(dict(plan.card)),
        enabled_categories=cats,
    )
    lua = import_player.generate_import_apply_lua(plan)
    try:
        companion_config.update_config(default_target_playerid=tid)
    except Exception:
        pass
    return turbo_apply_lua(
        lua,
        stem=f"import_{tid}",
        detail=plan.preflight or str(plan.card.get("name") or "import"),
        target_id=tid,
        kind="import",
        card_name=str(plan.card.get("name") or ""),
        wait=wait,
        snapshot_fields=fields,
    )


def restore_snapshot(sid: Optional[str] = None, *, wait: Optional[bool] = None) -> apply_service.ApplyResult:
    lua = snapshot_store.undo_lua_for(sid)
    snap = snapshot_store.get_snapshot(sid) if sid else None
    tid = int((snap or {}).get("target_id") or 0) or None
    return turbo_apply_lua(
        lua,
        stem=f"undo_{sid or 'last'}",
        detail=f"restore {sid or 'last'}",
        target_id=tid,
        kind="undo",
        wait=wait,
    )


# ── Career ops (LE DOC APIs — CE LE-safe parity) ─────────────────────


def career_transfer(
    *,
    playerid: int,
    to_teamid: int,
    transfersum: int = 0,
    wage: int = 5000,
    contract_months: int = 60,
    from_teamid: int = 0,
    release_clause: int = -1,
    wait: Optional[bool] = None,
) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_transfer_lua(
        playerid=playerid,
        to_teamid=to_teamid,
        transfersum=transfersum,
        wage=wage,
        contract_months=contract_months,
        from_teamid=from_teamid,
        release_clause=release_clause,
    )
    return turbo_apply_lua(
        lua,
        stem=f"xfer_{int(playerid)}_{int(to_teamid)}",
        detail=f"Transfer {playerid} → {to_teamid}",
        target_id=int(playerid),
        kind="career_transfer",
        wait=wait,
        to_generated=True,
    )


def career_loan(
    *,
    playerid: int,
    to_teamid: int,
    length_months: int = 12,
    loantobuy: int = -1,
    from_teamid: int = 0,
    wait: Optional[bool] = None,
) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_loan_lua(
        playerid=playerid,
        to_teamid=to_teamid,
        length_months=length_months,
        loantobuy=loantobuy,
        from_teamid=from_teamid,
    )
    return turbo_apply_lua(
        lua,
        stem=f"loan_{int(playerid)}_{int(to_teamid)}",
        detail=f"Loan {playerid} → {to_teamid} ({length_months}m)",
        target_id=int(playerid),
        kind="career_loan",
        wait=wait,
        to_generated=True,
    )


def career_release(*, playerid: int, wait: Optional[bool] = None) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_release_lua(playerid=playerid)
    return turbo_apply_lua(
        lua,
        stem=f"release_{int(playerid)}",
        detail=f"Release {playerid}",
        target_id=int(playerid),
        kind="career_release",
        wait=wait,
        to_generated=True,
    )


def career_set_budget(*, amount: int, wait: Optional[bool] = None) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_set_transfer_budget_lua(amount=amount)
    return turbo_apply_lua(
        lua,
        stem=f"budget_{int(amount)}",
        detail=f"SetTransferBudget {amount}",
        kind="career_budget",
        wait=wait,
        to_generated=True,
    )


def career_get_budget(*, wait: Optional[bool] = None) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_get_transfer_budget_lua()
    return turbo_apply_lua(
        lua,
        stem="budget_get",
        detail="GetTransferBudget",
        kind="career_budget",
        wait=wait,
        to_generated=True,
    )


def career_terminate_loan(*, playerid: int, wait: Optional[bool] = None) -> apply_service.ApplyResult:
    from . import career_ops

    lua = career_ops.generate_terminate_loan_lua(playerid=playerid)
    return turbo_apply_lua(
        lua,
        stem=f"termloan_{int(playerid)}",
        detail=f"TerminateLoan {playerid}",
        target_id=int(playerid),
        kind="career_loan",
        wait=wait,
        to_generated=True,
    )


def best_card_for_locked_target(
    *,
    year: str = "26",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Best cards for currently implied target from squad search name var path."""
    # Prefer last squad entry matching nothing — caller passes target dict better.
    sq = target_players.load_squad()
    players = sq.get("players") or []
    if not players:
        return []
    # default: first player — real GUI passes explicit target
    return card_match.best_match_for_target(players[0], year=year, limit=limit)


def best_card_for_playerid(
    playerid: int,
    *,
    year: str = "26",
    limit: int = 5,
) -> List[Dict[str, Any]]:
    hits = target_players.search_target(str(playerid), limit=5, year=year)
    if hits:
        return card_match.best_match_for_target(hits[0], year=year, limit=limit)
    # fall back name empty
    return card_match.best_matches(str(playerid), year=year, playerid=playerid, limit=limit)


def rebuild_catalog(*, progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    from . import card_index

    return card_index.rebuild_index(force=True, progress=progress)


def arm_status() -> Dict[str, Any]:
    """Product arm/LIVE panel data."""
    live = le_apply.bridge_alive(90)
    installed = le_apply.bridge_installed()
    age = le_apply.bridge_heartbeat_age_sec()
    pending = le_apply.list_pending_lua(force=True)
    autoarm = le_apply.autoarm_installed()
    n_pending = len(pending)
    busy = le_apply.bridge_busy()
    if live and busy:
        hint = "Worker busy on a job — wait (do not re-Execute mid-run)"
    elif live and n_pending:
        hint = (
            f"{n_pending} job(s) waiting — open Career hub / advance day, "
            "or Tools → Force drain → Execute"
        )
    elif live:
        hint = "Apply now — worker LIVE"
    elif autoarm and installed:
        hint = "Go LIVE once more if header is OFF · or restart FC26 with LE Launcher"
    elif installed:
        hint = "Click Go LIVE (copies bridge) → LE Lua Engine → Ctrl+V → Execute"
    else:
        hint = "Click Go LIVE"
    return {
        "worker_installed": installed,
        "live": live,
        "heartbeat_age_sec": age,
        "pending_jobs": n_pending,
        "busy": busy,
        "inject_autoarm": autoarm,
        "state": "LIVE" if live else ("INSTALLED" if installed else "OFF"),
        "hint": hint,
        "le_owns_anticheat": True,
        "protocol": "v3-meta+v2-queue",
    }


def install_and_arm_assets() -> Dict[str, Any]:
    """Install Lua bridge + inject DLL; write turbo queue layout; copy bridge.

    Does not enable LE Load auto-arm (use one_click_inject_arm for that).
    """
    out = le_apply.install_bridge()
    q = paths.queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    (q / "results").mkdir(parents=True, exist_ok=True)
    (q / "done").mkdir(parents=True, exist_ok=True)
    # Performance: empty pending start
    le_apply.clear_stale_jobs()
    le_apply.rebuild_pending()
    clip = False
    try:
        clip = apply_service.copy_bridge_to_clipboard()
    except Exception:
        pass
    wake: Dict[str, Any] = {}
    try:
        wake = force_turbo_drain_signal()
    except Exception as e:  # noqa: BLE001
        wake = {"wake": False, "error": str(e)}
    st = arm_status()
    return {
        "install": out,
        "clipboard_bridge": clip,
        "wake": wake,
        "arm": st,
        "next": "In LE: Features → Lua Engine → paste bridge → Execute once. Then LIVE ✓.",
    }


def go_live(*, prefer_autoarm: bool = False) -> Dict[str, Any]:
    """One-shot arm path: install worker + auto-copy bridge. No LE core patch by default.

    prefer_autoarm=False (default): never touch live_editor.lua — patching Load()
    has broken LE launch for users. Paste/Execute once per session instead.

    Returns UX contract: state already_live | need_paste | failed + title/body.
    """
    # Always scrub any leftover auto-arm so LE can launch
    try:
        le_apply.ensure_le_core_clean()
    except Exception:
        pass
    try:
        from . import companion_config as _cc

        _cc.update_config(inject_autoarm=False)
    except Exception:
        pass

    le_apply.clear_stale_jobs()
    le_apply.rebuild_pending()
    q = paths.queue_dir()
    q.mkdir(parents=True, exist_ok=True)
    (q / "results").mkdir(parents=True, exist_ok=True)
    (q / "done").mkdir(parents=True, exist_ok=True)

    was_live = le_apply.bridge_alive(90)
    install = le_apply.install_bridge()
    install_ok = bool(install.get("ok", True)) if isinstance(install, dict) else True

    autoarm: Dict[str, Any] = {"ok": False, "skipped": True, "reason": "disabled_default"}
    # Opt-in only — never install auto-arm from the normal Go LIVE button
    if prefer_autoarm:
        try:
            autoarm = le_apply.install_autoarm_hook()
            from . import companion_config as _cc

            _cc.update_config(inject_autoarm=True)
        except Exception as e:  # noqa: BLE001
            autoarm = {"ok": False, "error": str(e)}

    st = arm_status()
    now_live = bool(st.get("live")) or was_live
    clip = False
    # Always put something useful on the clipboard (no separate Copy click).
    if not now_live:
        try:
            clip = bool(apply_service.copy_bridge_to_clipboard())
        except Exception:
            clip = False
        # Retry once — PS clipboard can flake under load
        if not clip:
            try:
                clip = bool(apply_service.copy_bridge_to_clipboard())
            except Exception:
                clip = False
    else:
        # LIVE: short drain script so pending jobs can be flushed with one paste
        try:
            wake = force_turbo_drain_signal()
            clip = bool(wake.get("clipboard_force_drain"))
        except Exception:
            clip = False
        if not clip:
            try:
                clip = bool(apply_service.copy_bridge_to_clipboard())
            except Exception:
                clip = False

    ok = install_ok and (bool(autoarm.get("ok")) or bool(autoarm.get("skipped")))
    if now_live:
        state = "already_live"
        title = "You're LIVE ✓"
        body = (
            "Worker is connected. Apply, Boost, and Add team work now.\n\n"
            "You do not need to paste anything."
        )
        next_step = "Apply now"
    elif not ok:
        state = "failed"
        title = "Could not arm worker"
        err = str(autoarm.get("error") or install.get("error") or "unknown error")
        body = f"{err}\n\nTry Tools → Health, or restart LE Companion as admin."
        next_step = err
    else:
        state = "need_paste"
        title = "Almost LIVE — one paste"
        body = (
            "Bridge script is already on your clipboard.\n\n"
            "1. Open FC 26 Live Editor\n"
            "2. Features → Lua Engine\n"
            "3. Ctrl+A, Delete, Ctrl+V, then Execute\n\n"
            "Come back here — header shows LIVE ✓ when ready.\n"
            "(We do not patch LE Load — keeps the game launch stable.)"
        )
        next_step = body

    return {
        "ok": ok or now_live,
        "state": state,
        "title": title,
        "body": body,
        "next": next_step,
        "clipboard_ok": clip,
        "clipboard_bridge": clip,
        "install": install,
        "autoarm": autoarm,
        "arm": st,
        "session_live": now_live,
        "need_execute_this_session": state == "need_paste",
        "le_owns_anticheat": True,
        "fakeeaac_touched": False,
    }


def one_click_inject_arm() -> Dict[str, Any]:
    """Back-compat: same as Go LIVE — no auto-arm (safe LE launch)."""
    return go_live(prefer_autoarm=False)


def remove_inject_autoarm() -> Dict[str, Any]:
    """Undo SAFE auto-arm patch (stock LE Load). Worker file remains installed."""
    cleaned = le_apply.remove_autoarm_hook()
    try:
        from . import companion_config as _cc

        _cc.update_config(inject_autoarm=False)
    except Exception:
        pass
    return {
        "ok": bool(cleaned.get("ok")),
        "cleaned": cleaned,
        "autoarm_active": le_apply.autoarm_installed(),
        "next": "LE Load no longer auto-arms. Use paste/Execute or Inject arm again.",
    }


# One-liner for LE Lua Engine when worker already loaded (no full bridge paste).
FORCE_DRAIN_LUA = (
    "-- LE Companion ForceDrain (worker must already be loaded)\n"
    "if type(LECompanion_ForceDrain) == \"function\" then\n"
    "  local p, o, n = LECompanion_ForceDrain()\n"
    "  if Log then Log(string.format('[LE_Companion] ForceDrain processed=%s ok=%s note=%s', "
    "tostring(p), tostring(o), tostring(n))) end\n"
    "elseif type(LEProfileBridge_ForceDrain) == \"function\" then\n"
    "  LEProfileBridge_ForceDrain()\n"
    "else\n"
    "  error('Worker not loaded — paste full bridge (Copy bridge) then Execute once')\n"
    "end\n"
)


def force_turbo_drain_signal() -> Dict[str, Any]:
    """Wake worker hard (file protocol) + copy short ForceDrain Lua — no inject."""
    q = paths.queue_dir()
    names = le_apply.rebuild_pending()
    (q / "_wake.txt").write_text(
        "force_drain 1\n" + "\n".join(f"wake {n}" for n in names) + "\n",
        encoding="utf-8",
    )
    # If a pending job exists, also stage as _run_now so ForceDrain runs it first
    try:
        if names:
            src = q / names[0]
            if src.is_file():
                (q / "_run_now.lua").write_text(
                    src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                    newline="\n",
                )
    except OSError:
        pass
    clip = False
    try:
        from . import actions as _actions

        clip = _actions.copy_to_clipboard(
            _actions.normalize_lua_for_le_clipboard(FORCE_DRAIN_LUA)
        )
    except Exception:
        clip = False
    return {
        "pending": names,
        "wake": True,
        "live": le_apply.bridge_alive(90),
        "clipboard_force_drain": clip,
        "busy": le_apply.bridge_busy(),
    }
