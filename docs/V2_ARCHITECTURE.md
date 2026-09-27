# FC 26 Career Companion — v2 Architecture

**Status:** design blueprint, pre-implementation
**Supersedes:** `docs/PROTOCOL.md` (protocol v2) and `docs/ARCHITECTURE_DUAL_MODE.md`
**Target:** a rebuilt companion. v1 is `main.py` + `src/` (~70 modules, CustomTkinter) + `bridge/le_profile_bridge.lua`.

This is an implementation blueprint. Where a judgement call was made, the rejected
alternative is named inline.

> **Note on the superseded docs.** Both are partly stale and should be deleted, not
> amended, when P1 ships. `ARCHITECTURE_DUAL_MODE.md:58-63` still documents the manual
> "paste the bridge into LE's Lua Engine once per session" ritual as the arming
> procedure — `install_autorun_loader()` (`src/le_apply.py:315-339`) replaced that.
> Neither doc mentions the autorun stub, the registry-driven data-dir lookup, the
> `_bridge_busy.txt` marker, or that `_job_status.txt` is written by the job body rather
> than the bridge. Reading them today will actively mislead.

---

## 0. Ground truth this design is built on

Established by the research phase and re-verified against the installed tree at
`C:\Users\prated\Desktop\FC 26 LE v26.3.5\`.

| Fact | Evidence |
|---|---|
| LE Lua is 5.4.6, full stdlib incl. `io`, `os`, `package.loadlib` | `lua/DOC.MD:8` |
| Memory primitives exist: `ReadBytes/WriteBytes/ReadQword/WriteJMP/AllocateMemory/AOBScan` | `lua/libs/v2/imports/core/memory.lua` |
| **No assembler.** CE-style injection means emitting raw opcode bytes | `memory.lua` has `WriteJMP`, `AllocateMemory`, no `autoAssemble` |
| `SendHTTPRequest` is synchronous/blocking | `lua/libs/v2/imports/http/http.lua:15` |
| T3DB v2 in-memory API: fast reads/writes, **no insert/delete** | `lua/libs/v2/imports/t3db/{db,table,field}.lua` |
| v1 row API can insert/delete: `GetDBTableRows`, `InsertDBTableRow`, `DeleteDBTableRow`, `EditDBTableField` | `lua/DOC.MD:53-58` |
| `GetPlugin` reaches any game service | `t3db/db.lua:40`, `career_mode/helpers.lua:15` |
| `GetManagerObjByTypeId` reaches career manager objects | `career_mode/helpers.lua:7` |
| **Only four host events exist** | `bridge/le_profile_bridge.lua:307-326` |
| `lua/autorun/*.lua` auto-executes at LE startup | `lua/autorun/00_le_companion_autorun.lua` (already shipped) |
| Growth manager reverts attribute edits unless XP mirrored | `PlayerHasDevelopementPlan` / `PlayerSetValueInDevelopementPlan`, `lua/DOC.MD:32-33` |
| `CreatePlayer` with `playerid >= 500000` kills the game process | `src/add_player.py:25-28` (`GENERATED_ID_CEILING = 500000`) |

**Count corrections.** The research phase quoted "276 message types" and "155 manager
objects". Re-measured against the shipped tables: `CONST_CM_EVENTS_NAMES` in
`lua/libs/v2/imports/career_mode/consts.lua` has **289 ids (0..288), 288 named,
1 `UNKNOWN_MESSAGE_*`**; `enums.lua` defines **135 `ENUM_FCEGameModes*` type ids
spanning 19..153**. v2 must never hardcode these counts — it reads the shipped Lua
tables at arm time and reports what it found.

### The event-starvation problem, stated precisely

```
LE process lifetime
├── LEInitDone ─────── pre__/post__LEInitDoneEvent      (fires once)
└── Career Mode
    ├── in the hub, clicking       → CareerModeEvent    (frequent, bursty)
    ├── simulating / advancing day → CareerModeEvent    (very frequent)
    ├── in a match                 → CareerModeEvent    (frequent)
    ├── sitting on the main menu   → NOTHING
    ├── in Kick-Off / FUT / Clubs  → NOTHING
    └── alt-tabbed, game paused    → NOTHING
```

There is no frame hook, no timer, no `os.time`-driven callback. Coroutines exist but
nothing resumes them. A `while true do` poll loop inside a handler freezes the render
thread. **Therefore: the host drives timing, and the companion cannot pretend otherwise.**
Section 3 makes this a first-class, user-visible concept rather than something the UI
tries to paper over with a heartbeat.

---

## 1. Goals, non-goals, and safety invariants

### 1.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | Every job gets a result attributable to *that* job | 100% of jobs in `runs/` have a terminal record with matching `job_id` |
| G2 | Semantic success, not "Lua didn't throw" | `_job_status.txt` `found=false` can never surface as OK (today it does — see §3.1) |
| G3 | Honest liveness | Idle-but-armed worker reads ARMED, never OFF |
| G4 | No god object | Zero `app: Any` parameters; `AppState` is a frozen dataclass tree |
| G5 | Real logging | One `logging` config, zero silent `except: pass` in the service layer |
| G6 | Testable without a running game | Executor/Transport/Clock injectable; ≥70% line coverage on `core/` + `domain/` |
| G7 | Career state is first-class | form/morale/sharpness/fitness/release-clause/squad-role/growth-XP readable and writable |
| G8 | Attribute edits stick | Growth-XP mirror on by default for any attribute write |

### 1.2 Non-goals

- **Not** a save-file editor. v2 operates on the live process only.
- **Not** an anticheat/injection tool. LE `Launcher.exe` owns that, permanently.
- **Not** cross-platform. Windows only; registry and process-local file IPC are load-bearing.
- **Not** a replacement for LE's own UI. v2 is a *companion*: it does the things LE's
  Lua Engine makes tedious, not the things LE already does well.
- **No** background daemon that runs when the app is closed. The queue is inert on disk.
- **No** multiplayer / online-mode support. Career Mode only.

### 1.3 Non-negotiable safety invariants

These survive verbatim from v1. Each one exists because it was violated once and
something broke. They are enforced by code **and** by a test, named below.

| ID | Invariant | Enforcement |
|---|---|---|
| **SI-1** | LE Launcher owns anticheat and injection. The companion never swaps `FakeEAAC`, never installs a bypass, never touches `FakeEAACLauncher\`. | `tests/safety/test_si_anticheat.py` greps the whole source tree for `FakeEAAC` outside comments/docs |
| **SI-2** | The companion never replaces or modifies `FCLiveEditor.DLL`. | Hash-pin check at startup in `platform/le_install.py::verify_le_integrity()`; refuses to arm on mismatch |
| **SI-3** | **No auto-arm patch of `live_editor.lua`.** Arming happens *only* via `lua/autorun/`, LE's sanctioned entry point. | `tests/safety/test_si_no_autoarm.py` (port of `tools/verify_no_autoarm.py`): asserts `"AUTOARM" not in live_editor.lua`, `"00_le_companion" not in live_editor.lua`, and `"function LIVE_EDITOR:Load()" in live_editor.lua` |
| **SI-4** | `ReloadPlayersManager` is **never** called. | Emitters are schema-driven (§6.3); the opcode simply has no encoding for it. Plus a lint on every generated job body. |
| **SI-5** | `CreatePlayer` is opt-in per job (`allow_create: true`) and **never** with `playerid >= 500000`. Allocation range is `[460000, 499999]`. | `domain/roster/allocate.py::allocate_generated_playerid()`; job schema validation rejects out-of-range before the file is written |
| **SI-6** | Arming during LE init does handlers-only. Never drain inside `LIVE_EDITOR:Load()` / autorun. | `core/arm.lua` returns immediately when `_G.__LE_COMPANION_SAFE_ARM` is set (this is why v1's `bridge/le_profile_bridge.lua:348-358` exists) |
| **SI-7** | Every path derived from a job file is confined under `queue/`. No `..`, no absolute paths, no drive letters. **Enforced on both sides.** | `core/paths.py::confine()` (ported from `src/protocol.py::confined_job_path`) **and** `core/transport.lua::safe_name()` |
| **SI-8** | No blocking loop inside an event handler. Drain is bounded by a wall-clock budget and a job count. | `DRAIN_BUDGET_MS = 120`, `DRAIN_MAX_JOBS = 8` in `core/executor.lua`; exceeding either defers to the next event |

**SI-2 clarification.** v1 ships `native/le_companion_inject/` producing
`LECompanionInject.dll`, loaded *additively* via `package.loadlib`. That stays legal
under SI-2 — it is an extra DLL alongside LE, never a replacement. v2 keeps it opt-in
and off by default (§6.5).

**SI-7 is currently one-sided, and that is a real hole.** `src/protocol.py` is hardened
— `safe_job_name` rejects `_`-prefixes, `\`, `/`, `..`, drive letters, non-`.lua`, and
anything where `Path(raw).name != raw`; `confined_job_path` additionally resolves and
`relative_to(q)`. **The Lua side has none of it.** `read_pending`
(`bridge/le_profile_bridge.lua:55-67`) checks only the `_` prefix and the `.lua` suffix,
and `path_join(q, name)` will happily accept `../../evil.lua` — which the bridge then
`load()`s and `pcall()`s inside the game process. Anything that can write a line into
`_pending.txt` gets arbitrary code execution in FC 26. v3 closes this structurally
(the transport reads a fixed directory of `<ulid>.json` and executes *data*, never
source), and `core/transport.lua::safe_name()` validates the ULID form regardless.

**SI-3 has history.** `src/le_apply.py:91-273` still contains
`install_autoarm_hook` / `remove_autoarm_hook` / `autoarm_installed` /
`ensure_le_core_clean`, and `lua/libs/v2/imports/core/live_editor.lua.companion_bak`
still sits on disk — the residue of the patch attempt that froze LE at launch.
`install_bridge()` calls `ensure_le_core_clean()` first, and `tools/verify_no_autoarm.py`
asserts the scrub. v2 keeps the *scrubber* (users may have a patched install) but ships
no installer for the patch at all. The two arm mechanisms must never be conflated:
patching `live_editor.lua` is **dead**; `lua/autorun/` is **the only** arm path.

---

## 2. The dual-mode split

### 2.1 The principle

> Anything that needs to *observe or mutate live game state* runs inside.
> Anything that needs to *think, remember, or render* runs outside.
> The wire between them carries **intent in** and **facts out** — never Lua source
> that the outside authored blind.

v1 got this backwards. It generated bespoke Lua per job — `src/card_to_lua.py`,
`src/add_team_lua.py`, `src/career_ops.py`, `src/player_apply.py`, `src/undo_apply.py`
are five independent Lua *code generators*. Every new feature meant new Lua text, shipped
by string concatenation, unrunnable in a test, and debuggable only by reading LE's log.
Worse: the resident bridge is a *generic `load()` harness*, so the security and
correctness surface of the whole system is "whatever the string said".

**v2 inverts this: the resident Lua core is a fixed, versioned program. The transport
carries data, not code.**

### 2.2 Responsibility table

| Responsibility | v1 | v2 | Why it moves |
|---|---|---|---|
| Search 339,609-card catalog | Outside | **Outside** | SQLite + Python; no reason to be inside |
| Decide which fields to write | Outside | **Outside** | Pure domain logic, fully testable |
| Emit Lua text for a job | Outside (5 generators) | **Deleted** | Replaced by a JSON op list |
| Resolve `playerid` → DB record | Outside guesses, inside scans | **Inside** | Only the game knows; v1's `scanned=25000 found=false` was a blind scan |
| Read/write `players` table fields | Inside (generated Lua) | **Inside** (`core/db.lua`, schema-driven) | One code path, one bug surface |
| Read/write career state (form/morale/sharpness/fitness/role/release clause) | Inside, ad-hoc pointer chains per script | **Inside** (`career/state.lua`) | Pointer chains belong next to memory primitives |
| Growth-XP mirror after attribute writes | Missing → edits reverted | **Inside**, automatic | Must happen in the same event as the write |
| Verify the write landed (read-back) | Not done | **Inside** | Semantic success (G2) is only knowable inside |
| Squad export | Inside (`bridge/export_user_squad.lua`) | **Inside**, as `op:"squad.export"` | Same, but now a first-class op |
| Job scheduling / retry / dedup | Outside + inside (both, conflicting) | **Outside decides, inside executes exactly once** | v1's dual-write is the whole §3.1 bug |
| Liveness modelling | Outside infers from a heartbeat file | **Inside reports, outside reads** | See §3.4 |
| Snapshots / undo | Outside | **Outside** (inside supplies before-values) | Persistence is an outside concern |
| Profiles / packs | Outside → Lua text | **Outside → op lists** | 51 profiles + 19 packs become data |
| AOB scans / code injection | Not implemented | **Inside** (`mem/patch.lua`) | Only place with `AOBScan` |
| Logging | Nothing (`0` uses of `logging`) | **Both**, correlated by `job_id` | §7 |

### 2.3 Diagram

```
 ┌──────────────────────────── OUTSIDE (Python 3.12) ────────────────────────────┐
 │                                                                               │
 │  ui/            (Tk views — dumb, no domain logic, no `app` passing)          │
 │       ▲ renders AppState │ dispatches Intent                                  │
 │  app/           (AppState store, Intent → Command, presenters)                │
 │       ▲ ApplyOutcome     │ Job                                                │
 │  domain/        (players, career, roster, profiles, packs, catalog)           │
 │       ▲                  │                                                    │
 │  core/          (AppPaths, Clock, Executor, BridgeTransport, JobStore, log)   │
 └───────────────────────────────────┬───────────────────────────────────────────┘
                                     │  protocol v3: one job = one file
                     queue/jobs/<job_id>.json      ── intent ──▶
                     queue/results/<job_id>.json   ◀── facts ───
                     queue/session.json  (arm marker, session-scoped)
                     queue/drain.json    (last-drain age, per drain)
                                     │  (optional) HTTP push  ◀─── §3.6
 ┌───────────────────────────────────┴─── INSIDE (LE Lua 5.4.6, in-process) ─────┐
 │                                                                               │
 │  autorun/00_le_companion.lua   arm only, never drain                          │
 │  core/{arm,executor,transport,log,json}.lua                                   │
 │  ops/  {player,career,roster,squad,db,mem}.lua   ← the op registry            │
 │  db/   {schema,read,write}.lua   ← generic bit-packed T3DB reader/writer      │
 │  career/{state,growth}.lua       ← manager pointer chains + XP mirror         │
 │  mem/  {scan,patch}.lua          ← AOB + raw-opcode injection (opt-in)        │
 └───────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Being honest about timing

The host drives the clock. v2 does not hide this — it exposes it. Three consequences,
all designed for rather than worked around:

1. **The UI never says "applying…" indefinitely.** It says
   `Queued · waiting for the game to tick`. If the app knows the last drain was
   47 s ago and the player is on the main menu, it says so and tells them what to do:
   *"Enter Career Mode or advance a day."*
2. **Jobs have a `deadline_kind`, not a timeout.** `immediate` (drain on next event,
   report stale after N events missed), `next_session` (survives an LE restart),
   `manual` (only on Force Drain). v1 had one 55-second wall-clock timeout
   (`companion_config.json: timeout_live`) that was simply wrong when the user was
   in a menu.
3. **Force Drain stays.** Executing the arm script from LE's Lua Engine calls
   `LECompanion_ForceDrain()` synchronously. It is the escape hatch and always will be,
   because sometimes there genuinely is no event coming.

**Alternative rejected: a native tick provider.** `package.loadlib` can load a DLL that
spawns a Windows thread; that thread could poll `queue/` and do memory writes directly.
Rejected for v2.0 because (a) writing game memory from a non-game thread races the
engine and is a crash generator, (b) it cannot call back into Lua safely, so it would
need its own duplicate of the T3DB layout — a second source of truth, and (c) it makes
SI-2's "we don't touch injection" story much harder to defend. Revisit only if
event starvation proves fatal in practice (§9, R3).

---

## 3. Transport: protocol v3

### 3.1 What is broken in v2 (the protocol, not the app)

v1 writes **two files for one job**: the named job `queue/<stem>_<ts>.lua` (listed in
`_pending.txt`) *and* a copy at `queue/_run_now.lua`. The resident bridge drains
`_run_now.lua` first, then walks `_pending.txt` — and at
`bridge/le_profile_bridge.lua:213` it skips any pending job whose body is byte-identical
to the `_run_now` body it just ran:

```lua
if run_now_ran and run_now_body and body == run_now_body then
    finish_job(q, full, name)
    if Log then Log("[LE_Companion] skip-dup " .. name) end
```

That is the **73% duplicate-skip**. The work *does* happen — but under the name
`_run_now.lua`, so the result line is:

```
OK processed=1 ok=1 last=_run_now.lua
```

The job id is gone. `job_history.json` proves the damage — every recent row has
`"last_result": ""` and outcomes like `"blocked"` / `"queued_live"` with
`"reason": "Queued (no wait)"`. The app literally cannot say what happened.

Second defect, worse: **"OK" only means `load()`/`pcall` did not throw.** Right now, on
this machine:

```
queue/_last_result.txt : OK idle queue_empty
queue/_job_status.txt  : job=edit id=48940 found=false written=0 dev=false scanned=25000 names=false name=Petr Čech
```

The job scanned 25,000 records, found nothing, wrote nothing — and the protocol's
authoritative channel says OK.

**And here is the part that makes this a protocol bug rather than a knowledge gap: the
job already knew.** `src/player_apply.py:200-213` computes a genuinely good status:

```lua
local reason = "ok"
if not found then                 reason = "not_found"
elseif written == 0 then          reason = "all_writes_failed"
elseif failed > 0 then            reason = "partial_write_failures"
elseif not names_ok then          reason = "name_write_failed" end
```

…and emits `job=edit id=%d ok=%s found=%s written=%d failed=%d devfailed=%d dev=%s
scanned=%d names=%s names_ok=%s reason=%s name=%s`. That is a five-value outcome enum
with per-field failure counts. **The protocol has nowhere to put it**, so it goes to a
shared side-file that the bridge itself cannot see, gets regex-scraped by
`src/apply_service.py:32-109` (78 lines), and is used to retro-actively flip an already
"successful" `ApplyResult` to `outcome="error"` (`apply_service.py:215`).

So v3 is not inventing semantic success — it is **giving the answer v1 already computes
a first-class place to live**, per op, attributed to a job id. `reason` maps almost
directly onto `ApplyOutcome` (§4.4): `ok`→`APPLIED`, `not_found`→`NO_OP`,
`all_writes_failed`→`FAILED`, `partial_write_failures`→`PARTIAL`,
`name_write_failed`→`PARTIAL`.

Note also that `_job_status.txt` is **not** written by the bridge, despite
`docs/PROTOCOL.md:31` listing it as inject-side. It is written by the *generated job
body* (`player_apply.py:86` bakes the absolute path in; `add_player.py:796` for
add_to_team), and `src/protocol.py:418-420` writes a **third, tab-separated** format
from the Python dry-drain. One filename, three producers, two incompatible formats.

Third: liveness. `src/le_apply.py:50` sets `HEARTBEAT_LIVE_SEC = 90.0` and
`bridge_alive()` returns False when `_bridge_alive.txt` is older than that. But the
bridge only writes a heartbeat *inside a drain*, and a drain only happens on a Career
Mode event. **A perfectly healthy, correctly armed worker reads OFF the moment the user
stops clicking for 90 seconds.** Conflating "armed" with "recently ticked" is the bug.

A quieter defect in the same area: liveness reads the file's **mtime**, never the
`alive <unix> <note>` timestamp *inside* it (`protocol.py:204-213`,
`le_apply.py:489`). Mtime is the wrong clock — a backup tool, an AV scan, or a folder
sync can refresh it and manufacture a fake heartbeat, and clock skew producing a
negative age reads as *not* alive. v3 parses the content and ignores mtime entirely.

### 3.2 v3 layout: one job, one file, one result file

```
queue/
├── session.json                 # written once per LE session at arm; the ARM marker
├── drain.json                   # rewritten at the end of every drain; the TICK marker
├── jobs/
│   └── 01JQ8F3K2R7XZ.json       # one job, one file. ULID name == job_id.
├── claimed/
│   └── 01JQ8F3K2R7XZ.json       # atomically renamed here at claim time
├── results/
│   └── 01JQ8F3K2R7XZ.json       # written last; terminal once outcome ∈ TERMINAL (§4.4)
└── archive/
    └── 2026-07-26/…             # jobs + results, rotated by day
```

**Job ids are ULIDs** (`01JQ8F3K2R7XZ…`): 26 chars, lexicographically sortable by
creation time, collision-free, filesystem-safe. This replaces v1's
`{safe_stem}_{YYYYMMDDTHHMMSSZ}.lua`, which collided within the same second and carried
no identity beyond a human label. *Alternative rejected: UUID4 — not sortable, so the
drain order would need a separate index file.*

**There is no `_run_now.lua` and no `_pending.txt`.** The directory listing *is* the
pending list. `os.time`-ordered drain falls out of ULID sort order. This deletes
`le_apply.rebuild_pending()`, `protocol.rebuild_pending()`, `_pending_cache`,
`invalidate_pending_cache()`, `clear_stale_jobs()`, and the `_wake.txt` channel —
about 400 lines of v1.

**Claim is a rename, and rename is the lock.** `os.rename(jobs/X.json, claimed/X.json)`
is atomic on NTFS and fails if another drain already took it. This is how "exactly once"
is enforced without a lock file. If the process dies mid-job, the file is left in
`claimed/` — the next arm sweeps `claimed/` older than the current session id and emits
a `crashed` result for each (§3.5). v1 had no crash story at all; jobs simply vanished.

### 3.3 The job contract

`queue/jobs/<job_id>.json` — written **atomically** (write to `<job_id>.json.tmp` in the
same directory, `os.replace()`). The inside never sees a partial file.

```jsonc
{
  "v": 3,
  "job_id": "01JQ8F3K2R7XZ4M9QW1YV6HB0",
  "created_utc": "2026-07-26T20:41:07.412Z",
  "label": "Messi → OVR 94",          // human string, for logs/UI only
  "origin": "ui.cards.apply",          // which surface produced it
  "deadline_kind": "immediate",        // immediate | next_session | manual
  "requires": {
    "career_mode": true,               // refuse + report `not_in_career` if false
    "save_uid": "4a7f…",               // refuse if the user loaded a different save
    "core_version": ">=3.0.0"
  },
  "grants": {                          // explicit capability opt-in — SI-5, SI-8
    "allow_create_player": false,
    "allow_delete_row": false,
    "allow_memory_patch": false
  },
  "ops": [
    { "op": "player.resolve",  "playerid": 158023 },
    { "op": "player.read",     "fields": ["overallrating", "acceleration"] },
    { "op": "player.write",
      "fields": { "overallrating": 94, "acceleration": 91 },
      "mirror_growth_xp": true,
      "verify": "readback" },
    { "op": "career.set", "form": 10, "morale": 100, "sharpness": 100 }
  ]
}
```

Design notes:

- **`ops` is a list of typed dicts, never Lua source.** The resident core has a fixed
  registry `ops/registry.lua` mapping `"player.write"` → a Lua function. An unknown op
  is a clean `unsupported_op` result, not a syntax error at 3 a.m.
- **Ops share a per-job context.** `player.resolve` puts the record address in
  `ctx.player_record`; later ops use it. This is why one job = a list, not one op:
  resolve-then-write must happen in the *same* event, because a T3DB record address is
  only valid until the table reallocates.
- **`grants` is deny-by-default.** SI-5 becomes structural: a job that does not carry
  `allow_create_player: true` cannot reach `CreatePlayer`, no matter what the op list
  says. v1 relied on "the generator wouldn't emit that".
- **`requires.save_uid`** kills a whole class of v1 disaster — applying a job built for
  save A after the user loaded save B. `GetSaveUID` exists (`lua/DOC.MD:122`) and was
  never used for this.

### 3.4 The result contract — semantic success

`queue/results/<job_id>.json`, written atomically, **last**, after everything else. Its
`outcome` field is the only completion signal — terminal once it lands in `TERMINAL`
(§4.4), which is every value except `QUEUED` and `DEFERRED`. No polling of a shared
mutable file, and no inferring completion from a file's mere existence or mtime.

```jsonc
{
  "v": 3,
  "job_id": "01JQ8F3K2R7XZ4M9QW1YV6HB0",
  "session_id": "01JQ8DZ0000000000000000000",
  "outcome": "applied",         // ← the closed enum, §4.4. NOT a free string.
  "started_utc":  "2026-07-26T20:41:09.004Z",
  "finished_utc": "2026-07-26T20:41:09.061Z",
  "event": { "name": "post__CareerModeEvent", "message_id": 15, "message": "DAY_PASSED" },
  "context": { "in_career": true, "save_uid": "4a7f…", "user_teamid": 5,
               "core_version": "3.0.0", "game_ver": "1.0.138.57785" },
  "ops": [
    { "op": "player.resolve", "outcome": "applied",
      "found": true, "record_addr": "0x7FF6…", "scanned": 412, "elapsed_ms": 3 },
    { "op": "player.read", "outcome": "applied",
      "before": { "overallrating": 91, "acceleration": 89 } },
    { "op": "player.write", "outcome": "applied",
      "written": 2, "skipped": 0,
      "verified": { "overallrating": 94, "acceleration": 91 },
      "growth_xp_mirrored": true, "elapsed_ms": 11 },
    { "op": "career.set", "outcome": "partial",
      "applied_fields": ["form", "morale"],
      "failed_fields": [{ "field": "sharpness", "code": "manager_not_found" }] }
  ],
  "counts": { "ops": 4, "applied": 3, "partial": 1, "failed": 0 },
  "diagnostic": "career.set: FCECareerModeMatchSharpnessManager returned 0",
  "log_tail": ["…last 20 core log lines for this job…"]
}
```

**Semantic success rules — the whole point of v3:**

| Rule | Meaning |
|---|---|
| An op that throws → `outcome: "failed"`, with `error_kind` + Lua traceback. | Lua-level failure |
| An op that runs cleanly but changes nothing → `outcome: "no_op"`, **never `applied`**. | Kills `found=false` reading as OK |
| `player.write` with `verify: "readback"` re-reads every field after writing. Any mismatch → `partial` or `failed`, with the actual value. | Kills "wrote it but the growth manager took it back" |
| Job `outcome` is the **worst** op outcome, by the total order in §4.4. Never the last, never the best. | v1 took `ok_n < n` and lost which one failed |
| `no_op` on the job level is a *distinct, visible* outcome in the UI, not a green checkmark. | G2 |

`_job_status.txt` and its regex parser (`src/apply_service.py:32-109`, ~78 lines) are
deleted outright. So is `parse_result_line` (`src/protocol.py:215`) and the
`"OK processed=%d ok=%d last=%s"` string format.

### 3.5 Liveness: two independent facts, never one number

v1 asked one question ("is the heartbeat < 90 s old?") and got a wrong answer whenever
the user idled. v3 tracks two orthogonal facts and shows both.

**`queue/session.json` — the ARM marker. Written once, at arm, by autorun.**

```jsonc
{
  "v": 3,
  "session_id": "01JQ8DZ0000000000000000000",   // new ULID every LE start
  "armed_utc": "2026-07-26T20:38:00.100Z",
  "core_version": "3.0.0",
  "le_pid": 24188,
  "game_ver": "1.0.138.57785",
  "queue_dir": "C:/Users/.../LE_Profile_Executor/queue",
  "handlers": ["pre__CareerModeEvent", "post__CareerModeEvent", "post__LEInitDoneEvent"],
  "capabilities": { "t3db": true, "row_api": true, "memory": true,
                    "http_push": false, "execute_sql": false }
}
```

The outside compares `le_pid` against the running process list. **Armed is true iff the
file exists AND that pid is alive.** No age check — an arm marker does not decay. A
session that ends leaves a stale file; the pid check catches it, and the next arm
overwrites it anyway. *Alternative rejected: an age-based arm TTL — that is exactly the
90-second bug, one level up.*

**`queue/drain.json` — the TICK marker. Rewritten at the end of every drain.**

```jsonc
{ "v": 3, "session_id": "01JQ8DZ…", "drain_seq": 412,
  "at_utc": "2026-07-26T20:41:09.062Z",
  "trigger": "post__CareerModeEvent", "message_id": 15,
  "claimed": 1, "completed": 1, "deferred": 0, "budget_ms_used": 61,
  "queue_depth_after": 0 }
```

The outside computes `last_drain_age = now - at_utc`. **This is a diagnostic, not a
health verdict.** A big age plus a live pid plus an empty queue means "armed and idle,
which is correct". A big age plus a **non-empty** queue means "jobs are waiting for an
event" — and *that* is the only condition worth nagging about.

**The status matrix the UI renders:**

| session.json | pid alive | queue depth | last drain age | Status pill | Message |
|---|---|---|---|---|---|
| missing | – | – | – | `OFF` | "Start LE — the companion arms itself." |
| present | no | – | – | `OFF` | "LE isn't running." |
| present | yes | 0 | any | `ARMED` | "Ready. Jobs run on the next Career Mode tick." |
| present | yes | >0 | < 10 s | `LIVE` | "Working…" |
| present | yes | >0 | 10 s–2 min | `WAITING` | "N jobs queued — enter Career Mode or advance a day." |
| present | yes | >0 | > 2 min | `STALLED` | "N jobs queued, no game activity for M min. Use Force Drain." |

`ARMED` with an empty queue is a **success state**. In v1 it rendered `OFF`, which is
the single most-reported confusion. This table alone justifies the protocol rewrite.

**Crash recovery.** At arm, `core/arm.lua` scans `claimed/` for files whose
`session_id` differs from the new one and writes a result with
`outcome: "crashed"`, `diagnostic: "claimed by session X, never completed"`. No job
ever disappears silently.

### 3.6 Optional HTTP push (instant results)

File watching costs 60 ms of poll latency (`companion_config.json: poll_sec: 0.06`) and
burns a Python thread. `SendHTTPRequest` gives sub-millisecond delivery — but **it is
synchronous and it blocks the game thread.**

**Design:**

- The Python app binds `127.0.0.1:<ephemeral>` (the existing `src/web_server.py`
  `BaseHTTPRequestHandler` generalises to this) and writes the port into
  `session.json`… no — into `queue/push.json`, written by *Python*, read by Lua at arm.
- The inside pushes **only a notification**, never a payload:
  `POST /v3/notify {"job_id": "...", "session_id": "...", "outcome": "applied"}`.
  The result file is still the source of truth; the push just says "read it now".
- **Hard budget:** the push is attempted once, after `drain.json` is written (so a
  hang cannot cost a job its result), with the whole call inside `pcall` and a
  per-drain cap of one request. If it fails, `push_failed_count` increments in
  `drain.json`, and after 3 consecutive failures the core sets
  `push_enabled = false` in `session.json` and never tries again this session.
- **Off by default.** Enabled per-session only if `push.json` exists and its
  `pid` is alive. If the Python app dies with a socket half-open, the very first
  `SendHTTPRequest` could block the render thread for the OS connect timeout — this
  is the one place v2 can visibly hurt the game, and it must be opt-in with a
  visible toggle. *Alternative rejected: pushing the full result over HTTP — a large
  payload multiplies blocking time for zero benefit, since the file is already written.*

Baseline stays file-watching (`ReadDirectoryChangesW` via `watchdog`, falling back to a
250 ms scan of `results/`). Push is a latency optimisation, never a correctness
requirement.

---

## 4. Python module architecture

### 4.1 Layers

Strict downward dependency. Enforced in CI by `import-linter` contracts in
`pyproject.toml`; a violating import fails the build.

```
┌────────────────────────────────────────────────────────────────┐
│ ui/            Tk views. Render AppState, emit Intent.         │
│                MAY import: app/ (types only)                   │
├────────────────────────────────────────────────────────────────┤
│ app/           Store, reducers, commands, presenters.          │
│                MAY import: domain/, core/                      │
├────────────────────────────────────────────────────────────────┤
│ domain/        Pure logic. players, career, roster, catalog,   │
│                profiles, packs, snapshots. NO I/O.             │
│                MAY import: core/ (types + ports only)          │
├────────────────────────────────────────────────────────────────┤
│ core/          Ports & adapters. AppPaths, Clock, Executor,    │
│                BridgeTransport, JobStore, Store, logging.      │
│                MAY import: nothing above; stdlib only          │
├────────────────────────────────────────────────────────────────┤
│ platform/      Windows specifics: registry, process list,      │
│                LE install probing, file watching.              │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Dependency diagram

```
                      ┌──────────────────────────┐
                      │        ui/ (Tk)          │
                      │  shell, tabs/*, widgets  │
                      └────────┬────────▲────────┘
                    Intent     │        │  AppState (frozen)
                               ▼        │
                      ┌──────────────────────────┐
                      │         app/             │
                      │  Store · Commands ·      │
                      │  Presenters              │
                      └───┬──────────────┬───────┘
                          │              │
              ┌───────────▼──┐      ┌────▼──────────────────┐
              │  domain/     │      │  core/                │
              │  players     │      │  ┌─────────────────┐  │
              │  career      │◀─────┤  │ AppPaths (P)    │  │
              │  roster      │ types│  │ Clock (P)       │  │
              │  catalog     │      │  │ Executor (P)    │  │
              │  profiles    │      │  │ BridgeTransport │  │
              │  packs       │      │  │      (P)        │  │
              │  snapshots   │      │  │ JobStore        │  │
              │  outcome     │      │  │ log             │  │
              └──────────────┘      │  └────────┬────────┘  │
                                    └───────────┼───────────┘
                                                │ implemented by
                                    ┌───────────▼───────────┐
                                    │      platform/        │
                                    │ registry · procs ·    │
                                    │ fswatch · le_install  │
                                    └───────────────────────┘
     (P) = Protocol / port. Real + fake implementations; tests inject fakes.
```

### 4.3 The named seams

Every one is a `typing.Protocol` in `core/ports.py`, with a real and a fake
implementation. This is what makes G6 (test without a game) achievable.

**`AppPaths`** — replaces `src/paths.py`'s 18 module-level functions and, critically,
the **25 call sites that bypass it** with `paths.app_root() / "<literal>"`
(`companion_config.py:43`, `favorites.py:16`, `job_history.py:16`,
`undo_apply.py:18`, `target_players.py:23`, `snapshot_store.py:18`,
`grok_client.py:74`, …). Every user-data file gets a named property; nothing composes
a filename inline.

```python
class AppPaths(Protocol):
    root: Path              # writable, next to the exe   (was paths.app_root)
    resources: Path         # read-only _MEIPASS bundle   (was paths.resource_root)
    le_root: Path           # LE install                  (was paths.le_root)
    le_data_dir: Path | None  # HKLM\SOFTWARE\Live Editor\FC 26 "Data Dir"
    queue: Path; jobs: Path; results: Path; claimed: Path; archive: Path
    universe_db: Path       # card_db/catalog.sqlite  (376 MB, 339,609 rows)
    state_db: Path          # NEW: state.sqlite — replaces 7 loose json files
    logs: Path
    def confine(self, base: Path, name: str) -> Path: ...   # SI-7
```

`TempAppPaths` (a `tmp_path` fixture) makes every filesystem test hermetic. Today
`src/paths.py:143` hardcodes `Path("C:/FC 26 Live Editor")` as a registry fallback —
in v2 that literal lives in `platform/le_install.py` as one clearly-labelled
`_LEGACY_DEFAULT_DATA_DIR`, not in the path resolver.

**`Clock`** — v1 has **36 `time.time()` sites across 13 modules**, 13 of them inside
`le_apply.wait_until_applied` alone, plus `time.sleep`, `st_mtime`, and two
byte-identical `_timestamp()` helpers (`actions.py:22`, `protocol.py:75`). No timeout
is testable without really sleeping.

```python
class Clock(Protocol):
    def now(self) -> datetime: ...          # tz-aware UTC, always
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...
    def stamp(self) -> str: ...             # the ONE ISO-8601 formatter
class FakeClock(Clock):
    def advance(self, seconds: float) -> None: ...
```

**`Executor`** — v1 spawns raw `threading.Thread(daemon=True)` from
`src/ui/apply_flow.py:235` and elsewhere, and hand-rolls cancellation with generation
counters on the god object (`app._search_gen`, `app._enrich_gen`, `app._boost_gen`,
`app._busy`, `app._apply_busy`, `app._card_working`, `app._tab_building`).

```python
class Executor(Protocol):
    def submit(self, key: str, fn: Callable[[CancelToken], T]) -> Handle[T]: ...
    def cancel(self, key: str) -> None: ...       # supersedes same-key work
    def on_ui_thread(self, fn: Callable[[], None]) -> None: ...
```

Same-key submit cancels the in-flight one — that *is* the generation counter, done once.
`InlineExecutor` runs synchronously in tests. Every UI callback goes through
`on_ui_thread`, so `app.after` disappears from domain code.

**`BridgeTransport`** — the only thing that knows protocol v3 exists.

```python
class BridgeTransport(Protocol):
    def submit(self, job: Job) -> JobId: ...              # atomic write to jobs/
    def result(self, job_id: JobId) -> JobResult | None: ...
    def await_result(self, job_id: JobId, *, deadline: Deadline) -> JobResult: ...
    def cancel(self, job_id: JobId) -> bool: ...          # only if not yet claimed
    def liveness(self) -> Liveness: ...                   # the §3.5 matrix
    def force_drain_snippet(self) -> str: ...             # clipboard escape hatch
class FakeTransport(BridgeTransport):
    def complete(self, job_id: JobId, result: JobResult) -> None: ...
```

`FakeTransport` is what lets the *entire* apply pipeline be tested with no game, no LE,
and no files. v1's equivalent (`src/protocol.py::process_queue_protocol`, gated behind a
`_protocol_dry_run` marker file) tried this but reimplemented the drain in Python — a
second implementation that drifts. The fake asserts on the *contract*, not the drain.

**`typed AppState`** — the replacement for the god object, spelled out in §4.5.

**`ApplyOutcome`** — a closed enum, §4.4.

### 4.4 `ApplyOutcome`: one closed enum

v1 has **nine** competing result shapes (dataclass `ApplyResult` with redundant
`applied`/`queued`/`outcome` fields; five different `wait_until_applied` dicts; ten
`{"ok": bool, …}` variants with drifting keys; the `actions.write_lua` artifacts dict
with no `ok` at all; 40 bare-bool functions; raise-and-catch in `career_ops.py`;
`DrainStats` + a parsed text line; the `parse_job_status` tri-state dict where `None`
is a third truth value; and the pack/batch aggregate). One apply converts between
**six** of them in sequence.

v2 has one, in `domain/outcome.py`, ordered worst-to-best so "worst wins" is `min()`:

```python
class ApplyOutcome(IntEnum):
    CRASHED       = 0   # session died mid-job; found in claimed/ at next arm
    FAILED        = 1   # op raised, or verify read-back disagreed
    REJECTED      = 2   # preconditions unmet: not in career, wrong save, grant denied
    EXPIRED       = 3   # deadline passed with no drain (job never claimed)
    PARTIAL       = 4   # some ops applied, some did not
    NO_OP         = 5   # ran cleanly, changed nothing  ← the found=false case
    APPLIED       = 6   # every op applied and verified
    DEFERRED      = 7   # ran out of drain budget; resumes on the next event  (§6.3)
    QUEUED        = 8   # accepted, never claimed yet

TERMINAL = frozenset(ApplyOutcome) - {ApplyOutcome.QUEUED, ApplyOutcome.DEFERRED}

@dataclass(frozen=True, slots=True)
class JobResult:
    job_id: JobId
    outcome: ApplyOutcome
    ops: tuple[OpResult, ...]
    started: datetime | None
    finished: datetime | None
    context: GameContext | None
    diagnostic: str = ""
    def worst(self) -> ApplyOutcome:
        return min((o.outcome for o in self.ops), default=self.outcome)
```

Rules: **no booleans** (`applied: bool` and `outcome: str` cannot disagree if only one
exists). **No free-text status.** Every presenter is a `match` over the enum, and
`mypy --strict` fails the build on a non-exhaustive match — so adding a member forces
every UI surface to decide what it means. `QUEUED` and `DEFERRED` are the only
non-terminal values, which makes "did this finish?" a one-token `in TERMINAL` check
instead of the three-field dance in `ApplyResult`.

A `DEFERRED` job keeps its entry in `claimed/` and gets a **partial** result file
rewritten on each subsequent drain, carrying the resume cursor. It is the one case where
a result file is mutable — and it is safe because the file is only ever read as "not
terminal yet" until `outcome` lands in `TERMINAL`. *Judgement: the alternative,
returning the job to `jobs/`, loses the cursor and restarts a 22,000-row scan from zero
every event — which under event pressure means it never finishes.*

### 4.5 What replaces the god object

`PremiumApp(ctk.CTk)` (`src/gui.py:108`) carries **182 instance attributes** (36 set in
`__init__`, **151 injected from outside by tab modules**), **262 distinct `app.*` names**
referenced across `src/`, and **112 methods of which ~100 are one-line forwarders** like
`def _apply_card(self): return cards_tab._apply_card(self)`. Twelve modules take
`app: Any` (181 occurrences). Fifteen attribute names are read but never assigned
anywhere — latent `AttributeError`s. There is no declaration of the interface; the
closest thing is a docstring in `src/ui/apply_flow.py:1-6`.

It is replaced by **four** things, splitting the 182 attributes by what they actually are:

**1. `AppState` — frozen dataclass tree (`app/state.py`).** All *domain* state.

```python
@dataclass(frozen=True, slots=True)
class AppState:
    bridge:   BridgeState      # Liveness, session_id, queue_depth, last_drain_age
    target:   TargetState      # locked playerid, resolved name, source
    search:   SearchState      # query, year, filters, results: tuple[Card, ...], generation
    editor:   EditorState      # working Card, dirty fields, enabled categories
    squad:    SquadState       # from live sync (§5.3)
    jobs:     JobsState        # active: Map[JobId, JobView], history: tuple[JobRecord, ...]
    catalog:  CatalogState     # index health, row counts, last sync
    prefs:    Prefs            # was companion_config.json
```

Frozen, so a stale reference cannot silently mutate under a background thread — the
`app._hits` / `app._all_hits` / `app._target_hits` / `app._add_team_hits` /
`app._add_team_raw_hits` family (five parallel result lists, each with its own
invalidation bug) collapses into `search.results` plus per-surface selection indices.

**2. `Store` (`app/store.py`).** `dispatch(Intent) -> None`, `subscribe(fn)`,
`snapshot() -> AppState`. Reducers are pure `(AppState, Event) -> AppState`. All
mutation is one function. Commands (`app/commands/`) do the I/O and dispatch Events back.

**3. Per-view `ViewModel` (`ui/tabs/*/model.py`).** All *widget-local* state —
`app._boost_font_title`, `app._last_chrome_key`, `app._cards_edit_built`,
`app._tabs_built`, the `_editor_vars`/`_cat_vars`/`_ps_vars` dicts. These never belonged
in a shared namespace; they belong to one view and die with it.

**4. `Services` bundle (`app/services.py`).** A frozen dataclass holding the §4.3
ports: `paths`, `clock`, `executor`, `transport`, `store`, `log`. Constructed once in
`main.py` and passed explicitly. This is the *only* thing that gets passed around —
and unlike `app: Any` it is fully typed, so `services.transport.submot(...)` is a
type error, not a 3 a.m. `AttributeError`.

```
BEFORE                              AFTER
──────                              ─────
def build(app: Any) -> None:        def build(parent: tk.Widget,
    app._hits = search(...)                   svc: Services,
    app._search_gen += 1                      vm: CardsViewModel) -> None:
    app.status.set("...")               svc.executor.submit("cards.search",
    app.after(0, app._render)               lambda tok: svc.store.dispatch(
                                                SearchRequested(q, tok)))
182 attrs · 262 names · Any         AppState + ViewModel + Services · all typed
```

### 4.6 Module tree

```
companion/
├── main.py                    # composition root: builds Services, starts Store, opens shell
├── core/
│   ├── ports.py               # AppPaths, Clock, Executor, BridgeTransport (Protocols)
│   ├── paths.py               # RealAppPaths + confine()          (SI-7)
│   ├── clock.py               # SystemClock, FakeClock
│   ├── executor.py            # ThreadExecutor, InlineExecutor, CancelToken
│   ├── transport/
│   │   ├── v3.py              # FileTransport — the ONLY protocol-v3 writer/reader
│   │   ├── jobfile.py         # atomic write/read, ULID, schema validation
│   │   ├── watch.py           # ReadDirectoryChangesW + poll fallback
│   │   ├── push.py            # optional HTTP notify listener (§3.6)
│   │   └── fake.py            # FakeTransport
│   ├── store.py               # generic observable store
│   ├── db.py                  # state.sqlite: migrations, atomic tx
│   └── log.py                 # structured logging setup (§7)
├── domain/
│   ├── outcome.py             # ApplyOutcome, JobResult, OpResult
│   ├── job.py                 # Job, Op builders — the ONLY place ops are constructed
│   ├── players/               # schema, field_map, clamp, ovr_formula, positions, playstyles
│   ├── career/                # form/morale/sharpness/fitness/role/clause value objects
│   ├── roster/                # allocate.py (SI-5), create/transfer/loan/release plans
│   ├── catalog/               # universe queries, match/score, enrich
│   ├── profiles/              # 51 profiles as data → op lists
│   ├── packs/                 # 19 packs = ordered profile refs
│   └── snapshots/             # before-state capture, undo plan
├── app/
│   ├── state.py  store.py  services.py
│   ├── intents.py  events.py  reducers.py
│   ├── commands/              # apply_card, run_pack, sync_squad, add_to_team, undo, …
│   └── presenters/            # AppState → view dicts; the ONLY ApplyOutcome match sites
├── ui/
│   ├── shell.py               # window, tabview, status bar. ~150 lines, no domain logic
│   ├── widgets/               # from src/ui/{widgets,badge,skeleton,stepper,empty_state,collapsible}.py
│   ├── player_edit.py         # from src/ui/player_edit.py — already app-independent
│   └── tabs/{home,cards,editor,squad,boost,add_team,catalog,about}/{view.py,model.py}
├── platform/
│   ├── registry.py  procs.py  le_install.py  fswatch.py
└── ingame/                    # the resident Lua core, §6 — shipped as data, installed to LE
```

**Kept nearly as-is from v1** (these are good): `src/ui/player_edit.py` (763 lines,
already app-independent), `src/ui/widgets.py`, `src/player_schema.py`,
`src/field_map.py`, `src/base_players.py`, `src/card_index.py`, `src/ovr_formula.py`.
**Deleted:** `src/gui.py`, all five Lua generators, `src/protocol.py`,
`src/le_apply.py`'s wait/pending machinery, `src/apply_service.py::parse_job_status`.

---

## 5. Data layer

Three stores with genuinely different lifecycles. v1 has them smeared across
`card_db/`, seven loose JSON files at the repo root, and LE's `player_presets/`.

```
┌── PLAYER UNIVERSE ────┐  ┌── FC26 ORACLES ──────┐  ┌── LIVE SAVE STATE ────┐
│ 339,609 cards         │  │ base_players.csv     │  │ current squad         │
│ FIFA 18 → FC 26       │  │  22,348 × 149 cols   │  │ career state          │
│ read-mostly, ~376 MB  │  │ cards.csv            │  │ transfer budget       │
│ rebuilt from CSV/JSON │  │  24,731 × 55 cols    │  │ TTL: seconds          │
│ card_db/catalog.sqlite│  │ ships with LE        │  │ only source: the game │
└───────────────────────┘  └──────────────────────┘  └───────────────────────┘
        historical                 authoritative              volatile
```

### 5.1 The Player Universe (`card_db/catalog.sqlite`)

Verified: 339,609 rows in `cards`, plus a 3-row `meta` table. Schema today:

```sql
CREATE TABLE cards (id INTEGER PRIMARY KEY, year TEXT NOT NULL, name TEXT,
  name_norm TEXT NOT NULL, ovr INTEGER, revision TEXT, revision_norm TEXT,
  playerid TEXT, slug TEXT, source TEXT, payload TEXT NOT NULL);
```

Sources: 17 CSVs (`fifa18…fc26_datahub`, kafagy/FutHead/SoFIFA/official dumps),
`card_db/futbin/*.json`, `card_db/futgg/*.jsonl`, and LE's own
`player_presets/cards.csv`.

**Keep the file and the row format.** Migration cost would be days and the value is
nil. Three changes:

1. **Add `schema_version` to `meta`** and a real migration runner (`core/db.py`).
   Today `card_index.fingerprint()` is the only versioning and it only detects
   *source* change, not *schema* change.
2. **Add FTS5 over `name_norm`.** `card_catalog.search_cards` currently does Python-side
   scoring over `LIKE` results; at 339k rows that is the single biggest UI stall.
3. **Freeze `payload` as a documented shape.** `src/card_types.py::CardRow` is already a
   `TypedDict` — promote it to the validated boundary. Every writer (`futgg_client`,
   `futbin_parse`, `card_catalog.unify_row`) validates before insert, so downstream
   never sees a card missing `year`.

Read path: `domain/catalog/universe.py`, one query API, no module reaching into SQLite
directly.

### 5.2 The FC26 oracles

Authoritative, ships with LE, never written by the companion. Loaded read-only, cached
by mtime.

| Oracle | Source | Facts | Consumer |
|---|---|---|---|
| **Real-head set** | `player_presets/base_players.csv` (22,348 × 149) | The quartet `headassetid` / `headclasscode=0` / that row's own `headtypecode` / `hashighqualityhead`. Critically, `hashighqualityhead` is **not** an "is real face" flag — it selects a mesh pipeline and is 0 for 19,237 of 22,348 rows (Zidane, Pelé, van Basten included). Generic head = `hashighqualityhead=0, headclasscode=1, headassetid=0`. | `domain/roster/face.py` |
| **Nation / league / team maps** | The **live game DB**, not a file. `LE.db:GetTable("nations"/"leagues"/"teams")` | id→name, and team→league membership | Synced in via `op:"db.dump"` (§5.3), cached in `state.sqlite` per save |
| **Role names** | `role1..role5` columns + LE stock script constants (`mass_edit_squadrole.lua`: 1 Crucial, 2 Important, 3 Rotation, 4 Sporadic, 5 Prospect) | squad-role id→label | `domain/career/roles.py` |
| **Position codes** | `player_schema.POS_NAME_TO_CODE` / `POS_CODE_TO_NAME` | already correct | keep |
| **Playstyles** | `player_schema.PLAYSTYLE1` / `PLAYSTYLE2` bitmasks | already correct | keep |
| **Gender encoding** | `base_players.csv` | EA: 0=male, 1=female. **FUT.GG uses 1=male** — never pass through | `domain/catalog/normalize.py` |

`src/base_players.py` already encodes this knowledge well (its module docstring is the
best documentation in the repo). It moves to `domain/players/oracle.py` essentially
intact — but with the CSV parsed once into `state.sqlite` at first run instead of
`lru_cache`'d in memory, so 22,348 × 149 cells stop costing ~40 MB of RSS.

**Nation/league/team maps must come from the live DB, not a shipped file.** v1 tried
hardcoded nation tables and got colliding ids (see `base_players.py` docstring). The
game's own tables are the only truth, and they change with squad updates.

### 5.3 Live save state

The only source is the running game. Modelled as a **cache with a save-scoped key and a
short TTL**, never as durable data.

```
                       op: "squad.export"     ┌──────────────┐
  ui asks for squad ──▶ op: "career.snapshot" │  ONE job     │
                       op: "db.dump" tables=[…]└──────┬──────┘
                                                      │
   state.sqlite                                       ▼
   ┌────────────────────────────────────┐   results/<id>.json
   │ save_snapshot(save_uid, kind,      │◀── ingested by
   │               taken_utc, payload)  │    app/commands/sync_state.py
   └────────────────────────────────────┘
      key = (save_uid, kind) · TTL 120 s · invalidated on any write job
```

Rules:

- **Everything is keyed by `save_uid`** (`GetSaveUID`). Loading a different save
  invalidates the whole cache. v1's `current_squad.json` had no save key at all — it
  would happily show save A's squad while you edited save B.
- **Any successful write job invalidates the affected kinds.** A `player.write`
  invalidates `players`; a `roster.transfer` invalidates `squad` *and* `teams`.
- **The UI renders staleness.** `Squad · as of 40 s ago · refresh` — never a silent lie.
- **`current_squad.json` stops being the interchange format.** It becomes a
  `save_snapshot` row. (An export button that writes a JSON file for the user stays —
  that is a feature, not a data path.)

Career state (form, morale, sharpness, fitness, release clause, squad role, growth XP)
is **not in the game DB** — it lives in career manager structs. It arrives only via
`op:"career.snapshot"` (§6.4) and is cached the same way.

### 5.4 `state.sqlite` — replacing seven loose JSON files

v1 has seven modules each doing `json.loads(p.read_text())` /
`p.write_text(json.dumps(...))` with its own `except (OSError, JSONDecodeError): pass`
and **no atomic write anywhere** — a crash mid-write corrupts `job_history.json` or
`snapshots/index.json`. There is no persistence layer.

| v1 file | v2 table |
|---|---|
| `companion_config.json` | `prefs(key, value_json)` |
| `job_history.json` (array, capped at `MAX_ENTRIES = 40`) | `job_record(job_id PK, …)` — uncapped, §7.2 |
| `snapshots/index.json` + 31 `<sid>.json` (`MAX_SNAPSHOTS = 80`) | `snapshot(id, ts, save_uid, target_id, label, fields_json)` |
| `last_apply_snapshot.json` | a `snapshot` row flagged `is_last_apply` |
| `favorites.json` (`MAX = 80`, ad-hoc key `f"{year}\|{pid}\|{name}\|{rev}"`) | `favorite(year, playerid, revision, card_json)` with a real composite PK |
| `current_squad.json` | `save_snapshot` (§5.3) |
| `.first_run_done` | `prefs` |

`xai_credentials.json` **does not move into `state.sqlite`** — it holds plaintext OAuth
tokens at the repo root today (`access_token`, `refresh_token`). It moves to Windows
DPAPI (`CryptProtectData`) via `platform/secrets.py`, user-scoped. Putting secrets in a
world-readable SQLite file next to the exe would be the same bug with extra steps.

`profiles.json` **stays a file** — it is authored content, not user data, and being
diffable in git is the point.

---

## 6. The in-game Lua core

Installed to `<LE data dir>/lua/` — data dir from
`HKLM\SOFTWARE\Live Editor\FC 26` value `"Data Dir"` (already implemented in
`src/paths.py::le_data_dir`).

### 6.1 Module layout

```
lua/
├── autorun/
│   └── 00_le_companion.lua      ← ~30 lines. Locate core, dofile, arm. Nothing else.
└── le_companion/
    ├── init.lua                 ← module loader (no `require` path assumptions)
    ├── version.lua              ← CORE_VERSION = "3.0.0"
    ├── core/
    │   ├── arm.lua              ← register handlers, write session.json, sweep claimed/
    │   ├── executor.lua         ← claim → run ops → write result. The drain loop.
    │   ├── transport.lua        ← atomic file read/write, dir listing, os.rename claim
    │   ├── json.lua             ← reuse LE's imports/external/json.lua
    │   ├── log.lua              ← ring buffer + Log() passthrough
    │   └── ctx.lua              ← per-job context (save_uid, teamid, record addrs)
    ├── ops/
    │   ├── registry.lua         ← { ["player.write"] = fn, … }  the dispatch table
    │   ├── player.lua           ← resolve / read / write / verify
    │   ├── career.lua           ← form, morale, sharpness, fitness, role, clause
    │   ├── roster.lua           ← create / transfer / loan / release  (grant-gated)
    │   ├── squad.lua            ← export
    │   ├── db.lua               ← generic table read / write / dump
    │   └── mem.lua              ← scan / patch  (grant-gated, off by default)
    ├── db/
    │   ├── schema.lua           ← field descriptors, cached per table
    │   ├── read.lua             ← generic bit-packed reader
    │   └── write.lua            ← generic bit-packed writer + row API bridge
    ├── career/
    │   ├── managers.lua         ← GetManagerObjByTypeId wrappers + struct offsets
    │   ├── state.lua            ← the pointer chains
    │   └── growth.lua           ← the XP mirror
    └── mem/
        ├── scan.lua             ← AOBScan helpers, result caching
        └── patch.lua            ← allocate + emit raw opcode bytes + WriteJMP
```

**`autorun/00_le_companion.lua` arms only — it never drains.** This is SI-6, and v1's
`bridge/le_profile_bridge.lua:346-358` documents exactly why: a full `ForceDrain` during
`LIVE_EDITOR:Load()` freezes LE, because queued jobs execute inside init.

**How the core finds `queue/` — and why v1's answer was wrong.** v1 *rewrites line 14 of
the Lua source at install time*: the template says
`local QUEUE_DIR = "LE_Profile_Executor/queue"` and `install_bridge()`
(`src/le_apply.py:389-390`) substitutes an absolute path, emitting the patched text to
two places. The header even warns `-- INSTALL replaces this single line (do not add a
second QUEUE_DIR)`. Move the companion folder, rename a drive, or run a second install,
and the resident script silently points at a queue nobody writes to — with no error,
because a missing directory just looks like an empty queue.

v2 ships the core **byte-identical for every user** and reads its configuration from a
sibling data file:

```
<LE data dir>/lua/le_companion/config.json   ← written by the Python installer
{ "v": 3, "queue_dir": "C:/…/LE_Profile_Executor/queue", "installed_utc": "…",
  "app_version": "2.0.0", "core_version": "3.0.0" }
```

`core/arm.lua` reads it, verifies the directory is writable, and — critically — **writes
the resolved path into `session.json`**, so the outside can confirm the two agree
instead of assuming. A mismatch surfaces in Diagnostics (§7.3) as
`queue path mismatch: core points at X, app writes to Y`, which is the failure v1 could
not detect at all. *Alternative rejected: source patching — code that differs per
machine cannot be hash-verified, cannot be diffed against the repo, and cannot be
shipped as a signed artifact.*

**Global entry points** (successors to `LEProfileBridge_ForceDrain` /
`_G.LECompanion_ApplyQueue`): `LECompanion.drain()` for the manual Force Drain escape
hatch, `LECompanion.status()` returning the same table that `session.json` holds, and
`LECompanion.selftest()` running the op registry against a synthetic in-memory job.
All three are safe to call from LE's Lua Engine at any time.

### 6.2 The job executor

**Clock discipline first.** v1 mixes two clocks and never notices: `MIN_INTERVAL = 0.08`
and `BUSY_WATCHDOG = 120.0` are compared against `os.clock()` — which in standard Lua is
**CPU time consumed by the process**, not wall time — while `heartbeat()` and
`finish_job()` stamp with `os.time()` (wall seconds). The two are never compared to each
other, which is the only reason it has not produced a visible bug. v2 picks one and
documents it: **`os.clock()` for the intra-drain budget** (it is monotonic and
sub-second, and CPU-time is arguably the *better* measure of "how much game thread did I
just consume"), **`os.time()` for every timestamp written to a file**. Never the reverse,
never mixed in a comparison.

```lua
-- core/executor.lua  (shape, not final code)
local BUDGET_MS   = 120          -- SI-8: never hold the render thread longer
local MAX_JOBS    = 8

local function on_event(event_name, message_id)
  if S.draining then return end                    -- re-entrancy guard
  S.draining = true
  local t0, claimed, done = os.clock(), 0, 0

  for _, name in ipairs(TRANSPORT.list_jobs()) do  -- ULID sort == arrival order
    if (os.clock() - t0) * 1000 > BUDGET_MS or done >= MAX_JOBS then break end

    local path = TRANSPORT.claim(name)             -- os.rename; nil if lost the race
    if path then
      claimed = claimed + 1
      local job = TRANSPORT.read_json(path)
      local res = run_job(job, event_name, message_id)
      TRANSPORT.write_result(job.job_id, res)      -- atomic, written LAST
      done = done + 1
    end
  end

  TRANSPORT.write_drain(event_name, message_id, claimed, done,
                        (os.clock() - t0) * 1000)
  S.draining = false
end
```

`run_job` is where semantic success is produced:

```lua
local function run_job(job, ev, mid)
  local gate = PRECONDITIONS.check(job.requires)   -- career mode, save_uid, core version
  if not gate.ok then return REJECTED(job, gate.code) end

  local ctx, results = CTX.new(job), {}
  for i, op in ipairs(job.ops) do
    local handler = REGISTRY[op.op]
    if not handler then
      results[i] = { op = op.op, outcome = "failed", error_kind = "unsupported_op" }
    elseif not GRANTS.permits(job.grants, op.op) then
      results[i] = { op = op.op, outcome = "rejected", error_kind = "grant_denied" }
    else
      local ok, r = xpcall(handler, debug.traceback, ctx, op)
      results[i] = ok and r
        or { op = op.op, outcome = "failed", error_kind = "lua_error", traceback = r }
    end
    if results[i].outcome == "failed" and job.stop_on_failure ~= false then break end
  end
  return BUILD_RESULT(job, ev, mid, ctx, results)  -- job outcome = worst op outcome
end
```

Every op handler returns a table with an `outcome` field drawn from the same closed set
as §4.4. **A handler that finds nothing returns `"no_op"`, never `"applied"`** — this
one rule is the fix for `found=false` reading as OK.

### 6.3 Schema-driven generic DB reader/writer

LE already implements bit-packed field access in
`lua/libs/v2/imports/t3db/field.lua`. The encoding, verbatim:

```lua
function FIELD:GetInt(record_addr)
    local v = MEMORY:ReadQword(record_addr + self.offset)
    local a = v >> self.startbit
    local b = (1 << self.fld_desc.depth) - 1
    return (a & b) + self.fld_desc.min
end
```

That is:

```
value = ((qword >> startbit) & ((1 << depth) - 1)) + min
```

with `offset = floor(bit_offset / 8)`, `startbit = bit_offset % 8`, and field
`type`: `0` = string, `3` = int, `4` = float (float is an int reinterpreted via
`string.pack`/`unpack`).

**v2 does not reimplement this.** `db/read.lua` and `db/write.lua` wrap
`LE.db:GetTable(name)` and the `TABLE:GetRecordFieldValue` /
`TABLE:SetRecordFieldValue` / `GetFirstRecord` / `GetNextValidRecord` API. What v2 adds
is the layer above:

```lua
-- db/schema.lua
SCHEMA.describe("players")
--> { fields = { overallrating = {type=3, depth=7, min=0, max=127, offset=…, startbit=…},
--                acceleration  = {type=3, depth=7, min=0, …}, … },
--     record_size = …, count = … }        (cached per table per session)
```

Three capabilities that follow for free, and that v1's hand-written Lua could never do:

1. **Range validation before the write.** `depth` and `min` give the exact legal range.
   A job asking for `overallrating = 200` is rejected with
   `{"outcome":"failed","error_kind":"out_of_range","field":"overallrating","max":127}`
   *before* any memory is touched, instead of silently wrapping — which is what
   `SetInt`'s bit loop does today.
2. **Read-back verification.** Write, then `GetRecordFieldValue` the same field. If it
   disagrees, the outcome is `failed` with both values. This is what catches the growth
   manager reverting an edit.
3. **`op:"db.dump"`** — dump any table generically, which is how nation/league/team maps
   reach the outside (§5.2) with no per-table Lua.

**Insert and delete use the v1 row API, not T3DB.** T3DB has no insert/delete. So
`db/write.lua` routes structurally:

| Operation | API | Why |
|---|---|---|
| read field | `TABLE:GetRecordFieldValue` (T3DB) | fast, in-memory |
| write field | `TABLE:SetRecordFieldValue` (T3DB) | fast, in-memory |
| bounded scan | `GetFirstRecord`/`GetNextValidRecord` (T3DB) | no allocation per row |
| **bulk / unbounded lookup** | `GetDBTableRows` (v1) | **see the freeze rule below** |
| **insert row** | `InsertDBTableRow` (v1) | T3DB cannot |
| **delete row** | `DeleteDBTableRowByAddr` (v1) | T3DB cannot |
| single field by row id | `EditDBTableField` (v1) | fallback when T3DB table is unnamed |

**The freeze rule — the single most important operational constraint in the Lua core.**
A full record walk of a large table blocks the game thread long enough to freeze FC 26.
v1 discovered this the hard way and both styles carry the scar tissue in comments:

- `src/player_apply.py:157` — `-- Cap scan: full 20k+ walks freeze FC26; stop early if not found`, with `MAX_SCAN = 25000`, plus `scanned_n < 8000` for `editedplayernames` and `scanned < 30000` for free agents in `bridge/export_user_squad.lua:161`.
- `src/add_team_lua.py:324` — `-- Use GetDBTableRows only (avoid full-table record walks — freezes FC26).`

So v1 has **two disjoint DB access styles chosen per feature**, with the reasoning
living only in comments — the editor path record-walks with a cap, the add-to-team path
uses `GetDBTableRows` exclusively. v2 makes this a property of the schema layer, not of
whoever wrote the script:

| Access | Rule |
|---|---|
| **Point lookup by `playerid`** | Never scan. `db/schema.lua` builds a `playerid → record_addr` index on first use per table per session, and invalidates it on any row-API mutation. A repeated apply to the same player then costs one hash lookup, not `scanned=25000`. |
| **Bounded scan** | Allowed with an explicit `max_records`, and it must fit the §6.2 drain budget. Exceeding the budget mid-scan yields `deferred` — the op resumes at the same record index on the next event, with the cursor in `ctx`. **This is the one place resumability is worth the complexity**, because it is the only unbounded work the core does. |
| **Full-table dump** (`op:"db.dump"`) | Always chunked and always `deferred`-capable. Never attempted in a single event. |

This turns v1's `found=false scanned=25000` — a *capped scan that gave up* — into either
a hit, or an honest `no_op:not_indexed` after a complete pass. The user is never told
"not found" when the truth is "we stopped looking".

`src/add_team_lua.py` also documents the SI-4 rationale inline:
`-- Do not call the players-manager reload API (freezes many FC26 Career sessions)`.

Important consequence: **a T3DB record address is invalidated by any insert/delete.**
`ctx` therefore drops all cached record addresses after any row-API mutation, and
`player.resolve` must be re-run. This is encoded in `ctx.lua::invalidate_addrs()` and is
exactly the kind of rule that a per-job hand-written Lua script gets wrong once and
crashes the game.

**`ExecuteSQL` is not used in v2.0.** It is undocumented and unexplored; making it
load-bearing before anyone knows its transaction semantics would be reckless. It gets a
*diagnostics-only* probe (§7.3) that records its behaviour for a future release.

### 6.4 Career state and the growth-XP mirror

Career state is not in the DB. `career/managers.lua` wraps
`GetManagerObjByTypeId(type_id)` (`lua/libs/v2/imports/career_mode/helpers.lua:7`),
which walks `GetPlugin(ENUM_djb2FeFceGMCommServiceInterface_CLSS)` → `{0x20, 0x10}` →
`+ 0x20*type_id` → `{0x18, 0x0}`, validating an instance count of 1 at each step.

```
GetPlugin(FeFceGMCommServiceInterface)
        │  +0x20 → +0x10
        ▼
   mode_managers[]  ── + 0x20 * type_id ──▶ mode_manager
                                                │ +0x10 must == 1 (has instance)
                                                │ +0x8 → type, +0x10 must == 1
                                                ▼ +0x18 → +0x0
                                          manager object
```

`career/state.lua` maps each career field to `(manager type_id, struct offsets)`, in
**one table**, so a game patch that moves an offset is a one-line data change:

```lua
STATE_MAP = {
  squad_role     = { mgr = ENUM_FCEGameModesFCECareerModePlayerStatusManager,
                     vec = { begin = 0x18, item = { playerid = 0x0, value = 0x4 }, stride = 0x8 } },
  form           = { fn  = "SetPlayerForm" },       -- LE exposes a setter; prefer it
  morale         = { fn  = "SetPlayerMorale" },
  sharpness      = { fn  = "SetPlayerSharpness" },
  fitness        = { fn  = "SetPlayerFitness" },
  release_clause = { mgr = …, chain = { … } },
}
```

Prefer LE's documented setters (`SetPlayerForm`/`Morale`/`Sharpness`/`Fitness`,
`lua/DOC.MD:380-487`) over raw pointer walking wherever one exists. Raw chains are the
fallback for what LE does not expose (release clause, and the `mPlayerID`/`mRole` vector
walk that `helpers.lua::SetSquadRole` already demonstrates). *Judgement: fewer offsets
to re-verify each game patch is worth more than the marginal speed of a direct write.*

**Squad role writes go to two places.** LE's own `mass_edit_squadrole.lua` proves it:
`SetSquadRole(playerid, role)` for the manager struct **and**
`career_playercontract_table:SetRecordFieldValue(rec, "playerrole", role)` for the DB.
Writing one without the other produces a UI that disagrees with the simulation.
`ops/career.lua::set_squad_role` does both, atomically, and reports `partial` if only
one lands.

**The growth-XP mirror.** `PlayerGrowthManager` reverts attribute edits at the next
development tick unless the player's development plan agrees. LE exposes
`PlayerHasDevelopementPlan(playerid)` and
`PlayerSetValueInDevelopementPlan(playerid, …)` (`lua/DOC.MD:32-33`,
`lua/DOC.MD:488+`).

**Correction to the research brief: v1 is not missing this — it is missing it
*consistently*.** `src/player_apply.py:163-196` already gates on `IsInCM()` +
`PlayerHasDevelopementPlan`, sets `use_dev = true` in generated editor jobs, mirrors
each field, and counts `devfailed`. But it exists in **exactly one of the five Lua
generators** — `card_to_lua.py`, `add_team_lua.py`, `career_ops.py` and `undo_apply.py`
write attributes with no mirror at all. And the "which fields must not be mirrored"
knowledge is a hand-maintained inline exclusion chain:

```lua
if fname ~= "modifier" and fname ~= "preferredposition1" and fname ~= "height"
    and fname ~= "weight" and fname ~= "trait1" and fname ~= "trait2"
    and fname ~= "icontrait1" and fname ~= "icontrait2"
    and fname ~= "nationality" and fname ~= "birthdate"
    and fname ~= "runstylecode" and fname ~= "bodytypecode"
    and fname ~= "usercaneditname"
    and not string.find(fname, "preferredposition", 1, true)
    and not string.find(fname, "tattoo", 1, true)
    and not string.find(fname, "hair", 1, true)
    and not string.find(fname, "head", 1, true)
then
```

**In v2 this becomes a schema property, not a string chain.**
`domain/players/schema.py` marks every field `mirrors_growth_xp: bool` (true for the 34
`ATTR_FIELDS` and `overallrating`/`potential`; false for identity, appearance, and
position fields), and `db/schema.lua` receives it as data. One declaration, consulted by
every write path, testable without a game. The exclusion list above becomes a fixture in
`tests/domain/test_growth_fields.py` asserting the two agree.

```lua
-- career/growth.lua
function GROWTH.mirror(ctx, playerid, field, new_value)
  if not PlayerHasDevelopementPlan(playerid) then
    return { mirrored = false, reason = "no_development_plan" }   -- benign; report it
  end
  local ok = pcall(PlayerSetValueInDevelopementPlan, playerid, field, new_value)
  return { mirrored = ok, reason = ok and "" or "set_failed" }
end
```

Wired into `ops/player.lua::write`:

```
player.write
  ├─ validate against schema (depth/min)          → failed:out_of_range
  ├─ capture before-values                        → into result.before (feeds undo)
  ├─ SetRecordFieldValue for each field
  ├─ if op.mirror_growth_xp ~= false:
  │     for each field where schema.mirrors_growth_xp: GROWTH.mirror(...)
  │     ── DEFAULT ON, on EVERY write path. v1 had it on one of five. ──
  ├─ if op.verify == "readback": re-read every field
  └─ outcome = applied | partial | failed | no_op
```

`growth_xp_mirrored` and any `no_development_plan` reason are reported in the op result,
so the UI can say *"Applied — but this player has no development plan, so the growth
manager may revert it after the next match."* v1 said "OK".

**`PlayerDevelopmentManager` is a separate, complementary thing**
(`lua/libs/v2/imports/player_development/player_development_manager.lua`):
`AddPlayer(playerid, xp_multiplier, bonus_xp, no_decline)` persisted to LE's own
`extensions/careers/<SAVE_ID>/players_development.json`. That is the *rate* knob and it
is exposed as `op:"career.development"` — distinct from the mirror, which is about
making a specific edit stick.

### 6.5 The memory / AOB layer

Grant-gated (`allow_memory_patch`), **off by default**, and never used by any shipped
profile in v2.0. It exists because CE-style features (unlimited transfer budget without
touching the DB, disabled negotiation limits, gameplay tweaks) are the most-requested
category and are impossible any other way.

**This layer is entirely greenfield.** A repo-wide grep confirms that v1 calls
`ReadBytes`, `WriteBytes`, `AOBScan`, `GetManagerObjByTypeId` and `GetPlugin`
**zero times** — every companion feature today goes through LE's Lua DB APIs. So §6.4
and §6.5 have no migration story and no legacy behaviour to preserve; they are new
capability, which is exactly why they are phased last (P4, P5) and gated hardest.

Available: `AOBScan(base, size, pattern)`, `MEMORY:AOBScanGameModule(aob)` (uses
`LE_GAME_MODULE_BASE` / `LE_GAME_MODULE_SIZE`), `AllocateMemory(addr, size, 0x3000,
PAGE_EXECUTE_READWRITE)`, `WriteBytes`, `WriteJMP(from, to)`,
`MEMORY:ResolvePtr(addr, start)` for RIP-relative resolution.

**Not available: an assembler.** There is no `autoAssemble`. Every code cave is raw
opcode bytes.

```lua
-- mem/patch.lua — the shape of every patch
PATCH.define("budget_no_decrement", {
  aob      = "48 8B 4? ?? 89 ?? ?? ?? 00 00 E8",
  expect   = 1,                          -- refuse if the scan hits 0 or >1
  offset   = 0,
  original = { 0x89, 0x51, 0x18 },       -- exact bytes we expect to find (guard)
  cave = {
    size  = 64,
    bytes = { 0x90, 0x90, 0x90,          -- nop the decrement
              0xFF, 0x25, 0x00,0x00,0x00,0x00 },  -- jmp [rip+0]
    -- absolute return address appended at install time
  },
})
```

Non-negotiable rules for this layer:

1. **Verify before write.** The `original` bytes must match exactly. A game patch that
   moves the site produces `failed:signature_mismatch`, never a blind write.
2. **`expect` is mandatory.** A signature matching 0 or 3 sites is a failed scan, not a
   "pick the first one".
3. **Every patch is reversible.** Original bytes are saved in the result; `mem.restore`
   puts them back. Patches do not survive a session — no persistence, no auto-reapply.
4. **Never patch inside `FCLiveEditor.DLL`'s module range** (SI-2). `mem/scan.lua`
   rejects any target address inside the LE module.
5. **Emitting opcodes is a build-time job, not a runtime one.** Byte arrays are authored
   and unit-tested outside (`domain/patches/`, with a tiny x86-64 encoder covering the
   ~15 instruction forms actually needed: `nop`, `mov r/m64,r64`, `jmp rel32`,
   `jmp [rip+0]`, `push/pop`, `add/sub imm32`, `ret`). Lua receives a byte list and
   never assembles anything. *Alternative rejected: an assembler in Lua — a hand-rolled
   x86-64 encoder inside the game process, with no tests and no disassembler to check
   it, is how you get a silent crash three hours into a career.*

`AOBScan` over the whole game module is slow. `mem/scan.lua` caches every resolved
address in `session.json`'s `scan_cache`, keyed by `(game_ver, signature)`, and
re-validates the `original` bytes on reuse instead of rescanning.

---

## 7. Observability

Today: **699 `except` clauses in `src/`, 361 of them ending in a bare `pass`, and
zero uses of the `logging` module.** Eleven `print()` calls are the entire diagnostic
surface. `app.log` at the repo root is written by `app_entry.py` with its own
hand-rolled path derivation. This is the debt that makes every other bug expensive.

### 7.1 Logging strategy

**One configuration, in `core/log.py`, called exactly once from `main.py`.**

| Sink | Format | Level | Rotation |
|---|---|---|---|
| `logs/companion.jsonl` | one JSON object per line | DEBUG | 10 MB × 5 |
| `logs/companion.log` | human, `%(asctime)s %(levelname)-5s %(name)s [%(job_id)s] %(message)s` | INFO | 10 MB × 3 |
| console (dev only) | human, coloured | DEBUG | – |
| in-app ring buffer | last 500 records, for the diagnostics pane | INFO | – |

Every record carries contextual fields via a `contextvars`-backed filter:
`job_id`, `save_uid`, `session_id`, `command`, `surface`. Set once when a Command
starts; every log line inside it — including from `domain/` — is automatically tagged.
That is what makes "show me everything that happened for job 01JQ8F…" a single `grep`.

**Exception policy, enforced by `ruff` (`BLE001`, `S110`) and a custom check:**

| Situation | Required form |
|---|---|
| Expected, handled | `except FileNotFoundError: log.debug("...", exc_info=True)` — narrow type, always a log line |
| Expected, ignorable | `except OSError: log.debug(...)` — **`pass` is never allowed alone** |
| Unexpected | Do not catch. Let it reach the Command boundary. |
| Command boundary | Exactly one `except Exception` per Command, in `app/commands/_base.py`: logs `exception()` with full context and returns `ApplyOutcome.FAILED` |
| Lua/transport boundary | Never raises across the seam — it returns a `JobResult` with `FAILED` |

Target: **zero bare `except: pass` in `core/`, `domain/`, `app/`.** In `ui/` a narrow
`except tk.TclError: log.debug(...)` is permitted (destroyed widgets), and nowhere else.

### 7.2 Structured job records

`state.sqlite` table `job_record`, one row per job, **uncapped** (v1 capped
`job_history.json` at 40 rows and stored machine-absolute paths in it):

```sql
CREATE TABLE job_record (
  job_id        TEXT PRIMARY KEY,        -- ULID
  created_utc   TEXT NOT NULL,
  submitted_utc TEXT,
  claimed_utc   TEXT,
  finished_utc  TEXT,
  origin        TEXT NOT NULL,           -- ui.cards.apply, pack.matchday, cli, …
  label         TEXT NOT NULL,
  save_uid      TEXT,
  session_id    TEXT,
  outcome       INTEGER NOT NULL,        -- ApplyOutcome value
  op_count      INTEGER NOT NULL,
  applied_count INTEGER NOT NULL,
  failed_count  INTEGER NOT NULL,
  latency_ms    INTEGER,                 -- submitted → finished (includes event wait)
  exec_ms       INTEGER,                 -- claimed  → finished (actual work)
  trigger_event TEXT,                    -- which CareerModeEvent drained it
  job_json      TEXT NOT NULL,           -- the submitted job, verbatim
  result_json   TEXT                     -- the result, verbatim
);
CREATE INDEX ix_job_outcome ON job_record(outcome, created_utc DESC);
CREATE INDEX ix_job_save    ON job_record(save_uid, created_utc DESC);
```

The two latency columns are the whole point: `latency_ms - exec_ms` **is** the
event-starvation cost, measured rather than guessed. It is the number that decides
whether §9's R3 mitigation is ever needed.

Because `job_json` and `result_json` are stored verbatim, any job is replayable:
`companion replay 01JQ8F…` re-submits the identical op list. Bug reports become one id.

### 7.3 Diagnostics surface

An **About → Diagnostics** pane (successor to `src/health_check.py`, which is 4
functions and one `snapshot()` dict) plus `companion doctor` on the CLI, emitting the
same JSON.

```
LE INSTALL            ✔ C:\...\FC 26 LE v26.3.5   game 1.0.138.57785
  FCLiveEditor.DLL    ✔ hash matches pin                          (SI-2)
  live_editor.lua     ✔ unpatched, no AUTOARM                     (SI-3)
  autorun installed   ✔ lua\autorun\00_le_companion.lua  core 3.0.0
SESSION               ✔ ARMED  pid 24188  since 20:38:00 (4m ago)
  handlers            ✔ pre__/post__CareerModeEvent, post__LEInitDoneEvent
  capabilities        t3db ✔  row_api ✔  memory ✔  http_push ✖  execute_sql ?
QUEUE                 0 jobs · 0 claimed · last drain 47s ago (DAY_PASSED)
  jobs today          38 · applied 34 · no_op 3 · failed 1
  median latency      1.9s   median exec 61ms   ← the gap is event wait
GAME                  ✔ in Career Mode · save 4a7f… · team 5 Chelsea
DATA
  universe            ✔ 339,609 cards · 8 years · indexed 2026-07-25
  oracles             ✔ base_players 22,348×149 · cards 24,731×55
  live state          squad 40s old · career state 40s old
WARNINGS
  ⚠ 1 job failed in the last hour: 01JQ8F… player.resolve found=false (id 48940)
```

Also: **`companion selftest`**, which runs the whole pipeline against
`FakeTransport` with no game running — job build → submit → result parse → outcome →
presenter — and exits non-zero on any mismatch. This is the successor to
`src/protocol.py::protocol_self_check` and `tools/verify_no_autoarm.py`, and it runs
in CI.

---

## 8. Migration plan

**Prime directive: no user data is lost, and v1 keeps working until v2 is better at
everything it does.** They coexist — different queue subdirectories, different resident
Lua module names, different `state.sqlite` — so a user can run v1 the day after
installing v2.

### 8.1 What must survive

| Asset | Count / size | Migration |
|---|---|---|
| Profiles | **51** (`profiles.json`: 11 mass_edit, 10 visual, 6 export, 5 user_team, 5 user_team_event, 4 career_cleanup, 2 contracts, 2 unlocks, 2 play_as_player, 2 packs, 1 squad, 1 debug; kinds: 43 stock_script, 7 snippet, 1 app_script) | §8.3 — one-time transcription, then verified by parity test |
| Packs | **19** (`matchday, squad_boost, morale_day, contracts, contracts_plus_cpu, never_retire, career_cleanup, mass_visual, shoe_random, youth_regen_farm, max_growth, export_squad, export_season, unlocks_all, match_ready, signing_settle, season_kickoff, pre_match_full, club_refresh`) | Mechanical — a pack is an ordered list of profile ids |
| Card catalog | `catalog.sqlite` 376 MB, **339,609 rows**, + 17 CSVs + `futbin/` + `futgg/` | **File is used in place.** Add `schema_version` + FTS5 index. Zero copy. |
| Snapshots | 31 files + `index.json` | Imported into `state.sqlite.snapshot` |
| Job history | 40 rows | Imported into `job_record`, `outcome` string → enum, missing fields `NULL` |
| Favorites | `favorites.json` (absent on this machine, cap 80) | Imported if present |
| Config | `companion_config.json` | Imported into `prefs`; `timeout_live`/`poll_sec` dropped (no longer meaningful) |
| Squad | `current_squad.json` | Imported as a `save_snapshot` with `save_uid = NULL`, marked stale |
| Credentials | `xai_credentials.json` | Re-encrypted to DPAPI, **plaintext file shredded** after verified read-back |
| In-flight queue | `queue/*.lua` + `queue/*.lua.meta.json` + `queue/done/` | **Not migrated.** Drained or abandoned before cutover; the meta sidecars are imported into `job_record` as history only |

`companion migrate` performs all of this, is idempotent, writes a report, and
**never deletes a v1 file** except the credentials one (which is a security fix, and
only after the DPAPI round-trip is verified).

**A version-numbering trap to avoid repeating.** v1 already has two independent "version
3"s: the queue job protocol is **v2** (`protocol.PROTOCOL_VERSION = 2`) while the
`*.lua.meta.json` sidecar is `{"version": 3, …}`, and `companion_config.json` is
`{"version": 1}`, and `profiles.json` is `{"version": 1}`. Four counters, no relationship.
v2 has exactly two: `PROTOCOL_V = 3` (wire format, in every job and result) and
`state.sqlite`'s `schema_version` (storage). Everything else is derived.

### 8.2 Phases

```
P0  Foundation        ──▶ P1  Transport v3  ──▶ P2  Core ops   ──▶ P3  UI
     core/ seams           in-game core         profiles+packs      new shell
     state.sqlite          shadow mode          parity gate         v1 retired
     logging                                                          │
                                                                      ▼
                                                    P4  Career state ──▶ P5  Memory
```

**P0 — Foundation** *(ships nothing user-visible)*
`core/` ports + fakes; `core/log.py`; `state.sqlite` + migrations; `companion migrate`;
`domain/outcome.py`; the safety-invariant test suite (SI-1…SI-8).
**Gate:** `companion migrate` round-trips all 51 profiles, 19 packs, 31 snapshots,
40 job rows on a copy of the real user directory, verified field-by-field.

**P1 — Transport v3, shadow mode** *(v1 still does the work)*
Ship the resident core with `arm`, `executor`, `ops/registry`, and exactly **two** ops:
`diag.ping` and `db.dump`. v1's bridge keeps running from `lua/scripts/`; v3 arms from
`lua/autorun/` under a different global. The app writes v3 jobs **in parallel** with v1
jobs for read-only operations and compares.
**Gate:** 200 consecutive `diag.ping` jobs, 100% attributed to their `job_id`, zero
duplicate execution, `session.json`/`drain.json` liveness matching the §3.5 matrix by
hand-verification (idle in the main menu must read `ARMED`, not `OFF`).
**This phase alone fixes the 73% duplicate-skip and the false-OFF.**

**P2 — Core ops + profile parity** *(v2 becomes the default path)*
`ops/player`, `ops/db`, `ops/squad`, `ops/roster`; `db/schema|read|write`; all 51
profiles and 19 packs ported to op lists.
**Gate — the parity harness, and it is the crux of the whole migration:** for each of
the 51 profiles, run the v1 Lua and the v2 op list against the same save, dump the
affected tables before and after via `op:"db.dump"`, and assert byte-identical diffs.
Any profile that fails parity ships as a v1 passthrough (`kind: "legacy_lua"`) rather
than being silently changed. `tests/test_ce_parity_profiles.py` already exists as a
seed for this.

**P3 — New UI** *(v1 GUI retired)*
`app/` store + commands + presenters; `ui/` shell and the eight tabs, rebuilt against
`AppState`/`Services`. `src/ui/player_edit.py`, `widgets.py`, `badge.py`, `stepper.py`
port with minimal change. `PremiumApp` is deleted.
**Gate:** every tab renders from `AppState` alone (assertable: no `Any` in any `ui/`
signature, `mypy --strict` clean); ≥70% coverage on `app/` presenters.

**P4 — Career state**
`career/managers|state|growth`; `ops/career`; the growth-XP mirror **on by default**;
form/morale/sharpness/fitness/role/release-clause read and write; live-state sync (§5.3).
**Gate:** an attribute edit survives 10 simulated days with the mirror on, and is
demonstrably reverted with it off. That contrast is the proof the mirror works.

**P5 — Memory / AOB** *(opt-in, gated)*
`mem/scan|patch`; `domain/patches/` with the small x86-64 encoder; two shipped patches
maximum, both reversible, both with `expect = 1` signatures.
**Gate:** signature mismatch produces `failed:signature_mismatch` and *no* write; a
2-hour career session with both patches applied and reverted, no crash.

### 8.3 Porting the 51 profiles

Three kinds, three routes:

| v1 `kind` | Count | Route |
|---|---|---|
| `snippet` (7 keys: `full_fitness`, `full_sharpness`, `full_form`, `full_morale`, `full_form_morale_sharpness`, `matchday_pack`, `squad_boost_pack`) | 7 | The easiest — each is already one LE call: `UserTeamSetPlayersFitness(95)`, `UserTeamSetPlayersSharpness(100)`, `UserTeamSetPlayersForm(100)`, `UserTeamSetPlayersMorale(100)`, `UserTeamSetPlayersFormSharpnessMorale(100,100,120)`. They become `op:"career.set"` with a `scope:"user_senior_team"`, dispatching to the same functions. Mechanical. |
| `stock_script` (points at `lua/scripts/*.lua` shipped by LE) | 43 | Transcribe to op lists. Most are one loop over one table — `mass_edit_squadrole.lua` becomes `{op:"db.write", table:"career_playercontract", where:{contract_status:{not_in:[1,3,5]}}, set:{playerrole:3}}` plus `op:"career.set_squad_role"` (both halves, §6.4). |
| `app_script` | 1 | Case-by-case |

**These are LE's own scripts and they keep working regardless.** A user can always run
them from LE's Lua Engine — the companion is not their only home. That is the safety
net that makes it acceptable to ship a profile as `legacy_lua` if parity fails.

---

## 9. Risks and what to prototype first

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | **Op lists cannot express everything 51 profiles do.** Some stock script has control flow no schema captures. | High | Medium | `kind: "legacy_lua"` escape hatch survives permanently. The op registry is extensible. Parity gate catches it in P2, not in production. |
| **R2** | **Career manager offsets break on a game patch.** Every FC 26 title update can move them. | High | High | All offsets in one table (`career/state.lua::STATE_MAP`), keyed by `game_ver`. Prefer LE's documented setters over raw chains. A failed chain returns `failed:manager_not_found`, never a wild write. Diagnostics shows which chains resolve. |
| **R3** | **Event starvation is worse than modelled.** Users spend real time in menus; jobs sit for minutes. | Medium | High | Measured, not guessed: `latency_ms - exec_ms` in `job_record` (§7.2). If the p50 gap is bad, mitigations in order: (1) sharper `STALLED` messaging + one-click Force Drain, (2) HTTP push so at least *results* are instant, (3) reconsider the native tick provider (§2.4). |
| **R4** | **Growth mirror doesn't actually work**, or reverts anyway. `PlayerSetValueInDevelopementPlan`'s parameters are documented but not proven for every field. | Medium | High | **Prototype first (P-1 below).** If mirroring fails, the honest fallback is telling the user the edit is temporary — which is still better than v1 silently claiming success. |
| **R5** | **Atomic rename claim races** on some filesystem (OneDrive-synced folder, network drive, aggressive AV). | Low | High | `queue/` must be local NTFS; `companion doctor` refuses to arm on a synced or network path. A lost claim is safe by construction — the loser skips the job. |
| **R6** | **`SendHTTPRequest` blocks catastrophically** when the Python listener is gone. | Medium | High | Off by default; one attempt per drain, after the result is written; 3 strikes disables for the session. Worst case is a stutter, never data loss. |
| **R7** | **Rebuild loses a feature nobody documented.** 70 modules of accreted behaviour; 6-17% UI coverage means much is untested and unspecified. | High | Medium | Phased coexistence — v1 stays runnable through P3. Feature inventory extracted from `src/web_api.py`'s 28 endpoints, which is the most complete enumeration of app capabilities that exists. |
| **R8** | **`state.sqlite` migration corrupts user data.** | Low | High | `companion migrate` never deletes v1 files; writes to a new DB; verifies by re-reading and diffing; report shown before anything is used. |
| **R9** | **AOB patches crash the game** in a way users blame on LE. | Medium | High | P5 is last, opt-in, two patches max, `expect=1`, original-bytes guard, fully reversible, never persisted. |
| **R10** | **Arbitrary code execution via the queue.** v1's Lua does no path validation and `load()`s whatever `_pending.txt` names — anything that can append a line to that file runs code in FC 26. | Low (local-only attacker) | High | Closed by construction in v3: the core reads a fixed directory of `<ulid>.json` and executes *data* through a fixed op registry. There is no `load()` of external text anywhere in the v2 core. `core/transport.lua::safe_name()` additionally rejects any name that is not a bare 26-char ULID + `.json`. |
| **R11** | **Deferred/resumable scans hold a stale record address** across events, and the table reallocates in between. | Medium | High | `ctx` stores a record *index* and a table generation counter, never a raw address, for anything that survives an event boundary. Any row-API mutation bumps the generation and forces a restart of the scan. Addresses are valid only within one drain. |

### What to prototype first, in order

Four spikes, each answering one question that could invalidate the design. Nothing else
should start until these are answered.

**P-1 · The growth-XP mirror (highest risk, cheapest to answer).**
A ~60-line standalone Lua script, run from LE's Lua Engine, no companion involved.
v1's `src/player_apply.py:163-196` is the starting point — it already does the mirror,
so this spike is not "can we?" but **"does it actually hold, and for which fields?"**,
which nobody has ever measured. Write `overallrating` and three attributes on a squad
player; mirror; simulate 10 days; read back. Run once with `use_dev = true`, once with
`false`. Then repeat on a player where `PlayerHasDevelopementPlan` returns false.
**Answers:** does the mirror hold; is v1's 17-entry exclusion list right, too broad, or
too narrow; what happens with no development plan? **Invalidates:** G8, P4, and the
honesty of every "Applied" the app will ever show.

**P-2 · Protocol v3 claim + attribution under real event pressure.**
Resident core with only `diag.ping`. Submit 200 jobs while playing a career normally.
**Answers:** is `os.rename` claim genuinely atomic here; does every job get exactly one
attributed result; what is the real distribution of `latency_ms - exec_ms`; how many
jobs does a single event actually drain within a 120 ms budget?
**Invalidates:** §3 entirely, and R3's severity. This is the measurement that decides
whether the native tick provider ever needs to exist.

**P-3 · Schema-driven read/write, the `playerid` index, and read-back verification.**
`db/schema.lua` describing the `players` table from live memory; build the
`playerid → record` index and time it; write 20 fields across 5 players via
`SetRecordFieldValue`; read back; deliberately write out-of-range and confirm rejection
rather than bit-wrap.
**Answers:** are `depth`/`min` reliable for range validation; does read-back catch the
growth revert; **how long does one full index pass actually block the game thread**, and
does it fit a 120 ms budget or must it be chunked from day one? That last number decides
whether the §6.3 "never scan for a point lookup" design is affordable or whether every
first-touch of a table needs the deferred path.
**Invalidates:** §6.3, §6.2's budget constants, and G2.

**P-4 · Liveness on a real idle session.**
Arm via autorun, then: sit in the main menu 5 minutes; enter career; idle in the hub
3 minutes; advance a day; alt-tab 10 minutes; quit LE.
**Answers:** does the §3.5 matrix produce the right pill at every point, and does the
pid check correctly catch a dead session?
**Invalidates:** §3.5 and G3 — the single most user-visible defect in v1.

**Explicitly deferred, not prototyped:** `ExecuteSQL` (diagnostics probe only), the
native tick provider (only if P-2 says R3 is real), and any AOB patch (P5).

---

## Appendix A — v1 → v2 module map

| v1 | v2 | Note |
|---|---|---|
| `src/gui.py` (`PremiumApp`, 182 attrs) | `app/state.py` + `app/store.py` + `app/services.py` + `ui/tabs/*/model.py` | Deleted, split four ways |
| `src/paths.py` | `core/paths.py` (`AppPaths`) | Port + close the 25 bypasses |
| `src/protocol.py` | `core/transport/v3.py` | Rewritten for protocol v3 |
| `src/le_apply.py` | `core/transport/v3.py` + `platform/le_install.py` | Wait/pending machinery deleted |
| `src/apply_service.py` | `app/commands/apply.py` + `domain/outcome.py` | `parse_job_status` deleted |
| `src/actions.py` | `domain/job.py` | No more Lua text |
| `src/card_to_lua.py`, `add_team_lua.py`, `career_ops.py`, `player_apply.py`, `undo_apply.py` | `domain/job.py` op builders | **All five Lua generators deleted** |
| `src/product.py` | `domain/packs/` + `app/commands/run_pack.py` | |
| `src/profiles.py` | `domain/profiles/` | `profiles.json` stays a file |
| `src/card_catalog.py`, `card_index.py`, `card_match.py`, `card_enrich.py` | `domain/catalog/` | `catalog.sqlite` used in place |
| `src/player_schema.py`, `field_map.py`, `ovr_formula.py` | `domain/players/` | Ports nearly unchanged |
| `src/base_players.py` | `domain/players/oracle.py` | Best-documented module in v1; keep the docstring |
| `src/job_history.py`, `snapshot_store.py`, `favorites.py`, `companion_config.py`, `target_players.py` | `core/db.py` + `domain/snapshots/` | Seven JSON files → `state.sqlite` |
| `src/health_check.py` | `app/commands/doctor.py` | Expanded to §7.3 |
| `src/web_api.py`, `web_server.py` | `app/commands/` + `core/transport/push.py` | Endpoints become Commands; the HTTP server becomes the push listener |
| `src/ui/player_edit.py`, `widgets.py`, `badge.py`, `stepper.py`, `skeleton.py`, `empty_state.py`, `collapsible.py` | `ui/widgets/` | Already app-independent; port as-is |
| `src/ui/chrome.py` (1125 lines, 27 `app`-taking defs) | `ui/shell.py` + `app/presenters/` | Dissolved |
| `src/ui/tabs/cards.py` (1895 lines, 53 defs) | `ui/tabs/cards/{view,model}.py` | Largest file in v1; the densest god-object coupling |
| `bridge/le_profile_bridge.lua` (367 lines, generic `load()` harness) | `ingame/le_companion/` (§6.1) | Rewritten as a fixed program |
| `native/le_companion_inject/` | unchanged, opt-in, off by default | SI-2 |

## Appendix B — protocol v2 → v3 file map

| v2 file | v3 | Why |
|---|---|---|
| `_run_now.lua` | **gone** | Half of the dual-write; source of the 73% duplicate-skip |
| `_pending.txt` | **gone** | The `jobs/` directory listing is the queue |
| `_wake.txt` | **gone** | Claim-by-rename replaces the wake channel |
| `<stem>_<ts>.lua` | `jobs/<ulid>.json` | Data, not code; identity in the filename |
| `_last_result.txt` (shared, mutable, one line) | `results/<ulid>.json` (per job, immutable) | Attribution (G1) |
| `_job_status.txt` (regex-parsed key=value) | `results/<ulid>.json → ops[]` | Semantic success (G2) |
| `_bridge_alive.txt` (90 s TTL) | `session.json` (pid-checked, no TTL) | Idle ≠ dead (G3) |
| `_bridge_armed.txt` | `session.json` | Merged |
| `_bridge_busy.txt` | `drain.json → budget_ms_used` | Busy is a drain property |
| `_protocol_dry_run` | `FakeTransport` in tests | Not a production concern |
| `done/` | `archive/<date>/` | Job + result kept together |
| — | `claimed/` | **New.** Exactly-once + crash recovery |
| — | `drain.json` | **New.** Tick age, separate from arm |
