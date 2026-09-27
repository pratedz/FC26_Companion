"""Squad Planner — multi-player draft state (template × selection).

Grok / presets / manual only *author* a template. Apply still goes through
protocol-v3 jobs. Same safety model as single-player BuildProposal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from .builds import BuildProposal, proposal
from .job import Job, JobValidationError, Op, op_set_fields
from .player import normalize_patch, player_id


MAX_PLAYERS = 25
MAX_FIELDS = 20
MAX_OVERRIDE_FIELDS = 50
#: Shirt writes scan the whole link table. More than this in one job overruns
#: the worker's 5000ms cap and the plan comes back failed.
SHIRT_OPS_PER_JOB = 8
FIELD_OPS_PER_JOB = 8


@dataclass(frozen=True, slots=True)
class SquadMember:
    """One selected squad row with enough context for AI + matrix."""

    playerid: int
    name: str = ""
    position: str = ""
    ovr: int | None = None
    base: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SquadMember":
        raw = dict(row.get("_raw") or row)
        pid = player_id(raw.get("playerid") or raw.get("id") or row.get("id"))
        ovr_raw = raw.get("overallrating") or raw.get("ovr")
        try:
            ovr = int(ovr_raw) if ovr_raw not in (None, "") else None
        except (TypeError, ValueError):
            ovr = None
        base = {
            k: v for k, v in raw.items()
            if k not in {"_raw", "_key"} and not str(k).startswith("_")
        }
        return cls(
            playerid=pid,
            name=str(raw.get("name") or raw.get("playername") or row.get("name") or pid),
            position=str(
                raw.get("position")
                or raw.get("preferredposition1")
                or raw.get("pos")
                or row.get("pos")
                or ""
            ),
            ovr=ovr,
            base=base,
        )


@dataclass(frozen=True, slots=True)
class SquadPlan:
    """Multi-player draft: shared template (+ optional per-player overrides)."""

    members: tuple[SquadMember, ...]
    template: Mapping[str, int] = field(default_factory=dict)
    overrides: Mapping[int, Mapping[str, int]] = field(default_factory=dict)
    source: str = ""
    label: str = ""
    summary: str = ""
    warnings: tuple[str, ...] = ()
    blocking_issues: tuple[str, ...] = ()
    selection_revision: str = ""
    reasons: Mapping[int, str] = field(default_factory=dict)
    library_hints: Mapping[int, str] = field(default_factory=dict)
    chips: tuple[str, ...] = ()
    identity_count: int = 0
    mode: str = "template"
    # Current squad shirts (playerid → number) and the shirts this plan will write.
    shirt_book: Mapping[int, int] = field(default_factory=dict)
    shirt_names: Mapping[int, str] = field(default_factory=dict)
    jersey_numbers: Mapping[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.selection_revision and self.members:
            object.__setattr__(
                self, "selection_revision", selection_revision(self.members)
            )

    @property
    def playerids(self) -> tuple[int, ...]:
        return tuple(m.playerid for m in self.members)

    @property
    def field_count(self) -> int:
        if self.overrides:
            return sum(len(patch) for patch in self.overrides.values())
        return len(self.template)

    @property
    def player_count(self) -> int:
        return len(self.members)

    def with_template(self, proposed: BuildProposal) -> "SquadPlan":
        fields = dict(proposed.fields)
        if len(fields) > MAX_FIELDS:
            raise JobValidationError(
                f"Template has {len(fields)} fields; max is {MAX_FIELDS}."
            )
        return replace(
            self,
            template=fields,
            source=proposed.source,
            label=proposed.label,
            summary=proposed.summary,
            warnings=tuple(proposed.warnings),
            mode="template",
            blocking_issues=(),
        )

    def with_overrides(
        self,
        overrides: Mapping[int, Mapping[str, int]],
        *,
        source: str = "grok",
        label: str = "Per-player Grok session",
        summary: str = "",
        warnings: Sequence[str] = (),
        reasons: Mapping[int, str] | None = None,
        library_hints: Mapping[int, str] | None = None,
        chips: Sequence[str] = (),
        identity_count: int = 0,
        jersey_numbers: Mapping[int, int] | None = None,
        blocking_issues: Sequence[str] = (),
    ) -> "SquadPlan":
        cleaned: dict[int, dict[str, int]] = {}
        for raw_pid, patch in overrides.items():
            fields = normalize_patch(patch)
            if not fields:
                continue
            if len(fields) > MAX_OVERRIDE_FIELDS:
                raise JobValidationError(
                    f"Player {raw_pid} has {len(fields)} fields; "
                    f"max per player is {MAX_OVERRIDE_FIELDS}."
                )
            cleaned[int(raw_pid)] = fields
        shirts = {
            int(pid): int(number)
            for pid, number in dict(jersey_numbers or {}).items()
            if 1 <= int(number) <= 99
        }
        if not cleaned and not shirts:
            raise JobValidationError("No per-player patches to stage.")
        return replace(
            self,
            template={},
            overrides=cleaned,
            source=source,
            label=label,
            summary=summary,
            warnings=tuple(warnings),
            blocking_issues=tuple(blocking_issues),
            reasons={int(k): str(v) for k, v in dict(reasons or {}).items()},
            library_hints={int(k): str(v) for k, v in dict(library_hints or {}).items()},
            chips=tuple(chips),
            identity_count=int(identity_count),
            jersey_numbers=shirts,
            mode="per_player",
        )

    def keeping(self, playerids: Sequence[int]) -> "SquadPlan":
        want = {int(pid) for pid in playerids}
        members = tuple(m for m in self.members if m.playerid in want)
        if not members:
            raise JobValidationError("Select at least one squad player.")
        return replace(
            self,
            members=members,
            selection_revision="",
            overrides={
                pid: dict(patch)
                for pid, patch in self.overrides.items()
                if pid in want
            },
            jersey_numbers={
                pid: number
                for pid, number in self.jersey_numbers.items()
                if pid in want or pid not in {member.playerid for member in self.members}
            },
        )

    def with_member_bases(self, bases: Mapping[int, Mapping[str, Any]]) -> "SquadPlan":
        members: list[SquadMember] = []
        for member in self.members:
            row = bases.get(int(member.playerid))
            if not row:
                members.append(member)
                continue
            merged = dict(member.base)
            merged.update(dict(row))
            ovr_raw = merged.get("overallrating", member.ovr)
            try:
                ovr = int(ovr_raw) if ovr_raw not in (None, "") else member.ovr
            except (TypeError, ValueError):
                ovr = member.ovr
            members.append(replace(member, base=merged, ovr=ovr))
        return replace(self, members=tuple(members))

    def patch_for(self, playerid: int) -> dict[str, int]:
        """Template merged with any per-player override."""
        out = dict(self.template)
        extra = self.overrides.get(int(playerid)) or {}
        out.update(dict(extra))
        return out

    def matrix_rows(self) -> tuple[dict[str, Any], ...]:
        """Flatten for UI: one row per player × field that will change."""
        rows: list[dict[str, Any]] = []
        for member in self.members:
            patch = self.patch_for(member.playerid)
            for field_name, new_val in sorted(patch.items()):
                old = member.base.get(field_name)
                try:
                    old_i = int(old) if old is not None and old != "" else None
                except (TypeError, ValueError):
                    old_i = None
                rows.append(
                    {
                        "playerid": member.playerid,
                        "name": member.name,
                        "position": member.position,
                        "field": field_name,
                        "before": old_i,
                        "after": int(new_val),
                        "delta": (
                            int(new_val) - old_i if old_i is not None else None
                        ),
                        "changed": old_i != int(new_val),
                    }
                )
        return tuple(rows)

    def blast_summary(self) -> str:
        if self.overrides:
            return (
                f"{self.player_count} players · {self.field_count} fields · "
                f"{self.identity_count} identity"
            )
        return (
            f"{self.player_count} player(s) · {self.field_count} field(s) · "
            f"source {self.source or 'manual'}"
        )


def selection_revision(members: Sequence[SquadMember]) -> str:
    """Hash selection identity so a late Grok reply can be discarded."""
    body = {
        "ids": [m.playerid for m in members],
        "names": [m.name for m in members],
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def plan_from_rows(rows: Sequence[Mapping[str, Any]]) -> SquadPlan:
    """Build an empty plan from Club grid selection (max MAX_PLAYERS)."""
    members: list[SquadMember] = []
    seen: set[int] = set()
    for row in rows:
        try:
            member = SquadMember.from_row(row)
        except Exception:
            continue
        if member.playerid in seen:
            continue
        seen.add(member.playerid)
        members.append(member)
        if len(members) >= MAX_PLAYERS:
            break
    if not members:
        raise JobValidationError("Select at least one squad player.")
    return SquadPlan(members=tuple(members))


def template_from_fields(
    fields: Mapping[str, Any],
    *,
    source: str = "manual",
    label: str = "Squad template",
    summary: str = "",
    warnings: Sequence[str] = (),
) -> BuildProposal:
    """Validate a field map the same way single-player drafts do."""
    cleaned = normalize_patch(fields)
    if not cleaned:
        raise JobValidationError("Template has no valid fields.")
    if len(cleaned) > MAX_FIELDS:
        raise JobValidationError(
            f"Template has {len(cleaned)} fields; max is {MAX_FIELDS}."
        )
    return proposal(
        cleaned,
        source=source,
        label=label,
        summary=summary,
        warnings=tuple(warnings),
    )


def safe_jersey_writes(
    current: Mapping[int, int | None],
    targets: Mapping[int, int],
) -> list[tuple[int, int]]:
    """Order shirt writes so the destination is free at every step.

    Career Mode crashes when two players on the team wear the same number,
    and it sees each database write immediately. A swap is parked on the
    lowest free shirt first, then completed. The last write for a player is
    their requested number.
    """
    holders: dict[int, int] = {}
    worn: dict[int, int] = {}
    for pid, raw in current.items():
        try:
            number = int(raw or 0)
        except (TypeError, ValueError):
            number = 0
        player = int(pid)
        if 1 <= number <= 99 and number not in holders:
            holders[number] = player
            worn[player] = number
        else:
            worn.setdefault(player, 0)

    remaining: dict[int, int] = {}
    for pid, raw in targets.items():
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        player = int(pid)
        if not 1 <= number <= 99:
            continue
        worn.setdefault(player, 0)
        if worn[player] != number:
            remaining[player] = number

    writes: list[tuple[int, int]] = []

    def assign(player: int, number: int) -> None:
        old = worn.get(player, 0)
        if old and holders.get(old) == player:
            holders.pop(old, None)
        worn[player] = number
        holders[number] = player
        writes.append((player, number))

    while remaining:
        ready = sorted(
            player
            for player, number in remaining.items()
            if holders.get(number) in (None, player)
        )
        if ready:
            player = ready[0]
            assign(player, remaining.pop(player))
            continue
        blocked = [
            (player, number)
            for player, number in remaining.items()
            if holders.get(number) not in remaining
        ]
        if blocked:
            player, number = blocked[0]
            raise JobValidationError(
                f"Shirt #{number} is already worn and cannot be freed without a duplicate."
            )
        spare = next((number for number in range(1, 100) if number not in holders), None)
        if spare is None:
            raise JobValidationError(
                "No free shirt number left to move players without a duplicate."
            )
        assign(min(remaining), spare)
    return writes


def build_apply_job(
    plan: SquadPlan,
    *,
    growth_mirror: str = "auto",
    dry_run: bool = False,
    label: str = "",
) -> Job:
    """One multi-op job: set_fields per selected player (same template)."""
    if plan.blocking_issues:
        raise JobValidationError("Plan needs a rebuild: " + " ".join(plan.blocking_issues))
    if not plan.members:
        raise JobValidationError("No players in the plan.")
    if not plan.template and not plan.overrides and not plan.jersey_numbers:
        raise JobValidationError("Stage a template before applying.")
    if plan.player_count > MAX_PLAYERS:
        raise JobValidationError(f"At most {MAX_PLAYERS} players per plan.")

    ops: list[Op] = []
    for member in plan.members:
        patch = plan.patch_for(member.playerid)
        if not patch:
            continue
        safe = normalize_patch(patch)
        ops.append(
            op_set_fields(
                f"p{member.playerid}",
                key_value=member.playerid,
                fields=safe,
                growth_mirror=growth_mirror,  # type: ignore[arg-type]
                on_field_error="continue",
            )
        )
    teamid = 0
    for member in plan.members:
        try:
            teamid = int(member.base.get("teamid") or 0)
        except (TypeError, ValueError):
            teamid = 0
        if teamid > 0:
            break
    # Never write a shirt that somebody still wears. The game reads each
    # teamplayerlinks update immediately and crashes on a shared number.
    current_shirts = dict(plan.shirt_book)
    for member in plan.members:
        current_shirts.setdefault(member.playerid, member.base.get("jerseynumber"))
    for pid, number in safe_jersey_writes(current_shirts, plan.jersey_numbers):
        ops.append(
            op_set_fields(
                f"jersey{pid}-{number}",
                table="teamplayerlinks",
                key_value=int(pid),
                fields={"jerseynumber": number},
                growth_mirror="off",
                teamid=teamid or None,
                displace=False,
            )
        )
    if not ops:
        raise JobValidationError("Nothing to write for this selection.")
    return Job(
        ops=tuple(ops),
        label=label or f"Squad plan · {plan.player_count}p · {plan.field_count}f",
        origin="ui.squad_planner.apply",
        require_cm=True,
        dry_run=dry_run,
        budget_ms=_job_budget(ops),
    )


def build_apply_jobs(
    plan: SquadPlan,
    *,
    growth_mirror: str = "auto",
    dry_run: bool = False,
    label: str = "",
) -> tuple[Job, ...]:
    """Split a plan so each game job finishes inside the 5000ms worker cap.

    Shirt changes are kept in their safe order. A later part is only queued
    after the earlier part has been applied.
    """
    full = build_apply_job(
        plan, growth_mirror=growth_mirror, dry_run=dry_run, label=label
    )
    field_ops = [
        op for op in full.ops if str((op.body or {}).get("table") or "players") != "teamplayerlinks"
    ]
    shirt_ops = [
        op for op in full.ops if str((op.body or {}).get("table") or "") == "teamplayerlinks"
    ]
    groups: list[list[Op]] = []
    for start in range(0, len(field_ops), FIELD_OPS_PER_JOB):
        groups.append(field_ops[start:start + FIELD_OPS_PER_JOB])
    for start in range(0, len(shirt_ops), SHIRT_OPS_PER_JOB):
        groups.append(shirt_ops[start:start + SHIRT_OPS_PER_JOB])
    if len(groups) <= 1:
        return (full,)
    total = len(groups)
    jobs: list[Job] = []
    base = full.label or "Squad plan"
    for index, ops in enumerate(groups, start=1):
        jobs.append(
            Job(
                ops=tuple(ops),
                label=f"{base} · part {index}/{total}",
                origin=full.origin,
                require_cm=True,
                dry_run=dry_run,
                budget_ms=_job_budget(ops),
            )
        )
    return tuple(jobs)


def _job_budget(ops: Sequence[Op]) -> int:
    shirts = any(str((op.body or {}).get("table") or "") == "teamplayerlinks" for op in ops)
    if shirts:
        return 5000
    return min(5000, 200 + 150 * max(1, len(ops)))


def selection_summary_for_ai(plan: SquadPlan) -> list[dict[str, Any]]:
    """Compact roster lines for the Grok squad-template prompt."""
    out: list[dict[str, Any]] = []
    for m in plan.members:
        out.append(
            {
                "playerid": m.playerid,
                "name": m.name,
                "position": m.position,
                "overallrating": m.ovr,
                "sample": {
                    k: m.base.get(k)
                    for k in (
                        "acceleration", "sprintspeed", "finishing",
                        "shortpassing", "defensiveawareness", "stamina",
                    )
                    if k in m.base
                },
            }
        )
    return out
