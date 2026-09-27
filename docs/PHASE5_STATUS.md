# Phase 5 status — bulk edits and growth persistence

Implemented in Companion/core 2.5.1.

## Delivered

- `bulk_edit` descriptor v2, restricted to an explicit filter or team scope.
- User-team bulk writes mirror verified player attributes into development plans.
- A DB field failure prevents the corresponding growth mirror.
- Growth failures and players without development plans produce an honest partial result.
- `growth_sync` descriptor v2 reads current player-table attributes and resynchronizes
  development plans for explicit player IDs or the user team.
- Growth sync is bounded to 60 players and performs one valid-record players-table pass.
- Automations exposes **Resync Growth Plans** as a one-shot user-team action.
- Activity drawer accepts its live accent colour without crashing.

## Safety and rollout

- No whole-players-table bulk operation is allowed without an explicit filter/team scope.
- No runtime module reload is used. Core 2.5.1 is installed for the next normal FC/LE start.
- Existing FC sessions continue running their already-loaded core; they are never patched
  in place.
- Offline and contract gates pass. Live mutation behavior remains a `LIVE-GATE` until run
  against a disposable Career Mode save and observed across simulated days.
