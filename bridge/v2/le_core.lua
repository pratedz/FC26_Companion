--[[ LE Companion v2 resident core — entry point, namespace, utilities.

  Loaded once from autorun. Registers event handlers and NOTHING else: no DB
  access, no scan, no drain at load time. Draining during LE init is what froze
  the editor historically.

  Four host events exist and only four:
      pre__CareerModeEvent   post__CareerModeEvent
      pre__LEInitDoneEvent   post__LEInitDoneEvent
  Handler signature is (events_manager, event_id, event). There is no frame
  callback, no tick, no timer, and nothing resumes coroutines — so a blocking
  loop freezes the game thread and "resumable" has to mean "progress is a value
  on disk that the next event advances" (see le_worker.lua).

  Layout (the design doc's 15 modules collapsed into four files):
      le_core.lua    this file — util, json, log, events, wiring
      le_db.lua      schema, table handles, player index, verified writes
      le_ops.lua     validation + one handler per op
      le_worker.lua  queue drain, step pump, results, poison quarantine

  Nothing in this file may throw. Every LE global is probed with
  type(x) == "function" before it is called, and every call site that can reach
  the host is wrapped in pcall.
]]

local CORE_VERSION = "2.0.0"

local existing = rawget(_G, "LEC")
if type(existing) == "table" and existing.__loaded == true then
    return existing
end

local LEC = {}
LEC.__loaded = true
LEC.__armed = false
LEC.version = CORE_VERSION
LEC.contract = { descriptor_schema = 1, result_schema = 1 }
_G.LEC = LEC

-- ─────────────────────────────────────────────────────────────────────────
-- util
-- ─────────────────────────────────────────────────────────────────────────

local util = {}
LEC.util = util

-- os.clock() on Windows/MSVC is wall time since process start with ms
-- resolution; os.time() only has 1s resolution and cannot drive a step budget.
-- The __mock_clock indirection makes every timing test deterministic.
function util.clock()
    local mock = rawget(_G, "__mock_clock")
    if type(mock) == "function" then
        local ok, v = pcall(mock)
        if ok and type(v) == "number" then return v end
    end
    local ok, v = pcall(os.clock)
    if ok and type(v) == "number" then return v end
    return 0
end

function util.now()
    local ok, v = pcall(os.time)
    if ok and type(v) == "number" then return v end
    return 0
end

function util.is_callable(name)
    return type(rawget(_G, name)) == "function"
end

-- Call an LE global by name if and only if it exists. Returns ok, value...
function util.host(name, ...)
    local fn = rawget(_G, name)
    if type(fn) ~= "function" then return false, "no_api:" .. tostring(name) end
    return pcall(fn, ...)
end

function util.path_join(a, b)
    if a == nil or a == "" then return tostring(b or "") end
    local last = string.sub(tostring(a), -1)
    if last == "/" or last == "\\" then
        return tostring(a) .. tostring(b or "")
    end
    return tostring(a) .. "/" .. tostring(b or "")
end

function util.normalise_path(p)
    local s = tostring(p or "")
    s = s:gsub("\\", "/")
    s = s:gsub("//+", "/")
    return s
end

function util.is_absolute(p)
    local s = util.normalise_path(p)
    if s:match("^%a:/") then return true end
    if s:sub(1, 1) == "/" then return true end
    return false
end

-- A descriptor must never be able to steer a write outside the allowed roots.
function util.path_under(p, root)
    if root == nil or root == "" then return false end
    local a = util.normalise_path(p):lower()
    local b = util.normalise_path(root):lower()
    if b:sub(-1) ~= "/" then b = b .. "/" end
    if a:find("%.%./") then return false end
    if a:sub(-3) == "/.." then return false end
    return a:sub(1, #b) == b
end

function util.read_file(path)
    local f = io.open(tostring(path or ""), "rb")
    if not f then return nil end
    local data = f:read("*a")
    f:close()
    return data
end

function util.write_file(path, data)
    local f = io.open(tostring(path or ""), "wb")
    if not f then return false end
    f:write(tostring(data or ""))
    f:close()
    return true
end

function util.append_file(path, data)
    local f = io.open(tostring(path or ""), "ab")
    if not f then return false end
    f:write(tostring(data or ""))
    f:close()
    return true
end

function util.delete_file(path)
    pcall(function() os.remove(tostring(path or "")) end)
end

function util.file_exists(path)
    local f = io.open(tostring(path or ""), "rb")
    if f then f:close() return true end
    return false
end

-- write .tmp, remove dest, rename. os.rename refuses an existing dest on
-- Windows, so the remove is not optional.
function util.atomic_write(path, data)
    local dest = tostring(path or "")
    local tmp = dest .. ".tmp"
    if not util.write_file(tmp, data) then return false end
    pcall(function() os.remove(dest) end)
    local ok = pcall(function() return os.rename(tmp, dest) end)
    if not ok then
        -- Rename failed (dest locked). Fall back to a direct write so the
        -- caller still gets a file rather than silence.
        util.delete_file(tmp)
        return util.write_file(dest, data)
    end
    return true
end

local ERR_MAX = 200

-- v1 had to collapse Lua error text to a single token because it travelled in a
-- key=value line whose msg= key is matched greedily to end-of-line. JSON removes
-- that constraint; this is kept only for the legacy shim.
function util.collapse_err(e)
    local s = nil
    local ok = pcall(function() s = tostring(e) end)
    if not ok or type(s) ~= "string" or s == "" then
        s = "unprintable_error"
    end
    local ok2 = pcall(function()
        s = s:gsub("%s+", "_")
        s = s:gsub("^_+", "")
        s = s:gsub("_+$", "")
    end)
    if not ok2 or type(s) ~= "string" or s == "" then
        s = "unprintable_error"
    end
    if #s > ERR_MAX then s = string.sub(s, 1, ERR_MAX) end
    return s
end

function util.safe_tostring(v)
    local s = nil
    local ok = pcall(function() s = tostring(v) end)
    if ok and type(s) == "string" then return s end
    return "?"
end

-- Job ids name files. Anything outside this set is a rejection, not a sanitise.
function util.valid_job_id(id)
    if type(id) ~= "string" then return false end
    if #id < 1 or #id > 120 then return false end
    return id:match("^[A-Za-z0-9_.%-]+$") ~= nil
end

function util.count_keys(t)
    if type(t) ~= "table" then return 0 end
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    return n
end

function util.is_array(t)
    if type(t) ~= "table" then return false end
    local n = 0
    for k in pairs(t) do
        if type(k) ~= "number" then return false end
        n = n + 1
    end
    return n == #t
end

function util.copy_shallow(t)
    local out = {}
    if type(t) == "table" then
        for k, v in pairs(t) do out[k] = v end
    end
    return out
end

function util.sorted_keys(t)
    local keys = {}
    if type(t) == "table" then
        for k in pairs(t) do keys[#keys + 1] = k end
    end
    table.sort(keys, function(a, b) return util.safe_tostring(a) < util.safe_tostring(b) end)
    return keys
end

-- pcall wrapper that logs instead of propagating. The core never throws into
-- the host: a Lua error inside an event handler is a game-thread hazard.
function util.guard(fn, ...)
    if type(fn) ~= "function" then return false, "not_a_function" end
    local ok, a, b, c, d = pcall(fn, ...)
    if not ok then
        LEC.log.write("guard: " .. util.collapse_err(a))
        return false, a
    end
    return true, a, b, c, d
end

function util.traceback(msg)
    if type(debug) == "table" and type(debug.traceback) == "function" then
        local ok, tb = pcall(debug.traceback, util.safe_tostring(msg), 2)
        if ok and type(tb) == "string" then return tb end
    end
    return util.safe_tostring(msg)
end

-- ─────────────────────────────────────────────────────────────────────────
-- log — ring buffer plus Log() passthrough
-- ─────────────────────────────────────────────────────────────────────────

local log = { ring = {}, cap = 400, head = 0 }
LEC.log = log

function log.write(msg)
    local line = string.format("%.3f %s", util.clock(), util.safe_tostring(msg))
    log.head = log.head + 1
    log.ring[(log.head % log.cap) + 1] = line
    local host_log = rawget(_G, "Log")
    if type(host_log) == "function" then
        pcall(host_log, "[LEC] " .. util.safe_tostring(msg))
    end
    return line
end

-- Per-job capture: everything written between open()/close() lands in the
-- result JSON, so a support question is answered by one file.
function log.capture_open()
    log.capture = {}
end

function log.capture_close()
    local c = log.capture or {}
    log.capture = nil
    return c
end

function log.job(msg)
    local line = log.write(msg)
    if type(log.capture) == "table" and #log.capture < 500 then
        log.capture[#log.capture + 1] = line
    end
    return line
end

function log.tail(n)
    local out = {}
    local count = math.min(n or 50, log.cap)
    for i = math.max(1, log.head - count + 1), log.head do
        local v = log.ring[(i % log.cap) + 1]
        if v then out[#out + 1] = v end
    end
    return out
end

-- ─────────────────────────────────────────────────────────────────────────
-- json — LE ships imports/external/json; require it defensively.
-- ─────────────────────────────────────────────────────────────────────────

local json = {}
LEC.json = json

json.null = setmetatable({}, { __tostring = function() return "null" end })
json.empty_array = setmetatable({}, { __tostring = function() return "[]" end })

local ESCAPES = {
    ['"'] = '\\"', ["\\"] = "\\\\", ["\b"] = "\\b", ["\f"] = "\\f",
    ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
}

local function esc_char(c)
    local e = ESCAPES[c]
    if e then return e end
    return string.format("\\u%04x", string.byte(c))
end

local function encode_string(s)
    return '"' .. tostring(s):gsub('[%c"\\]', esc_char) .. '"'
end

local function encode_number(n)
    if n ~= n or n == math.huge or n == -math.huge then return "null" end
    if math.type and math.type(n) == "integer" then return string.format("%d", n) end
    if n == math.floor(n) and math.abs(n) < 1e15 then return string.format("%d", n) end
    return string.format("%.14g", n)
end

local encode_value

local function encode_table(v, depth)
    if depth > 24 then return "null" end
    if v == json.null then return "null" end
    if v == json.empty_array then return "[]" end
    if util.is_array(v) then
        local parts = {}
        for i = 1, #v do parts[i] = encode_value(v[i], depth + 1) end
        return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = util.sorted_keys(v)
    local parts = {}
    for i = 1, #keys do
        local k = keys[i]
        parts[#parts + 1] = encode_string(util.safe_tostring(k)) .. ":" .. encode_value(v[k], depth + 1)
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

encode_value = function(v, depth)
    local t = type(v)
    if v == nil then return "null" end
    if t == "boolean" then return v and "true" or "false" end
    if t == "number" then return encode_number(v) end
    if t == "string" then return encode_string(v) end
    if t == "table" then return encode_table(v, depth or 0) end
    return encode_string(util.safe_tostring(v))
end

local function fallback_encode(v)
    return encode_value(v, 0)
end

-- Minimal recursive-descent decoder. Only used when LE's json module is absent.
local function fallback_decode(str)
    local s = tostring(str or "")
    local pos = 1

    local function err(msg)
        error(string.format("json: %s at %d", msg, pos), 0)
    end

    local function skip_ws()
        while pos <= #s do
            local c = s:sub(pos, pos)
            if c == " " or c == "\t" or c == "\n" or c == "\r" then
                pos = pos + 1
            else
                break
            end
        end
    end

    local parse_value

    local function parse_string()
        pos = pos + 1
        local buf = {}
        while true do
            if pos > #s then err("unterminated string") end
            local c = s:sub(pos, pos)
            if c == '"' then
                pos = pos + 1
                break
            end
            if c == "\\" then
                local n = s:sub(pos + 1, pos + 1)
                if n == "n" then buf[#buf + 1] = "\n"
                elseif n == "t" then buf[#buf + 1] = "\t"
                elseif n == "r" then buf[#buf + 1] = "\r"
                elseif n == "b" then buf[#buf + 1] = "\b"
                elseif n == "f" then buf[#buf + 1] = "\f"
                elseif n == "u" then
                    local hex = s:sub(pos + 2, pos + 5)
                    local cp = tonumber(hex, 16) or 63
                    if cp < 128 then
                        buf[#buf + 1] = string.char(cp)
                    else
                        buf[#buf + 1] = "?"
                    end
                    pos = pos + 4
                else
                    buf[#buf + 1] = n
                end
                pos = pos + 2
            else
                buf[#buf + 1] = c
                pos = pos + 1
            end
        end
        return table.concat(buf)
    end

    local function parse_number()
        local start = pos
        while pos <= #s and s:sub(pos, pos):match("[%-%+%.eE0-9]") do
            pos = pos + 1
        end
        local n = tonumber(s:sub(start, pos - 1))
        if n == nil then err("bad number") end
        return n
    end

    local function parse_array()
        pos = pos + 1
        local out = {}
        skip_ws()
        if s:sub(pos, pos) == "]" then pos = pos + 1 return out end
        while true do
            out[#out + 1] = parse_value()
            skip_ws()
            local c = s:sub(pos, pos)
            if c == "," then
                pos = pos + 1
            elseif c == "]" then
                pos = pos + 1
                break
            else
                err("expected , or ]")
            end
        end
        return out
    end

    local function parse_object()
        pos = pos + 1
        local out = {}
        skip_ws()
        if s:sub(pos, pos) == "}" then pos = pos + 1 return out end
        while true do
            skip_ws()
            if s:sub(pos, pos) ~= '"' then err("expected key") end
            local k = parse_string()
            skip_ws()
            if s:sub(pos, pos) ~= ":" then err("expected :") end
            pos = pos + 1
            out[k] = parse_value()
            skip_ws()
            local c = s:sub(pos, pos)
            if c == "," then
                pos = pos + 1
            elseif c == "}" then
                pos = pos + 1
                break
            else
                err("expected , or }")
            end
        end
        return out
    end

    parse_value = function()
        skip_ws()
        local c = s:sub(pos, pos)
        if c == "{" then return parse_object() end
        if c == "[" then return parse_array() end
        if c == '"' then return parse_string() end
        if c == "t" and s:sub(pos, pos + 3) == "true" then pos = pos + 4 return true end
        if c == "f" and s:sub(pos, pos + 4) == "false" then pos = pos + 5 return false end
        if c == "n" and s:sub(pos, pos + 3) == "null" then pos = pos + 4 return nil end
        if c:match("[%-0-9]") then return parse_number() end
        err("unexpected character " .. tostring(c))
    end

    local v = parse_value()
    skip_ws()
    return v
end

-- LE's json.lua is preferred for decoding (it is battle-tested) but the encoder
-- is ours regardless: key order must be stable so a result file diffs cleanly.
do
    local host_json = nil
    local ok, mod = pcall(require, "imports/external/json")
    if ok and type(mod) == "table" then host_json = mod end
    if host_json == nil then
        local ok2, mod2 = pcall(require, "imports/external/json.lua")
        if ok2 and type(mod2) == "table" then host_json = mod2 end
    end
    json.host = host_json
    json.source = host_json and "le_external" or "inline_fallback"
end

function json.encode(v)
    local ok, out = pcall(fallback_encode, v)
    if ok and type(out) == "string" then return out end
    return "{\"error\":\"encode_failed\"}"
end

function json.decode(s)
    if type(s) ~= "string" or s == "" then return nil, "empty" end
    if json.host and type(json.host.decode) == "function" then
        local ok, v = pcall(json.host.decode, s)
        if ok and v ~= nil then return v end
    end
    local ok2, v2 = pcall(fallback_decode, s)
    if ok2 then return v2 end
    return nil, util.collapse_err(v2)
end

-- ─────────────────────────────────────────────────────────────────────────
-- events — ids come from ENUM_CM_EVENT_MSG_*, never CONST_CM_EVENTS_NAMES.
-- The two tables disagree above id 33 and GetCMEventNameByID reads the wrong
-- one, so it mislabels every event the core cares about.
-- ─────────────────────────────────────────────────────────────────────────

local events = {}
LEC.events = events

-- Fallback ids for LE v26.3.5, used when the enums module did not load.
events.FALLBACK_IDS = {
    ABOUT_TO_INIT_MODE = 5,
    CAREER_TYPE_SELECTED = 6,
    DAY_PASSED = 15,
    WEEK_PASSED = 16,
    SEASON_RESET = 23,
    SEASON_ENDED = 24,
    ENTERED_HUB_FIRST_TIME = 26,
    DATA_READY = 27,
    PREPARE_FOR_SAVE = 28,
    POST_LOAD_PREPARE = 29,
    PREPARE_FOR_FIRST_SAVE = 30,
    ABOUT_TO_ENTER_PREMATCH = 37,
    POST_MATCH_REPORTS_DISPLAYED = 39,
    ABOUT_TO_ENTER_A_MATCH = 42,
    FE_TO_BE = 43,
    ABOUT_TO_SIM_OVER_A_MATCH = 44,
    ABOUT_TO_ENTER_QUICK_SIM = 45,
    ABOUT_TO_ENTER_INTERACTIVE_SIM = 46,
    ABOUT_TO_ENTER_PLAY_HIGHLIGHTS = 47,
    ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM = 48,
    PLAYER_INSERTED_INTO_PLAYERS_TABLE = 58,
    PLAYER_DELETED_FROM_PLAYERS_TABLE = 59,
    PLAYERS_RETIRED = 60,
    ALL_RETIRED_PLAYERS_COMPLETE = 61,
    PLAYER_CONTRACT_TERMINATION = 62,
    PLAYER_CONTRACT_ACCEPTED = 63,
    SCREEN_HAS_DONE_LOADING = 68,
    ENTERING_TEAM_MANAGEMENT = 82,
    TRANSFER_MOVE_COMPLETE = 86,
    YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY = 109,
    PLAYER_ADDED_TO_TEAM = 110,
    PLAYER_REMOVED_FROM_TEAM = 111,
    YOUTH_PLAYER_PROMOTION = 112,
    YOUTH_PLAYERS_RETIREMENT = 115,
}

events.HARD_RESET_NAMES = {
    "DATA_READY", "POST_LOAD_PREPARE", "ABOUT_TO_INIT_MODE", "CAREER_TYPE_SELECTED",
}
events.INVALIDATE_PLAYERS_NAMES = {
    "PLAYER_INSERTED_INTO_PLAYERS_TABLE", "PLAYER_DELETED_FROM_PLAYERS_TABLE",
    "PLAYERS_RETIRED", "ALL_RETIRED_PLAYERS_COMPLETE",
    "YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY", "YOUTH_PLAYER_PROMOTION",
    "YOUTH_PLAYERS_RETIREMENT", "SEASON_RESET", "SEASON_ENDED",
}
events.INVALIDATE_SQUADS_NAMES = {
    "TRANSFER_MOVE_COMPLETE", "PLAYER_ADDED_TO_TEAM", "PLAYER_REMOVED_FROM_TEAM",
    "PLAYER_CONTRACT_TERMINATION", "PLAYER_CONTRACT_ACCEPTED",
}
-- The game is serialising a save or entering a match. Writing to the DB here is
-- a plausible cause of the historic freezes.
events.BLACKOUT_NAMES = {
    "PREPARE_FOR_SAVE", "PREPARE_FOR_FIRST_SAVE", "POST_LOAD_PREPARE",
    "ABOUT_TO_INIT_MODE", "DATA_READY", "SEASON_RESET",
    "ABOUT_TO_ENTER_PREMATCH", "ABOUT_TO_ENTER_A_MATCH", "FE_TO_BE",
    "ABOUT_TO_SIM_OVER_A_MATCH", "ABOUT_TO_ENTER_QUICK_SIM",
    "ABOUT_TO_ENTER_INTERACTIVE_SIM", "ABOUT_TO_ENTER_PLAY_HIGHLIGHTS",
    "ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM",
}
events.PUMP_NAMES = {
    "DAY_PASSED", "WEEK_PASSED", "ENTERED_HUB_FIRST_TIME",
    "POST_MATCH_REPORTS_DISPLAYED", "SCREEN_HAS_DONE_LOADING",
    "ENTERING_TEAM_MANAGEMENT",
}

events.id_of = {}
events.name_of = {}
events.hard_reset = {}
events.invalidate_players = {}
events.invalidate_squads = {}
events.blackout = {}
events.pump = {}
events.resolved_from = "fallback"

local function resolve_id(name)
    local v = rawget(_G, "ENUM_CM_EVENT_MSG_" .. name)
    if type(v) == "number" then
        events.resolved_from = "enums"
        return v
    end
    return events.FALLBACK_IDS[name]
end

local function fill(set, names)
    for i = 1, #names do
        local name = names[i]
        local id = events.id_of[name]
        if type(id) == "number" then set[id] = name end
    end
end

function events.load()
    pcall(function() require("imports/career_mode/enums") end)
    events.id_of = {}
    events.name_of = {}
    for name in pairs(events.FALLBACK_IDS) do
        local id = resolve_id(name)
        if type(id) == "number" then
            events.id_of[name] = id
            events.name_of[id] = name
        end
    end
    events.hard_reset = {}
    events.invalidate_players = {}
    events.invalidate_squads = {}
    events.blackout = {}
    events.pump = {}
    fill(events.hard_reset, events.HARD_RESET_NAMES)
    fill(events.invalidate_players, events.INVALIDATE_PLAYERS_NAMES)
    fill(events.invalidate_squads, events.INVALIDATE_SQUADS_NAMES)
    fill(events.blackout, events.BLACKOUT_NAMES)
    fill(events.pump, events.PUMP_NAMES)
    return true
end

function events.name(id)
    if type(id) ~= "number" then return "unknown" end
    return events.name_of[id] or ("event_" .. tostring(id))
end

-- Blackout wins over every other classification, including hard_reset. Several
-- ids (5, 27, 29, 23) are both "the world changed, drop the caches" and "do not
-- touch the DB right now"; the caller must see the dangerous half.
function events.classify(id)
    if type(id) ~= "number" then return "ignore" end
    if events.blackout[id] then return "blackout" end
    if events.hard_reset[id] then return "hard_reset" end
    if events.invalidate_players[id] then return "invalidate_players" end
    if events.invalidate_squads[id] then return "invalidate_squads" end
    if events.pump[id] then return "pump" end
    return "ignore"
end

-- Cache invalidation is a separate question from "may I drain". An id can be
-- both blackout and hard_reset; the cache still has to be dropped.
function events.cache_action(id)
    if type(id) ~= "number" then return "none" end
    if events.hard_reset[id] then return "hard_reset" end
    if events.invalidate_players[id] then return "invalidate_players" end
    if events.invalidate_squads[id] then return "invalidate_squads" end
    return "none"
end

function events.is_blackout(id)
    return type(id) == "number" and events.blackout[id] ~= nil
end

events.load()

-- ─────────────────────────────────────────────────────────────────────────
-- module loading
-- ─────────────────────────────────────────────────────────────────────────

local function self_dir()
    if type(debug) ~= "table" or type(debug.getinfo) ~= "function" then return nil end
    local ok, info = pcall(debug.getinfo, 1, "S")
    if not ok or type(info) ~= "table" then return nil end
    local src = util.safe_tostring(info.source)
    if src:sub(1, 1) == "@" then src = src:sub(2) end
    src = util.normalise_path(src)
    return src:match("^(.*)/[^/]+$")
end

LEC.dir = self_dir()

-- Try package.path first (an install may have wired it), then dofile relative
-- to this file. Both are pcall'd; a missing sibling degrades the core to
-- "armed but idle" rather than throwing inside LE's loader.
function LEC.require_module(name)
    local ok, mod = pcall(require, "le_companion." .. name)
    if ok and type(mod) == "table" then return mod end
    ok, mod = pcall(require, name)
    if ok and type(mod) == "table" then return mod end
    if LEC.dir then
        ok, mod = pcall(dofile, LEC.dir .. "/" .. name .. ".lua")
        if ok and type(mod) == "table" then return mod end
    end
    return nil, util.collapse_err(mod)
end

LEC.load_errors = {}

function LEC.load_modules()
    local order = { "le_db", "le_ops", "le_worker" }
    local field = { le_db = "db", le_ops = "ops", le_worker = "worker" }
    for i = 1, #order do
        local name = order[i]
        if LEC[field[name]] == nil then
            local mod, err = LEC.require_module(name)
            if mod then
                LEC[field[name]] = mod
            else
                LEC.load_errors[#LEC.load_errors + 1] = name .. ": " .. util.safe_tostring(err)
                log.write("module load failed " .. name .. " " .. util.safe_tostring(err))
            end
        end
    end
    return #LEC.load_errors == 0
end

-- ─────────────────────────────────────────────────────────────────────────
-- configuration + arming
-- ─────────────────────────────────────────────────────────────────────────

LEC.config = {
    queue_dir = nil,
    allowed_write_roots = {},
    le_scripts_dir = nil,
    default_budget_ms = 200,
    max_attempts = 3,
    rows_small_ceiling = 4000,
    max_playerid = 499999,
    enable_raw_lua = true,
}

function LEC.configure(opts)
    opts = type(opts) == "table" and opts or {}
    local cfg = LEC.config
    if type(opts.queue_dir) == "string" and opts.queue_dir ~= "" then
        cfg.queue_dir = util.normalise_path(opts.queue_dir)
    end
    if type(opts.le_scripts_dir) == "string" then
        cfg.le_scripts_dir = util.normalise_path(opts.le_scripts_dir)
    end
    if type(opts.default_budget_ms) == "number" then
        cfg.default_budget_ms = opts.default_budget_ms
    end
    if type(opts.max_attempts) == "number" then
        cfg.max_attempts = opts.max_attempts
    end
    if type(opts.enable_raw_lua) == "boolean" then
        cfg.enable_raw_lua = opts.enable_raw_lua
    end
    cfg.allowed_write_roots = {}
    if type(opts.allowed_write_roots) == "table" then
        for i = 1, #opts.allowed_write_roots do
            cfg.allowed_write_roots[#cfg.allowed_write_roots + 1] =
                util.normalise_path(opts.allowed_write_roots[i])
        end
    end
    if #cfg.allowed_write_roots == 0 and cfg.queue_dir then
        -- Default: the queue directory and its parent (the app root).
        cfg.allowed_write_roots[1] = cfg.queue_dir
        local parent = cfg.queue_dir:match("^(.*)/[^/]+$")
        if parent then cfg.allowed_write_roots[2] = parent end
    end
    if LEC.worker and type(LEC.worker.configure) == "function" then
        pcall(LEC.worker.configure, cfg)
    end
    return cfg
end

function LEC.qpath(name)
    return util.path_join(LEC.config.queue_dir, name)
end

function LEC.session_id()
    if LEC.__session then return LEC.__session end
    local seed = string.format("%d%.3f", util.now(), util.clock() * 1000)
    local h = 5381
    for i = 1, #seed do
        h = ((h * 33) + string.byte(seed, i)) % 4294967296
    end
    LEC.__session = string.format("%08x", h)
    return LEC.__session
end

function LEC.core_info()
    local ops_versions = {}
    if LEC.ops and type(LEC.ops.op_versions) == "function" then
        local ok, v = pcall(LEC.ops.op_versions)
        if ok and type(v) == "table" then ops_versions = v end
    end
    local warnings = {}
    for i = 1, #LEC.load_errors do warnings[#warnings + 1] = LEC.load_errors[i] end
    if LEC.db and type(LEC.db.warnings) == "function" then
        local ok, w = pcall(LEC.db.warnings)
        if ok and type(w) == "table" then
            for i = 1, #w do warnings[#warnings + 1] = w[i] end
        end
    end
    return {
        schema = 1,
        core_version = LEC.version,
        contract = LEC.contract,
        ops = ops_versions,
        capabilities = {
            "player_index", "squad_index", "chunked_jobs", "verify_writes",
            "growth_mirror", "snapshot", "two_phase_validation", "poison_quarantine",
        },
        limits = {
            max_playerid = LEC.config.max_playerid,
            max_written_records = 65535,
            default_budget_ms = LEC.config.default_budget_ms,
            max_attempts = LEC.config.max_attempts,
            rows_small_ceiling = LEC.config.rows_small_ceiling,
        },
        warnings = warnings,
        json_source = json.source,
        event_ids_from = events.resolved_from,
        lua = util.safe_tostring(rawget(_G, "_VERSION")),
        install_path = LEC.dir,
        queue_dir = LEC.config.queue_dir,
        armed_at = LEC.__armed_at or 0,
        last_pump_at = (LEC.worker and LEC.worker.last_pump_at) or 0,
        session_id = LEC.session_id(),
    }
end

-- Handlers ONLY. Nothing here touches the DB, allocates a scan, or drains.
function LEC.arm()
    if LEC.__armed then return true end
    local add = rawget(_G, "AddEventHandler")
    if type(add) ~= "function" then
        log.write("arm: AddEventHandler missing")
        return false
    end
    LEC.load_modules()
    LEC.__armed = true
    LEC.__armed_at = util.now()

    pcall(add, "post__LEInitDoneEvent", function()
        util.guard(function()
            if LEC.worker and type(LEC.worker.on_init_done) == "function" then
                LEC.worker.on_init_done()
            end
        end)
    end)

    pcall(add, "pre__CareerModeEvent", function(_mgr, event_id)
        util.guard(function()
            if LEC.worker and type(LEC.worker.on_career) == "function" then
                LEC.worker.on_career(event_id, "pre")
            end
        end)
    end)

    pcall(add, "post__CareerModeEvent", function(_mgr, event_id)
        util.guard(function()
            if LEC.worker and type(LEC.worker.on_career) == "function" then
                LEC.worker.on_career(event_id, "post")
            end
        end)
    end)

    log.write("armed core=" .. LEC.version .. " session=" .. LEC.session_id())
    return true
end

-- Manual drain from the LE console / Force Drain button. Deliberately not
-- called at load time.
function LEC.force_drain()
    if not (LEC.worker and type(LEC.worker.pump) == "function") then
        return "no_worker"
    end
    local ok, outcome = pcall(LEC.worker.pump, "force", nil)
    if not ok then return "error" end
    return outcome
end

_G.LECompanionV2_ForceDrain = LEC.force_drain
_G.LECompanionV2_Arm = LEC.arm

return LEC
