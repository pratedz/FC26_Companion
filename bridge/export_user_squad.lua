--[[ Export user senior squad → current_squad.json (silent).
     OUT_PATH is replaced by the companion with an absolute path on queue.
]]

local OUT_PATH = "LE_Profile_Executor/current_squad.json"

pcall(function() require "imports/career_mode/helpers" end)
pcall(function() require "imports/other/helpers" end)

local function json_escape(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\")
    s = s:gsub('"', '\\"')
    s = s:gsub("\r", "\\r")
    s = s:gsub("\n", "\\n")
    s = s:gsub("\t", "\\t")
    return s
end

local function write_file(path, data)
    local f = io.open(path, "w+")
    if not f then
        return false, "open failed: " .. tostring(path)
    end
    f:write(data)
    f:close()
    return true
end

local function log(msg)
    if Log then Log("[export_squad] " .. tostring(msg)) end
end

-- Soft CM check (do not hard-assert — bridge treats assert as run fail)
local in_cm = false
if type(IsInCM) == "function" then
    local ok, res = pcall(IsInCM)
    in_cm = ok and res and true or false
end
if not in_cm then
    log("Not in Career Mode — abort (load a CM save first)")
    write_file(
        OUT_PATH:gsub("current_squad%.json$", "queue/_export_status.txt"),
        "FAIL not_in_cm\n"
    )
    -- also try queue folder next to OUT_PATH parent
    return
end

local teamid = 0
if type(GetUserTeamID) == "function" then
    local ok, tid = pcall(GetUserTeamID)
    if ok and tid then teamid = tonumber(tid) or 0 end
end

local teamname = ""
if teamid > 0 and type(GetTeamName) == "function" then
    local ok, tn = pcall(GetTeamName, teamid)
    if ok and tn then teamname = tostring(tn) end
end

-- Collect player ids (use tonumber keys always)
local squad_ids = {}
if type(GetUserSeniorTeamPlayerIDs) == "function" then
    local ok, map = pcall(GetUserSeniorTeamPlayerIDs)
    if ok and type(map) == "table" then
        for pid, _ in pairs(map) do
            local n = tonumber(pid)
            if n and n > 0 then squad_ids[n] = true end
        end
    end
end

local jersey_by_id = {}
if teamid > 0 and LE and LE.db then
    local ok_tpl, tpl = pcall(function() return LE.db:GetTable("teamplayerlinks") end)
    if ok_tpl and tpl then
        local rec = tpl:GetFirstRecord()
        while rec and rec > 0 do
            local tid = tonumber(tpl:GetRecordFieldValue(rec, "teamid"))
            if tid == teamid then
                local pid = tonumber(tpl:GetRecordFieldValue(rec, "playerid"))
                local num = tonumber(tpl:GetRecordFieldValue(rec, "jerseynumber")) or 0
                if pid and pid > 0 then
                    jersey_by_id[pid] = num
                    squad_ids[pid] = true
                end
            end
            rec = tpl:GetNextValidRecord()
        end
    end
end

local by_id = {}
if LE and LE.db then
    local ok_pt, players_table = pcall(function() return LE.db:GetTable("players") end)
    if ok_pt and players_table then
        local rec = players_table:GetFirstRecord()
        while rec and rec > 0 do
            local pid = tonumber(players_table:GetRecordFieldValue(rec, "playerid"))
            if pid and squad_ids[pid] then
                local pos = tonumber(players_table:GetRecordFieldValue(rec, "preferredposition1")) or 0
                local ovr = tonumber(players_table:GetRecordFieldValue(rec, "overallrating")) or 0
                local pot = tonumber(players_table:GetRecordFieldValue(rec, "potential")) or 0
                local posname = ""
                if type(GetPlayerPrimaryPositionName) == "function" then
                    local okp, pn = pcall(GetPlayerPrimaryPositionName, pos)
                    if okp and pn then posname = tostring(pn) end
                end
                local pname = ""
                if type(GetPlayerName) == "function" then
                    local okn, nm = pcall(GetPlayerName, pid)
                    if okn and nm then pname = tostring(nm) end
                end
                by_id[pid] = {
                    playerid = pid,
                    overallrating = ovr,
                    potential = pot,
                    preferredposition1 = pos,
                    position = posname,
                    jerseynumber = jersey_by_id[pid] or 0,
                    name = pname,
                }
            end
            rec = players_table:GetNextValidRecord()
        end
    end
end

local list = {}
for _, p in pairs(by_id) do
    table.insert(list, p)
end
table.sort(list, function(a, b)
    local na = string.lower(a.name or "")
    local nb = string.lower(b.name or "")
    if na == nb then return (a.playerid or 0) < (b.playerid or 0) end
    return na < nb
end)

-- Free agents (team 111592) for SAFE add-to-team dummy overwrite (worst OVR first)
local FA_TEAM = 111592
local fa_ids = {}
if type(GetPlayerIDSForTeam) == "function" then
    local okfa, map = pcall(GetPlayerIDSForTeam, FA_TEAM)
    if okfa and type(map) == "table" then
        for pid, _ in pairs(map) do
            local n = tonumber(pid)
            if n and n > 0 and n < 460000 and not squad_ids[n] then
                fa_ids[n] = true
            end
        end
    end
end
local free_agents = {}
if LE and LE.db then
    local ok_pt, players_table = pcall(function() return LE.db:GetTable("players") end)
    if ok_pt and players_table then
        local rec = players_table:GetFirstRecord()
        local scanned = 0
        while rec and rec > 0 and scanned < 30000 do
            scanned = scanned + 1
            local pid = tonumber(players_table:GetRecordFieldValue(rec, "playerid"))
            if pid and fa_ids[pid] then
                local ovr = tonumber(players_table:GetRecordFieldValue(rec, "overallrating")) or 50
                table.insert(free_agents, {
                    playerid = pid,
                    overallrating = ovr,
                    teamid = FA_TEAM,
                    free_agent = true,
                    source = "free_agent",
                })
            end
            rec = players_table:GetNextValidRecord()
        end
    end
end
table.sort(free_agents, function(a, b)
    if (a.overallrating or 50) == (b.overallrating or 50) then
        return (a.playerid or 0) < (b.playerid or 0)
    end
    return (a.overallrating or 50) < (b.overallrating or 50)
end)
-- Cap pool for companion (worst 40)
local fa_out = {}
for i = 1, math.min(40, #free_agents) do
    fa_out[i] = free_agents[i]
end

local parts = {}
table.insert(parts, "{")
table.insert(parts, '  "mode": "career",')
table.insert(parts, string.format('  "teamid": %d,', teamid))
table.insert(parts, string.format('  "teamname": "%s",', json_escape(teamname)))
table.insert(parts, string.format('  "count": %d,', #list))
table.insert(parts, '  "players": [')
for i, p in ipairs(list) do
    local comma = (i < #list) and "," or ""
    table.insert(
        parts,
        string.format(
            '    {"playerid": %d, "name": "%s", "position": "%s", "overallrating": %d, "potential": %d, "jerseynumber": %d}%s',
            p.playerid or 0,
            json_escape(p.name),
            json_escape(p.position),
            p.overallrating or 0,
            p.potential or 0,
            p.jerseynumber or 0,
            comma
        )
    )
end
table.insert(parts, "  ],")
table.insert(parts, '  "free_agents": [')
for i, p in ipairs(fa_out) do
    local comma = (i < #fa_out) and "," or ""
    table.insert(
        parts,
        string.format(
            '    {"playerid": %d, "overallrating": %d, "teamid": %d, "free_agent": true, "source": "free_agent"}%s',
            p.playerid or 0,
            p.overallrating or 50,
            FA_TEAM,
            comma
        )
    )
end
table.insert(parts, "  ]")
table.insert(parts, "}")
table.insert(parts, "")

local body = table.concat(parts, "\n")
local ok, err = write_file(OUT_PATH, body)
if not ok then
    log("WRITE FAILED path=" .. tostring(OUT_PATH) .. " err=" .. tostring(err))
    return
end

log(string.format(
    "OK wrote %d players + %d free agents team=%s(%d) → %s",
    #list, #fa_out, teamname, teamid, OUT_PATH
))
