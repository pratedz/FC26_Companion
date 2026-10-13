"""Matchday entry point and typed retries for older Automation receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from ...app.commands.apply import submit_job
from ...app.commands.squad import sync_squad, sync_status_text
from ...domain.automation_guide import (
    preflight_team_scope,
    trusted_cached_teamid,
)
from ...domain.job import JobValidationError
from ...domain.pack_library import (
    actions as pack_actions,
    build_job as build_pack_job,
    load_v1_packs,
)
from ...domain.profile_library import (
    action_for as profile_action_for,
    actions as profile_actions,
    build_job as build_profile_job,
)
from ._common import set_status


# These are the V1 boost controls people actually used.  Each is a proven
# one-shot ``career.set`` host call in the V2 worker, not a stock-script
# placeholder or a recurring promise.  FC26 LE has no compatible getter for
# these transient Career values, so completion means the host accepted calls,
# not a database read-back verification.
PRIORITY_PROFILES: tuple[tuple[str, str, str, str], ...] = (
    ("full_fitness", "Fitness", "100", "Set the current senior squad to match fitness."),
    ("full_sharpness", "Sharpness", "100", "Set the current senior squad sharpness."),
    ("full_form", "Form", "100", "Set the current senior squad form."),
    ("full_morale", "Morale", "100", "Set the current senior squad morale."),
)

# Prominent V1-inspired combined actions. Everything else that is verified
# remains reachable from the compact chooser below without becoming a wall of
# equally-weighted cards.
PRIMARY_PACK_IDS: tuple[str, ...] = (
    "squad_boost",
    "matchday",
    "morale_day",
    "match_ready",
)

# Parsed profiles.json with mtime invalidation — keep json.loads off rebuilds.
_PROFILES_CACHE: dict[str, Any] = {"path": "", "mtime": None, "data": None}


def _read_profiles_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _profiles_from_cache(path: Path) -> dict[str, Any] | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if (
        _PROFILES_CACHE.get("path") == str(path)
        and _PROFILES_CACHE.get("mtime") == mtime
        and _PROFILES_CACHE.get("data") is not None
    ):
        cached = _PROFILES_CACHE["data"]
        return cached if isinstance(cached, dict) else {}
    return None


def _store_profiles_cache(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    _PROFILES_CACHE["path"] = str(path)
    _PROFILES_CACHE["mtime"] = mtime
    _PROFILES_CACHE["data"] = data
    return data


def _load_profiles_sync(svc: Any) -> dict[str, Any]:
    path = Path(svc.paths.profiles_file)
    cached = _profiles_from_cache(path)
    if cached is not None:
        return cached
    return _store_profiles_cache(path, _read_profiles_file(path))


def build(parent: Any, svc: Any, vm: Any = None) -> Any:
    """Stable navigation entry point for the Matchday surface."""
    from .matchday import build as build_matchday
    return build_matchday(parent, svc, vm)


def _automation_disabled_reason(state: Any, item_id: str) -> str:
    if not state.bridge.armed:
        return state.bridge.liveness.message
    return preflight_team_scope(state, item_id) or ""


def _native_items(
    svc: Any, *, profiles: dict[str, Any] | None = None
) -> Iterable[tuple[str, str, str, str]]:
    """One compact selectable inventory; duplicate priority buttons stay out."""
    seen: set[tuple[str, str]] = set()
    priority = {profile_id for profile_id, *_ in PRIORITY_PROFILES}
    flow_ids = set(PRIMARY_PACK_IDS)
    for item in pack_actions(load_v1_packs()):
        if item.mode != "native" or item.id in flow_ids:
            continue
        key = ("pack", item.id)
        if key not in seen:
            seen.add(key)
            yield (f"Workflow · {item.label}", "pack", item.id, item.description or item.reason)
    raw = profiles if profiles is not None else _load_profiles_sync(svc)
    for item in profile_actions(raw):
        if item.mode != "native" or item.id in priority:
            continue
        key = ("profile", item.id)
        if key not in seen:
            seen.add(key)
            # Some V1 labels say "Daily" although V2 deliberately maps them
            # to a one-shot typed action.  Always show the migration reason so
            # the compact picker cannot quietly promise recurring automation.
            detail = " · ".join(part for part in (item.description, item.reason) if part)
            yield (f"Profile · {item.label}", "profile", item.id, detail)


def _automation_scope(item_id: str, category: str = "") -> str:
    """Player-facing scope label; a generic picker must not hide who changes."""
    ident = str(item_id).lower()
    if ident == "ping" or category == "diag":
        return "Diagnostic - no save changes"
    if "export" in ident:
        return "Data export - no save changes"
    if "growth" in ident:
        return "Development - current senior squad"
    if ident.endswith("_team") or "team" in ident:
        return "Team-wide edit - current Career team"
    return "Current senior squad - one-shot"


def _requires_scope_confirmation(item_id: str) -> bool:
    ident = str(item_id).lower()
    return ident.endswith("_team") or ident == "growth_sync"


def _confirm_scope(item_id: str) -> bool:
    """Matchday stays fast; broad player-table actions require confirmation."""
    if not _requires_scope_confirmation(item_id):
        return True
    try:
        from tkinter import messagebox

        return bool(messagebox.askokcancel(
            "Confirm team-wide action",
            f"{_automation_scope(item_id)}. This is a one-shot queued action. Continue?",
        ))
    except Exception:
        return False


def _pending_count(svc: Any) -> int:
    raw = _load_profiles_sync(svc)
    return sum(1 for item in profile_actions(raw) if item.mode == "pending") + sum(
        1 for item in pack_actions(load_v1_packs()) if item.mode == "pending"
    )


def _refresh_squad(svc: Any) -> None:
    if not svc.store.snapshot().bridge.armed:
        set_status(svc, f"Refresh squad not queued: {svc.store.snapshot().bridge.liveness.message}")
        return
    try:
        set_status(svc, sync_status_text(sync_squad(svc)))
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Could not queue squad refresh: {exc}")


def _run_pack(svc: Any, pack_id: str) -> None:
    items = {item.id: item for item in pack_actions(load_v1_packs())}
    item = items.get(pack_id)
    if item is None:
        set_status(svc, f"Unknown automation: {pack_id}")
        return
    _submit_automation(svc, item.label, item.id, lambda teamid: build_pack_job(item.id, teamid=teamid))


def _run_profile(svc: Any, profile_id: str) -> None:
    raw = _load_profiles_sync(svc)
    item = profile_action_for(profile_id, raw)
    if item is None or item.mode != "native":
        set_status(svc, f"Unknown automation profile: {profile_id}")
        return
    _submit_automation(svc, item.label, item.id, lambda teamid: build_profile_job(item.id, teamid=teamid))


def automation_retryable(svc: Any, job_id: str) -> bool:
    """Only retry a durable job whose origin maps to a known typed action."""
    if not job_id or getattr(svc, "db", None) is None:
        return False
    try:
        record = svc.db.job_record(job_id)
        raw = record.job_json if record is not None else None
        origin = str(raw.get("origin") or "") if isinstance(raw, dict) else ""
    except Exception:
        return False
    return origin == "ui.matchday.prepare" or origin.startswith("ui.packs.") or origin.startswith("ui.profiles.")


def retry_recorded_automation(svc: Any, job_id: str) -> None:
    """Rebuild a failed automation through today's validated registry.

    Raw historical JSON is never replayed: the new job resolves FC's current
    senior squad and cannot inherit a former club's cached team id.
    """
    if getattr(svc, "db", None) is None:
        set_status(svc, "Retry unavailable: no local job audit is open.")
        return
    try:
        record = svc.db.job_record(job_id)
        raw = record.job_json if record is not None else None
        origin = str(raw.get("origin") or "") if isinstance(raw, dict) else ""
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"Retry unavailable: {exc}")
        return
    if origin == "ui.matchday.prepare":
        from ...app.commands.matchday import retry_preparation
        try:
            new_id = retry_preparation(svc, job_id)
            set_status(svc, f"Prepare squad queued ({new_id[:8]}…). Open Team Management or return to the Career hub.")
        except Exception as exc:
            set_status(svc, str(exc))
    elif origin.startswith("ui.packs."):
        _run_pack(svc, origin.removeprefix("ui.packs."))
    elif origin.startswith("ui.profiles."):
        _run_profile(svc, origin.removeprefix("ui.profiles."))
    else:
        set_status(svc, "Retry is unavailable for this historical job.")


def _submit_automation(
    svc: Any,
    label: str,
    item_id: str,
    builder: Callable[[int | None], Any],
) -> None:
    state = svc.store.snapshot()
    if not state.bridge.armed:
        set_status(svc, f"{label} not queued: {state.bridge.liveness.message}")
        return
    refusal = preflight_team_scope(state, item_id)
    if refusal:
        set_status(svc, refusal)
        return
    if not _confirm_scope(item_id):
        set_status(svc, "Team-wide action was not queued.")
        return
    teamid = trusted_cached_teamid(state, now=svc.clock.now())
    try:
        job_id = submit_job(
            svc,
            builder(teamid),
            # Unlike V1, do not spawn a 45-second waiter for every click. The
            # shell already collects exact per-job result files each second;
            # four queued clicks used to starve that shared four-worker pool.
            follow=False,
        )
        hint = f" for team {teamid}" if teamid is not None else " using FC's current team"
        set_status(
            svc,
            f"{label} queued ({job_id[:8]}…){hint}. Open Team Management or return to the Career hub to let FC run it safely."
        )
    except JobValidationError as exc:
        set_status(svc, f"{label} refused: {exc}")
    except Exception as exc:  # noqa: BLE001
        set_status(svc, f"{label} could not queue: {exc}")
