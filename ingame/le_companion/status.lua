--[[ le_companion/status.lua — session.json, drain.json, result writers. ]]

local util = require("util")
local json = require("json")
local version = require("version")

local status = {}
status._cfg = nil
status._session_id = nil
status._log = nil

function status.configure(cfg, log)
    status._cfg = cfg
    status._log = log
end

local function qpath(...)
    local cfg = status._cfg or {}
    return util.join(cfg.queue_dir, ...)
end

local function emit(msg)
    if status._log and type(status._log.write) == "function" then
        status._log.write(msg)
    else
        util.emit(msg)
    end
end

function status.session_id()
    if status._session_id then return status._session_id end
    local seed = string.format("%d%.3f", util.now(), util.clock() * 1000)
    local h = 5381
    for i = 1, #seed do
        h = ((h * 33) + string.byte(seed, i)) % 4294967296
    end
    status._session_id = string.format("%08x", h)
    return status._session_id
end

local function host_pid()
    local candidates = {
        "GetCurrentProcessId",
        "GetLEProcessId",
    }
    for i = 1, #candidates do
        local fn = rawget(_G, candidates[i])
        if type(fn) == "function" then
            local ok, v = pcall(fn)
            if ok and type(v) == "number" and v > 0 then return math.floor(v) end
        end
    end
    -- Optional LE object
    local le = rawget(_G, "LE")
    if type(le) == "table" and type(le.GetProcessId) == "function" then
        local ok, v = pcall(le.GetProcessId, le)
        if ok and type(v) == "number" and v > 0 then return math.floor(v) end
    end
    return 0
end

--- ARM marker. Written at arm time; no drain. Pid-checked by Python.
function status.write_session(extra)
    local cfg = status._cfg or {}
    if not cfg.queue_dir or cfg.queue_dir == "" then
        return false, "no_queue_dir"
    end
    local caps = {}
    for k, v in pairs(version.CAPABILITIES or {}) do
        caps[k] = v and true or false
    end
    caps.add_to_team = cfg.core_ops and cfg.core_ops.add_to_team == true or false
    -- Reflect whether we can see the jobs directory.
    local enum = util.enumeration_source(util.join(cfg.queue_dir, "jobs"))
    caps.enumerate = enum
    local body = {
        schema = version.SESSION_SCHEMA,
        session_id = status.session_id(),
        core_version = version.VERSION,
        contract = version.CONTRACT,
        armed_at = util.now(),
        le_pid = host_pid(),
        queue_dir = util.normalize(cfg.queue_dir),
        capabilities = caps,
        limits = version.LIMITS,
        ops = version.OP_VERSIONS,
        lua = tostring(_VERSION),
    }
    if type(extra) == "table" then
        local extra_caps = extra.capabilities
        for k, v in pairs(extra) do
            if k ~= "capabilities" then body[k] = v end
        end
        if type(extra_caps) == "table" then
            for k, v in pairs(extra_caps) do caps[k] = v end
            body.capabilities = caps
        end
    end
    -- A CareerModeEvent is not proof the save is loaded. Trust the live
    -- IsInCM probe, and do not let an older flag keep reads enabled on the menu.
    local save_ready = false
    local probe = rawget(_G, "LEC_CAREER_SAVE_READY")
    if type(probe) == "function" then
        local ok, value = pcall(probe)
        if ok and value == true then save_ready = true end
    end
    caps.in_career = save_ready
    caps.save_ready = save_ready
    caps.hub_ready = save_ready
    body.capabilities = caps
    -- Same-process career saves keep one Lua session_id. Publish the host
    -- save id when the hub is actually ready so Companion can see a save
    -- switch without inventing a new Live Editor API. The lookup is the
    -- existing GetSaveUID / GetCurrentSaveUID / GetCareerSaveUID probe.
    if save_ready then
        local uid_fn = rawget(_G, "LEC_CURRENT_SAVE_UID")
        if type(uid_fn) == "function" then
            local uid_ok, uid = pcall(uid_fn)
            if uid_ok and uid ~= nil and tostring(uid) ~= "" then
                body.save_uid = tostring(uid)
            end
        end
    end
    local text, err = json.try_encode(body)
    if not text then return false, err end
    local ok, reason = util.write_atomic(qpath("session.json"), text)
    if not ok then
        emit("write_session failed: " .. tostring(reason))
        return false, reason
    end
    return true
end

--- TICK marker. Written after every drain attempt (even empty).
function status.write_drain(info)
    local cfg = status._cfg or {}
    if not cfg.queue_dir or cfg.queue_dir == "" then
        return false, "no_queue_dir"
    end
    info = info or {}
    local body = {
        schema = version.DRAIN_SCHEMA,
        at = util.now(),
        session_id = status.session_id(),
        reason = info.reason or "pump",
        jobs_seen = info.jobs_seen or 0,
        jobs_done = info.jobs_done or 0,
        jobs_deferred = info.jobs_deferred or 0,
        jobs_failed = info.jobs_failed or 0,
        duration_ms = info.duration_ms or 0,
    }
    local text, err = json.try_encode(body)
    if not text then return false, err end
    return util.write_atomic(qpath("drain.json"), text)
end

function status.write_result(job_id, result)
    local cfg = status._cfg or {}
    if not cfg.queue_dir or cfg.queue_dir == "" then
        return false, "no_queue_dir"
    end
    local name, reason = util.job_filename(job_id)
    if not name then return false, reason end
    local path = util.join(cfg.queue_dir, "results", name)
    local text, err = json.try_encode(result)
    if not text then return false, err end
    return util.write_atomic(path, text)
end

function status.write_partial(job_id, result)
    local cfg = status._cfg or {}
    if not cfg.queue_dir or cfg.queue_dir == "" then
        return false, "no_queue_dir"
    end
    if not util.is_ulid(job_id) then return false, "not_ulid" end
    local path = util.join(cfg.queue_dir, "results", job_id .. ".partial.json")
    local text, err = json.try_encode(result)
    if not text then return false, err end
    return util.write_atomic(path, text)
end

function status.result_path(job_id)
    local name = util.job_filename(job_id)
    if not name then return nil end
    return qpath("results", name)
end

function status.claimed_path(job_id)
    local name = util.job_filename(job_id)
    if not name then return nil end
    return qpath("claimed", name)
end

function status.jobs_path(job_id)
    local name = util.job_filename(job_id)
    if not name then return nil end
    return qpath("jobs", name)
end

function status.poison_path(job_id)
    local name = util.job_filename(job_id)
    if not name then return nil end
    return qpath("poison", name)
end

return status
