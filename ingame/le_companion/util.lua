--[[ le_companion/util.lua — paths, atomic IO, name validation, clocks, errors.

WHY THIS MODULE EXISTS

  1. SI-7, and the reason it exists. v1's Lua worker read job names out of
     `_pending.txt` and `load()`ed whatever they pointed at, with NO validation
     of any kind. Anything that could append a line to a text file got arbitrary
     code execution inside FC 26. `safe_name()` here is the Lua half of the
     fix — the exact mirror of companion/core/paths.py::confine and
     companion/core/transport/jobfile.py::job_id_from_filename. A job file name
     must be a bare 26-character ULID plus ".json". Nothing else is a job.

  2. Atomic writes. A result file that the outside can observe half-written is
     a result file that makes the app lie. Everything goes through
     write_atomic(): write "<dest>.tmp", os.remove(dest), os.rename(tmp, dest).
     The remove is required — on Windows os.rename REFUSES to overwrite.

  3. Clocks. v1 mixed two clocks and never noticed: MIN_INTERVAL and
     BUSY_WATCHDOG were compared against os.clock() while heartbeats were
     stamped with os.time(). The two were never compared to each other, so the
     bug was invisible. Here they are two named functions with two documented
     meanings: clock() is the BUDGET timer (seconds, monotonic-ish, fractional)
     and now() is the WALL clock (integer unix seconds, what goes in files).
     V2_INGAME_CORE §0.8 measured os.clock in this host and found it usable as
     a wall timer; nothing here depends on that being exactly true, because
     clock() is only ever used for differences within one drain.

  4. Errors that survive. v1's collapse_err squashed whitespace to underscores
     and truncated at 200 chars because the text had to fit on a `key=value`
     line. With JSON that constraint is gone, so capture() keeps the full
     message AND the traceback; collapse_err() survives only for the legacy
     `_job_status.txt` shim, which still has the positional constraint.

  5. Directory listing — READ THIS BEFORE USING list_dir(). See §"ENUMERATION".

ENUMERATION — the honest version

  LE's Lua sandbox is Lua 5.4 with the standard library. The standard library
  has NO directory enumeration. LE's own host API (lua/DOC.MD, 50 functions)
  has none either — it is entirely DB/player/memory oriented. The two usual
  workarounds are both unavailable:

    * io.popen("dir /b") and os.execute — forbidden by tests/lua_lint.py, and
      rightly so: a shell call blocks the game thread and pops a console window
      over a fullscreen game.
    * package.loadlib("kernel32", "FindFirstFileW") — loadlib can only return
      functions with the lua_CFunction signature. Calling a raw Win32 export
      through it corrupts the Lua stack. That is a process kill, not a listing.
      LE's AOB/WriteJMP primitives could in principle trampoline into the real
      FindFirstFileW; doing that to read a directory would be the single most
      dangerous line of code in this project. Not doing it.

  So: this core CANNOT enumerate a directory by itself, and pretending
  otherwise would reproduce the exact class of bug v2 exists to kill — an empty
  listing that means "I am blind" being read as "the queue is empty".

  list_dir() therefore resolves through an ordered chain and ALWAYS reports
  which link answered:

    1. "host"   a host-provided lister (_G.LEC_DIR_LIST). Present in the Lua
                test harness (tests/lua/mock/le_stub.lua); absent in the game
                today; the seam is here so a future LE build costs us nothing.
    2. "index"  a sidecar written by the producer of the directory:
                  <dir>/index.json   a flat JSON array of file names
                  <dir>/index.txt    one file name per line
                Neither name can collide with a job file, because neither stem
                is a 26-char ULID. This is the mechanism the queue uses.
    3. "probe"  opts.candidates — names the caller already knows, filtered by
                existence. This is how resume and crash-sweep work with no
                index at all: the core wrote queue/state/<id>.state.json and
                queue/claimed/<id>.json itself, so it knows those ids.

  On failure it returns `nil, "no_enumeration"` — NEVER an empty table. Callers
  must distinguish "nothing queued" from "cannot see", and the runner surfaces
  the difference in session.json's `capabilities.enumerate`.

  LIMITATION, stated plainly: strategy 2 requires the Python side to write
  `queue/jobs/index.json` alongside each job (one call in
  FileTransport.submit/cancel). Until it does, newly submitted jobs are
  invisible to the core and the pill must not claim otherwise. A stale index is
  safe in one direction only: a listed name that no longer exists is skipped by
  the existence probe, but a job that exists and is not listed will not be
  drained until the index is rewritten. See MODULE_API.md §"Integration
  requirements".

  This core also cannot CREATE directories (no mkdir in the Lua stdlib). The
  queue subtree is created by companion/core/paths.py::ensure_layout. A missing
  directory surfaces as a "dir_missing" write error, never as silence.
--]]

local util = {}

util.TMP_SUFFIX = ".tmp"

-- Bare-ULID job file names. Crockford base32 minus I, L, O, U.
util.ULID_LEN = 26
util.ULID_CLASS = "^[0-9A-HJKMNP-TV-Z]+$"

-- Read ceiling for any single file the core ingests. A job descriptor that is
-- megabytes long is a bug or an attack, not a job.
util.MAX_READ_BYTES = 1048576

-------------------------------------------------------------------------------
-- Error sink (set by init.lua; falls back to LE's global Log)
-------------------------------------------------------------------------------

local error_sink = nil

--- Route util's own diagnostics somewhere. init.lua points this at log.lua.
-- Kept as an injected function rather than a require so util stays at the
-- bottom of the dependency graph with zero siblings (see §2 of the blueprint).
function util.set_error_sink(fn)
    error_sink = (type(fn) == "function") and fn or nil
end

local function emit(msg)
    if error_sink then
        pcall(error_sink, msg)
    elseif type(_G.Log) == "function" then
        pcall(_G.Log, "[LEC] " .. tostring(msg))
    end
end
util.emit = emit

-------------------------------------------------------------------------------
-- Clocks
-------------------------------------------------------------------------------

--- BUDGET clock. Fractional seconds, monotonic-ish, only meaningful as a
--- difference. Indirects through _G.__mock_clock so every timing test in
--- tests/lua/ is deterministic instead of flaky (V2_INGAME_CORE §8.2).
function util.clock()
    local f = _G.__mock_clock
    if type(f) == "function" then return f() end
    return os.clock()
end

--- WALL clock. Integer unix seconds. This is what goes into drain.json's `at`,
--- session.json, and every started_at/finished_at in a result. Python parses
--- the timestamp INSIDE the file and ignores mtime, so this value is load
--- bearing (mtime is refreshed by backup tools and AV scans).
function util.now()
    local f = _G.__mock_time
    if type(f) == "function" then return math.floor(f()) end
    return math.floor(os.time())
end

--- Human-facing UTC stamp. Never parsed for liveness — that is now()'s job.
function util.iso8601(t)
    local ok, s = pcall(os.date, "!%Y-%m-%dT%H:%M:%SZ", t or util.now())
    if ok and type(s) == "string" then return s end
    return ""
end

--- Absolute deadline `ms` milliseconds from now, in clock() units.
function util.deadline(ms)
    return util.clock() + (tonumber(ms) or 0) / 1000.0
end

--- Has a deadline from util.deadline() passed?  nil deadline == never expires
--- (a manual Force Drain where a one-off stall is acceptable and expected).
function util.expired(deadline)
    if deadline == nil then return false end
    return util.clock() >= deadline
end

--- Milliseconds remaining before `deadline`; 0 when past, math.huge when nil.
function util.remaining_ms(deadline)
    if deadline == nil then return math.huge end
    local left = (deadline - util.clock()) * 1000.0
    if left < 0 then return 0 end
    return left
end

--- Whole milliseconds elapsed since a clock() reading.
function util.elapsed_ms(t0)
    return math.floor(((util.clock() - t0) * 1000.0) + 0.5)
end

--- A restartable stopwatch. `sw:ms()` reads, `sw:reset()` restarts.
function util.stopwatch()
    local sw = {t0 = util.clock()}
    function sw:ms() return util.elapsed_ms(self.t0) end
    function sw:seconds() return util.clock() - self.t0 end
    function sw:reset() self.t0 = util.clock(); return self end
    return sw
end

-------------------------------------------------------------------------------
-- Strings
-------------------------------------------------------------------------------

function util.trim(s)
    if type(s) ~= "string" then return "" end
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

function util.startswith(s, prefix)
    return type(s) == "string" and type(prefix) == "string"
        and s:sub(1, #prefix) == prefix
end

function util.endswith(s, suffix)
    if type(s) ~= "string" or type(suffix) ~= "string" then return false end
    if suffix == "" then return true end
    return s:sub(-#suffix) == suffix
end

--- Split on a plain (non-pattern) separator. Empty fields are preserved.
function util.split(s, sep)
    local out = {}
    if type(s) ~= "string" then return out end
    sep = sep or "\n"
    local start = 1
    while true do
        local a, b = s:find(sep, start, true)
        if not a then
            out[#out + 1] = s:sub(start)
            break
        end
        out[#out + 1] = s:sub(start, a - 1)
        start = b + 1
    end
    return out
end

--- Lines of a text blob, with \r stripped and blank lines dropped.
function util.lines(s)
    local out = {}
    for _, raw in ipairs(util.split(s or "", "\n")) do
        local line = util.trim((raw:gsub("\r", "")))
        if line ~= "" then out[#out + 1] = line end
    end
    return out
end

--- "0x7FF6C2143A80" — record addresses in results are strings, not numbers,
--- because a 64-bit address does not survive a double round-trip intact.
function util.hex(addr)
    local n = tonumber(addr)
    if not n then return tostring(addr) end
    return string.format("0x%X", math.floor(n))
end

-------------------------------------------------------------------------------
-- Names and paths (SI-7)
-------------------------------------------------------------------------------

--- Is this a bare 26-character ULID?  Mirrors companion/domain/ids.py::is_ulid.
-- ULIDs sort lexicographically by creation time, which is why the queue needs
-- no separate ordering index: table.sort over ULIDs IS the drain order.
function util.is_ulid(s)
    return type(s) == "string"
        and #s == util.ULID_LEN
        and s:match(util.ULID_CLASS) ~= nil
end

--- Is this string safe to use as a single path component? (SI-7)
-- Rejects: empty, path separators, "..", a drive-letter prefix, a leading "_"
-- (reserved for v1's control files — `_pending.txt`, `_run_now.lua`,
-- `_wake.txt`, `_bridge_alive.txt` — which this core must never touch), and
-- anything with a control character or a NUL.
-- @return ok(boolean), reason(string|nil)
function util.safe_component(name)
    if type(name) ~= "string" or name == "" then
        return false, "empty_name"
    end
    if #name > 128 then
        return false, "name_too_long"
    end
    if name:find("[/\\]") then
        return false, "path_separator"
    end
    if name:find("..", 1, true) then
        return false, "dot_dot"
    end
    if name:sub(2, 2) == ":" then
        return false, "drive_letter"
    end
    if name:sub(1, 1) == "_" then
        return false, "reserved_prefix"
    end
    if name:find("%c") then
        return false, "control_char"
    end
    if name == "." then
        return false, "dot"
    end
    return true
end

--- The job-file name rule, and the ONLY one. `<26-char ULID>.json`.
-- Anything else is not a job — not `.lua` (that is v1's, untouchable), not a
-- `.tmp`, not an index sidecar, not a partial. This is the choke point that
-- makes v1's arbitrary-code-execution path unrepresentable.
-- @return ok(boolean), job_id(string|nil) on success / reason(string) on failure
function util.safe_name(name)
    if type(name) ~= "string" or name == "" then
        return false, "empty_name"
    end
    local comp_ok, comp_reason = util.safe_component(name)
    if not comp_ok then
        return false, comp_reason
    end
    if not util.endswith(name, ".json") then
        return false, "not_json"
    end
    local stem = name:sub(1, #name - 5)
    if not util.is_ulid(stem) then
        return false, "not_ulid"
    end
    return true, stem
end

--- `<ULID>.json` for a job id. Errors loudly rather than composing a bad path.
-- @return name(string|nil), reason(string|nil)
function util.job_filename(job_id)
    if not util.is_ulid(job_id) then return nil, "not_ulid" end
    return job_id .. ".json"
end

--- `<ULID>.json` -> ULID, or nil. Mirrors jobfile.py::job_id_from_filename.
function util.job_id_from_filename(name)
    local ok, stem_or_reason = util.safe_name(name)
    if ok then return stem_or_reason end
    return nil
end

--- Forward slashes, no duplicate separators, no trailing separator.
-- LE, the Windows CRT and Lua all accept forward slashes, and one convention
-- means one string comparison when the app checks that its queue_dir and the
-- core's queue_dir agree.
function util.normalize(p)
    if type(p) ~= "string" then return "" end
    local s = p:gsub("\\", "/")
    -- Collapse runs of "/" but keep a leading "//" (UNC) intact.
    local unc = util.startswith(s, "//")
    s = s:gsub("//+", "/")
    if unc then s = "/" .. s end
    if #s > 1 and util.endswith(s, "/") then
        s = s:sub(1, #s - 1)
    end
    return s
end

--- Join path fragments. Empty fragments are skipped, so join(dir, "", name)
--- does not produce a doubled separator.
function util.join(...)
    local parts = {}
    local n = select("#", ...)
    for i = 1, n do
        local piece = select(i, ...)
        if piece ~= nil and piece ~= "" then
            parts[#parts + 1] = util.normalize(tostring(piece))
        end
    end
    if #parts == 0 then return "" end
    return util.normalize(table.concat(parts, "/"))
end

function util.basename(p)
    local s = util.normalize(p)
    return s:match("([^/]*)$") or s
end

function util.dirname(p)
    local s = util.normalize(p)
    local dir = s:match("^(.*)/[^/]*$")
    return dir or ""
end

--- Absolute means a drive-letter root ("C:/...") or a UNC/rooted path.
function util.is_abs(p)
    local s = util.normalize(p)
    if s:match("^%a:/") then return true end
    if util.startswith(s, "//") then return true end
    return false
end

--- Resolve `name` strictly inside `base` (SI-7). The Lua mirror of
--- companion/core/paths.py::confine. There is no realpath in Lua, so this is a
--- syntactic guarantee: because `name` is validated as a single component with
--- no separators, no "..", and no drive letter, `base .. "/" .. name` cannot
--- escape `base`. Symlink traversal is out of scope on the platforms LE runs.
-- @return path(string|nil), reason(string|nil)
function util.confine(base, name)
    if type(base) ~= "string" or base == "" then
        return nil, "no_base"
    end
    local ok, reason = util.safe_component(name)
    if not ok then return nil, reason end
    return util.join(base, name)
end

--- confine() specialised to job files: base + a validated `<ULID>.json`.
-- @return path(string|nil), job_id(string|nil) / reason(string) on failure
function util.confine_job(base, name)
    local ok, stem_or_reason = util.safe_name(name)
    if not ok then return nil, stem_or_reason end
    return util.join(base, name), stem_or_reason
end

-------------------------------------------------------------------------------
-- File IO
-------------------------------------------------------------------------------

--- Does this path open for reading?  On Windows a directory does NOT, so this
--- is a file test, not a path test. Use dir_writable() for directories.
function util.exists(path)
    if type(path) ~= "string" or path == "" then return false end
    local f = io.open(path, "rb")
    if not f then return false end
    f:close()
    return true
end

--- Byte length, or nil.
function util.file_size(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local ok, size = pcall(function() return f:seek("end") end)
    f:close()
    if ok then return size end
    return nil
end

--- Read a whole file.
-- @return text(string|nil), reason(string|nil)
function util.read_file(path, max_bytes)
    if type(path) ~= "string" or path == "" then return nil, "no_path" end
    local f, oerr = io.open(path, "rb")
    if not f then return nil, "open_failed: " .. tostring(oerr) end
    local limit = max_bytes or util.MAX_READ_BYTES
    local ok, data = pcall(function() return f:read(limit + 1) end)
    f:close()
    if not ok then return nil, "read_threw" end
    if data == nil then return "", nil end          -- empty file, not an error
    if #data > limit then return nil, "too_large" end
    return data, nil
end

--- Read and drop a UTF-8 BOM if one is present. Python writes without a BOM,
--- but a user who "fixed" a file in Notepad will have added one, and a BOM in
--- front of "{" makes every JSON decoder fail with a useless message.
function util.read_text(path, max_bytes)
    local data, err = util.read_file(path, max_bytes)
    if not data then return nil, err end
    if data:sub(1, 3) == "\239\187\191" then
        data = data:sub(4)
    end
    return data, nil
end

--- Direct (non-atomic) write. Use ONLY for files nobody outside reads
--- mid-flight. Everything the Python side polls must use write_atomic.
-- @return ok(boolean), reason(string|nil)
function util.write_file(path, text)
    if type(path) ~= "string" or path == "" then return false, "no_path" end
    local f, oerr = io.open(path, "wb")
    if not f then
        -- io.open cannot distinguish "no such directory" from "denied", so say
        -- what we know and include the OS text rather than inventing a cause.
        return false, "open_failed: " .. tostring(oerr)
    end
    local ok = pcall(function() f:write(text or "") end)
    f:close()
    if not ok then return false, "write_threw" end
    return true
end

--- Atomic replace: write "<path>.tmp", remove the destination, rename.
-- The remove is NOT optional: on Windows os.rename refuses to overwrite an
-- existing file, so "write tmp then rename" alone silently fails from the
-- second write onward — which is exactly the kind of bug that leaves a stale
-- result file on disk and makes the app report last run's outcome.
-- @return ok(boolean), reason(string|nil)
function util.write_atomic(path, text)
    if type(path) ~= "string" or path == "" then return false, "no_path" end
    local tmp = path .. util.TMP_SUFFIX
    -- Keep the previous bytes at path.bak while the new file is renamed in.
    -- Windows os.rename cannot replace an existing file, and deleting the
    -- destination first made session.json vanish for a whole poll.
    local bak = path .. ".bak"
    local ok, reason = util.write_file(tmp, text)
    if not ok then
        os.remove(tmp)
        return false, reason
    end
    os.remove(bak)
    os.rename(path, bak)                         -- absent on the first write
    local rok, rerr = os.rename(tmp, path)
    if not rok then
        os.rename(bak, path)
        os.remove(tmp)
        return false, "rename_failed: " .. tostring(rerr)
    end
    os.remove(bak)
    return true
end

--- Delete. Missing is success — the caller wanted it gone and it is gone.
function util.remove(path)
    if type(path) ~= "string" or path == "" then return false, "no_path" end
    if not util.exists(path) then return true end
    local ok, err = os.remove(path)
    if not ok then return false, tostring(err) end
    return true
end

--- Plain rename with a normalised failure reason.
function util.rename(from, to)
    local ok, err = os.rename(from, to)
    if not ok then return false, tostring(err) end
    return true
end

--- CLAIM-BY-RENAME — the queue lock (V2_ARCHITECTURE §3.2).
-- os.rename(jobs/X.json, claimed/X.json) is atomic on NTFS and fails if the
-- destination already exists, so exactly one drain can win a job with no lock
-- file and no busy flag. A crash leaves the file in claimed/, where the next
-- arm sweeps it into a `crashed` result — v1 had no crash story at all and
-- jobs simply vanished.
-- @return ok(boolean), reason(string|nil)
--   "already_claimed" destination exists (another drain won, or a stale claim)
--   "vanished"        source is gone (cancelled between listing and claiming)
function util.claim(from, to)
    if util.exists(to) then return false, "already_claimed" end
    if not util.exists(from) then return false, "vanished" end
    local ok, err = os.rename(from, to)
    if not ok then
        if util.exists(to) then return false, "already_claimed" end
        return false, "rename_failed: " .. tostring(err)
    end
    return true
end

--- Is this directory present AND writable?  There is no stat() in Lua and
--- io.open on a directory fails on Windows, so the only honest test is to
--- write a probe file and delete it. The probe name is deliberately not a
--- valid job name (leading dot, no ULID stem), so a crash between create and
--- delete leaves litter that no code path can mistake for work.
-- @return ok(boolean), reason(string|nil)
function util.dir_writable(dir)
    if type(dir) ~= "string" or dir == "" then return false, "no_path" end
    local probe = util.join(dir, ".lec_probe.tmp")
    local f = io.open(probe, "wb")
    if not f then return false, "dir_missing_or_readonly" end
    f:close()
    os.remove(probe)
    return true
end

-------------------------------------------------------------------------------
-- Directory listing  (read the ENUMERATION note at the top of this file)
-------------------------------------------------------------------------------

-- Sidecar index file names. Neither stem is a 26-char ULID, so neither can be
-- confused with a job by safe_name().
util.INDEX_JSON = "index.json"
util.INDEX_TXT  = "index.txt"

--- The host-provided lister, if any. Returns a function or nil.
-- The Lua test harness installs _G.LEC_DIR_LIST; the game does not provide one.
local function host_lister()
    local f = _G.LEC_DIR_LIST
    if type(f) == "function" then return f end
    return nil
end

--- Pull the string elements out of a flat JSON array without a JSON parser.
-- util sits below json in the dependency graph and must stay dependency-free,
-- and the index file is by construction a flat array of plain file names —
-- no nesting, no escapes possible (a file name containing a quote or a
-- backslash would already have failed safe_component). If that assumption ever
-- stops holding, the names are rejected downstream by safe_name rather than
-- misparsed into a path.
local function extract_quoted(text)
    local out = {}
    for s in text:gmatch('"([^"\\]*)"') do
        out[#out + 1] = s
    end
    return out
end

--- List a directory.
-- @param dir  directory path
-- @param opts optional table:
--   candidates  array of names to probe when no index/host lister exists
--   filter      function(name) -> boolean, applied to every candidate
--   want_jobs   true: keep only names that pass safe_name(), sorted (ULID
--               order == creation order == drain order)
-- @return names(table), source(string)   on success ("host"|"index"|"probe")
-- @return nil, reason(string)            on failure ("no_enumeration")
--
-- NEVER returns an empty table to mean "I could not look". A caller that
-- cannot tell those apart is the v1 bug this whole protocol exists to kill.
function util.list_dir(dir, opts)
    opts = opts or {}
    if type(dir) ~= "string" or dir == "" then return nil, "no_path" end

    local names, source

    -- 1. Host lister (test harness, or a future LE build).
    local lister = host_lister()
    if lister then
        local ok, res = pcall(lister, dir)
        if ok and type(res) == "table" then
            names, source = res, "host"
        end
    end

    -- 2. Sidecar index written by the directory's producer.
    if not names then
        local text = util.read_text(util.join(dir, util.INDEX_JSON))
        if text and text ~= "" then
            names, source = extract_quoted(text), "index"
        else
            text = util.read_text(util.join(dir, util.INDEX_TXT))
            if text and text ~= "" then
                names, source = util.lines(text), "index"
            end
        end
    end

    -- 3. Caller-supplied candidates, filtered by existence. This is how the
    --    core finds its OWN files (state/, claimed/) with no index at all.
    if not names and type(opts.candidates) == "table" then
        local found = {}
        for _, n in ipairs(opts.candidates) do
            if type(n) == "string" and util.exists(util.join(dir, n)) then
                found[#found + 1] = n
            end
        end
        names, source = found, "probe"
    end

    if not names then
        return nil, "no_enumeration"
    end

    -- An index can be stale in the "listed but deleted" direction; drop those
    -- so callers never open a file that is not there. (The other direction —
    -- present but unlisted — is invisible and is documented as the limitation.)
    local out = {}
    for _, n in ipairs(names) do
        if type(n) == "string" and n ~= "" then
            local keep = true
            if opts.want_jobs then
                keep = (util.safe_name(n))
            end
            if keep and opts.filter then
                keep = opts.filter(n) and true or false
            end
            if keep and source == "index" and not util.exists(util.join(dir, n)) then
                keep = false
            end
            if keep then out[#out + 1] = n end
        end
    end
    table.sort(out)
    return out, source
end

--- Job ids in a queue directory, in drain order.
-- @return ids(table), source(string)  |  nil, reason
function util.list_job_ids(dir, opts)
    local o = util.copy(opts or {})   -- never mutate the caller's table
    o.want_jobs = true
    local names, source = util.list_dir(dir, o)
    if not names then return nil, source end
    local ids = {}
    for _, n in ipairs(names) do
        local id = util.job_id_from_filename(n)
        if id then ids[#ids + 1] = id end
    end
    table.sort(ids)     -- ULID lexical order IS creation order
    return ids, source
end

--- Which enumeration strategy would answer for this directory right now?
-- Reported in session.json so the app can say "the core cannot see the queue"
-- instead of showing a confident, wrong, empty queue.
-- @return "host" | "index" | "probe_only"
function util.enumeration_source(dir)
    if host_lister() then return "host" end
    if util.exists(util.join(dir, util.INDEX_JSON))
        or util.exists(util.join(dir, util.INDEX_TXT)) then
        return "index"
    end
    return "probe_only"
end

-------------------------------------------------------------------------------
-- Errors
-------------------------------------------------------------------------------

--- pcall that reports rather than swallows. The blueprint calls this at every
--- boundary where a failure must not take the game down with it.
-- @return ok(boolean), result-or-error
function util.pcall_log(fn, ...)
    if type(fn) ~= "function" then
        emit("pcall_log: not a function")
        return false, "not_a_function"
    end
    local ok, res = pcall(fn, ...)
    if not ok then
        emit("pcall failed: " .. tostring(res))
    end
    return ok, res
end

--- pcall_log with a label, so the log line says WHICH boundary failed.
function util.pcall_named(label, fn, ...)
    if type(fn) ~= "function" then
        emit(tostring(label) .. ": not a function")
        return false, "not_a_function"
    end
    local ok, res = pcall(fn, ...)
    if not ok then
        emit(tostring(label) .. " failed: " .. tostring(res))
    end
    return ok, res
end

--- xpcall with debug.traceback, captured at the point of the error.
-- This is what puts a REAL stack in result.error.traceback (§4.3) instead of
-- the single collapsed token v1 produced.
-- @return ok(boolean), result(any) | err(table {message, traceback, lua_version})
function util.capture(fn, ...)
    if type(fn) ~= "function" then
        return false, {message = "not a function", traceback = "", lua_version = _VERSION}
    end
    local tb
    local function handler(e)
        tb = debug.traceback(tostring(e), 2)
        return e
    end
    local ok, res = xpcall(fn, handler, ...)
    if ok then return true, res end
    return false, {
        message     = tostring(res),
        traceback   = tb or "",
        lua_version = _VERSION,
    }
end

--- Normalise any error value into the result-JSON `error` shape.
function util.err_table(phase, e, extra)
    local t = {phase = phase or "execute", lua_version = _VERSION}
    if type(e) == "table" then
        t.message   = tostring(e.message or e.msg or e[1] or "unknown error")
        t.traceback = tostring(e.traceback or "")
    else
        t.message   = tostring(e)
        t.traceback = ""
    end
    if type(extra) == "table" then
        for k, v in pairs(extra) do t[k] = v end
    end
    return t
end

--- LEGACY ONLY. Collapse an error to one whitespace-free token, truncated.
-- The v1 `_job_status.txt` line is `key=value` separated by spaces, and `msg=`
-- must stay LAST because apply_service.py matches it greedily to end-of-line.
-- Any v3 result field should carry the full text instead — use err_table.
function util.collapse_err(msg, limit)
    local s = tostring(msg or "")
    s = s:gsub("%s+", "_")
    limit = limit or 200
    if #s > limit then s = s:sub(1, limit) end
    if s == "" then s = "unknown" end
    return s
end

-------------------------------------------------------------------------------
-- Numbers
-------------------------------------------------------------------------------

function util.is_int(v)
    return type(v) == "number" and v == math.floor(v)
        and v ~= math.huge and v ~= -math.huge and v == v
end

function util.clamp(v, lo, hi)
    if v < lo then return lo end
    if v > hi then return hi end
    return v
end

function util.round(v)
    if type(v) ~= "number" then return v end
    if v >= 0 then return math.floor(v + 0.5) end
    return -math.floor(-v + 0.5)
end

--- tonumber that refuses strings, so a JSON "94" never silently becomes 94 and
--- lands in a bit-packed field. Type mismatches are validation errors, not
--- guesses (V2_INGAME_CORE §3.3).
function util.num(v)
    if type(v) == "number" then return v end
    return nil
end

-------------------------------------------------------------------------------
-- Tables
-------------------------------------------------------------------------------

function util.copy(t)
    if type(t) ~= "table" then return t end
    local out = {}
    for k, v in pairs(t) do out[k] = v end
    return out
end

function util.deep_copy(t, _seen)
    if type(t) ~= "table" then return t end
    _seen = _seen or {}
    if _seen[t] then return _seen[t] end
    local out = {}
    _seen[t] = out
    for k, v in pairs(t) do
        out[util.deep_copy(k, _seen)] = util.deep_copy(v, _seen)
    end
    return setmetatable(out, getmetatable(t))
end

--- Shallow merge of `src` over a copy of `dst`. `dst` is not mutated.
function util.merge(dst, src)
    local out = util.copy(dst or {})
    if type(src) == "table" then
        for k, v in pairs(src) do out[k] = v end
    end
    return out
end

--- Sorted key list. Sorting makes every log line and every JSON object the
--- core emits byte-stable, which is what makes golden-file tests possible.
function util.keys(t)
    local out = {}
    if type(t) ~= "table" then return out end
    for k in pairs(t) do out[#out + 1] = k end
    table.sort(out, function(a, b) return tostring(a) < tostring(b) end)
    return out
end

--- pairs() in sorted key order.
function util.sorted_pairs(t)
    local ks = util.keys(t)
    local i = 0
    return function()
        i = i + 1
        local k = ks[i]
        if k == nil then return nil end
        return k, t[k]
    end
end

function util.count(t)
    if type(t) ~= "table" then return 0 end
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    return n
end

function util.is_empty(t)
    return type(t) ~= "table" or next(t) == nil
end

--- Is `t` a dense 1..n array? (No holes; #t agrees with the pair count.)
function util.is_array(t)
    if type(t) ~= "table" then return false end
    local n = 0
    for k in pairs(t) do
        if type(k) ~= "number" or k < 1 or k ~= math.floor(k) then return false end
        n = n + 1
    end
    return n == #t
end

function util.contains(list, value)
    if type(list) ~= "table" then return false end
    for _, v in ipairs(list) do
        if v == value then return true end
    end
    return false
end

function util.index_of(list, value)
    if type(list) ~= "table" then return nil end
    for i, v in ipairs(list) do
        if v == value then return i end
    end
    return nil
end

--- { "a", "b" } -> { a = true, b = true }
function util.set_of(list)
    local out = {}
    if type(list) ~= "table" then return out end
    for _, v in ipairs(list) do out[v] = true end
    return out
end

--- Keys of a set, sorted. The inverse of set_of, for reporting.
function util.set_list(set)
    return util.keys(set)
end

function util.append(dst, src)
    if type(dst) ~= "table" then return dst end
    if type(src) == "table" then
        for _, v in ipairs(src) do dst[#dst + 1] = v end
    end
    return dst
end

--- list[i..j] (1-based, inclusive, j defaults to #list).
function util.slice(list, i, j)
    local out = {}
    if type(list) ~= "table" then return out end
    i = i or 1
    j = j or #list
    for k = i, j do
        if list[k] ~= nil then out[#out + 1] = list[k] end
    end
    return out
end

--- Last `n` entries of a list.
function util.tail(list, n)
    if type(list) ~= "table" then return {} end
    n = n or 20
    local from = #list - n + 1
    if from < 1 then from = 1 end
    return util.slice(list, from, #list)
end

function util.map(list, fn)
    local out = {}
    if type(list) ~= "table" or type(fn) ~= "function" then return out end
    for i, v in ipairs(list) do out[i] = fn(v, i) end
    return out
end

function util.filter(list, fn)
    local out = {}
    if type(list) ~= "table" or type(fn) ~= "function" then return out end
    for _, v in ipairs(list) do
        if fn(v) then out[#out + 1] = v end
    end
    return out
end

return util
