--[[ le_companion/runner.lua — claim-by-rename drain, poison, crash sweep.

SI-6: arm never calls pump. Writes still wait for host events / force_drain.
      Allow-listed reads may also drain on the fast-read timer (CT/LE style).
SI-8: drain is bounded by drain_budget_ms and drain_max_jobs.
]]

local util = require("util")
local json = require("json")
local version = require("version")
local status = require("status")
local ops = require("ops")

local runner = {}
runner._cfg = nil
runner._log = nil
runner.last_pump_at = 0
runner._busy = false
runner._fast_read_timer = nil
runner._career_seen = false

local FAST_READ_WAKE = "_fast_read"
local FAST_READ_MS = 200

function runner.configure(cfg, log)
    runner._cfg = cfg
    runner._log = log
end

local function emit(msg)
    if runner._log and type(runner._log.write) == "function" then
        runner._log.write(msg)
    else
        util.emit(msg)
    end
end

local function cfg()
    return runner._cfg or {}
end

local function qjoin(...)
    return util.join(cfg().queue_dir, ...)
end

local function read_job(path)
    local text, err = util.read_text(path)
    if not text then return nil, err end
    local obj, jerr = json.try_decode_object(text)
    if not obj then return nil, jerr end
    return obj
end

local function job_first_op(job_id)
    local path = status.claimed_path(job_id)
    if not path or not util.exists(path) then path = status.jobs_path(job_id) end
    local job = path and read_job(path) or nil
    if type(job) ~= "table" or type(job.ops) ~= "table" then return nil end
    local op = job.ops[1]
    return type(op) == "table" and op.op or nil
end

local function is_bootstrap_read(job_id)
    local path = status.claimed_path(job_id)
    if not path or not util.exists(path) then path = status.jobs_path(job_id) end
    local job = path and read_job(path) or nil
    if type(job) ~= "table" or type(job.ops) ~= "table" then return false end
    for i = 1, #job.ops do
        local op = job.ops[i]
        local name = type(op) == "table" and op.op or ""
        local safe = name == "diag.ping" or name == "db.dump"
            or name == "export_squad" or name == "snapshot"
            or (name == "budget" and (op.action or "get") == "get")
        if not safe then return false end
    end
    return #job.ops > 0
end

local function has_bootstrap_read()
    for _, dir in ipairs({ "claimed", "jobs" }) do
        local ids = util.list_job_ids(qjoin(dir)) or {}
        for i = 1, #ids do
            if is_bootstrap_read(ids[i]) then return true end
        end
    end
    return false
end

--- Keep a sidecar index so the next arm can crash-sweep without dir enum.
local function rewrite_dir_index(dir, include_job_id)
    local ids, source = util.list_job_ids(dir)
    if not ids and source == "no_enumeration" then
        -- Index-only hosts cannot enumerate a directory to create its first
        -- index.  A successful claim gives us the moved id, so seed the
        -- claimed index from that known fact instead of losing the job until
        -- the next Live Editor restart.
        if not include_job_id then return end
        ids = { include_job_id }
    elseif include_job_id then
        local present = false
        for i = 1, #ids do
            if ids[i] == include_job_id then present = true break end
        end
        if not present then ids[#ids + 1] = include_job_id end
    end
    local names = {}
    if ids then
        for i = 1, #ids do names[i] = ids[i] .. ".json" end
    end
    local text = json.try_encode(names)
    if text then util.write_atomic(util.join(dir, "index.json"), text) end
end

local function attempt_count(job_id)
    local path = qjoin("state", job_id .. ".state.json")
    local text = util.read_text(path)
    if not text then return 0 end
    local obj = json.try_decode_object(text)
    if not obj then return 0 end
    return tonumber(obj.attempts) or 0
end

local function write_attempts(job_id, n)
    local path = qjoin("state", job_id .. ".state.json")
    local body = {}
    local prior = util.read_text(path)
    if prior then body = json.try_decode_object(prior) or {} end
    body.schema = version.STATE_SCHEMA
    body.job_id = job_id
    body.attempts = n
    body.at = util.now()
    local text = json.try_encode(body)
    if text then util.write_atomic(path, text) end
end

local function read_state(job_id)
    local text = util.read_text(qjoin("state", job_id .. ".state.json"))
    if not text then return nil end
    return json.try_decode_object(text)
end

local function write_progress(job_id, progress, awaiting)
    local body = util.copy(progress or {})
    local previous = read_state(job_id) or {}
    body.schema = version.STATE_SCHEMA
    body.job_id = job_id
    -- A yielded step must not erase its retry count. Otherwise every resume
    -- starts again at attempt 1 and a crash loop can never reach quarantine.
    body.attempts = tonumber(body.attempts)
        or tonumber(previous.attempts)
        or 0
    body.state = awaiting and "awaiting_event" or "running"
    body.last_step_at = util.now()
    local text = json.try_encode(body)
    if text then
        return util.write_atomic(qjoin("state", job_id .. ".state.json"), text)
    end
    return false
end

local function poison(job_id, claimed_path, reason, detail)
    local dest = status.poison_path(job_id)
    if claimed_path and util.exists(claimed_path) and dest then
        local moved = util.rename(claimed_path, dest)
        -- A prior interrupted sweep can leave the poison filename occupied.
        -- The terminal audit below is still authoritative; do not leave a
        -- claimed file around for the next pump to resume.
        if not moved then util.remove(claimed_path) end
    end
    util.remove(qjoin("state", job_id .. ".state.json"))
    util.remove(qjoin("results", job_id .. ".partial.json"))
    status.write_result(job_id, {
        schema = version.RESULT_SCHEMA,
        job_id = job_id,
        state = "poisoned",
        ok = false,
        outcome = "failed",
        finished_at = util.now(),
        session_id = status.session_id(),
        core_version = version.VERSION,
        error = { phase = "poison", message = reason or "poisoned", detail = detail or "" },
        counts = {},
        ops = {},
        failures = { { reason = reason or "poisoned", detail = detail or "" } },
    })
    emit("poisoned " .. job_id .. " " .. tostring(reason))
end

--- Crash sweep: claimed jobs from a prior session are quarantined, never resumed.
function runner.crash_sweep()
    local claimed_dir = qjoin("claimed")
    local ids, source = util.list_job_ids(claimed_dir)
    if not ids then
        -- Probe-only: without an index we cannot list claimed; skip silently.
        return 0
    end
    local sid = status.session_id()
    local n = 0
    for i = 1, #ids do
        local jid = ids[i]
        local path = status.claimed_path(jid)
        local job = read_job(path)
        local claimed_by = job and tostring(job.claimed_by or "") or ""
        if claimed_by ~= "" and claimed_by ~= sid then
            poison(jid, path, "crashed_session",
                "claimed by session " .. claimed_by .. " which never completed it")
            n = n + 1
        end
    end
    if n > 0 then emit("crash_sweep quarantined " .. n .. " job(s)") end
    return n
end

local function validate_job(job)
    if type(job) ~= "table" then return false, "not_object" end
    if job.schema ~= version.CONTRACT then
        return false, "bad_schema", "schema " .. tostring(job.schema) .. " != " .. version.CONTRACT
    end
    if not util.is_ulid(job.job_id) then
        return false, "bad_job_id", tostring(job.job_id)
    end
    local ok, reason, detail = version.satisfies(job.core_min)
    if not ok then return false, reason, detail end
    if type(job.ops) ~= "table" or #job.ops == 0 then
        return false, "no_ops"
    end
    local budget = tonumber(job.budget_ms) or version.LIMITS.default_budget_ms or 200
    if budget <= 0 or budget > (version.LIMITS.max_budget_ms or 5000) then
        return false, "bad_budget", tostring(job.budget_ms)
    end
    if job.atomic and not job.dry_run then
        return false, "atomic_unavailable",
            "this core does not advertise rollback; no writes were attempted"
    end
    for i = 1, #job.ops do
        local op = job.ops[i]
        if type(op) ~= "table" then return false, "bad_op", "op is not an object" end
        if (op.op == "add_to_team" or op.op == "repair_partial_add") and #job.ops ~= 1 then
            return false, "experimental_shape",
                tostring(op.op) .. " must be the only operation in its guarded job"
        end
        local ok_op, op_reason, op_detail = version.op_supported(op.op, op.v or 1)
        if not ok_op then return false, op_reason, op_detail end
        local grant = version.OP_GRANTS[op.op]
        if grant and not ((job.grants or {})[grant]) then
            return false, "grant_denied",
                string.format("op %s requires grant %s", tostring(op.op), grant)
        end
    end
    return true
end

local function current_save_uid()
    for _, name in ipairs({ "GetSaveUID", "GetCurrentSaveUID", "GetCareerSaveUID" }) do
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            local ok, value = pcall(fn)
            if ok and value ~= nil and tostring(value) ~= "" then return tostring(value) end
        end
    end
    return nil
end

-- Live Editor often exposes C-style booleans as 0/1. Lua treats numeric 0 as
-- true, so never use normal Lua truthiness for a host readiness check.
local function host_true(value)
    return value == true or tonumber(value) == 1
end

runner._hub_ready = false

-- IsInCM is already true on the career menus. GetUserTeamID still crashes
-- there. Squad reads wait until the career hub has actually opened.
local function probe_in_cm()
    for _, name in ipairs({ "IsInCM", "IsInCareerMode" }) do
        local fn = rawget(_G, name)
        if type(fn) == "function" then
            local ok, value = pcall(fn)
            if ok then return host_true(value) end
        end
    end
    return false
end

local function career_save_ready()
    if runner._hub_ready ~= true then return false end
    return probe_in_cm()
end

function runner.career_save_ready()
    return career_save_ready()
end

function runner.current_save_uid()
    return current_save_uid()
end

_G.LEC_CAREER_SAVE_READY = runner.career_save_ready
_G.LEC_CURRENT_SAVE_UID = runner.current_save_uid

local function in_career_mode()
    return career_save_ready()
end

local function preconditions(job)
    local now = util.now()
    if tonumber(job.expires_at) and tonumber(job.expires_at) > 0
        and now >= tonumber(job.expires_at) then
        return false, "expired", "job expires_at deadline has passed"
    end
    local cm = in_career_mode()
    local requires = type(job.requires) == "table" and job.requires or {}
    if (job.require_cm ~= false or requires.career_mode == true) and not cm then
        return false, "career_required", "Career Mode save is not loaded"
    end
    if requires.core_version and not version.gte(version.VERSION, tostring(requires.core_version)) then
        return false, "core_too_old",
            string.format("requires core >= %s", tostring(requires.core_version))
    end
    local save_uid = current_save_uid()
    if requires.save_uid ~= nil then
        if not save_uid then
            return false, "save_uid_unavailable",
                "cannot verify requires.save_uid on this Live Editor build"
        end
        if tostring(requires.save_uid) ~= save_uid then
            return false, "wrong_save",
                string.format("loaded save %s does not match required save %s",
                    save_uid, tostring(requires.save_uid))
        end
    end
    return true, nil, nil, { in_career = cm, save_uid = save_uid }
end

local function execute_job(job, context)
    local t0 = util.clock()
    local budget_ms = tonumber(job.budget_ms)
        or cfg().default_budget_ms
        or version.LIMITS.default_budget_ms
        or 200
    local hard = util.deadline(budget_ms)
    local ctx = {
        session_id = status.session_id(),
        queue_dir = cfg().queue_dir,
        cfg = cfg(),
        save_uid = context and context.save_uid or nil,
        in_career = context and context.in_career or false,
        resume_state = job._resume_state,
    }
    local op_results = {}
    local failures = {}
    local counts = {
        ops_total = #job.ops,
        ops_ok = 0,
        ops_failed = 0,
        fields_written = 0,
        fields_failed = 0,
        fields_requested = 0,
        found = 0,
        missing = 0,
        targets = 0,
        growth_mirrored = 0,
        growth_failed = 0,
        side_effects = 0,
    }
    local all_ok = true
    local any_found = false
    local any_written = false
    local any_effect = false

    local snap_ok, snap = ops.capture_envelope_snapshot(job, ctx)
    if not snap_ok then
        return {
            schema = version.RESULT_SCHEMA,
            job_id = job.job_id,
            label = job.label or "",
            session_id = status.session_id(),
            core_version = version.VERSION,
            state = "rejected",
            ok = false,
            outcome = "rejected",
            started_at = util.now(),
            finished_at = util.now(),
            duration_ms = util.elapsed_ms(t0),
            steps = { total = 1, completed = 0, resumes = 0,
                attempts = attempt_count(job.job_id) + 1 },
            counts = counts,
            ops = {},
            failures = { {
                reason = snap.reason or "snapshot_failed",
                detail = snap.detail or "",
                phase = "snapshot",
            } },
            error = {
                phase = "snapshot",
                message = snap.reason or "snapshot_failed",
                detail = snap.detail or "",
            },
            env = { queue_dir = cfg().queue_dir, save_uid = ctx.save_uid or "" },
        }
    end

    for i = 1, #job.ops do
        if util.expired(hard) then
            all_ok = false
            failures[#failures + 1] = {
                reason = "budget_exhausted",
                detail = string.format("per-job budget %dms exhausted before op %d", budget_ms, i),
                phase = "budget",
            }
            break
        end
        local op = job.ops[i]
        local res = ops.dispatch(op, job, ctx)
        -- Chunked Phase 4 steps use the remaining per-job budget, but the
        -- transfer step always requests a real career event before verify.
        while op.op == "add_to_team" and res.deferred
            and not res.awaiting_event and not util.expired(hard) do
            ctx.resume_state = res.progress or ((res.data or {}).progress)
            res = ops.dispatch(op, job, ctx)
        end
        if res.deferred then
            local progress = res.progress or ((res.data or {}).progress) or {}
            local aggregate = progress.counts or res.counts or counts
            return {
                schema = version.RESULT_SCHEMA,
                job_id = job.job_id,
                label = job.label or "",
                session_id = status.session_id(),
                core_version = version.VERSION,
                state = "running",
                ok = false,
                outcome = "deferred",
                started_at = tonumber(progress.started_at) or util.now(),
                duration_ms = util.elapsed_ms(t0),
                steps = {
                    total = tonumber(progress.total_steps) or 10,
                    completed = math.max(0, (tonumber(progress.step) or 1) - 1),
                    resumes = tonumber(progress.resumes) or 0,
                    attempts = 0,
                },
                counts = aggregate,
                ops = {},
                failures = {},
                error = json.null,
                awaiting = res.awaiting_event and {
                    since = util.now(),
                    step = tonumber(progress.step) or 1,
                    reason = "post_transfer_settle",
                } or json.null,
                progress = progress,
                env = { queue_dir = cfg().queue_dir, save_uid = ctx.save_uid or "" },
            }
        end
        local op_ok = res.ok and true or false
        if not op_ok then all_ok = false end
        if op_ok then counts.ops_ok = counts.ops_ok + 1 else counts.ops_failed = counts.ops_failed + 1 end
        local c = res.counts or {}
        -- Never re-merge ops_ok / ops_failed / ops_total: those are owned by
        -- this loop. Handlers may still put them in counts (diag.ping used to);
        -- double-adding made a 1-op ping report ops_ok=2.
        for k, v in pairs(c) do
            if k ~= "ops_ok" and k ~= "ops_failed" and k ~= "ops_total"
                and type(v) == "number" and type(counts[k]) == "number" then
                counts[k] = counts[k] + v
            end
        end
        if (c.found or 0) > 0 then any_found = true end
        if (c.fields_written or 0) > 0 then any_written = true end
        if (c.side_effects or 0) > 0 then any_effect = true end
        op_results[#op_results + 1] = {
            id = op.id or ("op" .. i),
            op = op.op,
            ok = op_ok,
            counts = c,
            data = res.data or {},
            reason = res.reason,
            detail = res.detail,
        }
        if type(res.failures) == "table" then
            for _, f in ipairs(res.failures) do
                f.op = op.op
                f.phase = f.phase or "execute"
                failures[#failures + 1] = f
            end
        elseif not op_ok then
            failures[#failures + 1] = {
                op = op.op,
                reason = res.reason or "failed",
                detail = res.detail or "",
                phase = "execute",
            }
        end
        local on_error = op.on_error or "abort"
        if not op_ok and on_error == "abort" then break end
    end

    if util.expired(hard) and all_ok then
        all_ok = false
        failures[#failures + 1] = {
            reason = "budget_overrun",
            detail = string.format("job exceeded its %dms budget", budget_ms),
            phase = "budget",
        }
    end

    local state
    if all_ok then
        if not any_found and not any_written and counts.fields_requested > 0 then
            state = "no_op"
        else
            state = "done"
        end
    else
        if any_written or any_effect then state = "partial" else state = "failed" end
        -- rejected preconditions
        for _, f in ipairs(failures) do
            if f.reason == "grant_denied" or f.reason == "bad_schema"
                or f.reason == "core_too_old" or f.reason == "unknown_op" then
                state = "rejected"
                break
            end
        end
    end

    local finished = util.now()
    return {
        schema = version.RESULT_SCHEMA,
        job_id = job.job_id,
        label = job.label or "",
        session_id = status.session_id(),
        core_version = version.VERSION,
        state = state,
        ok = all_ok and true or false,
        outcome = state == "done" and "applied"
            or state == "no_op" and "no_op"
            or state == "partial" and "partial"
            or state == "rejected" and "rejected"
            or "failed",
        started_at = finished - math.floor(util.elapsed_ms(t0) / 1000),
        finished_at = finished,
        duration_ms = util.elapsed_ms(t0),
        steps = { total = 1, completed = 1, resumes = 0, attempts = attempt_count(job.job_id) + 1 },
        counts = counts,
        ops = op_results,
        failures = failures,
        error = all_ok and json.null or {
            phase = "execute",
            message = (#failures > 0 and failures[1].reason) or "failed",
            detail = (#failures > 0 and failures[1].detail) or "",
        },
        env = {
            queue_dir = cfg().queue_dir,
            save_uid = ctx.save_uid or "",
            in_career = ctx.in_career,
            snapshot = snap or json.null,
            budget_ms = budget_ms,
        },
        log = (runner._log and runner._log.capture_close and runner._log.capture_close()) or {},
    }
end

function runner.run_one(job_id, pump_reason)
    local from = status.jobs_path(job_id)
    local to = status.claimed_path(job_id)
    if not from or not to then return "bad_id" end
    local resuming = util.exists(to)

    -- Manual jobs remain pending until an explicit Force Drain. Read-only
    -- inspection before claim avoids manufacturing a terminal result.
    local pending = read_job(resuming and to or from)
    if pending and pending.deadline_kind == "manual" and pump_reason ~= "force" then
        return "deferred"
    end

    if not resuming then
        local ok_claim, creason = util.claim(from, to)
        if not ok_claim then
            return creason or "claim_failed"
        end
        rewrite_dir_index(qjoin("jobs"))
        rewrite_dir_index(qjoin("claimed"), job_id)
    end

    local job, jerr = read_job(to)
    if not job then
        poison(job_id, to, "bad_json", tostring(jerr))
        return "poisoned"
    end
    -- Stamp claim ownership
    job.claimed_by = status.session_id()
    job.claimed_at = util.now()
    local stamped = json.try_encode(job)
    if stamped then util.write_atomic(to, stamped) end
    local resumed_state = read_state(job_id)
    if resumed_state and tonumber(resumed_state.step) then
        resumed_state.resumes = (tonumber(resumed_state.resumes) or 0) + 1
        job._resume_state = resumed_state
    end

    local vok, vreason, vdetail = validate_job(job)
    if not vok then
        status.write_result(job_id, {
            schema = version.RESULT_SCHEMA,
            job_id = job_id,
            state = "rejected",
            ok = false,
            outcome = "rejected",
            finished_at = util.now(),
            session_id = status.session_id(),
            core_version = version.VERSION,
            error = { phase = "validate", message = vreason, detail = vdetail or "" },
            counts = {},
            ops = {},
            failures = { { reason = vreason, detail = vdetail or "", phase = "validate" } },
        })
        util.remove(to)
        return "rejected"
    end

    local pok, preason, pdetail, context = preconditions(job)
    if not pok then
        status.write_result(job_id, {
            schema = version.RESULT_SCHEMA,
            job_id = job_id,
            state = "rejected",
            ok = false,
            outcome = preason == "expired" and "expired" or "rejected",
            finished_at = util.now(),
            session_id = status.session_id(),
            core_version = version.VERSION,
            error = { phase = "precondition", message = preason, detail = pdetail or "" },
            counts = {},
            ops = {},
            failures = { { reason = preason, detail = pdetail or "",
                phase = "precondition" } },
        })
        util.remove(to)
        return "rejected"
    end

    if job.ops[1] and job.ops[1].op == "add_to_team" and not read_state(job_id) then
        -- Flush a resumable cursor before the first dangerous host call. A
        -- process death during step 1 therefore counts toward quarantine.
        write_progress(job_id, {
            step = 1, counts = {}, started_at = util.now(), resumes = 0,
        }, false)
    end
    -- yield_does_not_consume_attempts: Transfer settle can take many Career
    -- events. Count a fresh claim once, and count host throws, but do not
    -- burn max_attempts on a healthy deferred resume.
    local attempts = attempt_count(job_id)
    if not resuming then
        attempts = attempts + 1
        write_attempts(job_id, attempts)
    elseif attempts < 1 then
        attempts = 1
        write_attempts(job_id, attempts)
    end
    local max_attempts = tonumber(job.max_attempts) or version.LIMITS.max_attempts or 3
    if attempts > max_attempts then
        poison(job_id, to, "max_attempts",
            "step did not complete across " .. tostring(max_attempts) .. " sessions")
        return "poisoned"
    end

    if runner._log and runner._log.capture_open then runner._log.capture_open() end
    local ok, result = util.capture(function()
        return execute_job(job, context)
    end)
    if not ok then
        attempts = attempt_count(job_id) + 1
        write_attempts(job_id, attempts)
        if attempts >= max_attempts then
            poison(job_id, to, "crash_loop", result.message or "execute threw")
            return "poisoned"
        end
        -- Leave in claimed for retry / crash sweep
        status.write_result(job_id, {
            schema = version.RESULT_SCHEMA,
            job_id = job_id,
            state = "failed",
            ok = false,
            outcome = "failed",
            finished_at = util.now(),
            session_id = status.session_id(),
            core_version = version.VERSION,
            error = util.err_table("execute", result),
            counts = {},
            ops = {},
            failures = { { reason = "execute_threw", detail = result.message or "" } },
        })
        util.remove(to)
        return "failed"
    end

    if result.outcome == "deferred" or result.state == "running" then
        write_progress(job_id, result.progress, result.awaiting ~= json.null)
        status.write_partial(job_id, result)
        emit("job " .. job_id .. " yielded at step "
            .. tostring(((result.progress or {}).step) or "?"))
        return "deferred"
    end
    status.write_result(job_id, result)
    util.remove(to)
    util.remove(qjoin("state", job_id .. ".state.json"))
    util.remove(qjoin("results", job_id .. ".partial.json"))
    rewrite_dir_index(qjoin("claimed"))
    emit("job " .. job_id .. " -> " .. tostring(result.state) .. " ok=" .. tostring(result.ok))
    return result.state
end

local function pump_inner(reason, deadline, read_only_only)
    local t0 = util.clock()
    local seen, done, failed, deferred = 0, 0, 0, 0
    local budget_ms = version.LIMITS.drain_budget_ms or 120
    local max_jobs = version.LIMITS.drain_max_jobs or 8
    if reason == "force" then
        budget_ms = version.LIMITS.max_budget_ms or 5000
        max_jobs = 32
    end
    local hard = deadline or util.deadline(budget_ms)

    local jobs_dir = qjoin("jobs")
    local ids, source = util.list_job_ids(jobs_dir)
    if not ids then
        -- No enumeration: still stamp drain so Python knows we ticked.
        status.write_drain({
            reason = reason or "pump",
            jobs_seen = 0,
            jobs_done = 0,
            duration_ms = util.elapsed_ms(t0),
            note = "no_enumeration:" .. tostring(source),
        })
        runner.last_pump_at = util.now()
        return "no_enumeration"
    end

    -- Resume claimed chunked jobs before claiming new work.
    local claimed = util.list_job_ids(qjoin("claimed")) or {}
    local queue = {}
    local present = {}
    for i = 1, #claimed do
        queue[#queue + 1] = claimed[i]
        present[claimed[i]] = true
    end
    for i = 1, #ids do
        if not present[ids[i]] then queue[#queue + 1] = ids[i] end
    end

    for i = 1, #queue do
        if util.expired(hard) or seen >= max_jobs then
            deferred = #queue - seen
            break
        end
        if read_only_only and not is_bootstrap_read(queue[i]) then
            deferred = deferred + 1
        else
            seen = seen + 1
            local outcome = runner.run_one(queue[i], reason)
            if outcome == "done" or outcome == "no_op" or outcome == "partial"
                or outcome == "rejected" or outcome == "failed" or outcome == "poisoned" then
                if outcome == "done" or outcome == "no_op" then
                    done = done + 1
                else
                    failed = failed + 1
                end
            elseif outcome == "deferred" then
                deferred = deferred + 1
                -- sign_transfer_busy: do not start another Add Player while
                -- TransferPlayer is still settling. Overlapping pick_dummy used
                -- to false-fail the next dummy as dummy_not_free (-1 sentinel).
                if job_first_op(queue[i]) == "add_to_team" then
                    deferred = deferred + (#queue - i)
                    break
                end
            elseif outcome == "vanished" or outcome == "already_claimed" then
                -- skip
            else
                failed = failed + 1
            end
        end
    end

    status.write_drain({
        reason = reason or "pump",
        jobs_seen = seen,
        jobs_done = done,
        jobs_failed = failed,
        jobs_deferred = deferred,
        duration_ms = util.elapsed_ms(t0),
        enumerate = source,
    })
    runner.last_pump_at = util.now()
    return "ok"
end

function runner.pump(reason, deadline, read_only_only)
    if runner._busy then return "busy" end
    if not cfg().queue_dir or cfg().queue_dir == "" then return "no_queue" end
    runner._busy = true
    local ok, result = util.capture(function()
        return pump_inner(reason, deadline, read_only_only)
    end)
    -- A malformed job or unexpected host error must never wedge every future
    -- event behind a permanent "busy" state.
    runner._busy = false
    if not ok then
        emit("pump failed: " .. tostring(result.message or result))
        status.write_drain({
            reason = reason or "pump",
            jobs_seen = 0,
            jobs_done = 0,
            jobs_failed = 1,
            duration_ms = 0,
            note = "pump_threw",
        })
        return "failed"
    end
    return result
end

function runner.force_drain()
    return runner.pump("force", nil)
end

function runner.on_init_done()
    -- File markers only. Do not start the read timer or touch the database:
    -- LEInitDone fires on the main menu, before Career Mode.
    status.write_session({
        phase = "le_init_done",
        capabilities = { in_career = false, save_ready = false, hub_ready = false },
    })
    runner.crash_sweep()
end

function runner.on_career(event_id, phase)
    local events = require("events")
    local post = (phase == "post" or phase == nil)
    if post and events.is_hub(event_id) and probe_in_cm() then
        runner._hub_ready = true
    end
    if not probe_in_cm() then
        runner._hub_ready = false
    end
    local ready = career_save_ready()
    runner._career_seen = ready
    _G.LEC_CAREER_SEEN = ready
    pcall(function()
        status.write_session({
            phase = ready and "career_hub" or "menu",
            capabilities = { in_career = ready, save_ready = ready, hub_ready = ready },
        })
    end)
    if not ready then return end
    local kind = events.classify(event_id)
    if kind == "blackout" then
        -- Do not pump during this callback. Do not leave a sticky flag:
        -- CE Lua is single-threaded, so a timer cannot interrupt us here.
        emit("blackout " .. events.name(event_id) .. " (" .. tostring(phase) .. ")")
        return
    end
    local cache = events.cache_action(event_id)
    if cache == "hard_reset" or cache == "invalidate_players" or cache == "invalidate_squads" then
        pcall(function() require("db").reset() end)
    end
    -- Never touch the Career DB from a pre-event.  A settled front-end event
    -- may run a normal job, while *any other non-blackout post-event* can only
    -- release an explicitly allow-listed read.  The latter is important: a
    -- player snapshot must not remain stranded just because the user is on a
    -- Career hub which does not emit one of the three screen-navigation IDs.
    if phase == "post" or phase == nil then
        pcall(function() runner.start_fast_read_timer() end)
        local deadline = util.deadline(version.LIMITS.drain_budget_ms or 120)
        if kind == "pump" or kind == "hard_reset" or kind == "invalidate_squads" then
            runner.pump("event:" .. events.name(event_id), deadline)
        elseif has_bootstrap_read() then
            runner.pump("safe_read:" .. events.name(event_id), deadline, true)
        end
    end
end

function runner.tick_fast_read()
    -- Cheat Engine / Live Editor stay instant because they ReadProcessMemory
    -- (or T3DB get) on a timer, not after a hub click. Companion already uses
    -- the same T3DB path in snapshot; this tick only removes the wait.
    -- pump(..., true) is read_only_only — writes never run here.
    if not in_career_mode() then return "idle" end
    if runner._busy then return "busy" end
    local wake = util.exists(qjoin(FAST_READ_WAKE))
    if not wake and not has_bootstrap_read() then return "idle" end
    local deadline = util.deadline(version.LIMITS.drain_budget_ms or 120)
    local result = runner.pump("fast_read", deadline, true)
    if not has_bootstrap_read() then
        util.remove(qjoin(FAST_READ_WAKE))
    end
    return result
end

local function timer_is_live(timer)
    if timer == nil or timer == true then return false end
    local ok, enabled = pcall(function() return timer.Enabled end)
    return ok and (enabled == true or enabled == 1)
end

local function remember_timer(timer)
    runner._fast_read_timer = timer
    _G.LEC_FAST_READ_TIMER = timer
end

local function advertise_timer(live)
    -- Persist so a later write_session() does not advertise a dead timer
    -- (or wipe a live one) just because version.lua defaults to false.
    version.CAPABILITIES.fast_read_timer = live and true or false
    if not cfg().queue_dir or cfg().queue_dir == "" then return end
    pcall(function()
        status.write_session({
            phase = live and "fast_read_timer" or "fast_read_timer_missing",
            capabilities = { fast_read_timer = live and true or false },
        })
    end)
end

function runner.start_fast_read_timer()
    if timer_is_live(runner._fast_read_timer) then return true end
    if runner._fast_read_timer then
        pcall(function() runner._fast_read_timer.Enabled = false end)
        runner._fast_read_timer = nil
        _G.LEC_FAST_READ_TIMER = nil
    end
    local function on_tick()
        util.pcall_named("fast_read", runner.tick_fast_read)
    end
    local function arm_timer(timer)
        if timer == nil then return false end
        local ok = pcall(function()
            timer.Interval = FAST_READ_MS
            timer.OnTimer = on_tick
            timer.Enabled = true
        end)
        if not ok or not timer_is_live(timer) then return false end
        remember_timer(timer)
        return true
    end
    local create = rawget(_G, "createTimer")
    if type(create) == "function" then
        -- CE: createTimer(nil, false) then Interval / OnTimer / Enabled.
        local attempts = {
            function() return create(nil, false) end,
            function() return create() end,
        }
        local main = rawget(_G, "MainForm")
        if main ~= nil then
            attempts[#attempts + 1] = function() return create(main, false) end
            attempts[#attempts + 1] = function() return create(main) end
        end
        local get_form = rawget(_G, "getMainForm")
        if type(get_form) == "function" then
            local ok_form, form = pcall(get_form)
            if ok_form and form ~= nil then
                attempts[#attempts + 1] = function() return create(form, false) end
                attempts[#attempts + 1] = function() return create(form) end
            end
        end
        for i = 1, #attempts do
            local ok, timer = pcall(attempts[i])
            if ok and arm_timer(timer) then
                emit("fast_read timer armed (createTimer)")
                advertise_timer(true)
                return true
            end
        end
    end
    local set_timer = rawget(_G, "setTimer")
    if type(set_timer) == "function" then
        local ok, handle = pcall(set_timer, on_tick, FAST_READ_MS)
        if ok and handle ~= nil then
            remember_timer(handle)
            emit("fast_read timer armed (setTimer)")
            advertise_timer(true)
            return true
        end
    end
    emit("fast_read timer unavailable — snapshot waits for Career events")
    advertise_timer(false)
    return false
end

return runner
