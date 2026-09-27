"""The job contract: ``Job`` + ``Op`` builders. The ONLY place ops are constructed.

This replaces v1's five independent Lua *code generators* (``card_to_lua``,
``add_team_lua``, ``career_ops``, ``player_apply``, ``undo_apply``). The wire
carries **data**, never Lua source — which is what closes R10 (anything that
could append to ``_pending.txt`` got arbitrary code execution inside FC 26).

Envelope (``docs/schema/job.schema.json``)::

    {
      "schema": 3,
      "job_id": "01JQ8F3K2R7XZ4M9QW1YV6HB0",   # ULID, == filename stem
      "core_min": "2.3.1",
      "label": "Messi -> OVR 94",
      "origin": "ui.cards.apply",
      "created_at": 1784960972,
      "deadline_kind": "immediate",
      "require_cm": true,
      "dry_run": false,
      "budget_ms": 200,
      "max_attempts": 3,
      "requires": {"save_uid": "4a7f..."},
      "grants":   {"allow_create_player": false, ...},
      "ops": [ {"op": "set_fields", "id": "stats", ...} ]
    }

``grants`` is deny-by-default: a job that does not carry
``allow_create_player`` cannot reach ``CreatePlayer`` no matter what the op list
says (SI-5). v1 relied on "the generator wouldn't emit that".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Literal, Mapping, Sequence

from .ids import is_ulid, new_ulid

SCHEMA = 3
# The envelope remains compatible with the truthful 2.3.1 worker. Individual
# operation versions provide the feature gate for newer behavior (Phase 5 uses
# bulk_edit/growth_sync v2), so upgrading the app must not disable every
# established feature in an already-running game.
CORE_MIN = "2.3.1"


def is_sign_job_label(label: str) -> bool:
    """True for current Sign jobs and leftover Add Player labels in Activity."""
    text = str(label or "").strip()
    if text.startswith("Add Player") or text.startswith("Sign:"):
        return True
    return text.startswith("Sign ") and ":" in text

DeadlineKind = Literal["immediate", "next_session", "manual"]

# SI-5: CreatePlayer with playerid >= 500000 terminates FC26.exe. Clean split in
# the evidence: 463139..494215 all succeeded, 506153..546619 all killed it.
GENERATED_ID_MIN = 460000
GENERATED_ID_MAX = 499999
PLAYERID_HARD_MAX = 499999

# Ops the core knows. An unknown op is a clean `unsupported_op` result, never a
# syntax error at 3am. Keep in lockstep with ingame/le_companion/ops/_registry.lua.
KNOWN_OPS: frozenset[str] = frozenset(
    {
        "diag.ping",
        "db.dump",
        "set_fields",
        "create_player",
        "add_to_team",
        "repair_partial_add",
        "transfer",
        "budget",
        "bulk_edit",
        "export_squad",
        "snapshot",
        "growth_sync",
        "career.set",
        "injury.scan",
        "injury.cure",
    }
)

# Ops that cannot be rolled back, so `atomic: true` must reject them.
NON_REVERSIBLE_OPS: frozenset[str] = KNOWN_OPS

# Ops that require an explicit grant before the core will execute them.
OP_GRANTS: Mapping[str, str] = {
    "create_player": "allow_create_player",
    "add_to_team": "allow_add_to_team",
    # A repair only exists to complete an interrupted Add Player job.  Keep it
    # behind the same explicit grant; it must never become a general-purpose
    # player editor that can be queued by accident.
    "repair_partial_add": "allow_add_to_team",
}


class JobValidationError(ValueError):
    """A job was malformed. Raised before anything is written to the queue."""


@dataclass(frozen=True, slots=True)
class Grants:
    """Deny-by-default capabilities. SI-5, SI-8 become structural."""

    allow_create_player: bool = False
    allow_add_to_team: bool = False
    allow_delete_row: bool = False
    allow_memory_patch: bool = False
    # Whole-database profile migrations are deliberately separate from normal
    # team-scoped bulk edits. The UI requires an explicit confirmation before
    # issuing this grant.
    allow_mass_edit: bool = False

    def to_wire(self) -> dict[str, bool]:
        return {
            "allow_create_player": self.allow_create_player,
            "allow_add_to_team": self.allow_add_to_team,
            "allow_delete_row": self.allow_delete_row,
            "allow_memory_patch": self.allow_memory_patch,
            "allow_mass_edit": self.allow_mass_edit,
        }


@dataclass(frozen=True, slots=True)
class Op:
    """One typed operation. ``body`` holds the op-specific keys."""

    op: str
    id: str
    body: Mapping[str, Any] = field(default_factory=dict)
    on_error: Literal["abort", "continue"] = "abort"
    v: int = 1

    def to_wire(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op": self.op, "id": self.id}
        if self.v != 1:
            d["v"] = self.v
        if self.on_error != "abort":
            d["on_error"] = self.on_error
        d.update(self.body)
        return d


@dataclass(frozen=True, slots=True)
class Job:
    """An ordered list of ops, executed in one drain, sharing one context."""

    ops: tuple[Op, ...]
    job_id: str = ""
    label: str = ""
    origin: str = ""
    deadline_kind: DeadlineKind = "immediate"
    require_cm: bool = True
    dry_run: bool = False
    atomic: bool = False
    budget_ms: int = 200
    max_attempts: int = 3
    grants: Grants = field(default_factory=Grants)
    requires: Mapping[str, Any] = field(default_factory=dict)
    snapshot_to: str = ""
    expires_at: int = 0
    created_at: int = 0

    def __post_init__(self) -> None:
        if not self.job_id:
            object.__setattr__(self, "job_id", new_ulid())

    # ---- construction -------------------------------------------------

    def with_ops(self, *ops: Op) -> "Job":
        return replace(self, ops=self.ops + tuple(ops))

    # ---- validation ---------------------------------------------------

    def validate(self) -> None:
        """Raise ``JobValidationError`` if this job could not be executed safely.

        Runs *before* the file is written, so an invalid job never reaches the
        queue directory at all.
        """
        if not is_ulid(self.job_id):
            raise JobValidationError(f"job_id must be a 26-char ULID, got {self.job_id!r}")
        if not self.ops:
            raise JobValidationError("job has no ops")
        seen: set[str] = set()
        for op in self.ops:
            if not op.id:
                raise JobValidationError(f"op {op.op!r} has no id")
            if op.id in seen:
                raise JobValidationError(f"duplicate op id {op.id!r}")
            seen.add(op.id)
            if op.op not in KNOWN_OPS:
                raise JobValidationError(f"unknown op {op.op!r}")
            needed = OP_GRANTS.get(op.op)
            if needed and not getattr(self.grants, needed, False):
                raise JobValidationError(
                    f"op {op.op!r} requires grant {needed!r}, which was not given"
                )
            if op.op == "bulk_edit" and op.body.get("scope") == "all_players":
                raise JobValidationError(
                    "bulk_edit scope='all_players' is disabled until resumable batching is available"
                )
        if self.atomic and not self.dry_run:
            raise JobValidationError(
                "atomic:true is unavailable: the resident core cannot guarantee "
                "rollback for every supported host write"
            )
        if self.budget_ms <= 0 or self.budget_ms > 5000:
            raise JobValidationError(f"budget_ms out of range: {self.budget_ms}")
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise JobValidationError(f"max_attempts out of range: {self.max_attempts}")
        if self.deadline_kind not in ("immediate", "next_session", "manual"):
            raise JobValidationError(f"invalid deadline_kind: {self.deadline_kind!r}")
        if self.expires_at < 0:
            raise JobValidationError("expires_at must be a non-negative Unix timestamp")
        unknown_requires = set(self.requires) - {
            "career_mode", "save_uid", "core_version",
        }
        if unknown_requires:
            raise JobValidationError(
                f"unknown requirements: {sorted(unknown_requires)}"
            )
        if self.snapshot_to:
            unsupported = [op.op for op in self.ops if op.op != "set_fields"]
            if unsupported:
                raise JobValidationError(
                    "snapshot_to currently supports set_fields jobs only; "
                    f"unsupported ops: {sorted(set(unsupported))}"
                )
        # SI-5 enforced structurally, not by convention.
        for op in self.ops:
            if op.op == "create_player":
                pid = op.body.get("playerid")
                if pid is not None and int(pid) > PLAYERID_HARD_MAX:
                    raise JobValidationError(
                        f"playerid {pid} exceeds hard maximum {PLAYERID_HARD_MAX} "
                        "(CreatePlayer >= 500000 terminates FC26.exe)"
                    )
                rng = op.body.get("id_range")
                if rng and int(rng[1]) > PLAYERID_HARD_MAX:
                    raise JobValidationError(
                        f"id_range upper bound {rng[1]} exceeds {PLAYERID_HARD_MAX}"
                    )
            if op.op == "add_to_team" and op.body.get("strategy") != "dummy_overwrite":
                raise JobValidationError(
                    "Add Player only supports a live-verified free-agent dummy; "
                    "creating Career players is disabled."
                )
            if op.op == "repair_partial_add":
                if len(self.ops) != 1:
                    raise JobValidationError(
                        "repair_partial_add must be the only operation in its job"
                    )
                if not str(self.requires.get("save_uid") or ""):
                    raise JobValidationError(
                        "repair_partial_add requires the exact Career save_uid"
                    )

    # ---- wire ---------------------------------------------------------

    def to_wire(self, *, now: int | None = None) -> dict[str, Any]:
        self.validate()
        d: dict[str, Any] = {
            "schema": SCHEMA,
            "job_id": self.job_id,
            "core_min": CORE_MIN,
            "created_at": int(self.created_at or now or 0),
            "deadline_kind": self.deadline_kind,
            "require_cm": self.require_cm,
            "dry_run": self.dry_run,
            "atomic": self.atomic,
            "budget_ms": self.budget_ms,
            "max_attempts": self.max_attempts,
            "grants": self.grants.to_wire(),
            "ops": [o.to_wire() for o in self.ops],
        }
        if self.label:
            d["label"] = self.label
        if self.origin:
            d["origin"] = self.origin
        if self.requires:
            d["requires"] = dict(self.requires)
        if self.snapshot_to:
            d["snapshot_to"] = self.snapshot_to
        if self.expires_at:
            d["expires_at"] = int(self.expires_at)
        return d


# ---------------------------------------------------------------------------
# Op builders — the only supported way to construct an op.
# ---------------------------------------------------------------------------


def op_ping(op_id: str = "ping", note: str = "") -> Op:
    """The P-2 spike op: proves claim + attribution with zero side effects."""
    return Op("diag.ping", op_id, {"note": note} if note else {})


def op_db_dump(
    op_id: str,
    table: str,
    *,
    fields: Sequence[str] | None = None,
    where: Mapping[str, Any] | None = None,
    limit: int = 0,
    out_path: str = "",
) -> Op:
    body: dict[str, Any] = {"table": table}
    if fields:
        body["fields"] = list(fields)
    if where:
        body["where"] = dict(where)
    if limit:
        body["limit"] = int(limit)
    if out_path:
        body["out_path"] = out_path
    return Op("db.dump", op_id, body)


def op_set_fields(
    op_id: str,
    *,
    table: str = "players",
    key_field: str = "playerid",
    key_value: Any,
    fields: Mapping[str, Any],
    verify: bool = True,
    growth_mirror: Literal["auto", "off", "force"] = "auto",
    on_field_error: Literal["abort", "continue"] = "continue",
    require_found: bool = True,
    upsert: bool = False,
    teamid: int | None = None,
    displace: bool = False,
) -> Op:
    """Write fields to one record, verified by read-back.

    ``growth_mirror='auto'`` mirrors only fields in the core's ``PLAN_FIELDS``
    and only when ``PlayerHasDevelopementPlan(playerid)`` — the fix for
    attribute edits silently reverting in Career Mode.
    """
    if not fields:
        raise JobValidationError("set_fields with no fields")
    body: dict[str, Any] = {
        "table": table,
        "key": {"field": key_field, "value": key_value},
        "fields": dict(fields),
        "verify": verify,
        "growth_mirror": growth_mirror,
        "on_field_error": on_field_error,
        "require_found": require_found,
    }
    if upsert:
        body["upsert"] = True
    if teamid is not None:
        body["teamid"] = int(teamid)
    if displace:
        body["displace"] = True
    return Op("set_fields", op_id, body)


def op_set_names(
    op_id: str,
    playerid: int,
    *,
    firstname: str = "",
    surname: str = "",
    commonname: str = "",
    jerseyname: str = "",
) -> Op:
    """Names are just fields — an ``editedplayernames`` upsert.

    An ``editedplayernames`` row for the playerid wins outright over the four
    nameid lookups. ``upsert`` means: find the row; if absent insert once; if
    more than one matches, update the first and *report* the duplicates as a
    failure rather than inserting a third (v1 inserted three per job, so the
    game resolved the oldest, stalest row forever).
    """
    fields: dict[str, Any] = {}
    if firstname:
        fields["firstname"] = firstname
    if surname:
        fields["surname"] = surname
    if commonname:
        fields["commonname"] = commonname
    if jerseyname:
        fields["playerjerseyname"] = jerseyname
    if not fields:
        raise JobValidationError("set_names with no names")
    return Op(
        "set_fields",
        op_id,
        {
            "table": "editedplayernames",
            "key": {"field": "playerid", "value": int(playerid)},
            "upsert": True,
            "fields": fields,
            "verify": True,
            "growth_mirror": "off",
        },
    )


def op_create_player(
    op_id: str,
    *,
    playerid: int | None = None,
    id_range: tuple[int, int] = (GENERATED_ID_MIN, GENERATED_ID_MAX),
    on_conflict: Literal["fail", "next_free"] = "fail",
    fields: Mapping[str, Any] | None = None,
    names: Mapping[str, str] | None = None,
    generic_head: bool = True,
) -> Op:
    """Create a player. Requires ``grants.allow_create_player`` (SI-5).

    ``generic_head`` forces the always-safe combo
    (``hashighqualityhead=0, headclasscode=1, headassetid=0``); a real face is
    applied afterwards by a ``set_fields`` op on the created id, because
    pointing ``CreatePlayer`` at a real head asset is a known freeze.
    """
    lo, hi = int(id_range[0]), int(id_range[1])
    if hi > PLAYERID_HARD_MAX:
        raise JobValidationError(
            f"id_range upper bound {hi} exceeds hard maximum {PLAYERID_HARD_MAX}"
        )
    if playerid is not None and int(playerid) > PLAYERID_HARD_MAX:
        raise JobValidationError(
            f"playerid {playerid} exceeds hard maximum {PLAYERID_HARD_MAX}"
        )
    body: dict[str, Any] = {
        "id_range": [lo, hi],
        "on_conflict": on_conflict,
        "generic_head": generic_head,
    }
    if playerid is not None:
        body["playerid"] = int(playerid)
    if fields:
        body["fields"] = dict(fields)
    if names:
        body["names"] = dict(names)
    return Op("create_player", op_id, body)


def op_add_to_team(
    op_id: str,
    *,
    teamid: int,
    strategy: Literal["dummy_overwrite"] = "dummy_overwrite",
    dummy_pool: Sequence[int] | None = None,
    dummy_filter: Mapping[str, Any] | None = None,
    player: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    verify: Mapping[str, bool] | None = None,
    batch: Sequence[Mapping[str, Any]] | None = None,
    experimental: bool = False,
) -> Op:
    if not experimental:
        raise JobValidationError(
            "add_to_team is experimental and disabled by default; "
            "pass experimental=True only after enabling the Phase 4 worker toggle"
        )
    if strategy != "dummy_overwrite":
        raise JobValidationError(
            "Add Player cannot create a new Career player. "
            "Use a live-verified free-agent dummy instead."
        )
    body: dict[str, Any] = {"teamid": int(teamid), "strategy": strategy}
    if dummy_pool:
        body["dummy_pool"] = [int(x) for x in dummy_pool]
    if dummy_filter:
        body["dummy_filter"] = dict(dummy_filter)
    if player:
        body["player"] = dict(player)
    if contract:
        body["contract"] = dict(contract)
    if batch:
        body["batch"] = [dict(member) for member in batch]
        first = body["batch"][0]
        # A one-card bag still exposes the legacy single-player keys so older
        # inspectors and Lua v3 resume paths can read the same payload.
        if len(body["batch"]) == 1:
            if not dummy_pool and first.get("dummy_pool"):
                body["dummy_pool"] = [int(x) for x in first["dummy_pool"]]
            if player is None and first.get("player"):
                body["player"] = dict(first["player"])
            if dummy_filter is None and first.get("dummy_filter"):
                body["dummy_filter"] = dict(first["dummy_filter"])
            if contract is None and first.get("contract"):
                body["contract"] = dict(first["contract"])
    body["verify"] = dict(verify or {"name": True, "team": True, "fields": True})
    # Version 4 can sign a whole bag in one job: pick every dummy, write every
    # card, then TransferPlayer all of them before a single membership wait.
    # Version 3 is the single-player FC-visible name gate. An older worker
    # must reject v4 rather than silently applying only the first card.
    return Op("add_to_team", op_id, body, v=4)


def op_repair_partial_add(
    op_id: str,
    *,
    playerid: int,
    teamid: int,
    expected_overallrating: int,
    expected_potential: int,
    potential: int,
    names: Mapping[str, str],
    native_name_ids: Mapping[str, Any] | None = None,
) -> Op:
    """Complete only the identity fields of a provably interrupted Add Player.

    This is intentionally not exposed as a normal player-edit operation.  The
    resident worker re-checks membership and the exact old OVR/POT pair before
    touching a name identity or ``players.potential``.  A complete native
    dictionary identity is preferred: it makes FC resolve the name itself and
    never relies on the fragile edited-name overlay.
    """
    pid, tid = int(playerid), int(teamid)
    overall, old_potential, new_potential = (
        int(expected_overallrating), int(expected_potential), int(potential)
    )
    if pid <= 0 or tid <= 0:
        raise JobValidationError("repair_partial_add needs positive playerid and teamid")
    if overall <= 0 or old_potential < 0 or new_potential < overall:
        raise JobValidationError(
            "repair_partial_add potential must be at least the expected OVR"
        )
    clean_names = {
        key: str(value).strip()
        for key, value in names.items()
        if key in {"firstname", "surname", "commonname", "playerjerseyname", "jerseyname"}
        and str(value).strip()
    }
    if not clean_names:
        raise JobValidationError("repair_partial_add needs at least one name field")
    if "jerseyname" in clean_names and "playerjerseyname" not in clean_names:
        clean_names["playerjerseyname"] = clean_names.pop("jerseyname")
    clean_native_ids: dict[str, int] | None = None
    if native_name_ids is not None:
        identity_fields = (
            "firstnameid",
            "lastnameid",
            "commonnameid",
            "playerjerseynameid",
        )
        clean_native_ids = {}
        for field in identity_fields:
            if field not in native_name_ids:
                raise JobValidationError(
                    "repair_partial_add native_name_ids needs all four FC name IDs"
                )
            try:
                value = int(native_name_ids[field])
            except (TypeError, ValueError) as exc:
                raise JobValidationError(
                    f"repair_partial_add native {field} must be a nonnegative integer"
                ) from exc
            if value < 0:
                raise JobValidationError(
                    f"repair_partial_add native {field} must be a nonnegative integer"
                )
            clean_native_ids[field] = value
        if not any(clean_native_ids.values()):
            raise JobValidationError(
                "repair_partial_add native_name_ids needs at least one non-zero FC name ID"
            )
        if "usercaneditname" not in native_name_ids:
            raise JobValidationError(
                "repair_partial_add native_name_ids needs usercaneditname"
            )
        try:
            editable = int(native_name_ids["usercaneditname"])
        except (TypeError, ValueError) as exc:
            raise JobValidationError(
                "repair_partial_add native usercaneditname must be 0 or 1"
            ) from exc
        if editable not in {0, 1}:
            raise JobValidationError(
                "repair_partial_add native usercaneditname must be 0 or 1"
            )
        clean_native_ids["usercaneditname"] = editable
    body: dict[str, Any] = {
        "playerid": pid,
        "teamid": tid,
        "expected": {
            "overallrating": overall,
            "potential": old_potential,
        },
        "potential": new_potential,
        "names": clean_names,
    }
    if clean_native_ids is not None:
        body["native_name_ids"] = clean_native_ids
    return Op(
        "repair_partial_add",
        op_id,
        body,
        v=2,
    )


def op_transfer(
    op_id: str,
    *,
    action: Literal["transfer", "loan", "release", "terminate_loan", "list_player"],
    playerid: int,
    teamid: int | None = None,
    months: int | None = None,
    fee: int | None = None,
    wage: int | None = None,
    from_teamid: int | None = None,
    release_clause: int | None = None,
    loan_to_buy: int | None = None,
    list_kind: Literal["transfer", "loan", "none"] | None = None,
    clear_presigned: bool = True,
    clear_loan: bool = True,
    verify: bool = True,
) -> Op:
    body: dict[str, Any] = {"action": action, "playerid": int(playerid)}
    if teamid is not None:
        body["to_teamid"] = int(teamid)
    if months is not None:
        body["months"] = int(months)
    if fee is not None:
        body["transfersum"] = int(fee)
    if wage is not None:
        body["wage"] = int(wage)
    if from_teamid is not None:
        body["from_teamid"] = int(from_teamid)
    if release_clause is not None:
        body["release_clause"] = int(release_clause)
    if loan_to_buy is not None:
        body["loan_to_buy"] = int(loan_to_buy)
    if list_kind is not None:
        body["list"] = list_kind
    body["clear_presigned"] = bool(clear_presigned)
    body["clear_loan"] = bool(clear_loan)
    body["verify"] = bool(verify)
    return Op("transfer", op_id, body)


def op_budget(
    op_id: str,
    *,
    action: Literal["get", "set"] = "get",
    transfer: int | None = None,
    wage: int | None = None,
    scope: Literal["user", "cpu"] = "user",
    teamid: int | None = None,
) -> Op:
    """Budget. v1 used ``SetTransferBudget``, deprecated to a no-op stub in
    FC 26 — every budget edit silently did nothing. The core uses
    ``SetUserTransferBudget`` / ``SetCPUTransferBudget``."""
    body: dict[str, Any] = {"action": action, "target": scope}
    if transfer is not None:
        body["amount"] = int(transfer)
    if wage is not None:
        body["wage"] = int(wage)
    if teamid is not None:
        body["teamid"] = int(teamid)
    return Op("budget", op_id, body)


def op_bulk_edit(
    op_id: str,
    *,
    table: str = "players",
    where: Mapping[str, Any] | None = None,
    set_fields: Mapping[str, Any],
    limit: int = 0,
    growth_mirror: Literal["auto", "off", "force"] = "auto",
    scope: Literal["", "user_team", "user_senior_team", "all_players"] | None = None,
    teamid: int | None = None,
) -> Op:
    """Bulk field writes. Empty ``where`` on ``players`` is refused by the core
    unless ``scope='user_team'`` or ``teamid`` is set — never the whole DB.
    """
    body: dict[str, Any] = {
        "table": table,
        "set": dict(set_fields),
        "growth_mirror": growth_mirror,
    }
    if where:
        body["where"] = dict(where)
    if limit:
        body["limit"] = int(limit)
    if scope:
        body["scope"] = scope
    if teamid is not None:
        body["teamid"] = int(teamid)
    return Op("bulk_edit", op_id, body, v=2)


def op_export_squad(
    op_id: str = "squad",
    *,
    teamid: int | None = None,
    out_path: str = "",
    free_agent_candidates: Sequence[int] | None = None,
) -> Op:
    body: dict[str, Any] = {}
    if teamid is not None:
        body["teamid"] = int(teamid)
    if out_path:
        body["out_path"] = out_path
    if free_agent_candidates is not None:
        candidates: list[int] = []
        seen: set[int] = set()
        for raw in free_agent_candidates:
            try:
                playerid = int(raw)
            except (TypeError, ValueError) as exc:
                raise JobValidationError("free-agent candidate id must be numeric") from exc
            if not 0 < playerid <= 459999:
                raise JobValidationError(
                    f"free-agent candidate id {playerid} is outside the real-player range"
                )
            if playerid not in seen:
                candidates.append(playerid)
                seen.add(playerid)
        if len(candidates) > 30:
            raise JobValidationError("export_squad supports at most 30 free-agent candidates")
        body["free_agent_candidates"] = candidates
    return Op("export_squad", op_id, body)


def op_snapshot(
    op_id: str,
    *,
    playerids: Sequence[int],
    table: str = "players",
    fields: Sequence[str] | str = "*",
    out_path: str = "",
) -> Op:
    body: dict[str, Any] = {
        "table": table,
        "playerids": [int(p) for p in playerids],
        "fields": list(fields) if not isinstance(fields, str) else fields,
    }
    if out_path:
        body["out_path"] = out_path
    return Op("snapshot", op_id, body)


def op_growth_sync(
    op_id: str,
    *,
    playerids: Sequence[int] | None = None,
    scope: Literal["ids", "user_team"] = "ids",
    source: Literal["from_players_table"] = "from_players_table",
) -> Op:
    body: dict[str, Any] = {"scope": scope, "source": source}
    if playerids:
        body["playerids"] = [int(p) for p in playerids]
    return Op("growth_sync", op_id, body, v=2)


def op_injury_scan(op_id: str = "scan") -> Op:
    """List injured players on the current Career Mode user senior squad."""
    return Op("injury.scan", op_id, {})


def op_injury_cure(
    op_id: str = "cure",
    *,
    playerids: Sequence[int] | None = None,
) -> Op:
    """Clear injuries for the given player ids, or every injured squad member.

    An empty ``playerids`` list means: scan the current user squad and cure
    every injured player found.
    """
    body: dict[str, Any] = {
        "playerids": [int(p) for p in (playerids or ())],
    }
    return Op("injury.cure", op_id, body)


def op_career_set(
    op_id: str,
    *,
    scope: Literal["user_senior_team", "player"] = "user_senior_team",
    playerid: int | None = None,
    teamid: int | None = None,
    form: int | None = None,
    morale: int | None = None,
    sharpness: int | None = None,
    fitness: int | None = None,
    squad_role: int | None = None,
    release_clause: int | None = None,
) -> Op:
    """Career-manager state. None of these live in the game DB — they are in
    career manager structs reached by pointer chain, which is why they must be
    set inside the game rather than written to a table."""
    body: dict[str, Any] = {"scope": scope}
    if playerid is not None:
        body["playerid"] = int(playerid)
    # A freshly read squad can provide an expected-team guard.  The v2 worker
    # never uses this cache as targeting authority: Career jobs always resolve
    # the active user senior squad inside FC at execution time.
    if teamid is not None:
        body["teamid"] = int(teamid)
    for name, val in (
        ("form", form),
        ("morale", morale),
        ("sharpness", sharpness),
        ("fitness", fitness),
        ("squad_role", squad_role),
        ("release_clause", release_clause),
    ):
        if val is not None:
            body[name] = int(val)
    if len(body) == 1:
        raise JobValidationError("career.set with nothing to set")
    return Op("career.set", op_id, body, v=2)


def op_raw_lua(op_id: str, source: str) -> Op:
    """Developer escape hatch. Requires ``grants.allow_raw_lua``.

    This is the ONE place v2 executes source, it is off by default, and it is
    never used by any UI surface — only the developer console.
    """
    del op_id, source
    raise JobValidationError(
        "raw_lua was removed from protocol v3; use a typed operation"
    )


# ---------------------------------------------------------------------------
# Job builders for the common flows.
# ---------------------------------------------------------------------------


def job_ping(*, label: str = "ping", origin: str = "diag") -> Job:
    return Job(ops=(op_ping(),), label=label, origin=origin, require_cm=False)


def job_apply_player(
    playerid: int,
    fields: Mapping[str, Any],
    *,
    names: Mapping[str, str] | None = None,
    label: str = "",
    origin: str = "ui.cards.apply",
    growth_mirror: Literal["auto", "off", "force"] = "auto",
    snapshot_to: str = "",
    dry_run: bool = False,
    requires: Mapping[str, Any] | None = None,
) -> Job:
    """The most common job in the app: apply a card/preset to one player."""
    ops: list[Op] = [
        op_set_fields(
            "stats",
            key_value=int(playerid),
            fields=fields,
            growth_mirror=growth_mirror,
        )
    ]
    if names:
        ops.append(
            op_set_names(
                "names",
                int(playerid),
                firstname=names.get("firstname", ""),
                surname=names.get("surname", ""),
                commonname=names.get("commonname", ""),
                jerseyname=names.get("playerjerseyname", names.get("jerseyname", "")),
            )
        )
    return Job(
        ops=tuple(ops),
        label=label or f"Apply to {playerid}",
        origin=origin,
        snapshot_to=snapshot_to,
        dry_run=dry_run,
        requires=dict(requires or {}),
    )


def job_from_ops(
    ops: Iterable[Op],
    *,
    label: str = "",
    origin: str = "",
    grants: Grants | None = None,
    **kw: Any,
) -> Job:
    return Job(ops=tuple(ops), label=label, origin=origin, grants=grants or Grants(), **kw)
