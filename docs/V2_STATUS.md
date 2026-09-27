# v2 — what shipped, what's designed, what's next

Session of 2026-07-26. Test suite went **192 → 565 passing**, tree compiles clean,
zero undefined names, and the suite no longer mutates live user state.

---

## 1. The three reported bugs, root-caused

### "Crash"
`CreatePlayer` with a playerid **>= 500000 terminates FC26.exe**. The allocator's
ceiling was 559,999, so ~61% of created players landed in the kill zone.
Evidence is a clean split in `queue/_add_team_crash.log` vs `Logs/live_editor_*.log`:
ids 463139..494215 all completed; 506153..546619 all killed the process.

Fixed in `add_player.GENERATED_ID_MAX` (now 499999) plus a `HARD_MAX` guard in the
generated Lua so a stale preferred id can never reach `CreatePlayer`.

### "No face"
`hashighqualityhead` was hardcoded to `1`. It is **not** an "is real face" flag — it
selects between two mesh pipelines and is **0 for ~86% of players**, including
Zidane, Pele and van Basten. Pointing the game at a nonexistent HQ head is what
produced blank faces, and it broke precisely the legends worth importing.
`headtypecode` was additionally zeroed by the visual defaults, destroying e.g.
Zidane's 2502.

New `src/base_players.py` resolves the real quartet from `player_presets/base_players.csv`
(22,348 rows x 149 columns, previously almost unused). Falls back to the
always-safe generic combo (`hq=0, headclasscode=1, headassetid=0`) when a player
has no scanned head. Same fix applied to `import_player`'s fallback path.

### "No name"
Three compounding faults:
1. `InsertDBTableRow` was called unconditionally, **three times per job**, so
   duplicate `editedplayernames` rows accumulated and the game resolved the
   oldest — the logs show writing "Marco van Basten" and the game returning
   "Zidane" from 28 minutes earlier.
2. Success was never validated. `pcall` returns true even when the insert fails;
   LE's contract is `row.addr == "0"` (and in practice `addr` arrived `nil`), so
   **every** `name_insert_ok` line in the logs was actually a failure.
3. `usercaneditname` was stripped from the create payload, so created players
   could not be renamed at all.

Now: update-if-exists, insert exactly once, validate `addr`, log `name_insert_fail`
honestly.

---

## 2. Arming friction removed

LE auto-executes `<data dir>/lua/autorun/*.lua` at startup. The data dir comes from
`HKLM\SOFTWARE\Live Editor\FC 26` -> `Data Dir`. A loader stub is now installed
there, so **the per-session paste into LE's Lua Engine is gone**.

Evidence of how bad this was: 45 manual Lua-Engine interactions over 6 days, and
only 30 of 80 LE launches (37%) ever got armed.

The stub arms handlers only — draining during LE init is what froze the editor
when auto-arm was previously attempted by patching `live_editor.lua`.

Also fixed: the bridge registered **four event names that do not exist** in
`FCLiveEditor.DLL` (`postCareerModeLoadedFromSave`, `eventCareerModeHubEntered`,
`postMatch`, `eventPostMatch`). `AddEventHandler` accepts any string silently, so
a third of its wiring never dispatched. Only four real events exist:
`pre__`/`post__CareerModeEvent` and `pre__`/`post__LEInitDoneEvent`.

**Save-corruption risk closed**: the handler took no arguments, so it drained on
all 276 message types — including `PREPARE_FOR_SAVE`. Writing to the player DB
while the game serialises the save is a plausible cause of the historic freezes.
There is now a blackout set resolved by enum name (with a numeric fallback).

---

## 3. Correctness fixes across the import path

| Defect | Effect | Fix |
|---|---|---|
| FUT.GG `gender` passed through | 1 = male in FUT.GG, 0 = male in EA — **every imported player was flagged female**, loading a mismatched rig | mapped |
| `skillmoves` treated as stars | column is 0-indexed, so every import was **one star too good** (FUT.GG, Futbin, editor presets, AI prompt) | `stars_to_skillmoves()` |
| `bodytypecode` clamped 1..20 | real values reach 437; Pele (294) and Zidane (85) got generic builds | clamped 1..437 |
| `SetTransferBudget` | deprecated to a no-op stub in FC 26 — **budget edits did nothing** | `SetUserTransferBudget` / `SetCPUTransferBudget` |
| `PLAYSTYLE2` sparse | bits 6/7 missing; mask 1535 re-encoded to 1343 and every FUT.GG playstyle id >= 36 was shifted | dense 14-bit |
| `internationalrep` allowed 0 | out of range | clamped 1..5 |
| `shohan` composite absent | SHO/HAN never round-tripped; a phantom `"sho"` column was listed instead | all six composites |
| Card apply never renamed | `name_parts` plumbing existed but was never passed — every log line read `names=false` | opt-in `copy_name` |
| Nation table | Nigeria collided with Italy, and **134 of 160 nations resolved to nothing** | generated from real data |

---

## 4. Error reporting made honest

The worst class of bug found: `except Exception as e:` with `e` referenced in a
deferred Tk callback. Python deletes `e` when the block exits, so **every failed
apply crashed its own error handler** — no message, and `_apply_busy` never
cleared, leaving the Apply button stuck until restart. 13 instances.

Also fixed:
- `web_api` reported `ok=True` for hard failures (the expression included
  `or r.queued`, and the failure branch sets `queued=True`).
- A job that wrote nothing reported APPLIED — the generated Lua returned cleanly
  when the players table was nil and never wrote a status line.
- The `written` counter discarded its `pcall` result, so `written=91` could mean
  91 silent failures.
- Lua error text never left the game process; the bridge now carries it back.
- `boost_queue` declared success purely from file absence, and its `done/` scan
  matched `skipped_*` and `poison_*`.
- A distinct `timeout` outcome now exists (a 90-second hang and a never-collected
  job were both reported as `queued_live`).
- The pre-apply snapshot failing was swallowed — the apply proceeded and the user
  lost their only rollback with no warning.
- `_wake.txt` wrote `force 1`, but the bridge only recognises `force_drain`; the
  re-wake also resurrected already-archived job names (19 "miss" events).

---

## 5. New capability

| Module | What it gives you |
|---|---|
| `src/base_players.py` | authoritative identity/appearance for 22,348 FC 26 players |
| `src/fc26_data.py` + `src/generated/` | 5,826 real-head ids, 5,737 head names, 160 nations, 51 leagues, 662 clubs, 105 role names, empirical generic-head table |
| `src/universe.py` + `card_db/universe.sqlite` | **55,624 players / 238,692 observations across FIFA 18 - FC 26**, FTS5 search, sub-3ms queries |
| `src/teams_directory.py` + `card_db/teams.sqlite` | 931 teams / 73 leagues / 174 nations — this is what unblocks every career operation |
| `src/growth_xp.py` | the development-plan mirror, so edits stop reverting |
| `src/squad_snapshot.py` | squad-level snapshot / restore / diff before a batch |
| `tests/lua_lint.py` | structural validation of generated Lua (see below) |

### Generated Lua is now validated
Six modules build Lua by f-string templating and ship it into the game, where a
syntax error is invisible: the chunk fails to load and the user sees a generic
failure with no line number. There was **no validation at all**. There is no Lua
interpreter available here, so `tests/lua_lint.py` balances block structure — the
failure mode template edits actually produce — and enforces LE safety rules
(no `ReloadPlayersManager`, no shell calls). Every generator, both bridge copies
and the autorun stub are covered.

---

## 6. OPEN QUESTION — resolve before trusting the growth mirror

**What value does `PlayerSetValueInDevelopementPlan` want?**

- LE's `DOC.MD` prose: *"Use this to set corresponding XP points for given field."*
- LE's own example on the same page: `PlayerSetValueInDevelopementPlan(158023, "composure", 99)` — which reads as a raw attribute.
- xAranaktu's FC 25 Cheat Table converts attribute -> XP through a 99-entry curve.
- v1 of this app has always written **raw** values with `dev=true` in its logs, and
  nobody reported collapsed stats — which XP semantics would cause, since 99 XP
  maps to attribute 1 and the plan outranks the players table.

`growth_xp.DEFAULT_VALUE_MODE = "raw"` — the behaviour already proven in the field.
`mode="xp"` is available.

**To settle it**: on a live save, apply a known attribute value to a player in your
club, advance one day, and read the attribute back. If it collapsed, raw is wrong.
Do not flip the default on documentation alone — the failure mode is a visibly
ruined player.

---

## 7. Designs ready to build

| Doc | Contents |
|---|---|
| `V2_ARCHITECTURE.md` | dual-mode split, protocol v3 (one job / one result file), module seams, migration plan |
| `V2_UX_DESIGN.md` | 8 tabs -> 5 surfaces organised by career-manager job, complete feature-migration table, wireframes, click-count targets |
| `V2_INGAME_CORE.md` | resident Lua core, declarative job ops, player index, resumable long jobs, mock-LE test harness |
| `V2_FEATURE_PORT.md` | full Cheat Engine inventory with per-feature portability verdicts; **85% needs no assembly** |
| `V2_NEW_FEATURES.md` | 17 features in three tiers, with a deliberate "don't build" section |

### Two findings from the design work worth acting on
- **A security hole in the current protocol**: the Lua side validates a pending
  job name only by prefix and suffix, then `load()`s and `pcall()`s it. Anything
  that can append a line to `_pending.txt` gets arbitrary code execution inside
  FC 26. Protocol v3 closes this by executing declarative ops rather than source.
- **Write success cannot be detected by `pcall`**: `FIELD:SetInt` ends in an
  unconditional `return true` and truncates out-of-range values bitwise;
  `FIELD:SetString` has no `return` at all. Read-back is the only honest signal.

---

## 8. Needs a live game to verify

Nothing below could be tested offline; the game was not running this session.

1. The autorun stub actually arms on LE startup (check the log for
   `[LE_Companion] autorun armed`).
2. The growth-mirror value semantics (section 6).
3. That a real-face import now produces a correct face for a `hq=0` legend.
4. That an add-to-team below id 500000 completes without killing the process.
5. That the `PREPARE_FOR_SAVE` blackout does not starve legitimate drains.
