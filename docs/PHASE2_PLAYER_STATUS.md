# Phase 2 — canonical player flow

Status: **implemented and offline verified; live read/write remains a live gate**.

## User flow

1. Select a row on Club, select a Library result, or enter a numeric ID on
   Player.
2. A squad/catalog row is shown immediately when it exists. No default rating
   or other invented value is inserted.
3. `Reload live` submits one bounded `snapshot` operation for exactly the
   locked player ID and the canonical editable field list.
4. Edits are staged in `EditorState.dirty`. Changing target clears the previous
   target's staging. Reload refuses to discard staged changes.
5. Apply validates every field and value before creating a queue job. Unknown
   fields and out-of-range values are rejected, not silently dropped/clamped.
6. Only a verified `APPLIED` result promotes the exact submitted patch into
   the editor base. Partial, failed, no-op, late, or cross-target results do not
   clear newer user work.

## Canonical model

`companion/domain/player.py` owns the v2 player schema. It covers ratings, all
outfield and goalkeeper attributes, skills/foot, seven position slots, five
role slots, PlayStyle banks, body, animation, appearance, kit/accessories,
tattoos, career/identity, and composite face stats.

The UI, live-read command, and apply validation consume that same schema.
Canonical v2 does not import `src/player_schema.py`.

## Offline evidence

- Full v2 suite after integration: 398 passed, 33 skipped.
- Player/apply focused suite after safe reload/commit additions: 45 passed.
- Python compilation: passed.

## Live gates

- Confirm all requested players-table columns can be read on FC 26 LE v26.3.5.
- Confirm snapshot results return the selected player while Career Mode is
  loaded.
- Apply one disposable-save field, verify read-back, advance a day, and confirm
  the growth mirror persists it.
- Confirm the real GUI retains staged edits when a late live-read result
  arrives.
