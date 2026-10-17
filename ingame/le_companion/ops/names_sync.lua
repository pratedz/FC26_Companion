--[[ ops/names_sync.lua — "Check & fix in-game names" for verified signings.

Player names in FC 26 come from the game database, not from Live Editor's
SetGameLocString/AddCustomGameID (verified by reverse engineering): either the
four name-dictionary ids on the players row (native identity) or, when those
are detached (ids 0, usercaneditname 1), the player's editedplayernames row.
add_to_team writes exactly that in its name step; this op re-applies the same
rules to players that were signed earlier and now show a wrong name.

Contract (op v1, single-op job, grant allow_add_to_team, requires.save_uid):

  { op = "names.sync", v = 1, teamid = <user club>,
    players = { { playerid, names = {firstname, surname, commonname,
                  playerjerseyname}, name_strategy = "custom" | "native_ids",
                  native_name_ids = {firstnameid, lastnameid, commonnameid,
                  playerjerseynameid} (native only),
                  detach_name_dictionary = true|false (custom only) }, ... } }

Per player, step 1 ("write"):
  * refuse (no write) unless the player is provably on the user's club;
  * "already" (no write) when the stored identity is in place;
  * otherwise write: custom -> durable editedplayernames row first, then the
    dictionary detach (snapshotting the previous ids); native -> the four ids;
    then read the stored identity back.
Step 2 ("verify_visible") reads the stored identity back once more. Since
2.6.42 it no longer waits on GetPlayerName: that answers from Live Editor's
player cache, which LE rebuilds only on a day advance or a save load, so it
kept old names for the whole hub visit and every fix ended name_not_visible.
The cached name is still reported (le_cache_name) for diagnostics; a custom
detach whose stored identity does not read back is undone so no blank label
is left behind.

Nothing here transfers, edits stats/face, or touches another table.
]]

local util = require("util")
local add = require("ops.add_to_team")
local H = add.helpers

local M = {}

local MAX_PLAYERS = 60
local DICT_FIELDS = { "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid" }

local function positive(value)
    local n = tonumber(value)
    if not n or n <= 0 or n ~= math.floor(n) then return nil end
    return math.floor(n)
end

local function trim(value)
    return (tostring(value or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function clean_names(raw)
    if type(raw) ~= "table" then return nil end
    local given = {}
    for _, field in ipairs({ "firstname", "surname", "commonname", "playerjerseyname" }) do
        local value = trim(raw[field])
        if value ~= "" then given[field] = value end
    end
    if next(given) == nil then return nil end
    return H.name_fields(given)
end

local function native_ids_from(raw)
    if type(raw) ~= "table" then return nil, "native_name_ids must be a table" end
    local out, any = {}, false
    for _, field in ipairs(DICT_FIELDS) do
        local value = tonumber(raw[field])
        if value == nil or value < 0 or value ~= math.floor(value) then
            return nil, "native " .. field .. " must be a nonnegative integer"
        end
        out[field] = math.floor(value)
        if value > 0 then any = true end
    end
    if not any then return nil, "native_name_ids needs at least one non-zero FC name ID" end
    return out
end

local function dictionary_state(pid)
    local h, rec = H.player_record(pid)
    if not rec then return nil end
    local out = {}
    for _, field in ipairs({
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid", "usercaneditname",
    }) do
        out[field] = tonumber(h:get(field, rec))
    end
    return out
end

local function is_detached(dict)
    if type(dict) ~= "table" then return false end
    for _, field in ipairs(DICT_FIELDS) do
        if dict[field] ~= 0 then return false end
    end
    return dict.usercaneditname == 1
end

local function native_applied(dict, ids)
    if type(dict) ~= "table" or type(ids) ~= "table" then return false end
    for _, field in ipairs(DICT_FIELDS) do
        if dict[field] ~= ids[field] then return false end
    end
    return dict.usercaneditname == 0
end

local function host_name(pid)
    local fn = rawget(_G, "GetPlayerName")
    if type(fn) ~= "function" then return nil, false end
    local ok, value = pcall(fn, pid)
    if not ok then return nil, false end
    return tostring(value or ""), true
end

local function visible_ok(entry, name)
    if name == nil then return false end
    if entry.expected ~= "" and H.visible_name_matches(name, entry.expected) then
        return true
    end
    -- Native FC identities may render the jersey name / surname only.
    if entry.strategy == "native_ids" then
        for _, field in ipairs({ "playerjerseyname", "surname" }) do
            local alternative = tostring((entry.names or {})[field] or "")
            if alternative ~= "" and H.visible_name_matches(name, alternative) then
                return true
            end
        end
    end
    return false
end

--- The identity FC renders, read from the database (2.6.42).
local function stored_ok(entry, dict)
    dict = dict or dictionary_state(entry.playerid)
    if not dict then return false end
    if entry.strategy == "native_ids" then
        return native_applied(dict, entry.native_ids)
    end
    return H.durable_name_ok(entry.playerid, entry.names)
        and (entry.detach == false or is_detached(dict))
end

local function note_cache(entry)
    local name, available = host_name(entry.playerid)
    entry.le_cache_name = available and name or nil
    entry.le_cache_current = available and visible_ok(entry, name) or false
end

local function fail(reason, detail, state)
    return {
        ok = false, reason = reason, detail = detail or "",
        counts = (state and state.counts) or {},
        data = { progress = state },
        failures = { { reason = reason, detail = detail or "" } },
    }
end

local function add_count(state, key, n)
    state.counts = state.counts or {}
    state.counts[key] = (state.counts[key] or 0) + (n or 0)
end

-- One player's check + write. Mutates and returns ``entry``.
local function sync_one(entry, teamid, state)
    local pid = entry.playerid
    local member, source, actual = H.verify_team_membership(pid, teamid)
    if not member then
        entry.status, entry.reason = "refused", "not_on_team"
        entry.detail = string.format("player %d is not on club %d (observed %s)",
            pid, teamid, tostring(actual))
        return entry
    end
    entry.membership = source
    add_count(state, "found", 1)

    local dict = dictionary_state(pid)
    if not dict then
        entry.status, entry.reason = "failed", "player_not_found"
        entry.detail = "players row is not readable"
        return entry
    end
    local before = host_name(pid)
    entry.before_name = before
    note_cache(entry)
    if stored_ok(entry, dict) then
        -- The stored identity is already the signed one. LE's cached name
        -- (before_name) may lag until the next day advance; it is reported,
        -- never rewritten into the save.
        entry.status, entry.visible_name = "already", entry.expected
        return entry
    end

    if entry.strategy == "native_ids" then
        local write = util.copy(entry.native_ids)
        write.usercaneditname = 0
        local written, failures = H.set_map(pid, write)
        add_count(state, "fields_written", written)
        if written > 0 then add_count(state, "side_effects", 1) end
        local after = dictionary_state(pid)
        if #failures > 0 or not native_applied(after, entry.native_ids) then
            entry.status, entry.reason = "failed", (failures[1] or {}).reason or "native_name_write_failed"
            entry.detail = "native name ids did not read back"
            return entry
        end
        entry.status = "written"
        return entry
    end

    -- custom: durable editedplayernames row first, detach second (the order
    -- that prevents the blank Squad Hub label).
    local wok, wreason = H.write_names(pid, entry.names)
    add_count(state, "side_effects", 1)
    if not wok then
        entry.status, entry.reason = "failed", wreason or "name_write_failed"
        entry.detail = "editedplayernames row did not read back"
        return entry
    end
    add_count(state, "fields_written", 4)
    if entry.detach ~= false and not is_detached(dict) then
        entry.prev_nameids = H.snapshot_name_dict(pid) or {}
        if util.is_empty(entry.prev_nameids) then
            entry.status, entry.reason = "failed", "name_dictionary_snapshot_missing"
            entry.detail = "name row saved, but the FC identity could not be snapshotted, so it was not detached"
            return entry
        end
        local detach = {
            firstnameid = 0, lastnameid = 0, commonnameid = 0,
            playerjerseynameid = 0, usercaneditname = 1,
        }
        local dwritten, dfail = H.set_map(pid, detach)
        add_count(state, "fields_written", dwritten)
        if dwritten > 0 then entry.detached_now = true end
        if #dfail > 0 then
            if entry.detached_now then
                local restored = H.restore_name_dict(pid, entry.prev_nameids)
                entry.restored = restored and true or false
            end
            entry.status, entry.reason = "failed", dfail[1].reason or "name_detach_failed"
            entry.detail = "custom name was saved but could not be activated"
            return entry
        end
    end
    if not H.durable_name_ok(pid, entry.names) then
        entry.status, entry.reason = "failed", "name_not_applied"
        entry.detail = "editedplayernames row is missing or stale after the write"
        return entry
    end
    entry.status = "written"
    return entry
end

local function report(state)
    local players, totals = {}, { already = 0, fixed = 0, failed = 0, refused = 0 }
    for _, entry in ipairs(state.players or {}) do
        local status = entry.status
        if status == "written" then status = "failed" end
        totals[status] = (totals[status] or 0) + 1
        players[#players + 1] = {
            playerid = entry.playerid,
            status = status,
            reason = entry.reason,
            detail = entry.detail,
            expected = entry.expected,
            before_name = entry.before_name,
            visible_name = entry.visible_name,
            restored = entry.restored,
            le_cache_name = entry.le_cache_name,
            le_cache_current = entry.le_cache_current,
        }
    end
    return players, totals
end

local function finish(state)
    local players, totals = report(state)
    state.step = 3
    local counts = util.copy(state.counts or {})
    counts.targets = #players
    local data = {
        teamid = state.teamid, players = players,
        already = totals.already, fixed = totals.fixed,
        failed = totals.failed, refused = totals.refused,
    }
    if totals.failed == 0 and totals.refused == 0 then
        return { ok = true, deferred = false, counts = counts, data = data,
                 progress = state, failures = {} }
    end
    local failures = {}
    for _, row in ipairs(players) do
        if row.status == "failed" or row.status == "refused" then
            failures[#failures + 1] = {
                reason = row.reason or row.status, detail = row.detail or "",
                target = row.playerid,
            }
        end
    end
    return {
        ok = false, reason = failures[1].reason, detail = string.format(
            "%d fixed, %d already correct, %d failed, %d refused",
            totals.fixed, totals.already, totals.failed, totals.refused),
        counts = counts, data = data, progress = state, failures = failures,
    }
end

-- Step 2: the stored identity is the terminal gate, as in add_to_team.
local function verify_visible(state)
    local pending = 0
    for _, entry in ipairs(state.players or {}) do
        if entry.status == "written" then
            note_cache(entry)
            if stored_ok(entry) then
                entry.status, entry.visible_name = "fixed", entry.expected
            else
                pending = pending + 1
            end
        end
    end
    if pending == 0 then return finish(state) end
    for _, entry in ipairs(state.players or {}) do
        if entry.status == "written" then
            entry.status, entry.reason = "failed", "name_not_stored"
            entry.detail = "the name was written but the stored identity did not read back"
            if entry.detached_now then
                local restored, restored_written = H.restore_name_dict(entry.playerid, entry.prev_nameids)
                add_count(state, "fields_written", restored_written or 0)
                entry.restored = restored and true or false
                entry.detail = entry.detail .. (restored
                    and "; restored the original FC name identity"
                    or "; could not restore the original FC name identity")
            end
        end
    end
    return finish(state)
end

function M.step(op, job, ctx)
    local state = util.copy((ctx and ctx.resume_state) or {})
    state.step = tonumber(state.step) or 1
    state.total_steps = 2
    state.counts = state.counts or {}

    if not ((job or {}).grants or {}).allow_add_to_team then
        return fail("grant_denied", "names.sync requires allow_add_to_team", state)
    end
    local required_save = tostring(((job or {}).requires or {}).save_uid or "")
    if required_save == "" or required_save ~= tostring((ctx and ctx.save_uid) or "") then
        return fail("save_mismatch", "names.sync requires the Career save it was reviewed in", state)
    end
    local teamid = positive(op.teamid)
    local live = H.user_team()
    if not teamid or not live or teamid ~= live then
        return fail("team_changed", string.format(
            "reviewed club %s does not match active user club %s",
            tostring(op.teamid), tostring(live)), state)
    end
    state.teamid = teamid

    if state.step >= 2 then return verify_visible(state) end

    local list = type(op.players) == "table" and op.players or {}
    if #list < 1 or #list > MAX_PLAYERS then
        return fail("bad_names_request", string.format(
            "names.sync needs 1-%d players, got %d", MAX_PLAYERS, #list), state)
    end
    local entries, seen = {}, {}
    for i = 1, #list do
        local raw = list[i]
        local entry = { index = i, status = "pending" }
        if type(raw) ~= "table" then
            entry.status, entry.reason = "refused", "bad_request"
        else
            entry.playerid = positive(raw.playerid)
            entry.strategy = tostring(raw.name_strategy or "custom")
            entry.names = clean_names(raw.names)
            entry.detach = raw.detach_name_dictionary ~= false
            if entry.names then
                entry.expected = tostring(entry.names.commonname or "")
            end
            if not entry.playerid or entry.playerid > 499999 then
                entry.status, entry.reason, entry.detail = "refused", "bad_request", "playerid is invalid"
            elseif seen[entry.playerid] then
                entry.status, entry.reason, entry.detail = "refused", "bad_request", "player listed twice"
            elseif not entry.names or entry.expected == "" then
                entry.status, entry.reason, entry.detail = "refused", "bad_request", "names are missing"
            elseif entry.strategy == "native_ids" then
                local ids, err = native_ids_from(raw.native_name_ids)
                if not ids then
                    entry.status, entry.reason, entry.detail = "refused", "bad_request", err
                else
                    entry.native_ids = ids
                end
            elseif entry.strategy ~= "custom" then
                entry.status, entry.reason, entry.detail = "refused", "bad_request",
                    "name_strategy must be custom or native_ids"
            end
            if entry.playerid then seen[entry.playerid] = true end
        end
        entries[#entries + 1] = entry
    end
    state.players = entries

    if job.dry_run or op.dry_run then
        for _, entry in ipairs(entries) do
            if entry.status == "pending" then
                local member = H.verify_team_membership(entry.playerid, teamid)
                entry.before_name = host_name(entry.playerid)
                note_cache(entry)
                if not member then
                    entry.status, entry.reason = "refused", "not_on_team"
                elseif stored_ok(entry) then
                    entry.status, entry.visible_name = "already", entry.expected
                else
                    entry.status, entry.reason = "failed", "dry_run_mismatch"
                end
            end
        end
        return finish(state)
    end

    for _, entry in ipairs(entries) do
        if entry.status == "pending" then sync_one(entry, teamid, state) end
    end
    state.step = 2
    return verify_visible(state)
end

return M
