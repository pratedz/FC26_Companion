--[[ Phase 4 experimental add_to_team.

One invocation advances exactly one named step. The runner persists only
plain values (step/playerid/teamid/counts), never record addresses, so every
resume re-resolves the database row safely.
]]

local db = require("db")
local util = require("util")
local json = require("json")
local version = require("version")

local M = {}

local STEP_NAMES = {
    "resolve_team", "pick_dummy", "write_fields", "write_face",
    "preserve_name_dictionary", "reserve_dummy", "transfer", "verify_team",
    "reapply_fields", "write_verified_name",
}

local BATCH_STEP_NAMES = {
    "resolve_team", "pick_all", "write_all", "transfer_all",
    "verify_all", "reapply_all", "names_all",
}

local function active_step_names(state)
    if state and state.batch then return BATCH_STEP_NAMES end
    return STEP_NAMES
end

local function fail(reason, detail, state)
    local names = active_step_names(state)
    return {
        ok = false, reason = reason, detail = detail or "",
        counts = state.counts or {}, data = { progress = state },
        failures = { {
            reason = reason, detail = detail or "",
            step = names[state.step or 1],
        } },
    }
end

local function next_step(state, data, counts)
    local names = active_step_names(state)
    state.step = (state.step or 1) + 1
    state.counts = state.counts or {}
    for k, v in pairs(counts or {}) do
        state.counts[k] = (state.counts[k] or 0) + v
    end
    return {
        ok = true,
        deferred = state.step <= #names,
        awaiting_event = false,
        counts = counts or {},
        data = data or {},
        progress = state,
        failures = {},
    }
end

-- TransferPlayer / name dictionary updates settle on Career events. Soft-yield
-- instead of hard-failing so a slow move does not leave a blank-named player.
local function wait_event(state, data)
    return {
        ok = true,
        deferred = true,
        awaiting_event = true,
        counts = {},
        data = data or {},
        progress = state,
        failures = {},
    }
end

local function user_team()
    for _, name in ipairs({ "GetUserTeamID", "GetUserTeamId" }) do
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            local ok, value = pcall(fn)
            if ok and tonumber(value) and tonumber(value) > 0 then
                return math.floor(tonumber(value))
            end
        end
    end
    return nil
end

local function player_record(pid)
    local h, rec, reason = db.resolve("players", "playerid", pid)
    if not h then return nil, nil, reason or "no_players_table" end
    if not rec then return h, nil, "player_not_found" end
    return h, rec
end

local function host_true(value)
    return value == true or tonumber(value) == 1
end

local function consumed_dummy_store(ctx)
    local uid = tostring((ctx and ctx.save_uid) or "")
    local queue_dir = tostring((ctx and ctx.queue_dir) or "")
    if uid == "" then return nil, nil, "save_uid_unavailable" end
    if queue_dir == "" then return nil, nil, "queue_dir_unavailable" end
    local key = uid:gsub("[^%w_%-]", "_")
    local path = util.join(queue_dir, "state", "consumed_free_agent_dummies_" .. key .. ".json")
    local doc = { schema = 1, save_uid = uid, playerids = {} }
    local text = util.read_text(path)
    if text then
        local parsed = json.try_decode_object(text)
        if type(parsed) == "table" and tostring(parsed.save_uid or "") == uid then
            doc = parsed
            doc.playerids = type(doc.playerids) == "table" and doc.playerids or {}
        end
    end
    return path, doc, nil
end

local function dummy_is_consumed(doc, pid)
    local row = doc and doc.playerids and doc.playerids[tostring(pid)]
    return row ~= nil and row ~= false and tonumber(row) ~= 0
end

local function consume_dummy(path, doc, pid)
    doc.playerids = type(doc.playerids) == "table" and doc.playerids or {}
    doc.playerids[tostring(pid)] = { consumed_at = util.now() }
    local text = json.try_encode(doc)
    if not text then return false, "consumed_slot_encode_failed" end
    local ok, err = util.write_atomic(path, text)
    if not ok then return false, tostring(err or "consumed_slot_write_failed") end
    return true
end

local function safe_free_agent(pid, filter, on_club)
    local exists_fn = rawget(_G, "PlayerExists")
    local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(exists_fn) ~= "function" or type(team_fn) ~= "function" then
        return false, "slot_probe_unavailable", "required live player checks are unavailable"
    end
    local ok_exists, value = pcall(exists_fn, pid)
    if not ok_exists or not host_true(value) then
        return false, "dummy_missing", "selected free-agent player is no longer readable"
    end
    -- 2.6.42: GetTeamIdFromPlayerId answers from Live Editor's player cache,
    -- which still says "free agent" for a dummy signed earlier in the same
    -- hub session. The club's contracts (a DB read) are the authority here.
    if type(on_club) == "table" and on_club[pid] then
        return false, "dummy_not_free", "selected player is already on your club"
    end
    local ok_team, value_team = pcall(team_fn, pid)
    local live_teamid = ok_team and tonumber(value_team) or nil
    -- GetTeamIdFromPlayerId returns -1 (and sometimes fails) while a previous
    -- TransferPlayer is still settling. That is not proof this dummy joined a
    -- club; verify_team already treats -1 / nil / 0 / 111592 as unknown/free.
    if not ok_team or live_teamid == nil or live_teamid < 0 then
        return false, "dummy_team_unknown", "free-agent membership is not readable yet"
    end
    if live_teamid > 0 and live_teamid ~= 111592 then
        return false, "dummy_not_free", "selected player is no longer a free agent"
    end
    for _, fn_name in ipairs({ "IsPlayerPresigned", "IsPlayerLoanedOut" }) do
        local fn = rawget(_G, fn_name)
        if type(fn) ~= "function" then
            return false, "slot_probe_unavailable", fn_name .. " host check is unavailable"
        end
        local ok, active = pcall(fn, pid)
        if not ok then
            return false, "slot_probe_failed", fn_name .. " could not inspect the player"
        end
        if host_true(active) then
            return false, "dummy_not_clean", "selected player is presigned or loaned"
        end
    end
    local h, rec, reason = player_record(pid)
    if not rec then return false, reason or "player_not_found", "selected player record is unavailable" end
    local max_overall = math.min(75, tonumber((filter or {}).max_overall) or 75)
    local overall = tonumber(h:get("overallrating", rec))
    if not overall or overall > max_overall then
        return false, "dummy_rating_changed", "selected free agent no longer meets the low-rating safety limit"
    end
    local expected = tonumber((filter or {}).expected_overallrating)
    if expected and overall ~= expected then
        return false, "dummy_identity_changed", "selected free agent changed since the live review"
    end
    if db.field("players", "isretiring") == nil then
        return false, "slot_probe_unavailable", "players.isretiring safety field is unavailable"
    end
    local retiring = tonumber(h:get("isretiring", rec))
    if retiring == nil or retiring ~= 0 then
        return false, "dummy_retiring", "selected free agent is retiring or cannot be verified"
    end
    return true
end

-- ``GetTeamIdFromPlayerId`` is fast but, on some FC 26 Career screens, it
-- returns -1 while the player is already present in the club's squad.  The
-- squad exporter has reliable bounded membership sources, so use the same
-- sources before declaring an asynchronous TransferPlayer call unsuccessful.
local function ids_include(values, wanted_pid)
    wanted_pid = tonumber(wanted_pid)
    if type(values) ~= "table" or not wanted_pid or wanted_pid <= 0 then
        return false
    end
    wanted_pid = math.floor(wanted_pid)
    for key, value in pairs(values) do
        -- Host helpers may return either { id, id } or { [id] = true }.
        local candidate = tonumber(value)
        if not candidate or candidate <= 0 then candidate = tonumber(key) end
        if candidate and math.floor(candidate) == wanted_pid then return true end
    end
    return false
end

local function helper_has_player(helper_name, teamid, playerid)
    local fn = rawget(_G, helper_name)
    if type(fn) ~= "function" then return false, false end
    local ok, values = pcall(fn, teamid)
    if not ok or type(values) ~= "table" then return false, false end
    return ids_include(values, playerid), true
end

-- Returns ``verified, source, observed_teamid, readable``.  A true result is
-- positive proof that the exact player is now in the intended squad.  A
-- negative result never treats -1 / 0 / free-agent sentinels as proof of a
-- failed transfer; those values mean the host does not currently know.
local function verify_team_membership(playerid, wanted_teamid)
    local wanted = tonumber(wanted_teamid)
    local pid = tonumber(playerid)
    if not wanted or wanted <= 0 or not pid or pid <= 0 then
        return false, nil, nil, false
    end
    wanted, pid = math.floor(wanted), math.floor(pid)

    local actual, readable = nil, false
    local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(team_fn) == "function" then
        local ok, value = pcall(team_fn, pid)
        if ok then
            actual = tonumber(value)
            if actual == wanted then return true, "GetTeamIdFromPlayerId", actual, true end
            -- -1, 0 and 111592 are observed unknown/free-agent sentinels.
            if actual and actual > 0 and actual ~= 111592 then readable = true end
        end
    end

    -- This helper is only guaranteed to represent the active user club.
    if user_team() == wanted then
        local found, available = helper_has_player("GetUserSeniorTeamPlayerIDs", nil, pid)
        readable = readable or available
        if found then return true, "GetUserSeniorTeamPlayerIDs", actual, true end
    end

    for _, helper_name in ipairs({ "GetPlayerIDSForTeam", "GetPlayerIDsForTeam" }) do
        local found, available = helper_has_player(helper_name, wanted, pid)
        readable = readable or available
        if found then return true, helper_name, actual, true end
    end

    -- Last-resort database proof, matching the bounded squad-export fallback.
    local links = db.open("teamplayerlinks")
    if links then
        readable = true
        for rec in links:records() do
            local tid = tonumber(links:get("teamid", rec))
            local linked_pid = tonumber(links:get("playerid", rec))
            if tid == wanted and linked_pid == pid then
                return true, "teamplayerlinks", actual, true
            end
        end
    end
    return false, nil, actual, readable
end

-- FC's free-agent roster. A stable 111592 after TransferPlayer is a rejection,
-- not an in-flight move. -1 / nil / 0 are the values that still mean "settling".
local FREE_AGENT_TEAM_ID = 111592

local function host_id_set(fn_name, arg)
    local fn = rawget(_G, fn_name)
    if type(fn) ~= "function" then return nil end
    local ok, values
    if arg == nil then ok, values = pcall(fn) else ok, values = pcall(fn, arg) end
    if not ok or type(values) ~= "table" then return nil end
    local out = {}
    for key, value in pairs(values) do
        -- Host helpers may return either { id, id } or { [id] = true }.
        local pid = tonumber(value)
        if not pid or pid <= 0 then pid = tonumber(key) end
        if pid and pid > 0 then out[math.floor(pid)] = true end
    end
    return out
end

--- Count the senior squad the way FC's TransferPlayer refusal does
--- ("Target Team Squad is Full 42/52, Loaned Out: 10"): players at the club
--- plus players the club has loaned out, against LIMITS.max_squad_with_loans.
--
-- Present players come from the club's teamplayerlinks rows (helper fallback).
-- A loaned-out player's roster row usually moves to the borrowing club, but
-- his contract (GetUserSeniorTeamPlayerIDs reads career_playercontract) and
-- the playerloans row stay with the parent club, so those are the candidates
-- IsPlayerLoanedOut is asked about. Contract-only players that are NOT loaned
-- out (e.g. presigned for next season) do not take a place and are ignored.
-- Returns a plain table; readable=false means "cannot prove there is room".
local function squad_room(teamid)
    local limit = math.floor(tonumber((version.LIMITS or {}).max_squad_with_loans) or 52)
    local info = { readable = false, limit = limit, present = 0, loaned = 0, total = 0, free = 0 }
    teamid = tonumber(teamid)
    if not teamid or teamid <= 0 then
        info.reason = "target team is unknown"
        return info
    end
    teamid = math.floor(teamid)
    local loan_fn = rawget(_G, "IsPlayerLoanedOut")
    if type(loan_fn) ~= "function" then
        info.reason = "IsPlayerLoanedOut host check is unavailable"
        return info
    end
    local present = {}
    local links = db.open("teamplayerlinks")
    if links then
        for rec in links:records() do
            if tonumber(links:get("teamid", rec)) == teamid then
                local pid = tonumber(links:get("playerid", rec))
                if pid and pid > 0 then present[math.floor(pid)] = true end
            end
        end
        info.source = "teamplayerlinks"
    end
    if next(present) == nil then
        for _, helper in ipairs({ "GetPlayerIDSForTeam", "GetPlayerIDsForTeam" }) do
            local ids = host_id_set(helper, teamid)
            if ids and next(ids) ~= nil then
                present, info.source = ids, helper
                break
            end
        end
    end
    if next(present) == nil then
        info.reason = "squad membership is not readable yet"
        return info
    end
    local candidates = {}
    for pid in pairs(present) do candidates[pid] = true end
    if user_team() == teamid then
        for pid in pairs(host_id_set("GetUserSeniorTeamPlayerIDs", nil) or {}) do
            candidates[pid] = true
        end
    end
    local loans = db.open("playerloans")
    if loans then
        for rec in loans:records() do
            if tonumber(loans:get("teamidloanedfrom", rec)) == teamid then
                local pid = tonumber(loans:get("playerid", rec))
                if pid and pid > 0 then candidates[math.floor(pid)] = true end
            end
        end
    end
    local present_n, loaned_n = 0, 0
    for pid in pairs(candidates) do
        local ok, active = pcall(loan_fn, pid)
        if not ok then
            info.reason = "IsPlayerLoanedOut could not inspect player " .. tostring(pid)
            return info
        end
        if host_true(active) then
            loaned_n = loaned_n + 1
        elseif present[pid] then
            present_n = present_n + 1
        end
    end
    info.readable = true
    info.reason = nil
    info.present, info.loaned = present_n, loaned_n
    info.total = present_n + loaned_n
    info.free = limit - info.total
    return info
end

local function squad_room_detail(info)
    return string.format("%d senior + %d on loan = %d of %d",
        tonumber(info.present) or 0, tonumber(info.loaned) or 0,
        tonumber(info.total) or 0, tonumber(info.limit) or 52)
end

--- Fail before ANY write when the senior squad cannot take ``needed`` more
--- players. A whole batch is rejected rather than signing only the first few:
--- the receipt contract maps result playerids 1:1 onto request members, and a
--- silently shortened bag would look like a complete signing.
local function require_squad_room(state, needed)
    local info = squad_room(state.teamid)
    info.needed = needed
    state.squad_room = info
    if not info.readable then
        return fail("squad_check_unavailable",
            "cannot count the senior squad before writing: " .. tostring(info.reason or "unknown"),
            state)
    end
    if info.free < needed then
        local detail = squad_room_detail(info)
        if needed > 1 then
            detail = string.format("%s; room for %d of %d players",
                detail, math.max(0, info.free), needed)
        end
        return fail("squad_full", detail, state)
    end
    return nil
end

local function unused_jersey(links, teamid)
    if not db.field("teamplayerlinks", "jerseynumber") then return nil end
    local used = {}
    for rec in links:records() do
        if tonumber(links:get("teamid", rec)) == teamid then
            local jersey = tonumber(links:get("jerseynumber", rec))
            if jersey and jersey > 0 then used[math.floor(jersey)] = true end
        end
    end
    local jersey = 1
    while jersey <= 99 do
        if not used[jersey] then return jersey end
        jersey = jersey + 1
    end
    return nil
end

--- Move one free-agent roster row onto ``teamid``.
-- TransferPlayer logs "Target Team Squad is Full" and leaves the player on
-- 111592. The roster row itself can still change club. Since the squad-room
-- precheck this is only a fallback for a refusal while the club provably
-- still has a free place (finish_transfer re-counts first); it is never used
-- to push a squad past FC's own limit.
local function relink_free_agent(pid, teamid)
    local links = db.open("teamplayerlinks")
    if not links then return false, "no_teamplayerlinks", "roster table is unavailable" end
    local rec, matches = db.scan_matches(links, "playerid", pid)
    if not rec or matches ~= 1 then
        return false, "link_ambiguous", "expected exactly one roster row for this player"
    end
    local current = tonumber(links:get("teamid", rec))
    if current == teamid then return true, "already", "" end
    if current ~= FREE_AGENT_TEAM_ID then
        return false, "not_free_agent",
            string.format("player is on team %s, not the free-agent roster", tostring(current))
    end
    local fields = { teamid = teamid }
    local jersey = unused_jersey(links, teamid)
    if jersey then fields.jerseynumber = jersey end
    local written, failures = db.set_many(links, rec, fields, { on_field_error = "continue" })
    if failures[1] then
        return false, failures[1].reason or "relink_failed", failures[1].detail or ""
    end
    if (written or 0) < 1 then
        return false, "relink_failed", "roster row did not change"
    end
    return true, "teamplayerlinks", ""
end

--- Returns kind, reason, detail, squad_room.
-- kind is "ok" (player is on teamid), "wait" (host id is still unknown),
-- or "fail". A stable free-agent id never waits: FC already refused the move.
local function finish_transfer(pid, teamid)
    local verified, source = verify_team_membership(pid, teamid)
    if verified then return "ok", source or "verified", "" end
    local actual
    local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(team_fn) == "function" then
        local called, value = pcall(team_fn, pid)
        if called then actual = tonumber(value) end
    end
    if actual == nil or actual < 0 or actual == 0 then
        return "wait", "settling", ""
    end
    if math.floor(actual) == FREE_AGENT_TEAM_ID then
        local room = squad_room(teamid)
        local detail = ""
        if room.readable and room.free >= 1 then
            local moved, how, move_detail = relink_free_agent(pid, teamid)
            if moved then
                local again, again_source = verify_team_membership(pid, teamid)
                if again then return "ok", again_source or how or "teamplayerlinks", "" end
            end
            detail = move_detail or ""
        end
        local counts = room.readable and squad_room_detail(room) or "squad size unreadable"
        return "fail", "squad_full",
            string.format("FC refused the transfer: %s%s", counts,
                (detail ~= "" and ("; " .. detail)) or ""),
            room
    end
    return "fail", "team_not_applied",
        string.format("expected %s, observed %s", tostring(teamid), tostring(actual))
end

local function call_transfer(pid, teamid, contract)
    if type(rawget(_G, "TransferPlayer")) ~= "function" then
        return false, "unsupported_op", "TransferPlayer host API missing"
    end
    local from_team = 0
    local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(team_fn) == "function" then
        local called, value = pcall(team_fn, pid)
        local current = called and tonumber(value) or nil
        if current and current > 0 then from_team = math.floor(current) end
    end
    local c = type(contract) == "table" and contract or {}
    local ok, err = pcall(
        _G.TransferPlayer, pid, teamid,
        tonumber(c.transfersum) or 0, tonumber(c.wage) or 5000,
        tonumber(c.months) or 60, from_team, tonumber(c.release_clause) or -1)
    if not ok then return false, "transfer_threw", tostring(err) end
    return true, nil, nil
end

local function set_map(pid, fields)
    if util.is_empty(fields or {}) then return 0, {}, {} end
    local h, rec, reason = player_record(pid)
    if not rec then return 0, { { reason = reason } }, {} end
    local written, failures, _, applied = db.set_many(
        h, rec, fields, { on_field_error = "continue" })
    return written, failures, applied
end

--- Read the dummy's current value of every field the signing is about to
--- write, BEFORE the write. Only the first capture of a field is kept, so a
--- resumed step can never overwrite a pre-signing value with a card value.
-- The map travels in the persisted progress (and therefore in a failed
-- result), which is what lets the desktop offer "Restore free agent" for a
-- signing that wrote stats but never transferred. Unreadable fields are left
-- out; the desktop refuses to restore a field it has no original for.
local function capture_originals(pid, maps, into)
    local h, rec = player_record(pid)
    if not rec then return into end
    for _, map in ipairs(maps) do
        for field in pairs(map or {}) do
            if field ~= "playerid" and into[field] == nil then
                local value = h:get(field, rec)
                if value ~= nil then into[field] = value end
            end
        end
    end
    return into
end

local function record_applied(into, applied)
    for field, value in pairs(applied or {}) do into[field] = value end
end

local function protect_potential(fields)
    -- Historic/special cards often omit ``potential``.  Add Player transforms
    -- a low-rated free agent, so an omitted value otherwise leaves nonsense
    -- such as 90 OVR / 41 POT from the dummy.  Potential can never be below
    -- the requested overall for this cloned Career player.
    if type(fields) ~= "table" then return false end
    local overall = tonumber(fields.overallrating)
    if not overall or overall <= 0 then return false end
    overall = math.max(1, math.min(99, math.floor(overall)))
    local potential = tonumber(fields.potential)
    if not potential or potential < overall then
        fields.potential = overall
        return true
    end
    return false
end

local function requested_name_from_player(player)
    local n = ((player or {}).names or {})
    local common = tostring(n.commonname or "")
    if common ~= "" then return common end
    local first = tostring(n.firstname or "")
    local sur = tostring(n.surname or "")
    return (first .. " " .. sur):gsub("^%s+", ""):gsub("%s+$", ""):gsub("%s+", " ")
end

local function requested_name(op)
    return requested_name_from_player(op.player)
end

local function name_matches(actual, expected)
    local function norm(v)
        -- Strip only whitespace/punctuation. Do NOT use [^%w]: that drops
        -- non-ASCII letters (ć in Modrić) and falsely fails durable name checks.
        return tostring(v or ""):lower():gsub("[%s%p]+", "")
    end
    if expected == "" then return true end
    local a, e = norm(actual), norm(expected)
    if a == e then return true end
    -- Exact raw match after trim (preserves accents on both sides).
    local function trim(s)
        return tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", "")
    end
    return trim(actual):lower() == trim(expected):lower()
end

-- GetPlayerName is the only host-side name resolver available to this worker.
-- A card display name may omit a first name ("Iniesta" vs "Andrés Iniesta"),
-- so accept a complete normalized component match, but never an empty or
-- one-character match.  A durable database row alone is *not* proof: FC can
-- retain a blank/dummy label in Squad Hub after that row exists.
local function visible_name_matches(actual, expected)
    if name_matches(actual, expected) then return true end
    -- Compare complete name components, not raw substrings. This accepts
    -- "Iniesta" versus "Andrés Iniesta" but rejects "Ben" versus
    -- "Benedict", so an unchanged dummy can never satisfy the gate.
    local function tokens(v)
        local out = {}
        for token in tostring(v or ""):lower():gmatch("[^%s%p]+") do
            if token ~= "" then out[#out + 1] = token end
        end
        return out
    end
    local function contains_sequence(haystack, needle)
        if #needle == 0 or #needle > #haystack then return false end
        for start = 1, #haystack - #needle + 1 do
            local matched = true
            for offset = 1, #needle do
                if haystack[start + offset - 1] ~= needle[offset] then
                    matched = false
                    break
                end
            end
            if matched then return true end
        end
        return false
    end
    local got, want = tokens(actual), tokens(expected)
    return contains_sequence(got, want)
end

local function name_fields(names)
    local first = tostring(names.firstname or "")
    local sur = tostring(names.surname or "")
    local jersey = tostring(names.playerjerseyname or names.surname or "")
    local common = tostring(names.commonname or "")
    -- An empty commonname leaves Free Agent labels in Career Mode UI and has
    -- also been observed to make InsertDBTableRow reject the whole name row.
    if common == "" then
        if first ~= "" and sur ~= "" then
            common = first .. " " .. sur
        elseif sur ~= "" then
            common = sur
        elseif first ~= "" then
            common = first
        else
            common = "Player"
        end
    end
    if jersey == "" then jersey = common end
    return {
        firstname = first,
        surname = sur,
        playerjerseyname = jersey,
        commonname = common,
    }
end

local function row_playerid(row)
    if type(row) ~= "table" or row.playerid == nil then return nil end
    local raw = row.playerid
    if type(raw) == "table" then return tostring(raw.value or "") end
    return tostring(raw)
end

local function row_field_text(row, fname)
    if type(row) ~= "table" then return "" end
    local raw = row[fname]
    if type(raw) == "table" then return tostring(raw.value or "") end
    if raw == nil then return "" end
    return tostring(raw)
end

-- Career Squad Hub reads durable editedplayernames content (esp. commonname),
-- not a single cached GetPlayerName string. Every matching row must carry the
-- requested strings; a shell/stale first row is the classic blank-name failure.
local function name_row_content_matches(row, fields)
    if type(row) ~= "table" or type(fields) ~= "table" then return false end
    for _, fname in ipairs({ "firstname", "surname", "playerjerseyname", "commonname" }) do
        local want = tostring(fields[fname] or "")
        if want ~= "" and not name_matches(row_field_text(row, fname), want) then
            return false
        end
    end
    -- commonname is what Squad Hub displays for custom/detached names.
    local common = tostring(fields.commonname or "")
    if common == "" then return false end
    return name_matches(row_field_text(row, "commonname"), common)
end

local function edited_rows_for_pid(rows, pid_s)
    local out = {}
    if type(rows) ~= "table" then return out end
    for _, row in pairs(rows) do
        if row_playerid(row) == pid_s then
            out[#out + 1] = row
        end
    end
    return out
end

local function durable_name_ok(pid, fields)
    local want = name_fields(fields or {})
    local pid_s = tostring(math.floor(tonumber(pid) or 0))
    if pid_s == "0" or want.commonname == "" then return false, 0 end
    if type(rawget(_G, "GetDBTableRows")) == "function" then
        local ok, rows = pcall(_G.GetDBTableRows, "editedplayernames")
        if ok and type(rows) == "table" then
            local matches = edited_rows_for_pid(rows, pid_s)
            if #matches == 0 then return false, 0 end
            for i = 1, #matches do
                if not name_row_content_matches(matches[i], want) then
                    return false, #matches
                end
            end
            return true, #matches
        end
    end
    -- Cursor fallback: every match must hold the requested commonname.
    local h = db.open("editedplayernames")
    if not h then return false, 0 end
    local count, good = 0, 0
    for rec in h:records() do
        local v = h:get("playerid", rec)
        if v == pid_s or tonumber(v) == tonumber(pid_s) then
            count = count + 1
            local actual = {
                firstname = tostring(h:get("firstname", rec) or ""),
                surname = tostring(h:get("surname", rec) or ""),
                playerjerseyname = tostring(h:get("playerjerseyname", rec) or ""),
                commonname = tostring(h:get("commonname", rec) or ""),
            }
            if name_row_content_matches(actual, want) then
                good = good + 1
            end
        end
    end
    if count == 0 or good ~= count then return false, count end
    return true, count
end

local function edit_name_row(row, fields)
    if type(rawget(_G, "EditDBTableField")) ~= "function" then return false end
    local wrote = 0
    for fname, fval in pairs(fields) do
        local fld = row[fname]
        if type(fld) == "table" then
            fld.value = fval
            if pcall(_G.EditDBTableField, fld) then wrote = wrote + 1 end
        end
    end
    return wrote > 0
end

local function snapshot_name_dict(pid)
    local h, rec = player_record(pid)
    if not rec then return nil end
    local out = {}
    for _, fname in ipairs({
        "firstnameid", "lastnameid", "commonnameid",
        "playerjerseynameid", "usercaneditname",
    }) do
        if db.field("players", fname) then
            out[fname] = h:get(fname, rec)
        end
    end
    return out
end

-- If the guarded custom-name route cannot become visible in FC, restore the
-- source dummy's dictionary IDs before surfacing the failure. That leaves a
-- recoverable incomplete signing instead of a player with a blank label.
local function restore_name_dict(pid, previous)
    if type(previous) ~= "table" or util.is_empty(previous) then
        return false, 0, { { reason = "name_dictionary_snapshot_missing" } }
    end
    local restore = {}
    for _, fname in ipairs({
        "firstnameid", "lastnameid", "commonnameid",
        "playerjerseynameid", "usercaneditname",
    }) do
        if previous[fname] ~= nil then restore[fname] = previous[fname] end
    end
    if util.is_empty(restore) then
        return false, 0, { { reason = "name_dictionary_snapshot_missing" } }
    end
    local written, failures = set_map(pid, restore)
    return #failures == 0, written, failures
end

local function write_names(pid, names)
    local fields = name_fields(names or {})
    local pid_s = tostring(math.floor(tonumber(pid) or 0))
    if pid_s == "0" then return false, "bad_playerid", 0 end
    if fields.commonname == "" then return false, "empty_commonname", 0 end

    -- editedplayernames is small. The proven Live Editor path materialises it
    -- with GetDBTableRows + EditDBTableField / InsertDBTableRow. Success must
    -- mean durable content, not merely a visible shell row or a host addr.
    if type(rawget(_G, "GetDBTableRows")) == "function" then
        local ok, rows = pcall(_G.GetDBTableRows, "editedplayernames")
        if ok and type(rows) == "table" then
            local matches = edited_rows_for_pid(rows, pid_s)
            if #matches > 0 then
                local wrote = false
                for i = 1, #matches do
                    if edit_name_row(matches[i], fields) then wrote = true end
                end
                if not wrote then return false, "name_write_failed", 0 end
                local content_ok = durable_name_ok(pid, fields)
                if content_ok then return true, nil, 0 end
                return false, "name_not_applied", 0
            end
            if type(rawget(_G, "InsertDBTableRow")) == "function" then
                local row_data = {
                    playerid = pid_s,
                    firstname = fields.firstname,
                    surname = fields.surname,
                    playerjerseyname = fields.playerjerseyname,
                    commonname = fields.commonname,
                }
                local iok, inserted = pcall(_G.InsertDBTableRow, "editedplayernames", row_data)
                -- Some builds need EditDBTableField on the insert return itself.
                if iok and type(inserted) == "table" then
                    edit_name_row(inserted, fields)
                end
                -- Re-read: some LE builds return a useless addr even on success,
                -- and some return a non-zero addr that never becomes visible.
                local vok, after = pcall(_G.GetDBTableRows, "editedplayernames")
                local found = 0
                if vok and type(after) == "table" then
                    local after_matches = edited_rows_for_pid(after, pid_s)
                    found = #after_matches
                    for i = 1, #after_matches do
                        edit_name_row(after_matches[i], fields)
                    end
                end
                if found > 0 then
                    local content_ok = durable_name_ok(pid, fields)
                    if content_ok then return true, nil, 1 end
                    return false, "name_not_applied", 0
                end
                local addr = ""
                if iok and type(inserted) == "table" then
                    local raw = inserted.addr
                    if type(raw) == "table" then raw = raw.value end
                    addr = tostring(raw or "")
                end
                if not iok then
                    return false, "insert_threw", 0
                end
                if addr == "" or addr == "0" or addr == "nil" or addr:match("^table:") then
                    return false, "insert_rejected", 0
                end
                return false, "insert_unverified", 0
            end
        end
    end

    -- Fallback: cursor API used by set_fields elsewhere in the core.
    -- Update EVERY matching row — a stale first-of-N win blanks Career UI.
    local h, rec, _, matches = db.resolve("editedplayernames", "playerid", pid)
    if not h then
        h, rec, _, matches = db.resolve("editedplayernames", "playerid", pid_s)
    end
    if not h then return false, "no_editedplayernames_table", 0 end
    if rec then
        local any_written = false
        for crec in h:records() do
            local v = h:get("playerid", crec)
            if v == pid_s or tonumber(v) == tonumber(pid_s) then
                local written, failures = db.set_many(
                    h, crec, fields, { on_field_error = "continue" })
                if #failures > 0 then
                    return false, failures[1].reason or "name_write_failed", 0
                end
                if written > 0 then any_written = true end
            end
        end
        if not any_written and (matches or 0) > 0 then
            return false, "name_write_failed", 0
        end
        local content_ok = durable_name_ok(pid, fields)
        if content_ok then return true, nil, 0 end
        return false, "name_not_applied", 0
    end
    local row = { playerid = pid_s }
    for k, v in pairs(fields) do row[k] = v end
    local inserted, reason = db.insert_row("editedplayernames", row)
    db.reset()
    h, rec = db.resolve("editedplayernames", "playerid", pid)
    if not h or not rec then
        -- Text key first: FC stores editedplayernames.playerid as a string.
        h, rec = db.resolve("editedplayernames", "playerid", pid_s)
    end
    if not h or not rec then return false, reason or "name_insert_failed", 0 end
    -- Write every match after insert (host may materialise duplicates).
    for crec in h:records() do
        local v = h:get("playerid", crec)
        if v == pid_s or tonumber(v) == tonumber(pid_s) then
            local written, failures = db.set_many(
                h, crec, fields, { on_field_error = "continue" })
            if #failures > 0 then
                return false, failures[1].reason or "name_write_failed", 0
            end
            if written == 0 then return false, "name_write_failed", 0 end
        end
    end
    local content_ok = durable_name_ok(pid, fields)
    if content_ok then return true, nil, inserted and 1 or 0 end
    return false, "name_not_applied", 0
end

-- 2.6.42: the name step proves the identity FC renders from the database.
--
-- GetPlayerName is not proof of anything in the running session: it answers
-- from Live Editor's own player cache, which LE builds once and clears only
-- in its native DAY_PASSED / POST_LOAD_PREPARE listeners (FCLiveEditor.DLL
-- v26.3.6, MasterManager registration at 0x180363d00). In the same hub visit
-- it keeps returning the dummy's pre-signing name, so the old "visible name"
-- gate waited 8 Career events and then failed signings whose names were
-- already stored. FC itself renders the players-row dictionary ids, or the
-- editedplayernames row once those ids are detached (ops/names_sync.lua).
local DICT_FIELDS = { "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid" }

local function dictionary_ids(pid)
    local h, rec = player_record(pid)
    if not rec then return nil end
    local out = {}
    for _, field in ipairs({
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid", "usercaneditname",
    }) do
        out[field] = tonumber(h:get(field, rec))
    end
    return out
end

--- Returns ok, reason, detail for the stored name identity of ``pid``.
local function stored_name_ok(pid, player, strategy, native_ids)
    local dict = dictionary_ids(pid)
    if not dict then return false, "player_not_found", "players row is not readable" end
    if strategy == "native_ids" then
        for _, field in ipairs(DICT_FIELDS) do
            if dict[field] ~= (native_ids or {})[field] then
                return false, "native_name_not_stored", string.format(
                    "%s reads %s, expected %s", field, tostring(dict[field]),
                    tostring((native_ids or {})[field]))
            end
        end
        if dict.usercaneditname ~= 0 then
            return false, "native_name_not_stored", "usercaneditname reads "
                .. tostring(dict.usercaneditname) .. ", expected 0"
        end
        return true
    end
    if not durable_name_ok(pid, player.names or {}) then
        return false, "name_not_applied", "editedplayernames row is missing or stale"
    end
    -- Career renders the editedplayernames row only once the dictionary ids
    -- are detached; with them attached it still shows the dummy's name, so a
    -- custom name is never proven without the detach.
    if player.detach_name_dictionary == false then
        return false, "name_not_detached",
            "custom name stored, but the dictionary ids were kept, so FC still shows the old name"
    end
    for _, field in ipairs(DICT_FIELDS) do
        if dict[field] ~= 0 then
            return false, "name_detach_not_stored", field .. " reads "
                .. tostring(dict[field]) .. ", expected 0"
        end
    end
    if dict.usercaneditname ~= 1 then
        return false, "name_detach_not_stored", "usercaneditname reads "
            .. tostring(dict.usercaneditname) .. ", expected 1"
    end
    return true
end

--- Live Editor's cached name for diagnostics only (see above). nil when the
--- host has no GetPlayerName or it threw.
local function le_cache_name(pid)
    local fn = rawget(_G, "GetPlayerName")
    if type(fn) ~= "function" then return nil end
    local ok, value = pcall(fn, pid)
    if not ok then return nil end
    return tostring(value or "")
end

--- Record the outcome of the name step on ``into`` (a member or the single
--- path's state): what was proven, and what LE's cache still says.
local function record_name_proof(into, pid, expected)
    local cached = le_cache_name(pid)
    into.visible_name_expected = expected
    into.name_verified_via = "database"
    into.le_cache_name = cached
    into.le_cache_current = cached ~= nil and expected ~= ""
        and visible_name_matches(cached, expected) or false
    -- The stored display name; older readers use this field as the name.
    into.host_player_name = expected
end

--- Mark a free-agent dummy as used in this save (idempotent). Done as soon
--- as the dummy is on the club, so a later failure in the name step can never
--- leave it pickable for another signing.
local function consume_now(ctx, holder, state, pid)
    if holder.slot_consumed then return true end
    local consumed_path = state.consumed_path or holder.consumed_path
    local path, consumed, consume_reason = consumed_dummy_store(ctx)
    if consumed_path and consumed_path ~= "" then path = consumed_path end
    if not path or type(consumed) ~= "table" then
        return false, consume_reason or "consumed_slot_unavailable"
    end
    local marked, mark_reason = consume_dummy(path, consumed, pid)
    if not marked then return false, mark_reason or "consumed_slot_write_failed" end
    holder.slot_consumed = true
    state.consumed_path = path
    return true
end

local function members_from_op(op)
    if type(op.batch) == "table" and #op.batch >= 1 then
        return op.batch
    end
    return { {
        dummy_pool = op.dummy_pool,
        dummy_filter = op.dummy_filter,
        player = op.player,
        contract = op.contract,
    } }
end

local function member_at(list, index)
    if type(list) ~= "table" then return nil end
    return list[index] or list[tostring(index)]
end

local function ensure_member_states(state, count)
    state.members = type(state.members) == "table" and state.members or {}
    state.member_count = tonumber(state.member_count) or count
    local i = 1
    while i <= count do
        if type(member_at(state.members, i)) ~= "table" then
            state.members[i] = {}
        end
        i = i + 1
    end
    return state.members
end

local function try_pick_member(member, consumed, used, on_club)
    local pool = type(member.dummy_pool) == "table" and member.dummy_pool or {}
    local filter = type(member.dummy_filter) == "table" and member.dummy_filter or {}
    local n = math.min(#pool, 6)
    if n < 1 then
        return "fail", "bad_dummy_pool",
            "Add Player needs at least one reviewed free-agent dummy"
    end
    local saw_unknown = false
    local last_reason, last_detail = "no_dummy", "no eligible dummy in the explicit pool"
    for i = 1, n do
        local pid = tonumber(pool[i])
        if not pid or pid <= 0 or pid > 459999 then
            last_reason, last_detail = "bad_dummy", "dummy id is outside the real-player safety range"
        else
            pid = math.floor(pid)
            if used[pid] or dummy_is_consumed(consumed, pid) then
                last_reason, last_detail = "dummy_already_used",
                    "this free-agent dummy was already consumed in the current save"
            else
                local use_filter = filter
                if i > 1 then
                    use_filter = util.copy(filter)
                    use_filter.expected_overallrating = nil
                end
                local clean, reason, detail = safe_free_agent(pid, use_filter, on_club)
                if clean then
                    return "ok", pid, i
                end
                if reason == "dummy_team_unknown" then
                    saw_unknown = true
                elseif reason == "slot_probe_unavailable" or reason == "slot_probe_failed" then
                    return "fail", reason or "no_dummy", detail or ""
                end
                last_reason, last_detail = reason or last_reason, detail or last_detail
            end
        end
    end
    if saw_unknown then return "wait" end
    return "fail", last_reason, last_detail
end

local function finish_member_name(player, mstate, verify, ctx, state)
    local strategy = tostring(player.name_strategy or "custom")
    local inserted = 0
    local native_ids = nil
    local function fail_after_custom_detach(reason, detail)
        if strategy ~= "custom" or not mstate.name_dictionary_detached then
            return fail(reason, detail, state)
        end
        local restored, restored_written, restore_failures = restore_name_dict(
            mstate.playerid, mstate.prev_nameids)
        state.counts = state.counts or {}
        state.counts.fields_written = (state.counts.fields_written or 0) + restored_written
        mstate.name_dictionary_restore = {
            attempted = true,
            restored = restored,
            failures = restore_failures,
        }
        if restored then
            mstate.name_dictionary_detached = false
            return fail(reason, detail .. "; restored the original FC name identity", state)
        end
        local restore_reason = tostring((restore_failures[1] or {}).reason or "unknown")
        return fail(reason .. "_restore_failed",
            detail .. "; could not restore the original FC name identity (" .. restore_reason .. ")",
            state)
    end
    if strategy == "native_ids" then
        local native, count = {}, 0
        for _, field in ipairs({
            "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid",
        }) do
            local value = tonumber((player.fields or {})[field])
            if value == nil or value < 0 then
                return fail("native_name_ids_missing",
                    "native name strategy requires four verified dictionary ids", state)
            end
            native[field] = math.floor(value)
            count = count + 1
        end
        if count ~= 4 or not (
            native.firstnameid > 0 or native.lastnameid > 0
            or native.commonnameid > 0 or native.playerjerseynameid > 0
        ) then
            return fail("native_name_ids_missing",
                "native name strategy requires at least one real dictionary id", state)
        end
        native_ids = util.copy(native)
        native.usercaneditname = 0
        local nwritten, nfail = set_map(mstate.playerid, native)
        if #nfail > 0 or count ~= 4 then
            return fail((nfail[1] or {}).reason or "native_name_write_failed",
                "could not activate verified native name dictionary ids", state)
        end
        state.counts = state.counts or {}
        state.counts.fields_written = (state.counts.fields_written or 0) + nwritten
    elseif strategy == "custom" then
        local wok, wreason, wrote = write_names(mstate.playerid, player.names or {})
        inserted = wrote
        if not wok then
            mstate.verify_name_tries = (tonumber(mstate.verify_name_tries) or 0) + 1
            if mstate.verify_name_tries < 6 then
                return wait_event(state, { waiting_for = "name_write", reason = wreason,
                    tries = mstate.verify_name_tries, playerid = mstate.playerid })
            end
            return fail(wreason or "name_write_failed",
                "could not write editedplayernames after transfer", state)
        end
        if player.detach_name_dictionary ~= false then
            if type(mstate.prev_nameids) ~= "table" or util.is_empty(mstate.prev_nameids) then
                mstate.prev_nameids = snapshot_name_dict(mstate.playerid) or {}
            end
            if util.is_empty(mstate.prev_nameids) then
                return fail("name_dictionary_snapshot_missing",
                    "custom name cannot safely detach the original FC identity", state)
            end
            local detach = {
                firstnameid = 0, lastnameid = 0, commonnameid = 0,
                playerjerseynameid = 0, usercaneditname = 1,
            }
            local dwritten, dfail = set_map(mstate.playerid, detach)
            if dwritten > 0 then mstate.name_dictionary_detached = true end
            if #dfail > 0 then
                mstate.verify_name_tries = (tonumber(mstate.verify_name_tries) or 0) + 1
                if mstate.verify_name_tries < 6 then
                    return wait_event(state, { waiting_for = "name_detach",
                        reason = dfail[1].reason, tries = mstate.verify_name_tries,
                        playerid = mstate.playerid })
                end
                return fail_after_custom_detach(dfail[1].reason or "name_detach_failed",
                    "custom name was saved but could not be activated")
            end
            state.counts = state.counts or {}
            state.counts.fields_written = (state.counts.fields_written or 0) + dwritten
        end
    else
        return fail("bad_name_strategy", "expected native_ids or custom", state)
    end

    local expected = requested_name_from_player(player)
    if (verify or {}).name ~= false then
        local stored, why, detail = stored_name_ok(mstate.playerid, player, strategy, native_ids)
        if not stored then
            return fail_after_custom_detach(why, detail or "")
        end
    end
    record_name_proof(mstate, mstate.playerid, expected)
    local consumed_ok, consume_reason = consume_now(ctx, mstate, state, mstate.playerid)
    if not consumed_ok then
        return fail(consume_reason or "consumed_slot_write_failed",
            "name stored but the free-agent slot could not be reserved", state)
    end
    mstate.name_done = true
    mstate.name_rows_inserted = inserted
    return nil
end

local function step_batch(op, job, ctx, state)
    state.batch = true
    state.total_steps = #BATCH_STEP_NAMES
    local step = tonumber(state.step) or 1
    local batch = members_from_op(op)
    local count = #batch
    if count < 1 then
        return fail("bad_dummy_pool", "Add Player needs at least one reviewed free-agent dummy", state)
    end
    local members = ensure_member_states(state, count)

    if step == 1 then
        local tid = tonumber(op.teamid)
        local live_teamid = user_team()
        if tid and live_teamid and tid ~= live_teamid then
            return fail("team_changed",
                string.format("queued team %s does not match active user team %s",
                    tostring(tid), tostring(live_teamid)), state)
        end
        if not tid or tid <= 0 then tid = user_team() end
        if not tid or tid <= 0 then return fail("no_teamid", "could not resolve target team", state) end
        state.teamid = math.floor(tid)
        -- Squad room before ANY dummy is picked or written. FC refuses
        -- TransferPlayer at 52 including loans; writing first left free
        -- agents with a card's stats that never joined (2026-10-15).
        -- A manual relink member already on the club needs no new place.
        local needed = 0
        local j = 1
        while j <= count do
            local member = member_at(batch, j) or {}
            local placed = false
            if member.relink_only then
                local pool = type(member.dummy_pool) == "table" and member.dummy_pool or {}
                local pid = tonumber(pool[1] or member.playerid)
                placed = pid ~= nil and (verify_team_membership(pid, state.teamid)) == true
            end
            if not placed then needed = needed + 1 end
            j = j + 1
        end
        state.squad_needed = needed
        if needed > 0 then
            local refused = require_squad_room(state, needed)
            if refused then return refused end
        end
        return next_step(state, { teamid = state.teamid, batch = count })
    end

    -- pick_all: assign every dummy before any TransferPlayer
    if step == 2 then
        local consumed_path, consumed, consume_reason = consumed_dummy_store(ctx)
        if not consumed_path then
            return fail(consume_reason or "consumed_slot_unavailable",
                "cannot prove this dummy has not already been used in this save", state)
        end
        state.consumed_path = consumed_path
        local used = {}
        local i = 1
        while i <= count do
            local mstate = member_at(members, i)
            local pid = tonumber(mstate and mstate.playerid)
            if pid and pid > 0 then used[math.floor(pid)] = true end
            i = i + 1
        end
        -- One DB read for the whole bag: dummies already on the club.
        local on_club = (user_team() == state.teamid)
            and host_id_set("GetUserSeniorTeamPlayerIDs", nil) or nil
        local saw_wait = false
        local last_reason, last_detail = "no_dummy", "no eligible dummy in the explicit pool"
        i = 1
        while i <= count do
            local mstate = member_at(members, i)
            if not (mstate and tonumber(mstate.playerid) and tonumber(mstate.playerid) > 0) then
                local member = member_at(batch, i) or {}
                if member.relink_only then
                    local pool = type(member.dummy_pool) == "table" and member.dummy_pool or {}
                    local pid = tonumber(pool[1] or member.playerid)
                    if not pid or pid <= 0 then
                        return fail("bad_dummy", "relink needs the free-agent player id", state)
                    end
                    pid = math.floor(pid)
                    if used[pid] then
                        return fail("duplicate_relink", "the same player was listed twice", state)
                    end
                    mstate.playerid = pid
                    mstate.fields_written = true
                    mstate.relink_only = true
                    used[pid] = true
                else
                    local kind, a, b = try_pick_member(member, consumed, used, on_club)
                    if kind == "ok" then
                        mstate.playerid, mstate.dummy_index = a, b
                        used[a] = true
                    elseif kind == "wait" then
                        saw_wait = true
                    else
                        return fail(a or last_reason, b or last_detail, state)
                    end
                end
            end
            i = i + 1
        end
        local all_picked = true
        i = 1
        while i <= count do
            local mstate = member_at(members, i)
            if not (mstate and tonumber(mstate.playerid) and tonumber(mstate.playerid) > 0) then
                all_picked = false
            end
            i = i + 1
        end
        if all_picked then
            return next_step(state, { strategy = "dummy_overwrite", found = count },
                { found = count, targets = count })
        end
        if saw_wait then
            state.pick_dummy_tries = (tonumber(state.pick_dummy_tries) or 0) + 1
            if state.pick_dummy_tries >= 12 then
                return fail(last_reason or "dummy_team_unknown", last_detail or "", state)
            end
            return wait_event(state, {
                waiting_for = "dummy_team",
                tries = state.pick_dummy_tries,
            })
        end
        return fail(last_reason, last_detail, state)
    end

    -- write_all: fields and faces for every reserved dummy
    if step == 3 then
        -- A pick that waited for Career events may have let the squad
        -- change. Count again, still before the first field write.
        if (tonumber(state.pick_dummy_tries) or 0) > 0 and not state.room_rechecked then
            local any_written = false
            local j = 1
            while j <= count do
                local mstate = member_at(members, j)
                if mstate and mstate.fields_written and not mstate.relink_only then
                    any_written = true
                end
                j = j + 1
            end
            local needed = tonumber(state.squad_needed) or count
            if not any_written and needed > 0 then
                local refused = require_squad_room(state, needed)
                if refused then return refused end
            end
            state.room_rechecked = true
        end
        local written_total = 0
        local i = 1
        while i <= count do
            local member = member_at(batch, i) or {}
            local mstate = member_at(members, i)
            local player = type(member.player) == "table" and member.player or {}
            local pid = tonumber(mstate.playerid)
            if not pid or pid <= 0 then
                return fail("lost_progress", "playerid missing", state)
            end
            if not mstate.fields_written then
                local fields = util.copy(player.fields or {})
                fields.playerid = nil
                protect_potential(fields)
                local face = type(player.face) == "table" and player.face or {}
                mstate.original_values = capture_originals(pid, { fields, face },
                    type(mstate.original_values) == "table" and mstate.original_values or {})
                mstate.written_values = type(mstate.written_values) == "table"
                    and mstate.written_values or {}
                local written, failures, applied = set_map(pid, fields)
                record_applied(mstate.written_values, applied)
                if #failures > 0 then
                    return fail(failures[1].reason or "field_write_failed",
                        failures[1].detail or "", state)
                end
                mstate.requested_fields = fields
                written_total = written_total + written
                local fwritten, ffail, fapplied = set_map(pid, face)
                record_applied(mstate.written_values, fapplied)
                if #ffail > 0 then
                    return fail(ffail[1].reason or "face_write_failed",
                        ffail[1].detail or "", state)
                end
                written_total = written_total + fwritten
                mstate.fields_written = true
            end
            i = i + 1
        end
        return next_step(state, { names_deferred = true }, { fields_written = written_total })
    end

    -- transfer_all: native TransferPlayer, then a roster-row move when FC
    -- rejects the call because the senior squad is already full.
    if step == 4 then
        local i = 1
        local waiting = false
        local placed = 0
        while i <= count do
            local member = member_at(batch, i) or {}
            local mstate = member_at(members, i)
            local pid = tonumber(mstate.playerid)
            if not pid or pid <= 0 then
                return fail("lost_progress", "playerid missing", state)
            end
            if not mstate.transfer_started then
                local called, reason, detail = call_transfer(pid, state.teamid, member.contract)
                if not called then return fail(reason, detail, state) end
                mstate.transfer_started = true
            end
            if not mstate.transferred then
                local kind, reason, detail, room = finish_transfer(pid, state.teamid)
                if kind == "ok" then
                    mstate.transferred = true
                    mstate.transfer_via = reason
                    placed = placed + 1
                    -- The dummy is on the club now: never offer it again, even
                    -- if a later step fails. A write error is retried (and then
                    -- fatal) in the name step.
                    consume_now(ctx, mstate, state, pid)
                elseif kind == "wait" then
                    waiting = true
                else
                    if room then state.squad_room = room end
                    return fail(reason, detail, state)
                end
            else
                placed = placed + 1
            end
            i = i + 1
        end
        if waiting then
            state.verify_team_tries = (tonumber(state.verify_team_tries) or 0) + 1
            if state.verify_team_tries >= 12 then
                return fail("team_verify_unavailable",
                    "membership stayed unreadable after 12 waits", state)
            end
            return wait_event(state, {
                waiting_for = "team",
                expected_teamid = state.teamid,
                tries = state.verify_team_tries,
            })
        end
        return next_step(state, { transferred = count }, { side_effects = placed })
    end

    if step == 5 then
        local all_ok = true
        local any_readable = false
        local last_actual = nil
        local i = 1
        while i <= count do
            local mstate = member_at(members, i)
            local verified, source, actual, readable = verify_team_membership(
                mstate.playerid, state.teamid)
            any_readable = any_readable or readable
            last_actual = actual
            if verified then
                mstate.team_verified = true
                mstate.verify_source = source
            else
                local kind, reason, detail, room = finish_transfer(mstate.playerid, state.teamid)
                if kind == "ok" then
                    mstate.team_verified = true
                    mstate.verify_source = reason
                elseif kind == "fail" then
                    if room then state.squad_room = room end
                    return fail(reason, detail, state)
                else
                    all_ok = false
                end
            end
            i = i + 1
        end
        if all_ok then
            return next_step(state, { team_verified = true, batch = count })
        end
        state.verify_team_tries = (tonumber(state.verify_team_tries) or 0) + 1
        if state.verify_team_tries >= 12 then
            local reason = any_readable and "team_not_applied" or "team_verify_unavailable"
            return fail(reason,
                string.format("expected %s, observed %s after %s waits (membership sources=%s)",
                    state.teamid, tostring(last_actual), state.verify_team_tries,
                    any_readable and "readable" or "unavailable"), state)
        end
        return wait_event(state, {
            waiting_for = "team",
            expected_teamid = state.teamid,
            actual_teamid = last_actual,
            membership_sources = any_readable and "readable" or "unavailable",
            tries = state.verify_team_tries,
        })
    end

    if step == 6 then
        local written_total = 0
        local i = 1
        while i <= count do
            local member = member_at(batch, i) or {}
            local mstate = member_at(members, i)
            local player = type(member.player) == "table" and member.player or {}
            local requested = util.copy(mstate.requested_fields or player.fields or {})
            protect_potential(requested)
            mstate.requested_fields = requested
            local h, rec, reason = player_record(mstate.playerid)
            if not rec then return fail(reason or "player_not_found", "", state) end
            local differs = {}
            for field, expected in pairs(requested) do
                if field ~= "playerid" and h:get(field, rec) ~= expected then
                    differs[field] = expected
                end
            end
            local written, failures = set_map(mstate.playerid, differs)
            if #failures > 0 then
                return fail(failures[1].reason or "reapply_failed",
                    failures[1].detail or "", state)
            end
            written_total = written_total + written
            local face = type(player.face) == "table" and player.face or {}
            if not util.is_empty(face) then
                local fwritten, ffail = set_map(mstate.playerid, face)
                if #ffail > 0 then
                    return fail(ffail[1].reason or "face_write_failed",
                        ffail[1].detail or "", state)
                end
                written_total = written_total + fwritten
            end
            i = i + 1
        end
        return next_step(state, { reapplied = true }, { fields_written = written_total })
    end

    if step == 7 then
        local start_at = tonumber(state.name_index) or 1
        local i = start_at
        while i <= count do
            local member = member_at(batch, i) or {}
            local mstate = member_at(members, i)
            local player = type(member.player) == "table" and member.player or {}
            if not mstate.name_done then
                local res = finish_member_name(player, mstate, op.verify, ctx, state)
                if res then
                    state.name_index = i
                    return res
                end
            end
            i = i + 1
        end
        local ids, first = {}, nil
        local cache_stale = false
        i = 1
        while i <= count do
            local mstate = member_at(members, i)
            local pid = tonumber(mstate.playerid)
            if pid then
                ids[#ids + 1] = pid
                if not first then first = pid end
            end
            if mstate.le_cache_current == false then cache_stale = true end
            i = i + 1
        end
        state.step = #BATCH_STEP_NAMES + 1
        return {
            ok = true, deferred = false, counts = state.counts,
            data = {
                playerid = first, teamid = state.teamid,
                playerids = ids,
                verified = true, steps = #BATCH_STEP_NAMES,
                batch = count,
                visible_name_verified = true,
                name_verified_via = "database",
                -- LE's own name list (GetPlayerName) catches up after the next
                -- day advance or save load; FC reads the stored identity.
                le_name_cache_stale = cache_stale,
            },
            progress = state, failures = {},
        }
    end

    return fail("bad_resume_step", tostring(step), state)
end

function M.step(op, job, ctx)
    local state = util.copy((ctx and ctx.resume_state) or {})
    state.step = tonumber(state.step) or 1
    state.counts = state.counts or {}
    local step = state.step
    local player = type(op.player) == "table" and op.player or {}

    -- v1/v2 jobs can still be syntactically understood by the registry, but
    -- they carry the unsafe name contract that detached native IDs before a
    -- real FC-visible verification. Refuse them before any further mutation;
    -- a user can re-review the card under v3 or use the guarded repair route.
    if tonumber(op.v or 1) < 3 then
        return fail("stale_add_player_contract",
            "Add Player v3 is required for verified FC-visible player names; re-review this card",
            state)
    end

    -- Defense in depth for jobs written by an older desktop app.  Add Player
    -- no longer creates rows; it can only transform a live-verified free
    -- agent.  Reject before any host write or player creation API is touched.
    if op.strategy ~= nil and op.strategy ~= "dummy_overwrite" then
        return fail("create_strategy_disabled",
            "generated Career slots are disabled; use a live-verified free-agent dummy", state)
    end

    -- v4 bags (and any envelope that already carries `batch`) share one
    -- resumable cursor: pick every dummy, write every card, TransferPlayer
    -- all of them, then wait once. Leftover v3 jobs stay on the ten-step path.
    if tonumber(op.v or 1) >= 4 or (type(op.batch) == "table" and #op.batch > 0) then
        return step_batch(op, job, ctx, state)
    end

    if step == 1 then
        local tid = tonumber(op.teamid)
        local live_teamid = user_team()
        -- A queued draft must never follow the player into a different Career
        -- save/club. The desktop-side fresh-squad guard is useful, but FC is
        -- the final authority immediately before any dummy is touched.
        if tid and live_teamid and tid ~= live_teamid then
            return fail("team_changed",
                string.format("queued team %s does not match active user team %s",
                    tostring(tid), tostring(live_teamid)), state)
        end
        if not tid or tid <= 0 then tid = user_team() end
        if not tid or tid <= 0 then return fail("no_teamid", "could not resolve target team", state) end
        state.teamid = math.floor(tid)
        local refused = require_squad_room(state, 1)
        if refused then return refused end
        return next_step(state, { teamid = state.teamid })
    end

    if step == 2 then
        local pool = type(op.dummy_pool) == "table" and op.dummy_pool or {}
        local filter = type(op.dummy_filter) == "table" and op.dummy_filter or {}
        local n = math.min(#pool, 6)
        if n < 1 then
            return fail("bad_dummy_pool", "Add Player needs at least one reviewed free-agent dummy", state)
        end
        local consumed_path, consumed, consume_reason = consumed_dummy_store(ctx)
        if not consumed_path then
            return fail(consume_reason or "consumed_slot_unavailable",
                "cannot prove this dummy has not already been used in this save", state)
        end
        local saw_unknown = false
        local last_reason, last_detail = "no_dummy", "no eligible dummy in the explicit pool"
        local on_club = (user_team() == state.teamid)
            and host_id_set("GetUserSeniorTeamPlayerIDs", nil) or nil
        for i = 1, n do
            local pid = tonumber(pool[i])
            if not pid or pid <= 0 or pid > 459999 then
                last_reason, last_detail = "bad_dummy", "dummy id is outside the real-player safety range"
            else
                pid = math.floor(pid)
                if dummy_is_consumed(consumed, pid) then
                    last_reason, last_detail = "dummy_already_used",
                        "this free-agent dummy was already consumed in the current save"
                else
                    local use_filter = filter
                    if i > 1 then
                        use_filter = util.copy(filter)
                        use_filter.expected_overallrating = nil
                    end
                    local clean, reason, detail = safe_free_agent(pid, use_filter, on_club)
                    if clean then
                        -- Do not burn the slot until names are written. A failed
                        -- name insert used to leave a half-mutated free agent
                        -- permanently consumed, so later Add Player attempts
                        -- looked like "nothing happened".
                        state.playerid, state.dummy_index = pid, i
                        state.consumed_path = consumed_path
                        return next_step(state, {
                            playerid = state.playerid, strategy = "dummy_overwrite",
                        }, { found = 1, targets = 1 })
                    end
                    if reason == "dummy_team_unknown" then
                        saw_unknown = true
                    elseif reason == "slot_probe_unavailable" or reason == "slot_probe_failed" then
                        return fail(reason or "no_dummy", detail or "", state)
                    end
                    last_reason, last_detail = reason or last_reason, detail or last_detail
                end
            end
        end
        if saw_unknown then
            state.pick_dummy_tries = (tonumber(state.pick_dummy_tries) or 0) + 1
            if state.pick_dummy_tries >= 12 then
                return fail(last_reason or "dummy_team_unknown", last_detail or "", state)
            end
            return wait_event(state, {
                waiting_for = "dummy_team",
                tries = state.pick_dummy_tries,
            })
        end
        return fail(last_reason, last_detail, state)
    end

    if not state.playerid then return fail("lost_progress", "playerid missing", state) end

    if step == 3 then
        local fields = util.copy(player.fields or {})
        fields.playerid = nil
        protect_potential(fields)
        local face = type(player.face) == "table" and player.face or {}
        state.original_values = capture_originals(state.playerid, { fields, face },
            type(state.original_values) == "table" and state.original_values or {})
        state.written_values = type(state.written_values) == "table" and state.written_values or {}
        local written, failures, applied = set_map(state.playerid, fields)
        record_applied(state.written_values, applied)
        if #failures > 0 then
            return fail(failures[1].reason or "field_write_failed",
                failures[1].detail or "", state)
        end
        state.requested_fields = fields
        return next_step(state, { applied = applied }, {
            fields_requested = util.count(fields), fields_written = written,
        })
    end

    if step == 4 then
        local face = type(player.face) == "table" and player.face or {}
        state.written_values = type(state.written_values) == "table" and state.written_values or {}
        local written, failures, applied = set_map(state.playerid, face)
        record_applied(state.written_values, applied)
        if #failures > 0 then
            return fail(failures[1].reason or "face_write_failed",
                failures[1].detail or "", state)
        end
        return next_step(state, {}, {
            fields_requested = util.count(face), fields_written = written,
        })
    end

    if step == 5 then
        -- Keep the dummy's current dictionary ids through TransferPlayer.
        -- Transfer is asynchronous; detaching here made face/stats appear in
        -- Squad Hub before the durable custom name was ready, leaving a blank
        -- name during the settle window.  Step 10 performs the only detach.
        return next_step(state, { names_deferred = true })
    end

    if step == 6 then
        -- This batch reserves distinct reviewed dummy ids before it queues,
        -- and the desktop prevents another Add Player batch while one runs.
        -- Delay durable name writes and consumption until post-transfer so a
        -- failed/slow transfer never produces a blank custom-name window.
        return next_step(state, { names_deferred = true })
    end

    if step == 7 then
        if not state.transfer_started then
            local called, reason, detail = call_transfer(state.playerid, state.teamid, op.contract)
            if not called then return fail(reason, detail, state) end
            state.transfer_started = true
        end
        local kind, reason, detail, room = finish_transfer(state.playerid, state.teamid)
        if kind == "fail" then
            if room then state.squad_room = room end
            return fail(reason, detail, state)
        end
        if kind == "wait" then
            state.verify_team_tries = (tonumber(state.verify_team_tries) or 0) + 1
            if state.verify_team_tries >= 12 then
                return fail("team_verify_unavailable",
                    "membership stayed unreadable after 12 waits", state)
            end
            return wait_event(state, {
                waiting_for = "team",
                expected_teamid = state.teamid,
                tries = state.verify_team_tries,
            })
        end
        state.verify_source = reason
        consume_now(ctx, state, state, state.playerid)
        return next_step(state, { transferred = true, transfer_via = reason }, { side_effects = 1 })
    end

    if step == 8 then
        local verified, source, actual, readable = verify_team_membership(
            state.playerid, state.teamid)
        if verified then
            state.verify_source = source
            return next_step(state, {
                team_verified = true,
                verify_source = source,
                actual_teamid = actual,
            })
        end
        local kind, reason, detail, room = finish_transfer(state.playerid, state.teamid)
        if kind == "ok" then
            state.verify_source = reason
            return next_step(state, {
                team_verified = true,
                verify_source = reason,
                actual_teamid = state.teamid,
            })
        end
        if kind == "fail" then
            if room then state.squad_room = room end
            return fail(reason, detail, state)
        end
        -- TransferPlayer is async. Wait for Career events before failing; a
        -- hard fail on a -1 host sentinel used to leave face+stats on a late
        -- squad join without reaching the final durable name pass.
        state.verify_team_tries = (tonumber(state.verify_team_tries) or 0) + 1
        if state.verify_team_tries >= 12 then
            local why = readable and "team_not_applied" or "team_verify_unavailable"
            return fail(why,
                string.format("expected %s, observed %s after %s waits (membership sources=%s)",
                    state.teamid, tostring(actual), state.verify_team_tries,
                    readable and "readable" or "unavailable"), state)
        end
        return wait_event(state, {
            waiting_for = "team",
            expected_teamid = state.teamid,
            actual_teamid = actual,
            membership_sources = readable and "readable" or "unavailable",
            tries = state.verify_team_tries,
        })
    end

    if step == 9 then
        local requested = util.copy(state.requested_fields or player.fields or {})
        -- Defense for jobs queued by older desktop builds: heal a missing or
        -- low potential when the post-transfer field pass runs.
        protect_potential(requested)
        state.requested_fields = requested
        local h, rec, reason = player_record(state.playerid)
        if not rec then return fail(reason or "player_not_found", "", state) end
        local differs = {}
        for field, expected in pairs(requested) do
            if field ~= "playerid" and h:get(field, rec) ~= expected then
                differs[field] = expected
            end
        end
        local written, failures = set_map(state.playerid, differs)
        if #failures > 0 then
            return fail(failures[1].reason or "reapply_failed",
                failures[1].detail or "", state)
        end
        -- Names are deliberately left intact through transfer/reapply.  The
        -- sole dictionary detach happens in step 10 after team verification.
        local face = type(player.face) == "table" and player.face or {}
        if not util.is_empty(face) then
            local fwritten, ffail = set_map(state.playerid, face)
            if #ffail > 0 then
                return fail(ffail[1].reason or "face_write_failed",
                    ffail[1].detail or "", state)
            end
            written = written + fwritten
        end
        return next_step(state, { reapplied = util.count(differs) }, {
            fields_requested = util.count(differs), fields_written = written,
        })
    end

    if step == 10 then
        -- v3 has two explicit paths.  A verified base player keeps all four
        -- native FC dictionary IDs, so no custom-name detach can blank the
        -- Career UI.  Cards without a complete native identity use the custom
        -- path, but may finish only after the host resolves that same name.
        local strategy = tostring(player.name_strategy or "custom")
        local inserted = 0
        local native_ids = nil
        local function fail_after_custom_detach(reason, detail)
            if strategy ~= "custom" or not state.name_dictionary_detached then
                return fail(reason, detail, state)
            end
            local restored, restored_written, restore_failures = restore_name_dict(
                state.playerid, state.prev_nameids)
            state.counts = state.counts or {}
            state.counts.fields_written = (state.counts.fields_written or 0) + restored_written
            state.name_dictionary_restore = {
                attempted = true,
                restored = restored,
                failures = restore_failures,
            }
            if restored then
                state.name_dictionary_detached = false
                return fail(reason, detail .. "; restored the original FC name identity", state)
            end
            local restore_reason = tostring((restore_failures[1] or {}).reason or "unknown")
            return fail(reason .. "_restore_failed",
                detail .. "; could not restore the original FC name identity (" .. restore_reason .. ")",
                state)
        end
        if strategy == "native_ids" then
            local native, count = {}, 0
            for _, field in ipairs({
                "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid",
            }) do
                local value = tonumber((player.fields or {})[field])
                if value == nil or value < 0 then
                    return fail("native_name_ids_missing",
                        "native name strategy requires four verified dictionary ids", state)
                end
                native[field] = math.floor(value)
                count = count + 1
            end
            if count ~= 4 or not (
                native.firstnameid > 0 or native.lastnameid > 0
                or native.commonnameid > 0 or native.playerjerseynameid > 0
            ) then
                return fail("native_name_ids_missing",
                    "native name strategy requires at least one real dictionary id", state)
            end
            native_ids = util.copy(native)
            native.usercaneditname = 0
            local nwritten, nfail = set_map(state.playerid, native)
            if #nfail > 0 or count ~= 4 then
                return fail((nfail[1] or {}).reason or "native_name_write_failed",
                    "could not activate verified native name dictionary ids", state)
            end
            state.counts = state.counts or {}
            state.counts.fields_written = (state.counts.fields_written or 0) + nwritten
        elseif strategy == "custom" then
            -- Materialise and prove the custom row *before* detaching native
            -- ids. Detaching first is the blank-name defect seen in FC 26.
            local wok, wreason, wrote = write_names(state.playerid, player.names or {})
            inserted = wrote
            if not wok then
                state.verify_name_tries = (tonumber(state.verify_name_tries) or 0) + 1
                if state.verify_name_tries < 6 then
                    return wait_event(state, { waiting_for = "name_write", reason = wreason,
                        tries = state.verify_name_tries })
                end
                return fail(wreason or "name_write_failed",
                    "could not write editedplayernames after transfer", state)
            end
            if player.detach_name_dictionary ~= false then
                if type(state.prev_nameids) ~= "table" or util.is_empty(state.prev_nameids) then
                    state.prev_nameids = snapshot_name_dict(state.playerid) or {}
                end
                if util.is_empty(state.prev_nameids) then
                    return fail("name_dictionary_snapshot_missing",
                        "custom name cannot safely detach the original FC identity", state)
                end
                local detach = {
                    firstnameid = 0, lastnameid = 0, commonnameid = 0,
                    playerjerseynameid = 0, usercaneditname = 1,
                }
                local dwritten, dfail = set_map(state.playerid, detach)
                if dwritten > 0 then state.name_dictionary_detached = true end
                if #dfail > 0 then
                    state.verify_name_tries = (tonumber(state.verify_name_tries) or 0) + 1
                    if state.verify_name_tries < 6 then
                        return wait_event(state, { waiting_for = "name_detach",
                            reason = dfail[1].reason, tries = state.verify_name_tries })
                    end
                    return fail_after_custom_detach(dfail[1].reason or "name_detach_failed",
                        "custom name was saved but could not be activated")
                end
                state.counts = state.counts or {}
                state.counts.fields_written = (state.counts.fields_written or 0) + dwritten
            end
        else
            return fail("bad_name_strategy", "expected native_ids or custom", state)
        end

        local expected = requested_name(op)
        if (op.verify or {}).name ~= false then
            local stored, why, detail = stored_name_ok(state.playerid, player, strategy, native_ids)
            if not stored then
                return fail_after_custom_detach(why, detail or "")
            end
        end
        record_name_proof(state, state.playerid, expected)
        local consumed_ok, consume_reason = consume_now(ctx, state, state, state.playerid)
        if not consumed_ok then
            return fail(consume_reason or "consumed_slot_write_failed",
                "name stored but the free-agent slot could not be reserved", state)
        end
        state.step = 11
        return {
            ok = true, deferred = false, counts = state.counts,
            data = {
                playerid = state.playerid, teamid = state.teamid,
                verified = true, steps = #STEP_NAMES,
                name_rows_inserted = inserted,
                host_player_name = state.host_player_name,
                name_strategy = strategy,
                visible_name_verified = true,
                name_verified_via = "database",
                le_name_cache_stale = state.le_cache_current == false,
            },
            progress = state, failures = {},
        }
    end

    return fail("bad_resume_step", tostring(step), state)
end

-- Shared, already-proven helpers for the narrow follow-up ops
-- (ops/names_sync.lua, ops/restore_partial_add.lua). Exported instead of
-- copied so the durable-name and membership rules stay in exactly one place.
M.helpers = {
    user_team = user_team,
    host_true = host_true,
    player_record = player_record,
    set_map = set_map,
    verify_team_membership = verify_team_membership,
    squad_room = squad_room,
    squad_room_detail = squad_room_detail,
    consumed_dummy_store = consumed_dummy_store,
    name_fields = name_fields,
    name_matches = name_matches,
    visible_name_matches = visible_name_matches,
    durable_name_ok = durable_name_ok,
    write_names = write_names,
    snapshot_name_dict = snapshot_name_dict,
    restore_name_dict = restore_name_dict,
    FREE_AGENT_TEAM_ID = FREE_AGENT_TEAM_ID,
}

return M
