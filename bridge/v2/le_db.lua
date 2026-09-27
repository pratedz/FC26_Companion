--[[ LE Companion v2 — schema-driven DB access, player index, verified writes.

  Three facts drive every line of this file.

  1. pcall CANNOT detect a failed field write. FIELD:SetInt ends in an
     unconditional `return true` and truncates an out-of-range value bitwise;
     FIELD:SetString has no `return` at all, so a SUCCESSFUL string write
     reports nil. Read-back is the only honest success signal, so db.set writes
     and then reads and compares.

  2. Full record walks freeze FC 26, and the v1 answer (a MAX_SCAN of 25000)
     silently failed to find a real player. The answer here is an index built
     once per session, bounded by written_records (+0x7C) — never
     total_records (+0x78), and never by a magic constant.

  3. A T3DB TABLE snapshots first_record / record_size / written_records at
     GetTable() time, so a handle held across an insert is stale and its record
     addresses may point at moved memory. Handles here keep the table struct
     address and can re-read the header; every lookup proves the address still
     holds the player it claims to.

  Never call GetDBTableRows on `players`: ~21,000 rows x ~120 field tables is
  ~2.5M Lua tables and 4-5 seconds of frozen game thread, per call.
]]

local LEC = rawget(_G, "LEC")
if type(LEC) ~= "table" then
    error("le_db requires le_core to be loaded first", 0)
end

local util = LEC.util
local log = LEC.log

local db = {}

db.STRING_TYPE = 0
db.INT_TYPE = 3
db.FLOAT_TYPE = 4

db._warnings = {}

local function warn(msg)
    for i = 1, #db._warnings do
        if db._warnings[i] == msg then return end
    end
    db._warnings[#db._warnings + 1] = msg
    log.write("db warning: " .. msg)
end

function db.warnings()
    return util.copy_shallow(db._warnings)
end

-- ─────────────────────────────────────────────────────────────────────────
-- memory primitives
-- ─────────────────────────────────────────────────────────────────────────

local mem = {}
db.mem = mem

local function memory_obj()
    local m = rawget(_G, "MEMORY")
    if type(m) == "table" then return m end
    local ok, mod = pcall(require, "imports/core/memory")
    if ok and type(mod) == "table" then
        _G.MEMORY = mod
        return mod
    end
    return nil
end

-- Prefer MEMORY:Method, fall back to the bare global of the same name. The
-- mock harness installs the globals; LE installs the object.
local function mcall(method, ...)
    local m = memory_obj()
    if m and type(m[method]) == "function" then
        local ok, v = pcall(m[method], m, ...)
        if ok then return v end
        return nil
    end
    local g = rawget(_G, method)
    if type(g) == "function" then
        local ok, v = pcall(g, ...)
        if ok then return v end
    end
    return nil
end

function mem.available()
    if memory_obj() then return true end
    return type(rawget(_G, "ReadQword")) == "function"
end

function mem.read_qword(addr) return mcall("ReadQword", addr) end
function mem.read_int(addr) return mcall("ReadInt", addr) end
function mem.read_short(addr) return mcall("ReadShort", addr) end
function mem.read_char(addr) return mcall("ReadChar", addr) end
function mem.read_bytes(addr, n) return mcall("ReadBytes", addr, n) end
function mem.read_string(addr) return mcall("ReadString", addr) end
function mem.read_pointer(addr) return mcall("ReadPointer", addr) end
function mem.write_qword(addr, v) return mcall("WriteQword", addr, v) end
function mem.write_bytes(addr, b) return mcall("WriteBytes", addr, b) end

function mem.mlptr(base, offsets)
    local m = memory_obj()
    if m and type(m.ReadMultilevelPointer) == "function" then
        local ok, v = pcall(m.ReadMultilevelPointer, m, base, offsets)
        if ok and type(v) == "number" then return v end
        return 0
    end
    local cur = base
    for i = 1, #offsets do
        if type(cur) ~= "number" or cur <= 0 then return 0 end
        cur = mem.read_pointer(cur + offsets[i])
    end
    if type(cur) ~= "number" then return 0 end
    return cur
end

-- ─────────────────────────────────────────────────────────────────────────
-- schema — GetDBMeta() is the whole DB schema; read it once, cache it.
-- ─────────────────────────────────────────────────────────────────────────

local schema = {}
db.schema = schema

schema._meta = nil
schema._tables = nil
schema._fields = nil

function schema.reset()
    schema._meta = nil
    schema._tables = nil
    schema._fields = nil
end

function schema.meta()
    if schema._meta ~= nil then return schema._meta end
    local ok, m = util.host("GetDBMeta")
    if not ok or type(m) ~= "table" then
        return nil
    end
    schema._meta = m
    return m
end

-- name -> shortname, and shortname -> name.
function schema.tables()
    if schema._tables ~= nil then return schema._tables end
    local m = schema.meta()
    if m == nil then return nil end
    local map = m.shortname_name_tables_map
    if type(map) ~= "table" then return nil end
    local by_name, by_short = {}, {}
    for short, name in pairs(map) do
        if type(name) == "string" and name ~= "" then
            by_name[name] = short
            by_short[short] = name
        end
    end
    schema._tables = { by_name = by_name, by_short = by_short }
    return schema._tables
end

function schema.has_table(tname)
    local t = schema.tables()
    if t == nil then return nil end
    return t.by_name[tname] ~= nil
end

-- field name -> {name, shortname, depth, min, max}
-- Bit depth and min come from GetDBMeta; `kind` needs the live column
-- descriptor and is filled in by db.open() when a handle exists.
function schema.table_fields(tname)
    schema._fields = schema._fields or {}
    if schema._fields[tname] ~= nil then return schema._fields[tname] end
    local tabs = schema.tables()
    local m = schema.meta()
    if tabs == nil or m == nil then return nil end
    local short = tabs.by_name[tname]
    if short == nil then return nil end
    local descs = m.field_desc_map
    if type(descs) ~= "table" then return nil end
    local fdesc = descs[short]
    if type(fdesc) ~= "table" then return nil end
    local out = {}
    for fshort, d in pairs(fdesc) do
        if type(d) == "table" and type(d.name) == "string" then
            local depth = tonumber(d.depth) or 0
            local minv = tonumber(d.min) or 0
            out[d.name] = {
                name = d.name,
                shortname = fshort,
                depth = depth,
                min = minv,
                max = minv + (1 << depth) - 1,
                max_string_len = depth // 8,
            }
        end
    end
    schema._fields[tname] = out
    return out
end

function schema.field(tname, fname)
    local fields = schema.table_fields(tname)
    if fields == nil then return nil end
    return fields[fname]
end

function schema.has(tname, fname)
    return schema.field(tname, fname) ~= nil
end

function schema.range(tname, fname)
    local f = schema.field(tname, fname)
    if f == nil then return nil end
    return f.min, f.max
end

function schema.max_string_len(tname, fname)
    local f = schema.field(tname, fname)
    if f == nil then return nil end
    return f.max_string_len
end

-- Levenshtein-free "did you mean": one pass over the field names looking for a
-- near match. Cheap, and it turns a typo into an actionable error.
function schema.suggest(tname, fname)
    local fields = schema.table_fields(tname)
    if fields == nil or type(fname) ~= "string" then return nil end
    local target = fname:lower()
    local best, best_score = nil, 0
    for name in pairs(fields) do
        local other = name:lower()
        local n = math.min(#target, #other)
        local score = 0
        for i = 1, n do
            if target:sub(i, i) == other:sub(i, i) then score = score + 1 else break end
        end
        if math.abs(#other - #target) <= 2 and score > best_score and score >= 3 then
            best, best_score = name, score
        end
    end
    return best
end

-- The single choke point that makes a silently truncated write impossible.
-- Returns ok, reason, detail.
function schema.validate_value(tname, fname, v, kind_hint)
    local f = schema.field(tname, fname)
    if f == nil then
        if schema.meta() == nil then
            -- No schema available (not in a loaded save). Defer to write time;
            -- db.set still verifies by read-back.
            return true, nil, "schema_unavailable"
        end
        local hint = schema.suggest(tname, fname)
        local detail = string.format("field %s not in table %s", tostring(fname), tostring(tname))
        if hint then detail = detail .. string.format(" (did you mean %s?)", hint) end
        return false, "no_such_field", detail
    end
    local kind = kind_hint or db.kind_of(tname, fname)
    if kind == "string" then
        if type(v) ~= "string" then
            return false, "type_mismatch", string.format("field %s expects string", fname)
        end
        return true
    end
    if type(v) ~= "number" then
        return false, "type_mismatch", string.format("field %s expects number, got %s", fname, type(v))
    end
    if kind == "float" then
        return true
    end
    -- Default and int: integral and inside the bit depth.
    if v ~= math.floor(v) then
        return false, "not_integer", tostring(v)
    end
    if v < f.min or v > f.max then
        return false, "out_of_range", string.format(
            "value %d outside %d..%d (depth %d)", v, f.min, f.max, f.depth)
    end
    return true
end

-- ─────────────────────────────────────────────────────────────────────────
-- table handles
-- ─────────────────────────────────────────────────────────────────────────

local Handle = {}
Handle.__index = Handle

db._handles = {}

local function t3db_table_module()
    local ok, mod = pcall(require, "imports/t3db/table")
    if ok and type(mod) == "table" then return mod end
    return nil
end

local function db_service_addr()
    local clss = rawget(_G, "ENUM_djb2Database_CLSS")
    if type(clss) ~= "number" then return 0 end
    local ok, v = util.host("GetPlugin", clss)
    if not ok or type(v) ~= "number" then return 0 end
    return v - 8
end

local function read_shortname(addr, offset)
    local bytes = mem.read_bytes(addr + offset, 4)
    if type(bytes) ~= "table" then return nil end
    local out = {}
    for i = 1, 4 do
        local b = bytes[i]
        if type(b) ~= "number" then return nil end
        out[i] = string.char(b)
    end
    return table.concat(out)
end

-- Replicates DB:GetTable's linked-list walk, but keeps the struct address so
-- the header can be re-read later. DB:GetTable throws that address away.
local function find_table_addr(tname)
    if not mem.available() then return nil end
    local tabs = schema.tables()
    if tabs == nil then return nil end
    local svc = db_service_addr()
    if svc == 0 then return nil end
    local cur_db = mem.mlptr(svc, { 0x20, 0x08, 0x10 })
    local guard_db = 0
    while type(cur_db) == "number" and cur_db > 0 and guard_db < 64 do
        guard_db = guard_db + 1
        local tbl = mem.read_pointer(cur_db + 0x10)
        local guard_t = 0
        while type(tbl) == "number" and tbl > 0 and guard_t < 4096 do
            guard_t = guard_t + 1
            local short = read_shortname(tbl, 0x40)
            if short and tabs.by_short[short] == tname then
                return tbl
            end
            tbl = mem.read_pointer(tbl + 0x08)
        end
        cur_db = mem.read_pointer(cur_db + 0x18)
    end
    return nil
end

local function le_db_object()
    local le = rawget(_G, "LE")
    if type(le) == "table" and type(le.db) == "table" then return le.db end
    return nil
end

local function load_le_table(tname)
    local ledb = le_db_object()
    if ledb == nil or type(ledb.GetTable) ~= "function" then return nil end
    local ok, t = pcall(ledb.GetTable, ledb, tname)
    if ok and type(t) == "table" then return t end
    return nil
end

function Handle:refresh()
    if self.addr and mem.available() then
        local first = mem.read_qword(self.addr + 0x30)
        local size = mem.read_int(self.addr + 0x44)
        -- +0x7C is written_records (16-bit). +0x78 is total_records and
        -- includes deleted rows; iterating to it walks freed memory.
        local written = mem.read_short(self.addr + 0x7C)
        if type(first) == "number" and type(size) == "number" and type(written) == "number" then
            self.first_record = first
            self.record_size = size
            self.written_records = written
            if self.t then
                self.t.first_record = first
                self.t.record_size = size
                self.t.written_records = written
                self.t.last_record_idx = written - 1
            end
            if written >= 65535 then
                warn(string.format(
                    "table %s written_records is a 16-bit read at +0x7C and has saturated at %d",
                    tostring(self.name), written))
            end
            return true
        end
    end
    -- No struct address: re-open through LE's DB, which rebuilds the snapshot.
    local t = load_le_table(self.name)
    if t ~= nil then
        self.t = t
        self.first_record = t.first_record
        self.record_size = t.record_size
        self.written_records = t.written_records
        return true
    end
    return false
end

function Handle:count()
    return tonumber(self.written_records) or 0
end

function Handle:generation()
    return string.format("%s:%s:%s",
        util.safe_tostring(self.first_record),
        util.safe_tostring(self.written_records),
        util.safe_tostring(self.record_size))
end

function Handle:record_at(idx)
    local first = tonumber(self.first_record) or 0
    local size = tonumber(self.record_size) or 0
    return first + size * idx
end

-- A record is deleted when the high bit of its last byte is set.
function Handle:is_valid(rec)
    if self.t and type(self.t.IsRecordValid) == "function" then
        local ok, v = pcall(self.t.IsRecordValid, self.t, rec)
        if ok then return v and true or false end
    end
    local size = tonumber(self.record_size) or 0
    if size <= 0 then return false end
    local bytes = mem.read_bytes(rec + size - 1, 1)
    if type(bytes) ~= "table" or type(bytes[1]) ~= "number" then return false end
    return (bytes[1] & 0x80) == 0
end

-- Bounded by written_records. There is no MAX_SCAN here and there never will
-- be: a cap is how a real player went missing.
function Handle:records()
    self:refresh()
    local idx = -1
    local last = self:count() - 1
    return function()
        while idx < last do
            idx = idx + 1
            local rec = self:record_at(idx)
            if self:is_valid(rec) then return rec, idx end
        end
        return nil
    end
end

function Handle:field(fname)
    if self.t and type(self.t.fields) == "table" then
        return self.t.fields[fname]
    end
    return nil
end

function Handle:get(fname, rec)
    if self.t == nil or type(self.t.GetRecordFieldValue) ~= "function" then
        return nil, "no_table"
    end
    local ok, v = pcall(self.t.GetRecordFieldValue, self.t, rec, fname)
    if not ok then return nil, "read_threw" end
    return v
end

function db.reset()
    db._handles = {}
    schema.reset()
    db.index.reset()
end

function db.available()
    return schema.meta() ~= nil
end

function db.open(tname)
    if type(tname) ~= "string" or tname == "" then return nil, "bad_table_name" end
    local h = db._handles[tname]
    if h ~= nil then
        h:refresh()
        return h
    end
    h = setmetatable({ name = tname, addr = nil, t = nil,
                       first_record = 0, record_size = 0, written_records = 0 }, Handle)

    local addr = find_table_addr(tname)
    if addr then
        h.addr = addr
        local TABLE = t3db_table_module()
        local m = schema.meta()
        if TABLE and m then
            local ok, t = pcall(function()
                local obj = TABLE:new()
                obj:Load(addr, m)
                return obj
            end)
            if ok and type(t) == "table" and t.name ~= nil and t.name ~= "" then
                h.t = t
            end
        end
    end
    if h.t == nil then
        -- Fallback: LE's own DB object. Loses the struct address, so refresh()
        -- has to re-walk the list, but everything else still works.
        local t = load_le_table(tname)
        if t == nil then return nil, "no_such_table" end
        h.t = t
    end
    h:refresh()
    db._handles[tname] = h

    -- Fill in each field's `kind` from the live column descriptors, which is
    -- the only place the type code lives (GetDBMeta carries depth and min).
    local fields = schema.table_fields(tname)
    if fields and type(h.t.fields) == "table" then
        for name, f in pairs(h.t.fields) do
            local entry = fields[name]
            if entry ~= nil then
                entry.type = f.type
                if f.type == db.STRING_TYPE then entry.kind = "string"
                elseif f.type == db.FLOAT_TYPE then entry.kind = "float"
                else entry.kind = "int" end
            end
        end
    end
    return h
end

function db.kind_of(tname, fname)
    local f = schema.field(tname, fname)
    if f == nil then return nil end
    if f.kind ~= nil then return f.kind end
    -- Handle not open yet: open it to learn the column type.
    local h = db._handles[tname]
    if h == nil then
        local opened = db.open(tname)
        if opened == nil then return nil end
        f = schema.field(tname, fname)
        if f == nil then return nil end
    end
    return f.kind
end

-- ─────────────────────────────────────────────────────────────────────────
-- the write path
-- ─────────────────────────────────────────────────────────────────────────

local FLOAT_EPS = 1e-4

-- Returns ok, actual_value, reason, detail.
--
-- `actual_value` is the read-back, not the requested value. It is the whole
-- point of this function: SetRecordFieldValue returns true for a truncated int
-- and nil for a successful string, so its return value carries no information.
function db.set(h, rec, fname, value)
    if h == nil or h.t == nil then return false, nil, "no_table", "handle not open" end
    if type(h.t.SetRecordFieldValue) ~= "function" then
        return false, nil, "no_write_api", "SetRecordFieldValue missing"
    end

    local kind = db.kind_of(h.name, fname)
    local ok, code, detail = schema.validate_value(h.name, fname, value, kind)
    if not ok then return false, nil, code, detail end

    local written_value = value
    if kind == "string" then
        -- Clamp HERE. FIELD:SetString computes sub(1, len - max) and so keeps
        -- the WRONG END: a 20-char name into a 15-byte field stores 5 chars.
        local maxlen = schema.max_string_len(h.name, fname)
        if type(maxlen) == "number" and maxlen > 0 and #written_value > maxlen then
            written_value = written_value:sub(1, maxlen)
        end
    end

    local wok = pcall(h.t.SetRecordFieldValue, h.t, rec, fname, written_value)
    if not wok then return false, nil, "write_threw", nil end

    local rok, back = pcall(h.t.GetRecordFieldValue, h.t, rec, fname)
    if not rok then return false, nil, "readback_threw", nil end

    if kind == "float" then
        local diff = math.abs((tonumber(back) or 0) - (tonumber(written_value) or 0))
        if diff > FLOAT_EPS then
            return false, back, "verify_mismatch", string.format(
                "wrote %s read %s", util.safe_tostring(written_value), util.safe_tostring(back))
        end
        return true, back
    end

    if back ~= written_value then
        return false, back, "verify_mismatch", string.format(
            "wrote %s read %s", util.safe_tostring(written_value), util.safe_tostring(back))
    end
    return true, back
end

function db.get(h, rec, fname)
    if h == nil or h.t == nil then return nil, "no_table" end
    local ok, v = pcall(h.t.GetRecordFieldValue, h.t, rec, fname)
    if not ok then return nil, "read_threw" end
    return v
end

-- Returns written (meaning VERIFIED), failures[], skipped.
-- `written` never counts a write that could not be read back.
function db.set_many(h, rec, map, opts)
    opts = type(opts) == "table" and opts or {}
    local written, skipped = 0, 0
    local failures = {}
    local applied = {}
    local names = util.sorted_keys(map)
    for i = 1, #names do
        local fname = names[i]
        local value = map[fname]
        local ok, actual, reason, detail = db.set(h, rec, fname, value)
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
    return written, failures, skipped, applied
end

-- ─────────────────────────────────────────────────────────────────────────
-- row insert — the v1 API, with the guard that could never fire fixed
-- ─────────────────────────────────────────────────────────────────────────

function db.insert_row(tname, row_data)
    if not util.is_callable("InsertDBTableRow") then return nil, "no_insert_api" end
    local ok, row = util.host("InsertDBTableRow", tname, row_data)
    if not ok or type(row) ~= "table" then return nil, "insert_threw" end
    -- DOC.MD: a failed insert returns a row whose addr field is the STRING "0".
    -- row.addr is a DBRow field TABLE, so tostring(row.addr) is "table: 0x..."
    -- and is never "0" — which is why the v1 check never once fired.
    local addr = row.addr
    if type(addr) == "table" then addr = addr.value end
    addr = util.safe_tostring(addr or "")
    if addr == "" or addr == "0" or addr == "nil" then return nil, "insert_rejected" end
    return row
end

-- GetDBTableRows materialises one Lua table per row AND per field. On `players`
-- that is ~2.5M tables and 4-5 seconds of frozen thread. This wrapper exists
-- only for small tables (editedplayernames) where insert needs the v1 row API.
function db.rows_small(tname, max_rows)
    if not util.is_callable("GetDBTableRows") then return nil, "no_rows_api" end
    local ceiling = max_rows or LEC.config.rows_small_ceiling or 4000
    local h = db.open(tname)
    if h ~= nil then
        local n = h:count()
        if n > ceiling then
            return nil, string.format("table_too_large:%d>%d", n, ceiling)
        end
    end
    if tname == "players" then
        -- Not a size check: it is never correct to materialise this table.
        return nil, "refused_players_table"
    end
    local ok, rows = util.host("GetDBTableRows", tname)
    if not ok or type(rows) ~= "table" then return nil, "rows_threw" end
    return rows
end

function db.row_field_value(row, fname)
    if type(row) ~= "table" then return nil end
    local f = row[fname]
    if type(f) == "table" then return f.value end
    return f
end

function db.edit_row_field(row, fname, value)
    if type(row) ~= "table" then return false, "no_row" end
    if not util.is_callable("EditDBTableField") then return false, "no_edit_api" end
    local f = row[fname]
    if type(f) ~= "table" then return false, "no_field" end
    f.value = util.safe_tostring(value)
    local ok = util.host("EditDBTableField", f)
    if not ok then return false, "edit_threw" end
    -- Read back through the row object; it is the only signal available here.
    local back = db.row_field_value(row, fname)
    if util.safe_tostring(back) ~= util.safe_tostring(value) then
        return false, "verify_mismatch", back
    end
    return true, nil, back
end

-- ─────────────────────────────────────────────────────────────────────────
-- index — playerid -> record address, built once per session
-- ─────────────────────────────────────────────────────────────────────────

local index = {}
db.index = index

index.BUILD_SLICE = 4000

index.players = {
    by_id = {}, generation = nil, entries = 0, built_at = 0,
    valid = false, partial = false, cursor = 0, build_ms = 0, state = "cold",
}
index.squads = {
    by_team = {}, generation = nil, valid = false, entries = 0,
}

function index.reset()
    index.players = {
        by_id = {}, generation = nil, entries = 0, built_at = 0,
        valid = false, partial = false, cursor = 0, build_ms = 0, state = "cold",
    }
    index.squads = { by_team = {}, generation = nil, valid = false, entries = 0 }
end

function index.invalidate_players()
    index.players.valid = false
    index.players.partial = false
    index.players.cursor = 0
    index.players.state = "invalidated"
end

function index.invalidate_squads()
    index.squads.valid = false
    index.squads.by_team = {}
end

-- One pass reading exactly one field per record. Chunkable: pass a deadline and
-- it returns false when it runs out of budget, resuming on the next call.
function index.build_players(deadline)
    local h = db.open("players")
    if h == nil then return false, "no_players_table" end
    h:refresh()
    local gen = h:generation()
    local started = util.clock()

    if index.players.partial and index.players.generation ~= gen then
        index.players.partial = false
        index.players.cursor = 0
    end
    if not index.players.partial then
        index.players.by_id = {}
        index.players.cursor = 0
        index.players.entries = 0
        index.players.generation = gen
        index.players.build_ms = 0
    end

    local by_id = index.players.by_id
    local idx = index.players.cursor
    local last = h:count() - 1
    local n = 0

    while idx <= last do
        local rec = h:record_at(idx)
        if h:is_valid(rec) then
            local pid = h:get("playerid", rec)
            pid = tonumber(pid)
            if pid ~= nil then
                if by_id[pid] == nil then
                    index.players.entries = index.players.entries + 1
                end
                by_id[pid] = rec
            end
        end
        idx = idx + 1
        n = n + 1
        if n >= index.BUILD_SLICE then
            index.players.cursor = idx
            index.players.partial = idx <= last
            index.players.build_ms = index.players.build_ms + (util.clock() - started) * 1000
            if deadline ~= nil and util.clock() >= deadline then
                index.players.state = "building"
                return false
            end
            n = 0
            started = util.clock()
        end
    end

    index.players.cursor = idx
    index.players.partial = false
    index.players.valid = true
    index.players.built_at = util.clock()
    index.players.build_ms = index.players.build_ms + (util.clock() - started) * 1000
    index.players.state = "built"
    return true
end

-- Returns rec, nil on a hit; nil, reason otherwise. reason is one of
-- "stale" | "absent" | "moved".
function index.find_player(playerid)
    local h = db.open("players")
    if h == nil then return nil, "no_table" end
    if not index.players.valid then return nil, "stale" end
    if h:generation() ~= index.players.generation then return nil, "stale" end
    local rec = index.players.by_id[playerid]
    if rec == nil then return nil, "absent" end
    -- One field read proves the address still holds this player. The game can
    -- insert a row without firing an event the core sees.
    local pid = tonumber(h:get("playerid", rec))
    if pid ~= playerid then
        index.players.valid = false
        return nil, "moved"
    end
    return rec
end

-- The resolution ladder. Never silent: `state` lands in the result JSON so a
-- "why was this slow" question is answered by the file.
function index.resolve_player(playerid, deadline)
    local rec, reason = index.find_player(playerid)
    if rec ~= nil then return rec, "hit" end
    if reason == "absent" then return nil, "hit" end
    if reason == "no_table" then return nil, "no_table" end

    if reason == "moved" or reason == "stale" then
        local built = index.build_players(deadline)
        if built then
            local rec2 = index.find_player(playerid)
            if rec2 ~= nil then return rec2, "rebuilt" end
            return nil, "rebuilt"
        end
        -- Budget ran out mid-build. Do a bounded linear scan for THIS lookup
        -- only; the rebuild resumes on the next step.
        local found = db.scan_for(db.open("players"), "playerid", playerid)
        if found ~= nil then return found, "scan_fallback" end
        return nil, "scan_fallback"
    end
    return nil, "unknown"
end

-- Bounded by written_records, so even the degraded path cannot reproduce the
-- 25000-cap bug.
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

-- Every matching record, for upsert duplicate detection.
function db.scan_all(h, key_field, key_value, limit)
    local out = {}
    if h == nil then return out end
    for rec in h:records() do
        local v = h:get(key_field, rec)
        if v == key_value or tonumber(v) == tonumber(key_value) then
            out[#out + 1] = rec
            if limit and #out >= limit then break end
        end
    end
    return out
end

-- Resolve a target record for any table. players goes through the index (O(1));
-- everything else is a bounded scan.
function db.resolve(tname, key_field, key_value, deadline)
    local h = db.open(tname)
    if h == nil then return nil, nil, "no_such_table" end
    if tname == "players" and key_field == "playerid" then
        local rec, state = index.resolve_player(key_value, deadline)
        return h, rec, state
    end
    local rec = db.scan_for(h, key_field, key_value)
    return h, rec, "scan"
end

function index.build_squads()
    local h = db.open("teamplayerlinks")
    if h == nil then return false, "no_teamplayerlinks" end
    h:refresh()
    local by_team = {}
    local entries = 0
    for rec in h:records() do
        local team = tonumber(h:get("teamid", rec))
        local pid = tonumber(h:get("playerid", rec))
        if team ~= nil and pid ~= nil then
            local list = by_team[team]
            if list == nil then list = {} by_team[team] = list end
            list[#list + 1] = pid
            entries = entries + 1
        end
    end
    index.squads.by_team = by_team
    index.squads.entries = entries
    index.squads.generation = h:generation()
    index.squads.valid = true
    return true
end

function index.team_players(teamid)
    if not index.squads.valid then
        local ok = index.build_squads()
        if not ok then return nil end
    end
    return index.squads.by_team[teamid]
end

function index.report()
    return {
        state = index.players.state,
        entries = index.players.entries,
        rebuilt = index.players.state == "built" or index.players.state == "rebuilt",
        generation = index.players.generation,
        build_ms = math.floor((index.players.build_ms or 0) + 0.5),
        partial = index.players.partial,
    }
end

return db
