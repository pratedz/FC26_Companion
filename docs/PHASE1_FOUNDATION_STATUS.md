# Phase 1 foundation status — 2026-07-30

## Outcome

The canonical v2 runtime is `companion/`; `src/v2/` remains a reference-only
prototype. A boundary test prevents the canonical runtime from importing it.

The Phase 1 implementation is complete at the offline/code level. It is not
declared live-complete until the GUI and in-game write paths pass the gates
listed below.

## Implemented and offline-verified

- v1 and v2 use different autorun files. v2 installs
  `01_le_companion_v2_autorun.lua` and does not replace v1's
  `00_le_companion_autorun.lua`.
- installer status validates the real core, loader and queue configuration;
  Doctor consumes that status instead of assuming installation.
- PID-zero worker sessions use confirmed FC/Live Editor process evidence rather
  than being marked OFF solely because Lua did not provide a PID.
- SQLite is opened from the configured app root and hydrates preferences, job
  history and the latest squad cache.
- preference, submitted-job, completed-job and squad-cache changes persist.
- visible UI surfaces subscribe to relevant store slices and refresh on the UI
  thread.
- verified successful writes clear staged editor changes and invalidate squad
  data that must be read again.
- arbitrary `raw_lua` is removed from the normal protocol.
- `require_cm`, core/save requirements, expiry, manual-only jobs and per-job
  budgets are enforced.
- unsupported atomic jobs are rejected before a write. Atomic rollback is not
  advertised.
- `snapshot_to` captures supported `set_fields` pre-values and rejects jobs it
  cannot safely capture before writing.
- transfer, budget, create, career and bulk operations no longer claim complete
  success when verification is partial or unavailable.
- DPAPI availability requires a real protect/unprotect round trip. Credential
  migration leaves plaintext untouched and reports an error when DPAPI cannot
  actually encrypt.

## Verification

- canonical-boundary tests: 2 passed
- changed-foundation tests: 159 passed, 15 GUI-environment skips
- complete v2 suite: 373 passed, 33 environment skips
- Python compile check: passed
- installer dry-run: passed; planned a separate v2 loader and did not target
  `live_editor.lua`
- Doctor: correctly reports this machine's v2 worker as not installed and FC/LE
  as not running
- complete repository suite: 975 passed, 33 skipped, 1 failed

The remaining repository failure is a v1 credential test:
`tests/test_review_medium_fixes.py::test_credentials_dpapi_roundtrip`. On this
session Windows exposes `crypt32`, but `CryptProtectData` fails with error 2.
The unchanged v1 path falls back to plaintext. v2 does not.

## Live gates still required

1. Install v2 into the real Live Editor directory and confirm both v1 and v2
   loaders coexist.
2. Start FC 26 and Live Editor, then prove ARMED/LIVE transitions with the
   session's real process evidence.
3. Exercise every visible GUI surface and confirm refresh after preference,
   job, squad and apply-result changes.
4. On a disposable Career Mode save, verify supported snapshot capture,
   read-back, transfer, budget, create, career and bulk outcomes.
5. Restart v2 and confirm hydrated preferences/history/squad state using the
   real user directory.

Until those gates pass, write-capable operations remain `LIVE-GATE`, not
`PARITY`.
