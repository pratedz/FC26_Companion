"""Protocol v3 jobs: the declarative op schema and its builders.

A v1 job was **Lua source**. The in-game worker validated a pending entry by
checking it started with something other than ``_`` and ended in ``.lua``, then
``load()``ed and ``pcall()``ed the file. Anything that could append one line to
``queue/_pending.txt`` — another program, a synced folder, a downloaded file —
had arbitrary code execution inside FC 26 with full game-DB write access.

A v3 job is data::

    {"v": 3, "job_id": "01JQ…", "ops": [{"op": "set_fields", "id": "stats", …}]}

The in-game core maps ``"set_fields"`` to a fixed Lua function through a
registry. An unknown op is a clean ``unknown_op`` rejection, not a syntax error
at 3 a.m., and no descriptor can name a function the core did not already ship.
``raw_lua`` remains as an explicit, grant-gated, ``unsafe``-flagged escape hatch
so a UI can warn before it runs.

Everything here is pure: builders return dicts, validation returns a list of
strings. No I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .protocol import (
    PROTOCOL_VERSION,
    is_safe_job_id,
    iso_utc,
    new_job_id,
)

JOB_VERSION = PROTOCOL_VERSION

#: Hard ceiling from the field: ``CreatePlayer`` with playerid >= 500000
#: terminates FC26.exe. Checked at validation time, before anything runs.
MAX_PLAYERID = 499999

#: Ops that cannot be undone by restoring field values, so ``atomic`` (which is
#: implemented as snapshot-then-rollback) is a lie in their presence.
NON_REVERSIBLE_OPS = frozenset(
    {"create_player", "add_to_team", "transfer", "loan", "release", "raw_lua"}
)

#: Deny-by-default capabilities. A job that does not carry the grant cannot
#: reach the op, no matter what its op list says. v1 relied on "the generator
#: would not emit that".
GRANT_KEYS = ("allow_create_player", "allow_delete_row", "allow_memory_patch", "allow_raw_lua")

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_WHERE_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ON_ERROR = frozenset({"abort", "continue"})
_GROWTH_MIRROR = frozenset({"auto", "off", "force"})


class JobValidationError(ValueError):
    """Raised by :func:`validate_job` in ``strict`` mode. Carries every error."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: Tuple[str, ...] = tuple(errors)
        super().__init__("; ".join(errors) if errors else "invalid job")


# ---------------------------------------------------------------------------
# small validators — each returns an error string or ""
# ---------------------------------------------------------------------------


def _err(where: str, msg: str) -> str:
    return f"{where}: {msg}"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_map(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_seq(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (int, float, str)) and not isinstance(value, bool)


def _check_fields_map(where: str, value: Any, *, allow_empty: bool = False) -> List[str]:
    out: List[str] = []
    if not _is_map(value):
        return [_err(where, "must be an object of field -> value")]
    if not value and not allow_empty:
        out.append(_err(where, "must not be empty"))
    for name, val in value.items():
        if not _is_str(name) or not _FIELD_RE.match(name):
            out.append(_err(where, f"bad field name {name!r}"))
        if not _is_scalar(val):
            out.append(_err(where, f"field {name!r} value must be a number or string"))
    return out


def _check_rel_script(where: str, value: Any) -> List[str]:
    """A stock script is named by *relative path under the LE root*, not shipped.

    We never carry the body: the file already exists inside the user's Live
    Editor install, so a job cannot smuggle source through this op.
    """
    if not _is_str(value) or not value.strip():
        return [_err(where, "must be a relative path to a .lua script under the LE root")]
    raw = value.replace("\\", "/").strip()
    problems: List[str] = []
    if not raw.lower().endswith(".lua"):
        problems.append(_err(where, "must end in .lua"))
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        problems.append(_err(where, "must be relative, not absolute"))
    if ".." in raw.split("/"):
        problems.append(_err(where, "must not contain '..'"))
    if any(part in ("", ".") for part in raw.split("/")):
        problems.append(_err(where, "must not contain empty or '.' path parts"))
    return problems


def _check_out_path(where: str, value: Any, root: Optional[Path]) -> List[str]:
    """An op that writes a file must name an absolute path under an allowed root."""
    if not _is_str(value) or not value.strip():
        return [_err(where, "must be an absolute output path")]
    candidate = Path(value)
    if not candidate.is_absolute():
        return [_err(where, "must be absolute")]
    if ".." in candidate.parts:
        return [_err(where, "must not contain '..'")]
    if root is None:
        return []
    try:
        candidate.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return [_err(where, f"must stay under {root}")]
    return []


# ---------------------------------------------------------------------------
# op registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpSpec:
    """One op kind: what it requires, what it allows, what it costs."""

    kind: str
    version: int
    required: Tuple[str, ...]
    optional: Mapping[str, Any]
    check: Optional[Callable[[str, Mapping[str, Any], Optional[Path]], List[str]]] = None
    grants: Tuple[str, ...] = ()
    unsafe: bool = False
    summary: str = ""

    @property
    def keys(self) -> frozenset:
        return frozenset(self.required) | frozenset(self.optional)


def _check_set_fields(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    key = op.get("key")
    if not _is_map(key) or not _is_str(key.get("field")) or not _is_scalar(key.get("value")):
        out.append(_err(where, "key must be {'field': str, 'value': int|str}"))
    out += _check_fields_map(f"{where}.fields", op.get("fields"))
    mirror = op.get("growth_mirror", "auto")
    if mirror not in _GROWTH_MIRROR:
        out.append(_err(where, f"growth_mirror must be one of {sorted(_GROWTH_MIRROR)}"))
    if op.get("on_field_error", "continue") not in _ON_ERROR:
        out.append(_err(where, "on_field_error must be 'abort' or 'continue'"))
    return out


def _check_create_player(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    pid = op.get("playerid")
    if not _is_int(pid) or pid <= 0:
        out.append(_err(where, "playerid must be a positive integer"))
    elif pid > MAX_PLAYERID:
        out.append(
            _err(where, f"playerid {pid} exceeds hard maximum {MAX_PLAYERID} (CreatePlayer kills FC26.exe)")
        )
    span = op.get("id_range")
    if span is not None:
        if not _is_seq(span) or len(span) != 2 or not all(_is_int(x) for x in span):
            out.append(_err(where, "id_range must be [lo, hi] integers"))
        elif span[0] > span[1] or span[1] > MAX_PLAYERID:
            out.append(_err(where, f"id_range must be ascending and end <= {MAX_PLAYERID}"))
    if op.get("on_conflict", "fail") not in ("fail", "next_free"):
        out.append(_err(where, "on_conflict must be 'fail' or 'next_free'"))
    out += _check_fields_map(f"{where}.fields", op.get("fields", {}), allow_empty=True)
    out += _check_fields_map(f"{where}.names", op.get("names", {}), allow_empty=True)
    return out


def _check_add_to_team(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    if not _is_int(op.get("teamid")):
        out.append(_err(where, "teamid must be an integer"))
    if op.get("strategy", "dummy_overwrite") not in ("dummy_overwrite", "create"):
        out.append(_err(where, "strategy must be 'dummy_overwrite' or 'create'"))
    player = op.get("player")
    if not _is_map(player):
        out.append(_err(where, "player must be an object"))
    else:
        out += _check_fields_map(f"{where}.player.fields", player.get("fields", {}), allow_empty=True)
        out += _check_fields_map(f"{where}.player.names", player.get("names", {}), allow_empty=True)
        out += _check_fields_map(f"{where}.player.face", player.get("face", {}), allow_empty=True)
    pool = op.get("dummy_pool")
    if pool is not None and (not _is_seq(pool) or not all(_is_int(x) for x in pool)):
        out.append(_err(where, "dummy_pool must be a list of integers"))
    return out


def _check_transfer(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    if not _is_int(op.get("playerid")):
        out.append(_err(where, "playerid must be an integer"))
    if not _is_int(op.get("to_teamid")):
        out.append(_err(where, "to_teamid must be an integer"))
    return out


def _check_loan(where: str, op: Mapping[str, Any], root: Optional[Path]) -> List[str]:
    out = _check_transfer(where, op, root)
    months = op.get("months", 12)
    if not _is_int(months) or months <= 0:
        out.append(_err(where, "months must be a positive integer"))
    return out


def _check_release(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    if not _is_int(op.get("playerid")):
        return [_err(where, "playerid must be an integer")]
    return []


def _check_set_budget(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    if op.get("target") not in ("user", "cpu"):
        out.append(_err(where, "target must be 'user' or 'cpu'"))
    action = op.get("action", "set")
    if action not in ("set", "get"):
        out.append(_err(where, "action must be 'set' or 'get'"))
    if action == "set" and not _is_int(op.get("amount")):
        out.append(_err(where, "action 'set' requires an integer amount"))
    return out


def _check_bulk_edit(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out = _check_fields_map(f"{where}.set", op.get("set"))
    conds = op.get("where", [])
    if not _is_seq(conds):
        out.append(_err(where, "where must be a list of conditions"))
    else:
        for idx, cond in enumerate(conds):
            spot = f"{where}.where[{idx}]"
            if not _is_map(cond):
                out.append(_err(spot, "must be an object"))
                continue
            if not _is_str(cond.get("field")) or not _FIELD_RE.match(str(cond.get("field"))):
                out.append(_err(spot, "bad field name"))
            if cond.get("op") not in _WHERE_OPS:
                out.append(_err(spot, f"op must be one of {sorted(_WHERE_OPS)}"))
            if not _is_scalar(cond.get("value")):
                out.append(_err(spot, "value must be a number or string"))
    scope = op.get("scope", {})
    if not _is_map(scope):
        out.append(_err(f"{where}.scope", "must be an object"))
    else:
        extra = set(scope) - {"team", "playerids"}
        if extra:
            out.append(_err(f"{where}.scope", f"unknown keys {sorted(extra)}"))
        ids = scope.get("playerids")
        if ids is not None and (not _is_seq(ids) or not all(_is_int(x) for x in ids)):
            out.append(_err(f"{where}.scope.playerids", "must be a list of integers"))
    limit = op.get("limit", 2000)
    if not _is_int(limit) or limit <= 0:
        out.append(_err(where, "limit must be a positive integer"))
    chunk = op.get("chunk", 400)
    if not _is_int(chunk) or not 1 <= chunk <= 5000:
        out.append(_err(where, "chunk must be between 1 and 5000"))
    if op.get("growth_mirror", "auto") not in _GROWTH_MIRROR:
        out.append(_err(where, f"growth_mirror must be one of {sorted(_GROWTH_MIRROR)}"))
    return out


def _check_export_squad(where: str, op: Mapping[str, Any], root: Optional[Path]) -> List[str]:
    out = _check_out_path(f"{where}.out", op.get("out"), root)
    include = op.get("include", [])
    if not _is_seq(include):
        out.append(_err(f"{where}.include", "must be a list"))
    else:
        allowed = {"squad", "jersey_numbers", "free_agents"}
        bad = [x for x in include if x not in allowed]
        if bad:
            out.append(_err(f"{where}.include", f"unknown entries {bad}"))
    return out


def _check_snapshot(where: str, op: Mapping[str, Any], root: Optional[Path]) -> List[str]:
    out = _check_out_path(f"{where}.out", op.get("out"), root)
    targets = op.get("targets")
    if not _is_seq(targets) or not targets or not all(_is_int(x) for x in targets):
        out.append(_err(f"{where}.targets", "must be a non-empty list of integers"))
    fields = op.get("fields", "*")
    if fields != "*" and (not _is_seq(fields) or not all(_is_str(x) for x in fields)):
        out.append(_err(f"{where}.fields", "must be '*' or a list of field names"))
    return out


def _check_growth_sync(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    if not _is_int(op.get("playerid")):
        out.append(_err(where, "playerid must be an integer"))
    mode = op.get("mode")
    if mode not in ("from_players_table", "mirror", "clear"):
        out.append(_err(where, "mode must be 'from_players_table', 'mirror' or 'clear'"))
    if mode == "mirror":
        out += _check_fields_map(f"{where}.fields", op.get("fields"))
    return out


def _check_run_stock_script(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    return _check_rel_script(f"{where}.script", op.get("script"))


def _check_raw_lua(where: str, op: Mapping[str, Any], _root: Optional[Path]) -> List[str]:
    out: List[str] = []
    if not _is_str(op.get("source")) or not str(op.get("source")).strip():
        out.append(_err(where, "source must be non-empty Lua"))
    if not _is_str(op.get("reason")) or not str(op.get("reason")).strip():
        out.append(_err(where, "reason is required — say why source is being shipped"))
    timeout = op.get("timeout_ms", 3000)
    if not _is_int(timeout) or timeout <= 0:
        out.append(_err(where, "timeout_ms must be a positive integer"))
    return out


OP_SPECS: Tuple[OpSpec, ...] = (
    OpSpec(
        kind="set_fields",
        version=1,
        required=("key", "fields"),
        optional={
            "table": "players",
            "verify": True,
            "growth_mirror": "auto",
            "on_field_error": "continue",
            "require_found": True,
            "upsert": False,
        },
        check=_check_set_fields,
        summary="Write fields onto one row, verified by read-back.",
    ),
    OpSpec(
        kind="create_player",
        version=1,
        required=("playerid",),
        optional={
            "id_range": None,
            "on_conflict": "fail",
            "fields": {},
            "names": {},
            "generic_head": True,
        },
        check=_check_create_player,
        grants=("allow_create_player",),
        summary="Create a new player row. Hard playerid ceiling.",
    ),
    OpSpec(
        kind="add_to_team",
        version=1,
        required=("teamid", "player"),
        optional={
            "strategy": "dummy_overwrite",
            "dummy_pool": None,
            "dummy_filter": {},
            "contract": {},
            "verify": {"name": True, "team": True, "fields": True},
        },
        check=_check_add_to_team,
        summary="Place a player in a team (dummy overwrite by default).",
    ),
    OpSpec(
        kind="transfer",
        version=1,
        required=("playerid", "to_teamid"),
        optional={
            "from_teamid": 0,
            "transfersum": 0,
            "wage": 0,
            "months": 60,
            "release_clause": -1,
            "clear_presigned": True,
            "clear_loan": True,
            "verify": True,
        },
        check=_check_transfer,
        summary="Move a player between teams; verified by GetTeamIdFromPlayerId.",
    ),
    OpSpec(
        kind="loan",
        version=1,
        required=("playerid", "to_teamid"),
        optional={
            "months": 12,
            "loan_to_buy": -1,
            "from_teamid": 0,
            "clear_presigned": True,
            "verify": True,
        },
        check=_check_loan,
        summary="Loan a player out; pre-clears presigned/loan state.",
    ),
    OpSpec(
        kind="release",
        version=1,
        required=("playerid",),
        optional={"verify": True},
        check=_check_release,
        summary="Release a player to free agency.",
    ),
    OpSpec(
        kind="set_budget",
        version=1,
        required=("target",),
        optional={"action": "set", "amount": None, "teamid": None, "verify": True},
        check=_check_set_budget,
        summary="Get/set the user or CPU transfer budget (the non-stub API).",
    ),
    OpSpec(
        kind="bulk_edit",
        version=1,
        required=("table", "set"),
        optional={
            "where": [],
            "scope": {},
            "limit": 2000,
            "chunk": 400,
            "growth_mirror": "auto",
        },
        check=_check_bulk_edit,
        summary="Conjunctive filter + write across many rows, chunked.",
    ),
    OpSpec(
        kind="export_squad",
        version=1,
        required=("out",),
        optional={"include": ["squad"], "fields": [], "free_agents": {}},
        check=_check_export_squad,
        summary="Write the user squad to a JSON file under an allowed root.",
    ),
    OpSpec(
        kind="snapshot_capture",
        version=1,
        required=("out", "targets"),
        optional={"table": "players", "fields": "*", "include_growth": True},
        check=_check_snapshot,
        summary="Capture current values before a write. The undo primitive.",
    ),
    OpSpec(
        kind="growth_sync",
        version=1,
        required=("playerid", "mode"),
        optional={"fields": {}, "only_if_has_plan": True},
        check=_check_growth_sync,
        summary="Mirror attributes into the development plan so edits stop reverting.",
    ),
    OpSpec(
        kind="run_stock_script",
        version=1,
        required=("script",),
        optional={"args": {}, "timeout_ms": 10000},
        check=_check_run_stock_script,
        summary="Run a script that already ships inside the LE install, by path.",
    ),
    OpSpec(
        kind="raw_lua",
        version=1,
        required=("source", "reason"),
        optional={"timeout_ms": 3000, "label": ""},
        check=_check_raw_lua,
        grants=("allow_raw_lua",),
        unsafe=True,
        summary="ESCAPE HATCH: ship Lua source. Grant-gated and flagged unsafe.",
    ),
)

OP_KINDS: Mapping[str, OpSpec] = {spec.kind: spec for spec in OP_SPECS}

#: Keys every op may carry regardless of kind. Anything else (bar ``x_*``) is a
#: rejection — silently ignoring a misspelled key is how a job half-executes.
COMMON_OP_KEYS = frozenset({"op", "id", "v", "on_error"})

#: Envelope keys. Same rule.
ENVELOPE_KEYS = frozenset(
    {
        "v",
        "job_id",
        "created_utc",
        "label",
        "origin",
        "core_min",
        "require_cm",
        "atomic",
        "dry_run",
        "budget_ms",
        "max_attempts",
        "snapshot_to",
        "requires",
        "grants",
        "ops",
    }
)


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Job:
    """One job = one file = one result file.

    ``ops`` stays a tuple of plain dicts on purpose: the descriptor is the
    contract, it is what lands on disk, and ``job["ops"][0]["fields"]["ovr"]``
    is directly assertable in a test. Builders below produce them.
    """

    job_id: str
    ops: Tuple[Mapping[str, Any], ...]
    label: str = ""
    origin: str = ""
    created: str = ""
    core_min: str = "3.0.0"
    require_cm: bool = True
    atomic: bool = False
    dry_run: bool = False
    budget_ms: int = 200
    max_attempts: int = 3
    snapshot_to: str = ""
    requires: Mapping[str, Any] = field(default_factory=dict)
    grants: Mapping[str, Any] = field(default_factory=dict)
    v: int = JOB_VERSION

    @property
    def unsafe(self) -> bool:
        """True when the job ships Lua source. A UI must warn on this."""
        return any(OP_KINDS.get(str(op.get("op")), None) is not None
                   and OP_KINDS[str(op.get("op"))].unsafe for op in self.ops)

    @property
    def op_kinds(self) -> Tuple[str, ...]:
        return tuple(str(op.get("op") or "") for op in self.ops)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": int(self.v),
            "job_id": self.job_id,
            "created_utc": self.created or iso_utc(),
            "label": self.label,
            "origin": self.origin,
            "core_min": self.core_min,
            "require_cm": bool(self.require_cm),
            "atomic": bool(self.atomic),
            "dry_run": bool(self.dry_run),
            "budget_ms": int(self.budget_ms),
            "max_attempts": int(self.max_attempts),
            "snapshot_to": self.snapshot_to,
            "requires": dict(self.requires),
            "grants": dict(self.grants),
            "ops": [dict(op) for op in self.ops],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Job":
        raw_ops = data.get("ops")
        ops: Tuple[Mapping[str, Any], ...] = ()
        if _is_seq(raw_ops):
            ops = tuple(dict(op) for op in raw_ops if _is_map(op))
        requires = data.get("requires")
        grants = data.get("grants")
        return cls(
            job_id=str(data.get("job_id") or ""),
            ops=ops,
            label=str(data.get("label") or ""),
            origin=str(data.get("origin") or ""),
            created=str(data.get("created_utc") or ""),
            core_min=str(data.get("core_min") or "3.0.0"),
            require_cm=bool(data.get("require_cm", True)),
            atomic=bool(data.get("atomic", False)),
            dry_run=bool(data.get("dry_run", False)),
            budget_ms=int(data.get("budget_ms") or 200),
            max_attempts=int(data.get("max_attempts") or 3),
            snapshot_to=str(data.get("snapshot_to") or ""),
            requires=dict(requires) if _is_map(requires) else {},
            grants=dict(grants) if _is_map(grants) else {},
            v=int(data.get("v") or JOB_VERSION),
        )


def new_job(
    ops: Iterable[Mapping[str, Any]],
    *,
    job_id: Optional[str] = None,
    prefix: str = "",
    label: str = "",
    origin: str = "",
    grants: Optional[Mapping[str, Any]] = None,
    requires: Optional[Mapping[str, Any]] = None,
    require_cm: bool = True,
    atomic: bool = False,
    dry_run: bool = False,
    budget_ms: int = 200,
    max_attempts: int = 3,
    snapshot_to: str = "",
    core_min: str = "3.0.0",
) -> Job:
    """Build a :class:`Job`, assigning a ULID job id and default op ids."""
    materialised: List[Mapping[str, Any]] = []
    for index, op in enumerate(ops):
        item = dict(op)
        if not item.get("id"):
            item["id"] = f"{item.get('op', 'op')}_{index}"
        materialised.append(item)
    return Job(
        job_id=job_id or new_job_id(prefix),
        ops=tuple(materialised),
        label=label,
        origin=origin,
        created=iso_utc(),
        core_min=core_min,
        require_cm=require_cm,
        atomic=atomic,
        dry_run=dry_run,
        budget_ms=budget_ms,
        max_attempts=max_attempts,
        snapshot_to=snapshot_to,
        requires=dict(requires or {}),
        grants=dict(grants or {}),
    )


def grants_for(ops: Iterable[Mapping[str, Any]]) -> Dict[str, bool]:
    """The minimum grant set an op list needs. Explicit opt-in, computed once."""
    needed: Dict[str, bool] = {}
    for op in ops:
        spec = OP_KINDS.get(str(op.get("op") or ""))
        if spec is None:
            continue
        for grant in spec.grants:
            needed[grant] = True
        if spec.kind == "add_to_team" and op.get("strategy") == "create":
            needed["allow_create_player"] = True
    return needed


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_op(
    op: Any,
    *,
    where: str = "ops[0]",
    grants: Optional[Mapping[str, Any]] = None,
    output_root: Optional[Union[str, Path]] = None,
) -> List[str]:
    """Validate one op against its spec. Returns every error, not just the first."""
    if not _is_map(op):
        return [_err(where, "must be an object")]
    kind = op.get("op")
    if not _is_str(kind):
        return [_err(where, "missing 'op'")]
    spec = OP_KINDS.get(kind)
    if spec is None:
        return [_err(where, f"unknown op {kind!r} (known: {', '.join(sorted(OP_KINDS))})")]

    out: List[str] = []
    op_id = op.get("id")
    if op_id is not None and (not _is_str(op_id) or not _ID_RE.match(op_id)):
        out.append(_err(where, f"bad op id {op_id!r}"))
    version = op.get("v", 1)
    if not _is_int(version) or version < 1:
        out.append(_err(where, "op 'v' must be a positive integer"))
    elif version > spec.version:
        out.append(_err(where, f"op {kind} v{version} > supported v{spec.version}"))
    if op.get("on_error", "abort") not in _ON_ERROR:
        out.append(_err(where, "on_error must be 'abort' or 'continue'"))

    allowed = spec.keys | COMMON_OP_KEYS
    unknown = sorted(
        key for key in op if key not in allowed and not str(key).startswith("x_")
    )
    if unknown:
        out.append(_err(where, f"unknown keys {unknown} for op {kind}"))
    missing = sorted(key for key in spec.required if key not in op)
    if missing:
        out.append(_err(where, f"missing required keys {missing} for op {kind}"))

    grant_map = grants or {}
    for grant in spec.grants:
        if not bool(grant_map.get(grant)):
            out.append(_err(where, f"op {kind} requires grant {grant!r}"))
    if spec.kind == "add_to_team" and op.get("strategy") == "create" and not bool(
        grant_map.get("allow_create_player")
    ):
        out.append(_err(where, "add_to_team strategy 'create' requires grant 'allow_create_player'"))

    if spec.check is not None and not missing:
        root = Path(output_root) if output_root is not None else None
        out += spec.check(where, op, root)
    return out


def validate_job(
    job: Union[Job, Mapping[str, Any]],
    *,
    output_root: Optional[Union[str, Path]] = None,
    strict: bool = False,
) -> List[str]:
    """Validate a whole job. Returns a list of errors ([] means valid).

    ``output_root`` confines every op that writes a file (``export_squad``,
    ``snapshot_capture``). Pass ``strict=True`` to raise
    :class:`JobValidationError` instead of returning.
    """
    data = job.to_dict() if isinstance(job, Job) else job
    errors: List[str] = []
    if not _is_map(data):
        errors = ["job must be an object"]
        if strict:
            raise JobValidationError(errors)
        return errors

    version = data.get("v")
    if version != JOB_VERSION:
        errors.append(_err("job", f"unsupported protocol version {version!r} (want {JOB_VERSION})"))
    if not is_safe_job_id(data.get("job_id")):
        errors.append(_err("job", f"unsafe or missing job_id {data.get('job_id')!r}"))

    unknown = sorted(
        key for key in data if key not in ENVELOPE_KEYS and not str(key).startswith("x_")
    )
    if unknown:
        errors.append(_err("job", f"unknown envelope keys {unknown}"))

    for key in ("require_cm", "atomic", "dry_run"):
        if key in data and not _is_bool(data[key]):
            errors.append(_err("job", f"{key} must be a boolean"))
    for key in ("budget_ms", "max_attempts"):
        if key in data and (not _is_int(data[key]) or data[key] <= 0):
            errors.append(_err("job", f"{key} must be a positive integer"))
    grants = data.get("grants", {})
    if not _is_map(grants):
        errors.append(_err("job", "grants must be an object"))
        grants = {}
    else:
        bad = sorted(key for key in grants if key not in GRANT_KEYS)
        if bad:
            errors.append(_err("job", f"unknown grants {bad}"))
    if "requires" in data and not _is_map(data["requires"]):
        errors.append(_err("job", "requires must be an object"))
    if data.get("snapshot_to"):
        errors += _check_out_path(
            "job.snapshot_to",
            data["snapshot_to"],
            Path(output_root) if output_root is not None else None,
        )

    ops = data.get("ops")
    if not _is_seq(ops) or not ops:
        errors.append(_err("job", "ops must be a non-empty list"))
        ops = []

    seen: Dict[str, int] = {}
    for index, op in enumerate(ops):
        where = f"ops[{index}]"
        errors += validate_op(op, where=where, grants=grants, output_root=output_root)
        if _is_map(op):
            op_id = op.get("id")
            if _is_str(op_id):
                if op_id in seen:
                    errors.append(
                        _err(where, f"duplicate op id {op_id!r} (also ops[{seen[op_id]}])")
                    )
                else:
                    seen[op_id] = index

    if data.get("atomic"):
        offenders = sorted(
            {str(op.get("op")) for op in ops if _is_map(op) and str(op.get("op")) in NON_REVERSIBLE_OPS}
        )
        if offenders:
            errors.append(
                _err("job", f"atomic=true is not possible with non-reversible ops {offenders}")
            )

    if strict and errors:
        raise JobValidationError(errors)
    return errors


def is_valid(job: Union[Job, Mapping[str, Any]], **kwargs: Any) -> bool:
    return not validate_job(job, **kwargs)


# ---------------------------------------------------------------------------
# op builders — the only place ops are constructed
# ---------------------------------------------------------------------------


def _op(kind: str, op_id: str, **kwargs: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"op": kind, "id": op_id}
    for key, value in kwargs.items():
        if value is not None:
            out[key] = value
    return out


def set_fields(
    op_id: str,
    *,
    playerid: Optional[int] = None,
    fields: Mapping[str, Any],
    table: str = "players",
    key: Optional[Mapping[str, Any]] = None,
    verify: bool = True,
    growth_mirror: str = "auto",
    on_field_error: str = "continue",
    require_found: bool = True,
    upsert: bool = False,
) -> Dict[str, Any]:
    """Write fields onto one row. ``playerid=`` is sugar for the usual key."""
    if key is None:
        if playerid is None:
            raise ValueError("set_fields needs playerid= or key=")
        key = {"field": "playerid", "value": int(playerid)}
    return _op(
        "set_fields",
        op_id,
        table=table,
        key=dict(key),
        fields=dict(fields),
        verify=verify,
        growth_mirror=growth_mirror,
        on_field_error=on_field_error,
        require_found=require_found,
        upsert=upsert,
    )


def create_player(
    op_id: str,
    *,
    playerid: int,
    fields: Optional[Mapping[str, Any]] = None,
    names: Optional[Mapping[str, Any]] = None,
    id_range: Optional[Sequence[int]] = None,
    on_conflict: str = "fail",
    generic_head: bool = True,
) -> Dict[str, Any]:
    return _op(
        "create_player",
        op_id,
        playerid=int(playerid),
        fields=dict(fields or {}),
        names=dict(names or {}),
        id_range=list(id_range) if id_range is not None else None,
        on_conflict=on_conflict,
        generic_head=generic_head,
    )


def add_to_team(
    op_id: str,
    *,
    teamid: int,
    player: Mapping[str, Any],
    strategy: str = "dummy_overwrite",
    dummy_pool: Optional[Sequence[int]] = None,
    dummy_filter: Optional[Mapping[str, Any]] = None,
    contract: Optional[Mapping[str, Any]] = None,
    verify: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return _op(
        "add_to_team",
        op_id,
        teamid=int(teamid),
        player=dict(player),
        strategy=strategy,
        dummy_pool=list(dummy_pool) if dummy_pool is not None else None,
        dummy_filter=dict(dummy_filter or {}),
        contract=dict(contract or {}),
        verify=dict(verify or {"name": True, "team": True, "fields": True}),
    )


def transfer(
    op_id: str,
    *,
    playerid: int,
    to_teamid: int,
    from_teamid: int = 0,
    transfersum: int = 0,
    wage: int = 0,
    months: int = 60,
    release_clause: int = -1,
    clear_presigned: bool = True,
    clear_loan: bool = True,
    verify: bool = True,
) -> Dict[str, Any]:
    return _op(
        "transfer",
        op_id,
        playerid=int(playerid),
        to_teamid=int(to_teamid),
        from_teamid=int(from_teamid),
        transfersum=int(transfersum),
        wage=int(wage),
        months=int(months),
        release_clause=int(release_clause),
        clear_presigned=clear_presigned,
        clear_loan=clear_loan,
        verify=verify,
    )


def loan(
    op_id: str,
    *,
    playerid: int,
    to_teamid: int,
    months: int = 12,
    loan_to_buy: int = -1,
    from_teamid: int = 0,
    clear_presigned: bool = True,
    verify: bool = True,
) -> Dict[str, Any]:
    return _op(
        "loan",
        op_id,
        playerid=int(playerid),
        to_teamid=int(to_teamid),
        months=int(months),
        loan_to_buy=int(loan_to_buy),
        from_teamid=int(from_teamid),
        clear_presigned=clear_presigned,
        verify=verify,
    )


def release(op_id: str, *, playerid: int, verify: bool = True) -> Dict[str, Any]:
    return _op("release", op_id, playerid=int(playerid), verify=verify)


def set_budget(
    op_id: str,
    *,
    target: str = "user",
    action: str = "set",
    amount: Optional[int] = None,
    teamid: Optional[int] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    return _op(
        "set_budget",
        op_id,
        target=target,
        action=action,
        amount=int(amount) if amount is not None else None,
        teamid=int(teamid) if teamid is not None else None,
        verify=verify,
    )


def bulk_edit(
    op_id: str,
    *,
    table: str = "players",
    set_values: Mapping[str, Any],
    where: Optional[Sequence[Mapping[str, Any]]] = None,
    scope: Optional[Mapping[str, Any]] = None,
    limit: int = 2000,
    chunk: int = 400,
    growth_mirror: str = "auto",
) -> Dict[str, Any]:
    out = _op(
        "bulk_edit",
        op_id,
        table=table,
        where=[dict(cond) for cond in (where or [])],
        scope=dict(scope or {}),
        limit=int(limit),
        chunk=int(chunk),
        growth_mirror=growth_mirror,
    )
    out["set"] = dict(set_values)
    return out


def export_squad(
    op_id: str,
    *,
    out: Union[str, Path],
    include: Optional[Sequence[str]] = None,
    fields: Optional[Sequence[str]] = None,
    free_agents: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return _op(
        "export_squad",
        op_id,
        out=str(out),
        include=list(include or ["squad"]),
        fields=list(fields or []),
        free_agents=dict(free_agents or {}),
    )


def snapshot_capture(
    op_id: str,
    *,
    out: Union[str, Path],
    targets: Sequence[int],
    table: str = "players",
    fields: Union[str, Sequence[str]] = "*",
    include_growth: bool = True,
) -> Dict[str, Any]:
    return _op(
        "snapshot_capture",
        op_id,
        out=str(out),
        targets=[int(t) for t in targets],
        table=table,
        fields=fields if fields == "*" else list(fields),
        include_growth=include_growth,
    )


def growth_sync(
    op_id: str,
    *,
    playerid: int,
    mode: str = "from_players_table",
    fields: Optional[Mapping[str, Any]] = None,
    only_if_has_plan: bool = True,
) -> Dict[str, Any]:
    return _op(
        "growth_sync",
        op_id,
        playerid=int(playerid),
        mode=mode,
        fields=dict(fields or {}),
        only_if_has_plan=only_if_has_plan,
    )


def run_stock_script(
    op_id: str,
    *,
    script: str,
    args: Optional[Mapping[str, Any]] = None,
    timeout_ms: int = 10000,
) -> Dict[str, Any]:
    """Run a script that already ships inside the LE install, named by path.

    v1 read the file and shipped its **contents** through the queue; here only
    the relative path travels, so the bytes that run are the bytes the user
    installed.
    """
    return _op(
        "run_stock_script",
        op_id,
        script=str(script).replace("\\", "/"),
        args=dict(args or {}),
        timeout_ms=int(timeout_ms),
    )


def raw_lua(
    op_id: str,
    *,
    source: str,
    reason: str,
    timeout_ms: int = 3000,
    label: str = "",
) -> Dict[str, Any]:
    """The explicit escape hatch. Needs ``allow_raw_lua``; flags the job unsafe."""
    return _op(
        "raw_lua",
        op_id,
        source=str(source),
        reason=str(reason),
        timeout_ms=int(timeout_ms),
        label=label or None,
    )
