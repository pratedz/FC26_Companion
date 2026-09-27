"""Silent editor apply Lua (Log only, no MessageBox)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Set

from . import paths
from . import player_schema
from .card_types import CardDict


def _lua_str(s: str) -> str:
    return (
        str(s or "")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", "")
    )


def _lua_comment_safe(s: str, *, max_len: int = 80) -> str:
    """Strip sequences that can terminate a Lua long-comment --[[ ... ]]."""
    t = str(s or "").replace("\r", " ").replace("\n", " ")
    t = t.replace("]", "").replace("[", "").replace('"', "'")
    t = " ".join(t.split())
    return t[:max_len]


def generate_apply_player_lua(
    card: CardDict,
    target_playerid: int,
    *,
    enabled_categories: Optional[Iterable[str]] = None,
    message_box: bool = False,
    title: str = "Apply Player",
    name_parts: Optional[Mapping[str, Any]] = None,
) -> str:
    """Build silent apply Lua.

    name_parts optional keys: firstname, surname, commonname, playerjerseyname.
    When set, also writes editedplayernames + usercaneditname.
    """
    del message_box, title
    target = int(target_playerid)
    card = player_schema.normalize_player_card(card)
    cats: Optional[Set[str]]
    if enabled_categories is None:
        cats = None
        cat_list = "all"
    else:
        cats = set(enabled_categories)
        if not cats:
            raise ValueError("No topics checked.")
        cat_list = ", ".join(sorted(cats))

    updates = player_schema.card_to_field_updates(card, enabled_categories=cats)
    if not updates and not name_parts:
        raise ValueError("Nothing to write.")

    # Ensure usercaneditname when renaming
    name_map: Dict[str, str] = {}
    if name_parts:
        for k in ("firstname", "surname", "commonname", "playerjerseyname"):
            v = str(name_parts.get(k) or "").strip()
            if v:
                name_map[k] = v[:40] if k != "playerjerseyname" else v[:15]
        if name_map and not any(f == "usercaneditname" for f, _ in updates):
            updates.append(("usercaneditname", 1))

    if not updates and not name_map:
        raise ValueError("Nothing to write.")

    name = _lua_comment_safe(card.get("name") or "custom")
    field_rows = [f'  {{"{f}", {int(v)}}},' for f, v in updates]
    use_dev = cats is None or bool(cats & {"attributes", "ratings", "skills"})

    # Career Mode's PlayerGrowthManager keeps an XP-based copy of every growable
    # field and overwrites the players-table row, which is why edits "revert".
    # The mirror has to convert the target value into cumulative XP: the old code
    # passed the raw attribute value straight to PlayerSetValueInDevelopementPlan,
    # and since 99 XP is attribute 1 that actively corrupted the plan instead of
    # protecting the edit. growth_xp owns the curve and the field whitelist.
    growth_block = ""
    if use_dev:
        try:
            from . import growth_xp

            if growth_xp.growth_updates(updates):
                growth_block = (
                    "if found then\n"
                    + growth_xp.growth_sync_block(target, updates)
                    + "end\n"
                )
        except Exception:  # noqa: BLE001
            growth_block = ""

    write_names = bool(name_map)
    first_s = _lua_str(name_map.get("firstname") or name_map.get("commonname") or "Player")
    sur_s = _lua_str(name_map.get("surname") or name_map.get("commonname") or first_s)
    common_s = _lua_str(name_map.get("commonname") or sur_s)
    jersey_s = _lua_str(name_map.get("playerjerseyname") or sur_s)

    header = f"--[[ LE Companion editor silent | id={target} | {name} | {_lua_comment_safe(cat_list, max_len=120)} ]]"
    status_path = _lua_str(
        str((paths.queue_dir() / "_job_status.txt").resolve()).replace("\\", "/")
    )
    # Insert uses InsertDBTableRow (same as add_team / DOC.MD). CreateRecord is
    # not a reliable LE API and previously failed silently for missing name rows.
    # Only run when the players-table scan found the target (gate on `found`).
    name_block = ""
    if write_names:
        name_block = f"""
if found then
-- editedplayernames (Import Copy Name)
names_ok = pcall(function()
  local nt = LE.db:GetTable("editedplayernames")
  if nt == nil then return end
  local rec, found_n = nt:GetFirstRecord(), false
  local scanned_n = 0
  while rec > 0 and scanned_n < 8000 do
    scanned_n = scanned_n + 1
    local pid = tonumber(nt:GetRecordFieldValue(rec, "playerid"))
    if pid ~= nil and pid == target_playerid then
      found_n = true
      pcall(function() nt:SetRecordFieldValue(rec, "firstname", "{first_s}") end)
      pcall(function() nt:SetRecordFieldValue(rec, "surname", "{sur_s}") end)
      pcall(function() nt:SetRecordFieldValue(rec, "commonname", "{common_s}") end)
      pcall(function() nt:SetRecordFieldValue(rec, "playerjerseyname", "{jersey_s}") end)
      break
    end
    rec = nt:GetNextValidRecord()
  end
  if not found_n and type(InsertDBTableRow) == "function" then
    local row_data = {{
      playerid = string.format("%d", target_playerid),
      firstname = "{first_s}",
      surname = "{sur_s}",
      playerjerseyname = "{jersey_s}",
      commonname = "{common_s}",
    }}
    pcall(InsertDBTableRow, "editedplayernames", row_data)
  end
end)
end
"""

    body = f"""{header}
pcall(function() require("imports/career_mode/helpers") end)
local target_playerid = {target}
local use_dev = {"true" if use_dev else "false"}
local job_name = "{_lua_str(name)}"
local fields = {{
{chr(10).join(field_rows) if field_rows else ""}
}}
-- Status side-file is the companion's only proof of what happened. Every exit
-- path writes exactly one line, so "no file" can never be read as success.
local status_path = "{status_path}"
local function write_status(line)
  pcall(function()
    local f = io.open(status_path, "wb")
    if f then f:write(line .. "\\n") f:close() end
  end)
end
local players_table = LE.db:GetTable("players")
if players_table == nil then
  write_status(string.format(
    "job=edit id=%d ok=false found=false written=0 failed=0 scanned=0 names=%s reason=no_players_table name=%s",
    target_playerid, tostring({str(write_names).lower()}), job_name
  ))
  if Log then Log("[LE Companion] players missing") end
  return
end
local current_record = players_table:GetFirstRecord()
local found, written, used_dev, scanned = false, 0, false, 0
local failed, devfailed, names_ok = 0, 0, true
-- Cap scan: full 20k+ walks freeze FC26; stop early if not found
local MAX_SCAN = 25000
while current_record > 0 and scanned < MAX_SCAN do
  scanned = scanned + 1
  local playerid = tonumber(players_table:GetRecordFieldValue(current_record, "playerid"))
  if playerid ~= nil and playerid == target_playerid then
    found = true
    local has_dev = false
    if use_dev and type(IsInCM) == "function" then
      local okc, incm = pcall(IsInCM)
      if okc and incm and type(PlayerHasDevelopementPlan) == "function" then
        local okd, res = pcall(PlayerHasDevelopementPlan, target_playerid)
        if okd and res then has_dev = true used_dev = true end
      end
    end
    for i = 1, #fields do
      local fname, fval = fields[i][1], fields[i][2]
      -- Discarding this pcall is how written=91 used to mean 91 silent failures
      local okw = pcall(function() players_table:SetRecordFieldValue(current_record, fname, fval) end)
      if okw then
        written = written + 1
      else
        failed = failed + 1
      end
    end
    break
  end
  current_record = players_table:GetNextValidRecord()
end
{growth_block}
{name_block}
-- Do not call the players-manager reload API (freezes many FC26 Career sessions)
local reason = "ok"
if not found then
  reason = "not_found"
elseif written == 0 then
  reason = "all_writes_failed"
elseif failed > 0 then
  reason = "partial_write_failures"
elseif not names_ok then
  reason = "name_write_failed"
end
write_status(string.format(
  "job=edit id=%d ok=%s found=%s written=%d failed=%d devfailed=%d dev=%s scanned=%d names=%s names_ok=%s reason=%s name=%s",
  target_playerid, tostring(found and written > 0), tostring(found), written, failed,
  devfailed, tostring(used_dev), scanned, tostring({str(write_names).lower()}), tostring(names_ok),
  reason, job_name
))
if Log then
  if found then
    Log(string.format("[LE Companion] editor APPLIED id=%d n=%d failed=%d dev=%s names=%s %s",
      target_playerid, written, failed, tostring(used_dev), tostring({str(write_names).lower()}), job_name))
  else
    Log(string.format("[LE Companion] editor NOT FOUND id=%d scanned=%d", target_playerid, scanned))
  end
end
"""
    return body.replace("\r\n", "\n")
