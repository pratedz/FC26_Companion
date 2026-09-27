"""LE Lua script emitter for Add-to-team.

SAFE v15 default path (no CreatePlayer):
  1) Pick free-agent dummy from Python-provided HINT_DUMMIES (worst OVR)
  2) Overwrite players-table fields (import-style) + editedplayernames
  3) TransferPlayer into user team
  4) Optional real-face apply after transfer

CreatePlayer only when mode == "create" (explicit opt-in; freeze risk).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping, Optional, Sequence, Tuple


def _ascii_safe(s: str) -> str:
    """Pelé → Pele so LE strings / logs don't corrupt."""
    t = unicodedata.normalize("NFKD", str(s or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    out = []
    for c in t:
        if c.isalnum() or c in (" ", "-", "'", "."):
            out.append(c)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _esc(s: str) -> str:
    return _ascii_safe(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _row_to_lua_table(row: Mapping[str, str]) -> str:
    lines = [f'  ["{k}"] = "{_esc(row[k])}",' for k in sorted(row.keys())]
    return "{\n" + "\n".join(lines) + "\n}"


def _fields_to_lua_array(fields: Sequence[Tuple[str, int]]) -> str:
    lines = []
    for f, v in fields:
        fname = re.sub(r"[^A-Za-z0-9_]", "", str(f or ""))
        if not fname:
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        lines.append(f'  {{"{fname}", {iv}}},')
    return "{\n" + "\n".join(lines) + "\n}"


def render_add_to_team_lua(
    *,
    name: str,
    first: str,
    sur: str,
    jersey: str,
    teamid: int,
    mode: str,
    preferred_create_id: int,
    player_row: Mapping[str, str],
    field_updates: Sequence[Tuple[str, int]],
    dummy_ids: Sequence[int],
    transfersum: int,
    wage: int,
    contract_months: int,
    age: int,
    fallback_birthdate: str,
    use_real_face: bool,
    base_face_id: int,
    face_hq: Optional[int] = None,
    face_headtype: Optional[int] = None,
    face_variation: Optional[int] = None,
    nation_id: Optional[int],
    gen_min: int,
    gen_max: int,
    crash_log_path: str,
    status_path: str,
) -> str:
    """Emit SAFE v15 LE Lua.

    mode auto|dummy: dummy overwrite + transfer (no CreatePlayer).
    mode create: CreatePlayer opt-in only.
    """
    mode_s = (mode or "auto").strip().lower()
    if mode_s not in ("auto", "create", "dummy"):
        mode_s = "auto"
    # auto == dummy-first (never CreatePlayer)
    primary_dummy = mode_s in ("auto", "dummy")
    allow_create = mode_s == "create"

    tid = int(teamid)
    gen_id = int(preferred_create_id)
    dummies_lua = ", ".join(str(int(x)) for x in dummy_ids)
    row = dict(player_row or {})
    nat = int(nation_id) if nation_id and int(nation_id) > 0 else 0
    if nat > 0:
        row["nationality"] = str(nat)
    row_lua = _row_to_lua_table(row)
    fields_lua = _fields_to_lua_array(field_updates)
    use_face = bool(use_real_face and base_face_id > 0)
    face_id = int(base_face_id) if use_face else 0
    # Resolve the real face quartet from base_players.csv. Guessing these
    # values is what produced blank faces: hashighqualityhead is 0 for ~86% of
    # players and headtypecode is player-specific.
    if use_face and (face_hq is None or face_headtype is None):
        try:
            from . import base_players as _bp

            profile = _bp.face_profile(face_id)
            if profile.get("real"):
                if face_hq is None:
                    face_hq = profile.get("hashighqualityhead")
                if face_headtype is None:
                    face_headtype = profile.get("headtypecode")
                if face_variation is None:
                    face_variation = profile.get("headvariation")
            else:
                # Base data says this player has no scanned head. Fall back to
                # the always-safe generic combination rather than pointing the
                # game at a head asset that does not exist.
                use_face = False
                face_id = 0
        except Exception:  # noqa: BLE001
            pass
    face_hq = 0 if face_hq is None else (1 if int(face_hq) == 1 else 0)
    face_headtype = 0 if face_headtype is None else max(0, int(face_headtype))
    face_variation = 0 if face_variation is None else max(0, int(face_variation))
    first_s = _esc(first) or "Player"
    sur_s = _esc(sur) or first_s
    jersey_s = _esc(jersey) or sur_s
    if len(jersey_s) > 15:
        jersey_s = jersey_s[:15]

    # CreatePlayer block only for explicit create mode
    create_block = ""
    if allow_create:
        create_block = f"""
local function pdata_for_id(pid)
  local out = {{}}
  for k, v in pairs(player_data) do
    if k ~= "firstnameid" and k ~= "lastnameid" and k ~= "commonnameid"
      and k ~= "playerjerseynameid" and k ~= "usercaneditname" then
      out[k] = tostring(v)
    end
  end
  if NATION_ID and NATION_ID > 0 then
    out["nationality"] = string.format("%d", NATION_ID)
  end
  -- ALWAYS generic head on CreatePlayer (Icon headassetid freezes native FC26)
  out["headassetid"] = string.format("%d", pid)
  out["hashighqualityhead"] = "0"
  out["headclasscode"] = "1"
  out["headtypecode"] = "0"
  out["headvariation"] = out["headvariation"] or "0"
  if not out["height"] or tonumber(out["height"]) < 160 then out["height"] = "180" end
  if not out["weight"] or tonumber(out["weight"]) < 55 then out["weight"] = "75" end
  if not out["bodytypecode"] or out["bodytypecode"] == "0" then out["bodytypecode"] = "5" end
  out["birthdate"] = FALLBACK_BIRTHDATE
  return out
end

local function try_create()
  if type(CreatePlayer) ~= "function" then return 0, "no_CreatePlayer" end
  -- Absolute ceiling: CreatePlayer with pid >= 500000 kills the game process.
  local HARD_MAX = 499999
  local pid = PREFERRED_CREATE_ID
  if pid < GEN_MIN or pid > GEN_MAX then pid = GEN_MIN + 12345 end
  if pid > HARD_MAX then pid = GEN_MIN + 12345 end
  if type(PlayerExists) == "function" then
    local ok, ex = pcall(PlayerExists, pid)
    if ok and ex then
      pid = pid + 777
      if pid > GEN_MAX or pid > HARD_MAX then pid = GEN_MIN + 22222 end
      local ok2, ex2 = pcall(PlayerExists, pid)
      if ok2 and ex2 then
        log_step("create_skip_exists", "pid=" .. tostring(pid))
        return 0, "id_exists"
      end
    end
  end
  local pdata = pdata_for_id(pid)
  local nkeys = 0
  for _ in pairs(pdata) do nkeys = nkeys + 1 end
  log_step("create_enter", "pid=" .. tostring(pid)
    .. " keys=" .. tostring(nkeys)
    .. " face=generic:" .. tostring(pdata["headassetid"])
    .. " deferred_face=" .. tostring(DEFERRED_FACE_ID))
  local okc, created = pcall(CreatePlayer, pid, pdata)
  log_step("create_done", "ok=" .. tostring(okc) .. " ret=" .. tostring(created))
  if okc and created and tonumber(created) and tonumber(created) > 0 then
    return tonumber(created), "create"
  end
  if okc and (created == true or created == pid) then
    return pid, "create"
  end
  if type(PlayerExists) == "function" then
    local ok3, ex3 = pcall(PlayerExists, pid)
    if ok3 and ex3 then return pid, "create" end
  end
  return 0, "create_failed"
end
"""

    pick_logic = ""
    if primary_dummy:
        pick_logic = """
  -- DEFAULT: free-agent dummy overwrite (no CreatePlayer — avoids FC26 freeze)
  pid, path = try_dummy_overwrite()
  if not pid or pid == 0 then
    write_status("job=add_to_team ok=false path=none id=0 team=" .. tostring(teamid)
      .. " err=no_free_agent_dummy")
    log_step("abort", "no_free_agent_dummy — export free agents or pass dummy pool; CreatePlayer is opt-in only (mode=create)")
    return
  end
"""
    else:
        pick_logic = """
  pid, path = try_create()
  if not pid or pid == 0 then
    write_status("job=add_to_team ok=false path=none id=0 team=" .. tostring(teamid)
      .. " err=no_playerid mode=create")
    log_step("abort", "no_playerid")
    return
  end
"""

    body = f"""--[[ LE Companion add_to_team SAFE v15 | mode={mode_s} | team={tid} | {_esc(name)} ]]
-- v15: default = free-agent DUMMY overwrite + TransferPlayer (no CreatePlayer)
-- CreatePlayer only when mode=create (freeze risk). Log: {crash_log_path}
pcall(function() require("imports/other/helpers") end)

local MODE = "{mode_s}"
local TARGET_TEAM = {tid}
local PREFERRED_CREATE_ID = {gen_id}
local HINT_DUMMIES = {{ {dummies_lua} }}
local TRANSFERSUM = {int(transfersum)}
local WAGE = {max(500, int(wage))}
local CONTRACT_MONTHS = {max(12, min(72, int(contract_months)))}
local FIRST = "{first_s}"
local SUR = "{sur_s}"
local JERSEY = "{jersey_s}"
local TARGET_AGE = {int(age)}
local FALLBACK_BIRTHDATE = "{_esc(fallback_birthdate)}"
local DEFERRED_FACE_ID = {face_id}
local FACE_HQ = {face_hq}
local FACE_HEADTYPE = {face_headtype}
local FACE_VARIATION = {face_variation}
local NATION_ID = {nat}
local player_data = {row_lua}
local FIELD_UPDATES = {fields_lua}
local GEN_MIN = {int(gen_min)}
local GEN_MAX = {int(gen_max)}
local CRASH_LOG = "{crash_log_path}"
local STATUS_PATH = "{status_path}"
local PRIMARY_DUMMY = {"true" if primary_dummy else "false"}

local function log(msg)
  if Log then Log("[LE Companion] " .. tostring(msg)) end
end

local function log_step(step, detail)
  local line = string.format("%s step=%s %s\\n", os.date("!%Y-%m-%dT%H:%M:%SZ"), tostring(step), tostring(detail or ""))
  pcall(function()
    local f = io.open(CRASH_LOG, "ab")
    if f then f:write(line) f:close() end
  end)
  log(line)
end

local function write_status(s)
  pcall(function()
    local f = io.open(STATUS_PATH, "wb")
    if f then f:write(tostring(s) .. "\\n") f:close() end
  end)
end

local function resolve_team()
  if TARGET_TEAM and TARGET_TEAM > 0 then return TARGET_TEAM end
  if type(GetUserTeamID) == "function" then
    local ok, tid = pcall(GetUserTeamID)
    if ok and tid and tonumber(tid) and tonumber(tid) > 0 then return tonumber(tid) end
  end
  return 0
end

local function common_display()
  -- Prefer jersey (nick/display), then "First Last", then surname/first
  local c = JERSEY
  if c == nil or c == "" then
    if FIRST ~= "" and SUR ~= "" then c = FIRST .. " " .. SUR
    elseif SUR ~= "" then c = SUR
    else c = FIRST end
  end
  if c == nil or c == "" then c = "Player" end
  return c
end

local function force_name_fields(row, common)
  if type(row) ~= "table" or type(EditDBTableField) ~= "function" then return false end
  local map = {{
    firstname = FIRST,
    surname = SUR,
    playerjerseyname = JERSEY,
    commonname = common,
  }}
  local any = false
  for fname, fval in pairs(map) do
    local fld = row[fname]
    if type(fld) == "table" then
      fld.value = fval
      local ok = pcall(EditDBTableField, fld)
      if ok then any = true end
    end
  end
  return any
end

-- CRITICAL: free-agent dummies keep firstnameid/lastnameid/commonnameid dictionary
-- pointers. Stats/face can update while GetPlayerName still returns the FA label
-- (e.g. "Dony Tri Pamungkas") or "Player". Zero dict ids + usercaneditname so
-- editedplayernames / CM UI show the card name.
-- Use GetDBTableRows only (avoid full-table record walks — freezes FC26).
local function clear_name_dictionary_ids_on_players(pid)
  if not pid or pid <= 0 then return false end
  if type(GetDBTableRows) ~= "function" or type(EditDBTableField) ~= "function" then
    log_step("name_dict_clear", "no_api")
    return false
  end
  local pid_s = string.format("%d", pid)
  local cleared = 0
  local ok, rows = pcall(GetDBTableRows, "players")
  if ok and type(rows) == "table" then
    for _, r in pairs(rows) do
      if type(r) == "table" and r.playerid then
        local rpid = type(r.playerid) == "table" and tostring(r.playerid.value or "") or tostring(r.playerid)
        if rpid == pid_s then
          local ids = {{
            firstnameid = "0",
            lastnameid = "0",
            commonnameid = "0",
            playerjerseynameid = "0",
            usercaneditname = "1",
          }}
          for fname, fval in pairs(ids) do
            local fld = r[fname]
            if type(fld) == "table" then
              fld.value = fval
              if pcall(EditDBTableField, fld) then cleared = cleared + 1 end
            end
          end
          log_step("name_dict_clear", "written=" .. tostring(cleared))
          return cleared > 0
        end
      end
    end
  end
  log_step("name_dict_clear", "miss")
  return false
end

local function set_edited_name(pid)
  if not pid or pid <= 0 then return false end
  log_step("name_enter", "pid=" .. tostring(pid) .. " " .. FIRST .. " " .. SUR)
  local pid_s = string.format("%d", pid)
  local common = common_display()
  -- 1) Break dictionary name so CM cannot keep free-agent label
  clear_name_dictionary_ids_on_players(pid)
  -- 2) UPDATE any existing editedplayernames row for this pid first.
  -- Inserting unconditionally appends duplicates and the game resolves the
  -- FIRST matching row, so a stale row from an earlier run wins forever.
  local wrote = false
  local existing = 0
  if type(GetDBTableRows) == "function" then
    local ok2, rows = pcall(GetDBTableRows, "editedplayernames")
    if ok2 and type(rows) == "table" then
      for _, r in pairs(rows) do
        if type(r) == "table" and r.playerid then
          local rpid = type(r.playerid) == "table" and tostring(r.playerid.value or "") or tostring(r.playerid)
          if rpid == pid_s then
            existing = existing + 1
            if force_name_fields(r, common) then wrote = true end
          end
        end
      end
    end
  end
  log_step("name_existing", "rows=" .. tostring(existing) .. " updated=" .. tostring(wrote))
  -- 3) Only insert when the player has no row yet.
  if existing == 0 then
    if type(InsertDBTableRow) == "function" then
      local row_data = {{
        playerid = pid_s,
        firstname = FIRST,
        surname = SUR,
        playerjerseyname = JERSEY,
        commonname = common,
      }}
      local ok, row = pcall(InsertDBTableRow, "editedplayernames", row_data)
      -- LE DOC: a failed insert returns a row whose addr is "0" (or nil).
      -- pcall success alone does NOT mean the row was written.
      local addr = (ok and type(row) == "table") and tostring(row.addr or "") or ""
      if ok and addr ~= "" and addr ~= "0" then
        wrote = true
        log_step("name_insert_ok", "addr=" .. addr)
        force_name_fields(row, common)
      else
        log_step("name_insert_fail", "ok=" .. tostring(ok) .. " addr=[" .. addr .. "]")
      end
    else
      log_step("name_skip", "no_InsertDBTableRow")
    end
  end
  if type(GetPlayerName) == "function" then
    local okn, n = pcall(GetPlayerName, pid)
    log_step("name_verify", "GetPlayerName=[" .. tostring(n or "") .. "] ok=" .. tostring(okn))
  end
  log_step("name_done", wrote and "ok" or "partial")
  return wrote
end

-- Import-style field overwrite on an existing free-agent playerid (no CreatePlayer)
local function apply_fields_to_pid(pid)
  if not pid or pid <= 0 then return false end
  if type(GetDBTableRows) ~= "function" or type(EditDBTableField) ~= "function" then
    log_step("fields_skip", "no_GetDBTableRows")
    return false
  end
  log_step("fields_enter", "pid=" .. tostring(pid) .. " n=" .. tostring(#FIELD_UPDATES))
  local pid_s = string.format("%d", pid)
  local ok, rows = pcall(GetDBTableRows, "players")
  if not ok or type(rows) ~= "table" then
    log_step("fields_skip", "rows_fail")
    return false
  end
  for _, r in pairs(rows) do
    if type(r) == "table" and r.playerid then
      local rpid = type(r.playerid) == "table" and tostring(r.playerid.value or "") or tostring(r.playerid)
      if rpid == pid_s then
        local written = 0
        for i = 1, #FIELD_UPDATES do
          local fname, fval = FIELD_UPDATES[i][1], FIELD_UPDATES[i][2]
          -- Never write dictionary nameids via field list (0 via clear_name_* only)
          if fname ~= "firstnameid" and fname ~= "lastnameid" and fname ~= "commonnameid"
            and fname ~= "playerjerseynameid" then
            local fld = r[fname]
            if type(fld) == "table" then
              fld.value = tostring(fval)
              local okf = pcall(EditDBTableField, fld)
              if okf then written = written + 1 end
            end
          end
        end
        -- Face after stats (real head on existing free-agent id — safer than CreatePlayer)
        if DEFERRED_FACE_ID and DEFERRED_FACE_ID > 0 then
          -- hashighqualityhead selects between two mesh pipelines; it is NOT an
          -- "is real face" flag and is 0 for most players (Zidane, Pele, van
          -- Basten included). Hardcoding 1 pointed the game at an HQ head that
          -- does not exist, which is why real faces came out blank.
          local face_map = {{
            headassetid = string.format("%d", DEFERRED_FACE_ID),
            hashighqualityhead = string.format("%d", FACE_HQ),
            headclasscode = "0",
          }}
          if FACE_HEADTYPE and FACE_HEADTYPE > 0 then
            face_map["headtypecode"] = string.format("%d", FACE_HEADTYPE)
          end
          if FACE_VARIATION and FACE_VARIATION >= 0 then
            face_map["headvariation"] = string.format("%d", FACE_VARIATION)
          end
          for fname, fval in pairs(face_map) do
            local fld = r[fname]
            if type(fld) == "table" then
              fld.value = fval
              pcall(EditDBTableField, fld)
            end
          end
          log_step("face_apply_ok", "face=" .. tostring(DEFERRED_FACE_ID))
        end
        -- Name dictionary detach on same players row (stats path)
        local ids = {{
          firstnameid = "0",
          lastnameid = "0",
          commonnameid = "0",
          playerjerseynameid = "0",
          usercaneditname = "1",
        }}
        for fname, fval in pairs(ids) do
          local fld = r[fname]
          if type(fld) == "table" then
            fld.value = fval
            pcall(EditDBTableField, fld)
          end
        end
        log_step("fields_done", "written=" .. tostring(written))
        return true
      end
    end
  end
  log_step("fields_miss", "pid_not_in_players")
  return false
end

local function try_dummy_overwrite()
  if #HINT_DUMMIES == 0 then
    log_step("dummy_skip", "no_HINT_DUMMIES")
    return 0, "no_hint_dummies"
  end
  log_step("dummy_enter", "candidates=" .. tostring(#HINT_DUMMIES))
  for _, pid in ipairs(HINT_DUMMIES) do
    if pid and pid > 0 and pid < GEN_MIN then
      local exists = true
      if type(PlayerExists) == "function" then
        local ok, ex = pcall(PlayerExists, pid)
        exists = ok and ex
      end
      if exists then
        log_step("dummy_pick", "pid=" .. tostring(pid))
        local okf = apply_fields_to_pid(pid)
        if not okf then
          log_step("dummy_fields_fail", "pid=" .. tostring(pid))
          -- still transfer — fields may partially apply
        end
        set_edited_name(pid)
        return pid, "dummy"
      else
        log_step("dummy_missing", "pid=" .. tostring(pid))
      end
    end
  end
  return 0, "no_dummy"
end

local function do_transfer(pid, teamid)
  if not teamid or teamid <= 0 then return false, "no_teamid" end
  if not pid or pid <= 0 then return false, "no_pid" end
  if type(TransferPlayer) ~= "function" then return false, "no_TransferPlayer" end
  log_step("transfer_enter", "pid=" .. tostring(pid) .. " team=" .. tostring(teamid))
  if type(IsPlayerPresigned) == "function" then
    local ok, pre = pcall(IsPlayerPresigned, pid)
    if ok and pre and type(DeletePresignedContract) == "function" then
      pcall(DeletePresignedContract, pid)
    end
  end
  if type(IsPlayerLoanedOut) == "function" then
    local ok, loaned = pcall(IsPlayerLoanedOut, pid)
    if ok and loaned and type(TerminateLoan) == "function" then
      pcall(TerminateLoan, pid)
    end
  end
  local ok, err = pcall(TransferPlayer, pid, teamid, TRANSFERSUM, WAGE, CONTRACT_MONTHS, 0, -1)
  log_step("transfer_done", ok and "ok" or tostring(err))
  if not ok then return false, tostring(err) end
  return true, "ok"
end
{create_block}
log_step("start", "mode=" .. MODE .. " team=" .. tostring(TARGET_TEAM) .. " v15 primary_dummy=" .. tostring(PRIMARY_DUMMY) .. " nat=" .. tostring(NATION_ID) .. " deferred_face=" .. tostring(DEFERRED_FACE_ID) .. " dummies=" .. tostring(#HINT_DUMMIES))
local ok_main, err_main = pcall(function()
  if type(IsInCM) == "function" then
    local ok, in_cm = pcall(IsInCM)
    if ok and in_cm == false then
      write_status("job=add_to_team ok=false err=not_in_career")
      log_step("abort", "not_in_career")
      return
    end
  end

  local teamid = resolve_team()
  if teamid <= 0 then
    write_status("job=add_to_team ok=false err=no_teamid")
    log_step("abort", "no_teamid")
    return
  end

  local pid, path = 0, "none"
{pick_logic}
  set_edited_name(pid)
  local tok, terr = do_transfer(pid, teamid)
  -- Re-apply fields/face after transfer (CM may refresh row), then names last
  if path == "dummy" then
    apply_fields_to_pid(pid)
  end
  set_edited_name(pid)

  local status = string.format(
    "job=add_to_team ok=%s path=%s id=%d team=%d transfer=%s err=%s name=%s",
    tostring(tok), path, pid, teamid, tostring(tok), tostring(terr or "ok"), "{_esc(name)}"
  )
  write_status(status)
  log_step("finish", status)
end)

if not ok_main then
  write_status("job=add_to_team ok=false err=lua_error msg=" .. tostring(err_main):gsub("%s+", "_"))
  log_step("lua_error", tostring(err_main))
end
"""
    return body.replace("\r\n", "\n")
