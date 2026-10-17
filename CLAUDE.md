# CLAUDE.md — FC26 Companion (source)

Desktop companion for FC 26 Career Mode + xAranaktu's Live Editor (LE).
Python/CustomTkinter app writes JSON jobs; a resident Lua core inside LE runs
them against the live game. See README.md for the user-facing overview.

## Where things live (this machine)

| What | Path |
|---|---|
| This source repo (git) | `C:\Users\prated\Desktop\FC26_Companion_src` |
| Deployed app (what the owner runs) | `C:\Users\prated\Desktop\Fc26 LE\FC26_Companion` (`FC26_Companion.exe`, `_internal_v2\`, `state.sqlite`, `card_db\`, `queue\`) |
| LE install (Launcher.exe, DLL, offsets) | `C:\Users\prated\Desktop\Fc26 LE` |
| **LE data dir** (where LE actually loads Lua from) | `C:\FC 26 Live Editor` (registry `HKLM\SOFTWARE\Live Editor\FC 26`) |
| Installed Lua core | `C:\FC 26 Live Editor\lua\scripts\le_companion\` + `config.json` (queue_dir) |
| Autorun loaders | `C:\FC 26 Live Editor\lua\autorun\00_le_companion_autorun.lua` (v1 bridge) and `01_le_companion_v2_autorun.lua` (v2 core) |
| LE logs | `C:\Users\prated\Desktop\Fc26 LE\Logs\live_editor_<dd-mm-yyyy>.log` (look for `[LE_Companion]` / `[LEC]`) |

## Two generations in one repo

- **v2 — current.** `companion/` (Python) + `ingame/le_companion/` (Lua).
  Entry `app_entry_v2.py`, build `build_exe_v2.py`, tests `tests/v2/`.
- **v1 — legacy "LE_Profile_Executor".** `main.py`, `src/`, `bridge/`, `web/`,
  `native/`. Text-file queue (`queue/*.lua`, `_run_now.lua`, `_pending.txt`,
  `_wake.txt`, `_last_result.txt`). Must keep working (DECISIONS.md "prime
  directive"): v2 never touches v1's file patterns. v2 still imports some
  `src/` modules (cli, library, sign search, base_players, pack_library).

## v2 layering (strict, downward only — see `companion/__init__.py`)

`ui/ → app/ → domain/ → core/`; `platform/` implements `core` ports with
Windows APIs. `domain/job.py` is the only place game ops are built.
`core/transport/v3.py` is the only queue writer. `core/paths.py confine()`
keeps every job-derived path inside `queue/` (SI-7).

## Wire protocol v3 (Python ↔ Lua)

- Python writes `queue/jobs/<ULID>.json` atomically and rewrites
  `queue/jobs/index.json` (Lua cannot list directories). Read-only jobs also
  touch `queue/_fast_read`.
- Lua claims by rename into `claimed/`, writes `results/<ULID>.json`
  (`*.partial.json` while multi-step ops run), `state/`, `poison/`, `archive/`.
  Liveness: `session.json` + `drain.json` (pid-checked). Assets exports go to
  `queue/assets/<job>/<item>/`.
- Schemas: `docs/schema/job.schema.json`, `result.schema.json`. Python maps
  Lua `state/ok/counts/failures` onto `domain/outcome.py ApplyOutcome`.
- Ops: `diag.ping`, `set_fields`, `export_squad`, `snapshot`, `db.dump`,
  `transfer`, `budget`, `bulk_edit`, `career.set`, `growth_sync`,
  `create_player`, `add_to_team` (v4), `repair_partial_add`,
  `restore_partial_add`, `names.sync`, `injury.scan`, `injury.cure`,
  `assets.legacy`.

## Safety invariants — do not break

- **Never drain during LE init.** Autorun arms handlers only (SAFE_ARM). Draining
  at load froze LE.
- **Arm only via `lua/autorun/`.** Never patch LE's `live_editor.lua` (SI-3).
- **Hub readiness is a latch (2.6.42).** Only a settled hub event (post
  `ENTERED_HUB_FIRST_TIME` / `ENTERING_TEAM_MANAGEMENT` with `IsInCM()`) sets
  it. Hub screen/notification events (`events.BENIGN_NAMES`: `DATA_READY`,
  `LEAVING_TEAM_MANAGEMENT`, `PLAYER_ADDED_TO_TEAM`, …) keep it. Every other
  event, including day/hour/month passing, save, load, match, season reset
  and any unknown id, clears it and does no game or disk I/O. Readiness is
  re-checked between ops and jobs. This keeps the 2.6.36/2.6.37
  calendar-freeze fix — see `C:\Users\prated\Desktop\Fc26 LE\diagnosis\Career progression fix.md`.
- **Job classes (2.6.42, `runner.job_class`)**: `read`, `light` (only a keyed
  players-table `set_fields` without `upsert`, `growth_sync`, `budget` set) and
  `heavy` (everything else: add_to_team, transfer incl. release, create_player,
  names.sync, repair/restore_partial_add, asset export, set_fields upserts or
  on teamplayerlinks/editedplayernames, bulk_edit, career.set, injury.cure,
  anything unknown). A settled hub event runs every class for up to
  `hub_drain_budget_ms` (one visit finishes the queue). A quiet native tick
  runs read + light. A hub screen post event (throttled, `ui_pump_every_ms`)
  runs read + light only when no other Career event is open around it
  (DATA_READY also fires nested inside other events), only jobs with
  `budget_ms <= ui_job_budget_ms`, never `export_squad` / `db.dump`, and
  clears the latch when IsInCM says the save is gone (there is no
  Career-exit event). Every screen-event or tick drain starts with
  `db.reset()`: LE's table objects snapshot `first_record` /
  `written_records` when opened. Heavy jobs wait for Team Management.
- **`GetPlayerName` / `GetTeamIdFromPlayerId` are Live Editor's cache**, built
  on first use and cleared only by LE's native `DAY_PASSED` /
  `POST_LOAD_PREPARE` listeners (FCLiveEditor.DLL v26.3.6, MasterManager
  0x180363d00). In the hub visit of a write they return pre-write values.
  Never use them as proof of a write: verify from the DB (players name ids,
  editedplayernames, teamplayerlinks, `GetUserSeniorTeamPlayerIDs`). Before
  2.6.42 this made every signing wait 8 events and end `name_not_visible`, and
  releases end `unverified_side_effect`.
- No `ReloadPlayersManager` (SI-4). `CreatePlayer` only with
  `grants.allow_create_player` and ids clamped to 460000–499999 (SI-5).
  `add_to_team` needs `core_ops` enabled in config.json **and**
  `grants.allow_add_to_team`.
- Native injury memory access (`ingame/le_companion/ops/fitness_injuries.lua`)
  is gated to LE `v26.3.5`/`v26.3.6` plus a vtable check. A new LE version
  needs offline verification first (`tools/verify_injury_26_3_6.py`,
  `docs/INJURY_LAYOUT.md`).
- Product rules (PRODUCT.md): five honest job states (staged, queued,
  running, applied, failed); never write or show a value that was not read;
  show a reviewed diff before any queue write. No animation loops in the UI
  (they froze v1).

## Native plugin (lec_native.dll 1.1.0, core 2.6.42+)

LE has no native plugin API (`GetPlugin` only looks up game objects), but its
embedded Lua keeps `package.loadlib`. `ingame/le_companion/native.lua` loads
`native/lec_native.dll` (source: `native/lec_native/`, build: `python build.py`
with Zig). The DLL adds a ~200 ms tick on the Career-event thread that calls
`runner.on_native_tick()`: read + light jobs only, never while a Career event
is open or within `LIMITS.quiet_ms` of one, writes only after 1.5 s of quiet
with no job awaiting a post-transfer event. Two carriers: a thread timer
(needs a message pump — FC's Career thread has none, measured 2026-10-17) and
(1.1.0) one user APC at a time via `QueueUserAPC`, which Windows runs only
inside that thread's own alertable waits. **The APC carrier is off by
default** (alertable waits can sit deep inside game code; LE's lua_State has
no lock): `native.lua` calls `lec_native_no_apc` unless `config.json` has
`"native_apc": true`. `native_status.json` reports `carrier`
(`timer`/`apc`/`none`) and `reason: "no_carrier"` when nothing reaches the
thread. It calls LE's own `lua_gettop/settop/load/pcallk` by RVA **only after
matching LE v26.3.6's code bytes** (`le_sig.h`); any other LE build leaves it
idle. Status -> app "Instant mode" (doctor/Health/status). Kill switches:
`native/DISABLE`, `<queue>/native_disable`, `config.json "native": false` /
`"native_tick_writes": false`; APC only: `native/NO_APC`,
`<queue>/native_no_apc`, `config.json "native_apc": false`. Hub screen-event
drains: `config.json "ui_event_jobs": false`.
A new LE version needs the RVAs re-located (see `native/lec_native/README.md`).

## Versions — keep in sync

- `companion/__init__.py`: `__version__` (app), `PROTOCOL_V = 3`,
  `CORE_VERSION`, `SIGN_CORE_MIN`.
- `ingame/le_companion/version.lua`: `version.VERSION` must equal
  `CORE_VERSION` (enforced by `tests/v2/test_automation_workspace.py`).
  Both are `2.6.43` (2.6.42 hub latch, job classes, DB name proof, APC carrier; 2.6.43 review fixes). Bump both on any core change.
- Deployed `README.txt` carries per-release notes (written by the deploy step).

## Commands

```powershell
python -m pytest tests/v2 -q                 # ~1,500 tests, headless. A bare `pytest` also runs
                                             # tests/; its tests that write into the real LE
                                             # install (v1 autorun loader, live_editor.lua
                                             # auto-arm) run only with COMPANION_ALLOW_LIVE_LE_TESTS=1
$env:COMPANION_ALLOW_GUI_TESTS=1; python -m pytest tests/v2 -q   # include real-window tests
python app_entry_v2.py                       # run GUI from source
python app_entry_v2.py doctor                # any arg -> CLI (companion/cli.py)
python app_entry_v2.py gamelog --errors       # LE's own log (Logs\live_editor_<date>.log), read-only
python build_exe_v2.py                       # -> FC26_Companion.exe + _internal_v2/ in repo root
python tools/sync_futgg_database.py --verify-sync   # FUT.GG catalog gates (see GATES.md)
```

Known failing tests (31 as of 2026-10-17, all pre-existing):
`test_runner_event_drain_contract.py` (still asserts pre-2.6.36 event rules),
`test_legacy_assets.py`, `test_review_reports.py` (needs the gitignored
`review/` folder), `test_card_collections.py` / `test_add_player_surface.py` /
`test_sign_discovery.py` (Sign 2.11.6 changed picks to per player *and year*,
default All versions). Compare against this list before blaming a change.

## Deploying a build

There is no single deploy script; past releases used one-off
`tools/deploy_*.py` scripts. The pattern they follow, which any deploy must
keep:

1. Refuse to run while `FC26_Companion.exe` is open.
2. Back up the deployed exe, `_internal_v2\` (as `*.before_<ver>_<timestamp>`)
   and `state.sqlite`; keep a `backups\companion_before_<ver>` copy.
3. Swap in the new exe + `_internal_v2\`; never touch user data (`state.sqlite`,
   `card_db\`, `queue\`, `portraits\`, `snapshots\`) or the installed Lua worker
   unless the release ships a new core.
4. Verify and roll back on failure; update `README.txt` release notes.

The owner tests each release in their live Career save. Release checks never
write to FC.

## Gotchas

- `FC26_Companion.spec` has uncommitted absolute `C:/Users/prated/...` paths;
  `build_exe_v2.py` regenerates it anyway.
- `RECONNECT_LIVE_EDITOR.lua` is generated by
  `le_install.reconfigure_current_worker_snippet()`; it re-reads the worker's
  `config.json`, so it carries no queue path. `bridge/RUN_BRIDGE_ONCE.lua` is
  regenerated by v1 `src/le_apply.py install_bridge()` (and by some tests).
- The v1 bridge loader is **disabled** on this machine: moved to
  `C:\FC 26 Live Editor\lua\autorun_disabled\` on 2026-10-16 because it wrote
  heartbeat files on day-passing Career events. Nothing in `companion/`
  depends on it. Move it back to `lua\autorun\` only if v1 is needed.
- `add_to_team` counts squad + loaned-out players against
  `LIMITS.max_squad_with_loans` (52) in step 1 and refuses `squad_full` with
  zero writes. Older cores could end **partial** (dummy has the card's stats,
  never transferred). A `squad_full` result never queues anything by itself:
  Sign shows the counts plus explicit Retry / Restore free agent
  (`app/commands/sign_recovery.py`); Restore needs the pre-write values that
  only the new core records (`progress.members[i].original_values`).
- Club "Check names" / `app_entry_v2.py names-check [--apply]` diff the
  exported in-game names (`SquadState.game_names`) with signing receipts and
  queue one `names.sync` job (`ops/names_sync.lua`, helpers exported from
  `ops/add_to_team.lua` as `M.helpers`).
- `integrations/grok.py` is the Codex/OpenAI transport despite its name.
- `card_db/teams.sqlite` exists only in the deployed folder; `profiles.json`,
  `assets/` and most of `card_db/` are gitignored but needed to build.
- pandas/numpy/matplotlib/pyarrow are bundled into `_internal_v2` but unused.
