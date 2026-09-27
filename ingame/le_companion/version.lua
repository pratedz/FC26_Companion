--[[ le_companion/version.lua — the core's identity, as data.

WHY THIS MODULE EXISTS
  v1 had no version stamp anywhere. `PROTOCOL_VERSION = 2` lived in protocol.py
  and was never written into or read from a single queue file; the only markers
  on disk were a header comment and a hand-maintained "v15" string inside an
  unrelated generator. The single staleness signal was a substring test of the
  bridge path inside the autorun stub. So "is the installed core new enough to
  run this job?" was unanswerable, and the app papered over it by rewriting the
  bridge on every startup — which works only while the app and the bridge ship
  as one artifact. The moment the in-game side becomes a multi-file library
  with its own release cadence, that stops being viable.

  This module is that answer. It is pure data plus a semver comparator: no IO,
  no game access, no dependencies. Every other module and the whole version
  negotiation in V2_INGAME_CORE §7 reads from here, and `status.lua` copies it
  verbatim into `queue/session.json` so the Python side can refuse to queue a
  job the installed core cannot run (`CoreTooOld`, naming the specific op)
  instead of queueing one that fails at 3am inside the game.

  OP_VERSIONS is deliberately PER-OP. Shipping `add_to_team` v3 must not force
  a core-wide bump that invalidates every other descriptor. Python asks for
  exactly the op versions it needs.

  Keep KNOWN_OPS in lockstep with companion/domain/job.py::KNOWN_OPS and
  docs/schema/job.schema.json — those three lists are one contract in three
  places, and a disagreement is how a job becomes an "unknown op" at runtime.
--]]

local version = {}

-- Semantic version of the resident core. Bump on any behaviour change.
version.VERSION = "2.6.26"

-- Wire protocol contract number. Job envelopes carry `"schema": 3`; results
-- carry `"schema": 3`. This is PROTOCOL v3 (docs/V2_ARCHITECTURE.md §3), one
-- number for both directions, matching Python's `SCHEMA = 3`.
version.CONTRACT = 3
version.JOB_SCHEMA = 3
version.RESULT_SCHEMA = 3

-- The state-file and marker-file schemas travel with the contract.
version.STATE_SCHEMA = 3
version.SESSION_SCHEMA = 3
version.DRAIN_SCHEMA = 3

-- Stamped by the installer (companion/platform/le_install.py). Zero/empty in a
-- source checkout; a mismatch against companion_config.json means "someone
-- edited the installed core by hand or an install was interrupted".
version.BUILT_AT = 0
version.SHA256 = ""

--[[ Per-op descriptor versions. The core REFUSES an op whose descriptor `v`
     exceeds the number here, rather than executing a subtly different job than
     the one the app intended. Absent from this table == unknown op. ]]
version.OP_VERSIONS = {
    ["diag.ping"]     = 1,
    ["db.dump"]       = 1,
    ["set_fields"]    = 1,
    ["create_player"] = 1,
    ["add_to_team"]   = 4,
    ["repair_partial_add"] = 2,
    ["transfer"]      = 1,
    ["budget"]        = 1,
    ["bulk_edit"]     = 2,
    ["export_squad"]  = 1,
    ["snapshot"]      = 1,
    ["growth_sync"]   = 2,
    ["career.set"]    = 2,
    ["injury.scan"]   = 1,
    ["injury.cure"]   = 1,
}

--[[ Capabilities the Python side may gate features on. These describe what the
     core can DO, not what it happens to have loaded right now; `init.lua`
     subtracts any capability whose module failed to load before writing
     session.json, so the reported set is always the true one. ]]
version.CAPABILITIES = {
    player_index      = true,
    squad_index       = true,
    chunked_jobs      = true,
    verify_writes     = true,
    growth_mirror     = true,
    snapshot          = true,
    atomic_rollback   = false,
    aob               = true,
    legacy_lua_drain  = true,   -- v1 bridge coexists; permanent (DECISIONS.md)
    claim_by_rename   = true,
    crash_sweep       = true,
    fast_read_timer   = false,  -- set true in session.json only after createTimer enables
    http_push         = false,  -- SendHTTPRequest blocks the game thread; opt-in
    execute_sql       = false,
}

--[[ Hard limits. Every one of these is a number that broke something once.

     max_playerid       SI-5. Measured: CreatePlayer with ids 463139..494215 all
                        succeeded; 506153..546619 all KILLED the game process.
     drain_budget_ms    SI-8. Wall-clock ceiling for ONE drain across ALL jobs.
     drain_max_jobs     SI-8. Job ceiling for one drain. Either one exceeded
                        DEFERS the remainder to the next host event.
     default_budget_ms  Per-job default when the envelope omits `budget_ms`.
     max_attempts       Attempts before quarantine to queue/poison/.
     rows_small_max     db.rows_small() refuses tables bigger than this, because
                        GetDBTableRows on a large table is a 4-5 s stall that
                        allocates ~2.6M Lua tables.
]]
version.LIMITS = {
    max_playerid         = 499999,
    generated_id_min     = 460000,
    generated_id_max     = 499999,
    default_budget_ms    = 200,
    max_budget_ms        = 5000,
    drain_budget_ms      = 120,
    drain_max_jobs       = 8,
    max_attempts         = 3,
    max_job_bytes        = 1048576,
    rows_small_max       = 4000,
    index_build_slice    = 4000,
    log_ring_lines       = 500,
    log_tail_lines       = 120,
    next_free_id_probes  = 64,
}

-- Convenience: the op-name set, derived so it can never drift from OP_VERSIONS.
version.KNOWN_OPS = {}
for name in pairs(version.OP_VERSIONS) do
    version.KNOWN_OPS[name] = true
end

-- Ops that cannot be rolled back, so `atomic: true` must reject them at
-- validation. Mirrors companion/domain/job.py::NON_REVERSIBLE_OPS.
version.NON_REVERSIBLE_OPS = {}
for name in pairs(version.OP_VERSIONS) do
    version.NON_REVERSIBLE_OPS[name] = true
end

-- Ops that need an explicit grant in the envelope. Deny by default (SI-5).
-- Mirrors companion/domain/job.py::OP_GRANTS.
version.OP_GRANTS = {
    create_player = "allow_create_player",
    add_to_team = "allow_add_to_team",
    repair_partial_add = "allow_add_to_team",
}

-------------------------------------------------------------------------------
-- Semver
-------------------------------------------------------------------------------

--- Parse "MAJOR.MINOR.PATCH" into three integers.
-- Tolerates a leading "v" and a trailing "-prerelease"/"+build" (both ignored
-- for ordering, which is enough for our purposes and avoids a full semver
-- implementation nobody needs).
-- @return major, minor, patch  (nil when the string is not a version)
function version.parse(s)
    if type(s) ~= "string" then return nil end
    local core = s:match("^%s*v?([0-9]+%.[0-9]+%.[0-9]+)")
    if not core then
        -- Accept "2" and "2.1" as "2.0.0" / "2.1.0" so a sloppy core_min is a
        -- comparison, not a crash.
        local a, b = s:match("^%s*v?([0-9]+)%.?([0-9]*)")
        if not a then return nil end
        return tonumber(a), tonumber(b) or 0, 0
    end
    local a, b, c = core:match("^([0-9]+)%.([0-9]+)%.([0-9]+)$")
    return tonumber(a), tonumber(b), tonumber(c)
end

--- Compare two version strings.
-- @return -1 when a < b, 0 when equal, 1 when a > b, nil when either is unparseable.
function version.compare(a, b)
    local a1, a2, a3 = version.parse(a)
    local b1, b2, b3 = version.parse(b)
    if not a1 or not b1 then return nil end
    if a1 ~= b1 then return a1 < b1 and -1 or 1 end
    if a2 ~= b2 then return a2 < b2 and -1 or 1 end
    if a3 ~= b3 then return a3 < b3 and -1 or 1 end
    return 0
end

--- Is `have` at least `want`?  Unparseable input is NOT satisfied — an
--- unreadable core_min must refuse the job, never wave it through.
function version.gte(have, want)
    local c = version.compare(have, want)
    return c ~= nil and c >= 0
end

--- Does this core satisfy a job's `core_min`?
-- @return ok(boolean), reason(string|nil), detail(string|nil)
function version.satisfies(core_min)
    if core_min == nil or core_min == "" then
        return true   -- no floor requested
    end
    if version.parse(core_min) == nil then
        return false, "bad_core_min", tostring(core_min) .. " is not a semver"
    end
    if not version.gte(version.VERSION, core_min) then
        return false, "core_too_old",
            string.format("job requires core >= %s, this core is %s", core_min, version.VERSION)
    end
    return true
end

--- Is this op known, and is descriptor version `v` supported?
-- @return ok(boolean), reason(string|nil), detail(string|nil)
function version.op_supported(op_name, v)
    local have = version.OP_VERSIONS[op_name]
    if have == nil then
        return false, "unknown_op", string.format("op %s is not in this core's registry", tostring(op_name))
    end
    v = v or 1
    if type(v) ~= "number" or v ~= math.floor(v) or v < 1 then
        return false, "bad_op_version", string.format("op %s v=%s is not a positive integer", tostring(op_name), tostring(v))
    end
    if v > have then
        return false, "unknown_op_version",
            string.format("op %s v%d > core supports v%d", tostring(op_name), v, have)
    end
    return true
end

--- Is a job envelope's `schema` a contract this core speaks?
function version.schema_supported(schema)
    return schema == version.CONTRACT
end

--- One-line identity for logs. e.g. "le_companion 2.1.0 (contract 3)"
function version.banner()
    return string.format("le_companion %s (contract %d)", version.VERSION, version.CONTRACT)
end

return version
