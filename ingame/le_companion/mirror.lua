--[[ le_companion/mirror.lua — live squad mirror (<queue>/mirror/squad.json).

The app used to see squad changes only after Refresh, an export_squad job
that can block the game for 2-5 s. The mirror keeps a copy of the user's
senior squad on disk while the Career hub is settled, so the app can follow
it without queuing anything.

* Change detection first. A fingerprint from cheap host calls (save uid,
  user team id, sorted senior-squad ids) is compared at most every
  LIMITS.mirror_check_ms. A dirty flag forces a rebuild: a write job
  finished, a Career event other than a hub event fired (simulation, day
  advance, save), or LIMITS.mirror_verify_s passed since the last build.
* The rebuild reuses export_squad's phases (ops.squad_export_begin/step) in
  slices of LIMITS.mirror_tick_ms of util.clock per call. It starts over when
  the hub stops being ready, a job runs (jobs walk the same native table
  cursors) or another reader moved a cursor it was walking.
* squad.json is written atomically (tmp + rename) and only when the content
  hash changed. The content includes the ids of the write jobs that finished
  before the build started, so every write job yields one confirming file.

runner.tick_mirror() owns the readiness gates. Nothing here does host calls
or file IO outside mirror.tick(); on_unsettled/mark_dirty/note_write only
touch Lua state, so they are safe inside simulation callbacks.
]]

local util = require("util")
local json = require("json")
local version = require("version")
local ops = require("ops")
local status = require("status")

local mirror = {}
mirror.SCHEMA = 1
mirror.DIR = "mirror"
mirror.FILE = "squad.json"

-- Code defaults. version.LIMITS entries of the same name override them.
mirror.DEFAULTS = {
    mirror_tick_ms = 25,         -- util.clock budget for one tick
    mirror_check_ms = 1000,      -- fingerprint probe interval when nothing is dirty
    mirror_verify_s = 120,       -- rebuild at least this often on a settled hub (0 = never)
    mirror_retry_s = 30,         -- back-off after an unreadable squad or a failed write
    mirror_writes_kept = 16,     -- write job ids carried in squad.json
    mirror_max_restarts = 3,     -- incomplete walks retried before accepting the result
}

function mirror.limit(name)
    local v = tonumber((version.LIMITS or {})[name])
    if v ~= nil and v >= 0 then return v end
    return mirror.DEFAULTS[name]
end
local limit = mirror.limit

mirror._cfg = nil
mirror._log = nil
mirror._runner = nil
mirror._dirty = true          -- nothing written yet this session
mirror._dirty_gen = 0
mirror._build = nil
mirror._fp = nil              -- fingerprint of the last completed build
mirror._hash = nil            -- content hash of the last written file
mirror._seq = 0
mirror._writes = {}           -- recent write job ids, oldest first
mirror._last_check = nil
mirror._built_at = nil        -- util.clock of the last completed build
mirror._retry_at = nil
mirror._pending = nil         -- encoded file waiting for a write retry
mirror._restarts = 0
mirror.last = "idle"
mirror.stats = {
    ticks = 0, checks = 0, builds = 0, writes = 0,
    unchanged = 0, abandoned = 0, write_failed = 0,
}

function mirror.configure(cfg, log, runner)
    mirror._cfg = cfg
    mirror._log = log
    mirror._runner = runner
end

local function emit(msg)
    local log = mirror._log
    if log and type(log.write) == "function" then
        log.write("mirror: " .. msg)
    else
        util.emit("mirror: " .. msg)
    end
end

local function cfg()
    return mirror._cfg or {}
end

--- Drop an in-progress build; the rebuild stays owed. Lua state only.
function mirror.abandon(reason)
    if mirror._build ~= nil then
        mirror._build = nil
        mirror._dirty = true
        mirror.stats.abandoned = mirror.stats.abandoned + 1
        mirror.last = "abandoned:" .. tostring(reason or "")
    end
end

--- A tick threw: drop the build and wait LIMITS.mirror_retry_s before the
--- next attempt, so a persistent error cannot run on every tick.
function mirror.fail(reason)
    mirror.abandon(reason)
    mirror._dirty = true   -- also when the throw came before a build existed
    mirror._retry_at = util.clock() + limit("mirror_retry_s")
    mirror.last = "failed:" .. tostring(reason or "")
end

--- Something may have changed the squad: rebuild on the next settled tick.
function mirror.mark_dirty(reason)
    mirror._dirty = true
    mirror._dirty_gen = mirror._dirty_gen + 1
    mirror._retry_at = nil
    mirror._dirty_reason = reason
end

--- A write job finished (any outcome). Its id goes into the next file so the
--- app can tell a post-write mirror from an older one.
function mirror.note_write(job_id)
    if type(job_id) == "string" and job_id ~= "" then
        local w = mirror._writes
        for i = #w, 1, -1 do
            if w[i] == job_id then table.remove(w, i) end
        end
        w[#w + 1] = job_id
        local keep = math.max(1, math.floor(limit("mirror_writes_kept")))
        while #w > keep do table.remove(w, 1) end
    end
    mirror.mark_dirty("write")
end

--- A Career event that is not a settled hub event (simulation, day advance,
--- save, load). Never touches the host or the disk.
function mirror.on_unsettled()
    mirror.abandon("unsettled")
    mirror.mark_dirty("career_event")
end

local function runner_pump_gen()
    local r = mirror._runner
    return r and tonumber(r._pump_gen) or 0
end

local function hub_still_ready()
    local r = mirror._runner
    if not r then return true end
    return r._simulating ~= true and r._hub_ready == true
end

local function fingerprint()
    local r = mirror._runner
    if r and type(r.career_save_ready) == "function" and not r.career_save_ready() then
        return nil, "career_required"
    end
    local getter = rawget(_G, "GetUserTeamID")
    if type(getter) ~= "function" then return nil, "no_team_api" end
    local ok, tid = pcall(getter)
    tid = ok and tonumber(tid) or nil
    if not tid or tid <= 0 then return nil, "no_team" end
    tid = math.floor(tid)
    local list = {}
    for pid in pairs(ops.squad_helper_ids(tid)) do list[#list + 1] = math.floor(pid) end
    table.sort(list)
    local uid = ""
    if r and type(r.current_save_uid) == "function" then uid = r.current_save_uid() or "" end
    return string.format("%s|%d|%s", tostring(uid), tid, table.concat(list, ",")), nil, uid
end

--- FNV-1a, 64 bit. Lua 5.4 integer arithmetic wraps, as FNV expects.
function mirror.hash(s)
    local h = 0xcbf29ce484222325
    local prime = 0x100000001b3
    local n = #s
    for i = 1, n, 64 do
        local bytes = { string.byte(s, i, math.min(i + 63, n)) }
        for j = 1, #bytes do
            h = (h ~ bytes[j]) * prime
        end
    end
    return string.format("%016x", h)
end

local function mirror_path()
    local q = cfg().queue_dir
    if type(q) ~= "string" or q == "" then return nil end
    return util.confine(util.join(q, mirror.DIR), mirror.FILE)
end

local function count_keys(t)
    local n = 0
    for _ in pairs(t or {}) do n = n + 1 end
    return n
end

local function start_build(fp, uid, source)
    local b = {
        fp = fp,
        save_uid = uid or "",
        source = source,
        started_at = util.now(),
        clock0 = util.clock(),
        ticks = 0,
        dirty_gen = mirror._dirty_gen,
        pump_gen = runner_pump_gen(),
        writes = util.copy(mirror._writes),
    }
    local function still_valid()
        return mirror._build == b and hub_still_ready() and runner_pump_gen() == b.pump_gen
    end
    local st, early = ops.squad_export_begin({}, {}, { cfg = cfg() }, {
        players_only = true,
        dedupe = true,
        still_valid = still_valid,
    })
    if not st then
        return nil, (early and early.reason) or "not_ready"
    end
    b.st = st
    b.still_valid = still_valid
    mirror._build = b
    mirror._dirty = false
    return b
end

local function write_file(text, hash, seq)
    local path, why = mirror_path()
    if not path then return false, why or "no_queue" end
    local ok, reason = util.write_atomic(path, text)
    if not ok then return false, reason end
    mirror._hash = hash
    mirror._seq = seq
    mirror._pending = nil
    mirror.stats.writes = mirror.stats.writes + 1
    return true
end

local function retry_later(reason)
    mirror._retry_at = util.clock() + limit("mirror_retry_s")
    mirror.last = reason
    return reason
end

local function publish(b)
    local st = b.st
    mirror._build = nil
    local players = st.players
    table.sort(players, function(a, c) return a.playerid < c.playerid end)
    local session_id = status.session_id()
    -- Hashed content: everything the app ingests. generated_at / seq stay out
    -- so an unchanged squad never produces a new file.
    local content = {
        teamid = st.teamid,
        teamname = st.teamname or "",
        save_uid = b.save_uid or "",
        count = #players,
        players = json.array(players),
        writes_seen = json.array(b.writes),
    }
    -- Extra export fields the app ingests ride along when the export has them.
    local data = type(st.result) == "table" and st.result.data or nil
    if type(data) == "table" and data.team_shirts ~= nil then
        content.team_shirts = data.team_shirts
    end
    local ctext = json.try_encode(content)
    if not ctext then
        mirror._dirty = true
        return retry_later("encode_failed")
    end
    local hash = mirror.hash(ctext)
    mirror._fp = b.fp
    mirror._built_at = util.clock()
    mirror._restarts = 0
    mirror.stats.builds = mirror.stats.builds + 1
    if hash == mirror._hash then
        mirror.stats.unchanged = mirror.stats.unchanged + 1
        mirror.last = "unchanged"
        return "unchanged"
    end
    local seq = math.max(mirror._seq + 1, util.now() * 1000)
    local meta = {
        schema = mirror.SCHEMA,
        kind = "squad_mirror",
        seq = seq,
        hash = hash,
        session_id = session_id,
        core_version = version.VERSION,
        generated_at = util.now(),
        started_at = b.started_at,
        source = b.source or "",
        ticks = b.ticks,
        build_ms = math.floor(util.elapsed_ms(b.clock0)),
    }
    local mtext = json.try_encode(meta)
    if not mtext then
        mirror._dirty = true
        return retry_later("encode_failed")
    end
    -- Both objects are flat-keyed and disjoint: splice them into one object.
    local text = mtext:sub(1, -2) .. "," .. ctext:sub(2)
    local ok, reason = write_file(text, hash, seq)
    if not ok then
        mirror._pending = { text = text, hash = hash, seq = seq }
        mirror.stats.write_failed = mirror.stats.write_failed + 1
        emit("write failed (" .. tostring(reason) .. "); retry in "
            .. tostring(limit("mirror_retry_s")) .. " s")
        return retry_later("write_failed")
    end
    if mirror.stats.writes == 1 then
        emit(string.format("squad.json written: %d players, seq %d", #players, seq))
    end
    mirror.last = "written"
    return "written"
end

local function due_for_rebuild(now)
    if mirror._dirty then return true end
    local verify = limit("mirror_verify_s")
    return verify > 0 and mirror._built_at ~= nil and (now - mirror._built_at) >= verify
end

--- One slice of mirror work. Caller (runner.tick_mirror) has already checked
--- the hub gates. `deadline` is a util.deadline; nil means this tick's own
--- LIMITS.mirror_tick_ms budget.
function mirror.tick(source, deadline)
    mirror.stats.ticks = mirror.stats.ticks + 1
    local now = util.clock()
    deadline = deadline or util.deadline(limit("mirror_tick_ms"))

    local b = mirror._build
    if b == nil then
        if mirror._retry_at and now < mirror._retry_at then return "backoff" end
        if mirror._pending then
            local p = mirror._pending
            local ok = write_file(p.text, p.hash, p.seq)
            if not ok then return retry_later("write_failed") end
            mirror.last = "written"
            return "written"
        end
        local due = due_for_rebuild(now)
        if not due and mirror._last_check
            and (now - mirror._last_check) * 1000 < limit("mirror_check_ms") then
            return "idle"
        end
        mirror._last_check = now
        mirror.stats.checks = mirror.stats.checks + 1
        local fp, why, uid = fingerprint()
        if not fp then
            -- No club yet: probe again after one check interval, even if dirty.
            mirror._retry_at = now + limit("mirror_check_ms") / 1000
            mirror.last = why
            return why
        end
        if fp == mirror._fp and not due then
            mirror.last = "unchanged"
            return "unchanged"
        end
        local started, reason = start_build(fp, uid, source)
        if not started then return retry_later(reason) end
        b = started
    elseif b.dirty_gen ~= mirror._dirty_gen or b.pump_gen ~= runner_pump_gen()
        or not hub_still_ready() then
        mirror.abandon("superseded")
        return "restarted"
    end

    if util.clock() >= deadline then return "building" end
    b.ticks = b.ticks + 1
    local r = ops.squad_export_step(b.st, deadline)
    if mirror._build ~= b or not b.still_valid() then
        mirror.abandon("unsettled")
        return "stale"
    end
    if r == "more" then
        mirror.last = "building"
        return "building"
    end
    if r == "stale" then
        mirror.abandon("stale")
        return "stale"
    end
    local res = b.st.result
    if type(res) ~= "table" or res.ok ~= true then
        mirror._build = nil
        mirror._dirty = true
        return retry_later("not_ready:" .. tostring(type(res) == "table" and res.reason or "?"))
    end
    -- A walk whose cursor was moved by a reader we cannot see (LE's own UI)
    -- can skip rows. Every helper id must have produced one row; rebuild a
    -- few times before trusting a short result.
    local want = count_keys(b.st.squad_ids)
    if #b.st.players < want and mirror._restarts < limit("mirror_max_restarts") then
        mirror._restarts = mirror._restarts + 1
        mirror.abandon("incomplete")
        return "incomplete"
    end
    return publish(b)
end

--- Status for diagnostics (LEC.core_info / tests).
function mirror.info()
    return {
        dirty = mirror._dirty,
        building = mirror._build ~= nil,
        phase = mirror._build and mirror._build.st and mirror._build.st.phase or nil,
        seq = mirror._seq,
        hash = mirror._hash,
        last = mirror.last,
        stats = util.copy(mirror.stats),
    }
end

return mirror
