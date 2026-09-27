-- FC26 injury records, matched to Live Editor v26.3.5's native implementation.
-- Evidence and version fingerprint: docs/INJURY_LAYOUT.md.
-- Never search memory or follow arbitrary pointer candidates.
local M = {}
local STRIDE, RECOVERY_STRIDE, MAX_RECORDS = 20, 12, 65536
local FITNESS_OFFSET, RECOVERY_OFFSET = 0x3F38, 0x3F58
local MANAGER_VTABLE_RVA = 0xB7A2460
local UNUSED, BASE_DATE = 4294967295, 20080101

local function uint(b, index, n)
    local value = 0
    for j = n - 1, 0, -1 do value = value * 256 + b[index + j] end
    return value
end

local function put(b, index, value, n)
    for j = 0, n - 1 do
        b[index + j] = value % 256
        value = math.floor(value / 256)
    end
end

local function pointer(value)
    return type(value) == "number" and value >= 0x10000
        and value < 0x800000000000 and value % 4 == 0
end

-- Live Editor exposes MEMORY:ReadBytes. The isolated tests install the globals.
local function memory_api()
    local memory = rawget(_G, "MEMORY")
    if type(memory) == "table" then return memory end
    local ok, loaded = pcall(require, "imports/core/memory")
    if ok and type(loaded) == "table" then return loaded end
    return nil
end

local function can_read()
    local memory = memory_api()
    if memory and type(memory.ReadBytes) == "function" then return true end
    return type(rawget(_G, "ReadBytes")) == "function"
end

local function can_write()
    local memory = memory_api()
    if memory and type(memory.WriteBytes) == "function" then return true end
    return type(rawget(_G, "WriteBytes")) == "function"
end

local function read(addr, n)
    if not pointer(addr) or n < 1 or n > MAX_RECORDS * STRIDE then
        error("Invalid fitness read boundary")
    end
    local bytes
    local memory = memory_api()
    if memory and type(memory.ReadBytes) == "function" then
        local ok, value = pcall(memory.ReadBytes, memory, addr, n)
        if ok then bytes = value end
    end
    if type(bytes) ~= "table" and type(rawget(_G, "ReadBytes")) == "function" then
        local ok, value = pcall(ReadBytes, addr, n)
        if ok then bytes = value end
    end
    if type(bytes) ~= "table" or #bytes ~= n then
        error("Could not read the current fitness records")
    end
    return bytes
end

local function same(a, b)
    if #a ~= #b then return false end
    for i = 1, #a do if a[i] ~= b[i] then return false end end
    return true
end

function M.supported()
    return tostring(rawget(_G, "LE_VERSION")) == "v26.3.5"
        and type(rawget(_G, "LE_GAME_MODULE_BASE")) == "number"
        and type(rawget(_G, "GetManagerObjByTypeId")) == "function"
        and can_read()
end

local function manager_address()
    if not M.supported() then error("Injury reading is not supported by this Live Editor build") end
    if type(rawget(_G, "IsInCM")) == "function" and not IsInCM() then
        error("Open your Career Mode save before scanning injuries")
    end
    local addr = GetManagerObjByTypeId(47)
    if not pointer(addr) then error("Career Mode fitness data is not ready") end
    if uint(read(addr, 8), 1, 8) ~= LE_GAME_MODULE_BASE + MANAGER_VTABLE_RVA then
        error("This FC26 update uses a different fitness layout; injury reading was stopped")
    end
    return addr
end

local function vector(manager, offset, stride)
    local header = read(manager + offset, 24)
    local first, last, capacity = uint(header, 1, 8), uint(header, 9, 8), uint(header, 17, 8)
    if first == 0 and last == 0 and capacity == 0 then
        return { first = 0, count = 0, header = header, address = manager + offset, stride = stride }
    end
    if not pointer(first) or last < first or capacity < last
        or capacity >= 0x800000000000 or (last - first) % stride ~= 0
        or (capacity - first) % stride ~= 0 or capacity - first > MAX_RECORDS * stride then
        error("Fitness records changed or have an unsupported layout; scan again")
    end
    return { first = first, count = (last - first) / stride,
        header = header, address = manager + offset, stride = stride }
end

local function valid_date(value)
    local year, month, day = math.floor(value / 10000), math.floor(value / 100) % 100, value % 100
    if year < 1900 or year > 9999 or month < 1 or month > 12 then return false end
    local days = {31,28,31,30,31,30,31,31,30,31,30,31}
    if year % 4 == 0 and (year % 100 ~= 0 or year % 400 == 0) then days[2] = 29 end
    return day >= 1 and day <= days[month]
end

local function is_injured(record)
    -- Native Player Editor uses signed byte +0xF > 1. Fitness is independent.
    return record.bytes[16] > 1 and record.bytes[16] < 128
end

local function records(vec, wanted)
    local out = {}
    for block = 0, vec.count - 1, 128 do
        local n = math.min(128, vec.count - block)
        local bytes = read(vec.first + block * vec.stride, n * vec.stride)
        for row = 0, n - 1 do
            local index = row * vec.stride + 1
            local pid = uint(bytes, index, 4)
            if pid ~= UNUSED and (pid < 1 or pid > 2147483647) then
                error("Invalid player ID in fitness data; no injuries were changed")
            end
            if vec.stride == STRIDE and bytes[index + 14] > 100 then
                error("Invalid fitness value; this layout cannot be used")
            end
            if wanted[pid] then
                if out[pid] then error("Duplicate fitness records for a squad player; scan again") end
                local copy = {}
                for i = 1, vec.stride do copy[i] = bytes[index + i - 1] end
                out[pid] = { address = vec.first + (block + row) * vec.stride, bytes = copy }
                if vec.stride == STRIDE and is_injured(out[pid])
                    and not valid_date(uint(copy, 9, 4)) then
                    error("Invalid injury recovery date; this layout cannot be used")
                end
            end
        end
    end
    if not same(read(vec.address, 24), vec.header) then error("Fitness data changed during the scan; try again") end
    return out
end

local function snapshot(pids, with_recovery)
    local manager = manager_address()
    local wanted = {}
    for _, pid in ipairs(pids) do wanted[pid] = true end
    local fit = vector(manager, FITNESS_OFFSET, STRIDE)
    local result = { manager = manager, fit = fit, players = records(fit, wanted) }
    if with_recovery then
        result.recovery = vector(manager, RECOVERY_OFFSET, RECOVERY_STRIDE)
        result.recovery_players = records(result.recovery, wanted)
    end
    return result
end

function M.scan(pids)
    local ok, result = pcall(function()
        local snap = snapshot(pids, false)
        local players, matched = {}, 0
        for _, pid in ipairs(pids) do
            local record = snap.players[pid]
            if record then
                matched = matched + 1
                if is_injured(record) then
                    players[#players + 1] = { playerid = pid, injury = "Injured",
                        injury_type = record.bytes[18], return_date = uint(record.bytes, 9, 4) }
                end
            end
        end
        -- The native editor defaults an absent record to fit / no injury.
        return { players = players, covered = #pids, squad_records = matched,
            records = snap.fit.count, scan_api = "FC26 fitness records (LE 26.3.5)" }
    end)
    if not ok then return nil, tostring(result) end
    return result
end

local function write_verified(addr, bytes)
    local memory = memory_api()
    local ok, value = false, nil
    if memory and type(memory.WriteBytes) == "function" then
        ok, value = pcall(memory.WriteBytes, memory, addr, bytes)
    end
    if (not ok or value == false) and type(rawget(_G, "WriteBytes")) == "function" then
        ok, value = pcall(WriteBytes, addr, bytes)
    end
    return ok and value ~= false and same(read(addr, #bytes), bytes)
end

function M.cure(pids)
    local ok, result = pcall(function()
        if not can_write() then error("Injury healing is unavailable") end
        local snap = snapshot(pids, true)
        local result = { cured_ids = {}, failed_ids = {}, failures = {}, already_clear = 0 }
        local seen = {}
        for _, pid in ipairs(pids) do
            if not seen[pid] then
                seen[pid] = true
                local fit = snap.players[pid]
                if not fit or not is_injured(fit) then
                    result.already_clear = result.already_clear + 1
                else
                    local touched = {}
                    local cleared, detail = pcall(function()
                        if manager_address() ~= snap.manager
                            or not same(read(snap.fit.address, 24), snap.fit.header)
                            or not same(read(snap.recovery.address, 24), snap.recovery.header) then
                            error("Career data changed before healing; scan again")
                        end
                        local edits = {}
                        -- Same reset as native Heal Player: clear existing recovery
                        -- record, then reset the fitness record to its default state.
                        local recovery = snap.recovery_players[pid]
                        if recovery then
                            local reset = {}
                            for i = 1, RECOVERY_STRIDE do reset[i] = recovery.bytes[i] end
                            put(reset, 1, UNUSED, 4); put(reset, 5, BASE_DATE, 4)
                            edits[#edits + 1] = { before = recovery, after = reset }
                        end
                        local reset = {}
                        for i = 1, STRIDE do reset[i] = 0 end
                        put(reset, 1, UNUSED, 4); put(reset, 5, UNUSED, 4)
                        put(reset, 9, BASE_DATE, 4); reset[15] = 100
                        edits[#edits + 1] = { before = fit, after = reset }
                        for _, edit in ipairs(edits) do
                            if not same(read(edit.before.address, #edit.before.bytes), edit.before.bytes) then
                                error("Player injury changed before healing; scan again")
                            end
                        end
                        for _, edit in ipairs(edits) do
                            touched[#touched + 1] = edit.before
                            if not write_verified(edit.before.address, edit.after) then error("Could not verify the injury reset") end
                        end
                    end)
                    if cleared then
                        result.cured_ids[#result.cured_ids + 1] = pid
                    else
                        local restored = true
                        for i = #touched, 1, -1 do
                            local old = touched[i]
                            local rollback_ok, verified = pcall(write_verified, old.address, old.bytes)
                            if not rollback_ok or not verified then restored = false end
                        end
                        result.failed_ids[#result.failed_ids + 1] = pid
                        result.failures[#result.failures + 1] = tostring(detail)
                            .. (restored and "" or " — original state could not be restored; reload your career save")
                        -- Stop after an uncertain operation; do not touch more players.
                        break
                    end
                end
            end
        end
        return result
    end)
    if not ok then return nil, tostring(result) end
    return result
end

return M
