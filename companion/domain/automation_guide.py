"""Automations UX grouping + empty-team preflight / failure copy.

Keeps pack builders in pack_library; this module only answers:
  - which section does this action belong in?
  - can we run it with the current squad snapshot?
  - how do we explain empty_team / no_team?
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .pack_library import PackAction, QUICK_RAIL_IDS

# Pack / profile ids that need a resolvable Career Mode user team.
TEAM_SCOPED_IDS: frozenset[str] = frozenset({
    "match_ready", "pre_match", "pre_match_full", "matchday", "squad_boost",
    "club_refresh", "season_kickoff", "signing_settle", "morale_day",
    "growth_sync", "export_squad", "export_user_squad", "list_players",
    "full_fitness", "full_sharpness", "full_form", "full_morale",
    "full_form_morale_sharpness", "matchday_pack", "squad_boost_pack",
    "pot_99_team", "ovr_99_pot_99_team",
    "full_fitness_event", "full_sharpness_event",
    "auto_form_morale_sharpness", "auto_max_form", "auto_max_morale",
})

# Safe anytime: refresh/diag recovery paths (Automations routes these to sync_squad).
# Never hard-block on empty local cache — the point is to repopulate it.
RECOVERY_IDS: frozenset[str] = frozenset({
    "ping", "export_squad", "export_user_squad", "list_players",
})

# ``career.set`` v2 is the target-safe implementation: it resolves FC's
# current user senior squad at execution time and treats any desktop team id as
# a guard only.  Older workers advertised career.set v1 and could fall back to
# a cached club, so the priority boosts must not be enabled against them.
CAREER_SET_IDS: frozenset[str] = frozenset({
    "match_ready", "pre_match", "pre_match_full", "matchday", "squad_boost",
    "club_refresh", "season_kickoff", "signing_settle", "morale_day",
    "full_fitness", "full_sharpness", "full_form", "full_morale",
    "full_form_morale_sharpness", "matchday_pack", "squad_boost_pack",
    "full_fitness_event", "full_sharpness_event",
    "auto_form_morale_sharpness", "auto_max_form", "auto_max_morale",
})

# Intent sections for the Automations page (order = display order).
SECTIONS: tuple[tuple[str, str, str, frozenset[str]], ...] = (
    (
        "matchday",
        "Matchday · my club",
        "Use when your Career squad is loaded and you want fitness / form before a match.",
        frozenset({
            "match_ready", "pre_match", "pre_match_full", "matchday",
            "squad_boost", "signing_settle", "club_refresh", "season_kickoff",
            "morale_day",
        }),
    ),
    (
        "growth",
        "Growth · make edits stick",
        "Use after attribute edits so Career Mode development plans do not wipe them.",
        frozenset({"growth_sync"}),
    ),
    (
        "diag",
        "Diagnostics",
        "Use to check the worker or refresh squad data — safe anytime.",
        frozenset({"ping", "export_squad"}),
    ),
    (
        "advanced",
        "Advanced · whole save",
        "Use rarely. Changes every player in the save — requires typing ALL.",
        frozenset({
            "never_retire", "force_retire", "modifier_zero", "pot_99_all",
            "ovr_99_pot_99", "ovr_1_pot_99", "untuck_shirts", "tight_shirts",
            "mass_visual", "career_cleanup", "youth_regen_farm", "max_growth",
        }),
    ),
    (
        "other",
        "More packs & profiles",
        "Everything else (pending items stay Not ready until a safe op exists).",
        frozenset(),  # catch-all
    ),
)

EMPTY_TEAM_HINT = (
    "FC could not find a current senior squad. Load your Career Mode save and "
    "try again; reading the squad in Club is optional."
)

NO_TEAM_HINT = (
    "FC could not resolve the current Career Mode team. Load the save and enter "
    "a settled Career screen; reading the squad in Club is optional."
)

# A cache team id is only ever a *hint* for the in-game worker.  Keeping this
# check here makes the policy shared by the UI and tests: a prior save/session
# must never provide a team id for an automation job.
TEAM_HINT_TTL_SECONDS = 120.0


def trusted_cached_teamid(state: Any, *, now: datetime | None = None) -> int | None:
    """Return a fresh, same-session team-id hint, otherwise ``None``.

    The core validates a supplied id against the active Career save when it can
    read that API.  This caller-side check prevents stale squad data from even
    becoming a hint after changing saves or leaving Career Mode.
    """
    squad = getattr(state, "squad", None)
    if squad is None or bool(getattr(squad, "stale", True)):
        return None
    players = tuple(getattr(squad, "players", ()) or ())
    try:
        teamid = int(getattr(squad, "teamid", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not players or teamid <= 0:
        return None

    live = getattr(getattr(state, "bridge", None), "liveness", None)
    live_session = str(getattr(live, "session_id", "") or "")
    squad_session = str(getattr(squad, "session_id", "") or "")
    # No current worker session means there is nothing trustworthy to bind this
    # cache to.  The automation may still resolve the live team in FC, but it
    # must not carry a cached team id across that boundary.
    if not live_session or squad_session != live_session:
        return None

    taken = getattr(squad, "taken", None)
    if now is not None and isinstance(taken, datetime):
        try:
            if max(0.0, (now - taken).total_seconds()) > TEAM_HINT_TTL_SECONDS:
                return None
        except TypeError:
            # Naive/aware mixed values are not a trustworthy time comparison.
            return None
    return teamid


def section_for(item_id: str, category: str = "") -> str:
    """Return section key for a pack/profile id."""
    for key, _title, _blurb, ids in SECTIONS:
        if key == "other":
            continue
        if item_id in ids:
            return key
    cat = (category or "").lower()
    if cat in {"mass", "mass_edit", "visual"} and item_id not in TEAM_SCOPED_IDS:
        return "advanced"
    if cat in {"boost", "workflow", "user_team"}:
        return "matchday"
    if "growth" in cat:
        return "growth"
    if cat in {"diag", "squad", "export"}:
        return "diag"
    return "other"


def group_items(items: Sequence[PackAction]) -> list[tuple[str, str, str, list[PackAction]]]:
    """Group pack actions into ordered intent sections for the UI."""
    buckets: dict[str, list[PackAction]] = {key: [] for key, *_ in SECTIONS}
    for item in items:
        buckets[section_for(item.id, item.category)].append(item)
    out: list[tuple[str, str, str, list[PackAction]]] = []
    for key, title, blurb, _ids in SECTIONS:
        if buckets[key]:
            out.append((key, title, blurb, buckets[key]))
    return out


def team_scoped(item_id: str) -> bool:
    return item_id in TEAM_SCOPED_IDS


def preflight_team_scope(state: Any, item_id: str) -> str | None:
    """Do not reject a live team action merely because the local cache is empty.

    A stale app snapshot cannot safely identify the Career Mode team currently
    open in FC.  The installed worker resolves it at claim time with
    ``GetUserTeamID`` and ``GetUserSeniorTeamPlayerIDs``.  A fresh cache id is
    still passed as a checked hint, but a missing snapshot must not force the
    user through Club → Read squad before the one-click boosts can run.

    The core returns an actionable no-team/empty-team result if Career Mode is
    not ready.  This hook remains for a future deterministic local refusal.
    """
    if item_id in CAREER_SET_IDS:
        current = str(getattr(getattr(getattr(state, "bridge", None), "liveness", None), "core_version", "") or "")
        if not _version_at_least(current, (2, 6, 4)):
            return (
                "This boost needs the updated Companion worker (core 2.6.4). "
                "Use Menu → Install / repair worker, then restart Live Editor so it reloads the worker."
            )
    return None


def _version_at_least(value: str, required: tuple[int, int, int]) -> bool:
    try:
        parsed = tuple(int(part) for part in value.strip().split("."))
    except (AttributeError, TypeError, ValueError):
        return False
    return len(parsed) == 3 and parsed >= required


def map_failure_reason(reason: str, detail: str = "") -> str:
    """Map Lua/core reason tokens to actionable status text."""
    r = (reason or "").strip().lower()
    d = (detail or "").strip().lower()
    blob = f"{r} {d}"
    if "empty_team" in blob or "has no players" in blob:
        return EMPTY_TEAM_HINT
    if "no_team" in blob or "could not resolve" in blob or "getuserteamid" in blob:
        return NO_TEAM_HINT
    if "team_changed" in blob or "team mismatch" in blob:
        return (
            "Your Career Mode team changed since the squad was read. "
            "Club → Refresh squad, then run the boost again."
        )
    if "no_career" in blob or "career mode" in blob:
        return (
            "Career Mode save not loaded. Enter Career Mode, then Club → Read my squad."
        )
    if reason:
        return f"Failed: {reason}" + (f" — {detail}" if detail else "")
    return detail or "Failed inside the game."


def map_job_failure(result: Any) -> str:
    """Map a JobResult (or wire-like object) to actionable status text.

    Real results often put empty_team/no_team on ``failures[*].reason`` while
    ``error.message`` is a generic ``failed``. Scan both.
    """
    chunks: list[str] = []
    primary = ""

    err = getattr(result, "error", None)
    if isinstance(err, Mapping):
        primary = str(err.get("reason") or err.get("message") or "")
        for key in ("reason", "message", "detail"):
            val = err.get(key)
            if val:
                chunks.append(str(val))

    failures = getattr(result, "failures", None) or ()
    for fail in failures:
        if isinstance(fail, Mapping):
            fr = str(fail.get("reason") or "")
            fd = str(fail.get("detail") or "")
        else:
            fr = str(getattr(fail, "reason", "") or "")
            fd = str(getattr(fail, "detail", "") or "")
        if fr:
            chunks.append(fr)
            if not primary:
                primary = fr
        if fd:
            chunks.append(fd)

    diagnostic = str(getattr(result, "diagnostic", "") or "")
    if diagnostic:
        chunks.append(diagnostic)

    blob = " ".join(c for c in chunks if c).strip()
    return map_failure_reason(primary or blob, blob)


def primary_rail_ids() -> tuple[str, ...]:
    """Ids shown in the top Automations rail (from pack_library.QUICK_RAIL_IDS)."""
    return tuple(QUICK_RAIL_IDS)
