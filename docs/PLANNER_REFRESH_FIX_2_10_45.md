# Planner refresh and Build flow — 2.10.45 / worker 2.6.29

The reported Apply failure for player 81834 was a metadata mismatch. The real detailed database snapshot omitted the display name; the reader normalized that omission to an empty name. A later automatic Club refresh returned Pelé, and the source check incorrectly treated this as a changed player.

Detailed reads now preserve the Club display name. The apply source check compares database fields and club shirt numbers, excluding display labels and derived metadata. A genuine changed field still blocks Apply before submission and reports the player, field, original value and new value.

The resident worker's normal Club export now includes planning ratings, attributes, skills, PlayStyles and body values in the same valid-record scan. Field availability is resolved once before the scan; detailed values are read only for club members. Build reuses those exported values and starts designing the requested changes without queuing another player snapshot. The older sparse-export fallback is retained for incomplete reads. The UI shows Reading only when a read is actually needed.

The previously implemented feature-family guards remain: a skills/PlayStyles/shirts request does not write player attributes, overall or potential.

Validation: 87 tests passed, one opt-in test skipped. The regression tests execute the Lua export for 20 players, verify a single player-table scan, feed that export through the real Build entry point and reject any second snapshot request. Additional coverage checks real snapshot name omission, unchanged periodic refresh, changed-field rejection, mandatory skills/styles, grouped recovery and readback. No Career save was modified by testing.

Deployment requires a one-time restart of the resident worker. Future Build actions reuse normal Club refresh values and do not require a separate menu change to read each selected player.

Installation was deferred at the user's request. The ready bundle is preserved in `dist/FC26_Companion_2_10_45`; the installed Companion 2.10.44 and worker 2.6.28 are unchanged. Validation and package fingerprint are recorded in `logs/planner_refresh_2_10_45_validation.json`. App and worker rollback copies are retained in `build/rollback_companion_2_10_44_20260930` and `build/rollback_worker_before_2_6_29_20260930`.
