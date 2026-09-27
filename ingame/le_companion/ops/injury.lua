--[[ injury.scan / injury.cure — current Career Mode squad injuries.

Use the verified FC26 / LE 26.3.5 fitness layout, or explicit host APIs and
database fields on other supported hosts. Never infer injury from low fitness.
]]

local M = {}

local SCAN_APIS = {
    "IsPlayerInjured",
    "GetPlayerInjury",
    "GetPlayerInjuryName",
    "GetPlayerInjuryType",
}

local CURE_APIS = {
    "ClearPlayerInjury",
    "SetPlayerInjury",
}

local function ok_result(data, counts)
    return {
        ok = true,
        counts = counts or {},
        data = data or {},
        failures = {},
    }
end

local function fail_result(reason, detail, counts)
    return {
        ok = false,
        reason = reason,
        detail = detail,
        counts = counts or {},
        data = {},
        failures = { { reason = reason, detail = detail or "" } },
    }
end

local function resolve_user_teamid()
    for _, name in ipairs({ "GetUserTeamID", "GetUserTeamId" }) do
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            local ok, tid = pcall(fn)
            if ok then
                tid = tonumber(tid)
                if tid and tid > 0 then return math.floor(tid) end
            end
        end
    end
    return nil
end

local function collect_ids(fn_name, arg, destination, seen)
    local fn = rawget(_G, fn_name)
    if type(fn) ~= "function" then return 0 end
    local ok, values
    if arg == nil then ok, values = pcall(fn) else ok, values = pcall(fn, arg) end
    if not ok or type(values) ~= "table" then return 0 end
    local count = 0
    for key, value in pairs(values) do
        local pid = tonumber(value)
        if not pid or pid <= 0 then pid = tonumber(key) end
        if pid and pid > 0 then
            pid = math.floor(pid)
            if not seen[pid] then
                seen[pid] = true
                destination[#destination + 1] = pid
                count = count + 1
            end
        end
    end
    return count
end

--- Same membership order as export_squad helpers (no DB fallback).
local function resolve_squad_pids()
    local teamid = resolve_user_teamid()
    if not teamid then
        return nil, nil, "no_team", "Career Mode is not ready"
    end
    local out, seen = {}, {}
    collect_ids("GetUserSeniorTeamPlayerIDs", nil, out, seen)
    if #out == 0 then
        collect_ids("GetPlayerIDSForTeam", teamid, out, seen)
        collect_ids("GetPlayerIDsForTeam", teamid, out, seen)
    end
    table.sort(out)
    return out, teamid, nil, nil
end

local function first_api(names)
    for i = 1, #names do
        local name = names[i]
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            return name, fn
        end
    end
    return nil, nil
end

local function player_name(pid)
    local fn = rawget(_G, "GetPlayerName")
    if type(fn) == "function" then
        local ok, value = pcall(fn, pid)
        if ok and value ~= nil then
            local text = tostring(value):gsub("^%s+", ""):gsub("%s+$", "")
            if text ~= "" then return text end
        end
    end
    return tostring(pid)
end

local function injury_label(pid, fallback)
    local name_fn = rawget(_G, "GetPlayerInjuryName")
    if type(name_fn) == "function" then
        local ok, value = pcall(name_fn, pid)
        if ok and value ~= nil then
            local text = tostring(value):gsub("^%s+", ""):gsub("%s+$", "")
            if text ~= "" and text ~= "0" and text:lower() ~= "none" then
                return text
            end
        end
    end
    local type_fn = rawget(_G, "GetPlayerInjuryType")
    if type(type_fn) == "function" then
        local ok, value = pcall(type_fn, pid)
        if ok and value ~= nil then
            local text = tostring(value):gsub("^%s+", ""):gsub("%s+$", "")
            if text ~= "" and text ~= "0" and text:lower() ~= "none" then
                return text
            end
        end
    end
    return fallback or "injured"
end

local function empty_injury_text(value)
    if value == nil or value == false then return true end
    if type(value) == "string" then
        local text = value:gsub("^%s+", ""):gsub("%s+$", "")
        return text == "" or text == "0" or text:lower() == "none"
    end
    local n = tonumber(value)
    if n ~= nil then return n <= 0 end
    return false
end

local function check_injured(api_name, fn, pid)
    local ok, value = pcall(fn, pid)
    if not ok then return false, nil end
    if api_name == "IsPlayerInjured" then
        if value == true or tonumber(value) == 1 then
            return true, injury_label(pid, "injured")
        end
        return false, nil
    end
    if api_name == "GetPlayerInjuryName" then
        if empty_injury_text(value) then return false, nil end
        return true, tostring(value)
    end
    if api_name == "GetPlayerInjuryType" then
        if empty_injury_text(value) then return false, nil end
        return true, injury_label(pid, tostring(value))
    end
    -- GetPlayerInjury: nonzero / non-empty / table means injured.
    if type(value) == "table" then
        return true, injury_label(pid, "injured")
    end
    if empty_injury_text(value) then return false, nil end
    if type(value) == "string" then
        return true, value
    end
    return true, injury_label(pid, "injured")
end

local function scan_injured(pids, api_name, fn)
    local players = {}
    for i = 1, #pids do
        local pid = pids[i]
        local injured, label = check_injured(api_name, fn, pid)
        if injured then
            players[#players + 1] = {
                playerid = pid,
                name = player_name(pid),
                injury = label or "injured",
            }
        end
    end
    return players
end

local function restore_fitness(pid)
    local fn = rawget(_G, "SetPlayerFitness")
    if type(fn) ~= "function" then return end
    pcall(fn, pid, 100)
end

local function cure_one(pid)
    local clear = rawget(_G, "ClearPlayerInjury")
    if type(clear) == "function" then
        local ok = pcall(clear, pid)
        if ok then
            restore_fitness(pid)
            return true
        end
    end
    local set = rawget(_G, "SetPlayerInjury")
    if type(set) == "function" then
        local ok = pcall(set, pid, 0)
        if not ok then ok = pcall(set, pid, "none") end
        if ok then
            restore_fitness(pid)
            return true
        end
    end
    return false
end

local function ensure_helpers()
    pcall(require, "imports/career_mode/helpers")
end

local DB_INJURY_FIELDS = {
    "injury", "playerinjury", "injurytype", "injuryname",
    "injurypart", "injury_severity", "injuries",
}

local function find_fn(root, key)
    if type(root) ~= "table" or type(key) ~= "string" then return nil end
    local fn = rawget(root, key)
    if type(fn) == "function" then return fn end
    return nil
end

--- Host functions may live on _G or on the LE table, under a name this
--- build never published as IsPlayerInjured.
local function resolve_scan_api()
    local api_name, fn = first_api(SCAN_APIS)
    if api_name then return api_name, fn end
    for r = 1, 2 do
        local root = r == 1 and _G or rawget(_G, "LE")
        for i = 1, #SCAN_APIS do
            fn = find_fn(root, SCAN_APIS[i])
            if fn then return SCAN_APIS[i], fn end
        end
    end
    return nil, nil
end

local function native_reader()
    local ok, reader = pcall(require, "ops.fitness_injuries")
    if ok and reader.supported() then return reader end
    return nil
end

local INJURY_TABLES = {
    "player_injuries", "playerinjuries", "career_playerinjuries",
    "career_injuries", "injuries", "players",
}

local function host_call(name, ...)
    local fn = rawget(_G, name)
    if type(fn) ~= "function" then return nil end
    local ok, value = pcall(fn, ...)
    if not ok then return nil end
    return value
end

local function as_list(value)
    if type(value) ~= "table" then return {} end
    if #value > 0 then return value end
    local out = {}
    for _, item in pairs(value) do
        out[#out + 1] = item
    end
    return out
end

local function field_names(tname)
    local rows = as_list(host_call("GetDBTableFields", tname))
    local names = {}
    for i = 1, #rows do
        local item = rows[i]
        local name = type(item) == "table" and item.name or item
        if type(name) == "string" and name ~= "" then
            names[#names + 1] = name
        end
    end
    if #names > 0 then return names end
    local db = require("db")
    local known = db.table_fields and db.table_fields(tname) or nil
    if type(known) ~= "table" then return {} end
    for name in pairs(known) do
        if type(name) == "string" then names[#names + 1] = name end
    end
    table.sort(names)
    return names
end

local function names_with(names, needle)
    local found = {}
    for i = 1, #names do
        if names[i]:lower():find(needle, 1, true) then
            found[#found + 1] = names[i]
        end
    end
    return found
end

local function injury_columns(tname)
    local names = field_names(tname)
    local found = {}
    for i = 1, #DB_INJURY_FIELDS do
        for j = 1, #names do
            if names[j]:lower() == DB_INJURY_FIELDS[i] then
                found[#found + 1] = names[j]
            end
        end
    end
    if #found > 0 then return found end
    return names_with(names, "injur")
end

local function injury_sources()
    local tables = as_list(host_call("GetDBTablesNames"))
    local wanted = {}
    for i = 1, #INJURY_TABLES do wanted[INJURY_TABLES[i]] = true end
    for i = 1, #tables do
        local name = tables[i]
        if type(name) == "string" and name:lower():find("injur", 1, true) then
            wanted[name] = true
        end
    end
    local sources = {}
    for name in pairs(wanted) do
        local columns = injury_columns(name)
        if #columns > 0 then
            sources[#sources + 1] = { table = name, fields = columns }
        end
    end
    table.sort(sources, function(a, b) return a.table < b.table end)
    return sources, tables
end

local function injury_fields()
    local sources = injury_sources()
    for i = 1, #sources do
        if sources[i].table == "players" then return sources[i].fields end
    end
    if sources[1] then return sources[1].fields end
    return {}
end

local function label_from_fields(handle, rec, fields)
    local parts = {}
    for i = 1, #fields do
        local value = handle:get(fields[i], rec)
        if not empty_injury_text(value) then
            parts[#parts + 1] = tostring(value)
        end
    end
    if #parts == 0 then return nil end
    return table.concat(parts, " ")
end

local function scan_db(pids, sources)
    local db = require("db")
    local want = {}
    for i = 1, #pids do want[pids[i]] = true end
    local players = {}
    local seen = {}
    for s = 1, #sources do
        local source = sources[s]
        local handle = db.open(source.table)
        local names = field_names(source.table)
        local has_pid = false
        for i = 1, #names do
            if names[i]:lower() == "playerid" then has_pid = true end
        end
        if handle and has_pid then
            for rec in handle:records() do
                local pid = tonumber(handle:get("playerid", rec))
                if pid then
                    pid = math.floor(pid)
                    if want[pid] and not seen[pid] then
                        local label = label_from_fields(handle, rec, source.fields)
                        if label then
                            seen[pid] = true
                            players[#players + 1] = {
                                playerid = pid,
                                name = player_name(pid),
                                injury = label,
                            }
                        end
                    end
                end
            end
        end
    end
    return players
end

local function write_value_for(field_name)
    local db = require("db")
    local spec = db.field("players", field_name)
    if spec and tonumber(spec.max_string_len) and tonumber(spec.max_string_len) > 0 then
        return ""
    end
    return 0
end

local function cure_db(pid, sources)
    local db = require("db")
    local wrote = false
    for s = 1, #sources do
        local source = sources[s]
        local handle, rec = db.resolve(source.table, "playerid", pid)
        if handle and rec then
            for i = 1, #source.fields do
                local field_name = source.fields[i]
                local ok = handle:set(field_name, rec, write_value_for(field_name))
                if not ok and handle.t and type(handle.t.SetRecordFieldValue) == "function" then
                    ok = pcall(handle.t.SetRecordFieldValue, handle.t, rec, field_name, 0)
                end
                if ok then wrote = true end
            end
        end
    end
    if wrote then restore_fitness(pid) end
    return wrote
end

function M.scan(op, job, _ctx)
    ensure_helpers()
    if job and job.dry_run then
        return ok_result({ dry_run = true, players = {} }, { targets = 0, found = 0 })
    end
    local pids, teamid, reason, detail = resolve_squad_pids()
    if not pids then
        return fail_result(reason or "no_team", detail or "Career Mode is not ready")
    end
    if #pids == 0 then return fail_result("no_team", "No current squad is available; refresh your squad first") end
    local native = native_reader()
    if native then
        local data, err = native.scan(pids)
        if not data then return fail_result("injury_read_failed", err) end
        data.teamid = teamid
        for _, player in ipairs(data.players) do player.name = player_name(player.playerid) end
        table.sort(data.players, function(a, b) return a.name:lower() < b.name:lower() end)
        return ok_result(data, { targets = #pids, found = #data.players })
    end
    local api_name, fn = resolve_scan_api()
    local players, scan_api
    if api_name then
        players = scan_injured(pids, api_name, fn)
        scan_api = api_name
    else
        local sources = injury_sources()
        if #sources == 0 then
            return fail_result(
                "unsupported_op",
                "This Live Editor build does not expose injury status to Lua or "
                    .. "the game database. Review injuries in Live Editor's "
                    .. "Player Editor; its Heal Player action can clear them."
            )
        end
        players = scan_db(pids, sources)
        local labels = {}
        for i = 1, #sources do
            labels[#labels + 1] = sources[i].table
        end
        scan_api = table.concat(labels, "+")
    end
    return ok_result(
        {
            players = players,
            teamid = teamid,
            scan_api = scan_api,
            covered = #pids,
        },
        {
            targets = #pids,
            found = #players,
        }
    )
end

function M.cure(op, job, _ctx)
    ensure_helpers()
    if job and job.dry_run then
        return ok_result({ dry_run = true, cured = {} }, { targets = 0, side_effects = 0 })
    end
    local native = native_reader()
    if native then
        local pids, teamid, reason, detail = resolve_squad_pids()
        if not pids or #pids == 0 then return fail_result(reason or "no_team", detail or "No current squad is available") end
        local squad, seen, targets = {}, {}, {}
        for _, pid in ipairs(pids) do squad[pid] = true end
        local requested = type(op.playerids) == "table" and op.playerids or {}
        if #requested == 0 then requested = pids end
        for _, value in ipairs(requested) do
            local pid = tonumber(value)
            if pid and pid == math.floor(pid) and squad[pid] and not seen[pid] then
                targets[#targets + 1] = pid; seen[pid] = true
            end
        end
        if #targets == 0 then return fail_result("no_targets", "The selected players are no longer in your squad; scan again") end
        local result, err = native.cure(targets)
        if not result then return fail_result("injury_cure_failed", err) end
        local names = {}
        for _, pid in ipairs(result.cured_ids) do names[#names + 1] = player_name(pid) end
        local data = { cured = names, cured_ids = result.cured_ids,
            failed_ids = result.failed_ids, already_clear = result.already_clear,
            teamid = teamid, cure_api = "FC26 verified native injury reset" }
        local counts = { targets = #targets, found = #names + #result.failed_ids,
            side_effects = #names, fields_failed = #result.failed_ids }
        if #result.failed_ids > 0 then
            local failure = fail_result(#names > 0 and "partial_write" or "no_writes",
                table.concat(result.failures, "; "), counts)
            failure.data = data
            return failure
        end
        return ok_result(data, counts)
    end
    local cure_name = first_api(CURE_APIS)
    local sources = injury_sources()
    local fields = {}
    if sources[1] then fields = sources[1].fields end
    if not cure_name and #sources == 0 then
        return fail_result(
            "unsupported_op",
            "This Live Editor build has no injury clearing API or database field."
        )
    end
    local pids, teamid, reason, detail = resolve_squad_pids()
    if not pids then
        return fail_result(reason or "no_team", detail or "Career Mode is not ready")
    end

    local requested = {}
    local raw = type(op.playerids) == "table" and op.playerids or {}
    for i = 1, #raw do
        local pid = tonumber(raw[i])
        if pid and pid > 0 then
            requested[#requested + 1] = math.floor(pid)
        end
    end

    local targets
    if #requested == 0 then
        local scan_name, scan_fn = resolve_scan_api()
        local injured
        if scan_name then
            injured = scan_injured(pids, scan_name, scan_fn)
        elseif #sources > 0 then
            injured = scan_db(pids, sources)
        else
            return fail_result(
                "unsupported_op",
                "injury.cure: cannot see who is injured on this host"
            )
        end
        targets = {}
        for i = 1, #injured do
            targets[#targets + 1] = injured[i].playerid
        end
    else
        local squad = {}
        for i = 1, #pids do squad[pids[i]] = true end
        targets = {}
        for i = 1, #requested do
            local pid = requested[i]
            if squad[pid] then targets[#targets + 1] = pid end
        end
    end

    local cured = {}
    local failed = 0
    for i = 1, #targets do
        local pid = targets[i]
        local cleared = cure_name and cure_one(pid) or false
        if not cleared and #sources > 0 then
            cleared = cure_db(pid, sources)
        end
        if cleared then
            cured[#cured + 1] = player_name(pid)
        else
            failed = failed + 1
        end
    end

    local data = {
        cured = cured,
        teamid = teamid,
        cure_api = cure_name or ("players." .. table.concat(fields, "+")),
    }
    local counts = {
        targets = #targets,
        found = #targets,
        side_effects = #cured,
        fields_failed = failed,
    }
    if #targets > 0 and #cured == 0 then
        return fail_result(
            "no_writes",
            "injury.cure cleared 0 injuries across " .. #targets .. " player(s)",
            counts
        )
    end
    if failed > 0 then
        local r = fail_result(
            "partial_write",
            string.format("%d injury clear(s) failed", failed),
            counts
        )
        r.data = data
        return r
    end
    return ok_result(data, counts)
end

return M
