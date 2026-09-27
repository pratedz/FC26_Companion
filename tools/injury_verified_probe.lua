-- Read-only probe of the layout confirmed in FCLiveEditor.DLL v26.3.5.
-- Native accessor RVA 0x2C6CA0: vector +0x3F38/+0x3F40, stride 0x14.
-- Native player editor RVA 0x390D26: signed status byte +0x0F > 1 means injured.
-- No game writes, pointer search, or guessed object traversal.
local function emit(s) Log("[INJURY_VERIFIED] " .. tostring(s)) end
if tostring(rawget(_G, "LE_VERSION")) ~= "v26.3.5" then
    emit("STOP unsupported Live Editor version") return
end
local function read(addr, n)
    local ok, b = pcall(MEMORY.ReadBytes, MEMORY, addr, n)
    if not ok or type(b) ~= "table" or #b ~= n then error("Read failed") end
    return b
end
local function uint(b, first, n)
    local result = 0
    for j = n - 1, 0, -1 do result = result * 256 + b[first + j] end
    return result
end
local function pointer(n)
    return type(n) == "number" and n >= 0x10000 and n < 0x800000000000 and n % 4 == 0
end
local ok, err = pcall(function()
    local manager = GetManagerObjByTypeId(47)
    if not pointer(manager) then error("Career fitness manager unavailable") end
    local header = read(manager + 0x3F38, 24)
    local first, last, capacity = uint(header, 1, 8), uint(header, 9, 8), uint(header, 17, 8)
    emit(string.format("manager=%X first=%X last=%X capacity=%X", manager, first, last, capacity))
    if not pointer(first) or last < first or capacity < last
        or (last - first) % 20 ~= 0 or (capacity - first) % 20 ~= 0
        or (capacity - first) > 65536 * 20 then error("Invalid vector header") end
    local count = (last - first) / 20
    if count > 8192 then error("Probe record limit exceeded: " .. count) end
    local wanted, squad_count = {}, 0
    for key, value in pairs(GetUserSeniorTeamPlayerIDs()) do
        local pid = tonumber(value) or tonumber(key)
        if pid and not wanted[pid] then wanted[pid] = true; squad_count = squad_count + 1 end
    end
    local found, covered, bad = 0, 0, 0
    for block = 0, count - 1, 128 do
        local size = math.min(128, count - block)
        local bytes = read(first + block * 20, size * 20)
        for index = 0, size - 1 do
            local start = index * 20 + 1
            local pid = uint(bytes, start, 4)
            local fitness, status, kind = bytes[start + 14], bytes[start + 15], bytes[start + 17]
            if fitness > 100 then bad = bad + 1 end
            if wanted[pid] then
                covered = covered + 1
                local injured = status > 1 and status < 128
                if injured then found = found + 1 end
                emit(string.format("pid=%d name=%s fitness=%d status=%d type=%d date=%d injured=%s",
                    pid, tostring(GetPlayerName(pid)), fitness, status, kind,
                    uint(bytes, start + 8, 4), tostring(injured)))
            end
        end
    end
    emit(string.format("DONE records=%d squad=%d matched=%d injured=%d invalid_fitness=%d", count, squad_count, covered, found, bad))
end)
if not ok then emit("STOP " .. tostring(err)) end
