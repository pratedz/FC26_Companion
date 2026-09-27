--[[ LE Companion queue worker — pure Lua file I/O only (no shell).

  Protocol (v2):
    * Companion writes: queue/<job>.lua, _run_now.lua, _pending.txt, _wake.txt
    * Worker drains on career events + re-Execute (ForceDrain)
    * Never drain during LE Load auto-arm (SAFE_ARM) — freezes launch
    * Always run _run_now when present (latest apply wins)
    * Result is FAIL if any job pcall/load fails (ok < processed)

  Companion never injects; LE owns the game process.
]]

-- INSTALL replaces this single line (do not add a second QUEUE_DIR):
local QUEUE_DIR = "LE_Profile_Executor/queue"

local function path_join(a, b)
    if not a or a == "" then return b end
    local last = string.sub(a, -1)
    if last == "/" or last == "\\" then
        return a .. b
    end
    return a .. "/" .. b
end

local function qdir()
    return QUEUE_DIR
end

local function read_file(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local data = f:read("*a")
    f:close()
    return data
end

local function write_file(path, data)
    local f = io.open(path, "wb")
    if not f then return false end
    f:write(data or "")
    f:close()
    return true
end

local function delete_file(path)
    pcall(function() os.remove(path) end)
end

local function file_exists(path)
    local f = io.open(path, "rb")
    if f then f:close() return true end
    return false
end

local function read_pending(q)
    local data = read_file(path_join(q, "_pending.txt"))
    if not data or data == "" then return {} end
    local list, seen = {}, {}
    for line in string.gmatch(data, "[^\r\n]+") do
        line = (line:match("^%s*(.-)%s*$") or line)
        if line ~= "" and not line:match("^_") and line:lower():match("%.lua$") and not seen[line] then
            seen[line] = true
            table.insert(list, line)
        end
    end
    return list
end

local function clear_pending(q)
    write_file(path_join(q, "_pending.txt"), "")
end

-- Lua error text used to die in LE's in-game log. Collapse it to a single
-- key=value-safe token and hand it back so the companion can show it.
local ERR_MAX = 200
local function collapse_err(e)
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
    if #s > ERR_MAX then
        s = string.sub(s, 1, ERR_MAX)
    end
    return s
end

-- Returns ok, err_text ("" when ok). Never throws.
local function run_source(src, label)
    if not src or src == "" then return false, "empty_source" end
    local chunk, err = load(src, "@" .. tostring(label or "job"))
    if not chunk then
        local msg = collapse_err(err)
        if Log then Log("[LE_Companion] load: " .. msg) end
        return false, "load:" .. msg
    end
    local ok, err2 = pcall(chunk)
    if not ok then
        local msg = collapse_err(err2)
        if Log then Log("[LE_Companion] run: " .. msg) end
        return false, "run:" .. msg
    end
    return true, ""
end

local function finish_job(q, full, name)
    local dest = path_join(path_join(q, "done"), name)
    if file_exists(dest) then
        dest = path_join(path_join(q, "done"), tostring(os.time()) .. "_" .. name)
    end
    local data = read_file(full)
    if data then
        write_file(dest, data)
    end
    delete_file(full)
end

local function heartbeat(q, note)
    local msg = "alive " .. tostring(os.time()) .. " " .. tostring(note or "") .. "\n"
    write_file(path_join(q, "_bridge_alive.txt"), msg)
    write_file(path_join(q, "_bridge_armed.txt"), "armed " .. tostring(os.time()) .. "\n")
end

local function write_result(q, text)
    write_file(path_join(q, "_last_result.txt"), tostring(text or "") .. "\n")
end

-- The companion deletes _job_status.txt before it queues a job, so an absent
-- file means the job died before it could report. Fill that hole with the real
-- Lua error instead of letting the run look clean. Never clobber a status the
-- job itself managed to write.
local function write_job_error_status(q, name, err_text)
    local status = path_join(q, "_job_status.txt")
    if file_exists(status) then return false end
    return write_file(status, string.format(
        "job=bridge ok=false found=false written=0 failed=1 file=%s reason=lua_error msg=%s\n",
        tostring(name or "?"), tostring(err_text or "unknown_error")
    ))
end

local S = _G.__LE_COMPANION_BRIDGE_STATE
if type(S) ~= "table" then
    S = { busy = false, last_run = 0, handlers = false }
    _G.__LE_COMPANION_BRIDGE_STATE = S
end

-- Turbo drain: short throttle. Busy watchdog must cover long jobs (add_to_team).
local MIN_INTERVAL = 0.08
local BUSY_WATCHDOG = 120.0

local function set_busy_flag(q, busy, note)
    if busy then
        write_file(
            path_join(q, "_bridge_busy.txt"),
            "busy 1 t=" .. tostring(os.time()) .. " " .. tostring(note or "") .. "\n"
        )
    else
        delete_file(path_join(q, "_bridge_busy.txt"))
    end
end

local function process_queue(force)
    -- Busy watchdog: only recover after long hang (CreatePlayer can take >5s)
    if S.busy then
        local now = os.clock()
        if (now - (S.last_run or 0)) > BUSY_WATCHDOG then
            if Log then Log("[LE_Companion] busy watchdog reset") end
            S.busy = false
            pcall(function() set_busy_flag(qdir(), false) end)
        else
            pcall(function()
                heartbeat(qdir(), "busy")
                set_busy_flag(qdir(), true, "in_progress")
            end)
            return 0, 0, "busy"
        end
    end
    local now = os.clock()
    if not force and (now - (S.last_run or 0)) < MIN_INTERVAL then
        return 0, 0, "throttle"
    end
    S.busy = true
    S.last_run = now

    local n, ok_n = 0, 0
    local last_name = ""
    local err_note = ""
    local last_err = ""
    local run_now_ran = false

    local ok_outer, err_outer = pcall(function()
        local q = qdir()
        set_busy_flag(q, true, "drain")
        if not write_file(path_join(q, "_bridge_alive.txt"), "alive " .. tostring(os.time()) .. " drain\n") then
            err_note = "cannot_write_queue"
            return
        end
        write_file(path_join(q, "_bridge_armed.txt"), "armed " .. tostring(os.time()) .. "\n")

        -- 1) Always run single-slot _run_now FIRST (latest apply wins)
        local fb = path_join(q, "_run_now.lua")
        local run_now_body = nil
        if file_exists(fb) then
            run_now_body = read_file(fb)
            -- Heartbeat before long job so companion sees LIVE+busy, not timeout
            heartbeat(q, "run=_run_now")
            local ok, job_err = run_source(run_now_body, "_run_now.lua")
            finish_job(q, fb, "_run_now.lua")
            n = n + 1
            last_name = "_run_now.lua"
            run_now_ran = true
            if ok then
                ok_n = ok_n + 1
            else
                last_err = job_err or "unknown_error"
                pcall(function() write_job_error_status(q, "_run_now.lua", last_err) end)
            end
            if Log then
                Log("[LE_Companion] " .. (ok and "OK" or "FAIL") .. " _run_now.lua")
            end
            heartbeat(q, "done=_run_now ok=" .. tostring(ok))
        end

        -- 2) Merge pending + wake names (force_drain line is ignored as a name)
        local pending = read_pending(q)
        local wake = read_file(path_join(q, "_wake.txt"))
        if wake then
            for line in string.gmatch(wake, "[^\r\n]+") do
                if line:match("force_drain") then
                    -- companion requested hard drain (already force or ProcessQueue)
                else
                    local wname = line:match("wake%s+(%S+)") or line:match("^%s*(%S+%.lua)%s*$")
                    if wname and not wname:match("^_") then
                        local seen = false
                        for _, p in ipairs(pending) do
                            if p == wname then seen = true break end
                        end
                        if not seen then table.insert(pending, wname) end
                    end
                end
            end
        end

        local stuck = {}
        for _, name in ipairs(pending) do
            local full = path_join(q, name)
            if file_exists(full) then
                local body = read_file(full)
                -- Skip re-run if body matches _run_now we already executed
                if run_now_ran and run_now_body and body == run_now_body then
                    finish_job(q, full, name)
                    if Log then Log("[LE_Companion] skip-dup " .. name) end
                else
                    heartbeat(q, "run=" .. tostring(name))
                    local ok, job_err = run_source(body, name)
                    finish_job(q, full, name)
                    n = n + 1
                    last_name = name
                    if ok then
                        ok_n = ok_n + 1
                        if Log then Log("[LE_Companion] OK " .. name) end
                    else
                        last_err = job_err or "unknown_error"
                        pcall(function() write_job_error_status(q, name, last_err) end)
                        if Log then Log("[LE_Companion] FAIL " .. name .. " err=" .. last_err) end
                    end
                    heartbeat(q, "done=" .. tostring(name) .. " ok=" .. tostring(ok))
                end
                if file_exists(full) then
                    table.insert(stuck, name)
                end
            else
                if Log then Log("[LE_Companion] miss " .. tostring(name)) end
            end
        end

        if #stuck > 0 then
            write_file(path_join(q, "_pending.txt"), table.concat(stuck, "\n") .. "\n")
        else
            clear_pending(q)
        end
        delete_file(path_join(q, "_wake.txt"))
        heartbeat(q, "n=" .. tostring(n) .. " ok=" .. tostring(ok_n))

        -- Honest result: any load/run failure → FAIL (+ the real Lua error)
        if n > 0 then
            if ok_n < n then
                write_result(q, string.format(
                    "FAIL processed=%d ok=%d last=%s err=%s",
                    n, ok_n, last_name, (last_err ~= "" and last_err or "unknown_error")
                ))
            else
                write_result(q, string.format(
                    "OK processed=%d ok=%d last=%s", n, ok_n, last_name
                ))
            end
        elseif #stuck > 0 then
            write_result(q, "FAIL stuck jobs still on disk")
        else
            write_result(q, "OK idle queue_empty")
        end
    end)

    if not ok_outer then
        err_note = collapse_err(err_outer)
        pcall(function()
            write_result(qdir(), "FAIL drain err=" .. err_note)
            write_job_error_status(qdir(), "drain", err_note)
            heartbeat(qdir(), "error")
        end)
        if Log then Log("[LE_Companion] process error: " .. err_note) end
    elseif err_note == "cannot_write_queue" then
        if Log then Log("[LE_Companion] cannot write QUEUE_DIR=" .. tostring(qdir())) end
        pcall(function()
            write_result(qdir(), "FAIL cannot_write_queue path=" .. tostring(qdir()))
        end)
    end

    S.busy = false
    pcall(function() set_busy_flag(qdir(), false) end)
    if err_note == "" and last_err ~= "" then
        err_note = last_err
    end
    return n, ok_n, err_note
end

function LEProfileBridge_ProcessQueue()
    -- If companion wrote force_drain wake, skip throttle
    local wake = read_file(path_join(qdir(), "_wake.txt"))
    local force = wake and wake:find("force_drain") ~= nil
    return process_queue(force and true or false)
end

function LEProfileBridge_ForceDrain()
    return process_queue(true)
end

_G.LECompanion_ApplyQueue = LEProfileBridge_ProcessQueue
_G.LECompanion_ForceDrain = LEProfileBridge_ForceDrain

local function ensure_handlers()
    if S.handlers then return end
    if not AddEventHandler then return end
    pcall(function()
        require "imports/career_mode/enums"
    end)
    -- Never drain while the game is serialising or loading a save. The real
    -- handler signature is (events_manager, event_id, event); the old handler
    -- took no arguments and therefore drained on ALL 276 message types,
    -- including PREPARE_FOR_SAVE — writing to the player DB while the save is
    -- being written is a plausible cause of the historic freezes, and is
    -- exactly the window where a partial write would land in the save file.
    -- Resolved by name so the set survives id shifts between LE versions.
    local BLACKOUT = {}
    do
        local unsafe = {
            "ENUM_CM_EVENT_MSG_PREPARE_FOR_SAVE",
            "ENUM_CM_EVENT_MSG_PREPARE_FOR_FIRST_SAVE",
            "ENUM_CM_EVENT_MSG_POST_LOAD_PREPARE",
            "ENUM_CM_EVENT_MSG_ABOUT_TO_INIT_MODE",
            "ENUM_CM_EVENT_MSG_DATA_READY",
            "ENUM_CM_EVENT_MSG_SEASON_RESET",
        }
        for _, key in ipairs(unsafe) do
            local v = rawget(_G, key)
            if type(v) == "number" then BLACKOUT[v] = key end
        end
        -- Fallback if the enums module failed to load (ids for LE v26.3.5).
        if next(BLACKOUT) == nil then
            BLACKOUT[5] = "ABOUT_TO_INIT_MODE"
            BLACKOUT[23] = "SEASON_RESET"
            BLACKOUT[27] = "DATA_READY"
            BLACKOUT[28] = "PREPARE_FOR_SAVE"
            BLACKOUT[29] = "POST_LOAD_PREPARE"
            BLACKOUT[30] = "PREPARE_FOR_FIRST_SAVE"
        end
    end

    local function on_cm(_mgr, event_id)
        local blocked = type(event_id) == "number" and BLACKOUT[event_id]
        if blocked then
            if Log then
                Log("[LE_Companion] drain skipped during " .. tostring(blocked))
            end
            return
        end
        pcall(LEProfileBridge_ProcessQueue)
    end
    -- FCLiveEditor.DLL fires exactly four event names. AddEventHandler accepts
    -- any string without validating it, so the four invented names this list
    -- used to carry (postCareerModeLoadedFromSave, eventCareerModeHubEntered,
    -- postMatch, eventPostMatch) registered silently and never once dispatched.
    local names = {
        "post__CareerModeEvent",
        "pre__CareerModeEvent",
    }
    for _, ev in ipairs(names) do
        pcall(function()
            AddEventHandler(ev, on_cm)
        end)
    end
    -- Arm as soon as LE finishes initialising. Handlers only — draining here is
    -- what froze LE when auto-arm was tried via a live_editor.lua patch.
    pcall(function()
        AddEventHandler("post__LEInitDoneEvent", function()
            pcall(function() heartbeat(qdir(), "init_done") end)
        end)
    end)
    S.handlers = true
end

-- Re-Execute path
if _G.__LE_COMPANION_BRIDGE_LOADED then
    ensure_handlers()
    local p, o, note = LEProfileBridge_ForceDrain()
    if Log then
        Log(string.format(
            "[LE_Companion] re-Execute drain queue=%s processed=%s ok=%s note=%s",
            tostring(qdir()), tostring(p), tostring(o), tostring(note)
        ))
    end
    return
end

_G.__LE_COMPANION_BRIDGE_LOADED = true
ensure_handlers()

-- SAFE auto-arm during LIVE_EDITOR:Load — heartbeat + handlers only.
-- Full ForceDrain mid-init freezes LE (queued jobs ran during Load).
if _G.__LE_COMPANION_SAFE_ARM then
    local q = qdir()
    pcall(function()
        heartbeat(q, "safe_arm")
        write_result(q, "OK safe_arm handlers_only")
    end)
    if Log then
        Log("[LE_Companion] SAFE auto-arm ON (handlers only · no drain at Load) queue=" .. tostring(qdir()))
    end
    return
end

-- Manual Execute / re-arm in a running session: full drain
local p, o, note = LEProfileBridge_ForceDrain()
if Log then
    Log(string.format(
        "[LE_Companion] worker ON queue=%s processed=%s ok=%s note=%s",
        tostring(qdir()), tostring(p), tostring(o), tostring(note)
    ))
end
