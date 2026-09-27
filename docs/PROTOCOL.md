# LE Companion control protocol (v2)

Shared contract between the **external control/config program** and the
**inject-side module** (`LECompanionInject.dll` + in-LE Lua queue worker).

## Ownership (anticheat / inject)

| Component | Owns |
|-----------|------|
| LE `Launcher.exe` | FakeEAAC backup/install/restore, primary inject of `FCLiveEditor.DLL` |
| `FakeEAACLauncher/` | Stub EAAC launcher used only by LE Launcher |
| `FCLiveEditor.DLL` | In-process LE runtime, Lua Engine, game DB APIs |
| Companion inject-side | Queue arm/drain protocol markers; does **not** swap EAAC or replace LE DLL |
| External program | UX, profiles, card search, config, **issuing** jobs |

Companion **never** reimplements FakeEAAC install/restore.

## Queue directory layout

Default: `LE_Profile_Executor/queue/` (absolute path baked into installed Lua worker).

| Path | Writer | Reader | Meaning |
|------|--------|--------|---------|
| `<job>.lua` | External | Inject-side | Job body (LE-compatible Lua) |
| `_pending.txt` | External | Inject-side | One job filename per line (non-`_` names, `.lua`) |
| `_run_now.lua` | External | Inject-side | Single-slot latest apply (always drained first) |
| `_wake.txt` | External | Inject-side | `wake <name>.lua` hint |
| `_bridge_alive.txt` | Inject-side | External | Heartbeat (`alive <unix> …`) |
| `_bridge_armed.txt` | Inject-side | External | Armed marker (`armed <unix>`) |
| `_last_result.txt` | Inject-side | External | `OK …` or `FAIL …` |
| `_job_status.txt` | Inject-side | External | Last job name + status line |
| `done/<job>.lua` | Inject-side | — | Archived completed jobs |
| `../companion_config.json` | External | Both | Customization / control config |
| `_protocol_dry_run` | Self-check / tests | Native + Python dry drain | **Required** for dry ProcessQueue; without it jobs are left untouched (`FAIL dry_run_required`). In-LE Lua bridge does **not** use this (real LE APIs). |

## Drain order (inject-side)

1. Write alive + armed heartbeat.
2. If `_run_now.lua` exists → execute → move to `done/` → count.
3. Build pending list from `_pending.txt` + names in `_wake.txt`.
4. For each pending job file: skip body-duplicate of already-run `_run_now`; else execute → `done/`.
5. Rewrite `_pending.txt` with any stuck names (or clear).
6. Delete `_wake.txt`.
7. Write `_last_result.txt`:
   - `OK processed=N ok=N last=<name>` when all succeeded
   - `FAIL processed=N ok=M last=<name>` when any failed
   - `OK idle queue_empty` when nothing to do

## Execute semantics

| Context | Who runs Lua job body |
|---------|------------------------|
| In LE session (Lua worker armed) | LE Lua `load` + `pcall` (full game DB APIs) |
| Native DLL dry / unit test | Protocol consumer validates I/O; marks job executed without FC26 |
| External only | Writes jobs; waits on `_last_result.txt` / `done/` |

## Result observation (external)

1. Issue job → write files + rebuild pending.
2. Poll `_bridge_alive.txt` (LIVE if recent mtime).
3. Poll `_last_result.txt` for `OK` / `FAIL` containing job stem or `processed=`.
4. Confirm job file moved under `done/`.

## Config file (`companion_config.json`)

```json
{
  "version": 1,
  "default_target_playerid": null,
  "apply_categories": null,
  "auto_clear_stale": true,
  "queue_rel": "queue",
  "notes": "Written by external control; inject-side may read for defaults"
}
```

External program owns read/write. Inject-side may read; must not require it to drain.
