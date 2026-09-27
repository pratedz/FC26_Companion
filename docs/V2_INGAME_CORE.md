# V2 In-Game Lua Core — Implementation Blueprint

Status: design, not yet implemented. Target: FC 26 + Live Editor v26.3.5, Lua 5.4.6.

This document specifies a resident in-game Lua library (`LEC`, "LE Companion core") that
replaces the current model of shipping whole generated Lua bodies per job. It is written
against the code as it exists on 2026-07-26:

| Thing | Where |
| --- | --- |
| Current worker (~420 lines) | `C:\Users\prated\Desktop\FC 26 LE v26.3.5\LE_Profile_Executor\bridge\le_profile_bridge.lua` |
| Installed copy | `C:\Users\prated\Desktop\FC 26 LE v26.3.5\lua\scripts\00_le_companion_bridge.lua` |
| Autorun arm | `C:\Users\prated\Desktop\FC 26 LE v26.3.5\lua\autorun\00_le_companion_autorun.lua` |
| Generators | `src\player_apply.py`, `src\add_team_lua.py`, `src\career_ops.py`, `src\squad_export.py` |
| Export template | `bridge\export_user_squad.lua` |
| LE v1 API | `C:\Users\prated\Desktop\FC 26 LE v26.3.5\lua\DOC.MD` |
| LE v2 T3DB | `lua\libs\v2\imports\t3db\{db,table,field}.lua` |
| Career helpers | `lua\libs\v2\imports\career_mode\helpers.lua` |
| Event ids | `lua\libs\v2\imports\career_mode\enums.lua` (authoritative) |

---

## 0. Findings that drive the design

These were measured or read out of the tree, not assumed. They are the reason the design
looks the way it does.

### 0.1 `written=N` counts failures as successes

`src\player_apply.py` increments `written` when `pcall` succeeds:

```lua
local okw = pcall(function() players_table:SetRecordFieldValue(current_record, fname, fval) end)
if okw then written = written + 1 else failed = failed + 1 end
```

But `TABLE:SetRecordFieldValue` delegates to `FIELD:SetValue`, and for integer fields
`FIELD:SetInt` ends with an unconditional `return true`. It never validates range. The
bit loop only writes `depth` bits:

```lua
local depth = self.fld_desc.depth - 1
for i=0, depth do ... end
MEMORY:WriteQword(addr, v)
return true
```

An out-of-range value is **silently truncated** and still reports success. `pcall` can only
catch a Lua error, and there is no Lua error to catch. So `written=91` has never meant
91 correct values. **The only honest success signal is read-back.**

Worse for strings: `FIELD:SetString` returns `nil` (no `return` statement), so
`SetRecordFieldValue` returns `nil` — falsy — for *every* successful string write.
Any caller that trusts the return value gets string writes exactly backwards.

And the lie survives all the way to the user. `player_apply.py` computes
`ok = tostring(found and written > 0)`, so a job with `written=88 failed=3` emits `ok=true`.
On the Python side (`src\apply_service.py:137-158`) the `reason` heuristic that would have
flagged `reason=partial_write_failures` is explicitly suppressed when `ok is True`:

```python
if _is_bad_value(out.get("reason")) and out["ok"] is not True: failed = True
```

So a partial write is reported to the user as a clean success today. §4 fixes this by making
`ok` mean "every requested field verified" and by having Python assert on the counts rather
than on a Lua-computed boolean.

### 0.2 `FIELD:SetString` truncates from the wrong end (upstream bug)

```lua
local string_max_len = math.floor(self.fld_desc.depth / 8)
local new_val_len = string.len(new_value)
if new_val_len > string_max_len then
    new_value = new_value:sub(1, new_val_len - string_max_len)
```

For a 20-char name into a 15-byte field this evaluates `sub(1, 5)` and stores `"Marco"`
instead of `"Marco van Baste"`. The core must clamp strings itself **before** calling
`SetValue`, and must never rely on the library's clamp.

### 0.3 The 25000 scan cap is real and is being hit

Live artifact, `queue\_job_status.txt`:

```
job=edit id=48940 found=false written=0 dev=false scanned=25000 names=false name=Petr Čech
```

`scanned=25000` is the cap in `player_apply.py` (`local MAX_SCAN = 25000`), not the end of
the table. The player existed; the scan gave up. Note this line also has no `ok=`,
`failed=` or `reason=` key — the status format has drifted between generators and the
Python parser has to tolerate three shapes.

### 0.4 add_to_team does six full table materialisations, and lies about the result

From `queue\_add_team_crash.log` (real run, pid 85308 → team 2):

```
06:29:10 fields_enter pid=85308 n=74      ─┐
06:29:15 face_apply_ok face=192181         ├─ GetDBTableRows("players")  ~5s
06:29:15 fields_done written=74           ─┘
06:29:15 name_enter                       ─┐
06:29:20 name_dict_clear written=5         ├─ GetDBTableRows("players")  ~5s
06:29:20 name_insert_ok addr=table: ...    │  + GetDBTableRows("editedplayernames")
06:29:20 name_verify GetPlayerName=[Zidane]│
06:29:20 name_done ok                     ─┘
06:29:20 name_enter                       ─┐
06:29:24 name_dict_clear written=5         ├─ same two scans again  ~4s
06:29:24 name_insert_ok addr=table: ...    │
06:29:24 name_verify GetPlayerName=[Zidane]│
06:29:24 name_done ok                     ─┘
06:29:24 transfer_enter / transfer_done ok
06:29:24 fields_enter pid=85308 n=74      ─┐  ~4s
06:29:28 fields_done written=74           ─┘
06:29:28 name_enter                       ─┐  ~4s
06:29:32 name_done ok                     ─┘
06:29:32 finish job=add_to_team ok=true path=dummy id=85308 team=2 transfer=true err=ok
```

**22 seconds of frozen game thread**, not 3-5. Six `GetDBTableRows` materialisations.
And three separate observations that the final `ok=true` is false:

1. `name_verify GetPlayerName=[Zidane]` — after every naming attempt, the player is still
   called Zidane. The name never applied. The job reported `name_done ok` anyway.
2. `name_insert_ok addr=table: 00000003713ABAB0` — the guard is
   `local addr = tostring(row.addr or "")` then `if addr ~= "" and addr ~= "0"`. `row.addr`
   is a **table**, so `tostring` yields `"table: 0x..."`, which is never `"0"`. The
   insert-failure check can never fire. Per `DOC.MD`, the check should be on
   `row.addr == "0"` where `addr` is a string field of the returned `DBRow`.
3. Three inserts ran because `set_edited_name` is called three times and the
   `existing == 0` guard re-reads via `GetDBTableRows`, which did not observe the rows
   just inserted. The save now has duplicate `editedplayernames` rows for one playerid,
   and the game resolves the first match — so a stale row wins permanently.

`GetDBTableRows("players")` builds roughly 21,000 row tables × ~120 field tables each,
i.e. ~2.5 million Lua tables per call. That is the 4-5s. It is not fixable by tuning; it
must not be called on the players table at all.

### 0.5 `CONST_CM_EVENTS_NAMES` and `ENUM_CM_EVENT_MSG_*` disagree — use the enums

Two event-id tables ship in the LE v2 libs and they are **not the same numbering**:

| id | `CONST_CM_EVENTS_NAMES` (consts.lua) | `ENUM_CM_EVENT_MSG_*` (enums.lua) |
| --- | --- | --- |
| 8 | `USER_MATCH_COMPLETED_IN_TOURNAMENT` | `CPU_MATCH_COMPLETED` |
| 34 | `STAGE_STARTED` | `UNKNOWN_MESSAGE_34` |
| 52 | `PLAYER_GROWTH` | `MANAGER_PRESTIGE_EVENT`-1 … (`PLAYER_GROWTH` is 53) |
| 67 | `SCREEN_HAS_DONE_LOADING` | `PRIZE_MONEY_RECEIVED` (`SCREEN_HAS_DONE_LOADING` is 68) |
| 85 | `TRANSFER_MOVE_COMPLETE` | `TRANSFER_MOVE_ABOUT_TO_COMPLETE` (`..._COMPLETE` is 86) |
| 109 | `PLAYER_ADDED_TO_TEAM` | `YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY` (`PLAYER_ADDED_TO_TEAM` is 110) |

They agree up to 33 and drift by +1 from 34 onward (with a second drift at 8). `consts.lua`
runs to index 288; `enums.lua` defines exactly **276** constants, ids 0..275, terminated by
`ENUM_CM_EVENT_MSG_LAST_EVENT = 275`.

`GetCMEventNameByID()` reads `CONST_CM_EVENTS_NAMES`, so **it mislabels every event above
33**. LE's own shipped scripts (`lua\scripts\auto_max_user_team_form.lua`) compare against
`ENUM_CM_EVENT_MSG_*`, which is the numbering the DLL actually dispatches.

> **Rule for the core:** all event logic keys off `ENUM_CM_EVENT_MSG_*`. `CONST_CM_EVENTS_NAMES`
> is never used for control flow. The core ships its own `LEC.events.name(id)` built from the
> enums so logs are not misleading.

### 0.6 The event handler signature

Confirmed from `lua\scripts\auto_max_user_team_form.lua`:

```lua
function MaxForm__OnEvent(events_manager, event_id, event)
AddEventHandler("post__CareerModeEvent", MaxForm__OnEvent)
```

Three arguments. The current bridge registers `local function on_cm()` — it **discards
`event_id`**, so today the worker cannot distinguish a save load from a match sim, and
drains on all of them including the ones where draining is dangerous.

### 0.7 T3DB table headers are cached at `GetTable()` time

`DB:GetTable(name)` walks the DB linked list, builds a fresh `TABLE`, and `TABLE:Load` snapshots:

```lua
self.first_record   = MEMORY:ReadQword(addr + 0x30)
self.record_size    = MEMORY:ReadInt(addr + 0x44)
self.written_records = MEMORY:ReadShort(addr + 0x7C)
```

Two consequences:

- A `TABLE` object held across an insert/delete is stale. Record addresses derived from it
  may point at moved or freed memory. **Never persist a record address across a yield.**
- `written_records` is a **16-bit read**. Above 65535 rows it wraps. FC 26's `players` is
  ~21k so this is headroom, not a bug, but a heavily modded DB would silently truncate every
  scan. The core surfaces this as a warning in `_core_info.json`.

Also note `DB:GetTable` does not expose the table's address, so the core keeps its own thin
wrapper that remembers `addr` and can re-read the header on demand.

### 0.8 os.clock is a usable wall timer here

On Windows/MSVC, `clock()` returns wall-clock time since process start, so `os.clock()` is a
millisecond-resolution wall timer. `os.time()` has 1-second resolution and is unusable for a
step budget. The existing bridge already relies on this (`MIN_INTERVAL = 0.08`).

### 0.9 There is no quarantine code path — the 12 poison jobs were quarantined by hand

`queue\done\` holds twelve `poison_*.lua` files (`poison_v10_freeze_run_now.lua`,
`poison_freeze_add_team_546619.lua`, `poison_add_team_243_20260724T072401Z.lua`, …). **No
Python or Lua code writes that prefix.** They were moved by hand during the FC 26 freeze
investigation. The only code that knows the prefix exists is a *reader*
(`src\boost_queue.py:40-44`):

```python
# done/ prefixes that are NOT proof the LE worker ran the job:
#   skipped_ = clear_stale_jobs archived it, orphan_ = companion archived it,
#   poison_  = quarantined. Counting these as "applied" is how a wiped job
#   used to report success.
_NON_BRIDGE_PREFIXES = ("skipped_", "poison_", "orphan_")
```

So the current system has no crash-loop detector at all. A job that kills the process is
re-armed every 4 seconds by `le_apply._rewake_job` (`src\le_apply.py:780-805`) and, because
the Lua worker rewrites `_pending.txt` with the names of jobs still on disk
(`le_profile_bridge.lua:285-289`), it survives restarts and is retried indefinitely. The
twelve files are the human doing manually what §6.3 makes the core do automatically.

Two related classifier bugs to fix while migrating: `le_apply._done_artifacts_for`
(`le_apply.py:674-709`) treats an `orphan_` archive as proof the bridge ran the job and has
**no `poison_` case at all**, so a quarantined job reads as a genuine completion there, while
`boost_queue._bridge_artifact_for` (`boost_queue.py:47-66`) rejects all three prefixes. Two
readers, two verdicts, same directory.

### 0.10 The status format has three producers, three parsers, and a positional constraint

`_job_status.txt` is written by `player_apply.py` (two shapes, 12 keys), `add_team_lua.py`
(one shape, 7 keys) and the bridge's own `write_job_error_status` fallback (6 keys). Python
parses it with two regexes (`src\apply_service.py:44-48`):

```python
_STATUS_TOKEN_RE = re.compile(r"([A-Za-z_][\w]*)=([^\s]+)")
# msg= is the last key on the line and may carry spaces (Lua error text).
_STATUS_MSG_RE = re.compile(r"\bmsg=(.+)$", re.MULTILINE)
```

**`msg=` must remain the last key on the line** — the regex is greedy to end-of-line. That is
a positional coupling between a Lua `string.format` and a Python regex, and it is why the
error text has to be collapsed to underscores before it can be reported at all.

Seven of the keys Lua emits (`devfailed`, `dev`, `names`, `names_ok`, `name`, `transfer`,
`file`) are never read by anything. Meanwhile `_last_result.txt` has **three independent
parsers** (`protocol.py:215-244`, `le_apply.py:872-921`, `boost_queue.py:316-344`), each
re-deriving `re.search(r"ok=(\d+)")`. JSON removes the whole class of problem: one schema,
one parser, no positional constraints, no key that means something only to the writer.

---

## 1. Why move logic in-game at all

### 1.1 The honest trade

Generated-Lua-per-job is genuinely good at some things, and it is worth naming them before
arguing against it:

| Generated Lua per job wins on | Resident core wins on |
| --- | --- |
| **Debuggability.** The exact code that ran is on disk in `queue/done/`. You can paste it into the Lua Engine and re-run it. | **One implementation.** A scan bug is fixed once, not four times. |
| **Versioning is free.** The job body *is* the version. No negotiation, no compatibility matrix. | **Testability.** The core is a Lua library; it can run headless against a mock. Generated strings can only be tested by string-matching. |
| **No install step.** Ship a new Python build, new behaviour lands immediately. | **Invariants can be enforced.** `playerid < 500000`, range clamping, read-back verification — the generator *cannot* opt out. |
| **No stale-core class of bug.** There is no "user has core 2.0, app expects 2.3". | **Cheap state.** A player index built once per session, not once per job. |
| **Blast radius.** A bad job breaks one job. | **Resumability.** A job can span events. A generated body cannot; it runs to completion or freezes. |

The cost of the resident core is real: updating it means writing files into the LE install
and restarting LE (autorun runs at LE startup). Users will run mismatched versions. That is
a genuine regression in operational simplicity, and section 7 exists to contain it.

### 1.2 What actually keeps breaking

Look at what the recurring bugs have in common:

- 25000-record scan caps (§0.3) — **mechanism**
- writes that report success without verifying (§0.1) — **mechanism**
- string truncation from the wrong end (§0.2) — **mechanism**
- error text that never leaves the game — **mechanism**
- insert-success checks that can never fire (§0.4) — **mechanism**
- six redundant table materialisations (§0.4) — **mechanism**
- draining during a save-prepare event — **mechanism**

None of these are *what to write*. They are all *how to write it*. Meanwhile the things that
actually change week to week — which fields a card maps to, which dummy to pick, what the OVR
formula is, which team id — are pure data decisions that live comfortably in Python and are
already covered by pytest.

### 1.3 Recommendation: split by mechanism vs policy

> **Mechanism goes in-game and is versioned like a program. Policy stays in Python and is
> shipped as data. The escape hatch stays, permanently, for the developer console only.**

```
┌──────────────────────── PYTHON (policy, changes weekly) ─────────────────────┐
│  card_catalog / player_schema / field_map / ovr_formula / target_players     │
│  "Which player, which fields, which values, which dummy, which team"         │
│                                                                              │
│  builders:  build_set_fields_job()  build_add_to_team_job()  ...             │
│  output:    a JSON descriptor.  Testable with pytest. No Lua.                │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │  queue/<job_id>.job.json
┌───────────────────────────────────▼──────────────────────────── LUA (mechanism,
│  LEC — resident core, installed once, versioned                  changes rarely) ─┐
│  "How to find a record, how to write a field and prove it, how to report,        │
│   how to not freeze the game thread, how to resume"                              │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │  queue/results/<job_id>.json
                                    ▼
                            Python reads one JSON
```

Concretely, this is where the line falls:

| Concern | Side | Why |
| --- | --- | --- |
| Which fields a FUT card maps to | Python | Data. Changes with every card release. Already unit-tested. |
| Clamping a value to the field's bit depth | Lua | Only the runtime knows `GetDBMeta()`. Must be unskippable. |
| Choosing a free-agent dummy | Python | Policy; needs the card DB and user preferences. |
| Refusing `playerid >= 500000` | Lua | A safety invariant. If a generator can forget it, it will. |
| The OVR formula | Python | Pure arithmetic, no game state, trivially testable. |
| Mirroring attribute writes into the growth plan | Lua | Depends on `PlayerHasDevelopementPlan` at write time. |
| Whether to run at all outside Career Mode | Lua | The envelope flag is declared in Python, enforced in Lua. |
| Retry / poison quarantine | Lua | Only the in-game side survives to observe a crash loop. |

The one thing that should **not** move in-game is anything that needs the internet, the card
database, or a UI. The core never makes a network call, never reads `profiles.json`, and never
decides *what* a player should look like.

---

## 2. Module layout

Installed as a directory, loaded once from autorun. Every module is a plain Lua module
returning a table; no globals except the single `LEC` namespace.

```
<LE data dir>/lua/
├── autorun/
│   └── 00_le_companion_autorun.lua      (existing; extended to load the core)
└── scripts/
    ├── 00_le_companion_bridge.lua       (existing legacy worker; kept through migration)
    └── le_companion/
        ├── init.lua          entry point, LEC namespace, version, wiring
        ├── version.lua       VERSION, CONTRACT, OP_VERSIONS  (generated at build)
        ├── util.lua          path/io/atomic-write/error-collapse/clock
        ├── json.lua          vendored copy of imports/external/json.lua
        ├── log.lua           ring buffer + Log() passthrough; per-job capture
        ├── events.lua        event id constants, classification, dispatch
        ├── schema.lua        GetDBMeta()-driven field descriptors + validation
        ├── db.lua            table handles, typed get/set with read-back verify
        ├── index.lua         playerid -> record addr map, generation, invalidation
        ├── status.lua        result/partial/state JSON writers
        ├── growth.lua        dev-plan XP mirror
        ├── career.lua        manager struct accessors (GetManagerObjByTypeId)
        ├── mem.lua           memory primitives + AOB, with caching
        ├── runner.lua        queue drain, step pump, budget, poison quarantine
        └── ops/
            ├── _registry.lua
            ├── set_fields.lua
            ├── create_player.lua
            ├── add_to_team.lua
            ├── transfer.lua      (transfer / loan / release / terminate / list)
            ├── budget.lua
            ├── bulk_edit.lua
            ├── export_squad.lua
            ├── snapshot.lua
            ├── growth_sync.lua
            └── raw_lua.lua
```

Dependency graph — strictly acyclic, `ops/*` at the top, `util` at the bottom:

```
                        ┌──────────┐
                        │  init    │
                        └────┬─────┘
                 ┌───────────┼────────────┐
            ┌────▼────┐ ┌────▼────┐  ┌────▼────┐
            │ runner  │ │ events  │  │ status  │
            └────┬────┘ └────┬────┘  └────┬────┘
                 │           │            │
            ┌────▼───────────▼────────────▼─────┐
            │            ops/*                  │
            └────┬──────────┬─────────┬─────────┘
        ┌────────▼──┐  ┌────▼───┐ ┌───▼────┐ ┌────────┐
        │  index    │  │ growth │ │ career │ │  mem   │
        └────┬──────┘  └───┬────┘ └───┬────┘ └───┬────┘
             └──────┬──────┴──────────┘          │
                ┌───▼───┐   ┌────────┐           │
                │  db   │──▶│ schema │           │
                └───┬───┘   └───┬────┘           │
                    └─────┬─────┴────────────────┘
                    ┌─────▼─────┐  ┌──────┐  ┌──────┐
                    │   util    │  │ json │  │ log  │
                    └───────────┘  └──────┘  └──────┘
```

### 2.1 `init.lua` — load once, register handlers, never drain

```lua
-- lua/scripts/le_companion/init.lua
if _G.LEC and _G.LEC.__loaded then return _G.LEC end

local LEC = {}
_G.LEC = LEC
LEC.__loaded  = true
LEC.version   = require("le_companion.version")
LEC.util      = require("le_companion.util")
LEC.json      = require("le_companion.json")
LEC.log       = require("le_companion.log")
LEC.events    = require("le_companion.events")
LEC.schema    = require("le_companion.schema")
LEC.db        = require("le_companion.db")
LEC.index     = require("le_companion.index")
LEC.status    = require("le_companion.status")
LEC.growth    = require("le_companion.growth")
LEC.career    = require("le_companion.career")
LEC.mem       = require("le_companion.mem")
LEC.runner    = require("le_companion.runner")

-- LEC.configure() is called by autorun with the queue dir substituted at install time.
function LEC.configure(opts)
    LEC.queue_dir = opts.queue_dir
    LEC.runner.configure(opts)
end

-- Handlers ONLY. Draining during LE init froze the editor historically; nothing here
-- touches the DB, allocates a scan, or reads a queue file beyond writing _core_info.json.
function LEC.arm()
    if LEC.__armed then return end
    LEC.__armed = true

    AddEventHandler("post__LEInitDoneEvent", function()
        -- Safe: pure file write, no DB access.
        LEC.util.pcall_log(LEC.status.write_core_info)
    end)

    AddEventHandler("pre__CareerModeEvent", function(_, event_id)
        LEC.events.on_career(event_id, "pre")
    end)

    AddEventHandler("post__CareerModeEvent", function(_, event_id)
        LEC.events.on_career(event_id, "post")
    end)
end

return LEC
```

`autorun/00_le_companion_autorun.lua` gains three lines after the existing bridge load:

```lua
package.path = CORE_DIR .. "/?.lua;" .. package.path
local ok, LEC = pcall(require, "le_companion.init")
if ok then LEC.configure({queue_dir = QUEUE_DIR}); LEC.arm() end
```

The legacy bridge load stays exactly as it is. Both can be armed simultaneously; they own
different file patterns (`*.lua` vs `*.job.json`) and coordinate through one busy flag.

### 2.2 `schema.lua` — the DB schema is data, read it once

`GetDBMeta()` returns the full schema at runtime. Today every generator hardcodes assumptions
about field names and ranges; `schema.lua` makes them queryable.

```lua
-- schema.field("players", "overallrating") ->
--   { name="overallrating", type=3, depth=7, min=1, max=128,
--     offset=..., startbit=..., kind="int" }
function schema.field(table_name, field_name)   -- nil if absent
function schema.has(table_name, field_name)     -- boolean
function schema.range(table_name, field_name)   -- min, max   (max = min + (1<<depth) - 1)
function schema.kind(table_name, field_name)    -- "int" | "float" | "string"
function schema.max_string_len(table_name, f)   -- floor(depth / 8)
function schema.tables()                        -- { [name]=true }
function schema.validate_value(tname, fname, v) -- ok, err_code, detail
```

`validate_value` is the single choke point that makes §0.1 impossible:

```lua
function schema.validate_value(tname, fname, v)
    local f = schema.field(tname, fname)
    if not f then return false, "no_such_field",
        string.format("field %s not in table %s", fname, tname) end
    if f.kind == "string" then
        if type(v) ~= "string" then return false, "type_mismatch", "expected string" end
        return true            -- clamping happens in db.set, not here
    end
    if type(v) ~= "number" then return false, "type_mismatch", "expected number" end
    if f.kind == "int" then
        if v ~= math.floor(v) then return false, "not_integer", tostring(v) end
        if v < f.min or v > f.max then
            return false, "out_of_range",
                string.format("value %d outside %d..%d (depth %d)", v, f.min, f.max, f.depth)
        end
    end
    return true
end
```

The schema is loaded lazily on first use and cached for the session. It is invalidated only
on `POST_LOAD_PREPARE` / `DATA_READY` (a different save can, in principle, carry a modded
schema).

### 2.3 `db.lua` — table handles that know their address, and writes that prove themselves

`DB:GetTable` in the LE lib does not expose the table address, and caches its header
(§0.7). `db.lua` replicates the list walk once, keeps the address, and can refresh.

```lua
-- Handle, not a snapshot.
local h = LEC.db.open("players")     -- cached per session, keyed by name
h.addr                                -- T3DB table struct address
h:refresh()                           -- re-read first_record/record_size/written_records
h:generation()                        -- "7ff6c2100000:21437:328"  (base:count:size)
h:record_at(idx)                      -- first_record + record_size*idx
h:is_valid(rec)                       -- high bit of last byte clear
h:count()                             -- written_records  (NOT total_records)
```

Iteration helper that is correct by construction — bounded by `written_records`, never by a
magic constant:

```lua
-- for rec in h:records() do ... end
function Table:records()
    self:refresh()
    local idx, last = -1, self.written_records - 1
    return function()
        while idx < last do
            idx = idx + 1
            local rec = self.first_record + self.record_size * idx
            if self:is_valid(rec) then return rec, idx end
        end
        return nil
    end
end
```

The write path. Note the four things it does that no current generator does: validate, clamp
strings itself, read back, and classify the failure.

```lua
-- returns ok(boolean), reason(string|nil), detail(string|nil), readback(any)
function db.set(h, rec, fname, value)
    local ok, code, detail = LEC.schema.validate_value(h.name, fname, value)
    if not ok then return false, code, detail end

    local f = LEC.schema.field(h.name, fname)
    if f.kind == "string" then
        -- Clamp HERE. FIELD:SetString truncates from the wrong end (see §0.2).
        local maxlen = LEC.schema.max_string_len(h.name, fname)
        if #value > maxlen then value = value:sub(1, maxlen) end
    end

    local wok = pcall(function() h.t:SetRecordFieldValue(rec, fname, value) end)
    if not wok then return false, "write_threw", nil end

    -- The ONLY honest success signal.
    local rok, back = pcall(function() return h.t:GetRecordFieldValue(rec, fname) end)
    if not rok then return false, "readback_threw", nil end

    if f.kind == "float" then
        if math.abs((back or 0) - value) > 1e-4 then
            return false, "verify_mismatch",
                string.format("wrote %s read %s", tostring(value), tostring(back)), back
        end
    elseif back ~= value then
        return false, "verify_mismatch",
            string.format("wrote %s read %s", tostring(value), tostring(back)), back
    end
    return true, nil, nil, back
end

function db.get(h, rec, fname)          -- nil, reason on failure
function db.set_many(h, rec, map, opts) -- -> written, failures[]  (opts.verify default true)
```

`db.set_many` returns `written` meaning *verified*, and a `failures` array of
`{field, requested, readback, reason, detail}`. That array is what lands in the result JSON.

Row insert/delete still needs the v1 API — T3DB cannot insert. `db.insert_row` wraps it and
fixes the §0.4 check:

```lua
function db.insert_row(table_name, row_data)
    if type(InsertDBTableRow) ~= "function" then return nil, "no_insert_api" end
    local ok, row = pcall(InsertDBTableRow, table_name, row_data)
    if not ok or type(row) ~= "table" then return nil, "insert_threw" end
    -- DOC.MD: a failed insert returns a row whose addr field is the STRING "0".
    -- row.addr is a DBRow field table, not a string. tostring(row.addr) is
    -- "table: 0x..." and is never "0" — which is why the old guard never fired.
    local addr = row.addr
    if type(addr) == "table" then addr = addr.value end
    addr = tostring(addr or "")
    if addr == "" or addr == "0" then return nil, "insert_rejected" end
    return row
end
```

`db.lua` exposes **no** wrapper for `GetDBTableRows` on `players`. Calling it is a 4-5s stall
(§0.4). The one place it is still needed is `editedplayernames` (small table, and insert
requires the v1 row API); that path is explicitly named `db.rows_small(name, max_rows)` and
refuses tables whose `written_records` exceeds a configured ceiling (default 4000).

### 2.4 `index.lua` — see §5

### 2.5 `status.lua` — see §4

### 2.6 `growth.lua` — the XP mirror, schema-driven

Attribute writes revert unless growth XP is mirrored via
`PlayerSetValueInDevelopementPlan`. Today the eligibility rule is a hardcoded exclusion list
inside `player_apply.py` — a negative list of ~14 field names plus four `string.find`
prefix tests. It is duplicated nowhere else, so `add_team_lua.py` mirrors nothing at all.

`DOC.MD` states the plan covers: all attributes, workrates, skillmoves, weakfoot. That is a
**positive** list and it is small. The core carries it as data:

```lua
growth.PLAN_FIELDS = {           -- positive list; anything absent is never mirrored
  acceleration=true, sprintspeed=true, positioning=true, finishing=true,
  shotpower=true, longshots=true, volleys=true, penalties=true,
  vision=true, crossing=true, freekickaccuracy=true, shortpassing=true,
  longpassing=true, curve=true, agility=true, balance=true, reactions=true,
  ballcontrol=true, dribbling=true, composure=true, interceptions=true,
  headingaccuracy=true, marking=true, standingtackle=true, slidingtackle=true,
  jumping=true, stamina=true, strength=true, aggression=true,
  gkdiving=true, gkhandling=true, gkkicking=true, gkpositioning=true, gkreflexes=true,
  attackingworkrate=true, defensiveworkrate=true,
  skillmoves=true, weakfootabilitytypecode=true,
}
```

`overallrating` and `potential` are deliberately **not** in the list — they are not plan
fields per `DOC.MD`, and the current code mirrors them, which is one plausible source of
"attributes drift back after a match".

```lua
-- ctx caches the has_plan answer for the job; one pcall per player, not per field.
function growth.has_plan(playerid)         -- cached, false outside CM
function growth.eligible(fname)            -- PLAN_FIELDS[fname] == true
function growth.mirror(playerid, fname, v) -- ok, reason
function growth.mirror_many(playerid, map, mode)
    -- mode: "auto" (only if has_plan), "force", "off"
    -- -> mirrored, skipped, failures[]
function growth.resync_from_table(playerid)
    -- read every eligible field off the players row and push it into the plan.
    -- This is the fix for "I did not change anything and the stats reverted".
end
```

### 2.7 `career.lua` — manager struct accessors

`GetManagerObjByTypeId(type_id)` (career_mode/helpers.lua) resolves an FCE career manager
object. The type ids are in `enums.lua`. Wrapping them centrally means offsets live in one
file that can be re-based when the game patches.

```lua
career.TYPE = {
  BUDGET        = ENUM_FCEGameModesFCECareerModeBudgetManager,        -- 25
  CALENDAR      = ENUM_FCEGameModesFCECareerModeCalendarManager,      -- 26
  DATA          = ENUM_FCEGameModesFCECareerModeDataController,       -- 34
  PLAYER_GROWTH = ENUM_FCEGameModesFCECareerModePlayerGrowthManager,  -- 83
  PLAYER_STATUS = ENUM_FCEGameModesFCECareerModePlayerStatusManager,  -- 89
  TRANSFER      = ENUM_FCEGameModesFCECareerModeTransferManager,      -- 129
  USER          = ENUM_FCEGameModesFCECareerModeUserManager,
}

function career.obj(type_id)      -- 0 when absent; never throws
function career.in_cm()           -- cached per pump
function career.user_teamid()     -- career_users[0].clubteamid via T3DB, not a 20k scan
function career.is_manager_career()
function career.pap_id()
function career.squad_role_set(playerid, role)   -- from helpers.lua SetSquadRole
function career.date()                            -- {day, month, year}
function career.save_uid()
```

Every accessor declares its offsets in a table at the top of the file with a comment naming
where the offset came from, so a game patch is a one-file diff:

```lua
career.OFFSETS = {
  USER = { mUserType = 0x37, mPlayerId = 0x3C },   -- FCECareerModeUserManager.lua
  CAL  = { day = 0x34, month = 0x38, year = 0x3C },-- other/helpers.lua GetCurrentDate
}
```

### 2.8 `mem.lua` — memory + AOB, with a signature cache

Thin pass-through to LE's primitives plus the one thing the LE lib lacks: AOB results cached
across a session and keyed by module base, because `AOBScan` over the game module is slow and
must never run inside a step budget.

```lua
function mem.read_qword(addr) / read_int / read_short / read_bytes / read_string
function mem.write_qword(addr, v) / write_int / write_bytes / write_string
function mem.mlptr(base, offsets)         -- ReadMultilevelPointer, 0 on any nil hop
function mem.aob(name, pattern, opts)     -- cached; opts.resolve_ptr = N
function mem.aob_cache_clear()
function mem.module()                     -- {name, base, size} from LE_GAME_MODULE_*
```

`mem.aob` records a `scanned_ms` per signature and refuses to run when the caller is inside a
step budget with less than `scan_ms` remaining — an AOB scan is a "do it during a yield"
operation, and the runner schedules it as its own step.

---

## 3. The job contract

### 3.1 File layout in the queue directory

Additive. Nothing that exists today is renamed or repurposed.

```
queue/
├── <job_id>.job.json          NEW  descriptor, drained by LEC.runner
├── <job_id>.lua               existing legacy body, drained by the old bridge
├── _run_now.lua               existing
├── _pending.txt               existing
├── _wake.txt                  existing
├── _job_status.txt            existing (dual-written during migration)
├── _last_result.txt           existing (dual-written during migration)
├── _bridge_alive.txt          existing
├── _core_info.json            NEW  written at LEInitDone; version negotiation (§7)
├── _last_result.json          NEW  {"job_id": "...", "path": "results/....json"}
├── results/
│   ├── <job_id>.json          NEW  final result
│   └── <job_id>.partial.json  NEW  rewritten after every step
├── state/
│   └── <job_id>.state.json    NEW  resume cursor + attempt counter (§6)
├── poison/
│   └── <job_id>.job.json      NEW  quarantined after max_attempts (§6)
└── done/
    └── <job_id>.job.json      existing convention, extended to descriptors
```

### 3.2 The envelope

A job is an **ordered list of ops**, not a single kind. This is what makes `add_to_team`
expressible as composition instead of a 600-line generator, and it is what makes chunking
natural — an op boundary is always a safe yield point.

```json
{
  "schema": 1,
  "job_id": "apply_158023_20260726T102200Z",
  "core_min": "2.0.0",
  "label": "Apply Lionel Messi TOTY to 158023",
  "created_at": 1784960972,
  "require_cm": true,
  "atomic": false,
  "dry_run": false,
  "budget_ms": 200,
  "max_attempts": 3,
  "snapshot_to": "C:/.../snapshots/apply_158023_20260726T102200Z.json",
  "ops": [ { "op": "set_fields", "id": "stats", "...": "..." } ]
}
```

| Key | Req | Meaning |
| --- | --- | --- |
| `schema` | yes | Descriptor envelope version. Core refuses anything it does not know. |
| `job_id` | yes | Unique. Names the result, state and done files. `[A-Za-z0-9_.-]{1,120}`. |
| `core_min` | yes | Semver. Core refuses if `core.version < core_min`. |
| `label` | no | Human string for logs and the result. |
| `require_cm` | no | Default `true`. Refuse (not fail) outside Career Mode. |
| `atomic` | no | Default `false`. Snapshot-then-rollback on any op failure. **Validation rejects `atomic:true` if the job contains a non-reversible op** (`create_player`, `transfer`, `loan`, `release`, `add_to_team`, `raw_lua`). |
| `dry_run` | no | Validate + resolve targets + report what *would* change. Zero writes. |
| `budget_ms` | no | Default 200. Wall-clock the pump may spend per invocation. |
| `max_attempts` | no | Default 3. Then quarantine (§6). |
| `snapshot_to` | no | Capture every field the job will touch, before touching it. Undo for free. |
| `ops` | yes | Non-empty array. Executed in order. |

Every op shares:

| Key | Req | Meaning |
| --- | --- | --- |
| `op` | yes | Kind. Must be in the core's registry. |
| `id` | yes | Unique within the job. Result failures are attributed to it. |
| `v` | no | Op descriptor version, default 1. Core refuses `v` > its own. |
| `on_error` | no | `"abort"` (default) or `"continue"`. |

**Unknown keys are rejected**, at any level, unless prefixed `x_`. Silently ignoring a
misspelled key is how a job half-executes. `x_` keys are reserved for forward-compatible
annotations the core may ignore (e.g. `x_ui_hint`).

### 3.3 Op: `set_fields`

Replaces the whole of `src/player_apply.py`'s generated body.

```json
{
  "op": "set_fields",
  "id": "stats",
  "table": "players",
  "key": {"field": "playerid", "value": 158023},
  "fields": {
    "overallrating": 94,
    "potential": 94,
    "finishing": 95,
    "firstname": "Lionel",
    "usercaneditname": 1
  },
  "verify": true,
  "growth_mirror": "auto",
  "on_field_error": "continue",
  "require_found": true
}
```

- `key` — when `table == "players"` and `key.field == "playerid"`, resolution goes through
  the index (§5): O(1). Otherwise a bounded `h:records()` scan. Either way there is **no
  `MAX_SCAN`**; iteration is bounded by `written_records`.
- `fields` — one map. Types come from the schema; the descriptor does not distinguish ints
  from strings, because the schema already knows and a mismatch is a validation error, not a
  guess.
- `growth_mirror` — `"auto"` | `"off"` | `"force"`. `auto` mirrors only fields in
  `growth.PLAN_FIELDS` and only when `PlayerHasDevelopementPlan(playerid)`.
- `on_field_error` — `continue` records the failure and carries on; `abort` stops the op.
- `require_found` — when true (default) a missing target fails the op. When false it is a
  reported no-op (used by bulk workflows that tolerate absent ids).

A `set_names` convenience is *not* a separate op: names are just fields. The
`editedplayernames` handling that both `player_apply.py` and `add_team_lua.py` reimplement
differently becomes a second `set_fields` op with `"table": "editedplayernames"` and
`"upsert": true`:

```json
{
  "op": "set_fields", "id": "names", "table": "editedplayernames",
  "key": {"field": "playerid", "value": 158023},
  "upsert": true,
  "fields": {"firstname": "Lionel", "surname": "Messi",
             "commonname": "Messi", "playerjerseyname": "Messi"}
}
```

`upsert: true` means: find the row; if absent, `db.insert_row`; **if more than one row
matches, update the first and report the duplicates as a failure** rather than inserting a
third (the §0.4 bug).

### 3.4 Op: `create_player`

```json
{
  "op": "create_player",
  "id": "mk",
  "playerid": 247001,
  "id_range": [240000, 249999],
  "on_conflict": "next_free",
  "fields": {"height": 180, "weight": 75, "bodytypecode": 5, "...": "..."},
  "names": {"firstname": "Marco", "surname": "van Basten", "playerjerseyname": "van Basten"},
  "generic_head": true
}
```

Core-enforced invariants that the descriptor **cannot** override:

- `playerid <= 499999`. `CreatePlayer` with `playerid >= 500000` kills the process. This is a
  hard clamp in `ops/create_player.lua`, checked at validation time, before any op runs.
- `PlayerExists(playerid)` must be false. Duplicate playerids crash. `on_conflict` chooses
  `"fail"` (default) or `"next_free"` (walk `id_range` for the first free id, bounded to 64
  probes so the search cannot become the stall).
- `generic_head: true` forces `headassetid = playerid`, `hashighqualityhead = 0`,
  `headclasscode = 1`, `headtypecode = 0`. Pointing `CreatePlayer` at a real head asset is a
  known freeze; a real face is applied *afterwards* by a `set_fields` op on the created id.

The op result carries `"data": {"playerid": 247001}` so a following op can reference it via
`"$ref": "mk.playerid"` — a one-level reference resolved by the runner, no expression
language.

### 3.5 Op: `add_to_team`

The chunked one. Replaces `src/add_team_lua.py` (600 lines of Python emitting ~370 lines of
Lua) with a descriptor and a state machine.

```json
{
  "op": "add_to_team",
  "id": "add",
  "teamid": 2,
  "strategy": "dummy_overwrite",
  "dummy_pool": [85308, 91234, 77120],
  "dummy_filter": {"max_playerid": 240000, "must_exist": true, "must_be_free_agent": true},
  "player": {
    "fields": {"overallrating": 93, "finishing": 92, "...": "..."},
    "names": {"firstname": "Marco", "surname": "van Basten", "playerjerseyname": "van Basten"},
    "face": {"headassetid": 192181, "hashighqualityhead": 0,
             "headclasscode": 0, "headtypecode": 2508, "headvariation": 0},
    "detach_name_dictionary": true
  },
  "contract": {"transfersum": 0, "wage": 5000, "months": 60},
  "verify": {"name": true, "team": true, "fields": true}
}
```

`strategy` is `"dummy_overwrite"` (default, no `CreatePlayer`) or `"create"` (opt-in, routes
through the `create_player` invariants).

Step plan — each row is one resumable step (§6):

| # | Step | Yield policy | Notes |
| --- | --- | --- | --- |
| 1 | `resolve_team` | budget | `teamid` or `career.user_teamid()` |
| 2 | `pick_dummy` | budget | first pool entry passing `dummy_filter`; index lookup, no scan |
| 3 | `write_fields` | budget | `db.set_many`, verified |
| 4 | `write_face` | budget | only if `player.face` present |
| 5 | `detach_name_dict` | budget | zero `firstnameid`/`lastnameid`/`commonnameid`/`playerjerseynameid`, set `usercaneditname=1` |
| 6 | `write_names` | budget | `editedplayernames` upsert, duplicates reported |
| 7 | `transfer` | **event** | `TransferPlayer`; hard yield so CM settles |
| 8 | `verify_team` | budget | `GetTeamIdFromPlayerId(pid) == teamid` |
| 9 | `reapply_fields` | budget | only fields whose read-back now differs |
| 10 | `verify_name` | budget | `GetPlayerName(pid)` compared to the requested display name |

Step 10 is the one that turns the §0.4 silent failure into a reported one: today the job logs
`name_verify GetPlayerName=[Zidane]` and then writes `ok=true`. Here a mismatch is
`failures: [{"op":"add","step":"verify_name","reason":"name_not_applied","expected":"Marco van Basten","actual":"Zidane"}]`
and `ok: false`.

Step 9 replaces the current unconditional "re-apply everything twice" (two extra full scans)
with "re-apply only what actually differs", which is usually zero fields.

### 3.6 Ops: `transfer`, `loan`, `release`, `terminate_loan`, `list_player`

One module, five kinds, because they share the presigned/loan pre-clearing dance that
`career_ops.py` copy-pastes into every generator.

```json
{"op":"transfer","id":"t1","playerid":158023,"to_teamid":2,"from_teamid":0,
 "transfersum":0,"wage":5000,"months":60,"release_clause":-1,
 "clear_presigned":true,"clear_loan":true,"verify":true}

{"op":"loan","id":"l1","playerid":158023,"to_teamid":2,"months":12,
 "loan_to_buy":-1,"from_teamid":0,"clear_presigned":true,"verify":true}

{"op":"release","id":"r1","playerid":158023,"verify":true}

{"op":"terminate_loan","id":"tl","playerid":158023}

{"op":"list_player","id":"ls","playerid":158023,"list":"transfer"}
```

`list` is `"transfer" | "loan" | "none"`. `"none"` calls `RemovePlayerFromLists`. The core
enforces the `DOC.MD` warning that a player must not be on both lists — asking for
`"transfer"` removes them from the loan list first.

`verify: true` means: after the call, re-read `GetTeamIdFromPlayerId(playerid)` and require it
to equal `to_teamid`. `TransferPlayer` returns `void`; today "success" means "pcall did not
throw", which is a claim about Lua, not about the save.

### 3.7 Op: `budget`

```json
{"op":"budget","id":"b1","target":"user","action":"set","amount":50000000,"verify":true}
{"op":"budget","id":"b2","target":"user","action":"get"}
{"op":"budget","id":"b3","target":"cpu","action":"get"}
```

The knowledge that `SetTransferBudget`/`GetTransferBudget` are deprecated FC 26 stubs and the
real functions are `Set/GetUserTransferBudget` and `Get/SetCPUTransferBudget` lives here, in
one place, instead of in a comment inside `career_ops.py`. `get` puts the value in
`result.ops[].data.amount`.

### 3.8 Op: `bulk_edit`

The op most likely to blow the frame budget, so it is chunked unconditionally.

```json
{
  "op": "bulk_edit",
  "id": "b1",
  "table": "players",
  "where": [
    {"field": "overallrating", "op": ">=", "value": 80},
    {"field": "potential", "op": "<", "value": 85}
  ],
  "scope": {"team": 2},
  "set": {"potential": 90},
  "limit": 2000,
  "chunk": 400,
  "growth_mirror": "auto"
}
```

- `where` — conjunctive only. Operators `== != < <= > >=`. No expression language; anything
  more complex is Python's job (compute the id list and send `scope.playerids`).
- `scope` — `{"team": N}` (resolved via `teamplayerlinks`, using the squad index),
  `{"playerids": [...]}`, or absent (whole table).
- `chunk` — records examined per step. The runner may reduce it if a step overruns `budget_ms`
  (adaptive: halve on overrun, cap at 25).
- `limit` — hard ceiling on modified records; exceeding it fails the op rather than silently
  stopping.

### 3.9 Op: `export_squad`

Replaces `bridge/export_user_squad.lua` and the `OUT_PATH` regex rewrite in
`src/squad_export.py`.

```json
{
  "op": "export_squad",
  "id": "e1",
  "out": "C:/Users/prated/Desktop/FC 26 LE v26.3.5/LE_Profile_Executor/current_squad.json",
  "include": ["squad", "jersey_numbers", "free_agents"],
  "fields": ["playerid","name","position","overallrating","potential","jerseynumber"],
  "free_agents": {"teamid": 111592, "limit": 40, "order": "ovr_asc", "max_playerid": 460000}
}
```

The current template does two full `players` walks with a `scanned < 30000` cap and builds
JSON by string concatenation. The op does **one** pass driven by the index, and emits via
`LEC.json.encode`, so a quote in a team name cannot corrupt the file.

`out` is validated: it must be an absolute path under a directory the core is configured to
write to (the queue dir's parent, by default). A descriptor cannot make the core write to
arbitrary locations on disk.

### 3.10 Op: `snapshot`

The undo primitive, and the thing that makes `atomic` possible.

```json
{
  "op": "snapshot",
  "id": "s1",
  "out": "C:/.../snapshots/apply_158023_20260726T102200Z.json",
  "table": "players",
  "targets": [158023],
  "fields": ["overallrating","potential","finishing"],
  "include_growth": true
}
```

`fields: "*"` captures every field in the table schema for those targets. Restoring is not a
new op — it is a `set_fields` job built by Python from the snapshot file, which means restore
goes through the same validation and verification as any other write.

The envelope's `snapshot_to` is sugar: the runner synthesises this op as op #0 with `fields`
computed as the union of every field the job's `set_fields`/`bulk_edit` ops will touch.

### 3.11 Op: `growth_sync`

```json
{"op":"growth_sync","id":"g1","playerid":158023,
 "mode":"from_players_table","only_if_has_plan":true}

{"op":"growth_sync","id":"g2","playerid":158023,
 "mode":"mirror","fields":{"finishing":95,"composure":94}}

{"op":"growth_sync","id":"g3","playerid":158023,"mode":"clear"}
```

`from_players_table` is the interesting one: read every `growth.PLAN_FIELDS` value off the
players row and push it into the development plan. That is the standing fix for "the
attributes reverted after a match" without the caller needing to remember what it wrote.

### 3.12 Op: `raw_lua` — the escape hatch

```json
{
  "op": "raw_lua",
  "id": "probe",
  "label": "probe PlayerGrowthManager offsets",
  "reason": "offset 0x40 unverified on 26.3.5; one-off measurement",
  "source": "local m = LEC.career.obj(LEC.career.TYPE.PLAYER_GROWTH)\nreturn {addr = m}",
  "timeout_ms": 3000
}
```

The chunk is loaded with `load(src, "@"..job_id..":"..op.id)` and `pcall`ed exactly as today,
but inside the core's error capture, so its Lua error text reaches the result JSON instead of
dying in the LE log. A returned table is JSON-encoded into `result.ops[].data`.

**When to use it — and when not.**

Use `raw_lua` for:

- probing an undocumented API, offset or struct while developing a new op;
- reproducing a user's bug report verbatim;
- a genuine one-off against one save that will never be repeated.

Do **not** use it for:

- anything a shipped user-facing action performs. If a feature needs it, that is the signal to
  add a real op.
- anything that writes to the DB in a loop. It bypasses the budget and will freeze the thread.

Enforcement, so this does not rot: the core marks any result containing a `raw_lua` op with
`"unsafe": true`, and the Python side asserts in a contract test that no builder reachable
from the GUI or web API emits `raw_lua`. It is available from the developer console only.

### 3.13 How this kills the duplicated code

| Concern | Today | v2 |
| --- | --- | --- |
| Find a player by id | `player_apply.py` T3DB walk capped at 25000; `add_team_lua.py` `GetDBTableRows` ×6; `export_user_squad.lua` two walks capped at 30000 | `LEC.index.find(playerid)` — O(1), one build per session |
| Write a field | inline `pcall(SetRecordFieldValue)` (player_apply) vs `EditDBTableField` on a materialised row (add_team) | `LEC.db.set` — validated, clamped, verified |
| Count successes | `pcall` return | read-back equality |
| Report status | three hand-formatted `key=value` lines with different key sets | one `LEC.status.write` JSON |
| Career-mode guard | `pcall(IsInCM)` copy-pasted into every generator | envelope `require_cm` |
| Dev-plan mirror | a 14-name exclusion list in `player_apply.py` only | `growth.PLAN_FIELDS` positive list, shared |
| Name writing | duplicated in `player_apply.py` and `add_team_lua.py`, subtly different, both broken | one `set_fields` + `upsert` path |
| Presigned/loan pre-clear | duplicated in `career_ops.py` ×2 and `add_team_lua.py` | `ops/transfer.lua` |
| Error text | collapsed to a token and mostly lost | full text + traceback in the result |

The four Python modules shrink from Lua emitters to dict builders:

```python
# src/player_apply.py — after
def build_set_fields_job(card, target_playerid, *, enabled_categories=None,
                         name_parts=None, snapshot_to=None) -> dict:
    updates = player_schema.card_to_field_updates(card, enabled_categories=...)
    ops = [{"op": "set_fields", "id": "stats", "table": "players",
            "key": {"field": "playerid", "value": int(target_playerid)},
            "fields": {f: int(v) for f, v in updates},
            "growth_mirror": "auto", "on_field_error": "continue"}]
    if name_parts:
        ops.append({"op": "set_fields", "id": "names", "table": "editedplayernames",
                    "key": {"field": "playerid", "value": int(target_playerid)},
                    "upsert": True, "fields": _name_fields(name_parts)})
    return {"schema": 1, "job_id": job_id, "core_min": "2.0.0",
            "require_cm": True, "snapshot_to": snapshot_to, "ops": ops}
```

No Lua escaping, no `_lua_str`, no `_lua_comment_safe`, no f-string indentation hazards, and
the whole thing is assertable with `assert job["ops"][0]["fields"]["overallrating"] == 94`.

---

## 4. Result reporting

One file per job: `queue/results/<job_id>.json`, written atomically (write `.tmp`,
`os.remove(dest)`, `os.rename`). A `.partial.json` alongside it is rewritten after **every
step**, so a hard freeze still leaves evidence of exactly which step was executing.

### 4.1 Success with partial field failures

```json
{
  "schema": 1,
  "job_id": "apply_158023_20260726T102200Z",
  "label": "Apply Lionel Messi TOTY to 158023",
  "core_version": "2.0.0",
  "state": "done",
  "ok": false,
  "unsafe": false,
  "started_at": 1784960972,
  "finished_at": 1784960973,
  "duration_ms": 812,
  "steps": {"total": 3, "completed": 3, "resumes": 0, "attempts": 1},
  "counts": {
    "ops_total": 2, "ops_ok": 1, "ops_failed": 1,
    "targets": 1, "found": 1, "missing": 0,
    "fields_requested": 91,
    "fields_written": 88,
    "fields_failed": 3,
    "fields_skipped": 0,
    "growth_mirrored": 62,
    "growth_failed": 0
  },
  "ops": [
    {
      "id": "stats", "op": "set_fields", "ok": false,
      "counts": {"requested": 91, "written": 88, "failed": 3, "growth_mirrored": 62},
      "data": {"record": "0x7FF6C2143A80", "resolved_via": "index"}
    },
    {
      "id": "names", "op": "set_fields", "ok": true,
      "counts": {"requested": 4, "written": 4, "failed": 0},
      "data": {"upserted": false, "duplicates": 0}
    }
  ],
  "failures": [
    {"op": "stats", "target": 158023, "field": "gksavetype",
     "reason": "no_such_field",
     "detail": "field gksavetype not in table players"},
    {"op": "stats", "target": 158023, "field": "trait1", "requested": 268435456,
     "reason": "out_of_range",
     "detail": "value 268435456 outside 0..16777215 (depth 24)"},
    {"op": "stats", "target": 158023, "field": "potential", "requested": 99,
     "readback": 94, "reason": "verify_mismatch",
     "detail": "wrote 99 read 94"}
  ],
  "error": null,
  "index": {
    "state": "hit", "entries": 21437, "rebuilt": false,
    "generation": "7ff6c2100000:21437:328", "build_ms": 0
  },
  "env": {
    "in_cm": true, "manager_career": true, "user_teamid": 2,
    "save_uid": "104cde1628794d4aad00d081fe19343",
    "game_date": {"day": 14, "month": 8, "year": 2026}
  },
  "log": [
    "12.481 index hit entries=21437",
    "12.483 stats resolved 158023 -> 0x7FF6C2143A80 via index",
    "12.502 stats gksavetype rejected: no_such_field",
    "12.610 stats written=88 failed=3"
  ]
}
```

Note what `ok` means: **every op succeeded and every requested field verified**. Three field
failures out of 91 makes the job `ok: false`. There is no reading of this file under which
`written=88` looks like a clean run. That is the point.

### 4.2 Rejected before execution (§7 refusal)

```json
{
  "schema": 1,
  "job_id": "add_team_2_20260726T190000Z",
  "core_version": "2.0.0",
  "state": "rejected",
  "ok": false,
  "started_at": 1784999000,
  "finished_at": 1784999000,
  "duration_ms": 4,
  "steps": {"total": 0, "completed": 0, "resumes": 0, "attempts": 1},
  "counts": {"ops_total": 3, "ops_ok": 0, "ops_failed": 0},
  "ops": [],
  "failures": [
    {"phase": "validate", "op": "add", "reason": "unknown_op_version",
     "detail": "op add_to_team v3 > core supports v2"},
    {"phase": "validate", "op": "mk", "reason": "playerid_ceiling",
     "detail": "playerid 500001 exceeds hard maximum 499999"},
    {"phase": "validate", "op": "stats", "field": "pacediv", "reason": "no_such_field",
     "detail": "field pacediv not in table players (did you mean pacdiv?)"}
  ],
  "error": null,
  "writes_performed": 0
}
```

`"writes_performed": 0` is an explicit, machine-checkable claim that nothing was touched. It
appears on every result and is asserted by the Lua test suite for the rejected case.

### 4.3 Lua error, with the real text

```json
{
  "schema": 1,
  "job_id": "bulk_pot_20260726T191500Z",
  "core_version": "2.0.0",
  "state": "failed",
  "ok": false,
  "steps": {"total": 12, "completed": 7, "resumes": 3, "attempts": 1},
  "counts": {"ops_total": 1, "ops_ok": 0, "ops_failed": 1,
             "targets": 2400, "found": 1610, "fields_written": 1610, "fields_failed": 0},
  "failures": [],
  "error": {
    "phase": "execute",
    "op": "b1",
    "step": 8,
    "message": "imports/t3db/field.lua:63: attempt to perform bitwise operation on a nil value (field 'depth')",
    "traceback": "stack traceback:\n\t.../field.lua:63: in method 'GetInt'\n\t.../field.lua:120: in method 'GetValue'\n\t.../table.lua:153: in method 'GetRecordFieldValue'\n\t.../le_companion/db.lua:88: in function 'db.get'\n\t.../le_companion/ops/bulk_edit.lua:61: in function 'step'",
    "lua_version": "Lua 5.4"
  },
  "partial_progress": {"cursor": {"op_index": 0, "record_idx": 7200}, "resumable": false}
}
```

The full message and traceback survive. The current `collapse_err` squashes whitespace to
underscores and truncates at 200 chars precisely because the text had to survive a
`key=value` line; with JSON that constraint disappears.

### 4.4 Poisoned

```json
{
  "schema": 1, "job_id": "add_team_2_20260725T062900Z", "state": "poisoned", "ok": false,
  "steps": {"total": 10, "completed": 6, "resumes": 2, "attempts": 3},
  "error": {"phase": "quarantine", "message": "max_attempts (3) reached without completion",
            "last_error": "process terminated during step 7 (transfer)"},
  "quarantined_to": "queue/poison/add_team_2_20260725T062900Z.job.json",
  "advice": "step 7 (transfer) has crashed the game 3 times; re-queueing will not help"
}
```

### 4.5 The pointer file and the compatibility shim

`queue/_last_result.json`:

```json
{"schema": 1, "job_id": "apply_158023_20260726T102200Z",
 "path": "results/apply_158023_20260726T102200Z.json",
 "ok": false, "state": "done", "written_at": 1784960973}
```

During migration the core **also** writes the legacy `_last_result.txt` and `_job_status.txt`
so an un-updated Python build still works:

```lua
-- status.lua, removed at the end of Phase 6
function status.write_legacy_shim(res)
    -- NOTE: msg= MUST stay last. apply_service.py:46 matches it greedily to
    -- end-of-line (_STATUS_MSG_RE = r"\bmsg=(.+)$"). Any key after msg= is
    -- swallowed into the message. See §0.10.
    local line = string.format(
        "job=%s id=%s ok=%s found=%s written=%d failed=%d reason=%s",
        res.kind or "job", tostring(res.primary_target or 0),
        res.ok and "true" or "false",
        tostring((res.counts.found or 0) > 0),
        res.counts.fields_written or 0, res.counts.fields_failed or 0,
        res.reason or (res.ok and "ok" or "failed"))
    if res.error then
        line = line .. " msg=" .. LEC.util.collapse_err(res.error.message)
    end
    util.write_file(qpath("_job_status.txt"), line .. "\n")
    util.write_file(qpath("_last_result.txt"), string.format(
        "%s processed=%d ok=%d last=%s", res.ok and "OK" or "FAIL",
        res.counts.ops_total or 0, res.counts.ops_ok or 0, res.job_id))
end
```

Note the shim's `ok` is the v2 semantic (`every requested field verified`), which is stricter
than the legacy `found and written > 0`. That is deliberate: a partial write that the old
pipeline reported as success (§0.1) will start reporting as failure the moment the core takes
over an action, **before** Python is updated. Verify this against
`tests\test_apply_job_status.py` during Phase 3 — the existing case at `:30` asserts an
`ok=true` line surfaces `path`/`id`, and the new stricter `ok` must not break it.

Artifacts the core should stop feeding, because nothing reads them: `_bridge_armed.txt`
(`le_apply.bridge_armed()` at `le_apply.py:500-503` has zero callers), `<job>.lua.meta.json`
and `queue/results/<id>.json` (both write-only, `product.py:243-255`, ~100 orphaned files on
disk). The v2 `results/<job_id>.json` is a *different* file with a real reader, and the
Phase 6 cleanup should delete the write-only pair rather than leave two directories named
`results`.

---

## 5. The player index

### 5.1 What it is

```lua
LEC.index.players = {
    by_id      = { [158023] = 0x7FF6C2143A80, ... },  -- playerid -> record address
    generation = "7ff6c2100000:21437:328",            -- first_record:written_records:record_size
    entries    = 21437,
    built_at   = 12.481,                              -- os.clock()
    valid      = true,
    partial    = false,                               -- true while a chunked build is in flight
    cursor     = 0,                                   -- resume point for a chunked build
}
```

A second, cheaper index exists for squads because it invalidates on different events:

```lua
LEC.index.squads = {
    by_team = { [2] = {158023, 20801, ...} },  -- from teamplayerlinks
    generation = "...", valid = true,
}
```

### 5.2 How to build it

One pass, reading exactly one field per record. This is the whole build:

```lua
-- index.lua
local BUILD_SLICE = 4000   -- records per step when chunked

function index.build_players(budget_deadline)
    local h = LEC.db.open("players")
    h:refresh()
    local gen = h:generation()

    if index.players.partial and index.players.generation ~= gen then
        index.players.partial, index.players.cursor = false, 0   -- table moved mid-build
    end
    if not index.players.partial then
        index.players.by_id, index.players.cursor, index.players.entries = {}, 0, 0
        index.players.generation = gen
    end

    local by_id  = index.players.by_id
    local idx    = index.players.cursor
    local last   = h.written_records - 1
    local base, size = h.first_record, h.record_size
    local n = 0

    while idx <= last do
        local rec = base + size * idx
        if h:is_valid(rec) then
            local pid = h.t:GetRecordFieldValue(rec, "playerid")
            if pid then by_id[pid] = rec; index.players.entries = index.players.entries + 1 end
        end
        idx, n = idx + 1, n + 1
        if n >= BUILD_SLICE then
            index.players.cursor  = idx
            index.players.partial = idx <= last
            if budget_deadline and LEC.util.clock() >= budget_deadline then
                return false            -- yield; resume next step
            end
            n = 0
        end
    end

    index.players.cursor, index.players.partial, index.players.valid = idx, false, true
    index.players.built_at = LEC.util.clock()
    return true
end
```

Notes that matter:

- It reads **only `playerid`**, not the whole record. Contrast `GetDBTableRows("players")`,
  which materialises ~120 field tables per row.
- It is bounded by `written_records`, so there is no `MAX_SCAN` and no possibility of the
  §0.3 bug.
- It is chunkable. The very first job after a save load pays the build incrementally
  across steps rather than in one stall. If the caller passes no deadline (e.g. a manual
  Force Drain where a one-off stall is acceptable and expected), it runs to completion.

### 5.3 Lookup, with staleness proof at the point of use

```lua
function index.find_player(playerid)
    local h = LEC.db.open("players")
    if not index.players.valid or h:generation() ~= index.players.generation then
        return nil, "stale"                     -- caller decides: rebuild or scan
    end
    local rec = index.players.by_id[playerid]
    if not rec then return nil, "absent" end
    -- Cheap proof the address still holds this player. One field read.
    local ok, pid = pcall(function() return h.t:GetRecordFieldValue(rec, "playerid") end)
    if not ok or pid ~= playerid then
        index.players.valid = false
        return nil, "moved"
    end
    return rec
end
```

`h:generation()` re-reads the live table header (`first_record`, `written_records`,
`record_size`) from the table struct address every call. That is three memory reads — cheap
enough to do per lookup, and it catches the §0.7 stale-handle problem that no amount of event
subscription can catch (the game can insert a row without firing an event the core sees).

### 5.4 When to invalidate — the exact event ids

All ids are `ENUM_CM_EVENT_MSG_*` from `lua\libs\v2\imports\career_mode\enums.lua`
(**not** `CONST_CM_EVENTS_NAMES`; see §0.5).

**Hard rebuild — drop everything, including the schema cache:**

| id | Name | Why |
| --- | --- | --- |
| 27 | `DATA_READY` | new DB loaded |
| 29 | `POST_LOAD_PREPARE` | save loaded; every address is new |
| 5 | `ABOUT_TO_INIT_MODE` | mode restarting |
| 6 | `CAREER_TYPE_SELECTED` | new career |

**Invalidate the player index — the `players` row set changed:**

| id | Name |
| --- | --- |
| 58 | `PLAYER_INSERTED_INTO_PLAYERS_TABLE` |
| 59 | `PLAYER_DELETED_FROM_PLAYERS_TABLE` |
| 60 | `PLAYERS_RETIRED` |
| 61 | `ALL_RETIRED_PLAYERS_COMPLETE` |
| 109 | `YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY` |
| 112 | `YOUTH_PLAYER_PROMOTION` |
| 115 | `YOUTH_PLAYERS_RETIREMENT` |
| 23 | `SEASON_RESET` |
| 24 | `SEASON_ENDED` |

**Invalidate the *squad* index only — `teamplayerlinks` / contracts changed, `players` did not:**

| id | Name |
| --- | --- |
| 86 | `TRANSFER_MOVE_COMPLETE` |
| 110 | `PLAYER_ADDED_TO_TEAM` |
| 111 | `PLAYER_REMOVED_FROM_TEAM` |
| 62 | `PLAYER_CONTRACT_TERMINATION` |
| 63 | `PLAYER_CONTRACT_ACCEPTED` |

This distinction matters: a transfer is by far the most common event during normal play, and
invalidating a 21k-entry player index on every transfer would defeat the whole point. A
transfer does not move a `players` record.

**Blackout — never run a job or a scan; the game is serialising or entering a match:**

| id | Name |
| --- | --- |
| 28 | `PREPARE_FOR_SAVE` |
| 30 | `PREPARE_FOR_FIRST_SAVE` |
| 37 | `ABOUT_TO_ENTER_PREMATCH` |
| 42 | `ABOUT_TO_ENTER_A_MATCH` |
| 43 | `FE_TO_BE` |
| 44 | `ABOUT_TO_SIM_OVER_A_MATCH` |
| 45 | `ABOUT_TO_ENTER_QUICK_SIM` |
| 46 | `ABOUT_TO_ENTER_INTERACTIVE_SIM` |
| 47 | `ABOUT_TO_ENTER_PLAY_HIGHLIGHTS` |
| 48 | `ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM` |

This is a real fix, not defensive padding: the current bridge drains on *every*
`post__CareerModeEvent` because it discards `event_id` (§0.6). Draining while the game is
writing a save is a plausible cause of the historic freezes.

**Pump — good moments to advance a chunked job (the game is idle at a menu):**

| id | Name |
| --- | --- |
| 15 | `DAY_PASSED` |
| 16 | `WEEK_PASSED` |
| 26 | `ENTERED_HUB_FIRST_TIME` |
| 39 | `POST_MATCH_REPORTS_DISPLAYED` |
| 68 | `SCREEN_HAS_DONE_LOADING` |
| 82 | `ENTERING_TEAM_MANAGEMENT` |

`events.lua` holds these as four sets and exposes `events.classify(id)` returning
`"hard_reset" | "invalidate_players" | "invalidate_squads" | "blackout" | "pump" | "ignore"`.
Anything not listed is `"ignore"` — the core does not pump on 240 unrelated news events.

### 5.5 Memory cost

Lua 5.4 hash node is `TValue` (16 bytes) + `TKey` (16 bytes) = 32 bytes, and the hash part is
sized to a power of two. For 21,437 entries:

| | |
| --- | --- |
| Nodes allocated | 32,768 (next power of two) |
| `by_id` hash part | 32,768 × 32 B = **1.0 MiB** |
| `Table` struct + metadata | < 1 KiB |
| Squad index (~25 teams × 30 ids, array parts) | ~50 KiB |
| **Total resident** | **~1.1 MiB** |

Compare the thing it replaces. `GetDBTableRows("players")` builds one Lua table per row and
one per field: 21,437 × (1 + ~120) ≈ **2.6 million tables**. At a floor of ~80 bytes per
empty table plus its hash part and the interned key strings, that is comfortably over 250 MiB
of transient allocation and several seconds of GC — which is exactly the 4-5 s per call
measured in §0.4. `add_to_team` does it six times.

So the index costs about 1 MiB of steady-state memory and removes ~25 seconds of stall from a
single job. Even a build measured in whole seconds amortises after two lookups.

**Honest caveat:** the build cost has not been measured on this machine. `lua\libs\v2\tests\timing.lua`
exists precisely for this and should be run first:

```lua
-- lua/libs/v2/tests/timing.lua already walks players and logs "%d rows in players in %.5fs"
```

If a `GetNextValidRecord` walk that reads no fields costs `T`, the index build costs roughly
`T` plus 21k `GetRecordFieldValue` calls. Set `BUILD_SLICE` from that measurement so one slice
lands under ~50 ms, and record the measured `build_ms` in every result's `index` block so the
number is visible in production rather than assumed.

### 5.6 Fallback when the index is stale

Never silently. The resolution ladder, in order:

```
index.find_player(pid)
  ├─ hit            → use it.                          result.index.state = "hit"
  ├─ "absent"       → the id genuinely is not in the table.
  │                   Report found=0, missing=1.       result.index.state = "hit"
  ├─ "moved"        → address no longer holds that pid.
  │                   Mark invalid, rebuild, retry once.
  │                   If it still misses → "absent".   result.index.state = "rebuilt"
  └─ "stale"        → generation changed or never built.
        ├─ budget remaining ≥ est_build_ms → rebuild, retry.  state = "rebuilt"
        └─ budget exhausted → linear scan for THIS lookup only,
             bounded by written_records, and schedule a rebuild
             as the next step.                          state = "scan_fallback"
```

The linear fallback is the same `h:records()` iterator — bounded by `written_records`, so even
the degraded path cannot reproduce §0.3. Every result carries `index.state`, so a support
question ("why was this job slow?") is answered by the result file.

---

## 6. Long-job chunking

### 6.1 The constraint

Only four host events exist and nothing resumes coroutines. There is no frame callback, no
timer, and a blocking loop freezes the game thread. So "resumable" cannot mean
`coroutine.yield` — it must mean **the job's progress is a value on disk, and each host
callback advances it**.

### 6.2 State machine

```
                    ┌──────────────┐
     descriptor ───▶│   queued     │  <job_id>.job.json present, no state file
                    └──────┬───────┘
                           │ pump: validate whole job (§7)
              ┌────────────┴────────────┐
              │                         │
     validation failed          validation passed
              │                         │
        ┌─────▼──────┐          ┌───────▼───────┐
        │  rejected  │          │   running     │◀──────────────┐
        │ writes = 0 │          └───────┬───────┘               │
        └────────────┘                  │                       │
                            ┌───────────┼──────────────┐        │
                            │           │              │        │
                   step() ok, more   step() ok,     step()      │
                   steps, budget     done          threw        │
                   left │            │              │           │
                        │            │              │           │
              ┌─────────▼──┐   ┌─────▼────┐   ┌─────▼─────┐     │
              │ loop within│   │   done   │   │  failed   │     │
              │ same pump  │   └──────────┘   └───────────┘     │
              └─────────┬──┘                                    │
                        │ budget exhausted, or step wants event │
                  ┌─────▼──────────┐                            │
                  │    yielded     │  state file persisted ─────┘
                  │ awaiting_event │  next career event resumes
                  └─────┬──────────┘
                        │ attempts > max_attempts
                  ┌─────▼──────┐
                  │  poisoned  │  moved to queue/poison/
                  └────────────┘
```

### 6.3 The pump

```lua
-- runner.lua
local DEFAULT_BUDGET_MS = 200

function runner.pump(reason, event_id)
    if runner.busy then return "busy" end
    local class = LEC.events.classify(event_id)
    if class == "blackout" then return "blackout" end     -- never drain mid-save/match
    if class == "hard_reset" then LEC.index.reset(); LEC.schema.reset() end
    if class == "invalidate_players" then LEC.index.players.valid = false end
    if class == "invalidate_squads"  then LEC.index.squads.valid  = false end
    if class ~= "pump" and reason ~= "force" then return "not_a_pump_event" end

    runner.busy = true
    local job = runner.active or runner.next_job()
    if not job then runner.busy = false; return "idle" end

    local deadline = LEC.util.clock() + (job.budget_ms or DEFAULT_BUDGET_MS) / 1000

    -- Increment attempts and FLUSH BEFORE executing anything dangerous. A hard
    -- crash must still leave evidence that this job was tried. This is what turns
    -- an infinite crash-loop into a quarantine after 3 tries.
    job.state.attempts = (job.state.attempts or 0) + 1
    if job.state.attempts > (job.max_attempts or 3) then
        runner.quarantine(job, "max_attempts reached without completion")
        runner.busy = false
        return "poisoned"
    end
    runner.persist_state(job)

    local outcome = "yielded"
    repeat
        local ok, done, want_event, err = job:step(deadline)
        runner.persist_partial(job)
        if not ok then
            runner.finish(job, "failed", err)
            outcome = "failed" ; break
        end
        if done then
            runner.finish(job, "done", nil)
            outcome = "done" ; break
        end
        if want_event then
            job.state.awaiting_event = true
            outcome = "awaiting_event" ; break
        end
    until LEC.util.clock() >= deadline

    if outcome == "yielded" or outcome == "awaiting_event" then
        job.state.attempts = 0            -- progress was made; reset the crash counter
        runner.persist_state(job)
        runner.active = job
    else
        runner.active = nil
    end
    runner.busy = false
    return outcome
end
```

Two details carry most of the value:

1. **`attempts` is incremented and flushed *before* the step runs.** A step that kills the
   process leaves `attempts = N` on disk. Next session it is `N+1`. At 4 it is quarantined.
   Today there is no counter at all (§0.9): the Lua worker rewrites `_pending.txt` with the
   names of jobs still on disk (`le_profile_bridge.lua:285-289`) and Python re-arms every
   4 seconds via `_rewake_job` (`le_apply.py:780-805`), so a job that kills the process is
   retried on every restart, forever. The twelve `poison_*` files in `queue/done/` are a
   human having performed this quarantine by hand, twelve times.
2. **`attempts` is reset to 0 whenever a step completes without crashing.** So a long job
   that legitimately takes 30 steps is not quarantined for being long; only a job that fails
   to complete a *single* step across 3 sessions is.

### 6.4 Yield policy

| Policy | Meaning | Used by |
| --- | --- | --- |
| `budget` (default) | resume within the same pump while wall-clock remains | every DB step, index build, bulk_edit slices |
| `event` | hard yield; do not resume until a real career event arrives | after `TransferPlayer`, `CreatePlayer`, `LoanPlayer` — the CM needs to settle |

This matters because career events only fire when the CM timeline advances. A user sitting in
the hub may generate none. With `budget` as the default, such a user still completes
everything except the steps that genuinely require the game to move. And the result says so:
`"state": "awaiting_event"` with `"awaiting": {"since": 1784961002, "step": 7, "reason": "post_transfer_settle"}`
— rather than reporting success it has not earned, or hanging with no explanation.

Python surfaces this honestly: *"Transfer applied. Waiting for the game to advance one day to
verify — press Continue in Career Mode."*

### 6.5 State file

`queue/state/<job_id>.state.json`:

```json
{
  "schema": 1,
  "job_id": "add_team_2_20260726T190000Z",
  "state": "awaiting_event",
  "attempts": 0,
  "cursor": {
    "op_index": 0,
    "step": 7,
    "step_name": "transfer",
    "record_idx": 0,
    "dummy_index": 1
  },
  "resolved": {"playerid": 85308, "teamid": 2},
  "resume_token": "7ff6c2100000:21437:328",
  "started_at": 1784961000,
  "last_step_at": 1784961002,
  "counts": {"fields_written": 74, "fields_failed": 0},
  "failures": [],
  "awaiting": {"since": 1784961002, "reason": "post_transfer_settle"}
}
```

**Invariant: no record address is ever persisted.** The cursor stores `playerid` and
`record_idx`, never `0x7FF6C2143A80`. On resume, `resume_token` is compared to the live
`h:generation()`; if it differs, the job re-resolves every target through the index and
restarts the current op's record cursor from 0. This is the rule that makes resumption safe
across the insert/delete that §0.7 warns about.

### 6.6 Adaptive chunk sizing

A step that overruns its deadline halves the op's `chunk` for the next step (floor 25) and
records it. A step that finishes in under a third of the budget increases it by 50% (ceiling
`chunk` from the descriptor). This is one integer in the state file and it makes the core
self-tuning across machines without anyone measuring anything:

```json
"tuning": {"chunk": 200, "last_step_ms": 61, "overruns": 1}
```

### 6.7 What add_to_team looks like after this

Before (§0.4): one `pcall`, 22 s of frozen thread, six full materialisations, `ok=true` while
the name was never applied, and a crash re-runs it forever.

After: 10 steps, each bounded to ~200 ms, one hard yield after the transfer, no
`GetDBTableRows` on `players` at all, `ok` gated on `GetPlayerName` and
`GetTeamIdFromPlayerId` actually reflecting the request, and three crashed attempts move the
job to `queue/poison/` with an explanation instead of a twelfth retry.

---

## 7. Versioning and compatibility

### 7.1 How Python detects the installed core

The core writes `queue/_core_info.json` on `post__LEInitDoneEvent` and refreshes it on every
pump. It is the single source of truth for what the in-game side can do.

```json
{
  "schema": 1,
  "core_version": "2.1.0",
  "contract": {"descriptor_schema": 1, "result_schema": 1},
  "ops": {
    "set_fields": 1, "create_player": 1, "add_to_team": 2, "transfer": 1,
    "loan": 1, "release": 1, "terminate_loan": 1, "list_player": 1,
    "budget": 1, "bulk_edit": 1, "export_squad": 1, "snapshot": 1,
    "growth_sync": 1, "raw_lua": 1
  },
  "capabilities": [
    "player_index", "squad_index", "chunked_jobs", "verify_writes",
    "growth_mirror", "snapshot", "atomic_rollback", "aob", "legacy_lua_drain"
  ],
  "limits": {
    "max_playerid": 499999,
    "max_written_records": 65535,
    "default_budget_ms": 200,
    "max_attempts": 3
  },
  "warnings": [],
  "le_version": "26.3.5",
  "game_module": {"name": "FC26.exe", "base": "0x140000000", "size": 168427520},
  "lua": "Lua 5.4",
  "install_path": "C:/Users/prated/Desktop/FC 26 LE v26.3.5/lua/scripts/le_companion",
  "queue_dir": "C:/Users/prated/Desktop/FC 26 LE v26.3.5/LE_Profile_Executor/queue",
  "armed_at": 1784960900,
  "last_pump_at": 1784960972,
  "session_id": "4f2a1c9e"
}
```

`session_id` is regenerated at each arm, so Python can tell "the core is installed but LE was
restarted" from "the core is running and I have been talking to it".

Python side:

```python
# src/core_info.py
def read_core_info(queue_dir: Path) -> CoreInfo | None: ...

def require(info: CoreInfo | None, op: str, min_version: int) -> None:
    if info is None:
        raise CoreNotInstalled(
            "In-game core not found. Install it from Settings -> Install Bridge, "
            "then restart Live Editor.")
    have = info.ops.get(op)
    if have is None:
        raise CoreTooOld(f"This build needs the '{op}' operation. "
                         f"Installed core {info.core_version} does not provide it. "
                         f"Reinstall the bridge.")
    if have < min_version:
        raise CoreTooOld(f"'{op}' v{min_version} required, core provides v{have}. "
                         f"Reinstall the bridge (Settings -> Install Bridge).")
```

Staleness: if `armed_at` predates the LE process start, or `last_pump_at` is older than the
heartbeat window, the app reports "core installed but not armed — restart Live Editor"
instead of queueing a job that will never be picked up.

### 7.2 How the core refuses rather than half-executes

**Two-phase execution. Validate the entire job. Only then execute anything.**

```lua
-- runner.lua
function runner.run(job_desc)
    local errs = LEC.validate.job(job_desc)     -- pure; touches no game memory
    if #errs > 0 then
        LEC.status.write_rejected(job_desc, errs)   -- writes_performed = 0
        return "rejected"
    end
    return runner.execute(job_desc)
end
```

`LEC.validate.job` checks, in this order, accumulating **all** errors rather than stopping at
the first (so one round-trip tells Python everything that is wrong):

1. `schema` is a version the core knows. Unknown → reject, nothing else is checked.
2. `core_min` ≤ `core_version` (semver compare).
3. `job_id` matches `^[A-Za-z0-9_.-]{1,120}$`.
4. `ops` is a non-empty array; every `id` is unique.
5. For each op: `op` is in the registry; `v` ≤ the registry's version for that kind.
6. For each op: required keys present; **no unknown keys** except `x_*`.
7. For each op: types correct (`teamid` is a number, `fields` is a table, ...).
8. Referenced tables exist in `GetDBMeta()`.
9. Every field name in `fields` / `set` / `where` exists in that table's schema.
10. Every integer value is within `min .. min + 2^depth - 1`.
11. `create_player.playerid` ≤ 499999 and, when `on_conflict == "fail"`, does not exist.
12. Output paths (`out`, `snapshot_to`) are absolute and under an allowed root.
13. `atomic: true` is incompatible with any non-reversible op present.
14. `$ref` targets resolve to an earlier op in the same job.

Checks 1-7 and 13-14 are pure and run without touching game memory. Checks 8-12 need the
schema (one `GetDBMeta()`, cached) but perform no writes.

Three properties make the refusal trustworthy:

- **No partial state.** Validation runs before op #1, so there is no "ops 1-2 applied, op 3
  rejected". The result carries `"writes_performed": 0` and the Lua test suite asserts the
  fake memory is byte-identical after a rejected job.
- **Unknown keys are errors, not warnings.** A descriptor from a newer app carrying
  `"cascade": true` that this core does not implement gets rejected, rather than executing a
  subtly different job than the one the app intended. The `x_` escape exists for the cases
  where "ignore it" is genuinely correct.
- **Op versions are per-op, not global.** Adding `add_to_team` v2 does not force a core-wide
  version bump that invalidates every other descriptor. Python asks for exactly what it needs.

### 7.3 Forward and backward compatibility matrix

| App | Core | Behaviour |
| --- | --- | --- |
| old | old | unchanged |
| old | new | works — the core still drains `*.lua` (`legacy_lua_drain` capability is permanent) |
| new | old | `core_info.require()` raises before queueing; the user is told to reinstall, with the specific op named. During migration, the app falls back to the legacy generator for that action. |
| new | none | `CoreNotInstalled`; the app offers to install |
| new | new, LE not restarted | `session_id` / `armed_at` stale → "restart Live Editor" |

The queue directory contract is only ever **extended**. `_pending.txt`, `_run_now.lua`,
`_wake.txt`, `_job_status.txt`, `_last_result.txt` keep their exact current meaning forever.
Every v2 artefact is a new filename. That is what makes every cell above degrade gracefully
instead of corrupting a save.

### 7.4 Install and stamping

`version.lua` is generated at build time:

```lua
return {
  VERSION   = "2.1.0",
  CONTRACT  = {descriptor_schema = 1, result_schema = 1},
  BUILT_AT  = 1784960000,
  SHA256    = "3f9c...",     -- over the concatenated module sources
  OP_VERSIONS = {set_fields = 1, add_to_team = 2, ...},
}
```

The installer writes `SHA256` into `companion_config.json` too. On startup Python compares the
config's expected hash to `_core_info.json`'s reported hash; a mismatch means "someone edited
the installed core by hand or an install was interrupted" and the app offers a clean reinstall.

This is strictly stronger than today, where **no version stamp or checksum exists anywhere**:

- `le_apply._bridge_source_text_with_abs_queue` (`le_apply.py:61-87`) rewrites exactly one
  line (`^local QUEUE_DIR\s*=\s*.*$`) and records nothing about what was installed.
- `ensure_sync_installed` (`le_apply.py:440-465`) papers over the gap by unconditionally
  rewriting `lua/scripts/00_le_companion_bridge.lua` on every startup — which works only
  because the app and the bridge ship together.
- `PROTOCOL_VERSION = 2` (`protocol.py:33`) is never written into or read from any queue
  file. The only version markers are a header comment (`Protocol (v2):`,
  `le_profile_bridge.lua:3`) and a hand-maintained `v15` string in `add_team_lua.py:558`.
- The single staleness signal is `autorun_status()["points_at_current_bridge"]`
  (`le_apply.py:356-379`), a substring test of the bridge path inside the autorun stub.

Unconditional-rewrite-on-startup stops being viable the moment the in-game side is a
multi-file library with its own release cadence, which is exactly why §7.1 exists.

---

## 8. Testability

The single biggest argument for the resident core is that a Lua *library* can be tested
without FC 26, and a generated Lua *string* cannot. This section is the payoff.

### 8.1 Layout

```
LE_Profile_Executor/tests/
├── lua/
│   ├── run.lua                    harness entry: lua5.4 tests/lua/run.lua
│   ├── harness.lua                ~60 line describe/it/assert (no external deps)
│   ├── mock/
│   │   ├── le_stub.lua            all LE host globals
│   │   ├── fake_memory.lua        byte-addressable RAM as Lua tables
│   │   ├── fake_db.lua            lays out synthetic T3DB tables in fake memory
│   │   └── fixtures/
│   │       ├── dbmeta.json        trimmed real GetDBMeta() dump
│   │       └── players_25001.lua  generator for a 25,001-row players table
│   └── spec/
│       ├── schema_spec.lua
│       ├── db_spec.lua
│       ├── index_spec.lua
│       ├── runner_spec.lua
│       ├── growth_spec.lua
│       └── ops_*_spec.lua
└── test_core_contract.py          pytest: descriptors, results, version negotiation, e2e
```

### 8.2 The mock LE shim

The whole trick is that `t3db/field.lua` is **pure arithmetic over `ReadQword`/`WriteQword`**.
Give it a fake memory and it runs unmodified. Nothing about the bit-packing needs the game.

```lua
-- tests/lua/mock/fake_memory.lua
local M = {pages = {}, PAGE = 0x1000}

local function page_of(addr) return addr // M.PAGE, addr % M.PAGE end

function M.byte(addr)
    local p, o = page_of(addr)
    local pg = M.pages[p]; if not pg then return 0 end
    return pg[o] or 0
end

function M.set_byte(addr, v)
    local p, o = page_of(addr)
    local pg = M.pages[p]; if not pg then pg = {}; M.pages[p] = pg end
    pg[o] = v & 0xFF
end

function M.install()   -- define the host globals field.lua/table.lua call
    _G.ReadBytes = function(addr, n)
        local t = {}; for i = 1, n do t[i] = M.byte(addr + i - 1) end; return t
    end
    _G.WriteBytes = function(addr, bytes)
        for i = 1, #bytes do M.set_byte(addr + i - 1, bytes[i]) end
    end
    _G.ReadQword = function(addr)
        local v = 0
        for i = 7, 0, -1 do v = (v << 8) | M.byte(addr + i) end
        return v
    end
    _G.WriteQword = function(addr, v)
        for i = 0, 7 do M.set_byte(addr + i, (v >> (i * 8)) & 0xFF) end
    end
    _G.ReadInteger = function(addr) return ReadQword(addr) & 0xFFFFFFFF end
    _G.ReadShort   = function(addr) return ReadQword(addr) & 0xFFFF end
    -- ReadString / WriteString / WriteInteger / WriteShort likewise
end

function M.snapshot()  -- deep copy, for "assert nothing was written"
function M.equals(snap) -- byte-compare
return M
```

`le_stub.lua` provides the rest, and — critically — **records calls** so tests can assert on
side effects the fake DB cannot model:

```lua
-- tests/lua/mock/le_stub.lua
local S = {calls = {}, handlers = {}, in_cm = true, players = {}, names = {}}

local function record(name, ...) S.calls[#S.calls+1] = {name = name, args = {...}} end

_G.Log        = function(msg) S.calls[#S.calls+1] = {name = "Log", args = {msg}} end
_G.IsInCM     = function() return S.in_cm end
_G.GetDBMeta  = function() return S.dbmeta end
_G.AddEventHandler = function(ev, fn) S.handlers[ev] = S.handlers[ev] or {}
                                      table.insert(S.handlers[ev], fn) end

_G.PlayerExists                    = function(pid) return S.players[pid] ~= nil end
_G.GetPlayerName                   = function(pid) return S.names[pid] or "" end
_G.GetTeamIdFromPlayerId           = function(pid) return (S.players[pid] or {}).team or -1 end
_G.TransferPlayer                  = function(...) record("TransferPlayer", ...)
                                       local pid, to = ...
                                       S.players[pid].team = to end
_G.CreatePlayer                    = function(...) record("CreatePlayer", ...) return (...) end
_G.PlayerHasDevelopementPlan       = function(pid) return S.has_plan[pid] == true end
_G.PlayerSetValueInDevelopementPlan= function(...) record("DevPlan", ...) end
_G.InsertDBTableRow                = function(...) record("Insert", ...) return S.next_row end
_G.GetPlugin                       = function(id) return S.plugins[id] or 0 end
_G.AOBScan                         = function() return S.aob_result or 0 end

-- Deterministic time so budget/chunking tests are not flaky.
S.clock = 0.0
function S.advance(sec) S.clock = S.clock + sec end
_G.__mock_clock = function() return S.clock end   -- LEC.util.clock() indirects through this

-- Fire an event exactly as the DLL would: (events_manager, event_id, event)
function S.fire(ev, event_id)
    for _, fn in ipairs(S.handlers[ev] or {}) do fn(nil, event_id, nil) end
end
return S
```

`LEC.util.clock()` is written as `return (_G.__mock_clock or os.clock)()` — one indirection,
and every timing test becomes deterministic.

`fake_db.lua` builds real T3DB layouts from the fixture schema: it writes the table struct
(`first_record` at +0x30, `record_size` at +0x44, `written_records` at +0x7C, column
descriptors from +0x84 in 0x10 strides) and N records into fake memory, so
`LEC.db.open("players")` and `TABLE:GetRecordFieldValue` run the **real** code path.

Running it: `lua5.4 tests/lua/run.lua`. Lua 5.4 is required (not LuaJIT) — `field.lua` uses
`&`, `|`, `~`, `<<`, `>>`, which are 5.3+ operators.

### 8.3 What the Lua suite must assert

Each of these is a regression test for a bug that has actually shipped.

| # | Spec | Asserts | Guards against |
| --- | --- | --- | --- |
| 1 | `schema_spec` | for every `(min, depth)` in the fixture: writing `min`, `min+1`, `max-1`, `max` round-trips exactly | bit-pack correctness |
| 2 | `schema_spec` | `max+1` and `min-1` are rejected by `validate_value` and **never written** (fake memory unchanged) | §0.1 silent truncation |
| 3 | `db_spec` | with validation bypassed, an out-of-range write is caught by read-back and counted as `failed`, not `written` | §0.1 `written=91` lying |
| 4 | `db_spec` | a 20-char string into a 15-byte field stores the first 15 chars, not `sub(1, 5)` | §0.2 upstream truncation bug |
| 5 | `db_spec` | a string write reports success even though `SetRecordFieldValue` returned `nil` | §0.1 string return value |
| 6 | `db_spec` | `insert_row` returns nil when the stub returns a row with `addr.value == "0"` | §0.4 unreachable guard |
| 7 | `index_spec` | over 25,001 synthetic records, record #25,001 is found | §0.3 the 25000 cap |
| 8 | `index_spec` | `fire("post__CareerModeEvent", 58)` invalidates the player index; `fire(..., 86)` does **not**; `fire(..., 86)` invalidates the squad index | §5.4 wrong-event invalidation |
| 9 | `index_spec` | mutating `written_records` in fake memory makes the next lookup return `"stale"` and rebuild; `index.state == "rebuilt"` in the result | §0.7 stale handles |
| 10 | `index_spec` | a lookup whose address now holds a different playerid returns `"moved"` and rebuilds | address reuse |
| 11 | `runner_spec` | `bulk_edit` over 5,000 records with `budget_ms = 5` completes across N > 1 steps and the final counts equal a single-shot run | §6 chunking correctness |
| 12 | `runner_spec` | no step exceeds `budget_ms` by more than one record's work | frame-budget discipline |
| 13 | `runner_spec` | an op that always throws is quarantined after exactly 3 attempts; `state == "poisoned"`; the job file is in `poison/` | §6.3 the 12 poison jobs |
| 14 | `runner_spec` | `attempts` is persisted **before** the step executes (kill the step mid-run, assert the state file already says 2) | crash-loop detection |
| 15 | `runner_spec` | a job resumed after `resume_token` changes re-resolves targets and does not reuse a stale address | §6.5 address persistence |
| 16 | `runner_spec` | `classify(28)` is `blackout`; a pump on event 28 performs zero DB reads | draining during save-prepare |
| 17 | `validate_spec` | a job whose op #3 has a bad field name performs zero writes; `state == "rejected"`; `writes_performed == 0`; fake memory byte-identical | §7.2 half-execution |
| 18 | `validate_spec` | an unknown key `"cascade"` is rejected; `"x_ui_hint"` is accepted | §3.2 forward-compat |
| 19 | `validate_spec` | `create_player` with `playerid = 500001` is rejected at validation, `CreatePlayer` never called | process-kill invariant |
| 20 | `validate_spec` | `atomic: true` + a `transfer` op is rejected with `atomic_irreversible` | §3.2 honest atomicity |
| 21 | `growth_spec` | `set_fields` on `finishing` with a plan records a `DevPlan` call; on `height` it does not; with no plan and `mode=auto`, none | §2.6 mirror scope |
| 22 | `ops_add_to_team_spec` | when the name stub keeps returning the old name, `ok == false` with `reason == "name_not_applied"` | §0.4 `ok=true` while unnamed |
| 23 | `ops_add_to_team_spec` | the whole op performs zero `GetDBTableRows` calls on `players` | §0.4 the 22-second stall |

Test 23 is worth calling out: it is a *negative* test on the call recorder, and it is the one
that stops the 4-5 s materialisation from creeping back in.

### 8.4 Python-side contract tests

`tests/test_core_contract.py`, plus additions to the existing suite. The current suite has no
golden files anywhere — every Lua assertion is a substring check against freshly generated
text — so Phase 0's golden descriptors are a genuine upgrade, not a like-for-like port.

**Existing tests that must keep passing unchanged** (they encode the queue contract, and §7.3
promises it never changes):

| Test | Asserts |
| --- | --- |
| `test_actions_queue.py:40` | the four-file write: `<job>.lua`, `_run_now.lua`, `_pending.txt`, `_wake.txt` |
| `test_actions_queue.py:69` | `OK processed=1 ok=0` is not a silent success |
| `test_le_apply.py:37` | `_pending.txt` lists non-`_` `*.lua` only |
| `test_le_apply.py:124` | bare `OK idle queue_empty` never counts as applied |
| `test_le_apply.py:143` | exactly one `local QUEUE_DIR =` line survives install |
| `test_le_apply.py:179` | install must not patch `live_editor.lua` |
| `test_protocol_dual_mode.py:130` | `actions.write_lua` and `protocol` agree on `RUN_NOW_NAME`/`PENDING_NAME` |
| `test_review_medium_fixes.py:51,60` | `safe_job_name` rejects traversal; traversal in `_pending.txt`/`_wake.txt` cannot touch files outside the queue |
| `test_v2_fixes.py:216,250,261` | autorun arms with `__LE_COMPANION_SAFE_ARM` and no drain; no invented event names; `post__CareerModeEvent` and `post__LEInitDoneEvent` are registered |

The traversal tests matter more in v2, not less: `out` and `snapshot_to` in a descriptor are
new attack surface, and §7.2 check 12 must be tested with the same inputs
(`..\secret.json`, `C:\\Windows\\x.json`, `sub/dir.json`).

**Tests that must be rewritten, not ported**, because they assert the shape of generated Lua:
`test_card_to_lua.py:38,66,72`, `test_player_editor.py:108,133,139,158`,
`test_add_player_crash_safe.py:40-106`, `test_career_ops.py:13-71`,
`test_import_player.py:122-178`, `test_v2_fixes.py:31-46,196-205`. Each becomes an assertion
about a descriptor dict. Two are worth calling out because they are testing an invariant that
moves into the core: `test_add_player_crash_safe.py` asserts the generated Lua contains no
`CreatePlayer` in auto mode and carries an id-ceiling guard, and `test_v2_fixes.py:31-46`
asserts the `GENERATED_ID` ceiling appears in the Lua text. In v2 those are core invariants
(§3.4) tested by Lua spec 19, and the Python test becomes "the builder does not *ask* for
`create` in auto mode".

**Test isolation gap to close first.** `tests\conftest.py:25-59` has a session-scoped autouse
guard that fails the run if `companion_config.json`, `profiles.json`, `current_squad.json`,
`favorites.json` or `last_apply_snapshot.json` change — but it does **not** cover
`queue/_pending.txt` or `bridge/RUN_BRIDGE_ONCE.lua`, and
`test_le_apply.py:179` rewrites both in the real app root. Extend the guard before adding
tests that write to the queue, or the v2 suite will start corrupting the developer's own
queue directory.

What the new Python tests must assert:

1. **Every builder emits a valid descriptor.** A JSON Schema at
   `docs/schema/job.schema.json` and `docs/schema/result.schema.json`; parametrise over every
   `build_*_job` function and validate. Catches key typos that the core would reject at
   runtime, at pytest time instead.
2. **Golden descriptors.** `tests/golden/apply_158023.job.json` etc., byte-compared against a
   fresh build from a fixed card fixture. Any change to a descriptor becomes a reviewable
   diff — the property the current generated-Lua approach has and must not lose.
3. **Result parsing round-trips.** For each fixture in `tests/fixtures/results/`
   (`done_ok.json`, `done_partial.json`, `rejected.json`, `failed_lua.json`,
   `poisoned.json`, `awaiting_event.json`) assert the UI summary string, the boolean success,
   and that `failures[]` reaches the user with field names intact.
4. **`ok` is never inferred from counts.** Assert that a result with
   `fields_written = 88, fields_failed = 3` is reported as a failure. This is the direct
   regression test for the `written=91` class of bug on the Python side.
5. **Missing result file is a failure, not a success.** Assert that a job whose result file
   never appears within the timeout reports failure with "no result from the game", never
   silence. (The current bridge already has `write_job_error_status` for this; keep the
   behaviour.)
6. **Version negotiation.** Given a `_core_info.json` with `ops.add_to_team = 1` and a builder
   requiring 2, assert `CoreTooOld` is raised, the message names `add_to_team`, and **nothing
   is written to the queue directory**.
7. **No `raw_lua` from user-facing paths.** Walk every builder reachable from `web_api.py` and
   `gui.py` and assert no emitted descriptor contains `"op": "raw_lua"`. (§3.12 enforcement.)
8. **No Lua in descriptors.** Assert emitted JSON contains no `SetRecordFieldValue`,
   `GetDBTableRows`, `pcall`, or `load(` — a guard that a generator has not regressed into
   string-building.
9. **End-to-end through the mock.** The strongest test: pytest builds a descriptor, writes it
   to a temp queue dir, shells out to `lua5.4 tests/lua/run.lua --job <path>` with the mock
   shim, reads the produced result JSON, and asserts the Python parser's verdict. Skip with
   `pytest.mark.skipif(shutil.which("lua5.4") is None)` so CI without Lua still runs the rest.

```python
@pytest.mark.skipif(shutil.which("lua5.4") is None, reason="lua5.4 not installed")
def test_set_fields_end_to_end(tmp_path):
    job = player_apply.build_set_fields_job(CARD_MESSI, 158023)
    (tmp_path / f"{job['job_id']}.job.json").write_text(json.dumps(job))
    subprocess.run(["lua5.4", "tests/lua/run.lua", "--queue", str(tmp_path)], check=True)
    res = json.loads((tmp_path / "results" / f"{job['job_id']}.json").read_text())
    verdict = protocol.summarise_result(res)
    assert verdict.ok is True
    assert verdict.written == len(job["ops"][0]["fields"])
```

That test exercises the descriptor schema, the validator, the schema-driven writer, the bit
packing, the index, the result writer and the Python parser — with no game running.

---

## 9. Migration path

The governing rule: **the user must never be one release away from a broken bridge.** Every
phase is additive, every phase is independently shippable, and every phase has a one-flag
rollback.

```
Phase 0 ─ baseline        no behaviour change, tests only
Phase 1 ─ core installed  idle; drains *.job.json only; legacy bridge untouched
Phase 2 ─ read-only ops   export_squad, snapshot, budget get
Phase 3 ─ single writes   set_fields, transfer/loan/release, budget set
Phase 4 ─ add_to_team     the hard one, opt-in, measured against the legacy path
Phase 5 ─ bulk + growth   bulk_edit, growth_sync
Phase 6 ─ cleanup         delete the Lua emitters; keep the legacy drain forever
```

### Phase 0 — baseline (no behaviour change)

- Add `docs/schema/job.schema.json` and `docs/schema/result.schema.json`.
- Add `tests/lua/` (harness + mock shim + fake memory + fake DB) and get the **existing**
  `t3db` code under test — specifically the bit-pack round trip and the `SetString`
  truncation bug (§0.2). This proves the mock is faithful before anything depends on it.
- Add golden tests for the *current* generated Lua from all four generators, so the migration
  has a reference to diff against.
- Run `lua\libs\v2\tests\timing.lua` in-game once and record the `players` walk time in this
  document. That number sizes `BUILD_SLICE` (§5.2) and every budget default.

Ships nothing to the game. Rollback: n/a.

### Phase 1 — core installed but idle

- Ship `lua/scripts/le_companion/**` and extend `autorun/00_le_companion_autorun.lua` to
  `require` it after the existing bridge load.
- The core registers handlers, writes `_core_info.json`, and drains **only** `*.job.json`.
  Zero such files exist yet, so the core does nothing but heartbeat.
- The legacy bridge is byte-identical and keeps draining `*.lua`.
- Both share one busy flag so they cannot drain concurrently.

Acceptance: `_core_info.json` appears after an LE restart; every existing action still works;
no measurable change in job timing.

Rollback: delete the `require` line from autorun. The core is inert.

### Phase 2 — read-only ops first

Migrate `export_squad` and `snapshot`, plus `budget action=get`. These do not write to the DB,
so a bug costs a wrong JSON file, not a corrupted save.

- `src/squad_export.py` gains `build_export_squad_job()`; the `OUT_PATH` regex rewrite dies.
- Python flag `companion_config.json → "core_ops": ["export_squad", "snapshot", "budget_get"]`.
- For each migrated action: if `core_info.require()` raises, fall back to the legacy generator
  and log `core_fallback=export_squad`.

Acceptance: the exported `current_squad.json` is field-for-field identical to the legacy
export on the same save (a real diff, not a spot check).

Rollback: remove the op from `core_ops`.

### Phase 3 — single-record writes

Migrate `set_fields` (replacing `player_apply.py`'s body) and
`transfer`/`loan`/`release`/`terminate_loan`/`list_player`/`budget set` (replacing
`career_ops.py`).

This is the phase that retires the 25000 cap and the lying `written` counter for the most
common action in the app.

- Keep the legacy generator behind the flag for one full release.
- Every migrated apply sets `snapshot_to`, so undo gets *better* in this phase, not worse —
  a concrete user-visible win that pays for the migration risk.
- Compare: run the same card apply through both paths on a scratch save and diff a
  `fields: "*"` snapshot. They must agree, except where the legacy path was wrong (§0.1) —
  and where they disagree, the core must be demonstrably right.

This is also the phase where the Python wait loop gets simpler, because a result file is
definitive in a way `_last_result.txt` never was. Today `le_apply.wait_until_applied`
(`le_apply.py:725-970`) carries:

| Knob | Value | Why it exists |
| --- | --- | --- |
| `timeout_sec` | 45.0 | guess |
| soft-extend | 4 × 20 s, trigger at 55% elapsed | the bridge cannot say "still working" |
| `hard_cap` | 90.0 | ceiling on the guessing |
| re-arm cadence | every 4.0 s | jobs silently vanish |
| busy-flag staleness | 180.0 s | `_bridge_busy.txt` can be orphaned |
| heartbeat LIVE window | 90.0 s | liveness inferred from an mtime |

Most of that is compensating for a worker that cannot report progress. With
`results/<job_id>.partial.json` rewritten after every step, "still working" is a fact with a
step number and a timestamp, not an inference from a file's mtime. The re-arm loop can be
gated on "no partial file has appeared and no step has advanced in N seconds" instead of a
blind 4-second retry — which is also what stops the re-arm loop from resurrecting a job the
core has deliberately quarantined.

Do **not** delete the heartbeat or the soft-extend in this phase. They still cover the legacy
`*.lua` path, which lives until Phase 6 and, for `legacy_lua_drain`, forever.

Acceptance: `_job_status.txt` and `results/*.json` agree for 20 consecutive real applies;
no `scanned=25000` line ever appears again.

Rollback: `core_ops` flag.

### Phase 4 — add_to_team

The hard one, and the reason for the whole exercise. Ship **disabled by default**.

- Implement `ops/add_to_team.lua` as the 10-step chunked plan (§3.5).
- Opt-in via `"core_ops": [..., "add_to_team"]`, surfaced in the UI as an experimental toggle.
- Acceptance is measured, not asserted:

| Metric | Legacy (measured, §0.4) | Target |
| --- | --- | --- |
| Longest single thread stall | ~5,000 ms | < 250 ms |
| Total wall time | ~22,000 ms | < 3,000 ms |
| `GetDBTableRows("players")` calls | 6 | 0 |
| `editedplayernames` rows created per run | 3 | 1 |
| Reports `ok=true` when the name did not apply | yes | no |
| Retries after a crash | unbounded | 3, then quarantine |

Only when all six columns are met does it become the default. Until then both paths ship and
the legacy one is what runs unless the user opts in.

Rollback: the toggle.

### Phase 5 — bulk and growth

`bulk_edit` and `growth_sync`. `bulk_edit` has no legacy equivalent (today's bulk operations
are separate hand-written scripts in `lua/scripts/`), so it is purely additive. `growth_sync`
`from_players_table` is the standing fix for reverting attributes and should be offered as a
one-click "resync growth plan" action.

### Phase 6 — cleanup

- Delete the Lua-emitting functions from `player_apply.py`, `add_team_lua.py`,
  `career_ops.py`, `squad_export.py`. Keep the modules; they are now descriptor builders.
- Delete the `_job_status.txt` / `_last_result.txt` dual-write shim from `status.lua`.
- **Collapse the duplicated readers that the v1 protocol grew.** These are safe to do only
  once the JSON result is the sole verdict source:
  - three independent `_last_result.txt` parsers (`protocol.py:215-244`,
    `le_apply.py:872-921`, `boost_queue.py:316-344`) → one `results/*.json` reader;
  - two divergent `done/` evidence classifiers (`le_apply._done_artifacts_for` at `:674-709`
    treats `orphan_` as proof and has no `poison_` case; `boost_queue._bridge_artifact_for`
    at `:47-66` rejects all three prefixes) → one, driven by the result file rather than by
    filename archaeology;
  - the write-only artifacts: `_bridge_armed.txt`, `<job>.lua.meta.json`, and the old
    `queue/results/<id>.json` telemetry (§4.5).
- **Keep `legacy_lua_drain` in the core permanently.** It is ~40 lines, it lets any old queued
  job or community script still run, and it means a user who has not updated the app is never
  stranded. Removing it buys nothing.
- Keep `raw_lua`, developer console only.

### What never changes across all six phases

- `queue/_pending.txt`, `queue/_run_now.lua`, `queue/_wake.txt` keep their exact semantics.
- `queue/_bridge_alive.txt` / `_bridge_armed.txt` heartbeats keep their format.
- The `*.meta.json` sidecar convention (`{"version":3,"id":...,"kind":...,"job":...}`) is
  extended with `"descriptor": true`, never restructured.
- The core never deletes anything from the queue directory that it did not write.

That invariant is what makes every phase's rollback a config flag rather than a repair job.

---

## Appendix A — quick reference

**Never do these:**

- `GetDBTableRows("players")` — 4-5 s, ~2.6M Lua tables (§0.4)
- trust `SetRecordFieldValue`'s return value (§0.1, §0.2)
- iterate to `total_records` — use `written_records` (§0.7)
- persist a record address across a yield (§6.5)
- `CreatePlayer` with `playerid >= 500000` — kills the process
- `CreatePlayer` with an existing playerid — crashes
- use `CONST_CM_EVENTS_NAMES` for control flow — it disagrees with the DLL above id 33 (§0.5)
- drain on `PREPARE_FOR_SAVE` (28) or any `ABOUT_TO_ENTER_*` event (§5.4)
- write attributes without mirroring growth XP (§2.6)
- `AOBScan` inside a step budget (§2.8)

**Event ids used by the core** (all `ENUM_CM_EVENT_MSG_*`, `enums.lua`, 276 constants, 0..275):

```
hard reset          5, 6, 27, 29
invalidate players  23, 24, 58, 59, 60, 61, 109, 112, 115
invalidate squads   62, 63, 86, 110, 111
blackout            28, 30, 37, 42, 43, 44, 45, 46, 47, 48
pump                15, 16, 26, 39, 68, 82
everything else     ignore
```

**Handler signature:** `function(events_manager, event_id, event)` — the current bridge
discards all three (§0.6).
