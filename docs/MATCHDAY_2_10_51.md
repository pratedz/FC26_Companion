# Matchday — Companion 2.10.51

Automations is now Matchday. The page focuses on preparing the currently loaded
Career squad with one button. Profile inventories, diagnostic shortcuts and queue
administration have been removed from this page. Activity still owns job details,
cancellation and retry, including typed retries of older Automation jobs.

## Playing a match

1. Load your Career save in FC 26 with Live Editor.
2. Open Matchday and press **Prepare squad**.
3. Open Team Management or return to the Career hub to trigger a safe game event.
4. Read the preparation result on Matchday, then play.

The default routine preserves the previous Prepare squad settings: fitness 100,
sharpness 100, form 100 and morale 120. Numbers are requested targets, not readings
of current player condition. Completion confirms that FC accepted the calls;
these transient Career values have no supported read-back getter.

**Adjust routine** lets you turn off individual boosts. Choices are remembered in
the existing preferences database. Optional injury recovery is off by default.
When enabled, it clears injuries found on the current senior squad using the
existing guarded injury implementation. It requires resident core 2.6.31.

Preparation resolves current senior-team membership inside FC. Submission rereads
worker liveness and binds the job to its reported Career save. A cached team ID is
used only as a checked hint when the cache belongs to the same save and worker
session and is fresh. An empty squad cache does not require a Club refresh.

While a preparation is pending, the button is disabled to prevent duplicate jobs.
The guard survives Companion restarts. Pending preparation remains visible when
switching saves; completed receipts appear only for their own save. The result
card tracks preparation jobs rather than unrelated player edits or exports.
Failures and partial results remain visible with an Activity link. Successful
completion enables the button for the next match.

## Validation

- 1,119 tests passed; 30 GUI/optional tests skipped under the normal test guard.
- 22 Matchday command/recipe cases cover defaults, selected steps, recovery,
  preference persistence, duplicate submission, restart recovery, save switching,
  exact receipts, event-before-database ordering and safe typed retries.
- Separate real CTk checks run invisibly with isolated fake data at 800×600 and
  1040×700 content sizes, exercising routine controls, pending and completed
  states, retained selections and control bounds.
- The desktop executable is rebuilt and deployed with a recoverable previous
  runtime and SQLite backup. Existing user data and the installed LE worker are
  preserved. No FC writes are performed by the release checks.

The UI and submission flow were validated without an active game. The existing
Career setters and injury worker are reused; the new UI has not been exercised
against a running FC match during this release.
