--[[ le_companion/native.lua — optional native plugin (lec_native.dll).

Live Editor gives Lua no timer: the core only ran on allow-listed Career hub
events. lec_native.dll (native/lec_native/ in the source repo) adds a ~200 ms
tick on the thread that delivers Career Mode events and calls
LECompanion_NativeTick() there. The DLL enters Lua only after it has matched
LE v26.3.6's code bytes, only on the Career event thread and only with the
same lua_State; otherwise it stays idle and the core behaves exactly as
before.

The tick arrives by thread timer or (1.1.0) by an APC that Windows runs only
inside one of that thread's own alertable waits. FC's Career thread has no
message pump, so the timer never fired there; native_status.json reports
which carrier delivers ("carrier") or "no_carrier" when neither does.

What a tick may do is decided by the runner (runner.on_native_tick): reads
and plain field edits once the hub has been quiet, never while a Career
event is open, never a signing, release or other heavy job. Simulation days
never run anything.

Disable without uninstalling: config.json "native": false, or create
native/DISABLE (stops ticks within ~1 s), or <queue>/native_disable. APC
only: config.json "native_apc": false, native/NO_APC or <queue>/native_no_apc.
]]

local native = {
    loaded = false,
    reason = "not_loaded",
    path = nil,
    ticks = 0,
    hotkeys = 0,
    apc = false,    -- the loaded DLL has the APC carrier and it is enabled
}

local DLL = "native/lec_native.dll"

local function file_exists(path)
    local f = io.open(path, "rb")
    if f then f:close() return true end
    return false
end

-- This module's own directory. init.lua's self_dir() calls
-- pcall(debug.getinfo, 1, "S"), whose level 1 is pcall itself ("=[C]"), so
-- LEC.dir is nil under stock Lua 5.4 and native.load would get no core_dir.
-- Here getinfo(1) is the closure below, whose source is this file.
native.own_dir = (function()
    if type(debug) ~= "table" or type(debug.getinfo) ~= "function" then return nil end
    local ok, info = pcall(function() return debug.getinfo(1, "S") end)
    if not ok or type(info) ~= "table" then return nil end
    local src = tostring(info.source or "")
    if src:sub(1, 1) ~= "@" then return nil end
    return (src:sub(2):gsub("\\", "/"):match("^(.*)/[^/]+$"))
end)()

-- When init.lua had no DIR it could not read config.json either, so honour
-- its kill switches here. Only ever tightens (false), never enables.
local function apply_install_switches(dir, cfg)
    local f = io.open(dir .. "/config.json", "rb")
    if not f then return end
    local text = f:read("*a") or ""
    f:close()
    if text:match('"native"%s*:%s*false') then cfg.native = false end
    if text:match('"native_tick_writes"%s*:%s*false') then cfg.native_tick_writes = false end
    if text:match('"native_apc"%s*:%s*false') then cfg.native_apc = false end
end

--- Load the plugin from <core_dir>/native/lec_native.dll.
--- Never throws. Returns true when the DLL's entry points were bound.
function native.load(core_dir, cfg, runner)
    native.runner = runner
    if (type(core_dir) ~= "string" or core_dir == "") and type(native.own_dir) == "string" then
        core_dir = native.own_dir
        if type(cfg) == "table" then pcall(apply_install_switches, core_dir, cfg) end
    end
    if type(cfg) == "table" and cfg.native == false then
        native.reason = "disabled_by_config"
        return false
    end
    if type(core_dir) ~= "string" or core_dir == "" then
        native.reason = "no_core_dir"
        return false
    end
    if type(package) ~= "table" or type(package.loadlib) ~= "function" then
        native.reason = "no_loadlib"
        return false
    end
    local path = core_dir .. "/" .. DLL
    native.path = path
    if not file_exists(path) then
        native.reason = "dll_missing"
        return false
    end
    local open, err = package.loadlib(path, "lec_native_open")
    if type(open) ~= "function" then
        native.reason = "loadlib_failed:" .. tostring(err)
        return false
    end
    local mark = package.loadlib(path, "lec_native_mark_career")
    local hotkey = package.loadlib(path, "lec_native_mark_hotkey")
    if type(mark) ~= "function" or type(hotkey) ~= "function" then
        native.reason = "exports_missing"
        return false
    end
    local ok, open_err = pcall(open)
    if not ok then
        native.reason = "open_failed:" .. tostring(open_err)
        return false
    end
    -- Optional (1.1.0+): the APC carrier stays off unless config.json says
    -- "native_apc": true. Turned off right after open, before any Career
    -- event could give it a thread. An older DLL has no carrier to stop.
    local no_apc = package.loadlib(path, "lec_native_no_apc")
    native.apc = false
    if type(no_apc) == "function" then
        if type(cfg) == "table" and cfg.native_apc == true then
            native.apc = true
        else
            pcall(no_apc)
        end
    end
    native._mark_career = mark
    native._mark_hotkey = hotkey
    _G.LECompanion_NativeTick = native.tick
    native.loaded = true
    native.reason = "loaded"
    return true
end

--- Called first in every Career Mode event handler (pre and post). The DLL
--- records the thread and lua_State and arms its timer on that thread.
function native.mark_career()
    local f = native._mark_career
    if f then f() end
end

--- Entry point the DLL calls every tick, on the Career event thread.
function native.tick()
    native.ticks = native.ticks + 1
    local r = native.runner
    if r and r.on_native_tick then
        return r.on_native_tick()
    end
end

--- For an LE hotkey bound to hotkey_refresh.lua: records the hotkey thread
--- for native_status.json and runs one read-only refresh.
function native.hotkey()
    native.hotkeys = native.hotkeys + 1
    local f = native._mark_hotkey
    if f then f() end
    local r = native.runner
    if r and r.tick_fast_read then
        return r.tick_fast_read()
    end
end

return native
