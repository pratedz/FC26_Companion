--[[ ops/restore_partial_add.lua — put a partially-signed free agent back.

An add_to_team that wrote a card's stats/face onto a free-agent dummy but
never transferred him (FC refused: squad full) leaves a free agent with, e.g.,
Messi's ratings. add_to_team (with the squad-room precheck release) records
the dummy's value of every field BEFORE writing it (progress.members[i].original_values) and what
it actually wrote (written_values). The desktop turns that receipt into this
op after showing the user the exact field list.

Contract (op v1, single-op job, grant allow_add_to_team, requires.save_uid):

  { op = "restore_partial_add", v = 1, source_job_id = "<failed sign job>",
    members = { { playerid, expected = { field = written value, ... },
                  restore = { field = original value, ... } }, ... } }

All members are checked before the first write, and the whole job is refused
with zero writes unless every dummy
  * is still a free agent (not on the user's club, not loaned/presigned), and
  * still holds exactly the values the failed signing wrote.
Then exactly the listed fields are written back and verified by read-back,
and the dummy is released from the per-save consumed-dummy file if a signing
had marked it.
]]

local util = require("util")
local json = require("json")
local add = require("ops.add_to_team")
local H = add.helpers

local M = {}

local MAX_MEMBERS = 6

local function fail(reason, detail, counts, data, failures)
    return {
        ok = false, reason = reason, detail = detail or "",
        counts = counts or {}, data = data or {},
        failures = failures or { { reason = reason, detail = detail or "" } },
    }
end

local function positive(value)
    local n = tonumber(value)
    if not n or n <= 0 or n ~= math.floor(n) then return nil end
    return math.floor(n)
end

local function same_value(have, want)
    if type(want) == "number" then
        local n = tonumber(have)
        return n ~= nil and math.abs(n - want) < 1e-4
    end
    return tostring(have) == tostring(want)
end

local function value_ok(value)
    return type(value) == "number" or type(value) == "string"
end

local function validate_member(raw)
    if type(raw) ~= "table" then return nil, "member is not an object" end
    local pid = positive(raw.playerid)
    if not pid or pid > 459999 then
        return nil, "dummy id is outside the real-player safety range"
    end
    local expected = type(raw.expected) == "table" and raw.expected or nil
    local restore = type(raw.restore) == "table" and raw.restore or nil
    if not expected or not restore or util.is_empty(expected) then
        return nil, "expected and restore maps are required"
    end
    for field, value in pairs(expected) do
        if type(field) ~= "string" or field == "playerid" or not value_ok(value)
            or not value_ok(restore[field]) then
            return nil, "field " .. tostring(field) .. " has no matching restore value"
        end
    end
    for field in pairs(restore) do
        if expected[field] == nil then
            return nil, "restore field " .. tostring(field) .. " was not written by the signing"
        end
    end
    return { playerid = pid, expected = expected, restore = restore }
end

-- Zero-write precondition check for one dummy.
local function check_member(member)
    local pid = member.playerid
    local user = H.user_team()
    if user then
        local on_club = H.verify_team_membership(pid, user)
        if on_club then
            return "already_signed", "player " .. pid .. " is on your club; it is no longer a free agent"
        end
    end
    local team_fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(team_fn) ~= "function" then
        return "slot_probe_unavailable", "GetTeamIdFromPlayerId is unavailable"
    end
    local ok, value = pcall(team_fn, pid)
    local actual = ok and tonumber(value) or nil
    if actual == nil or actual < 0 then
        return "dummy_team_unknown", "free-agent membership is not readable yet"
    end
    if actual > 0 and actual ~= H.FREE_AGENT_TEAM_ID then
        return "dummy_not_free", string.format("player %d is now on team %d", pid, actual)
    end
    for _, fn_name in ipairs({ "IsPlayerPresigned", "IsPlayerLoanedOut" }) do
        local fn = rawget(_G, fn_name)
        if type(fn) ~= "function" then
            return "slot_probe_unavailable", fn_name .. " host check is unavailable"
        end
        local called, active = pcall(fn, pid)
        if not called then return "slot_probe_failed", fn_name .. " could not inspect the player" end
        if H.host_true(active) then
            return "dummy_not_clean", "player is presigned or loaned"
        end
    end
    local h, rec, reason = H.player_record(pid)
    if not rec then return reason or "player_not_found", "players row is not readable" end
    local fields = util.keys(member.expected)
    table.sort(fields)
    for _, field in ipairs(fields) do
        local have = h:get(field, rec)
        if not same_value(have, member.expected[field]) then
            return "dummy_changed", string.format(
                "%s is %s, the failed signing wrote %s; something changed this player since",
                field, tostring(have), tostring(member.expected[field]))
        end
    end
    return nil
end

local function release_consumed(ctx, pids)
    local path, doc = H.consumed_dummy_store(ctx)
    if not path or type(doc) ~= "table" or type(doc.playerids) ~= "table" then return 0 end
    local released = 0
    for _, pid in ipairs(pids) do
        if doc.playerids[tostring(pid)] ~= nil then
            doc.playerids[tostring(pid)] = nil
            released = released + 1
        end
    end
    if released > 0 then
        local text = json.try_encode(doc)
        if text then util.write_atomic(path, text) end
    end
    return released
end

function M.run(op, job, ctx)
    if not ((job or {}).grants or {}).allow_add_to_team then
        return fail("grant_denied", "restore_partial_add requires allow_add_to_team")
    end
    local required_save = tostring(((job or {}).requires or {}).save_uid or "")
    if required_save == "" or required_save ~= tostring((ctx and ctx.save_uid) or "") then
        return fail("save_mismatch", "restore requires the Career save the signing ran in")
    end
    local raw = type(op.members) == "table" and op.members or {}
    if #raw < 1 or #raw > MAX_MEMBERS then
        return fail("bad_restore_request", string.format(
            "restore needs 1-%d members, got %d", MAX_MEMBERS, #raw))
    end
    local members, seen = {}, {}
    for i = 1, #raw do
        local member, err = validate_member(raw[i])
        if not member then return fail("bad_restore_request", err) end
        if seen[member.playerid] then
            return fail("bad_restore_request", "the same dummy is listed twice")
        end
        seen[member.playerid] = true
        members[#members + 1] = member
    end

    local refusals = {}
    for _, member in ipairs(members) do
        local reason, detail = check_member(member)
        if reason then
            refusals[#refusals + 1] = { reason = reason, detail = detail or "", target = member.playerid }
        end
    end
    local requested = 0
    for _, member in ipairs(members) do requested = requested + util.count(member.restore) end
    if #refusals > 0 then
        return fail(refusals[1].reason, refusals[1].detail,
            { targets = #members, fields_requested = requested, fields_written = 0 },
            { refused = true, source_job_id = op.source_job_id }, refusals)
    end
    if job.dry_run or op.dry_run then
        return { ok = true, data = { dry_run = true, checked = #members }, failures = {},
                 counts = { targets = #members, found = #members, fields_requested = requested } }
    end

    local written_total, failures, rows, pids = 0, {}, {}, {}
    for _, member in ipairs(members) do
        local written, fails = H.set_map(member.playerid, member.restore)
        written_total = written_total + written
        local row = { playerid = member.playerid, fields_written = written,
                      fields_requested = util.count(member.restore), ok = #fails == 0 }
        for _, f in ipairs(fails) do
            failures[#failures + 1] = {
                reason = f.reason or "restore_write_failed", detail = f.detail or "",
                field = f.field, target = member.playerid,
            }
        end
        rows[#rows + 1] = row
        if #fails == 0 then pids[#pids + 1] = member.playerid end
    end
    local released = release_consumed(ctx, pids)
    local counts = {
        targets = #members, found = #members, fields_requested = requested,
        fields_written = written_total, fields_failed = #failures,
        side_effects = written_total > 0 and 1 or 0,
    }
    local data = { members = rows, source_job_id = op.source_job_id, released_consumed = released }
    if #failures > 0 then
        return fail(failures[1].reason, string.format("%d/%d fields restored",
            written_total, requested), counts, data, failures)
    end
    return { ok = true, data = data, counts = counts, failures = {} }
end

return M
