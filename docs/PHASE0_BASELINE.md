# Phase 0 baseline — 2026-07-30

This file is the recovery and scope baseline for the v2 repair.

## Recovery

- Backup root: `phase0_backup_20260730/`
- Quarantined v3 jobs: `queue/archive/phase0_stale_v3_20260730/`
- Quarantined job count: 47
- Live `queue/jobs/index.json`: empty
- Nothing was deleted.

The backup contains:

- v1 user state and configuration;
- snapshots and credentials;
- the pre-change v2 Python/Lua/test source;
- the installed v1 autorun loader and bridge.

Do not restore the quarantined jobs directly into `queue/jobs/`. They include
transfer operations and must be reviewed against the intended Career Mode save
before any selective requeue.

## Product boundary

- v1 (`src/`, `main.py`, `LE_Profile_Executor.exe`) remains the supported
  working product during the repair.
- `companion/` is the canonical v2 runtime and architecture.
- `src/v2/` is a non-runtime prototype. Useful contracts may be ported into
  `companion/`, but new runtime work must not be added there.
- `ingame/le_companion/` is the canonical protocol-v3 in-game source.

## Phase 1 definition of done

Phase 1 is complete only when all of the following are true:

1. v1 and v2 worker installation cannot overwrite or disable one another.
2. Setup and Doctor report the actual installed worker state.
3. An armed session is not reported OFF solely because Lua cannot expose a PID.
4. Every visible surface updates after store state changes.
5. job history, preferences and save-scoped squad cache persist and hydrate.
6. successful writes clear staged changes and invalidate stale live data.
7. protocol fields advertised as safety features are enforced or rejected.
8. normal protocol jobs cannot carry arbitrary Lua source.
9. offline tests pass, with live-only behavior explicitly gated for disposable
   save verification.

Offline implementation and verification results are recorded in
`docs/PHASE1_FOUNDATION_STATUS.md`. Live-only gates remain deliberately open.
