--[[ One-purpose recovery for a partial Phase 4 Add Player job.

This is deliberately narrower than set_fields: it cannot transfer, alter a
face, modify age/stats, or choose a player by name.  It only completes the
custom-name row and corrects potential after proving the exact player is still
in the expected team of the same Career save and still has the expected old
OVR/POT pair.
]]

local db = require("db")

local M = {}

local function fail(reason, detail, counts, data)
    return {
        ok = false, reason = reason, detail = detail or "",
        counts = counts or {}, data = data or {},
        failures = { { reason = reason, detail = detail or "" } },
    }
end

local function ok(data, counts)
    return { ok = true, data = data or {}, counts = counts or {}, failures = {} }
end

local function positive(value)
    local n = tonumber(value)
    if not n or n <= 0 then return nil end
    return math.floor(n)
end

local function user_team()
    for _, name in ipairs({ "GetUserTeamID", "GetUserTeamId" }) do
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            local called, value = pcall(fn)
            local tid = called and positive(value) or nil
            if tid then return tid end
        end
    end
    return nil
end

local function list_has_player(fn_name, arg, playerid)
    local fn = rawget(_G, fn_name)
    if type(fn) ~= "function" then return false end
    local called, values
    if arg == nil then called, values = pcall(fn) else called, values = pcall(fn, arg) end
    if not called or type(values) ~= "table" then return false end
    for key, value in pairs(values) do
        local pid = positive(value) or positive(key)
        if pid == playerid then return true end
    end
    return false
end

-- GetTeamIdFromPlayerId is sometimes briefly -1 after an async transfer even
-- when the roster helper/table already lists the player.  Treat a positive,
-- different team as a hard mismatch, but accept verified roster membership.
local function verify_membership(teamid, playerid)
    local fn = rawget(_G, "GetTeamIdFromPlayerId")
    if type(fn) == "function" then
        local called, value = pcall(fn, playerid)
        local actual = called and tonumber(value) or nil
        if actual and math.floor(actual) == teamid then
            return true, "GetTeamIdFromPlayerId", actual
        end
        -- 111592 is FC's free-agent/unknown sentinel, not evidence that a
        -- completed async transfer landed at a different club.
        if actual and actual > 0 and actual ~= 111592 then
            return false, "different_team", actual
        end
    end

    if user_team() == teamid and list_has_player("GetUserSeniorTeamPlayerIDs", nil, playerid) then
        return true, "GetUserSeniorTeamPlayerIDs", nil
    end
    for _, name in ipairs({ "GetPlayerIDSForTeam", "GetPlayerIDsForTeam" }) do
        if list_has_player(name, teamid, playerid) then
            return true, name, nil
        end
    end

    local links = db.open("teamplayerlinks")
    if links and db.field("teamplayerlinks", "teamid") and db.field("teamplayerlinks", "playerid") then
        for rec in links:records() do
            if positive(links:get("teamid", rec)) == teamid
                and positive(links:get("playerid", rec)) == playerid then
                return true, "teamplayerlinks", nil
            end
        end
    end
    return false, "unverified", nil
end

local function names_from(op)
    local raw = type(op.names) == "table" and op.names or {}
    local names = {}
    for _, field in ipairs({ "firstname", "surname", "commonname", "playerjerseyname" }) do
        local value = tostring(raw[field] or "")
        value = value:gsub("^%s+", ""):gsub("%s+$", "")
        if value ~= "" then names[field] = value end
    end
    if not names.commonname then
        names.commonname = (tostring(names.firstname or "") .. " " .. tostring(names.surname or ""))
            :gsub("^%s+", ""):gsub("%s+$", ""):gsub("%s+", " ")
    end
    if names.commonname == "" then names.commonname = nil end
    if not names.playerjerseyname or names.playerjerseyname == "" then
        names.playerjerseyname = names.surname or names.commonname
    end
    return names
end

-- A native identity is copied only from a verified FC base-player row.  Zero
-- is a valid value for an optional component (notably playerjerseynameid), so
-- require the complete four-field bundle and at least one real dictionary ID.
-- Keep usercaneditname explicit: a native profile must stay attached to FC's
-- dictionary instead of being silently converted into a custom-name player.
local function native_name_ids_from(op)
    if op.native_name_ids == nil then return nil, nil end
    if type(op.native_name_ids) ~= "table" then
        return nil, "native_name_ids must be a table"
    end
    local values, any_positive = {}, false
    for _, field in ipairs({
        "firstnameid", "lastnameid", "commonnameid", "playerjerseynameid",
    }) do
        local raw = op.native_name_ids[field]
        local value = tonumber(raw)
        if value == nil or value < 0 or math.floor(value) ~= value then
            return nil, "native " .. field .. " must be a nonnegative integer"
        end
        value = math.floor(value)
        values[field] = value
        if value > 0 then any_positive = true end
    end
    if not any_positive then
        return nil, "native_name_ids needs at least one non-zero FC name ID"
    end
    local editable = tonumber(op.native_name_ids.usercaneditname)
    if editable == nil or (editable ~= 0 and editable ~= 1) then
        return nil, "native usercaneditname must be 0 or 1"
    end
    values.usercaneditname = math.floor(editable)
    return values, nil
end

local function row_playerid(row)
    local value = db.row_field_value(row, "playerid")
    return value == nil and nil or tostring(value)
end

local function row_text(row, field)
    local value = db.row_field_value(row, field)
    return value == nil and "" or tostring(value)
end

local function same_name(actual, expected)
    local function normalized(value)
        -- Keep non-ASCII letters intact; stripping them caused false failures
        -- for names such as ModriÄ‡ and JÃ©rÃ©my.
        return tostring(value or ""):lower():gsub("[%s%p]+", "")
    end
    return normalized(actual) == normalized(expected)
end

local function visible_name_matches(actual, expected)
    if same_name(actual, expected) then return true end
    -- A complete component sequence accepts Iniesta / Andrés Iniesta while
    -- rejecting embedded substrings such as Ben / Benedict. A recovery must
    -- never claim a name is fixed merely because part of a dummy name matches.
    local function tokens(value)
        local out = {}
        for token in tostring(value or ""):lower():gmatch("[^%s%p]+") do
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

local function matching_rows(rows, playerid)
    local matches, needle = {}, tostring(math.floor(playerid))
    for _, row in pairs(rows or {}) do
        if row_playerid(row) == needle then matches[#matches + 1] = row end
    end
    return matches
end

local function rows_are_durable(rows, fields)
    if #rows == 0 or tostring(fields.commonname or "") == "" then return false end
    for i = 1, #rows do
        for _, field in ipairs({ "firstname", "surname", "playerjerseyname", "commonname" }) do
            local expected = tostring(fields[field] or "")
            if expected ~= "" and not same_name(row_text(rows[i], field), expected) then
                return false
            end
        end
        if not same_name(row_text(rows[i], "commonname"), fields.commonname) then return false end
    end
    return true
end

local function edit_host_row(row, fields)
    local edit = rawget(_G, "EditDBTableField")
    if type(edit) ~= "function" then return false end
    local wrote = 0
    for field, value in pairs(fields) do
        local target = row[field]
        if type(target) == "table" then
            target.value = value
            if pcall(edit, target) then wrote = wrote + 1 end
        end
    end
    return wrote > 0
end

local function durable_name_ok(playerid, fields)
    local pid = tostring(math.floor(playerid))
    if type(rawget(_G, "GetDBTableRows")) == "function" then
        local called, rows = pcall(_G.GetDBTableRows, "editedplayernames")
        if called and type(rows) == "table" then
            return rows_are_durable(matching_rows(rows, playerid), fields)
        end
    end
    local h = db.open("editedplayernames")
    if not h then return false end
    local rows = {}
    for rec in h:records() do
        if tostring(h:get("playerid", rec) or "") == pid
            or tonumber(h:get("playerid", rec)) == tonumber(pid) then
            rows[#rows + 1] = {
                playerid = tostring(h:get("playerid", rec) or ""),
                firstname = tostring(h:get("firstname", rec) or ""),
                surname = tostring(h:get("surname", rec) or ""),
                playerjerseyname = tostring(h:get("playerjerseyname", rec) or ""),
                commonname = tostring(h:get("commonname", rec) or ""),
            }
        end
    end
    return rows_are_durable(rows, fields)
end

local function write_names(playerid, fields)
    -- Use the hardened Live Editor materialised-row path first.  An insert
    -- address alone is not proof: Career can retain a shell/stale row and
    -- still display a blank Squad Hub name.
    if type(rawget(_G, "GetDBTableRows")) == "function" then
        local called, rows = pcall(_G.GetDBTableRows, "editedplayernames")
        if called and type(rows) == "table" then
            local matches = matching_rows(rows, playerid)
            if #matches == 0 and type(rawget(_G, "InsertDBTableRow")) == "function" then
                local inserted_ok = pcall(_G.InsertDBTableRow, "editedplayernames", {
                    playerid = tostring(math.floor(playerid)),
                    firstname = fields.firstname or "",
                    surname = fields.surname or "",
                    playerjerseyname = fields.playerjerseyname or "",
                    commonname = fields.commonname or "",
                })
                if not inserted_ok then
                    return false, "name_insert_failed", "InsertDBTableRow threw", 0, {}
                end
                local reread, after = pcall(_G.GetDBTableRows, "editedplayernames")
                matches = reread and type(after) == "table" and matching_rows(after, playerid) or {}
                if #matches == 0 then
                    return false, "name_insert_unverified", "insert did not materialise a name row", 0, {}
                end
            end
            if #matches > 0 then
                local wrote = 0
                for i = 1, #matches do
                    if edit_host_row(matches[i], fields) then wrote = wrote + 1 end
                end
                if wrote == 0 or not durable_name_ok(playerid, fields) then
                    return false, "name_not_applied", "durable name row is missing or stale", wrote, {}
                end
                return true, "durable", "", wrote, {}
            end
        end
    end

    -- Cursor fallback for LE builds without materialised editedplayernames.
    local h, rec, state = db.resolve("editedplayernames", "playerid", playerid)
    if not h then return false, "name_table_unavailable", tostring(state), 0, {} end
    if not rec then
        local row, ierr = db.insert_row("editedplayernames", {
            playerid = tostring(math.floor(playerid)),
            firstname = fields.firstname or "", surname = fields.surname or "",
            playerjerseyname = fields.playerjerseyname or "", commonname = fields.commonname or "",
        })
        if not row then return false, "name_insert_failed", tostring(ierr), 0, {} end
        db.reset()
        h, rec, state = db.resolve("editedplayernames", "playerid", playerid)
        if not h or not rec then return false, "name_insert_unverified", tostring(state), 0, {} end
    end
    local requested, written = 0, 0
    for _ in pairs(fields) do requested = requested + 1 end
    for current in h:records() do
        local row_pid = h:get("playerid", current)
        if tostring(row_pid or "") == tostring(math.floor(playerid))
            or tonumber(row_pid) == tonumber(playerid) then
            local count, failures = db.set_many(h, current, fields, { on_field_error = "abort" })
            if #failures > 0 or count ~= requested then
                return false, "name_write_failed", "custom name was not fully written", written + count, failures
            end
            written = written + count
        end
    end
    if written == 0 or not durable_name_ok(playerid, fields) then
        return false, "name_not_applied", "durable name row is missing or stale", written, {}
    end
    return true, "durable", "", written, {}
end

-- A durable editedplayernames row is only displayed by Career when the
-- player's dictionary ids are detached.  Old partial Add Player jobs already
-- did this before transfer; doing it here as an idempotent, post-name step
-- also repairs any legacy job that stopped one step earlier.
local function detach_name_dictionary(players, rec)
    local fields = {}
    local defaults = {
        firstnameid = 0,
        lastnameid = 0,
        commonnameid = 0,
        playerjerseynameid = 0,
        usercaneditname = 1,
    }
    for field, value in pairs(defaults) do
        if db.field("players", field) then fields[field] = value end
    end
    local requested = 0
    for _ in pairs(fields) do requested = requested + 1 end
    if requested == 0 then
        return false, 0, { { reason = "name_dictionary_unavailable" } }
    end
    local written, failures = db.set_many(players, rec, fields, {
        on_field_error = "abort",
    })
    return #failures == 0 and written == requested, written, failures
end

function M.run(op, job, ctx)
    if not (job.grants and job.grants.allow_add_to_team) then
        return fail("grant_denied", "repair_partial_add requires allow_add_to_team")
    end
    local required_save = tostring((job.requires or {}).save_uid or "")
    if required_save == "" or required_save ~= tostring((ctx and ctx.save_uid) or "") then
        return fail("save_mismatch", "repair requires the original Career save_uid")
    end

    local playerid, teamid = positive(op.playerid), positive(op.teamid)
    local expected = type(op.expected) == "table" and op.expected or {}
    local expected_ovr = tonumber(expected.overallrating)
    local expected_pot = tonumber(expected.potential)
    local target_pot = tonumber(op.potential)
    local names = names_from(op)
    local native_name_ids, native_error = native_name_ids_from(op)
    if not playerid or not teamid or not expected_ovr or expected_pot == nil
        or not target_pot or target_pot < expected_ovr or next(names) == nil
        or native_error then
        if native_error then
            return fail("bad_repair_request", native_error)
        end
        return fail("bad_repair_request", "missing strict player/team/OVR/POT/name preconditions")
    end

    local member, source, actual_team = verify_membership(teamid, playerid)
    if not member then
        if source == "different_team" then
            return fail("repair_team_mismatch",
                "player is now in team " .. tostring(actual_team) .. ", not expected team " .. tostring(teamid))
        end
        return fail("repair_team_unverified",
            "could not prove player " .. tostring(playerid) .. " belongs to expected team " .. tostring(teamid))
    end

    local players, rec, reason = db.resolve("players", "playerid", playerid)
    if not players or not rec then return fail("repair_player_missing", tostring(reason)) end
    local actual_ovr = tonumber(players:get("overallrating", rec))
    local actual_pot = tonumber(players:get("potential", rec))
    if actual_ovr ~= expected_ovr or actual_pot ~= expected_pot then
        return fail("repair_player_changed", string.format(
            "expected OVR/POT %s/%s, found %s/%s", tostring(expected_ovr),
            tostring(expected_pot), tostring(actual_ovr), tostring(actual_pot)))
    end

    local requested_names = 0
    for _ in pairs(native_name_ids or names) do requested_names = requested_names + 1 end
    if job.dry_run or op.dry_run then
        return ok({ dry_run = true, playerid = playerid, teamid = teamid, membership = source }, {
            targets = 1, fields_requested = requested_names + 1,
        })
    end

    local name_state, names_written, detached_written = "native", 0, 0
    if native_name_ids then
        -- Native repair intentionally writes only authoritative player name
        -- identity fields.  It never creates editedplayernames rows and never
        -- detaches FC's dictionary, so face/stats/age/transfer stay untouched.
        local native_written, native_failures = db.set_many(players, rec, native_name_ids, {
            on_field_error = "abort",
        })
        if #native_failures > 0 or native_written ~= requested_names then
            return fail("native_name_write_failed", "FC native name identity was not fully written", {
                targets = 1, fields_requested = requested_names + 1,
                fields_written = native_written,
                fields_failed = requested_names - native_written,
            }, { native_name_failures = native_failures })
        end
        names_written = native_written
    else
        local names_ok, custom_state, name_detail, custom_written, name_failures = write_names(playerid, names)
        if not names_ok then
            return fail(custom_state, name_detail, {
                targets = 1, fields_requested = requested_names + 1,
                fields_written = custom_written, fields_failed = requested_names - custom_written,
            }, { name_failures = name_failures })
        end
        name_state, names_written = custom_state, custom_written
    end

    -- Resolve again after a possible editedplayernames insert resets cached DB
    -- handles. The OVR/POT pair is re-read immediately before the only player
    -- writes so a changed target can never be repaired by a stale record addr.
    players, rec, reason = db.resolve("players", "playerid", playerid)
    if not players or not rec then
        return fail("repair_player_missing_after_names", tostring(reason), {
            targets = 1, fields_requested = requested_names + 1,
            fields_written = names_written,
        })
    end
    actual_ovr = tonumber(players:get("overallrating", rec))
    actual_pot = tonumber(players:get("potential", rec))
    if actual_ovr ~= expected_ovr or actual_pot ~= expected_pot then
        return fail("repair_player_changed_after_names", "player OVR/POT changed before potential repair", {
            targets = 1, fields_requested = requested_names + 1,
            fields_written = names_written,
        })
    end
    if not native_name_ids then
        local detached, custom_detached_written, detach_failures = detach_name_dictionary(players, rec)
        detached_written = custom_detached_written
        if not detached then
            return fail("repair_name_dictionary_failed", "custom name was saved but could not be activated", {
                targets = 1, fields_requested = requested_names + 1,
                fields_written = names_written + detached_written,
                fields_failed = 1,
            }, { name_dictionary_failures = detach_failures })
        end
    end
    -- A durable row is only a prerequisite.  The old recovery path could
    -- report success while FC still showed a blank/dummy name in Squad Hub.
    -- Do not change potential or mark this repair complete unless FC's own
    -- name resolver agrees with the requested visible identity.
    local get_name = rawget(_G, "GetPlayerName")
    if type(get_name) ~= "function" then
        return fail("name_visibility_unavailable",
            "GetPlayerName is required to verify the FC-visible repaired name", {
                targets = 1, fields_requested = requested_names + 1,
                fields_written = names_written + detached_written,
                fields_failed = 1,
            })
    end
    local name_ok, visible = pcall(get_name, playerid)
    local name_visible = name_ok and visible_name_matches(visible, names.commonname)
    -- Native FC profiles may report a surname/jersey name while the catalog
    -- uses the full common name. Only this native repair path may use those
    -- verified source components as display alternatives.
    if not name_visible and native_name_ids then
        for _, field in ipairs({ "playerjerseyname", "surname" }) do
            local alternative = tostring(names[field] or "")
            if alternative ~= "" and name_ok and visible_name_matches(visible, alternative) then
                name_visible = true
                break
            end
        end
    end
    if not name_visible then
        return fail("name_not_visible",
            string.format("expected FC-visible name %s, observed %s", tostring(names.commonname), tostring(visible)), {
                targets = 1, fields_requested = requested_names + 1,
                fields_written = names_written + detached_written,
                fields_failed = 1,
            })
    end
    local written, failures = db.set_many(players, rec, { potential = target_pot }, {
        on_field_error = "abort",
    })
    if #failures > 0 or written ~= 1 then
        return fail("repair_potential_failed", "potential was not verified", {
            targets = 1, fields_requested = requested_names + 1,
            fields_written = names_written + detached_written + written,
            fields_failed = 1,
        }, { potential_failures = failures })
    end
    return ok({
        playerid = playerid, teamid = teamid, membership = source,
        name = name_state, visible_name = tostring(visible), potential = target_pot,
    }, {
        targets = 1, found = 1, fields_requested = requested_names + 1,
        fields_written = names_written + detached_written + 1,
        fields_failed = 0, side_effects = 1,
    })
end

return M
