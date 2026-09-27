-- FC26 LE 26.3.5: bounded, read-only FitnessManager scan.
-- Reads only the already resolved manager object's own first 0x3B80 bytes.
-- It never follows an address from those bytes and never writes game memory.
local function emit(message)
    print("INJURY_BOUNDED " .. tostring(message))
end

if tostring(rawget(_G, "LE_VERSION")) ~= "v26.3.5" then
    emit("STOP version mismatch")
    return
end

local ok_manager, manager = pcall(GetManagerObjByTypeId, 47)
if not ok_manager or type(manager) ~= "number" or manager < 0x10000 then
    emit("STOP FitnessManager unavailable")
    return
end

local ok_squad, squad = pcall(GetUserSeniorTeamPlayerIDs)
if not ok_squad or type(squad) ~= "table" then
    emit("STOP squad unavailable")
    return
end
local wanted = {}
local squad_count = 0
for key, value in pairs(squad) do
    local pid = tonumber(value)
    if not pid or pid <= 0 then pid = tonumber(key) end
    if pid and pid > 0 then
        wanted[math.floor(pid)] = true
        squad_count = squad_count + 1
    end
end
emit("manager=" .. manager .. " squad=" .. squad_count)

local hits = 0
local failures = 0
for offset = 0, 0x3B00, 0x100 do
    local ok, bytes = pcall(MEMORY.ReadBytes, MEMORY, manager + offset, 0x100)
    if not ok or type(bytes) ~= "table" or #bytes < 0x100 then
        failures = failures + 1
        emit(string.format("unreadable_chunk=%X", offset))
    else
        for index = 1, 0xFD, 4 do
            local pid = bytes[index] + bytes[index + 1] * 256
                + bytes[index + 2] * 65536 + bytes[index + 3] * 16777216
            if wanted[pid] then
                hits = hits + 1
                emit(string.format("squad_pid=%d manager_offset=%X", pid,
                    offset + index - 1))
            end
        end
    end
end
emit("DONE hits=" .. hits .. " unreadable_chunks=" .. failures)
