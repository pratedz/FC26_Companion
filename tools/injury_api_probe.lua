-- Safe FC26 Live Editor injury API probe. No memory reads or game writes.
local function emit(message)
    print("INJURY_PROBE " .. tostring(message))
end

local function relevant(name)
    name = tostring(name):lower()
    return name:find("injur", 1, true)
        or name:find("heal", 1, true)
        or name:find("fitness", 1, true)
        or name:find("recover", 1, true)
end

emit("LE_VERSION=" .. tostring(rawget(_G, "LE_VERSION")))
local ok_team, team = pcall(function() return GetUserTeamID() end)
emit("USER_TEAM=" .. tostring(ok_team and team or "unavailable"))

local globals = {}
for key, value in pairs(_G) do
    if type(key) == "string" and relevant(key) then
        globals[#globals + 1] = key .. ":" .. type(value)
    end
end
table.sort(globals)
for _, item in ipairs(globals) do emit("GLOBAL " .. item) end

local le = rawget(_G, "LE")
if type(le) == "table" then
    local keys = {}
    for key, value in pairs(le) do
        if type(key) == "string" then
            keys[#keys + 1] = key .. ":" .. type(value)
            if type(value) == "table" then
                for child, child_value in pairs(value) do
                    if type(child) == "string" and relevant(child) then
                        emit("LE." .. key .. "." .. child .. ":" .. type(child_value))
                    end
                end
            end
        end
    end
    table.sort(keys)
    for _, item in ipairs(keys) do emit("LE " .. item) end
else
    emit("LE=" .. type(le))
end

local list_tables = rawget(_G, "GetDBTablesNames")
local list_fields = rawget(_G, "GetDBTableFields")
if type(list_tables) == "function" then
    local ok, tables = pcall(list_tables)
    if ok and type(tables) == "table" then
        for _, name in pairs(tables) do
            if type(name) == "string" and relevant(name) then
                emit("TABLE " .. name)
            end
        end
    end
end
if type(list_fields) == "function" then
    for _, name in ipairs({"players", "career_playercontract"}) do
        local ok, fields = pcall(list_fields, name)
        if ok and type(fields) == "table" then
            for _, field in pairs(fields) do
                local label = type(field) == "table" and field.name or field
                if type(label) == "string" and relevant(label) then
                    emit("FIELD " .. name .. "." .. label)
                end
            end
        end
    end
end
emit("DONE")
