"""Safe v2 equivalents for v1's profile library.

Profiles are content, not executable Lua.  This module makes the migration
status visible and turns each proven equivalent into one typed protocol-v3 job.
Profiles that depended on undocumented stock scripts remain explicitly marked
as unavailable instead of silently pretending to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .job import Job, JobValidationError, op_bulk_edit, op_career_set, op_export_squad


@dataclass(frozen=True, slots=True)
class ProfileAction:
    id: str
    label: str
    description: str
    category: str
    mode: str  # native | pending
    reason: str = ""


_CAREER: Mapping[str, Mapping[str, int]] = {
    "full_fitness": {"fitness": 100},
    "full_sharpness": {"sharpness": 100},
    "full_form": {"form": 100},
    "full_morale": {"morale": 100},
    "full_form_morale_sharpness": {"form": 100, "morale": 120, "sharpness": 100},
    "matchday_pack": {"fitness": 100, "sharpness": 100},
    "squad_boost_pack": {"fitness": 100, "form": 100, "morale": 120, "sharpness": 100},
}

_MASS: Mapping[str, Mapping[str, int]] = {
    "never_retire": {"isretiring": 0},
    "force_retire": {"isretiring": 1},
    "modifier_zero": {"modifier": 0},
    "pot_99_all": {"potential": 99},
    "ovr_99_pot_99": {"overallrating": 99, "potential": 99},
    "ovr_1_pot_99": {"overallrating": 1, "potential": 99},
    "untuck_shirts": {"jerseystylecode": 1},
    "tight_shirts": {"jerseyfit": 1},
}

_TEAM_MASS: Mapping[str, Mapping[str, int]] = {
    "pot_99_team": {"potential": 99},
    "ovr_99_pot_99_team": {"overallrating": 99, "potential": 99},
}

# Recurring v1 event profiles → one-shot typed career equivalents.
# Honest: not a host event subscription; same fields as the one-shot profile.
_ONESHOT_ALIAS: Mapping[str, str] = {
    "full_fitness_event": "full_fitness",
    "full_sharpness_event": "full_sharpness",
    "auto_form_morale_sharpness": "full_form_morale_sharpness",
    "auto_max_form": "full_form",
    "auto_max_morale": "full_morale",
}

# Profiles that share another profile's job builder (export / aliases).
_EXPORT_IDS = frozenset({"export_user_squad", "list_players"})

# The primary Automations controls are product capabilities, not optional
# ``profiles.json`` content.  Keep their label, availability and job builder
# in this typed module so a missing/corrupt user profile file cannot leave a
# visible button that has no action behind it.
_BUILTIN_ACTIONS: Mapping[str, tuple[str, str, str]] = {
    "full_fitness": ("Fitness", "Set the current senior squad to match fitness.", "boost"),
    "full_sharpness": ("Sharpness", "Set the current senior squad sharpness.", "boost"),
    "full_form": ("Form", "Set the current senior squad form.", "boost"),
    "full_morale": ("Morale", "Set the current senior squad morale.", "boost"),
    "full_form_morale_sharpness": (
        "Form + Morale + Sharpness",
        "Set the current senior squad form, morale and sharpness.",
        "boost",
    ),
    "matchday_pack": ("Match Day", "Set fitness and sharpness for the current senior squad.", "boost"),
    "squad_boost_pack": ("Full Squad Boost", "Set fitness, form, morale and sharpness.", "boost"),
}

_PENDING: Mapping[str, str] = {
    "extend_contracts": "Needs a verified contract-date operation.",
    "extend_cpu_contracts": "Needs a verified contract-date operation.",
    "mass_edit_age": "Needs a verified birthdate conversion operation.",
    "mass_edit_squadrole": "Needs a verified all-team career-role operation.",
    "fix_heads": "Needs a verified real-head mapping source.",
    "custom_headasset_map": "Requires a supplied player-to-head map.",
    "custom_tattoos_map": "Requires a supplied player-to-tattoo map.",
    "medium_socks": "Socklengthcode medium value is not confirmed against FC 26 LE.",
    "randomize_shoe_models": "Random visual writes need a reproducible safe contract.",
    "set_generic_heads": "Requires an explicit player list.",
    "set_generic_heads_alt": "Requires an explicit player list.",
    "players_list_retiring": "Requires an explicit player list.",
    "custom_headasset_to_manager": "Needs a verified manager appearance operation.",
    "unlock_boots": "Needs a verified unlock operation.",
    "unlock_mgr_clothes": "Needs a verified unlock operation.",
    "export_season_stats": "Needs a dedicated season export operation.",
    "export_transfer_history": "Needs a dedicated transfer-history export operation.",
    "export_fixtures": "Needs a dedicated fixture export operation.",
    "list_transfer_bans": "Needs a dedicated transfer-ban read operation.",
    "delete_generated_players": "Deletion stays unavailable without a verified safe contract.",
    "transfer_ban_all": "Needs a verified career-ban operation.",
    "find_teams_no_gk": "Needs a dedicated diagnostic operation.",
    "small_squad_find": "Needs a dedicated diagnostic operation.",
    "pap_all_playstyles": "Needs a verified Player Career operation.",
    "auto_max_vpro_fitness": "Needs a verified Player Career event operation.",
    "print_jersey_numbers": "Needs a dedicated jersey-number export operation.",
    "track_cm_events": "Debug event hooks are deliberately not enabled from the UI.",
}

_MASS_PENDING_REASON = (
    "Whole-save editing is disabled until it has a resumable, per-tick batch "
    "implementation. This prevents a large player-table write from freezing FC."
)


def actions(raw_profiles: Mapping[str, Any] | None = None) -> tuple[ProfileAction, ...]:
    """Create a complete, truthful profile inventory.

    ``profiles.json`` can add labels and legacy entries, but the core four
    matchday actions stay available even when that user-editable file is
    missing or malformed.
    """
    rows = list((raw_profiles or {}).get("profiles") or [])
    out: list[ProfileAction] = []
    for raw in rows:
        pid = str(raw.get("id") or "")
        if not pid:
            continue
        mode, reason = "pending", _PENDING.get(pid, "No safe v2 equivalent is defined yet.")
        if pid in _CAREER:
            mode, reason = "native", "Runs as a typed Career Mode operation."
        elif pid in _ONESHOT_ALIAS:
            mode, reason = (
                "native",
                "One-shot typed Career Mode op (v1 was a recurring event script; "
                "re-run when you need it again).",
            )
        elif pid in _MASS:
            mode, reason = "pending", _MASS_PENDING_REASON
        elif pid in _TEAM_MASS:
            mode, reason = "native", "Applies to your current Career Mode team."
        elif pid in _EXPORT_IDS:
            mode, reason = "native", "Exports the live squad through the v3 worker."
        out.append(ProfileAction(
            id=pid, label=str(raw.get("label") or pid),
            description=str(raw.get("description") or ""),
            category=str(raw.get("category") or "other"), mode=mode, reason=reason,
        ))
    seen = {item.id for item in out}
    for pid, (label, description, category) in _BUILTIN_ACTIONS.items():
        if pid not in seen:
            out.append(ProfileAction(
                id=pid,
                label=label,
                description=description,
                category=category,
                mode="native",
                reason="Runs as a typed Career Mode operation.",
            ))
    return tuple(out)


def action_for(profile_id: str, raw_profiles: Mapping[str, Any] | None = None) -> ProfileAction | None:
    """Return the single typed definition used by both UI and job builder."""
    return next((item for item in actions(raw_profiles) if item.id == profile_id), None)


def _resolve_career_fields(profile_id: str) -> Mapping[str, int] | None:
    if profile_id in _CAREER:
        return _CAREER[profile_id]
    alias = _ONESHOT_ALIAS.get(profile_id)
    if alias and alias in _CAREER:
        return _CAREER[alias]
    return None


def build_job(profile_id: str, *, teamid: int | None = None) -> Job:
    """Build the native v3 job for a profile or raise a useful refusal."""
    career = _resolve_career_fields(profile_id)
    if career is not None:
        return Job(
            ops=(op_career_set(
                profile_id, scope="user_senior_team", teamid=teamid, **career
            ),),
            label=profile_id.replace("_", " ").title(), origin=f"ui.profiles.{profile_id}",
        )
    if profile_id in _MASS:
        raise JobValidationError(_MASS_PENDING_REASON)
    if profile_id in _TEAM_MASS:
        return Job(
            ops=(op_bulk_edit(profile_id, scope="user_team", set_fields=_TEAM_MASS[profile_id], growth_mirror="auto"),),
            label=profile_id.replace("_", " ").title(), origin=f"ui.profiles.{profile_id}",
            budget_ms=5000,
        )
    if profile_id in _EXPORT_IDS:
        return Job(
            ops=(op_export_squad(profile_id),), label="Export Current Squad",
            origin=f"ui.profiles.{profile_id}",
        )
    raise JobValidationError(_PENDING.get(profile_id, "This v1 profile has no safe v2 equivalent yet."))
