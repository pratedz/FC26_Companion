# Plan these players: Career Mode workflow review

Reviewed September 30, 2026. Baseline: current working source for Companion 2.10.41, including the existing uncommitted review redesign. The reported four-part request was then fixed in 2.10.42 with parallel agents. Existing unrelated edits were preserved.

The first and second priorities, signing/team recommendations and main efficiency recommendations below have subsequently been implemented in 2.10.43. See [the implementation record](PLAN_THESE_PLAYERS_IMPLEMENTATION_2_10_43.md) for the resulting behavior; this review remains the baseline assessment.

## Assessment as a Career Mode player

I would use this feature after signing a group of players to give each a useful profile, restore familiar shirt numbers, and bring the squad into a chosen strength range. The current split review is a good foundation: I can inspect one player at a time, see named PlayStyle+ changes and compare real values, with Apply always accessible.

The feature primarily edits player profiles. To help me improve my *team*, it also needs formation, actual starters, bench roles, positional coverage and a clear handoff from a successful signing batch. Those are the most valuable product additions after correctness and recovery.

## Current flow

1. Search or obtain recommendations in Sign; add verified local cards to the Signing Bag.
2. Review and queue the signing. Applied writes invalidate the Club cache; the existing automatic squad coordinator reads it again when Career Mode is ready.
3. Open Club and tick the players. Two or more checked players open Plan these players; a single checked player opens the Player surface instead.
4. Enter a request and choose Update plan. Python interprets recognizable scope and exact targets, and the AI handles broader interpretation. General interpretation automatically starts building, while the named-overall shortcut asks for a separate Build/Rebuild.
5. Read detailed player values when needed, build proposals in groups of five, and inspect the review. The build overlaps up to three AI groups.
6. Adjust included players, overall targets or the ceiling switch, then rebuild. Type APPLY to queue a multi-player write.
7. Apply runs player-field and shirt jobs in sequence. A part waits for a terminal outcome before the next starts. Successful writes invalidate Club for a fresh squad read.

Important distinctions: a queued signing has not yet joined the squad; a ready plan has not yet changed the game; a successful final job is not a complete receipt for all earlier parts.

## What is good

- Per-player proposals preserve more character than applying identical numbers across a whole squad. Explicit named overall targets are pinned locally, and position/numeric scope is reapplied after AI interpretation.
- The review has separate scroll regions, search, range selection and persistent actions. Searching reuses row widgets. Progress updates avoid rebuilding the entire player list.
- Named PlayStyles are encoded locally. Field bounds and valid storage masks remain enforced even with the count ceiling disabled.
- Unrequested identity, body, age and skill fields are generally filtered out. The default no-downgrade rule protects known outfield ratings, although this relies on having detailed current values.
- Shirt writes have deliberate ordering, including temporary spare numbers for swaps. Additional squad members whose shirts must move appear in the review.
- Applying is separated from AI generation, requires multi-player confirmation and uses the normal job transport. Waiting for Career Mode is treated as waiting, rather than immediately failing the entire sequence.
- Sign already keeps verified additions scoped to the current Career save/team, including actual resulting player IDs. That is a useful foundation for a direct planning handoff.

## The reported 20-player request: causes and changes

| Problem | Confirmed cause | Behavior in 2.10.42 |
| --- | --- | --- |
| Not everyone got five-star skill moves | The parser missed “5 skill star move.” Parsed star levels were only hints, and the AI prompt explicitly treated them as optional direction. | Recognizes the user's wording and writes the exact five-star value to every selected player, including keepers. The raw value is 4; the review displays 5★. Mandatory fields survive patch truncation. |
| Not everyone got Quick Step+ | Named styles were passed to the AI as hints and never required by local staging. | Quick Step+ is added locally to every selected outfield player, preserving existing and additional proposed styles. Keeper exceptions are shown explicitly, with applicable keeper styles for the general improvement request. |
| Ronaldinho could get #40 | AI shirt suggestions were processed first and locked the player before familiar-number recommendations. Shared signature numbers could leave later players with arbitrary old numbers. | User-specified shirts win first. Deterministic recommendations cover all selected players and reserve signature choices before alternatives. AI #40 cannot suppress Ronaldinho's preferred #10. If another player owns the priority for #10, a familiar alternative such as #80 is deliberate and explained. |
| No ceiling still produced too few styles | Removing the ceiling imposed no useful density, and the prompt still associated higher OVR with small sets. | An explicit unlimited improvement gets a substantial profile based on position and strongest live attributes. Valid sparse drafts are completed locally, preserving existing and AI-selected additions above the defaults. |
| A normal Club export skipped the detailed read | `needs_live_base` counted dictionary entries. A 13-field roster row contains no stamina or style banks but passed the old ten-field threshold. | Read eligibility checks the actual attributes, style banks and requested skill fields. An incomplete read blocks building the whole selection instead of silently removing unread players. |
| Detailed reads could repeatedly scan the player table | The snapshot operation called a table scan independently for each player. | A multi-player read scans valid records once, preserves request order and reads fields only for matching players. |

The richer defaults are **minimums, not caps**:

| Target overall | Outfield: regular / Plus | Goalkeeper: regular / Plus |
| --- | --- | --- |
| Below 88 | 8 / 6 | 4 / 3 |
| 88–92 | 12 / 8 | 6 / 5 |
| 93+ | 14 / 10 | 6 / 6 |

These apply to an explicit improve/add/boost request with no ceiling. Removing a ceiling alone preserves current styles without forcing this density. An explicit numeric count or an “only” request bypasses the default density; enforcing arbitrary exact style counts is a separate capability, so this is not a promise that every such count is currently guaranteed. Keepers have six supported keeper styles. Existing higher valid counts remain intact.

Missing or invalid general AI proposals still block Apply. Local completion enriches a valid sparse proposal; it does not conceal a failed player. The exact skills/Quick Step requests can be filled locally, but the broader requested build must pass its proposal contract.

The review now shows request completion counts, skill stars, actual Plus counts, selected-player reasons and relevant shirt conflict explanations. It hides completion checks after selection/target/ceiling edits until a new preview is ready.

## Remaining limitations and recommendations

### First priority: save binding and complete recovery

**Bind the draft to the save and club where it was created.** `SquadPlan` has no original save/session identity. `apply_squad_plan` copies the *current* squad save ID into the outgoing job at Apply time. A fake-transport probe confirmed that an old draft for club A can be submitted with save B's requirement after switching saves. Store original save UID, team ID and relevant snapshot revision in the draft, reject a mismatch, and revalidate membership before writing.

**Capture every applied part and aggregate the result.** `_submit_plan_parts` gives only the first part a `snapshot_to` path. The baseline 20-player combined probe had 41 operations in six parts, with only eight operations covered by that snapshot. Later parts can change players and shirts without corresponding undo coverage. Capture each part or a complete pre-plan snapshot, group the parts under one parent plan, and give one receipt listing every completed/failed player and field. Shirt undo also needs safe collision ordering and team-scoped keys.

**Persist plan progress for restart recovery.** The sequence lives in a worker closure. After an app restart, queued-job reconciliation exists, but there is no persisted parent plan that can resume the unsent remaining parts. Persist the ordered parts, cursor and results. Offer retry of failed/unsubmitted work rather than rebuilding and repeating a successful batch.

### Second priority: predictable selection and request flow

**Keep the original roster separate from the included selection.** Rebuild currently calls `keeping(refined.playerids)` and replaces the plan/session with the smaller selection. A probe excluding one of two players left only the other in the next draft. Keep all original members and an inclusion set so “All” and checking a player back on work after a rebuild.

**Use one request/build action.** The top Update plan sometimes triggers the entire build and sometimes only prepares targets; the bottom Build/Rebuild uses the existing session instead of reading the current request entry. Editing the text does not itself invalidate the old preview. Use one Build/Rebuild action that consumes the current text, make a text change mark the preview stale, and preserve earlier proposals while a rebuild is running.

**Respect negative instructions explicitly.** A baseline parser probe of “improve stamina but do not change age, height, weight or identity” enabled the age/body/identity families because it matched the nouns without interpreting their negation. Age staging then uses its own peak-age heuristic. Explicit exclusions must take precedence, and age changes should use an actual requested age or clearly reviewed target.

**Use actual starters for protection.** “Protect starters” currently means the highest-OVR eleven within the selected roster. Selecting two bench players with that wording protects both and leaves nothing to edit. Use the live lineup or a user-defined starting XI; display the protected names before building.

**Expose the 25-player limit before discarding anyone.** The Club path caps a large selection at the highest-OVR 25 and sends its warning to status outside the new window. The domain constructor also truncates at 25. For a large signing project, show the omitted players and offer explicit batches, or let the planning selection contain the whole squad while splitting only the writes.

### Third priority: connect signing to team improvement

- After verified signing success and the resulting squad refresh, show “Plan these 20 signed players.” Use the returned Career player IDs rather than catalog IDs or names. Keep Recently Added available as a Club filter and preserve the signing batch context.
- Let players choose a formation, actual starter/rotation/prospect roles and a preferred overall range. Current role ranking uses OVR and potential gaps; it does not know the user's intended lineup.
- Show positional coverage and a team-level before/after summary: starters, bench strength, pace/passing/defensive balance and which weaknesses the plan addresses. The current average-OVR squad summary cannot establish this on its own.
- Add saved requests such as “five-star skills + Quick Step+,” “rich PlayStyle profiles,” and “familiar shirts.” Show the exact scope before building and allow repeat use after the next signing batch.
- Show planned versus read-back outcomes after Apply, with “Open Club” returning to the affected players. “Plan applied” should be supported by a complete multi-part receipt.

## Code and efficiency assessment

The domain/command/transport split provides a useful boundary: proposal code cannot directly submit game writes, and field validation is reusable. The largest maintenance concern is the planner's shared state dictionary and callbacks, which mix request interpretation, selection, detailed reads, asynchronous proposal generation and application. Move these phases into a typed session/controller with a transition model, request revision and per-window task IDs.

Current task names such as `squad.grok.interpret` and `squad.grok.session` are shared. The executor cancels an older task when another task with the same name starts, so multiple planner windows can interfere. Window close does not explicitly cancel its requests, and queued UI callbacks lack a window/request-generation check. Use per-window keys, cancellation on close and generation checks before accepting any callback.

A normal 20-player general build uses one interpretation request plus four proposal groups, overlapping up to three groups. Missing/invalid players may require a repair request. Rebuild currently regenerates the entire included selection. Cache proposals by player ID + detailed base revision + effective recipe + target; regenerate only affected players, and retain valid groups when one fails. Cache Library hints too; they are currently looked up per player for each build and contain a descriptive card hint rather than a complete imported card profile.

Keep the improvements already present: bounded player selection, row reuse during search, independent scrolling, background AI work and sequential safe shirt writes. The new snapshot pass removes a significant repeated database scan. The review still computes the whole change matrix when summarizing each player, roughly O(players² × fields); index the changed rows once per draft if profiling shows this matters. At the current 25-player cap, this is lower priority than unnecessary AI calls and database scans.

## Verification and limits

The baseline focused run returned 104 passed, two failed and four skipped. The two failures exposed incomplete goalkeeper Plus proposals being accepted.

After implementation, the focused planner/sign/recovery suites returned 166 passed and four GUI skips. The real Tk review checks were then enabled separately and passed at 1020×740 and 800×600, including the complete four-part 20-player request, visible completion checks and persistent Apply.

The broader v2 run returned 934 passed, 25 skipped and two failures in unchanged Sign/timer source-text checks: `test_update_review_cta_requeries_active_jobs_and_does_not_freeze_the_lock` and `test_fast_read_timer_starts_after_le_init_and_retries_on_career_post`. These should be reconciled with their current intended behavior separately.

Verification uses mocked AI, fake transport, real UI widgets and an actual Lua runtime with a fake database. It verifies proposal/staging/write contracts and a single-pass multi-player read. No paid AI request, live signing, live player edit or Career-save modification was performed. Live-game read timing and final in-game read-back remain unverified.

Packaged verification also exposed an existing Settings import error and a worker-install callback missing its required queue path. Both were corrected. Three additional real-widget Settings tests passed, covering opening the AI settings card and the worker button's success, rejection and exception outcomes with mocked installation.

App release: 2.10.42. Bundled resident core: 2.6.27, required for the updated snapshot implementation. The normal project-root executable and portable bundle were rebuilt and installed; their executable hashes match the verified distribution. Packaged Settings imports and queue-path forwarding were checked, and the installed executable's version command exited successfully. Packaging is recorded in `logs/build_squad_requests_2_10_42.log` and `logs/build_squad_settings_2_10_42.log`; final artifact verification is in `logs/squad_requests_verification_2_10_42.json`.

The resident worker installation remains pending because FC26 is running. Save and close FC26 and Live Editor, open the updated Companion, then use Settings → Install / repair worker before restarting Live Editor and the game. No running game code or Career save was modified.
