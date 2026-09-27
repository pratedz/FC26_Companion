--[[ le_companion/ops.lua — op registry + handlers.

KNOWN_OPS stay in lockstep with companion/domain/job.py and version.OP_VERSIONS.
Unknown ops become clean unsupported_op rejections — never a Lua error.
]]

local util = require("util")
local version = require("version")
local db = require("db")

local ops = {}
ops.handlers = {}
local path_allowed

local function ok_result(data, counts)
    return {
        ok = true,
        counts = counts or {},
        data = data or {},
        failures = {},
    }
end

local function fail_result(reason, detail, counts, failures)
    return {
        ok = false,
        reason = reason,
        detail = detail,
        counts = counts or {},
        data = {},
        failures = failures or { { reason = reason, detail = detail or "" } },
    }
end

-------------------------------------------------------------------------------
-- diag.ping — pure; proves claim + attribution with zero side effects
-------------------------------------------------------------------------------

ops.handlers["diag.ping"] = function(op, job, ctx)
    -- Do not put ops_ok in counts — the runner owns that tally.
    return ok_result({
        note = op.note or "",
        core_version = version.VERSION,
        session_id = ctx.session_id,
        queue_dir = ctx.queue_dir,
        at = util.now(),
        contract = version.CONTRACT,
    }, {})
end

-------------------------------------------------------------------------------
-- set_fields — verified write via db.set_many
-------------------------------------------------------------------------------

local PLAN_FIELDS = {
    acceleration=true, sprintspeed=true, finishing=true, shotpower=true,
    longshots=true, shortpassing=true, longpassing=true, ballcontrol=true,
    dribbling=true, composure=true, standingtackle=true, stamina=true,
    strength=true, reactions=true, agility=true, balance=true,
    vision=true, crossing=true, curve=true, interceptions=true,
    headingaccuracy=true, jumping=true, aggression=true, positioning=true,
    volleys=true, penalties=true, freekickaccuracy=true,
    -- Canonical players-table/editor spelling. Keep marking for old saves.
    defensiveawareness=true, marking=true,
    slidingtackle=true, skillmoves=true, weakfootabilitytypecode=true,
    gkdiving=true, gkhandling=true, gkkicking=true,
    gkpositioning=true, gkreflexes=true,
}

local function growth_field_count(fields)
    local n = 0
    for name, value in pairs(fields or {}) do
        if PLAN_FIELDS[name] and type(value) == "number" then n = n + 1 end
    end
    return n
end

local function mirror_growth(playerid, fields, mode)
    if mode == "off" then return 0, 0, 0 end
    local setter = rawget(_G, "PlayerSetValueInDevelopementPlan")
    if type(setter) ~= "function" then
        return 0, growth_field_count(fields), 0
    end
    local has_plan = true
    local checker = rawget(_G, "PlayerHasDevelopementPlan")
    if mode ~= "force" and type(checker) == "function" then
        local okp, value = pcall(checker, tonumber(playerid))
        has_plan = okp and value and true or false
    end
    if not has_plan then return 0, 0, growth_field_count(fields) end

    local mirrored, failed = 0, 0
    for name, value in pairs(fields or {}) do
        if PLAN_FIELDS[name] and type(value) == "number" then
            local ok, returned = pcall(setter, tonumber(playerid), name, value)
            if ok and returned ~= false then
                mirrored = mirrored + 1
            else
                failed = failed + 1
            end
        end
    end
    return mirrored, failed, 0
end

ops.handlers["set_fields"] = function(op, job, ctx)
    local table_name = op.table or "players"
    local key = op.key or {}
    local key_field = key.field or "playerid"
    local key_value = key.value
    local fields = op.fields or {}
    if util.is_empty(fields) then
        return fail_result("no_fields", "set_fields with empty fields map")
    end
    if key_value == nil then
        return fail_result("no_key", "set_fields missing key.value")
    end

    local n_req = util.count(fields)
    if job.dry_run or op.dry_run then
        return ok_result({ dry_run = true, table = table_name, key = key, fields = fields }, {
            fields_requested = n_req,
            fields_written = 0,
            fields_failed = 0,
            found = 0,
        })
    end

    if not db.available() and not rawget(_G, "LE") then
        return fail_result("no_db", "database not available (Career Mode save not loaded?)", {
            fields_requested = n_req,
            fields_written = 0,
            fields_failed = n_req,
            found = 0,
        })
    end

    local h, rec, state, matches = db.resolve(table_name, key_field, key_value)
    if h == nil then
        return fail_result("no_table", tostring(state or "no_such_table"), {
            fields_requested = n_req, fields_written = 0, fields_failed = 0, found = 0,
        })
    end
    if rec == nil then
        if op.upsert and table_name == "editedplayernames" then
            -- FC's edited-name table expects the player id as text when it
            -- creates a row, although numeric lookup remains supported.
            local insert_key = key_value
            if key_field == "playerid" then insert_key = tostring(key_value) end
            local row_data = { [key_field] = insert_key }
            for k, v in pairs(fields) do row_data[k] = v end
            local row, ierr = db.insert_row(table_name, row_data)
            -- The returned address is unreliable in some Live Editor builds.
            -- Always prove the row exists by looking it up again.
            db.reset()
            h, rec, state, matches = db.resolve(table_name, key_field, key_value)
            if not h or not rec then
                return fail_result("insert_unverified", tostring(ierr or state or "insert_not_visible"), {
                    fields_requested = n_req, fields_written = 0,
                    fields_failed = n_req, found = 0, side_effects = row and 1 or 0,
                })
            end
            local iwritten, ifailures = db.set_many(h, rec, fields, {
                on_field_error = op.on_field_error or "continue",
            })
            if #ifailures > 0 or iwritten ~= n_req then
                local r = fail_result("partial_write",
                    string.format("%d/%d inserted fields verified", iwritten, n_req), {
                        fields_requested = n_req, fields_written = iwritten,
                        fields_failed = #ifailures, found = 1, side_effects = 1,
                    }, ifailures)
                r.data = { upsert = "inserted", table = table_name }
                return r
            end
            return ok_result({ upsert = "inserted", table = table_name }, {
                fields_requested = n_req, fields_written = iwritten,
                fields_failed = 0, found = 1, side_effects = 1,
            })
        end
        if op.require_found ~= false then
            return fail_result("not_found",
                string.format("%s=%s not in %s", tostring(key_field), tostring(key_value), table_name), {
                    fields_requested = n_req, fields_written = 0, fields_failed = 0, found = 0, missing = 1,
                })
        end
        return ok_result({ found = false }, {
            fields_requested = n_req, fields_written = 0, fields_failed = 0, found = 0, missing = 1,
        })
    end

    local displaced = nil
    if table_name == "teamplayerlinks" and tonumber(op.teamid) and tonumber(op.teamid) > 0 then
        local want_team = math.floor(tonumber(op.teamid))
        local exact = nil
        for candidate in h:records() do
            if tonumber(h:get("playerid", candidate)) == tonumber(key_value)
                and tonumber(h:get("teamid", candidate)) == want_team then
                exact = candidate
                break
            end
        end
        if exact then rec = exact end
        local new_no = tonumber(fields.jerseynumber)
        -- Park the current wearer on a free number before this write. Moving
        -- them onto this player's old shirt duplicates that number until the
        -- second write, and Career Mode crashes as soon as it sees the share.
        if new_no and new_no >= 1 and new_no <= 99 and rec then
            local used = {}
            for candidate in h:records() do
                if tonumber(h:get("teamid", candidate)) == want_team then
                    local worn = tonumber(h:get("jerseynumber", candidate))
                    if worn and worn >= 1 and worn <= 99 and used[worn] == nil then
                        used[worn] = candidate
                    end
                end
            end
            local wanted = math.floor(new_no)
            local other = used[wanted]
            if other and other ~= rec then
                local replacement = nil
                for n = 1, 99 do
                    if used[n] == nil then replacement = n break end
                end
                if not replacement then
                    return fail_result("no_spare_shirt",
                        "every shirt number 1-99 is already used on this team")
                end
                local moved, move_fail = db.set_many(h, other, { jerseynumber = replacement }, {
                    on_field_error = "abort",
                })
                if moved ~= 1 or #move_fail > 0 then
                    return fail_result("shirt_displace_failed",
                        "could not move the player who already wears that number")
                end
                displaced = { from = wanted, to = replacement }
            end
        end
    end

    if op.upsert and (matches or 0) > 1 then
        return fail_result("duplicate_rows",
            string.format("%s contains %d rows with %s=%s; refusing an ambiguous update",
                table_name, matches, tostring(key_field), tostring(key_value)), {
                fields_requested = n_req, fields_written = 0,
                fields_failed = n_req, found = matches, duplicates = matches - 1,
            })
    end

    local written, failures = db.set_many(h, rec, fields, {
        on_field_error = op.on_field_error or "continue",
    })

    -- Growth mirror (best-effort; never fails the whole op alone).
    local growth_ok, growth_fail, growth_no_plan = 0, 0, 0
    local gm = op.growth_mirror or "auto"
    if table_name == "players" then
        if #failures == 0 and written == n_req then
            growth_ok, growth_fail, growth_no_plan =
                mirror_growth(key_value, fields, gm)
        else
            growth_fail = growth_field_count(fields)
        end
    end

    local counts = {
        fields_requested = n_req,
        fields_written = written,
        fields_failed = #failures,
        found = 1,
        growth_mirrored = growth_ok,
        growth_failed = growth_fail,
        growth_no_plan = growth_no_plan,
    }
    if #failures == 0 and written == n_req
        and (growth_fail > 0 or growth_no_plan > 0) then
        return fail_result(
            "growth_mirror_incomplete",
            string.format(
                "player fields were written, but %d growth field(s) failed and "
                .. "%d had no development plan",
                growth_fail, growth_no_plan),
            counts)
    end
    local all_ok = (#failures == 0) and written == n_req
    if all_ok then
        local data = { table = table_name, key = key, resolve = state }
        if displaced then data.displaced_shirt = displaced end
        return ok_result(data, counts)
    end
    local r = fail_result("partial_write", string.format("%d/%d fields verified", written, n_req), counts, failures)
    r.ok = false
    return r
end

-------------------------------------------------------------------------------
-- export_squad
-------------------------------------------------------------------------------

ops.handlers["export_squad"] = function(op, job, ctx)
    -- Do not touch a Career table while FC is still moving through its loading
    -- screens. Live Editor returns numeric 0 for "no user club"; Lua considers
    -- 0 truthy, so it must be rejected explicitly rather than as a nil check.
    -- Opening Companion on the main menu used to call GetUserTeamID here and
    -- crash the game. A CareerModeEvent must have fired first.
    local ready_fn = rawget(_G, "LEC_CAREER_SAVE_READY")
    local save_ready = type(ready_fn) == "function" and ready_fn() == true
    if not save_ready then
        return fail_result(
            "career_required",
            "Career Mode save is not loaded",
            { targets = 0 }
        )
    end
    local teamid = tonumber(op.teamid)
    if (not teamid or teamid <= 0) and type(rawget(_G, "GetUserTeamID")) == "function" then
        local ok, tid = pcall(_G.GetUserTeamID)
        if ok then teamid = tonumber(tid) end
    end
    if not teamid or teamid <= 0 then
        if job.dry_run then
            return ok_result({ dry_run = true, players = {}, teamid = nil }, { targets = 0 })
        end
        return fail_result("no_team", "Career Mode is not ready: no user team id yet", { targets = 0 })
    end
    teamid = math.floor(teamid)

    local players, free_agents = {}, {}
    local squad_ids, jersey_by_id = {}, {}
    local edited_names = {}
    local diagnostics = {
        template_candidates = 0,
        accepted = 0,
        rejected_not_free = 0,
        rejected_presigned_or_loaned = 0,
        rejected_rating_or_retirement = 0,
        host_checks_unavailable = 0,
    }

    local function host_yes(value)
        return value == true or tonumber(value) == 1
    end

    local function collect_ids(fn_name, arg, destination)
        local fn = rawget(_G, fn_name)
        if type(fn) ~= "function" then return 0 end
        local ok, values
        if arg == nil then ok, values = pcall(fn) else ok, values = pcall(fn, arg) end
        if not ok or type(values) ~= "table" then return 0 end
        local count = 0
        for key, value in pairs(values) do
            local pid = tonumber(value)
            if not pid or pid <= 0 then pid = tonumber(key) end
            if pid and pid > 0 and not destination[pid] then
                destination[pid] = true
                count = count + 1
            end
        end
        return count
    end

    -- Helpers are the normal, cheap path. Only if this host exposes neither
    -- helper do we use teamplayerlinks, and even then read fields known to be
    -- present on the table (never the missing `leagueid` field).
    collect_ids("GetUserSeniorTeamPlayerIDs", nil, squad_ids)
    if next(squad_ids) == nil then
        collect_ids("GetPlayerIDSForTeam", teamid, squad_ids)
        collect_ids("GetPlayerIDsForTeam", teamid, squad_ids)
    end
    if next(squad_ids) == nil then
        local links = db.open("teamplayerlinks")
        if links then
            local has_jersey = db.field("teamplayerlinks", "jerseynumber") ~= nil
            for rec in links:records() do
                local tid = tonumber(links:get("teamid", rec))
                local pid = tonumber(links:get("playerid", rec))
                if tid == teamid and pid and pid > 0 then
                    squad_ids[pid] = true
                    if has_jersey then
                        jersey_by_id[pid] = tonumber(links:get("jerseynumber", rec)) or 0
                    end
                end
            end
        end
    end

    -- Shirt numbers live on teamplayerlinks even when the squad id list came
    -- from GetUserSeniorTeamPlayerIDs. Without this pass every shirt is 0.
    if next(jersey_by_id) == nil and db.field("teamplayerlinks", "jerseynumber") ~= nil then
        local links = db.open("teamplayerlinks")
        if links then
            for rec in links:records() do
                local tid = tonumber(links:get("teamid", rec))
                local pid = tonumber(links:get("playerid", rec))
                if tid == teamid and pid and squad_ids[pid] then
                    jersey_by_id[pid] = tonumber(links:get("jerseynumber", rec)) or 0
                end
            end
        end
    end

    if next(squad_ids) == nil then
        return {
            ok = false,
            deferred = true,
            awaiting_event = true,
            reason = "db_not_ready",
            detail = "team " .. tostring(teamid) .. " membership is not readable yet",
            counts = { targets = 0, found = 0 },
            progress = {
                step = 1, attempts = 0, started_at = util.now(),
                reason = "db_not_ready", teamid = teamid,
                counts = { targets = 0, found = 0 },
            },
        }
    end

    -- Known low-rated IDs are only a fallback. The free-agent team list is the
    -- real pool: a save can sign the hardcoded names away, which is why the
    -- counter was stuck around 9. Live checks still reject anyone who is no
    -- longer free, loaned, presigned, retiring, or above 75 overall.
    local FREE_AGENT_TEAM_ID = 111592
    local FREE_AGENT_KEEP = 20
    local FREE_AGENT_CHECK_CAP = 120
    local template_ids = {}
    local free_ids = {}
    local raw_template = type(op.free_agent_candidates) == "table" and op.free_agent_candidates or {}
    for i = 1, math.min(#raw_template, 30) do
        local pid = tonumber(raw_template[i])
        if pid and pid > 0 and pid <= 459999 and not squad_ids[pid] then
            template_ids[math.floor(pid)] = true
        end
    end
    collect_ids("GetPlayerIDSForTeam", FREE_AGENT_TEAM_ID, free_ids)
    collect_ids("GetPlayerIDsForTeam", FREE_AGENT_TEAM_ID, free_ids)

    local ph = db.open("players")
    if not ph then
        return fail_result("players_table_unavailable", "could not open players table")
    end
    local teamname = ""
    if type(rawget(_G, "GetTeamName")) == "function" then
        local ok, value = pcall(_G.GetTeamName, teamid)
        if ok and value then teamname = tostring(value) end
    end

    -- GetPlayerName can remain cached until the Career screen is reopened.
    -- The Companion writes a player's requested name into editedplayernames,
    -- so prefer that durable row during every squad refresh. This keeps the
    -- desktop Player tab in sync as soon as Refresh squad completes.
    local names = db.open("editedplayernames")
    if names and db.field("editedplayernames", "playerid") then
        local has_first = db.field("editedplayernames", "firstname") ~= nil
        local has_surname = db.field("editedplayernames", "surname") ~= nil
        local has_common = db.field("editedplayernames", "commonname") ~= nil
        for rec in names:records() do
            local pid = tonumber(names:get("playerid", rec))
            if pid and squad_ids[pid] then
                local common = has_common and tostring(names:get("commonname", rec) or "") or ""
                local first = has_first and tostring(names:get("firstname", rec) or "") or ""
                local surname = has_surname and tostring(names:get("surname", rec) or "") or ""
                local display = common ~= "" and common
                    or (first .. " " .. surname):gsub("^%s+", ""):gsub("%s+$", "")
                if display ~= "" then edited_names[pid] = display end
            end
        end
    end

    local function current_free_teamid(pid)
        local exists_fn = rawget(_G, "PlayerExists")
        local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
        if type(exists_fn) ~= "function" or type(team_fn) ~= "function" then
            diagnostics.host_checks_unavailable = diagnostics.host_checks_unavailable + 1
            return nil
        end
        local ok_exists, raw_exists = pcall(exists_fn, pid)
        if not ok_exists or not host_yes(raw_exists) then return nil end
        local ok_team, raw_teamid = pcall(team_fn, pid)
        local live_tid = ok_team and tonumber(raw_teamid) or nil
        if live_tid == 0 or live_tid == 111592 then return live_tid end
        diagnostics.rejected_not_free = diagnostics.rejected_not_free + 1
        return nil
    end

    local function clean_free_candidate(pid, prec)
        for _, fn_name in ipairs({ "IsPlayerPresigned", "IsPlayerLoanedOut" }) do
            local fn = rawget(_G, fn_name)
            if type(fn) ~= "function" then
                diagnostics.host_checks_unavailable = diagnostics.host_checks_unavailable + 1
                return false
            end
            local ok, active = pcall(fn, pid)
            if not ok then
                diagnostics.host_checks_unavailable = diagnostics.host_checks_unavailable + 1
                return false
            end
            if host_yes(active) then
                diagnostics.rejected_presigned_or_loaned = diagnostics.rejected_presigned_or_loaned + 1
                return false
            end
        end
        local rating = tonumber(ph:get("overallrating", prec))
        if db.field("players", "isretiring") == nil then
            diagnostics.host_checks_unavailable = diagnostics.host_checks_unavailable + 1
            return false
        end
        local retiring = tonumber(ph:get("isretiring", prec))
        if not rating or rating > 75 or retiring == nil or retiring ~= 0 then
            diagnostics.rejected_rating_or_retirement = diagnostics.rejected_rating_or_retirement + 1
            return false
        end
        return true, rating
    end

    local free_candidates, seen_free = {}, {}
    local free_checks = 0
    local function add_free_candidate(pid, prec)
        if seen_free[pid] or free_checks >= FREE_AGENT_CHECK_CAP then return end
        if not pid or pid <= 0 or pid > 459999 or squad_ids[pid] then return end
        local rating_now = tonumber(ph:get("overallrating", prec))
        if not rating_now or rating_now > 75 then
            diagnostics.rejected_rating_or_retirement = diagnostics.rejected_rating_or_retirement + 1
            return
        end
        free_checks = free_checks + 1
        diagnostics.template_candidates = diagnostics.template_candidates + 1
        local live_tid = current_free_teamid(pid)
        if not live_tid then return end
        local clean, rating = clean_free_candidate(pid, prec)
        if not clean then return end
        seen_free[pid] = true
        local entry = {
            playerid = pid, teamid = live_tid, overallrating = rating,
            source = "free_agent_template",
        }
        if type(rawget(_G, "GetPlayerName")) == "function" then
            local ok, name = pcall(_G.GetPlayerName, pid)
            if ok and name then entry.name = tostring(name) end
        end
        free_candidates[#free_candidates + 1] = entry
        diagnostics.accepted = diagnostics.accepted + 1
    end

    -- One valid-record pass remains necessary for safe DB record addresses,
    -- but all native free-agent checks are bounded to the explicit 30 IDs.
    for prec in ph:records() do
        local pid = tonumber(ph:get("playerid", prec))
        if pid and squad_ids[pid] then
            local entry = {
                playerid = pid, teamid = teamid, teamname = teamname,
                jerseynumber = jersey_by_id[pid] or 0,
            }
            for _, fname in ipairs({
                "overallrating", "potential", "preferredposition1",
                "nationality", "height", "weight",
            }) do
                if db.field("players", fname) then
                    local value = ph:get(fname, prec)
                    if value ~= nil then entry[fname] = value end
                end
            end
            if edited_names[pid] then
                entry.name = edited_names[pid]
            elseif type(rawget(_G, "GetPlayerName")) == "function" then
                local ok, name = pcall(_G.GetPlayerName, pid)
                if ok and name then entry.name = tostring(name) end
            end
            if type(rawget(_G, "GetPlayerPrimaryPositionName")) == "function" then
                local ok, position = pcall(_G.GetPlayerPrimaryPositionName, entry.preferredposition1 or 0)
                if ok and position then entry.position = tostring(position) end
            end
            players[#players + 1] = entry
        elseif pid and (template_ids[pid] or free_ids[pid]) then
            add_free_candidate(pid, prec)
        end
    end

    table.sort(free_candidates, function(a, b)
        if a.overallrating == b.overallrating then return a.playerid < b.playerid end
        return a.overallrating < b.overallrating
    end)
    for i = 1, math.min(FREE_AGENT_KEEP, #free_candidates) do free_agents[i] = free_candidates[i] end

    if #players == 0 then
        return {
            ok = false, deferred = true, awaiting_event = true,
            reason = "db_not_ready",
            detail = "team " .. tostring(teamid) .. " membership is not readable yet",
            counts = { targets = 0, found = 0 },
            progress = {
                step = 1, attempts = 0, started_at = util.now(),
                reason = "db_not_ready", teamid = teamid,
                counts = { targets = 0, found = 0 },
            },
        }
    end

    local out_path = op.out_path
    if type(out_path) == "string" and out_path ~= "" then
        local allowed = false
        local roots = (ctx.cfg and ctx.cfg.allowed_write_roots) or {}
        local norm = util.normalize(out_path)
        for i = 1, #roots do
            local root = util.normalize(roots[i]):lower()
            if util.startswith(norm:lower(), root) then allowed = true; break end
        end
        if allowed then
            local json = require("json")
            local text = json.try_encode({ teamid = teamid, players = players, at = util.now() })
            if text then util.write_atomic(out_path, text) end
        end
    end

    return ok_result({
        teamid = teamid, teamname = teamname, players = players,
        free_agents = free_agents, free_agent_diagnostics = diagnostics,
        count = #players,
    }, { targets = #players, found = #players })
end

-------------------------------------------------------------------------------
-- transfer
-------------------------------------------------------------------------------

local function host_function(...)
    local names = { ... }
    for i = 1, #names do
        local fn = rawget(_G, names[i])
        if type(fn) == "function" then return fn, names[i] end
    end
    return nil, nil
end

local function player_teamid(playerid)
    local fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(fn) == "function" then
        local ok, value = pcall(fn, playerid)
        if ok and tonumber(value) then return tonumber(value), "host" end
    end
    local links = db.open("teamplayerlinks")
    local rec = links and db.scan_for(links, "playerid", playerid) or nil
    return rec and tonumber(links:get("teamid", rec)) or nil, "teamplayerlinks"
end

local function clear_player_state(playerid, op, action)
    local cleared = {}
    if op.clear_presigned ~= false
        and (action == "transfer" or action == "loan")
        and type(rawget(_G, "IsPlayerPresigned")) == "function" then
        local ok, active = pcall(_G.IsPlayerPresigned, playerid)
        if ok and active then
            if type(rawget(_G, "DeletePresignedContract")) ~= "function" then
                return false, "presigned_clear_unavailable", cleared
            end
            local cok, returned = pcall(_G.DeletePresignedContract, playerid)
            if not cok or returned == false then
                return false, "presigned_clear_failed", cleared
            end
            cleared.presigned = true
        end
    end
    if op.clear_loan ~= false and action == "transfer"
        and type(rawget(_G, "IsPlayerLoanedOut")) == "function" then
        local ok, active = pcall(_G.IsPlayerLoanedOut, playerid)
        if ok and active then
            if type(rawget(_G, "TerminateLoan")) ~= "function" then
                return false, "loan_clear_unavailable", cleared
            end
            local cok, returned = pcall(_G.TerminateLoan, playerid)
            if not cok or returned == false then
                return false, "loan_clear_failed", cleared
            end
            cleared.loan = true
        end
    end
    return true, nil, cleared
end

local function verify_list_state(playerid, requested)
    local tf = rawget(_G, "IsPlayerOnTransferList")
    local lf = rawget(_G, "IsPlayerOnLoanList")
    if type(tf) ~= "function" or type(lf) ~= "function" then
        return nil, "list state getters are unavailable"
    end
    local okt, transfer = pcall(tf, playerid)
    local okl, loan = pcall(lf, playerid)
    if not okt or not okl then return nil, "list state getter threw" end
    local expected_t = requested == "transfer"
    local expected_l = requested == "loan"
    return (not not transfer) == expected_t and (not not loan) == expected_l,
        string.format("transfer=%s loan=%s", tostring(transfer), tostring(loan))
end

ops.handlers["transfer"] = function(op, job, ctx)
    local action = op.action or "transfer"
    local playerid = tonumber(op.playerid)
    if not playerid then
        return fail_result("bad_playerid", "transfer requires playerid")
    end
    if job.dry_run then
        return ok_result({ dry_run = true, action = action, playerid = playerid }, {})
    end

    local before_team = player_teamid(playerid)
    local verify = op.verify ~= false
    local ok_clear, clear_reason, cleared = clear_player_state(playerid, op, action)
    if not ok_clear then
        return fail_result(clear_reason, "required player state could not be cleared")
    end

    local fn, api_name, args
    if action == "transfer" then
        fn, api_name = host_function("TransferPlayer")
        local to_teamid = tonumber(op.to_teamid or op.teamid)
        if not to_teamid or to_teamid <= 0 then
            return fail_result("bad_teamid", "transfer requires to_teamid")
        end
        args = {
            playerid, to_teamid, tonumber(op.transfersum or op.fee) or 0,
            tonumber(op.wage) or 5000, tonumber(op.months) or 60,
            tonumber(op.from_teamid) or 0, tonumber(op.release_clause) or -1,
        }
    elseif action == "loan" then
        fn, api_name = host_function("LoanPlayer")
        local to_teamid = tonumber(op.to_teamid or op.teamid)
        if not to_teamid or to_teamid <= 0 then
            return fail_result("bad_teamid", "loan requires to_teamid")
        end
        args = {
            playerid, to_teamid, tonumber(op.months) or 12,
            tonumber(op.loan_to_buy) or -1, tonumber(op.from_teamid) or 0,
        }
    elseif action == "release" then
        fn, api_name = host_function("ReleasePlayerFromTeam")
        args = { playerid }
    elseif action == "terminate_loan" then
        fn, api_name = host_function("TerminateLoan")
        args = { playerid }
    elseif action == "list_player" then
        local list = tostring(op.list or "transfer")
        if list ~= "transfer" and list ~= "loan" and list ~= "none" then
            return fail_result("bad_list", "list must be transfer, loan, or none")
        end
        -- Never allow both lists at once. Removing first is harmless and also
        -- makes repeated requests idempotent.
        local remove = rawget(_G, "RemovePlayerFromLists")
        if type(remove) ~= "function" then
            return fail_result("unsupported_op", "RemovePlayerFromLists host API missing")
        end
        local rok, rret = pcall(remove, playerid)
        if not rok or rret == false then
            return fail_result("list_clear_failed", tostring(rret))
        end
        if list == "none" then
            fn, api_name, args = nil, "RemovePlayerFromLists", nil
        elseif list == "transfer" then
            fn, api_name = host_function("AddPlayerToTransferList")
            args = { playerid }
        else
            fn, api_name = host_function("AddPlayerToLoanList")
            args = { playerid }
        end
        if list == "none" then
            local vok, detail = verify_list_state(playerid, list)
            if verify and vok == nil then
                return fail_result("unverified_side_effect", detail,
                    { targets = 1, side_effects = 1 })
            end
            if verify and not vok then
                return fail_result("verify_mismatch", detail,
                    { targets = 1, side_effects = 1 })
            end
            return ok_result({
                action = action, playerid = playerid, list = list,
                api = api_name, verified = vok == true,
            }, { targets = 1, side_effects = 1 })
        end
    else
        return fail_result("bad_action", "unknown transfer action " .. tostring(action))
    end

    if type(fn) ~= "function" then
        return fail_result("unsupported_op",
            string.format("%s: required host API is unavailable", tostring(action)))
    end
    local ok, returned = pcall(fn, table.unpack(args))
    if not ok then return fail_result("transfer_threw", tostring(returned)) end
    if returned == false then
        return fail_result("host_rejected", api_name .. " returned false")
    end

    local data = {
        action = action, playerid = playerid, api = api_name,
        cleared = cleared, verified = false,
    }
    if not verify then
        return ok_result(data, { targets = 1, side_effects = 1 })
    end
    if action == "transfer" or action == "loan" then
        local expected = tonumber(op.to_teamid or op.teamid)
        local actual, source = player_teamid(playerid)
        data.teamid, data.verify_source = actual, source
        if actual == expected then
            data.verified = true
            return ok_result(data, { targets = 1, found = 1, side_effects = 1 })
        end
        return fail_result("verify_mismatch",
            string.format("%s returned but team read-back was %s, expected %s",
                api_name, tostring(actual), tostring(expected)),
            { targets = 1, side_effects = 1 })
    elseif action == "release" then
        local actual, source = player_teamid(playerid)
        data.teamid, data.verify_source = actual, source
        -- A released player may no longer have a teamplayerlinks record, so
        -- nil is a valid and useful read-back when the old team was known.
        if actual ~= before_team then
            data.verified = true
            return ok_result(data, { targets = 1, found = 1, side_effects = 1 })
        end
        return fail_result("verify_mismatch",
            string.format("release returned but team remained %s", tostring(actual)),
            { targets = 1, side_effects = 1 })
    elseif action == "terminate_loan" then
        local is_loaned = rawget(_G, "IsPlayerLoanedOut")
        if type(is_loaned) == "function" then
            local vok, active = pcall(is_loaned, playerid)
            if vok and not active then
                data.verified = true
                return ok_result(data, { targets = 1, side_effects = 1 })
            end
            return fail_result("verify_mismatch",
                "TerminateLoan returned but player is still loaned",
                { targets = 1, side_effects = 1 })
        end
    elseif action == "list_player" then
        local vok, detail = verify_list_state(playerid, tostring(op.list or "transfer"))
        if vok == true then
            data.verified = true
            return ok_result(data, { targets = 1, side_effects = 1 })
        end
        if vok == false then
            return fail_result("verify_mismatch", detail,
                { targets = 1, side_effects = 1 })
        end
        return fail_result("unverified_side_effect", detail,
            { targets = 1, side_effects = 1 })
    end
    return fail_result("unverified_side_effect",
        api_name .. " returned but no supported read-back API is available",
        { targets = 1, side_effects = 1 })
end

-------------------------------------------------------------------------------
-- bulk_edit / career.set / budget / export helpers as real-or-unsupported
-------------------------------------------------------------------------------

--- Resolve the user senior team id when present.
local function resolve_user_teamid()
    if type(rawget(_G, "GetUserTeamID")) == "function" then
        local ok, tid = pcall(_G.GetUserTeamID)
        if ok and type(tid) == "number" and tid > 0 then return math.floor(tid) end
    end
    if type(rawget(_G, "GetUserTeamId")) == "function" then
        local ok, tid = pcall(_G.GetUserTeamId)
        if ok and type(tid) == "number" and tid > 0 then return math.floor(tid) end
    end
    return nil
end

--- Player ids returned by the host's current-user-senior-team helper.
---
--- ``teamplayerlinks`` is often temporarily empty while FC changes Career
--- screens, even though this helper already has the complete active squad.
--- Export uses the helper for that reason.  Career writes must use the same
--- membership source or a freshly exported 41-player squad can immediately
--- fail as ``empty_team``.
local function user_senior_playerids()
    local out, seen = {}, {}
    local fn = rawget(_G, "GetUserSeniorTeamPlayerIDs")
    if type(fn) ~= "function" then return out end
    local ok, values = pcall(fn)
    if not ok or type(values) ~= "table" then return out end
    for key, value in pairs(values) do
        local pid = tonumber(key)
        if not pid or pid <= 0 then pid = tonumber(value) end
        if pid and pid > 0 then
            pid = math.floor(pid)
            if not seen[pid] then
                seen[pid] = true
                out[#out + 1] = pid
            end
        end
    end
    table.sort(out)
    return out
end

--- playerids currently linked to *teamid*.  Prefer the authoritative host
--- helper for the active user team (the same source V1 boosts use); links are
--- only a fallback/supplemental source when that helper is unavailable.
local function team_playerids(teamid)
    local out = {}
    if teamid == nil then return out end
    local host_ids = user_senior_playerids()
    local active_teamid = resolve_user_teamid()
    if #host_ids > 0 and active_teamid and tonumber(teamid) == active_teamid then
        return host_ids
    end
    local links = db.open("teamplayerlinks")
    if links then
        for rec in links:records() do
            local tid = tonumber(links:get("teamid", rec))
            local pid = tonumber(links:get("playerid", rec))
            if tid == tonumber(teamid) and pid then
                out[#out + 1] = pid
            end
        end
    end
    if #out == 0 and active_teamid and tonumber(teamid) == active_teamid then
        return host_ids
    end
    return out
end

local function where_empty(where)
    return type(where) ~= "table" or next(where) == nil
end

ops.handlers["bulk_edit"] = function(op, job, ctx)
    local set_map = op["set"] or op.set_fields or {}
    if util.is_empty(set_map) then
        return fail_result("no_fields", "bulk_edit with empty set")
    end
    if job.dry_run then
        return ok_result({ dry_run = true }, { fields_requested = util.count(set_map) })
    end
    if not db.available() then
        return fail_result("no_db", "bulk_edit needs a loaded Career Mode save")
    end
    local table_name = op.table or "players"
    local h = db.open(table_name)
    if not h then return fail_result("no_table", table_name) end

    local where = op.where or {}
    local limit = tonumber(op.limit) or 0
    local scope = op.scope
    local teamid = tonumber(op.teamid)

    -- Empty where on players would match the entire DB (~21k rows). Refuse
    -- unless the caller asked for an explicit team scope.
    local allow_ids = nil  -- nil = use where filter; table = only these playerids
    if table_name == "players" and where_empty(where) then
        if scope == "all_players" then
            -- A full player-table edit used to execute in one game tick.  It
            -- could hold the database lock for thousands of writes and crash
            -- or freeze FC.  Keep the wire route explicitly rejected until a
            -- cursor-based, resumable batch implementation is live.
            return fail_result("unsafe_scope",
                "bulk_edit scope=all_players is disabled until resumable batching is available")
        elseif scope == "user_team" or scope == "user_senior_team" then
            teamid = teamid or resolve_user_teamid()
            if not teamid then
                return fail_result("no_team",
                    "bulk_edit scope=user_team needs GetUserTeamID / loaded Career Mode")
            end
            allow_ids = team_playerids(teamid)
            if #allow_ids == 0 then
                return fail_result("empty_team",
                    string.format("team %s has no players in teamplayerlinks", tostring(teamid)))
            end
        elseif teamid then
            allow_ids = team_playerids(teamid)
            if #allow_ids == 0 then
                return fail_result("empty_team",
                    string.format("team %s has no players in teamplayerlinks", tostring(teamid)))
            end
        else
            return fail_result("need_where",
                "bulk_edit on players with empty where is refused — pass where={...}, "
                .. "teamid=N, or scope=user_team (never the whole players table)")
        end
    end

    local id_set = nil
    if allow_ids then
        id_set = {}
        for i = 1, #allow_ids do id_set[allow_ids[i]] = true end
    end

    local touched, failed, fields_written, field_failures = 0, 0, 0, 0
    local growth_mirrored, growth_failed, growth_no_plan = 0, 0, 0
    for rec in h:records() do
        local match = true
        if id_set then
            local pid = tonumber(h:get("playerid", rec))
            match = pid ~= nil and id_set[pid] == true
        else
            for k, v in pairs(where) do
                local cur = h:get(k, rec)
                if cur ~= v and tonumber(cur) ~= tonumber(v) then match = false; break end
            end
        end
        if match then
            local w, fails = db.set_many(h, rec, set_map, { on_field_error = "continue" })
            touched = touched + 1
            fields_written = fields_written + w
            if #fails > 0 then
                failed = failed + 1
                field_failures = field_failures + #fails
                if table_name == "players" then
                    growth_failed = growth_failed + growth_field_count(set_map)
                end
            elseif table_name == "players" then
                local pid = tonumber(h:get("playerid", rec))
                local gm, gf, gn = mirror_growth(
                    pid, set_map, op.growth_mirror or "auto")
                growth_mirrored = growth_mirrored + gm
                growth_failed = growth_failed + gf
                growth_no_plan = growth_no_plan + gn
            end
            if limit > 0 and touched >= limit then break end
        end
    end
    local data = {
        touched = touched,
        teamid = teamid,
        scope = scope,
    }
    local counts = {
        targets = touched,
        fields_requested = touched * util.count(set_map),
        fields_written = fields_written,
        fields_failed = field_failures,
        found = touched,
        growth_mirrored = growth_mirrored,
        growth_failed = growth_failed,
        growth_no_plan = growth_no_plan,
    }
    if failed > 0 or growth_failed > 0 or growth_no_plan > 0 then
        local r = fail_result("partial_write",
            string.format(
                "%d target(s) had DB failures; %d growth fields failed; "
                .. "%d growth fields had no development plan",
                failed, growth_failed, growth_no_plan),
            counts)
        r.data = data
        return r
    end
    return ok_result(data, counts)
end

ops.handlers["career.set"] = function(op, job, ctx)
    if job.dry_run then
        return ok_result({ dry_run = true, scope = op.scope }, {})
    end

    -- V1's working boosts explicitly load this stock LE module before using
    -- UserTeamSetPlayersFitness/etc.  That module defines both the confirmed
    -- SetPlayer* setters and GetUserSeniorTeamPlayerIDs.  Loading it here is
    -- best-effort because some LE builds preload it, but never silently invent
    -- a setter when the import is absent.
    pcall(require, "imports/career_mode/helpers")

    local setters = {
        -- These are the actual FC 26 LE Career Mode helpers.  The inverse
        -- ``PlayerSet*`` names never existed, so every boost was previously
        -- classified as an unsupported field after target resolution.
        fitness = "SetPlayerFitness",
        form = "SetPlayerForm",
        morale = "SetPlayerMorale",
        sharpness = "SetPlayerSharpness",
        squad_role = "SetSquadRole",
        release_clause = "SetReleaseClause",
    }
    local fields_requested = {}
    for field, _ in pairs(setters) do
        if op[field] ~= nil then fields_requested[#fields_requested + 1] = field end
    end
    if #fields_requested == 0 then
        return fail_result("no_fields", "career.set with nothing to set")
    end

    -- Resolve target playerids from scope.
    local scope = op.scope or "player"
    local targets = {}
    if tonumber(op.playerid) then
        targets[1] = tonumber(op.playerid)
    elseif scope == "user_senior_team" or scope == "user_team" then
        local requested_teamid = tonumber(op.teamid)
        local live_teamid = resolve_user_teamid()
        -- The desktop app supplies a team id only from a fresh, same-session
        -- squad read.  Refuse rather than applying that cached hint if FC can
        -- prove the active Career team has changed meanwhile.
        if requested_teamid and live_teamid and requested_teamid ~= live_teamid then
            return fail_result("team_changed",
                string.format("career.set team hint %s does not match active user team %s",
                    tostring(requested_teamid), tostring(live_teamid)))
        end
        -- The target is *always* the live current-user senior squad.  A
        -- desktop team id is only an expected-team guard above: queued work
        -- may outlive a save/club switch, so it must never become authority to
        -- fall back to a prior club.  This mirrors V1's proven Boost path and
        -- stays usable while GetUserTeamID is temporarily unavailable.
        targets = user_senior_playerids()
        if #targets == 0 and live_teamid then
            targets = team_playerids(live_teamid)
        end
        if #targets == 0 then
            if not live_teamid then
                return fail_result("no_team",
                    "career.set could not resolve the current user senior team (Career Mode save loaded)")
            end
            return fail_result("empty_team",
                string.format("current user team %s has no players to apply career.set", tostring(live_teamid)))
        end
    else
        return fail_result("no_playerid",
            "career.set requires playerid, or scope=user_senior_team with a resolvable user team")
    end

    -- Host API presence: if none of the field setters exist, refuse honestly.
    local any_api = false
    for _, field in ipairs(fields_requested) do
        local api = setters[field]
        if api and type(rawget(_G, api)) == "function" then any_api = true; break end
    end
    if not any_api then
        return fail_result("unsupported_op",
            "career.set: host career APIs not available (need Career Mode + LE helpers)")
    end

    local applied_n, failed_n = 0, 0
    local per_player = {}
    for i = 1, #targets do
        local pid = targets[i]
        local row = { playerid = pid, fields = {} }
        for field, api in pairs(setters) do
            if op[field] ~= nil then
                if type(rawget(_G, api)) == "function" then
                    local ok = pcall(rawget(_G, api), pid, tonumber(op[field]))
                    row.fields[field] = ok and true or false
                    if ok then applied_n = applied_n + 1 else failed_n = failed_n + 1 end
                else
                    row.fields[field] = false
                    failed_n = failed_n + 1
                end
            end
        end
        per_player[#per_player + 1] = row
    end

    if applied_n == 0 then
        return fail_result("no_writes",
            "career.set applied 0 field writes across " .. #targets .. " player(s)", {
                targets = #targets, found = #targets, fields_failed = failed_n,
            })
    end
    local data = {
        scope = scope,
        targets = targets,
        applied = per_player,
        fields_applied = applied_n,
        fields_failed = failed_n,
        -- FC 26 LE exposes setters but no compatible read-back helpers for
        -- these transient Career values.  Do not label a successful pcall as
        -- a database-verified write in the desktop UI.
        host_calls_accepted = true,
        verified = false,
    }
    local counts = {
        targets = #targets,
        found = #targets,
        fields_requested = #targets * #fields_requested,
        fields_written = applied_n,
        fields_failed = failed_n,
    }
    if failed_n > 0 then
        local r = fail_result("partial_write",
            string.format("%d career field write(s) failed or were unsupported", failed_n),
            counts)
        r.data = data
        return r
    end
    return ok_result(data, counts)
end

ops.handlers["budget"] = function(op, job, ctx)
    local action = op.action or "get"
    local scope = op.target or op.scope or "user"
    if scope ~= "user" and scope ~= "cpu" then
        return fail_result("bad_target", "budget target must be user or cpu")
    end
    if action == "get" then
        local getter = scope == "cpu" and "GetCPUTransferBudget" or "GetUserTransferBudget"
        if type(rawget(_G, getter)) == "function" then
            local ok, val = pcall(rawget(_G, getter))
            if ok then
                return ok_result({ amount = val, transfer = val, target = scope }, {})
            end
        end
        return fail_result("unsupported_op", "budget get: host API missing")
    end
    if job.dry_run then
        return ok_result({
            dry_run = true, amount = op.amount or op.transfer, target = scope,
        }, {})
    end
    if action ~= "set" then
        return fail_result("bad_action", "budget action must be get or set")
    end
    local requested = tonumber(op.amount ~= nil and op.amount or op.transfer)
    if requested == nil or requested < 0 or requested ~= math.floor(requested) then
        return fail_result("bad_amount", "budget set requires a non-negative integer amount")
    end
    if op.wage ~= nil then
        return fail_result("unsupported_field",
            "FC 26 exposes no verified wage-budget setter; no write attempted")
    end
    local setter = scope == "cpu" and "SetCPUTransferBudget" or "SetUserTransferBudget"
    if type(rawget(_G, setter)) ~= "function" then
        return fail_result("unsupported_op", "budget set: host API missing (use SetUserTransferBudget)")
    end
    local ok, returned = pcall(rawget(_G, setter), requested)
    if not ok then return fail_result("budget_threw", tostring(returned)) end
    if returned == false then return fail_result("host_rejected", setter .. " returned false") end
    local getter = scope == "cpu" and "GetCPUTransferBudget" or "GetUserTransferBudget"
    if type(rawget(_G, getter)) == "function" then
        local ok, actual = pcall(rawget(_G, getter))
        if ok and tonumber(actual) == requested then
            return ok_result(
                { amount = actual, transfer = actual, target = scope, verified = true },
                { side_effects = 1 })
        end
        return fail_result("verify_mismatch",
            string.format("budget read-back was %s, expected %s",
                tostring(actual), tostring(requested)),
            { side_effects = 1 })
    end
    return fail_result("unverified_side_effect",
        "budget setter returned but no getter was available for read-back",
        { side_effects = 1 })
end

ops.handlers["db.dump"] = function(op, job, ctx)
    if not db.available() then
        return fail_result("no_db", "db.dump needs a loaded save")
    end
    local tname = op.table
    if not tname then return fail_result("no_table", "db.dump requires table") end
    local h = db.open(tname)
    if not h then return fail_result("no_table", tname) end
    return ok_result({
        table = tname,
        count = h:count(),
        note = "row dump omitted in-core; use out_path via Python for bulk export",
    }, { targets = h:count() })
end

ops.handlers["snapshot"] = function(op, job, ctx)
    local ids = op.targets or op.playerids or {}
    if #ids == 0 then return fail_result("no_targets", "snapshot requires playerids") end
    if not db.available() then
        return fail_result("no_db", "snapshot needs a loaded save")
    end
    local h = db.open(op.table or "players")
    if not h then return fail_result("no_table", "players") end
    local rows = {}
    for i = 1, #ids do
        local rec = db.scan_for(h, "playerid", tonumber(ids[i]))
        if rec then
            local row = { playerid = tonumber(ids[i]) }
            local fields = op.fields
            if fields == "*" then
                fields = util.keys(db.table_fields(op.table or "players") or {})
            end
            if type(fields) == "table" then
                for _, f in ipairs(fields) do row[f] = h:get(f, rec) end
            end
            rows[#rows + 1] = row
        end
    end
    local data = {
        table = op.table or "players", rows = rows,
        save_uid = ctx.save_uid or "", taken_at = util.now(),
    }
    local out_path = op.out or op.out_path
    if out_path and out_path ~= "" then
        if not path_allowed(out_path, ctx) then
            return fail_result("snapshot_path_denied",
                "snapshot out must be inside an allowed write root")
        end
        local text, err = require("json").try_encode(data)
        if not text then return fail_result("snapshot_encode_failed", tostring(err)) end
        local wok, werr = util.write_atomic(out_path, text)
        if not wok then return fail_result("snapshot_write_failed", tostring(werr)) end
        data.out = out_path
    end
    return ok_result(data, { targets = #ids, found = #rows })
end

ops.handlers["growth_sync"] = function(op, job, ctx)
    if type(rawget(_G, "PlayerSetValueInDevelopementPlan")) ~= "function" then
        return fail_result("unsupported_op", "growth_sync: PlayerSetValueInDevelopementPlan missing")
    end
    if op.source ~= nil and op.source ~= "from_players_table" then
        return fail_result("bad_source", "growth_sync source must be from_players_table")
    end
    if not db.available() then
        return fail_result("no_db", "growth_sync needs a loaded Career Mode save")
    end

    local targets = {}
    local scope = op.scope or "ids"
    if scope == "user_team" or scope == "user_senior_team" then
        local teamid = resolve_user_teamid()
        if not teamid then
            return fail_result("no_team", "growth_sync could not resolve the user team")
        end
        targets = team_playerids(teamid)
    elseif scope == "ids" then
        for i = 1, #(op.playerids or {}) do
            local pid = tonumber(op.playerids[i])
            if pid and pid > 0 then targets[#targets + 1] = math.floor(pid) end
        end
    else
        return fail_result("bad_scope", "growth_sync scope must be ids or user_team")
    end
    if #targets == 0 then
        return fail_result("no_targets", "growth_sync resolved no players")
    end
    if #targets > 60 then
        return fail_result("too_many_targets", "growth_sync is limited to 60 players per job")
    end
    if job.dry_run then
        return ok_result(
            { dry_run = true, scope = scope, targets = targets },
            { targets = #targets, growth_mirrored = 0, growth_failed = 0 })
    end

    local wanted = {}
    for i = 1, #targets do wanted[targets[i]] = true end
    local h = db.open("players")
    if not h then return fail_result("no_table", "players") end
    local mirrored, failed, no_plan, found = 0, 0, 0, 0
    for rec in h:records() do
        local pid = tonumber(h:get("playerid", rec))
        if pid and wanted[pid] then
            local values = {}
            for name in pairs(PLAN_FIELDS) do
                local value = h:get(name, rec)
                if type(value) == "number" then values[name] = value end
            end
            local gm, gf, gn = mirror_growth(pid, values, "auto")
            mirrored = mirrored + gm
            failed = failed + gf
            no_plan = no_plan + gn
            found = found + 1
            wanted[pid] = nil
        end
    end
    local missing = #targets - found
    local counts = {
        targets = #targets,
        found = found,
        missing = missing,
        growth_mirrored = mirrored,
        growth_failed = failed,
        growth_no_plan = no_plan,
        fields_requested = mirrored + failed + no_plan,
        fields_written = mirrored,
        fields_failed = failed,
    }
    local data = {
        scope = scope,
        target_count = #targets,
        found = found,
        players_without_plan_fields = no_plan,
    }
    if found == 0 then
        return fail_result(
            "targets_not_found", "none of the growth-sync players were found", counts)
    end
    if mirrored == 0 and no_plan > 0 and failed == 0 and missing == 0 then
        local result = fail_result(
            "no_growth_plans",
            "players were found, but none had a development plan to synchronize",
            counts)
        result.data = data
        return result
    end
    if failed > 0 or missing > 0 or no_plan > 0 then
        local result = fail_result(
            "partial_write",
            string.format(
                "%d growth field(s) failed; %d player(s) missing; "
                .. "%d field(s) had no development plan",
                failed, missing, no_plan),
            counts)
        result.data = data
        return result
    end
    return ok_result(data, counts)
end

ops.handlers["create_player"] = function(op, job, ctx)
    if not (job.grants and job.grants.allow_create_player) then
        return fail_result("grant_denied", "create_player requires grants.allow_create_player")
    end
    local pid = tonumber(op.playerid)
    if pid and pid > (version.LIMITS.max_playerid or 499999) then
        return fail_result("playerid_ceiling",
            "CreatePlayer with playerid >= 500000 terminates FC26.exe")
    end
    if type(rawget(_G, "CreatePlayer")) ~= "function" then
        return fail_result("unsupported_op", "CreatePlayer host API missing")
    end
    if job.dry_run then
        return ok_result({ dry_run = true, playerid = pid }, {})
    end
    -- Prefer next free id in range when requested.
    if pid == nil and type(op.id_range) == "table" then
        local lo, hi = tonumber(op.id_range[1]) or 460000, tonumber(op.id_range[2]) or 499999
        if hi > 499999 then hi = 499999 end
        pid = lo
        local h = db.open("players")
        if h then
            for try = lo, math.min(hi, lo + 64) do
                if not db.scan_for(h, "playerid", try) then pid = try; break end
            end
        end
    end
    if not pid then return fail_result("no_playerid", "create_player needs playerid or id_range") end
    local fields = op.fields or {}
    if op.generic_head ~= false then
        fields.hashighqualityhead = fields.hashighqualityhead or 0
        fields.headclasscode = fields.headclasscode or 1
        fields.headassetid = fields.headassetid or 0
    end
    local ok, err = pcall(_G.CreatePlayer, pid, fields)
    if not ok then return fail_result("create_threw", tostring(err)) end
    local h = db.open("players")
    local rec = h and db.scan_for(h, "playerid", pid) or nil
    if not rec then
        return fail_result("unverified_side_effect",
            "CreatePlayer returned but the new player was not readable",
            { targets = 1, side_effects = 1 })
    end
    return ok_result(
        { playerid = pid, verified = true },
        { targets = 1, found = 1, side_effects = 1 })
end

ops.handlers["add_to_team"] = function(op, job, ctx)
    local enabled = ctx and ctx.cfg and ctx.cfg.core_ops
        and ctx.cfg.core_ops.add_to_team == true
    if not enabled then
        return fail_result("experimental_disabled",
            "add_to_team is disabled; enable the Phase 4 worker toggle and restart Live Editor")
    end
    local ok, module = pcall(require, "ops.add_to_team")
    if not ok or type(module) ~= "table" or type(module.step) ~= "function" then
        return fail_result("module_load_failed", tostring(module))
    end
    return module.step(op, job, ctx)
end

-- A one-purpose recovery for a previously interrupted Add Player job.  It is
-- protected by the same opt-in switch and grant as Add Player, and the module
-- refuses to write unless its own live preconditions still match exactly.
ops.handlers["repair_partial_add"] = function(op, job, ctx)
    local enabled = ctx and ctx.cfg and ctx.cfg.core_ops
        and ctx.cfg.core_ops.add_to_team == true
    if not enabled then
        return fail_result("experimental_disabled",
            "repair_partial_add requires the Add Player worker toggle")
    end
    local ok, module = pcall(require, "ops.repair_partial_add")
    if not ok or type(module) ~= "table" or type(module.run) ~= "function" then
        return fail_result("module_load_failed", tostring(module))
    end
    return module.run(op, job, ctx)
end

ops.handlers["injury.scan"] = function(op, job, ctx)
    local ok, module = pcall(require, "ops.injury")
    if not ok or type(module) ~= "table" or type(module.scan) ~= "function" then
        return fail_result("module_load_failed", tostring(module))
    end
    return module.scan(op, job, ctx)
end

ops.handlers["injury.cure"] = function(op, job, ctx)
    local ok, module = pcall(require, "ops.injury")
    if not ok or type(module) ~= "table" or type(module.cure) ~= "function" then
        return fail_result("module_load_failed", tostring(module))
    end
    return module.cure(op, job, ctx)
end

-------------------------------------------------------------------------------
-- envelope snapshot_to -- capture every supported field before any write
-------------------------------------------------------------------------------

path_allowed = function(path, ctx)
    if type(path) ~= "string" or path == "" then return false end
    local norm = util.normalize(path):lower()
    local roots = (ctx.cfg and ctx.cfg.allowed_write_roots) or {}
    for i = 1, #roots do
        local root = util.normalize(roots[i]):lower()
        if norm == root or util.startswith(norm, root .. "/") then return true end
    end
    return false
end

function ops.capture_envelope_snapshot(job, ctx)
    if type(job.snapshot_to) ~= "string" or job.snapshot_to == "" then
        return true, nil
    end
    if job.dry_run then return true, { dry_run = true } end
    if not path_allowed(job.snapshot_to, ctx) then
        return false, {
            reason = "snapshot_path_denied",
            detail = "snapshot_to must be inside an allowed write root",
        }
    end
    if not db.available() then
        return false, { reason = "snapshot_no_db", detail = "snapshot_to needs a loaded save" }
    end

    local rows = {}
    for i = 1, #job.ops do
        local op = job.ops[i]
        if op.op ~= "set_fields" then
            return false, {
                reason = "snapshot_unsupported_op",
                detail = "snapshot_to currently supports set_fields jobs only",
            }
        end
        local key = op.key or {}
        local table_name = op.table or "players"
        local key_field = key.field or "playerid"
        local h, rec, state = db.resolve(table_name, key_field, key.value)
        if not h then
            return false, { reason = "snapshot_no_table", detail = tostring(state) }
        end
        if not rec and not (op.upsert and table_name == "editedplayernames") then
            return false, {
                reason = "snapshot_target_missing",
                detail = string.format("%s=%s not in %s",
                    tostring(key_field), tostring(key.value), table_name),
            }
        end
        local before = {}
        if rec then
            for field in pairs(op.fields or {}) do
                before[field] = h:get(field, rec)
            end
        end
        rows[#rows + 1] = {
            op_id = op.id,
            table = table_name,
            key = { field = key_field, value = key.value },
            existed = rec ~= nil,
            fields = before,
        }
    end

    local payload = {
        schema = version.RESULT_SCHEMA,
        kind = "pre_write_snapshot",
        job_id = job.job_id,
        save_uid = ctx.save_uid or "",
        taken_at = util.now(),
        rows = rows,
    }
    local text, err = require("json").try_encode(payload)
    if not text then
        return false, { reason = "snapshot_encode_failed", detail = tostring(err) }
    end
    local ok, reason = util.write_atomic(job.snapshot_to, text)
    if not ok then
        return false, { reason = "snapshot_write_failed", detail = tostring(reason) }
    end
    return true, { path = job.snapshot_to, rows = #rows }
end

-------------------------------------------------------------------------------
-- dispatch
-------------------------------------------------------------------------------

function ops.dispatch(op_body, job, ctx)
    if type(op_body) ~= "table" then
        return fail_result("bad_op", "op body is not a table")
    end
    local name = op_body.op
    local ok_v, reason, detail = version.op_supported(name, op_body.v or 1)
    if not ok_v then
        return fail_result(reason or "unsupported_op", detail)
    end
    local grant = version.OP_GRANTS[name]
    if grant then
        local grants = job.grants or {}
        if not grants[grant] then
            return fail_result("grant_denied",
                string.format("op %s requires grant %s", tostring(name), grant))
        end
    end
    local handler = ops.handlers[name]
    if type(handler) ~= "function" then
        return fail_result("unsupported_op",
            string.format("op %s is known but has no handler in this core", tostring(name)))
    end
    local ok, res = util.capture(function()
        return handler(op_body, job, ctx)
    end)
    if not ok then
        return fail_result("op_threw", res.message or tostring(res), {}, {
            { reason = "op_threw", detail = res.message or "", phase = "execute", op = name },
        })
    end
    if type(res) ~= "table" then
        return fail_result("bad_handler_return", "handler returned non-table")
    end
    return res
end

function ops.op_versions()
    return util.copy(version.OP_VERSIONS)
end

function ops.list()
    return util.keys(version.OP_VERSIONS)
end

return ops
