# Squad plan repair — FC26 Companion 2.10.39

## Runtime diagnosis

The evidence was read from `logs/companion.log`, the live `state.sqlite` database in read-only mode, and the worker result files. No FC process memory was read and no new game edit was submitted.

### Why part 2 did not queue at 09:28

Job `01M3GB3ACJGPDX980XA6D1VACH` was submitted at **09:28:54.806 Bangkok time**. Its worker result records `started_at=1790476192` (**09:29:52**) and `finished_at=1790476193` (**09:29:53**), with **1382 ms** execution time and a **5000 ms** budget. All eight shirt writes succeeded.

The worker started roughly 57 seconds after submission. The old `_submit_plan_parts` stopped as soon as its 45-second follow returned a non-success, including a non-terminal queued/running result. Thus the continuation was gone before part 1 actually ran.

There was also a separate result-read failure: `env.snapshot.path` contains a raw Windows path with single backslashes, including `C:\Users\...`. This is invalid JSON. The strict reader returned `None` even though the file said `ok: true`, `outcome: applied`, and all eight operations succeeded. SQLite then stored **EXPIRED (3)** at **09:29:55.655**, with “Queue entry is missing after the grace period.” The 09:28 database verdict was not CRASHED (0).

The older `01M3EQ51SSA3B9V907FRNCK3WD` database row did contain a synthetic CRASHED verdict while its final result reports a successful 560-field apply. Its file has the same unescaped snapshot-path issue.

### Why the apply job had no player attributes

`stage_per_player_plan` extracts `jerseynumber` for the shirt planner but previously also passed it into `sanitize_patch` as a player attribute. `normalize_patch` correctly rejects it: `jerseynumber` belongs to `teamplayerlinks`, not `players`. Because that rejection occurred per player, the entire attribute patch for that player was discarded, while the separately staged shirt changes survived.

A local reproduction with 21 selected players and mixed shirt/PlayStyle+ proposals produced **20 session targets, zero override fields, and 20 shirt changes** before the fix. The missing target was the goalkeeper, removed by the old default scope. This matches the observed 21-player opening followed by a 20-player, zero-field job.

The original provider response was not retained, so the exact PlayStyle+ masks proposed at 09:28 cannot be reconstructed. This report does not claim the provider returned any particular masks. The rejection of a mixed shirt/attribute patch is reproduced directly from the staging code.

### Did no-ceiling reach Apply?

Python parses both supplied prompt variants as `unlimited_playstyles=True`, with playstyles enabled. The 09:28 apply job contains no `players` operations or `icontrait1`/`icontrait2` writes, so PlayStyle+ did not reach that apply.

After this fix, regression tests follow no-ceiling through staging, preview and generated apply operations for **20, 21 and 22 players**, including the goalkeeper. They retain `icontrait1=255` and `icontrait2=15` (12 total bits) below 85 OVR. Switching the session ceiling back on retains the standard two-bit limit.

## Changes

- Extract shirt proposals before attribute sanitization. Keep unknown attribute names and invalid values rejected; one invalid player's patch does not drop the other players.
- Keep selected goalkeepers unless explicitly excluded. AI interpretation cannot silently narrow that keeper scope. Combined named-shirt and PlayStyle requests cannot take the shirt-only shortcut.
- Keep the split-plan continuation alive across non-terminal follow returns. Submit each next part only after the previous part really succeeds. Genuine terminal failures stop the chain. Report worker/queue exceptions with the affected part and reason.
- Preserve the existing **45-second follow**, **60-second missing-queue grace**, **eight shirt operations per job**, **5000 ms worker cap**, safe swap ordering, field validation and confirm-APPLY dialog.
- Bind all parts to the save UID at Apply, so a later Career save change cannot redirect remaining parts.
- Treat a premature crash result as pending when its claim belongs to the live worker or is still being stamped. PID zero continues to use host-process evidence. Allow a brief replacement window for an owner-less synthetic crash whose claim just disappeared.
- Read worker results with a narrow compatibility repair for absolute Windows paths in known path properties, followed by strict JSON decoding. Job/config/grant JSON parsing remains strict. New squad snapshot paths use forward slashes so the loaded worker does not reproduce the path-escape fault.
- Partial result files cannot carry a terminal success verdict, even if they contain an explicit outcome field.
- On startup, reconcile synthetic crash/missing-queue history verdicts against real terminal worker results. This updates reporting only and never requeues old work or invents missing parts. Finished-job history replaces an existing entry for the same job ID instead of duplicating it.
- Keep group-failure warnings when successful proposals also return Library hints. Disable duplicate Apply while a plan is applying and keep pending status visible.

All product changes are Python-side. The Lua core remains **2.6.26**. No new operation, worker installation or game restart is required for these changes.

## Validation

**246 tests passed**:

```text
python -m pytest tests/v2/test_squad_plan_recovery.py tests/v2/test_squad_jersey.py tests/v2/test_squad_grok_session.py tests/v2/test_ui_widgets.py tests/v2/test_contracts.py tests/v2/test_apply_command.py tests/v2/test_squad_planner.py tests/v2/test_app_layer.py tests/v2/test_squad_sync_runtime_trace.py -q
```

Coverage includes the requested Shift selection cases, standard/unlimited PlayStyle ceilings, partial provider failure, safe shirt batching, premature-crash replacement, delayed first execution with a malformed result path, database correction, invalid-player isolation, terminal failure stopping and later-part queue exceptions. The delayed-execution case runs for 20, 21 and 22 selected players.

Existing tests that required silently dropping a selected goalkeeper were updated to the requested explicit-exclusion behavior. Stale tests that expected older Club-page text or version 2.10.36 were updated to the current planner placement and release version.

Both actual malformed success files now decode as **APPLIED** through the compatibility reader. Testing used temporary queues and databases; it did not call a live AI provider or apply a new plan inside FC26.

## Executable

`C:\Users\prated\Desktop\FC 26 LE v26.3.5\LE_Profile_Executor\FC26_Companion.exe`

Packaging completed successfully on 27 September 2026. The final executable was reopened and is responding. Build log: `logs/build_squad_plan_fix_2_10_39.log`.

The interim build was opened during packaging and reconciled 28 historical verdicts. Its reporting timestamps and Undo marker were then corrected from the worker artifacts, with SQLite backups retained under `snapshots/`. The final code prevents historical corrections from changing Undo or moving old finish times to today.

The disputed records now read:

| Job | Recorded outcome | Written | Worker execution |
| --- | --- | --- | --- |
| `01M3GB3ACJGPDX980XA6D1VACH` | APPLIED | 8 | 1382 ms |
| `01M3EQ51SSA3B9V907FRNCK3WD` | APPLIED | 560 | 1591 ms |
| `01M3F24E53GC8Q92FRGYQQ1Z3G` | PARTIAL | 31 | 5039 ms |

Part 1 of the old split plan remains only part 1; reporting correction did not queue its missing part 2. A new plan must be rebuilt, reviewed and confirmed with APPLY. No new live plan was applied by this repair.
