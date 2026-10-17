--[[ le_companion/init.lua — entry point.

Loaded by autorun via require("le_companion.init").
SI-6: arm registers handlers ONLY — never drains at load/init.
      A read-only CE timer may be started; it never runs writes.
]]

if type(_G.LEC) == "table" and _G.LEC.__loaded == true then
    return _G.LEC
end

-- These helpers are not guaranteed to exist before an autorun script asks for
-- them.  Without the explicit imports GetUserTeamID/IsInCM can be absent even
-- while a Career Mode save is open.
pcall(function() require("imports/career_mode/helpers") end)
pcall(function() require("imports/other/helpers") end)

-- Ensure sibling modules resolve both as le_companion.X and bare X.
local function self_dir()
    if type(debug) ~= "table" or type(debug.getinfo) ~= "function" then return nil end
    -- Not pcall(debug.getinfo, 1, ...): there level 1 is pcall itself ("=[C]")
    -- and DIR was always nil, so config.json and the native DLL were never found.
    local ok, info = pcall(function() return debug.getinfo(1, "S") end)
    if not ok or type(info) ~= "table" then return nil end
    local src = tostring(info.source or "")
    if src:sub(1, 1) == "@" then src = src:sub(2) end
    src = src:gsub("\\", "/")
    return src:match("^(.*)/[^/]+$")
end

local DIR = self_dir()
if DIR then
    package.path = DIR .. "/?.lua;" .. DIR .. "/?/init.lua;" .. package.path
end

local function load_mod(name)
    local ok, mod = pcall(require, "le_companion." .. name)
    if ok and type(mod) == "table" then
        -- Every sibling module uses the short require name (for compatibility
        -- with Live Editor's test/offline loader).  Alias the namespaced
        -- instance immediately so Lua never creates a second, unconfigured
        -- copy of stateful modules such as status and db.
        package.loaded[name] = mod
        return mod
    end
    ok, mod = pcall(require, name)
    if ok and type(mod) == "table" then
        package.loaded["le_companion." .. name] = mod
        return mod
    end
    if DIR then
        ok, mod = pcall(dofile, DIR .. "/" .. name .. ".lua")
        if ok and type(mod) == "table" then
            package.loaded[name] = mod
            package.loaded["le_companion." .. name] = mod
            return mod
        end
    end
    return nil, mod
end

local version = assert(load_mod("version"), "le_companion.version missing")
local util = assert(load_mod("util"), "le_companion.util missing")
local json = assert(load_mod("json"), "le_companion.json missing")
local log = assert(load_mod("log"), "le_companion.log missing")
local events = assert(load_mod("events"), "le_companion.events missing")
local status = assert(load_mod("status"), "le_companion.status missing")
local db = assert(load_mod("db"), "le_companion.db missing")
local ops = assert(load_mod("ops"), "le_companion.ops missing")
-- Optional, like native: without mirror.lua the core runs as before.
local mirror = load_mod("mirror")
local runner = assert(load_mod("runner"), "le_companion.runner missing")
-- Optional: a missing or broken native.lua must never stop the core.
local native = load_mod("native")

util.set_error_sink(function(msg) log.write(msg) end)

local LEC = {}
LEC.__loaded = true
LEC.__armed = false
LEC.version = version.VERSION
LEC.VERSION = version.VERSION
LEC.CONTRACT = version.CONTRACT
LEC.util = util
LEC.json = json
LEC.log = log
LEC.events = events
LEC.status = status
LEC.db = db
LEC.ops = ops
LEC.runner = runner
LEC.native = native
LEC.mirror = mirror
LEC.dir = DIR
LEC.load_errors = {}

LEC.config = {
    queue_dir = nil,
    allowed_write_roots = {},
    default_budget_ms = version.LIMITS.default_budget_ms,
    max_attempts = version.LIMITS.max_attempts,
    drain_budget_ms = version.LIMITS.drain_budget_ms,
    drain_max_jobs = version.LIMITS.drain_max_jobs,
    max_playerid = version.LIMITS.max_playerid,
    core_ops = {},
    native = true,              -- load native/lec_native.dll (tick)
    native_tick_writes = true,  -- native tick may run read/light jobs on a quiet hub
    mirror = true,              -- keep <queue>/mirror/squad.json current on a settled hub
    ui_event_jobs = true,       -- 2.6.42: hub screen events may run read/light jobs
    native_apc = false,         -- 2.6.42: lec_native's APC carrier; config.json "native_apc": true enables it
}

local function read_install_config()
    if not DIR then return nil end
    local text = util.read_text(util.join(DIR, "config.json"))
    if not text then return nil end
    local obj = json.try_decode_object(text)
    return obj
end

function LEC.configure(opts)
    opts = type(opts) == "table" and opts or {}
    local installed = read_install_config()
    if installed and type(installed.queue_dir) == "string" and not opts.queue_dir then
        opts.queue_dir = installed.queue_dir
    end
    local c = LEC.config
    if type(opts.queue_dir) == "string" and opts.queue_dir ~= "" then
        c.queue_dir = util.normalize(opts.queue_dir)
    end
    if type(opts.default_budget_ms) == "number" then
        c.default_budget_ms = opts.default_budget_ms
    end
    if type(opts.max_attempts) == "number" then
        c.max_attempts = opts.max_attempts
    end
    local configured_ops = opts.core_ops
    if configured_ops == nil and installed then configured_ops = installed.core_ops end
    c.core_ops = {}
    if type(configured_ops) == "table" then
        for i = 1, #configured_ops do
            if type(configured_ops[i]) == "string" then
                c.core_ops[configured_ops[i]] = true
            end
        end
    end
    for _, key in ipairs({ "native", "native_tick_writes", "mirror", "ui_event_jobs", "native_apc" }) do
        local v = opts[key]
        if v == nil and installed then v = installed[key] end
        if type(v) == "boolean" then c[key] = v end
    end
    c.allowed_write_roots = {}
    if type(opts.allowed_write_roots) == "table" then
        for i = 1, #opts.allowed_write_roots do
            c.allowed_write_roots[#c.allowed_write_roots + 1] =
                util.normalize(opts.allowed_write_roots[i])
        end
    end
    if #c.allowed_write_roots == 0 and c.queue_dir then
        c.allowed_write_roots[1] = c.queue_dir
        local parent = c.queue_dir:match("^(.*)/[^/]+$")
        if parent then c.allowed_write_roots[2] = parent end
    end
    status.configure(c, log)
    runner.configure(c, log)
    return c
end

function LEC.banner()
    return version.banner()
end

function LEC.session_id()
    return status.session_id()
end

function LEC.core_info()
    return {
        schema = 1,
        core_version = version.VERSION,
        contract = version.CONTRACT,
        ops = version.OP_VERSIONS,
        capabilities = version.CAPABILITIES,
        limits = version.LIMITS,
        warnings = util.copy(LEC.load_errors),
        event_ids_from = events.resolved_from,
        lua = tostring(_VERSION),
        install_path = DIR,
        queue_dir = LEC.config.queue_dir,
        armed_at = LEC.__armed_at or 0,
        last_pump_at = runner.last_pump_at or 0,
        session_id = status.session_id(),
        native = native and { loaded = native.loaded, reason = native.reason } or { loaded = false, reason = "module_missing" },
        mirror = mirror and mirror.info() or { last = "module_missing" },
    }
end

--- Handlers ONLY. Arm never drains (SI-6). The read-only CE timer starts
--- after LEInitDone / the first settled career post-event, not at autorun.
function LEC.arm()
    if LEC.__armed then return true end
    local add = rawget(_G, "AddEventHandler")
    if type(add) ~= "function" then
        -- Offline / unit-test host: still mark armed and write session if queue set.
        log.write("arm: AddEventHandler missing — session-only arm")
        LEC.__armed = true
        LEC.__armed_at = util.now()
        if LEC.config.queue_dir then
            status.write_session({ phase = "arm_no_host" })
            runner.crash_sweep()
        end
        return true
    end

    LEC.__armed = true
    LEC.__armed_at = util.now()

    pcall(add, "post__LEInitDoneEvent", function()
        util.pcall_named("on_init_done", function()
            runner.on_init_done()
        end)
    end)

    -- Career events are synchronous native callbacks. A pre-event must revoke
    -- readiness before the game starts saving, resetting or advancing time.
    -- runner.on_career does only Lua state changes outside a settled post-hub.
    local function career_handler(phase)
      return function(a, b)
        local id = nil
        if type(b) == "number" then
            id = b
        elseif type(a) == "number" then
            id = a
        end
        -- First, before any early return: the native plugin pins its tick to
        -- the thread and lua_State that deliver Career events.
        if native then native.mark_career() end
        if not id then return end
        runner.on_career(id, phase)
      end
    end
    pcall(add, "pre__CareerModeEvent", career_handler("pre"))
    pcall(add, "post__CareerModeEvent", career_handler("post"))

    -- Native plugin: binds entry points only. Its timer is armed by the
    -- first Career event, never during LE init (SI-6).
    if native then
        local ok, loaded = pcall(native.load, DIR, LEC.config, runner)
        if not ok then native.reason = "load_threw:" .. tostring(loaded) end
        log.write("native: " .. tostring(native.reason))
    end

    -- Write session immediately so Python sees ARMED without waiting for LEInitDone.
    if LEC.config.queue_dir then
        status.write_session({ phase = "arm", capabilities = { in_career = false, save_ready = false, hub_ready = false } })
        -- Crash sweep is pure file IO — safe. No job drain.
        runner.crash_sweep()
    end

    log.write("armed " .. version.banner() .. " session=" .. status.session_id())
    return true
end

function LEC.force_drain()
    return runner.force_drain()
end

_G.LEC = LEC
_G.LECompanion = LEC
_G.LECompanionV2_ForceDrain = function() return LEC.force_drain() end
_G.LECompanionV2_Arm = function() return LEC.arm() end

return LEC
