"""Career-mode growth (XP) mirroring — the "make edits stick" layer.

Writing an attribute into the ``players`` DB table is **not** enough in Career
Mode. ``PlayerGrowthManager`` holds its own XP-based copy of every growable
attribute for players in a user-managed team, and it rewrites the DB row from
that XP after a match, a training session or a save/load. The user's edit
silently reverts.

The fix (proven by xAranaktu's FC 25 Cheat Table) is to mirror every attribute
write into the growth system by converting the target attribute value into the
equivalent *cumulative* XP total and writing that too.

FC 26 Live Editor exposes this natively — no memory patching required::

    bool PlayerHasDevelopementPlan(int playerid)
    void PlayerSetValueInDevelopementPlan(int playerid, string field_name, int value)

(LE spells it "Developement". Both are Career-Mode-only; ``field_name`` uses
``players`` table field names and ``value`` is XP points, not the attribute.)

Curve data is lifted verbatim from ``FC 25 CT v25.1.6/lua/consts.lua``
(``PlayerGrowthManager_Data``): ``xp_to_attribute`` (99 cumulative entries) and
``xp_to_star`` (5 entries). ``fields_ordered_array`` is the growth system's
memory layout; here it only serves as the authoritative *set* of growable
fields, because LE takes field names and we never touch offsets.

Two deliberate deviations from the Cheat Table:

* ``skillmoves`` is stored 0-indexed in the DB (0 = 1 star … 4 = 5 stars) while
  ``weakfootabilitytypecode`` is 1-indexed (1 = 1 star … 5 = 5 stars); verified
  against ``player_presets/base_players.csv``. The CT clamps both to 1..5 and
  indexes ``xp_to_star`` directly, which under-sets skill moves by one star.
  :func:`field_value_to_stars` converts each field correctly.
* :func:`xp_to_attribute` is a true inverse of :func:`attribute_to_xp`. The CT's
  ``xp_to_attr`` returns ``i-1`` for the first threshold ``>= xp``, so it maps a
  freshly written total back to ``value - 1``; that off-by-one would make any
  read-back/verify pass look wrong.

Nothing here talks to the game directly — it produces Lua for the LE bridge.
"""

from __future__ import annotations

import bisect
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from . import paths


# ── Curve data (FC 25 CT consts.lua :: PlayerGrowthManager_Data) ──────

#: Growth-system field order (also the memory layout the CT walks). The first
#: 34 are attributes; the last two are the star fields.
GROWTH_FIELDS_ORDERED: Tuple[str, ...] = (
    "acceleration",
    "sprintspeed",
    "agility",
    "balance",
    "jumping",
    "stamina",
    "strength",
    "reactions",
    "aggression",
    "composure",
    "interceptions",
    "positioning",
    "vision",
    "ballcontrol",
    "crossing",
    "dribbling",
    "finishing",
    "freekickaccuracy",
    "headingaccuracy",
    "longpassing",
    "shortpassing",
    "defensiveawareness",
    "shotpower",
    "longshots",
    "standingtackle",
    "slidingtackle",
    "volleys",
    "curve",
    "penalties",
    "gkdiving",
    "gkhandling",
    "gkkicking",
    "gkreflexes",
    "gkpositioning",
    "weakfootabilitytypecode",
    "skillmoves",
)

#: Star-rated growth fields (converted through :data:`XP_TO_STAR`).
STAR_FIELDS: Tuple[str, ...] = ("weakfootabilitytypecode", "skillmoves")
#: Stored 0-indexed in the players table: 0 = 1 star … 4 = 5 stars.
ZERO_INDEXED_STAR_FIELDS: Tuple[str, ...] = ("skillmoves",)
#: Stored 1-indexed in the players table: 1 = 1 star … 5 = 5 stars.
ONE_INDEXED_STAR_FIELDS: Tuple[str, ...] = ("weakfootabilitytypecode",)

#: The 34 1..99 attributes tracked by the growth system.
GROWABLE_ATTRIBUTE_FIELDS: Tuple[str, ...] = tuple(
    f for f in GROWTH_FIELDS_ORDERED if f not in STAR_FIELDS
)

#: Every players-table field the growth system owns.
GROWABLE_FIELDS: frozenset = frozenset(GROWTH_FIELDS_ORDERED)

#: Cumulative XP required for attribute value 1..99 (index 0 == value 1).
XP_TO_ATTRIBUTE: Tuple[int, ...] = (
    1000, 2101, 3202, 4305, 5410, 6518, 7628, 8742, 9860, 10983,
    12110, 13243, 14382, 15528, 16680, 17840, 19008, 20185, 21370, 22565,
    23770, 24986, 26212, 27450, 28700, 29963, 31238, 32527, 33830, 35148,
    36480, 37828, 39192, 40573, 41970, 43385, 44818, 46270, 47740, 49230,
    50740, 52271, 53822, 55395, 56990, 58608, 60248, 61912, 63600, 65313,
    67050, 68813, 70602, 72418, 74260, 76130, 78028, 79955, 81910, 83895,
    85910, 87956, 90032, 92140, 94280, 96453, 98658, 100897, 103170, 105478,
    107820, 110198, 112612, 115063, 117550, 120075, 122638, 125240, 127880, 130560,
    133280, 136041, 138842, 141685, 144570, 147498, 150468, 153482, 156540, 159643,
    162790, 165983, 169222, 172508, 175840, 179220, 182648, 186125, 189650,
)

#: Cumulative XP required for 1..5 stars (index 0 == 1 star).
XP_TO_STAR: Tuple[int, ...] = (100, 2500, 5000, 7500, 10000)

MIN_ATTRIBUTE = 1
MAX_ATTRIBUTE = 99
MIN_STARS = 1
MAX_STARS = 5

#: Status side-file written by :func:`growth_sync_block` (kept separate from
#: ``_job_status.txt`` so a spliced block cannot clobber the host job's line).
GROWTH_STATUS_NAME = "_growth_status.txt"
JOB_STATUS_NAME = "_job_status.txt"

FieldPairs = Union[Mapping[str, Any], Iterable[Sequence[Any]]]


# ── Conversions ──────────────────────────────────────────────────────


def is_growable(field: Any) -> bool:
    """True when the growth system keeps an XP copy of ``field``."""
    return _norm(field) in GROWABLE_FIELDS


def is_star_field(field: Any) -> bool:
    """True for skillmoves / weakfootabilitytypecode (star-rated, 1..5)."""
    return _norm(field) in STAR_FIELDS


def attribute_to_xp(value: Any) -> int:
    """Cumulative XP for an attribute value. Clamped to 1..99."""
    v = _clamp(_as_int(value), MIN_ATTRIBUTE, MAX_ATTRIBUTE)
    return XP_TO_ATTRIBUTE[v - 1]


def xp_to_attribute(xp: Any) -> int:
    """Attribute value for a cumulative XP total — inverse of :func:`attribute_to_xp`.

    Highest value whose threshold is ``<= xp``; clamped to 1..99 outside the
    curve (below the first threshold still reads as 1, the game's floor).
    """
    total = _as_int(xp)
    if total < XP_TO_ATTRIBUTE[0]:
        return MIN_ATTRIBUTE
    idx = bisect.bisect_right(XP_TO_ATTRIBUTE, total)
    return _clamp(idx, MIN_ATTRIBUTE, MAX_ATTRIBUTE)


def stars_to_xp(stars: Any) -> int:
    """Cumulative XP for a 1..5 star rating. Clamped."""
    s = _clamp(_as_int(stars), MIN_STARS, MAX_STARS)
    return XP_TO_STAR[s - 1]


def xp_to_stars(xp: Any) -> int:
    """Star rating for a cumulative XP total — inverse of :func:`stars_to_xp`."""
    total = _as_int(xp)
    if total < XP_TO_STAR[0]:
        return MIN_STARS
    idx = bisect.bisect_right(XP_TO_STAR, total)
    return _clamp(idx, MIN_STARS, MAX_STARS)


def field_value_to_stars(field: Any, value: Any) -> int:
    """Convert a stored star-field value to a 1..5 star rating.

    ``skillmoves`` is 0-indexed in the players table, ``weakfootabilitytypecode``
    is 1-indexed. Getting this wrong costs the user a whole star.
    """
    f = _norm(field)
    if f not in STAR_FIELDS:
        raise ValueError(f"not a star field: {field!r}")
    raw = _as_int(value)
    stars = raw + 1 if f in ZERO_INDEXED_STAR_FIELDS else raw
    return _clamp(stars, MIN_STARS, MAX_STARS)


def stars_to_field_value(field: Any, stars: Any) -> int:
    """Inverse of :func:`field_value_to_stars` (1..5 stars -> stored value)."""
    f = _norm(field)
    if f not in STAR_FIELDS:
        raise ValueError(f"not a star field: {field!r}")
    s = _clamp(_as_int(stars), MIN_STARS, MAX_STARS)
    return s - 1 if f in ZERO_INDEXED_STAR_FIELDS else s


def field_value_to_xp(field: Any, value: Any) -> int:
    """XP to write into the development plan for ``field`` = ``value``."""
    f = _norm(field)
    if f in STAR_FIELDS:
        return stars_to_xp(field_value_to_stars(f, value))
    if f in GROWABLE_FIELDS:
        return attribute_to_xp(value)
    raise ValueError(f"field is not tracked by the growth system: {field!r}")


def xp_to_field_value(field: Any, xp: Any) -> int:
    """Inverse of :func:`field_value_to_xp` (XP total -> stored field value)."""
    f = _norm(field)
    if f in STAR_FIELDS:
        return stars_to_field_value(f, xp_to_stars(xp))
    if f in GROWABLE_FIELDS:
        return xp_to_attribute(xp)
    raise ValueError(f"field is not tracked by the growth system: {field!r}")


# Which number PlayerSetValueInDevelopementPlan actually wants is genuinely
# ambiguous, and getting it wrong is destructive, so the mode is explicit.
#
#   "raw" — write the attribute value itself (1..99, or the raw star code).
#   "xp"  — write the cumulative XP for that value, via XP_TO_ATTRIBUTE.
#
# The evidence is split. LE's DOC.MD prose says "Use this to set corresponding
# XP points for given field", and xAranaktu's own FC 25 Cheat Table converts
# attribute -> XP through this same 99-entry curve before touching the growth
# system. But DOC.MD's own example passes 99 for composure, which reads as an
# attribute value, and v1 of this app has always written raw values with
# dev=true in its logs without anyone reporting collapsed stats — which is what
# XP semantics would produce, since 99 XP maps to attribute 1 and the plan
# outranks the players table.
#
# So RAW is the default: it is the behaviour already proven in the field. Flip
# to "xp" only after probing a live save (write a known value, advance a day,
# read the attribute back).
DEFAULT_VALUE_MODE = "raw"


def growth_updates(fields: FieldPairs, *, mode: str = DEFAULT_VALUE_MODE) -> List[Tuple[str, int]]:
    """Filter ``fields`` down to growable ones and convert values for the plan.

    Accepts a mapping or an iterable of ``(field, value)`` pairs — the same
    shape ``player_schema.card_to_field_updates`` produces. Non-growable fields,
    blanks and unparseable values are dropped; a repeated field keeps its last
    value at its first position.

    ``mode`` selects the value semantics — see :data:`DEFAULT_VALUE_MODE`.
    """
    if mode not in ("raw", "xp"):
        raise ValueError(f"mode must be 'raw' or 'xp', got {mode!r}")
    out: "dict[str, int]" = {}
    for field, value in _iter_pairs(fields):
        f = _norm(field)
        if f not in GROWABLE_FIELDS:
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        try:
            out[f] = field_value_to_xp(f, value) if mode == "xp" else _as_int(value)
        except (TypeError, ValueError):
            continue
    return list(out.items())


# ── Lua generation ───────────────────────────────────────────────────


def growth_status_path() -> str:
    """Absolute forward-slash path of the growth block's status side-file."""
    return _lua_path((paths.queue_dir() / GROWTH_STATUS_NAME).resolve())


def growth_sync_block(
    playerid: Any,
    fields: FieldPairs,
    *,
    status_path: Optional[str] = None,
    mode: str = DEFAULT_VALUE_MODE,
) -> str:
    """Lua fragment that mirrors ``fields`` into the player's development plan.

    Self-contained ``do ... end`` block with its own locals, so it can be
    appended to an existing apply job instead of being queued as a separate
    job — which matters: a standalone job would execute at a different Career
    event than the DB write it is supposed to back up.

    Guarantees:

    * every LE global is reached only through ``type(...) == "function"`` plus
      ``pcall`` — nothing is ever called directly, so a missing API cannot throw;
    * the whole body runs inside a ``pcall``; a Lua error still produces a
      status line (``reason=lua_error``);
    * exactly one status line is written on every exit path.
    """
    pid = _as_int(playerid)
    updates = growth_updates(fields, mode=mode)
    path = status_path if status_path is not None else growth_status_path()
    rows = "\n".join(f'  {{"{f}", {xp}}},' for f, xp in updates)
    return _BLOCK_TEMPLATE.format(
        pid=pid,
        total=len(updates),
        status_path=_lua_str(path),
        rows=rows,
    ).replace("\r\n", "\n")


def generate_growth_sync_lua(
    playerid: Any,
    fields: FieldPairs,
    *,
    status_path: Optional[str] = None,
) -> str:
    """Standalone growth-sync job (queueable on its own).

    Prefer :func:`growth_sync_block` spliced into the apply job whenever the
    same values are also being written to the ``players`` table.
    """
    pid = _as_int(playerid)
    path = status_path if status_path is not None else _lua_path(
        (paths.queue_dir() / JOB_STATUS_NAME).resolve()
    )
    block = growth_sync_block(pid, fields, status_path=path)
    header = f"--[[ LE Companion growth sync | id={pid} ]]"
    return f"{header}\n{block}"


# ── internals ────────────────────────────────────────────────────────

# Every LE global (IsInCM / PlayerHasDevelopementPlan /
# PlayerSetValueInDevelopementPlan / Log) is type-checked and then invoked via
# pcall with the function passed as a *value* — `Name(` never appears, so no
# call site can hit a nil global. ReloadPlayersManager() from DOC.MD's example
# is deliberately NOT called: it freezes many FC 26 Career sessions (see the
# same note in player_apply.py).
_BLOCK_TEMPLATE = """do
-- LE Companion growth sync (make edits stick)
-- Career keeps an XP copy of every growable attribute in PlayerGrowthManager
-- and rewrites the players table from it after a match/training. Writing the DB
-- value alone reverts; mirroring it as XP is what makes the edit permanent.
local g_pid = {pid}
local g_total = {total}
local g_status_path = "{status_path}"
local g_fields = {{
{rows}
}}
local function g_log(msg)
  if type(Log) == "function" then pcall(Log, msg) end
end
local function g_report(ok, written, failed, plan, reason, msg)
  local line = string.format(
    "job=growth id=%d ok=%s written=%d failed=%d total=%d plan=%s reason=%s",
    g_pid, tostring(ok), written, failed, g_total, tostring(plan), reason
  )
  if msg ~= nil and msg ~= "" then line = line .. " msg=" .. msg end
  pcall(function()
    local f = io.open(g_status_path, "wb")
    if f then f:write(line .. "\\n") f:close() end
  end)
  g_log("[LE Companion] " .. line)
end
local g_ok, g_err = pcall(function()
  if g_total == 0 then
    g_report(true, 0, 0, false, "no_growable_fields")
    return
  end
  if type(IsInCM) ~= "function" then
    g_report(false, 0, 0, false, "no_iscm_api")
    return
  end
  local okc, in_cm = pcall(IsInCM)
  if (not okc) or (not in_cm) then
    -- Outside Career there is no growth system; the DB write is authoritative.
    g_report(true, 0, 0, false, "not_in_career")
    return
  end
  if type(PlayerHasDevelopementPlan) ~= "function" then
    g_report(false, 0, 0, false, "no_hasplan_api")
    return
  end
  if type(PlayerSetValueInDevelopementPlan) ~= "function" then
    g_report(false, 0, 0, false, "no_growth_api")
    return
  end
  local okp, has_plan = pcall(PlayerHasDevelopementPlan, g_pid)
  if not okp then
    g_report(false, 0, 0, false, "hasplan_error")
    return
  end
  if not has_plan then
    -- Not in a user-managed team: no plan overrides the row, nothing to mirror.
    g_report(true, 0, 0, false, "no_dev_plan")
    return
  end
  local g_written, g_failed = 0, 0
  for i = 1, #g_fields do
    local fname, fxp = g_fields[i][1], g_fields[i][2]
    if pcall(PlayerSetValueInDevelopementPlan, g_pid, fname, fxp) then
      g_written = g_written + 1
    else
      g_failed = g_failed + 1
    end
  end
  local g_reason = "ok"
  if g_written == 0 then
    g_reason = "all_writes_failed"
  elseif g_failed > 0 then
    g_reason = "partial_failures"
  end
  g_report(g_written > 0, g_written, g_failed, true, g_reason)
end)
if not g_ok then
  local g_msg = tostring(g_err)
  g_msg = g_msg:gsub("%s+", " ")
  g_report(false, 0, 0, false, "lua_error", g_msg)
end
end
"""


def _norm(field: Any) -> str:
    return str(field or "").strip().lower()


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(float(str(value).strip()))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _iter_pairs(fields: FieldPairs) -> Iterable[Tuple[Any, Any]]:
    if isinstance(fields, Mapping):
        return list(fields.items())
    out: List[Tuple[Any, Any]] = []
    for item in fields or ():
        if isinstance(item, (str, bytes)):
            raise TypeError("fields must be a mapping or (field, value) pairs")
        seq = tuple(item)
        if len(seq) < 2:
            raise TypeError("fields must be a mapping or (field, value) pairs")
        out.append((seq[0], seq[1]))
    return out


def _lua_str(s: Any) -> str:
    return (
        str(s or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def _lua_path(p: Any) -> str:
    return str(p).replace("\\", "/")
