# Phase 3 and 4 status — v2.2.0

## Phase 3 — verified single-record writes

- Player edits use schema-validated `set_fields` and read-back verification.
- Name upserts refuse duplicate rows and verify inserted values.
- Transfer, loan, release, terminate-loan and list operations use the FC 26
  Live Editor APIs with documented argument order.
- Destination-team operations verify the resulting team id.
- User and CPU transfer budgets use the non-deprecated FC 26 APIs and verify
  the value after a write.
- Every non-dry editor apply captures a pre-write snapshot before changing the
  save. A snapshot failure rejects the job before the first write.
- Successful snapshots are stored in `state.sqlite`; Activity exposes
  **Undo last change**, restored through the same verified write pipeline.

## Phase 4 — experimental add to team

- The app and worker both deny the operation by default.
- Enabling the Club toggle updates only the companion-owned worker config and
  requires a Live Editor restart.
- Library exposes **Add to my team (experimental)** after opt-in.
- The command uses only free-agent ids exported from the currently loaded save,
  protects every current-squad id and never guesses a disposable player.
- The worker runs a ten-step resumable plan:
  resolve team, select dummy, write fields, write face, detach name dictionary,
  write names, transfer, verify team, reapply drifted fields, verify name.
- The transfer step always yields to a later Career Mode event before
  verification.
- Progress is written after each step and resumes by ids, never by stale record
  addresses.
- Repeated process failures are quarantined after three attempts.
- A team or name mismatch is a failed result, never a false success.

## Verification

- Complete headless v2 suite: 427 passed, 33 environment skips.
- Complete real-window v2 suite: 460 passed.
- Python compilation passed.
- Packaged EXE and installed worker are verified separately during deployment.

## Live gates

Phase 3 and Phase 4 write behavior remains `LIVE-GATE` until tested on a
disposable Career Mode save. Phase 4 stays disabled by default until all
documented performance and persistence measurements pass, including:

- longest game-thread stall below 250 ms;
- total add-to-team wall time below 3 seconds;
- zero `GetDBTableRows("players")` calls;
- exactly one edited-name row;
- truthful failure when the requested name does not apply;
- quarantine after three interrupted attempts.
