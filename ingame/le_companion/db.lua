--[[ le_companion/db.lua — schema-aware table access + verified writes.

Host calls are always pcall'd. Without a loaded save / LE APIs this module
returns honest "no_table" / "no_api" errors rather than inventing success.
]]

local util = require("util")

local db = {}
db.STRING_TYPE = 0
db.INT_TYPE = 3
db.FLOAT_TYPE = 4
db._handles = {}
db._schema_fields = {}
db._meta = nil
db._warnings = {}

local function host(name, ...)
    local fn = rawget(_G, name)
    if type(fn) ~= "function" then return false, "no_api:" .. tostring(name) end
    return pcall(fn, ...)
end

local function warn(msg)
    for i = 1, #db._warnings do
        if db._warnings[i] == msg then return end
    end
    db._warnings[#db._warnings + 1] = msg
end

function db.warnings()
    return util.copy(db._warnings)
end

function db.reset()
    db._handles = {}
    db._schema_fields = {}
    db._meta = nil
end

function db.meta()
    if db._meta ~= nil then return db._meta end
    local ok, m = host("GetDBMeta")
    if ok and type(m) == "table" then
        db._meta = m
        return m
    end
    return nil
end

function db.available()
    return db.meta() ~= nil
end

function db.table_fields(tname)
    if db._schema_fields[tname] then return db._schema_fields[tname] end
    local m = db.meta()
    if not m then return nil end
    local map = m.shortname_name_tables_map
    if type(map) ~= "table" then return nil end
    local short
    for s, name in pairs(map) do
        if name == tname then short = s; break end
    end
    if not short then return nil end
    local descs = m.field_desc_map
    if type(descs) ~= "table" or type(descs[short]) ~= "table" then return nil end
    local out = {}
    for _, d in pairs(descs[short]) do
        if type(d) == "table" and type(d.name) == "string" then
            local depth = tonumber(d.depth) or 0
            local minv = tonumber(d.min) or 0
            out[d.name] = {
                name = d.name,
                depth = depth,
                min = minv,
                max = minv + (1 << depth) - 1,
                max_string_len = depth // 8,
            }
        end
    end
    db._schema_fields[tname] = out
    return out
end

function db.field(tname, fname)
    local fields = db.table_fields(tname)
    if not fields then return nil end
    return fields[fname]
end

function db.validate_value(tname, fname, v)
    local f = db.field(tname, fname)
    if f == nil then
        if db.meta() == nil then return true, nil, "schema_unavailable" end
        return false, "no_such_field",
            string.format("field %s not in table %s", tostring(fname), tostring(tname))
    end
    if type(v) == "string" then
        return true
    end
    if type(v) ~= "number" then
        return false, "type_mismatch",
            string.format("field %s expects number, got %s", fname, type(v))
    end
    if v ~= math.floor(v) then
        return false, "not_integer", tostring(v)
    end
    if v < f.min or v > f.max then
        return false, "out_of_range",
            string.format("value %d outside %d..%d", v, f.min, f.max)
    end
    return true
end

local Handle = {}
Handle.__index = Handle

function Handle:refresh()
    if self.t and type(self.t.written_records) == "number" then
        self.written_records = self.t.written_records
        self.first_record = self.t.first_record
        self.record_size = self.t.record_size
        return true
    end
    return false
end

function Handle:count()
    return tonumber(self.written_records) or 0
end

function Handle:get(fname, rec)
    if self.t == nil or type(self.t.GetRecordFieldValue) ~= "function" then
        return nil, "no_table"
    end
    local ok, v = pcall(self.t.GetRecordFieldValue, self.t, rec, fname)
    if not ok then return nil, "read_threw" end
    return v
end

function Handle:set(fname, rec, value)
    if self.t == nil or type(self.t.SetRecordFieldValue) ~= "function" then
        return false, nil, "no_write_api"
    end
    local ok_v, code, detail = db.validate_value(self.name, fname, value)
    if not ok_v then return false, nil, code, detail end
    local written = value
    if type(value) == "string" then
        local f = db.field(self.name, fname)
        local maxlen = f and f.max_string_len or 0
        if type(maxlen) == "number" and maxlen > 0 and #written > maxlen then
            written = written:sub(1, maxlen)
        end
    end
    local wok = pcall(self.t.SetRecordFieldValue, self.t, rec, fname, written)
    if not wok then return false, nil, "write_threw" end
    local rok, back = pcall(self.t.GetRecordFieldValue, self.t, rec, fname)
    if not rok then return false, nil, "readback_threw" end
    if back ~= written then
        if type(written) == "number" and type(back) == "number"
            and math.abs(back - written) < 1e-4 then
            return true, back
        end
        return false, back, "verify_mismatch",
            string.format("wrote %s read %s", tostring(written), tostring(back))
    end
    return true, back
end

function Handle:records()
    self:refresh()
    -- Live Editor tables expose a stateful valid-record iterator. It is the
    -- same API used by the proven v1 exporter and avoids constructing record
    -- addresses for deleted/unused slots. Calling GetRecord(index) on those
    -- slots can hand GetRecordFieldValue an invalid native pointer.
    if type(self.t.GetFirstRecord) == "function"
        and type(self.t.GetNextValidRecord) == "function" then
        local first = true
        local finished = false
        return function()
            if finished then return nil end
            local ok, rec
            if first then
                first = false
                ok, rec = pcall(self.t.GetFirstRecord, self.t)
            else
                ok, rec = pcall(self.t.GetNextValidRecord, self.t)
            end
            if not ok or type(rec) ~= "number" or rec <= 0 then
                finished = true
                return nil
            end
            return rec
        end
    end

    -- Offline test hosts may only implement index lookup. Never synthesize
    -- native record addresses from first_record/record_size.
    local idx = -1
    local last = self:count() - 1
    return function()
        while idx < last do
            idx = idx + 1
            if type(self.t.GetRecord) == "function" then
                local ok, rec = pcall(self.t.GetRecord, self.t, idx)
                if ok and rec ~= nil then return rec, idx end
            else return nil end
        end
        return nil
    end
end

local function load_le_table(tname)
    local le = rawget(_G, "LE")
    if type(le) == "table" and type(le.db) == "table" and type(le.db.GetTable) == "function" then
        local ok, t = pcall(le.db.GetTable, le.db, tname)
        if ok and type(t) == "table" then return t end
    end
    local ok2, t2 = host("GetDBTable", tname)
    if ok2 and type(t2) == "table" then return t2 end
    return nil
end

function db.open(tname)
    if type(tname) ~= "string" or tname == "" then return nil, "bad_table_name" end
    local h = db._handles[tname]
    if h then
        h:refresh()
        return h
    end
    local t = load_le_table(tname)
    if t == nil then return nil, "no_such_table" end
    h = setmetatable({
        name = tname,
        t = t,
        first_record = t.first_record,
        record_size = t.record_size,
        written_records = t.written_records or 0,
    }, Handle)
    db._handles[tname] = h
    return h
end

function db.scan_for(h, key_field, key_value)
    if h == nil then return nil end
    for rec in h:records() do
        local v = h:get(key_field, rec)
        if v == key_value or tonumber(v) == tonumber(key_value) then
            return rec
        end
    end
    return nil
end

--- Return the first matching record and the total number of matches.
--- The duplicate count is important for small upsert tables such as
--- editedplayernames: silently updating the first of several rows would make
--- the result look successful while the game may continue reading a stale row.
function db.scan_matches(h, key_field, key_value)
    if h == nil then return nil, 0 end
    local first, count = nil, 0
    for rec in h:records() do
        local v = h:get(key_field, rec)
        if v == key_value or tonumber(v) == tonumber(key_value) then
            count = count + 1
            if first == nil then first = rec end
        end
    end
    return first, count
end

function db.resolve(tname, key_field, key_value)
    local h, err = db.open(tname)
    if h == nil then return nil, nil, err or "no_such_table" end
    local rec, matches = db.scan_matches(h, key_field, key_value)
    return h, rec, rec and "scan" or "absent", matches
end

function db.set_many(h, rec, map, opts)
    opts = opts or {}
    local written, failures, applied = 0, {}, {}
    local names = util.keys(map)
    for i = 1, #names do
        local fname = names[i]
        local value = map[fname]
        local ok, actual, reason, detail = h:set(fname, rec, value)
        if ok then
            written = written + 1
            applied[fname] = actual
        else
            failures[#failures + 1] = {
                field = fname,
                requested = value,
                readback = actual,
                reason = reason or "unknown",
                detail = detail,
            }
            if opts.on_field_error == "abort" then break end
        end
    end
    return written, failures, 0, applied
end

function db.insert_row(tname, row_data)
    if type(rawget(_G, "InsertDBTableRow")) ~= "function" then
        return nil, "no_insert_api"
    end
    local ok, row = host("InsertDBTableRow", tname, row_data)
    if not ok or type(row) ~= "table" then return nil, "insert_threw" end
    local addr = row.addr
    if type(addr) == "table" then addr = addr.value end
    addr = tostring(addr or "")
    if addr == "" or addr == "0" or addr == "nil" then
        return nil, "insert_rejected"
    end
    return row
end

function db.rows_small(tname, max_rows)
    if type(rawget(_G, "GetDBTableRows")) ~= "function" then
        return nil, "no_rows_api"
    end
    if tname == "players" then return nil, "refused_players_table" end
    local ceiling = max_rows or 4000
    local h = db.open(tname)
    if h and h:count() > ceiling then
        return nil, string.format("table_too_large:%d>%d", h:count(), ceiling)
    end
    local ok, rows = host("GetDBTableRows", tname)
    if not ok or type(rows) ~= "table" then return nil, "rows_threw" end
    return rows
end

function db.row_field_value(row, fname)
    if type(row) ~= "table" then return nil end
    local f = row[fname]
    if type(f) == "table" then return f.value end
    return f
end

return db
