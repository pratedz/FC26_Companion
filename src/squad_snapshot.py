"""Squad-level snapshot + restore (the safety net for batch / pack / mass edits).

`snapshot_store` covers a *single* player edit. Nothing covered a run that
touches the whole squad, which is exactly where a bad pack or a buggy mass edit
eats a career save. This module stores a whole-squad "before" state and can
regenerate Lua that writes it back.

Two capture sources, deliberately separate:

* :func:`capture_from_squad` — instant, offline, no Lua round-trip. It can only
  store what ``current_squad.json`` contains (see its docstring for exactly what
  that means for restore fidelity).
* :func:`generate_squad_capture_lua` — queued Lua that reads a much richer field
  set straight from the game DB; :func:`ingest_db_capture` turns its output into
  a normal snapshot with full-fidelity restore.

Safety rules (mirrors src/snapshot_store.py and src/undo_apply.py):
  * snapshot ids may not contain ``..``, ``/`` or ``\\``;
  * every resolved path is re-checked to be inside ``snapshots/squad/``;
  * index entries are always rewritten to bare basenames;
  * **no field name reaches a Lua string literal without passing
    :func:`_safe_field_name`** (regex + membership in ``player_schema``);
  * missing squad export degrades to ``None``/empty, never an exception.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import paths
from . import target_players

# Squad snapshots hold every player, so they are ~100x a per-player snapshot.
# 20 is the cap that keeps the folder small enough to ship inside a portable
# install (snapshot_store keeps 80 because its entries are a few hundred bytes).
MAX_SQUAD_SNAPSHOTS = 20

# Guard against a pathological squad export driving a multi-megabyte Lua file.
MAX_CAPTURE_PLAYERS = 1000

# Field names emitted into Lua string literals — reject injection / traversal.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# What export_user_squad.lua puts in current_squad.json per player.
EXPORT_FIELDS: Tuple[str, ...] = ("overallrating", "potential", "jerseynumber")

# Categories pulled by the DB capture Lua. Everything an editor apply or a pack
# realistically rewrites; face/kit/tattoo/career stay out so the capture file
# does not balloon for a benefit nobody restores.
CAPTURE_CATEGORIES: Tuple[str, ...] = (
    "ratings",
    "attributes",
    "skills",
    "positions",
    "playstyles",
    "body",
)

# Short labels for diff lines.
_DIFF_LABELS: Dict[str, str] = {
    "overallrating": "OVR",
    "potential": "POT",
    "jerseynumber": "#",
    "preferredposition1": "POS",
    "skillmoves": "SM",
    "weakfootabilitytypecode": "WF",
}


# ── field-name validation ────────────────────────────────────────────

@lru_cache(maxsize=1)
def _schema_fields() -> frozenset:
    """Known LE editor field names. Empty frozenset if the schema won't import."""
    try:
        from . import player_schema

        return frozenset(player_schema.ALL_EDIT_FIELDS)
    except Exception:  # noqa: BLE001 - schema is optional at import time
        return frozenset()


def _safe_field_name(name: Any) -> Optional[str]:
    """Return the field name only if it is safe to interpolate into Lua.

    Stricter than undo_apply on one point: if the schema cannot be imported we
    reject *everything* rather than falling back to "regex looks fine". A
    restore that silently writes unknown columns is worse than one that writes
    nothing.
    """
    f = str(name or "").strip()
    if not f or len(f) > 64 or not _FIELD_NAME_RE.match(f):
        return None
    if f not in _schema_fields():
        return None
    return f


def capture_field_names() -> List[str]:
    """Schema-validated field list used by the DB capture Lua (stable order)."""
    try:
        from . import player_schema

        wanted = player_schema.fields_for_categories(CAPTURE_CATEGORIES)
        ordered = [f for f in player_schema.ALL_EDIT_FIELDS if f in wanted]
    except Exception:  # noqa: BLE001
        return []
    out: List[str] = []
    seen = set()
    for f in ordered:
        sf = _safe_field_name(f)
        if sf and sf not in seen:
            seen.add(sf)
            out.append(sf)
    return out


def _safe_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n < -2147483648 or n > 2147483647:
        return None
    return n


def _lua_str(s: Any) -> str:
    return (
        str(s or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def _lua_comment_safe(s: Any, *, max_len: int = 80) -> str:
    """Strip sequences that can terminate a Lua long-comment --[[ ... ]]."""
    t = str(s or "").replace("\r", " ").replace("\n", " ")
    t = t.replace("]", "").replace("[", "").replace('"', "'")
    t = " ".join(t.split())
    return t[:max_len]


# ── storage (snapshots/squad/) ───────────────────────────────────────

def squad_dir() -> Path:
    d = paths.app_root() / "snapshots" / "squad"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return squad_dir() / "index.json"


def db_capture_path() -> Path:
    """Where generate_squad_capture_lua tells LE to drop its JSON."""
    return squad_dir() / "_db_capture.json"


def _safe_sid(sid: Any) -> Optional[str]:
    s = str(sid or "").strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        return None
    if not _SID_RE.match(s):
        return None
    return s


def _confined_snapshot_path(path_str: str, *, sid: str = "") -> Optional[Path]:
    """Resolve path only if it is a .json file directly under snapshots/squad/."""
    base = squad_dir().resolve()
    raw = (path_str or "").strip()
    candidates: List[Path] = []
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            candidates.append(base / p.name)
        else:
            candidates.append(p)
        # Prefer the basename under snapshots/squad even if an absolute (or
        # traversing) path was stored in an older / hand-edited index.
        candidates.append(base / Path(raw).name)
    if sid:
        candidates.append(base / f"{sid}.json")
    seen = set()
    for cand in candidates:
        try:
            rp = cand.resolve()
        except OSError:
            continue
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        try:
            rp.relative_to(base)
        except ValueError:
            continue
        if rp.parent != base:
            continue
        if rp.suffix.lower() != ".json":
            continue
        if rp.name in ("index.json", "_db_capture.json"):
            continue
        return rp
    return None


def _atomic_write_json(path: Path, data: Any) -> bool:
    """tmp file + os.replace — a killed process must not leave a half snapshot."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except (OSError, TypeError, ValueError):
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
        return False


def _load_index() -> List[Dict[str, Any]]:
    p = _index_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_index(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Persist at most MAX_SQUAD_SNAPSHOTS rows; delete the evicted payloads."""
    keep = rows[:MAX_SQUAD_SNAPSHOTS]
    evicted = rows[MAX_SQUAD_SNAPSHOTS:]
    cleaned: List[Dict[str, Any]] = []
    for row in keep:
        r = dict(row)
        sid = _safe_sid(r.get("id")) or ""
        rel = f"{sid}.json" if sid else Path(str(r.get("path") or "")).name
        if not rel.endswith(".json"):
            rel = f"{sid or 'snap'}.json"
        # Always store relative basenames only (never absolute escape paths)
        r["path"] = Path(rel).name
        cleaned.append(r)
    _atomic_write_json(_index_path(), cleaned)
    for row in evicted:
        sid = _safe_sid(row.get("id")) or ""
        # Delete by id first: a hand-edited `path` must never point the unlink
        # at a *different* snapshot's file.
        stored = f"{sid}.json" if sid else str(row.get("path") or "")
        victim = _confined_snapshot_path(stored, sid=sid)
        if victim is not None:
            try:
                victim.unlink()
            except OSError:
                pass
    return cleaned


def _index_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": payload.get("id"),
        "ts": payload.get("ts"),
        "iso": payload.get("iso"),
        "label": payload.get("label"),
        "kind": payload.get("kind"),
        "source": payload.get("source"),
        "teamid": payload.get("teamid"),
        "teamname": payload.get("teamname"),
        "count": payload.get("count"),
        "n_fields": len(payload.get("restore_fields") or []),
        "path": f"{payload.get('id')}.json",
    }


def _store(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sid = _safe_sid(payload.get("id"))
    if not sid:
        return None
    fpath = squad_dir() / f"{sid}.json"
    if not _atomic_write_json(fpath, payload):
        return None
    idx = _load_index()
    idx = [r for r in idx if r.get("id") != sid]
    idx.insert(0, _index_row(payload))
    _save_index(idx)
    return payload


# ── capture ──────────────────────────────────────────────────────────

def _new_payload(label: str, kind: str, source: str) -> Dict[str, Any]:
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:12],
        "ts": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "label": _lua_comment_safe(label or kind or "squad", max_len=120),
        "kind": _lua_comment_safe(kind or "batch", max_len=32) or "batch",
        "source": source,
    }


def capture_from_squad(
    label: str = "",
    kind: str = "batch",
    *,
    squad_path: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Snapshot the currently exported squad. Returns None when there is none.

    RESTORE FIDELITY — read this before trusting it as a full backup.
    This captures *only what the squad export gives us*: ``current_squad.json``
    (written by bridge/export_user_squad.lua) carries playerid, name, position
    name, overallrating, potential and jerseynumber. It is **not** the ~149-column
    players row. So a restore built from this snapshot rewrites overallrating and
    potential and nothing else:

      * ``jerseynumber`` lives in ``teamplayerlinks``, not ``players``, and is not
        in the editor schema, so it is stored for diffing/display but never
        written back;
      * ``name`` and ``position`` are display strings, stored for the confirm
        dialog only;
      * attributes, playstyles, skill moves, body, roles, face and contract data
        are simply not in the export — if a pack rewrote those, this snapshot
        cannot undo them.

    For a complete before-state, queue :func:`generate_squad_capture_lua` first
    and feed its result through :func:`ingest_db_capture`; that path stores every
    field in :func:`capture_field_names` and restores all of them.
    """
    try:
        squad = target_players.load_squad(squad_path)
    except Exception:  # noqa: BLE001 - a broken export must not crash a batch
        return None
    if not squad.get("loaded"):
        return None
    rows = squad.get("players") or []
    if not rows:
        return None

    players: List[Dict[str, Any]] = []
    for raw in rows[:MAX_CAPTURE_PLAYERS]:
        if not isinstance(raw, dict):
            continue
        pid = _safe_int(raw.get("playerid"))
        if pid is None or pid <= 0:
            continue
        values: Dict[str, int] = {}
        for f in EXPORT_FIELDS:
            v = _safe_int(raw.get(f))
            if v is not None:
                values[f] = v
        players.append(
            {
                "playerid": pid,
                "name": str(raw.get("name") or "").strip(),
                "position": str(raw.get("position") or "").strip(),
                "values": values,
            }
        )
    if not players:
        return None

    payload = _new_payload(label, kind, "squad_export")
    payload.update(
        {
            "mode": str(squad.get("mode") or "career"),
            "teamid": _safe_int(squad.get("teamid")) or 0,
            "teamname": str(squad.get("teamname") or ""),
            "count": len(players),
            "fields": list(EXPORT_FIELDS),
            "restore_fields": _restore_fields(players),
            "players": players,
        }
    )
    return _store(payload)


def _restore_fields(players: Sequence[Dict[str, Any]]) -> List[str]:
    """Schema-validated field names present in the captured values, in order."""
    out: List[str] = []
    seen = set()
    for p in players:
        for f in (p.get("values") or {}):
            if f in seen:
                continue
            seen.add(f)
            if _safe_field_name(f):
                out.append(f)
    return out


def load_db_capture() -> Optional[Dict[str, Any]]:
    """Read the JSON produced by generate_squad_capture_lua (None if absent)."""
    p = db_capture_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def ingest_db_capture(
    label: str = "",
    kind: str = "batch",
    *,
    merge_names: bool = True,
) -> Optional[Dict[str, Any]]:
    """Turn a completed DB capture file into a stored, full-fidelity snapshot.

    Returns None when the capture file is missing or unusable (LE never ran the
    job, wrong save loaded, …) so callers can fall back to capture_from_squad.
    """
    data = load_db_capture()
    if not data:
        return None
    rows = data.get("players")
    if not isinstance(rows, list) or not rows:
        return None

    meta: Dict[int, Dict[str, Any]] = {}
    if merge_names:
        try:
            squad = target_players.load_squad()
        except Exception:  # noqa: BLE001
            squad = {}
        for p in squad.get("players") or []:
            pid = _safe_int(p.get("playerid"))
            if pid:
                meta[pid] = p

    players: List[Dict[str, Any]] = []
    for raw in rows[:MAX_CAPTURE_PLAYERS]:
        if not isinstance(raw, dict):
            continue
        pid = _safe_int(raw.get("playerid"))
        if pid is None or pid <= 0:
            continue
        vals = raw.get("values")
        if not isinstance(vals, dict):
            continue
        values: Dict[str, int] = {}
        for k, v in vals.items():
            f = _safe_field_name(k)
            iv = _safe_int(v)
            if f is not None and iv is not None:
                values[f] = iv
        if not values:
            continue
        info = meta.get(pid) or {}
        players.append(
            {
                "playerid": pid,
                "name": str(info.get("name") or "").strip(),
                "position": str(info.get("position") or "").strip(),
                "values": values,
            }
        )
    if not players:
        return None

    payload = _new_payload(label, kind, "db_capture")
    payload.update(
        {
            "mode": str(data.get("mode") or "career"),
            "teamid": _safe_int(data.get("teamid")) or 0,
            "teamname": str(data.get("teamname") or ""),
            "count": len(players),
            "fields": _restore_fields(players),
            "restore_fields": _restore_fields(players),
            "players": players,
        }
    )
    return _store(payload)


# ── listing / retrieval ──────────────────────────────────────────────

def list_squad_snapshots(limit: int = MAX_SQUAD_SNAPSHOTS) -> List[Dict[str, Any]]:
    """Index rows, newest first."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = MAX_SQUAD_SNAPSHOTS
    if n <= 0:
        return []
    return _load_index()[:n]


def get_squad_snapshot(sid: str) -> Optional[Dict[str, Any]]:
    """Full snapshot payload, or None (unknown id / escape attempt / bad json)."""
    safe = _safe_sid(sid)
    if not safe:
        return None
    for row in _load_index():
        if row.get("id") == safe:
            p = _confined_snapshot_path(str(row.get("path") or ""), sid=safe)
            if p is not None and p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return None
                return data if isinstance(data, dict) else None
            return None
    p = _confined_snapshot_path(f"{safe}.json", sid=safe)
    if p is not None and p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
    return None


def delete_squad_snapshot(sid: str) -> bool:
    """Drop one snapshot + its index row. False when nothing was removed."""
    safe = _safe_sid(sid)
    if not safe:
        return False
    idx = _load_index()
    keep = [r for r in idx if r.get("id") != safe]
    removed = len(keep) != len(idx)
    p = _confined_snapshot_path(f"{safe}.json", sid=safe)
    if p is not None and p.is_file():
        try:
            p.unlink()
            removed = True
        except OSError:
            pass
    if len(keep) != len(idx):
        _save_index(keep)
    return removed


def format_snapshot_line(entry: Dict[str, Any], idx: int = 0) -> str:
    """One-line UI row: `[0] 07-26 14:03  Before pack  batch  23 players  …`."""
    import datetime as _dt

    row = entry if isinstance(entry, dict) else {}
    ts = row.get("ts") or 0
    try:
        when = _dt.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        when = "??"
    team = str(row.get("teamname") or "").strip() or f"team {row.get('teamid') or 0}"
    src = row.get("source") or "squad_export"
    fidelity = "full" if src == "db_capture" else "ovr/pot"
    return (
        f"[{idx}] {when}  {row.get('label') or row.get('kind') or 'squad'}  "
        f"{row.get('kind') or 'batch'}  players={row.get('count') or 0}  "
        f"fields={row.get('n_fields') or 0} ({fidelity})  "
        f"{team}  sid={row.get('id')}"
    )


# ── Lua: DB capture ──────────────────────────────────────────────────

_CAPTURE_BODY = r"""
local function log(msg)
  if Log then pcall(Log, "[LE Companion] " .. tostring(msg)) end
end

local function write_status(line)
  pcall(function()
    local f = io.open(STATUS_PATH, "wb")
    if f then f:write(tostring(line) .. "\n") f:close() end
  end)
end

local function json_escape(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\")
  s = s:gsub('"', '\\"')
  s = s:gsub("\r", "\\r")
  s = s:gsub("\n", "\\n")
  s = s:gsub("\t", "\\t")
  return s
end

local function row_pid(r)
  local v = r.playerid
  if type(v) == "table" then return tostring(v.value or "") end
  return tostring(v)
end

local function field_value(r, fname)
  local fld = r[fname]
  if fld == nil then return nil end
  if type(fld) == "table" then return fld.value end
  if type(fld) == "number" or type(fld) == "string" then return fld end
  return nil
end

-- Never throw: every exit path writes exactly one status line, so "no file"
-- can never be misread as success.
local captured, missed = 0, 0
local reason = "ok"
local ok_main = pcall(function()
  -- Raw record walks over players (25000 cap) have been observed to stop early
  -- and silently return a partial squad. GetDBTableRows is the freeze-safe
  -- pattern used everywhere else in this project.
  if type(GetDBTableRows) ~= "function" then
    reason = "no_getdbtablerows"
    return
  end
  local ok, rows = pcall(GetDBTableRows, "players")
  if not ok or type(rows) ~= "table" then
    reason = "rows_fail"
    return
  end
  local teamid = 0
  if type(GetUserTeamID) == "function" then
    local okt, tid = pcall(GetUserTeamID)
    if okt and tonumber(tid) then teamid = tonumber(tid) end
  end
  local teamname = ""
  if teamid > 0 and type(GetTeamName) == "function" then
    local okn, tn = pcall(GetTeamName, teamid)
    if okn and tn then teamname = tostring(tn) end
  end

  local parts = {}
  local seen = {}
  for _, r in pairs(rows) do
    if type(r) == "table" and r.playerid then
      local pid_s = row_pid(r)
      if WANT[pid_s] and not seen[pid_s] then
        seen[pid_s] = true
        local kv = {}
        for i = 1, #FIELDS do
          local fname = FIELDS[i]
          local v = field_value(r, fname)
          if v == nil then
            missed = missed + 1
          else
            local num = tonumber(v)
            if num ~= nil then
              kv[#kv + 1] = string.format('"%s": %d', fname, math.floor(num))
            else
              kv[#kv + 1] = string.format('"%s": "%s"', fname, json_escape(v))
            end
          end
        end
        captured = captured + 1
        parts[#parts + 1] = string.format(
          '    {"playerid": %d, "values": {%s}}',
          math.floor(tonumber(pid_s) or 0),
          table.concat(kv, ", ")
        )
        if captured % 25 == 0 then
          log(string.format("squad capture %d/%d", captured, WANT_N))
        end
      end
    end
  end

  local body = {}
  body[#body + 1] = "{"
  body[#body + 1] = '  "kind": "squad_db_capture",'
  body[#body + 1] = string.format('  "label": "%s",', json_escape(LABEL))
  body[#body + 1] = '  "mode": "career",'
  body[#body + 1] = string.format('  "teamid": %d,', teamid)
  body[#body + 1] = string.format('  "teamname": "%s",', json_escape(teamname))
  body[#body + 1] = string.format('  "count": %d,', captured)
  body[#body + 1] = '  "players": ['
  for i = 1, #parts do
    body[#body + 1] = parts[i] .. ((i < #parts) and "," or "")
  end
  body[#body + 1] = "  ]"
  body[#body + 1] = "}"
  body[#body + 1] = ""

  local okw = pcall(function()
    local f = io.open(OUT_PATH, "wb")
    if f == nil then error("open_failed") end
    f:write(table.concat(body, "\n"))
    f:close()
  end)
  if not okw then
    reason = "write_failed"
    captured = 0
  end
end)

if not ok_main then
  reason = "lua_error"
end
if reason == "ok" and captured == 0 then
  reason = "no_players_matched"
end
write_status(string.format(
  "job=squad_capture ok=%s written=%d failed=%d players=%d wanted=%d reason=%s",
  tostring(reason == "ok" and captured > 0), captured, missed, captured, WANT_N, reason
))
log(string.format("squad capture done written=%d failed=%d reason=%s", captured, missed, reason))
"""


def generate_squad_capture_lua(
    playerids: Iterable[Any],
    fields: Optional[Sequence[str]] = None,
    *,
    label: str = "squad capture",
) -> str:
    """Lua that reads a rich field set for `playerids` and writes it as JSON.

    The output lands at :func:`db_capture_path` (under the app root) and is
    consumed by :func:`ingest_db_capture`, so a snapshot can be complete instead
    of limited to the six columns the squad export carries.

    Uses ``GetDBTableRows("players")`` — one call, one pass for the whole squad.
    A raw ``GetFirstRecord``/``GetNextValidRecord`` walk with a 25000 cap has
    been observed to stop early and silently return a partial squad, which for a
    *backup* is worse than failing loudly. Every global is guarded with
    ``type(x) == "function"`` and every call is wrapped in ``pcall``: the script
    never throws and always writes exactly one status line.
    """
    ids: List[int] = []
    seen = set()
    for raw in playerids or []:
        pid = _safe_int(raw)
        if pid is None or pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
        if len(ids) >= MAX_CAPTURE_PLAYERS:
            break

    safe_fields: List[str] = []
    for f in (fields if fields is not None else capture_field_names()):
        sf = _safe_field_name(f)
        if sf and sf not in safe_fields:
            safe_fields.append(sf)

    out_path = _lua_str(str(db_capture_path().resolve()).replace("\\", "/"))
    status_path = _lua_str(
        str((paths.queue_dir() / "_job_status.txt").resolve()).replace("\\", "/")
    )
    want_rows = "\n".join(f'  ["{pid}"] = true,' for pid in ids)
    field_rows = "\n".join(f'  "{f}",' for f in safe_fields)
    head = _lua_comment_safe(f"{label} | players={len(ids)} fields={len(safe_fields)}")

    header = f"""--[[ LE Companion squad capture | {head} ]]
pcall(function() require("imports/career_mode/helpers") end)
pcall(function() require("imports/other/helpers") end)
local OUT_PATH = "{out_path}"
local STATUS_PATH = "{status_path}"
local LABEL = "{_lua_str(label)}"
local WANT_N = {len(ids)}
local WANT = {{
{want_rows}
}}
local FIELDS = {{
{field_rows}
}}
"""
    return (header + _CAPTURE_BODY).replace("\r\n", "\n")


# ── Lua: restore ─────────────────────────────────────────────────────

_RESTORE_BODY = r"""
local function log(msg)
  if Log then pcall(Log, "[LE Companion] " .. tostring(msg)) end
end

local function write_status(line)
  pcall(function()
    local f = io.open(STATUS_PATH, "wb")
    if f then f:write(tostring(line) .. "\n") f:close() end
  end)
end

local function row_pid(r)
  local v = r.playerid
  if type(v) == "table" then return tostring(v.value or "") end
  return tostring(v)
end

local want = {}
local total_players, expected = 0, 0
for i = 1, #PLAYERS do
  want[PLAYERS[i][1]] = PLAYERS[i][2]
  total_players = total_players + 1
  expected = expected + #PLAYERS[i][2]
end

local matched, written, failed = 0, 0, 0
local reason = "ok"
local ok_main = pcall(function()
  -- GetDBTableRows: one fetch for the whole squad (record walks freeze FC26 and
  -- have been seen to stop early; a partial restore is a corrupted save).
  if type(GetDBTableRows) ~= "function" or type(EditDBTableField) ~= "function" then
    reason = "no_db_api"
    return
  end
  local ok, rows = pcall(GetDBTableRows, "players")
  if not ok or type(rows) ~= "table" then
    reason = "rows_fail"
    return
  end
  local seen = {}
  for _, r in pairs(rows) do
    if type(r) == "table" and r.playerid then
      local pid_s = row_pid(r)
      local fields = want[pid_s]
      if fields ~= nil and not seen[pid_s] then
        seen[pid_s] = true
        matched = matched + 1
        for i = 1, #fields do
          local fname, fval = fields[i][1], fields[i][2]
          local fld = r[fname]
          if type(fld) == "table" then
            -- Discarding this pcall is how written=91 used to mean 91 silent failures
            local okw = pcall(function()
              fld.value = tostring(fval)
              EditDBTableField(fld)
            end)
            if okw then written = written + 1 else failed = failed + 1 end
          else
            failed = failed + 1
          end
        end
        if matched % BATCH == 0 then
          log(string.format(
            "squad restore %d/%d players written=%d failed=%d",
            matched, total_players, written, failed
          ))
        end
      end
    end
  end
end)

local missing = total_players - matched
if not ok_main then
  reason = "lua_error"
elseif reason == "ok" then
  if matched == 0 then
    reason = "no_players_matched"
  elseif written == 0 then
    reason = "all_writes_failed"
  elseif failed > 0 then
    reason = "partial_write_failures"
  elseif missing > 0 then
    reason = "partial_players_missing"
  end
end

-- NEVER ReloadPlayersManager — freezes FC26 Career
write_status(string.format(
  "job=squad_restore sid=%s ok=%s players=%d matched=%d missing=%d written=%d failed=%d expected=%d reason=%s label=%s",
  SID, tostring(reason == "ok" and written > 0), total_players, matched, missing,
  written, failed, expected, reason, LABEL
))
log(string.format(
  "squad restore done matched=%d/%d written=%d failed=%d reason=%s",
  matched, total_players, written, failed, reason
))
"""


def generate_squad_restore_lua(sid: str, *, batch: int = 25) -> str:
    """Lua that writes every captured field back for every captured player.

    Field names are re-validated here (never trusted from the stored JSON) the
    same way undo_apply does it, so a hand-edited or legacy snapshot cannot
    inject Lua through a field name. Values are re-parsed as ints.

    Raises ValueError when the snapshot is unknown or has nothing restorable —
    matching snapshot_store.undo_lua_for.
    """
    snap = get_squad_snapshot(sid)
    if not snap:
        raise ValueError(f"squad snapshot not found: {sid}")

    rows: List[str] = []
    n_fields = 0
    for p in snap.get("players") or []:
        if not isinstance(p, dict):
            continue
        pid = _safe_int(p.get("playerid"))
        if pid is None or pid <= 0:
            continue
        vals = p.get("values")
        if not isinstance(vals, dict):
            continue
        pairs: List[str] = []
        for k, v in vals.items():
            f = _safe_field_name(k)
            if f is None:
                continue
            iv = _safe_int(v)
            if iv is None:
                continue
            pairs.append(f'{{"{f}", {iv}}}')
        if not pairs:
            continue
        n_fields += len(pairs)
        rows.append(f'  {{"{pid}", {{{", ".join(pairs)}}}}},')

    if not rows:
        raise ValueError(f"squad snapshot has no restorable fields: {sid}")

    try:
        nbatch = max(1, min(200, int(batch)))
    except (TypeError, ValueError):
        nbatch = 25

    status_path = _lua_str(
        str((paths.queue_dir() / "_job_status.txt").resolve()).replace("\\", "/")
    )
    label = _lua_comment_safe(snap.get("label") or "squad restore")
    team = _lua_comment_safe(snap.get("teamname") or "", max_len=40)
    safe_sid = _safe_sid(snap.get("id")) or _safe_sid(sid) or "unknown"

    header = f"""--[[ LE Companion squad restore | sid={safe_sid} | {label} | {team} \
| players={len(rows)} fields={n_fields} ]]
pcall(function() require("imports/career_mode/helpers") end)
local SID = "{_lua_str(safe_sid)}"
local LABEL = "{_lua_str(label).replace(' ', '_')}"
local STATUS_PATH = "{status_path}"
local BATCH = {nbatch}
-- {{playerid_string, {{{{field, value}}, ...}}}}
local PLAYERS = {{
{chr(10).join(rows)}
}}
"""
    return (header + _RESTORE_BODY).replace("\r\n", "\n")


# ── diff (confirm dialog) ────────────────────────────────────────────

def _fmt_val(field: str, v: Any) -> str:
    return "—" if v is None else str(v)


def diff_against_squad(
    sid: str,
    *,
    squad_path: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Compare a stored snapshot to the live current_squad.json.

    Returns None only when `sid` is unknown. With no squad export it returns a
    dict with ``ok=False`` / ``reason="no_squad_export"`` and empty lists rather
    than raising, so a confirm dialog can still show the snapshot metadata.

    Only fields present in *both* the snapshot and the export are compared —
    with a squad-export snapshot that means overallrating / potential /
    jerseynumber.
    """
    snap = get_squad_snapshot(sid)
    if not snap:
        return None

    out: Dict[str, Any] = {
        "sid": snap.get("id"),
        "label": snap.get("label"),
        "kind": snap.get("kind"),
        "source": snap.get("source"),
        "ts": snap.get("ts"),
        "ok": False,
        "reason": "no_squad_export",
        "changed": [],
        "missing": [],
        "added": [],
        "unchanged": 0,
        "lines": [],
        "summary": "",
    }

    try:
        squad = target_players.load_squad(squad_path)
    except Exception:  # noqa: BLE001
        squad = {"loaded": False, "players": []}
    if not squad.get("loaded"):
        out["summary"] = "No squad export — run Export squad to compare"
        return out

    current: Dict[int, Dict[str, Any]] = {}
    for p in squad.get("players") or []:
        pid = _safe_int(p.get("playerid"))
        if pid:
            current[pid] = p

    snap_ids = set()
    for p in snap.get("players") or []:
        if not isinstance(p, dict):
            continue
        pid = _safe_int(p.get("playerid"))
        if pid is None:
            continue
        snap_ids.add(pid)
        name = str(p.get("name") or "").strip() or f"id {pid}"
        live = current.get(pid)
        if live is None:
            out["missing"].append({"playerid": pid, "name": name})
            continue
        changes: List[Dict[str, Any]] = []
        for f, old in (p.get("values") or {}).items():
            if f not in live:
                continue
            new = _safe_int(live.get(f))
            old_i = _safe_int(old)
            if old_i is None or new is None or old_i == new:
                continue
            changes.append({"field": f, "from": old_i, "to": new})
        if not changes:
            out["unchanged"] += 1
            continue
        changes.sort(key=lambda c: str(c["field"]))
        bits = ", ".join(
            f"{_DIFF_LABELS.get(str(c['field']), str(c['field']))} "
            f"{_fmt_val(str(c['field']), c['from'])}→{_fmt_val(str(c['field']), c['to'])}"
            for c in changes
        )
        line = f"{name} ({pid}): {bits}"
        out["changed"].append(
            {"playerid": pid, "name": name, "changes": changes, "line": line}
        )
        out["lines"].append(line)

    for pid, p in current.items():
        if pid not in snap_ids:
            nm = str(p.get("name") or "").strip() or f"id {pid}"
            out["added"].append({"playerid": pid, "name": nm})

    out["changed"].sort(key=lambda c: (-len(c["changes"]), str(c["name"])))
    out["lines"] = [c["line"] for c in out["changed"]]
    out["ok"] = True
    out["reason"] = "ok"
    out["summary"] = (
        f"{len(out['changed'])} changed · {out['unchanged']} unchanged · "
        f"{len(out['missing'])} gone · {len(out['added'])} new"
    )
    return out
