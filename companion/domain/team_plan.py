"""User-owned team context and honest before/after squad summaries."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

FORMATIONS: dict[str, tuple[str, ...]] = {
    "4-3-3": ("GK", "RB", "CB", "CB", "LB", "CDM", "CM", "CM", "RW", "ST", "LW"),
    "4-2-3-1": ("GK", "RB", "CB", "CB", "LB", "CDM", "CDM", "RM", "CAM", "LM", "ST"),
    "4-4-2": ("GK", "RB", "CB", "CB", "LB", "RM", "CM", "CM", "LM", "ST", "ST"),
    "3-5-2": ("GK", "CB", "CB", "CB", "RWB", "CDM", "CM", "CM", "LWB", "ST", "ST"),
    "5-3-2": ("GK", "RWB", "CB", "CB", "CB", "LWB", "CM", "CDM", "CM", "ST", "ST"),
}
ROLE_NAMES = ("starter", "rotation", "prospect")


@dataclass(frozen=True, slots=True)
class TeamContext:
    formation: str = "4-3-3"
    starters: tuple[int, ...] = ()
    roles: Mapping[int, str] = field(default_factory=dict)
    overall_min: int | None = None
    overall_max: int | None = None

    def __post_init__(self) -> None:
        if self.formation not in FORMATIONS:
            raise ValueError("Choose a supported formation.")
        ids = tuple(dict.fromkeys(int(pid) for pid in self.starters))
        roles = {int(pid): str(role).casefold() for pid, role in self.roles.items()}
        if any(pid < 1 for pid in (*ids, *roles)):
            raise ValueError("Team roles require Career player IDs.")
        if any(role not in ROLE_NAMES for role in roles.values()):
            raise ValueError("Choose starter, rotation or prospect.")
        ids = tuple(pid for pid in ids if roles.get(pid, "starter") == "starter")
        ids = tuple(dict.fromkeys((*ids, *(pid for pid, role in roles.items() if role == "starter"))))
        if len(ids) > 11:
            raise ValueError("A starting XI can contain at most 11 players.")
        if (self.overall_min is None) != (self.overall_max is None):
            raise ValueError("Enter both ends of the overall range.")
        if self.overall_min is not None and not 1 <= int(self.overall_min) <= int(self.overall_max) <= 99:
            raise ValueError("Overall range must be between 1 and 99, with minimum first.")
        object.__setattr__(self, "starters", ids)
        object.__setattr__(self, "roles", roles)


def context_from_rows(rows: Sequence[Mapping[str, Any]]) -> TeamContext:
    """Use explicit lineup flags only; OVR/order never implies starter status."""
    ids: list[int] = []
    roles: dict[int, str] = {}
    for row in rows:
        raw = row.get("_raw") or row
        try:
            pid = int(raw.get("playerid") or raw.get("id"))
        except (TypeError, ValueError):
            continue
        marker = raw.get("is_starter", raw.get("starter", raw.get("startingxi")))
        role = str(raw.get("planning_role") or "").casefold()
        if marker is True or marker == 1 or str(marker).casefold() in {"true", "yes"} or role == "starter":
            ids.append(pid)
        if role in ROLE_NAMES:
            roles[pid] = role
    # Incomplete/ambiguous exports are not a usable XI.
    if len(set(ids)) > 11:
        ids = []
        roles = {pid: role for pid, role in roles.items() if role != "starter"}
    return TeamContext(starters=tuple(ids), roles=roles)


def apply_team_context(session: Any, context: TeamContext) -> Any:
    from .squad_session import spread_target

    recipe = session.recipe
    if context.overall_min is not None:
        recipe = replace(recipe, band_lo=int(context.overall_min), band_hi=int(context.overall_max),
                         target_ovr=None, soft_band=False)
    targets = []
    for item in session.targets:
        role = context.roles.get(item.playerid)
        if role is None and item.playerid in context.starters:
            role = "starter"
        rung = {"starter": "star", "rotation": "rotation", "prospect": "prospect"}.get(role, item.rung)
        target = item.target_ovr
        if not item.pinned and (context.overall_min is not None or role is not None):
            target = spread_target(rung, recipe, item.ovr)
            if not recipe.allow_nerf:
                target = max(target, item.ovr or target)
        why = item.why
        if role:
            why += f" · Your role: {role}"
        targets.append(replace(item, rung=rung, target_ovr=target, why=why))
    extra = (f"Overall {context.overall_min}–{context.overall_max}",) if context.overall_min is not None else ()
    chips = tuple(dict.fromkeys((*session.chips, f"Formation {context.formation}", *extra)))
    return replace(session, recipe=recipe, targets=tuple(targets), chips=chips)


_COMPATIBLE = {
    "GK": {"GK"}, "CB": {"CB", "LCB", "RCB"},
    "RB": {"RB", "RWB"}, "LB": {"LB", "LWB"},
    "RWB": {"RWB", "RB", "RM"}, "LWB": {"LWB", "LB", "LM"},
    "CDM": {"CDM", "RDM", "LDM", "CM"},
    "CM": {"CM", "RCM", "LCM", "CDM", "CAM"},
    "CAM": {"CAM", "RAM", "LAM", "CF"},
    "RM": {"RM", "RW", "RAM"}, "LM": {"LM", "LW", "LAM"},
    "RW": {"RW", "RM", "RF"}, "LW": {"LW", "LM", "LF"},
    "ST": {"ST", "RS", "LS", "CF"},
}


def coverage(members: Sequence[Any], formation: str) -> tuple[int, tuple[str, ...]]:
    """Maximum matching: one player cannot fill two formation slots."""
    from .squad_session import _pos_token

    slots = FORMATIONS[formation]
    positions = []
    for member in members:
        base = member.base
        raw = [member.position, *(base.get(f"preferredposition{i}") for i in range(1, 5))]
        positions.append({_pos_token(pos).upper() for pos in raw if pos not in (None, "", -1)})
    matches: dict[int, int] = {}

    def assign(slot_index: int, visited: set[int]) -> bool:
        for player_index, choices in enumerate(positions):
            if player_index in visited or not choices.intersection(_COMPATIBLE[slots[slot_index]]):
                continue
            visited.add(player_index)
            old_slot = matches.get(player_index)
            if old_slot is None or assign(old_slot, visited):
                matches[player_index] = slot_index
                return True
        return False

    for index in range(len(slots)):
        assign(index, set())
    filled = set(matches.values())
    return len(filled), tuple(slot for index, slot in enumerate(slots) if index not in filled)


def team_summary(original_plan: Any, staged_plan: Any, context: TeamContext) -> tuple[str, ...]:
    """Summarize the selected planning roster; missing stats stay unknown."""
    members = original_plan.members
    patched = {member.playerid: {**member.base, **staged_plan.patch_for(member.playerid)} for member in members}
    lines = [f"Planning roster: {len(members)} players · {context.formation} (formation guidance)"]
    filled, missing = coverage(members, context.formation)
    lines.append(f"Position coverage: {filled}/11 slots" + (" · Need " + ", ".join(missing) if missing else " · All slots covered"))
    if context.starters:
        starters = [m for m in members if m.playerid in context.starters]
        bench = [m for m in members if m.playerid not in context.starters]
        lines.append(f"Known starters in this selection: {len(starters)}/11 · Other players: {len(bench)}")
        groups = [("Starter OVR", starters), ("Other players OVR", bench)]
    else:
        lines.append("Starting XI unknown — assign starter roles to compare starters and bench.")
        groups = [("Roster OVR", members)]

    def average(items: Sequence[Any], fields: tuple[str, ...], after: bool) -> float | None:
        values = []
        for member in items:
            row = patched[member.playerid] if after else member.base
            raw = [row.get(key, member.ovr if key == "overallrating" else None) for key in fields]
            if all(isinstance(value, (int, float)) and 0 < value <= 99 for value in raw):
                values.append(sum(raw) / len(raw))
        return sum(values) / len(values) if values else None

    for label, items in groups:
        before, after = average(items, ("overallrating",), False), average(items, ("overallrating",), True)
        lines.append(f"{label}: {before:.1f} → {after:.1f}" if before is not None and after is not None else f"{label}: unavailable")
    outfield = [m for m in members if str(m.position).casefold() not in {"gk", "0"}]
    metrics = {"Pace": ("acceleration", "sprintspeed"), "Passing": ("shortpassing", "longpassing", "vision"),
               "Defence": ("defensiveawareness", "standingtackle", "interceptions")}
    before_metrics = {}
    for label, fields in metrics.items():
        before, after = average(outfield, fields, False), average(outfield, fields, True)
        if before is not None and after is not None:
            before_metrics[label] = before
            lines.append(f"{label}: {before:.1f} → {after:.1f}")
        else:
            lines.append(f"{label}: detailed values needed")
    if len(before_metrics) == 3:
        weakest = min(before_metrics, key=before_metrics.get)
        lines.append(f"Lowest measured area: {weakest.lower()} · Means use players with complete values.")
    return tuple(lines)
