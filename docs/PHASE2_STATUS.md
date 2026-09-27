# Phase 2 status — v2.1.0

## Fixed live-session blocker

The screenshot showing a green `ARMED` badge with no squad was caused by a
stale protocol-v3 session marker from an earlier FC 26 process. Only the legacy
v1 bridge was alive, so v2 jobs could be queued but never drained.

V2 now:

- rejects worker sessions from an older FC 26 process;
- rejects obsolete or mismatched core capabilities and versions;
- reports that Live Editor and FC 26 must be restarted instead of showing a
  false green state;
- automatically requests one squad read when a fresh compatible worker session
  appears;
- recovers squad results after timeouts or application restarts;
- preserves the last good squad when an export fails or returns no players;
- reports actionable Career Mode/export errors.

## Player and squad

- real squad rows lock one app-wide player target and open Player;
- manual numeric player targeting is available;
- player IDs outside the safe FC 26 range are rejected before transport;
- exact-player live reload uses the read-only snapshot operation;
- the editor exposes the canonical categorized FC 26 field schema;
- unknown fields and out-of-range values are rejected before queue submission;
- switching targets clears the previous target's staged patch;
- live reload cannot overwrite newer staged changes;
- verified applies promote only the exact submitted patch;
- successful late results can recover editor state after the original callback
  or application process is gone.

## Library

- local multi-year search through `universe.sqlite`;
- year and OVR filters;
- variant browsing;
- best-match ranking;
- comparison foundation;
- persistent SQLite favorites;
- safe cross-year import plans that stage supported fields;
- stable Career `person_id` is kept separate from FUT `variant_id`;
- historical cards never replace an already selected live target;
- staging navigates directly to Player.

## Verification

- complete v2 suite: 401 passed, 33 environment skips;
- real window-opening Tk suite: 30 passed;
- whole repository: 1003 passed, 33 skipped, one pre-existing v1 DPAPI failure;
- local Neymar catalog smoke: search, variants and cross-year staging passed;
- installer now rejects an installed core whose version or bytes differ from
  the core shipped with the application.

## Remaining live gates

1. Install the v2.1.0 worker and restart Live Editor and FC 26.
2. Confirm the pending read-only squad request is claimed and returns the
   currently loaded Career Mode squad.
3. Reload a real player and verify the snapshot column set against the current
   FC 26 database build.
4. On a disposable save, apply a reviewed patch and confirm read-back and
   persistence across an in-game day.
5. Visually inspect the large player field editor and Library actions at the
   user's normal game resolution.

Write-capable behavior remains `LIVE-GATE` until these checks pass.
