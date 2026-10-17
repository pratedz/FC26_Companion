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
class ShirtIssue:
    """A requested shirt that cannot be written as planned.

    It belongs to one player and never blocks the rest of the plan. ``holder``
    is the current wearer when exactly one teammate wears the number.
    """

    playerid: int
    number: int
    holder: int | None = None
    holder_name: str = ""
    message: str = ""

    @property
    def can_swap(self) -> bool:
        return self.holder is not None


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
    # Per-player shirt problems. Apply skips these numbers, never the plan.
    shirt_issues: tuple[ShirtIssue, ...] = ()
    # Team-linked players outside the senior list (loaned out, reserves).
    # Their shirts count as worn, and the planner never moves them silently.
    shirt_hidden: frozenset[int] = frozenset()
    source_save_uid: str = ""
    source_teamid: int | None = None
    source_snapshot_revision: str = ""
    source_snapshot_fingerprint: str = ""

    def bind_source(self, save_uid: str, teamid: int | None,
                    snapshot_revision: str = "") -> "SquadPlan":
        """Capture the export that authored this plan; never rebind a draft."""
        if self.source_save_uid and (self.source_save_uid != str(save_uid)
                                     or self.source_teamid != teamid):
            raise JobValidationError("This plan belongs to a different Career save or club. Rebuild it.")
        return replace(self, source_save_uid=self.source_save_uid or str(save_uid),
                       source_teamid=self.source_teamid if self.source_teamid is not None else teamid,
                       source_snapshot_revision=self.source_snapshot_revision or str(snapshot_revision),
                       source_snapshot_fingerprint=self.source_snapshot_fingerprint or hashlib.sha256(
                           json.dumps([dict(member.base) for member in self.members], sort_keys=True,
                                      separators=(",", ":"), default=str).encode()).hexdigest())

    def reselect(self, rows: Sequence[Mapping[str, Any]]) -> "SquadPlan":
        """Change selected membership while retaining the original club context."""
        chosen = plan_from_rows(rows)
        prior = {member.playerid: member for member in self.members}
        members = tuple(prior.get(member.playerid, member) for member in chosen.members)
        selected = {member.playerid for member in members}
        previous = set(self.playerids)
        return replace(self, members=members, selection_revision="",
                       overrides={pid: patch for pid, patch in self.overrides.items() if pid in selected},
                       jersey_numbers={pid: number for pid, number in self.jersey_numbers.items()
                                       if pid in selected or pid not in previous},
                       shirt_issues=tuple(issue for issue in self.shirt_issues if issue.playerid in selected))

    def __post_init__(self) -> None:
        if not self.selection_revision and self.members:
            object.__setattr__(
                self, "selection_revision", selection_revision(self.members)
            )
        if self.source_save_uid and not self.source_snapshot_fingerprint:
            object.__setattr__(self, "source_snapshot_fingerprint", hashlib.sha256(
                json.dumps([dict(member.base) for member in self.members], sort_keys=True,
                           separators=(",", ":"), default=str).encode()).hexdigest())

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
        shirt_issues: Sequence[ShirtIssue] = (),
    ) -> "SquadPlan":
        cleaned: dict[int, dict[str, int]] = {}
        for raw_pid, patch in overrides.items():
            if not patch:
                continue
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
        if not cleaned and not shirts and not blocking_issues and not shirt_issues:
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
            shirt_issues=tuple(shirt_issues),
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
            shirt_issues=tuple(issue for issue in self.shirt_issues if issue.playerid in want),
        )

    def with_member_bases(self, bases: Mapping[int, Mapping[str, Any]]) -> "SquadPlan":
        members: list[SquadMember] = []
        for member in self.members:
            row = bases.get(int(member.playerid))
            if not row:
                members.append(member)
                continue
            merged = dict(member.base)
            # Detailed database snapshots do not include a display name.
            # normalize_record supplies an empty one; it is not a rename.
            merged.update({name: value for name, value in row.items()
                           if name != "name" or value not in (None, "")})
            ovr_raw = merged.get("overallrating", member.ovr)
            try:
                ovr = int(ovr_raw) if ovr_raw not in (None, "") else member.ovr
            except (TypeError, ValueError):
                ovr = member.ovr
            members.append(replace(member, base=merged, ovr=ovr))
        return replace(self, members=tuple(members))

    def shirt_issue_for(self, playerid: int) -> ShirtIssue | None:
        return next((issue for issue in self.shirt_issues if issue.playerid == int(playerid)), None)

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


def plan_from_rows(rows: Sequence[Mapping[str, Any]], *, source_save_uid: str = "",
                   source_teamid: int | None = None,
                   source_snapshot_revision: str = "") -> SquadPlan:
    """Keep the full Club selection; worker jobs are bounded independently."""
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
    if not members:
        raise JobValidationError("Select at least one squad player.")
    return SquadPlan(members=tuple(members), source_save_uid=source_save_uid,
                     source_teamid=source_teamid, source_snapshot_revision=source_snapshot_revision)


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
    *,
    skipped: list[tuple[int, int, str]] | None = None,
) -> list[tuple[int, int]]:
    """Order shirt writes so the destination is free at every step.

    Career Mode crashes when two players on the team wear the same number,
    and it sees each database write immediately. A swap is parked on a free
    shirt first, then completed. The last write for a player is their
    requested number.

    A write that cannot be made without a duplicate is dropped on its own and
    reported in ``skipped`` as ``(playerid, number, reason)``; every other
    shirt write still goes ahead.
    """
    holders: dict[int, int] = {}
    worn: dict[int, int] = {}
    shared: set[int] = set()
    for pid, raw in current.items():
        try:
            number = int(raw or 0)
        except (TypeError, ValueError):
            number = 0
        player = int(pid)
        if 1 <= number <= 99 and number not in holders:
            holders[number] = player
            worn[player] = number
        elif 1 <= number <= 99:
            # Two teammates already share this number. Moving one of them
            # still leaves the other on it, so it is never a free target.
            shared.add(number)
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

    def drop(player: int, number: int, reason: str) -> None:
        remaining.pop(player, None)
        if skipped is not None:
            skipped.append((player, number, reason))

    for player, number in sorted(remaining.items()):
        if number in shared:
            drop(player, number, "shared")
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
        blocked = sorted(
            (player, number)
            for player, number in remaining.items()
            if holders.get(number) not in remaining
        )
        if blocked:
            # The wearer is not moving in this plan. Skip only this write;
            # anyone waiting for this player's old number is re-checked next.
            player, number = blocked[0]
            drop(player, number, "worn")
            continue
        wanted = set(remaining.values())
        spare = next((number for number in range(99, 1, -1)
                      if number not in holders and number not in wanted and number not in shared), None)
        if spare is None:
            player = min(remaining)
            drop(player, remaining[player], "no_spare")
            continue
        assign(min(remaining), spare)
    return writes


def build_apply_job(
    plan: SquadPlan,
    *,
    growth_mirror: str = "auto",
    dry_run: bool = False,
    label: str = "",
    skipped: list[tuple[int, int, str]] | None = None,
) -> Job:
    """One multi-op job: set_fields per selected player (same template)."""
    if plan.blocking_issues:
        raise JobValidationError("Plan needs a rebuild: " + " ".join(plan.blocking_issues))
    if not plan.members:
        raise JobValidationError("No players in the plan.")
    if not plan.template and not plan.overrides and not plan.jersey_numbers:
        raise JobValidationError("Stage a template before applying.")

    teamid = int(plan.source_teamid or 0)
    for member in (() if teamid else plan.members):
        try:
            teamid = int(member.base.get("teamid") or 0)
        except (TypeError, ValueError):
            teamid = 0
        if teamid > 0:
            break
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
                teamid=teamid or None,
            )
        )
    # Never write a shirt that somebody still wears. The game reads each
    # teamplayerlinks update immediately and crashes on a shared number.
    current_shirts = dict(plan.shirt_book)
    for member in plan.members:
        current_shirts.setdefault(member.playerid, member.base.get("jerseynumber"))
    for pid, number in safe_jersey_writes(current_shirts, plan.jersey_numbers, skipped=skipped):
        # Each shirt write re-checks the club in game and refuses a taken
        # number (displace=False). One refused shirt must not stop the rest.
        ops.append(replace(
            op_set_fields(
                f"jersey{pid}-{number}",
                table="teamplayerlinks",
                key_value=int(pid),
                fields={"jerseynumber": number},
                growth_mirror="off",
                teamid=teamid or None,
                displace=False,
            ),
            on_error="continue",
        ))
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
    skipped: list[tuple[int, int, str]] | None = None,
) -> tuple[Job, ...]:
    """Split a plan so each game job finishes inside the 5000ms worker cap.

    Shirt changes are kept in their safe order. A later part is only queued
    after the earlier part has been applied.
    """
    full = build_apply_job(
        plan, growth_mirror=growth_mirror, dry_run=dry_run, label=label, skipped=skipped
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


# ---------------------------------------------------------------------------
# Per-player issues: blocked fields, shirt problems and "Apply the rest".
# ---------------------------------------------------------------------------


def _issue_owner(plan: SquadPlan, issue: str) -> int | None:
    """The selected player an issue line belongs to (``"Name: ..."``)."""
    best: tuple[int, int] | None = None
    for member in plan.members:
        prefix = member.name + ":"
        if issue.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), member.playerid)
    return best[1] if best else None


def blocked_playerids(plan: SquadPlan) -> set[int] | None:
    """Players whose required changes were rejected.

    ``None`` means an issue belongs to the whole plan (for example unknown
    starters), so no part of it may be applied.
    """
    owners: set[int] = set()
    for issue in plan.blocking_issues:
        owner = _issue_owner(plan, issue)
        if owner is None:
            return None
        owners.add(owner)
    return owners


# Words that mark a staging note as a problem rather than an explanation.
_PROBLEM_WORDS = ("no applicable playstyles", "rebuild", "rejected", "failed", "missing", "invalid")


def issue_playerids(plan: SquadPlan) -> set[int]:
    """Every player with any problem: blocked fields, shirts or staging notes."""
    found = {owner for owner in (_issue_owner(plan, issue) for issue in plan.blocking_issues)
             if owner is not None}
    found.update(issue.playerid for issue in plan.shirt_issues)
    for note in plan.warnings:
        owner = _issue_owner(plan, note)
        if owner is not None and any(word in note.casefold() for word in _PROBLEM_WORDS):
            found.add(owner)
    return found


def _member_name(plan: SquadPlan, pid: int) -> str:
    member = next((m for m in plan.members if m.playerid == pid), None)
    return member.name if member else str(plan.shirt_names.get(pid) or pid)


def plan_without_blocked(plan: SquadPlan) -> tuple[SquadPlan, list[str]]:
    """Keep every valid item; drop only blocked players' fields.

    Shirts are separate items: a player's rejected PlayStyles do not cancel
    his valid shirt. Shirt issues are already absent from ``jersey_numbers``.
    Returns the reduced plan and plain sentences naming what is skipped.
    """
    blocked = blocked_playerids(plan)
    if blocked is None:
        raise JobValidationError("This problem affects the whole plan: " + " ".join(plan.blocking_issues))
    skipped = [f"{_member_name(plan, pid)}: player changes skipped until fixed."
               for pid in sorted(blocked, key=lambda pid: _member_name(plan, pid))]
    skipped.extend(f"{_member_name(plan, issue.playerid)}: shirt skipped. {issue.message}".strip()
                   for issue in plan.shirt_issues)
    rest = replace(
        plan,
        overrides={pid: patch for pid, patch in plan.overrides.items() if pid not in blocked},
        blocking_issues=(),
    )
    if not rest.overrides and not rest.jersey_numbers and not rest.template:
        raise JobValidationError("Nothing valid is left to apply. Fix the listed problems first.")
    return rest, skipped


def splice_player(plan: SquadPlan, source: SquadPlan, playerid: int) -> SquadPlan:
    """Replace one player's staged result; every other player stays as reviewed."""
    pid = int(playerid)
    name = _member_name(plan, pid)

    def mine(text: str) -> bool:
        return text.startswith(name + ":")

    overrides = dict(plan.overrides)
    if pid in source.overrides:
        overrides[pid] = dict(source.overrides[pid])
    else:
        overrides.pop(pid, None)
    reasons = dict(plan.reasons)
    if source.reasons.get(pid):
        reasons[pid] = source.reasons[pid]
    hints = dict(plan.library_hints)
    if source.library_hints.get(pid):
        hints[pid] = source.library_hints[pid]
    return replace(
        plan,
        overrides=overrides,
        reasons=reasons,
        library_hints=hints,
        blocking_issues=tuple(issue for issue in plan.blocking_issues if not mine(issue))
        + tuple(issue for issue in source.blocking_issues if mine(issue)),
        warnings=tuple(note for note in plan.warnings if not mine(note))
        + tuple(note for note in source.warnings if mine(note)),
    )
