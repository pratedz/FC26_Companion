# Club planning implementation — 2.10.43

Implemented September 30, 2026, using three parallel agents plus integration and independent review. The original review describes the 2.10.41 baseline and the first fixes in 2.10.42. This release implements its first and second priorities, the signing/team recommendations, and the main efficiency recommendations. Existing unrelated working changes were preserved.

## Career save and complete recovery

- Drafts capture the original Career save, club and export revision. Applying checks that identity and affected membership. An unchanged periodic squad refresh remains usable; materially changed export values require a rebuild. Each queued job carries the original save requirement and the updated worker requirement.
- The worker checks the active Career club and club membership immediately before scoped player writes. Club-shirt snapshots and writes use both player and club keys, preserving national-team links. An unexpectedly occupied shirt stops the reviewed write rather than displacing an unreviewed player.
- Every write part receives its own pre-write snapshot. An atomic durable parent record stores jobs, status, results, retries and progress before submission. Activity → Squad plans shows one grouped receipt and per-field before/planned/read-back values.
- Resume reconciles existing queued results and retains successful writes. A failed part retries unfinished operations; uncertain writes with no queue/result evidence stop for inspection rather than being blindly replayed.
- Undo restores original values from all relevant attempt snapshots and orders club-shirt swaps safely. Apply and undo cannot run concurrently for the same parent. Undo uses the normal explicit UNDO confirmation. The original plan cannot be resumed after its undo has started.
- Final verification separately reads the player and club-shirt fields. Pending read-back remains pending, and a mismatch reports incomplete verification. Verify again re-reads the final values without replaying successful writes.

## Predictable request and selection

- One Build/Rebuild action consumes the current request. Editing the request invalidates Apply while retaining the previous preview for comparison.
- A window-owned controller keeps the original roster separate from included players. Excluding, rebuilding and including a player again preserves that player. Whole-club planning is available and selections above 25 are retained; only submitted write jobs are divided into bounded parts.
- Explicit negative instructions override general improvement requests, including excluded attribute families, names and positions. Age changes require a requested age. When a live Career date is unavailable, the review explicitly identifies the FC26 season-start reference and shows a human-readable birth date.
- Protect starters uses known or user-assigned starter IDs. Unknown starters block that request with an explanation; the highest-rated selected players are never silently treated as the XI. Protected names are visible before building.

## Sign, team context and reusable requests

- Verified signing results provide actual Career player IDs. The Sign handoff offers Plan these N signed players once those players are present in a fresh export of the same save and club. Partial successes remain available with reviewed, verified, waiting and unconfirmed counts.
- Club has a Recently Added filter. Signing receipts can be restored from the existing durable audit after restart. Open Club from a plan receipt returns to the affected IDs and revalidates their save, club and current membership.
- Optional controls choose a planning formation, starter/rotation/prospect roles and preferred overall range. Formation and roles reach the AI proposal payload and cache keys. Formation guides profiles and coverage; it does not change in-game tactics.
- The review compares the selected planning roster before and after: formation-slot coverage, known starter/other-player strength, pace, passing and defence. Unknown statistics and an unknown XI are labelled explicitly. One flexible player cannot fill multiple formation slots in the coverage calculation.
- Built-in request presets and named saved requests support repeating skills/Quick Step+, richer styles, familiar shirts and prospect improvements after another signing batch.

## Efficiency and asynchronous behavior

- Planner task keys belong to each window. Closing cancels its planning tasks, and generation checks reject old callbacks after a request change or close.
- Bounded per-window caches reuse valid per-player proposals by source scope, live values, effective recipe, target, request and provider/model. Changed or failed players are regenerated; valid groups survive a sibling failure. Library hints are cached within the window.
- Review changes are indexed once per immutable preview rather than scanning the whole matrix for each player. The index retains at most four previews.
- Detailed multi-player snapshots scan valid records once. Worker membership is indexed once per job rather than repeating a club-link scan for each player operation.

## Verification and deployment

Verification uses real hidden/offscreen UI widgets, mocked AI and transport, a durable fake worker and an actual Lua runtime with a fake database. It does not perform paid AI requests, sign live players or change a Career save. Final live-game timing and in-game outcomes still require a normal Career session.

The regression suites cover 30-player selection restoration, current-request rebuilding, real-window sizing, save/club switches, a failed middle part followed by restart, partial operation retry, safe cyclic shirt undo, national-team preservation, queued-result reconciliation, final mismatches, pending verification and runner-lock races. The final focused run passed 279 tests with 11 skips; the opt-in real-widget/window run passed 20 tests. The broader v2 run passed 1,000 tests with 30 skips and one pre-existing timer source-check failure (`test_fast_read_timer_starts_after_le_init_and_retries_on_career_post`). The resident timer behavior was not changed by this work.

Application: 2.10.43. Resident worker: 2.6.28. Existing historical jobs from older releases cannot gain missing rollback snapshots retroactively; complete recovery applies to newly recorded plans.

The application and portable bundle were built and installed, and their executable hashes match the verified distribution. The package contains every new controller/recovery/handoff/cache module and the matching Lua worker. With FC26, Live Editor and Companion stopped, the configured resident worker was backed up and updated. Installer status confirms core 2.6.28, matching worker content and the correct queue path; the existing `add_to_team` opt-in was preserved. The worker will load at the next normal game/Live Editor start.

Verification record: `logs/priority_implementation_verification_2_10_43.json`. Build log: `logs/build_priority_implementation_2_10_43.log`. Worker backup: `build/rollback_worker_2_6_28_20260930_123526`.
