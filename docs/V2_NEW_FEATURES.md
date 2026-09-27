# V2 — New Features Specification

**Product:** FC 26 Career Mode companion (LE Profile Executor)
**Scope:** features that exist in neither the current companion nor FC 26 Live Editor.
**Audience:** the person playing a Career Mode save who wants it to be *theirs*.
**Status:** proposal. Nothing here is implemented.

---

## 1. Why a v2 at all

Today the player has two tools and a gap between them.

**Live Editor** is a *field editor*. It shows you a player and lets you change numbers on him. It is excellent at that and it is not going to be beaten at it.

**The companion** is a *card applier and script runner*. It searches 339,609 cards across FIFA 18 → FC 26, pastes one onto a playerid, and fires 51 Lua profiles at your squad.

Neither of them is a *career tool*. The gap is everything between "I can change a number" and "I can shape my save":

| What the player wants | What happens today |
|---|---|
| "Bring Zidane into my Chelsea" | You get Zidane's *stats* on a stranger's face, with the wrong name, nationality, age and body. |
| "Make this edit permanent" | Development plans outrank the `players` table and quietly revert it within a few match days. |
| "Plan my whole squad, then commit" | One player at a time, no preview, no diff, no way back. |
| "Put it back how it was" | Undo replays *the card you just applied* — it never read the old values, and it never touches names, faces, transfers or growth. |
| "Build a legends XI" | Eleven separate manual operations, and the free-agent pool is empty so most of them fail. |
| "Keep my squad fit without babysitting it" | Alt-tab and re-run a pack, every single match. |

Everything below builds on the plumbing that already works — the queue protocol, the card catalogue, `base_players.csv`, the Lua bridge — and on capabilities in LE that nobody is currently using.

### 1.1 What the platform actually gives us (verified against this install)

These are the load-bearing facts. Every feature below is built on them.

| Capability | Where it comes from |
|---|---|
| **Stable cross-year identity** | EA `playerid` is the same from FIFA 18 to FC 26. `catalog.sqlite` carries `playerid` on ~85% of 339,609 cards, incl. 17,954 FIFA 18 rows. This is the single fact the whole product rests on. |
| **Full appearance ground truth** | `player_presets/base_players.csv` — 22,348 rows × 149 columns: identity, name-dictionary ids, nationality, birthdate, 44 appearance fields, tattoos, body, kit, PlayStyle bitfields (`trait1/trait2/icontrait1/icontrait2`), roles 1–5. |
| **Real-face registry** | `lua/scripts/fix_players_headmodels.lua` → `valid_headmodels`, **exactly 5,737** `[playerid] = true, -- Name` entries. A face set *and* a free playerid→name map. |
| **Literal custom names** | `InsertDBTableRow("editedplayernames", {...})` accepts arbitrary strings, after zeroing `firstnameid/lastnameid/commonnameid/playerjerseynameid` and setting `usercaneditname=1`. Already proven in `add_team_lua.py`. |
| **Growth XP is writable** | `PlayerHasDevelopementPlan(pid)` + `PlayerSetValueInDevelopementPlan(pid, field, value)` (sets XP points, not display values), and `PlayerDevelopmentManagerAddPlayer(pid, xp_multiplier, bonus_xp, no_decline)` persisted to `<LE data>/extensions/careers/<SAVE_UID>/players_development.json`. |
| **Career events, 275 of them** | `AddEventHandler("post__CareerModeEvent", fn)` → `fn(events_manager, event_id, event)`, dispatched by numeric `ENUM_CM_EVENT_MSG_*` id. |
| **Per-save identity** | `GetSaveUID()` — a stable 31-char id per career save. All companion state should be keyed on it. |
| **In-game clock** | `GetCurrentDate()` → `{day, month, year}`; `GetSeasonEndDate()`. |
| **Season statistics** | `GetPlayerStats(pid)` / `GetPlayersStats()` → apps, goals, assists, clean sheets, cards, per competition. |
| **OVR maths, offline** | `src/ovr_formula.py` — per-position weights in pure Python. We can recompute OVR without touching the game. |

### 1.2 Hard constraints every feature must respect

These come from the shipped DLL, not from documentation, and they invalidate a lot of obvious designs.

1. **`ReloadPlayersManager` does not exist in FC 26.** It is in `lua/DOC.MD` (which is the stale v1 doc) and absent from `FCLiveEditor.DLL`. Never spec it.
2. **There are exactly four event hook names**: `pre__/post__CareerModeEvent`, `pre__/post__LEInitDoneEvent`. `AddEventHandler` does not validate the string, so an invented name registers silently and never fires. All event selectivity is by numeric `event_id`.
3. **Form, morale, sharpness and fitness are setters with no getters.** `SetPlayerForm/Morale/Sharpness/Fitness` exist; there is no read path short of pointer-walking `PlayerFormManager` (82) / `PlayerMoraleManager` (85). Any UI that promises to *show* current morale is promising a struct walk.
4. **Development plans outrank the `players` table** for all attributes, workrates, skillmoves and weakfoot, for user-team players in Manager Career. Writing the DB row alone silently reverts.
5. **`GetTransferBudget`/`SetTransferBudget` are deprecated shims that only print a warning.** Use `GetUserTransferBudget` / `SetUserTransferBudget` / `GetCPUTransferBudget` / `SetCPUTransferBudget`.
6. **Release clause is write-only, at transfer time** — the 5th positional arg of `cTransferPlayer(pid, from, to, sum, release_clause, wage, months)`; `-1` = none. No getter. (LE's own Bulk Edit can set it directly; delegate rather than reimplement.)
7. **`hashighqualityhead` is not an "is real face" flag.** It is a mesh-pipeline selector and is `0` for 19,237 of 22,348 base rows. The real test is `headassetid > 0 AND headclasscode == 0`. The current import path hardcodes `hashighqualityhead: 1` in its fallback and is wrong.
8. **Full-table record walks and `CreatePlayer` are the known freeze/crash vectors.** Keep the existing rules: row-object API (`GetDBTableRows`/`EditDBTableField`) for small tables, cursor API with `MAX_SCAN` for `players`, real-face writes only onto an existing playerid *after* stats, `CreatePlayer` opt-in only, never above playerid 500000.
9. **`players.modifier` must be zeroed** whenever `overallrating` is written, or OVR is skewed.
10. **`enums.lua` and `consts.lua` disagree by one from index 8 onward.** Pick `enums.lua` as canonical and pin the ids we use with a self-test.

---

## 2. Feature specifications

Seventeen features. Each one: the player's problem, what it does, how it works, where it lives in the UI, what can go wrong, and effort.

---

### F1 — Time Machine: full-identity cross-year import

**Player problem.** I asked for Zidane and I got a stranger wearing Zidane's stats.

**What it does.** One action turns a chosen target into a chosen player from any FIFA year — name, face, nationality, age, height, weight, foot, appearance, PlayStyles, roles, positions, attributes — and tells you honestly, before you commit, which parts it could and could not reproduce.

**How it works.**
- **Source chain:** `catalog.sqlite` card → EA `playerid` → `base_players.csv` row (149 cols) → `valid_headmodels` membership. Every field in the resulting bundle is tagged with its source so the UI can show provenance rather than guessing silently.
- **Name:** zero the four name-dictionary ids on the `players` row, set `usercaneditname=1`, then insert or edit the `editedplayernames` row. Re-assert after `TransferPlayer`, which can restore the dictionary name.
- **Face ladder**, in order, with the chosen rung shown to the player:
  1. Source playerid is in `valid_headmodels` **and** its base row has `headclasscode == 0` → real scanned head; copy `headassetid`, `headtypecode`, `headvariation`, and that row's own `hashighqualityhead`.
  2. No scanned face → **lookalike**: rank the 5,737-face pool by nationality, `skintonecode`, `hairtypecode`/`haircolorcode`, height band and age band.
  3. Explicit generic: `headassetid=0, headclasscode=1, hashighqualityhead=0`.
- **Age:** `birthdate` written as a Gregorian day count derived from a target age against `GetCurrentDate()`. "1998 Zidane at 26" and "2006 Zidane at 34" are the same dropdown.
- **Appearance:** the full 44-field appearance block plus tattoos, body and kit. *None of this is copied today* — the current import covers name, head and birthdate only, with head defaulted off.
- **Persistence:** every attribute / skillmove / weakfoot write goes through **F2**.
- **Gender guard:** `gender` is 0=male / 1=female in EA encoding and FUT.GG inverts it. Block cross-gender face writes outright.

**UI.** Cards → pick target → **Import as person**. A single Identity panel showing a provenance list — *Name: literal ✓ · Face: real scanned ✓ · Nationality: base row ✓ · Age: set to 26 · Appearance: 41 of 44 fields* — with a per-group toggle and a preview card. One Import button.

**Risk / safety.** Overwriting a real squad member destroys him. Default target is a free agent or the dummy pool; any other target needs explicit confirmation and an automatic restore point (F4). Real face + `CreatePlayer` is a known freeze vector — keep it on the existing-playerid path only.

**Effort: L.**

---

### F2 — Make It Stick: the persistence engine

**Player problem.** I edit a player, play three weeks, and the numbers are back.

**What it does.** Every edit is recorded as an *intent*, mirrored into the development plan so the career engine agrees with it, and re-asserted automatically whenever the game drifts.

**How it works.**
- **Mirror on write.** After each field write: if `IsInCM()` and `PlayerHasDevelopementPlan(pid)`, call `PlayerSetValueInDevelopementPlan(pid, field, value)`. This already exists — but only inside `player_apply.py`, behind an inline category gate. The add-to-team path, undo, and every pack path skip it entirely, which means *the edits people care about most — a new signing's stats — are exactly the ones that revert*. Fixing coverage is most of this feature.
- **Single source of truth.** `player_schema.EDIT_CATEGORIES` already carries a `dev_plan` boolean per category and nothing reads it; `player_apply.py` re-derives a different set inline (`skills` is `dev_plan: False` in the schema yet included in the code). Make the schema flag authoritative and delete the inline set.
- **Decline control.** `PlayerDevelopmentManagerAddPlayer(pid, xp_multiplier, bonus_xp, no_decline)` + `PlayerDevelopmentManagerSave()`. LE persists this to `extensions/careers/<SAVE_UID>/players_development.json`, which the companion can read and merge between sessions.
- **Intent ledger.** `state/<SAVE_UID>/intents.json`, keyed by `GetSaveUID()`, one row per `(playerid, field, value, ts, source)`.
- **Drift re-assert.** A resident event script on `post__CareerModeEvent`, firing on the same id set `pap_all_playstyles.lua` already proved works for keeping things applied — `POST_LOAD_PREPARE` (29), `DAY_PASSED` (15), `USER_MATCH_COMPLETED` (7), `ENTERED_HUB_FIRST_TIME` (26) — plus `PLAYER_GROWTH` (53). It reads live values for pinned players, diffs against the ledger, and rewrites only what drifted.
- Always zero `players.modifier` when `overallrating` is in the bundle.

**UI.** A padlock — **Keep this** — on every edit surface. A Pinned list. A **Drift** column in the squad grid: intended vs live, one click to re-assert. Status line: *"12 players pinned · 0 drifted · last checked 3 in-game days ago."*

**Risk / safety.** Dev plans exist only for user-team players in Manager Career — pinning a CPU player must report "no plan on this player", not silently no-op. LE's own example calls `ReloadPlayersManager()` first; that function does not exist in FC 26, so re-assert must be event-timed. Hard cap on pinned players per event, and a kill-switch file the resident script checks so it can be stopped without a restart.

**Effort: M.** Highest value per line of code in this document.

---

### F3 — Squad Planner: grid, staged changes, diff, one batch

**Player problem.** I can only see and change one player at a time, so I can't actually plan anything.

**What it does.** Your whole club as a spreadsheet you can edit. Sort and filter, stage changes across many players, review a field-level diff, apply once.

**How it works.**
- **Read.** `export_user_squad.lua` currently returns six fields per player. Extend it to a planning row: age from `birthdate`, contract year and `playerrole` from `career_playercontract`, `potential`, PlayStyle bitfields, roles 1–5, `isretiring`, and `PlayerHasDevelopementPlan`. Keep the existing scan cap.
  *Known limit:* form / morale / sharpness / fitness have **no getters**. Either show them as "set-only" or accept a pointer walk of `PlayerFormManager` (82) / `PlayerMoraleManager` (85). Recommendation: ship without them rather than ship a struct walk that breaks next patch.
- **Stage.** Changes live in a Python-side plan object and are never queued until Apply. `ovr_formula.py` recomputes OVR and best-position live, so the grid shows the resulting rating before anything is written.
- **Apply.** One Lua body that walks `players` **once** and applies every staged field for every staged player in a single record pass. Today, N players means N jobs and N full-table scans. Per-player `pcall` so one bad field cannot abort the batch, with a per-player result written back to `queue/`.
- **Diff.** Rendered from the plan against the last squad read, grouped by player, old → new, with a warning band for anything the Realism Dial (F9) flags.

**UI.** New **Planner** tab. Left: the grid. Right: a pending-changes basket with counts. Bottom: *"Review 34 changes across 9 players"* → diff modal → Apply, with progress and a retry list for failures.

**Risk / safety.** A mandatory, non-optional restore point (F4) before every batch. Gate on career state so nothing writes while a match is loading. One big body is one big failure surface — per-player isolation is not optional.

**Effort: L.**

---

### F4 — Restore Points: whole-squad snapshot and rollback

**Player problem.** There is no "put my save back".

**What it does.** Named restore points that capture the full state of every player in your club plus career-level state, and put it back in one action.

**How it works.**
- **Capture** in one `players` walk: every `ALL_EDIT_FIELDS` value for every squad playerid, plus their `editedplayernames` rows, plus their `career_playercontract` rows, plus `GetUserTransferBudget()`, plus the current `players_development.json`, plus `GetCurrentDate()`. Written to `state/<SAVE_UID>/restore/<id>.json`.
- **Restore** emits the reverse batch through F3's single-pass writer, including name rows and a development-plan re-assert.
- This closes a real gap: today's snapshots store *the values being applied*, not a pre-read of the live DB — so "undo" is really "re-apply the last card" — and they never touch names, faces, transfers or growth.
- Auto-capture before any batch, pack or import. Manual capture any time. Retain N with a pin.

**UI.** Restore points list — name, in-game date, player count, what triggered it. **Preview diff vs live**, then Restore. The Squad tab carries a persistent line: *"Last restore point: 14 Aug 2026 — before Legacy XI."*

**Risk / safety.** Restore is itself a mass write and runs through the same guardrails. It cannot undo things outside its capture set — a completed transfer that already generated news, a retired player — and the UI must say so in plain words rather than implying a time machine. Throttle to one capture per batch, not per field.

**Effort: M.** Must ship before or with F3 and F1.

---

### F5 — Two-Minute First Run

**Player problem.** Getting from "downloaded" to "first successful edit" is install worker → copy bridge → alt-tab → paste → execute → wait → export squad, and every step fails silently.

**What it does.** One screen that performs all of it, watches for each signal, and hands you your actual squad with a suggested first action.

**How it works.** A state machine over signals that already exist: LE install found via `paths.le_root()` and the registry data dir; worker installed; bridge freshness via `protocol.bridge_alive(max_age_sec=90)`; `IsInCM()` from a probe job; `current_squad.json` present and fresh. Each state has exactly one action and one visible failure reason. The bridge text goes to the clipboard at the right moment; on LIVE it auto-runs the squad export and advances.

**UI.** Full-screen stepper. Five steps, current one expanded, rest collapsed. Ends on *"Your squad — 45 players"* with three suggested first actions.

**Risk / safety.** None beyond today. Explicitly must **not** auto-arm inject (already known to break LE launch) and must not touch FakeEAAC — LE owns that boundary.

**Effort: S.** Cheapest feature here and it gates every other one.

---

### F6 — Legacy XI: build a whole themed squad in one operation

**Player problem.** Building a legends side means eleven manual imports and hoping nothing runs out.

**What it does.** Choose or build a squad of any players from any years, map them to formation slots, get the whole thing in one operation behind one rollback point.

**How it works.**
- A **recipe** is a list of `(source card ref, formation slot, target age, realism mode)`.
- **Resolve** first, execute second: allocate a target playerid per entry from free agents (`GetPlayerIDSForTeam(111592)`), then the dummy pool, then — opt-in only — existing squad members. If it cannot fill every slot it refuses to start, rather than half-building a squad.
- **Execute** with F1's identity bundle through F3's single-pass writer, then `TransferPlayer` per entry into the user team, then one settle pass (form / morale / sharpness).
- Ship a handful of curated recipes and "save my current squad as a recipe".

> **Prerequisite, not optional:** `current_squad.json.free_agents` is empty on this save, which is exactly why add-to-team already reports "no free-agent pool". The free-agent walk in `export_user_squad.lua` has to be fixed before this feature can work at all. F12 catches this class of problem.

**UI.** Pitch view, 11 slots, drag a card from search into a slot, per-slot age dial. A readiness bar — *"11/11 sources resolved · 9/11 real faces · 11/11 targets allocated"* — and one Build button.

**Risk / safety.** The most destructive operation in the product. Mandatory restore point, mandatory dry-run preview, hard refusal to overwrite squad players without per-player confirmation.

**Effort: L.**

---

### F7 — Career Autopilot: rules bound to career events

**Player problem.** The upkeep I want has to be remembered and re-run by hand, every match.

**What it does.** A small rules engine — *when X happens in my career, do Y to Z* — running inside the game, no alt-tabbing.

**How it works.**
- LE dispatches `post__CareerModeEvent` with typed numeric ids. The useful ones already exist: `ABOUT_TO_ENTER_PREMATCH` (37), `DAY_PASSED` (15), `WEEK_PASSED` (16), `END_OF_SEASON_REACHED` (19), `SEASON_RESET` (23), `POST_LOAD_PREPARE` (29), `INJURY` (165), `LONGTERM_INJURY` (122), `BACK_FROM_INJURY` (66), `TRANSFER_MOVE_COMPLETE` (86), `PLAYER_ADDED_TO_TEAM` (110), `TRANSFER_WINDOW_CLOSED` (102), `OBJECTIVES_GENERATED` (147), `PLAYER_GROWTH` (53), `PLAYERS_RETIRED` (60).
- Rules are declarative — `{event, scope, condition, action, limit}` — compiled by the companion into one resident autorun script. The bridge already arms `post__CareerModeEvent`, so this extends a proven path rather than inventing plumbing.
- Actions map to existing primitives: `SetPlayerFitness/Form/Morale/Sharpness`, contract extension, `SetUserTransferBudget`, re-assert of F2 intents. **Injury healing has no manager struct** — it is reachable only via events plus LE's own Bulk Edit "Heal Player", so auto-heal fires on `INJURY`/`LONGTERM_INJURY` and delegates.
- Every fire is logged with `GetCurrentDate()`.

Stock rules worth shipping with: fitness ≥ 90 before every match · heal short injuries on day change · settle morale after a signing · extend any contract under 12 months at season end · re-assert pinned edits after load.

**UI.** **Rules** tab. One line per rule with a toggle, a scope chip, and *"fired 14× — last 3 Sep"*. A prominent global Autopilot switch, and a **dry run** that replays the last 30 in-game days and reports what would have fired.

**Risk / safety.** The most dangerous surface in the product. Bind to safe ids only — never `ABOUT_TO_ENTER_A_MATCH` (42) or `TEAMS_READY_FOR_USER_MATCH` (41). Throttle to the bridge's existing `MIN_INTERVAL`. Hard per-event work cap. A kill switch that survives a crash, i.e. a file the script checks on every fire. Pin the event ids with a self-test, because `enums.lua` and `consts.lua` disagree from index 8.

**Effort: M.**

---

### F8 — Transfer Fixer: make the deal you actually want possible

**Player problem.** The player I want is unavailable, unaffordable, or the CPU just says no — and the existing career ops only move players I already own.

**What it does.** Pick any player in the world, see in plain language what is blocking the deal, then apply the smallest set of edits that unblocks it — so you still negotiate and sign him yourself.

**How it works.**
- **Diagnose** by reading: `GetTeamIdFromPlayerId`, `career_playercontract` (duration, `playerrole`), `cIsPlayerTransferListed` / `cIsPlayerLoanListed`, `IsPlayerPresigned`, `IsPlayerLoanedOut`, `GetUserTransferBudget`, `GetCPUTransferBudget`.
- **Unblock ladder**, shown as a tick-list in increasing order of how much it bends the fiction, each row stating what it costs you narratively:
  1. Set a release clause inside your budget (5th arg of `cTransferPlayer`, or delegate to LE's Bulk Edit release-clause action).
  2. `cAddPlayerToTransferList`.
  3. Shorten contract to one year.
  4. Lower squad role — `SetSquadRole(pid, role)` **and** `career_playercontract.playerrole` (1 Crucial … 5 Prospect); both are required.
  5. Raise your own budget via `SetUserTransferBudget`.
  6. Force the move with `cTransferPlayer`.
- LE already ships "always allow transfer & loan approach" and "disable negotiation status check" — detect and offer those rather than reimplementing them.

**UI.** Search any player → a **"Why can't I sign him?"** card with red / amber / green rows → tick fixes → *"Apply 2 fixes"* → go negotiate in-game. A visually separate, deliberately less attractive **"Just give him to me"** at the bottom.

**Risk / safety.** Editing CPU squads is where saves break. Restore point first. Warn explicitly when the target is a key player at a title rival, because removing him rewrites the league.

**Effort: M.**

---

### F9 — Realism Dial

**Player problem.** The only two settings are "vanilla" and "99 everything", and the second one kills the save by season two.

**What it does.** One global slider that every write path consults.

- **Broadcast** — attributes clamped to the player's own historical range for the chosen age; potential ≥ OVR but ≤ a plausible ceiling; imports age-scaled (pace and stamina decay past 30, composure and positioning rise); OVR recomputed via `ovr_formula.py` rather than pasted; 99s reserved for players who actually had them.
- **Manager** — warnings, no clamping.
- **Sandbox** — nothing is blocked.

**How it works.** A single validation function that all builders call, taking the field bundle and the target's age. **The age curves come from the cross-year database itself** — the same `playerid` appears across nine games, so real attribute-versus-age curves are *measurable* rather than invented. That is the argument for this feature: no other tool has the longitudinal data to do it honestly.

**UI.** One control in the header, always visible, always showing the current mode. Diffs show clamped values struck through beside the original.

**Risk / safety.** Only that it irritates people if it feels like a nanny. It must be one click from anywhere, and it must never modify a value without showing what it changed.

**Effort: M** (S if the measured age curves are deferred to a second pass). **The hooks must be laid during Tier 1 even though the dial ships later** — retrofitting a validation layer through six builders is far more expensive than stubbing it now.

---

### F10 — Face Coverage: stop the potato-head squad

**Player problem.** After a few imports, regens and seasons, half my squad has generic faces and every cutscene looks wrong.

**What it does.** Scans your squad, reports exactly who has a real face and who doesn't, and offers the best real-face substitute for each generic player as one batch.

**How it works.**
- Per squad playerid, read `headassetid`, `headclasscode`, `hashighqualityhead` and test membership in `valid_headmodels` (5,737 ids).
- **Correctness this must get right**, and the current import path gets wrong: real face is `headassetid > 0 AND headclasscode == 0`, *not* `hashighqualityhead == 1` — that flag is 0 for 19,237 of 22,348 base rows.
- For generics, rank candidates from the pool by nationality, `skintonecode`, `hairtypecode`/`haircolorcode`, height band and age band from `base_players.csv`.
- Apply through F3's writer. Portraits come from LE's existing `PlayerCapture*` miniface API.

**UI.** Squad → **Faces**. A miniface grid, real ones solid, generics dimmed. Per-row "suggest" with three candidates. A **Fix all 14** batch.

**Risk / safety.** Face writes are the freeze vector. Keep the rule: real-face fields only on an existing playerid, written after stats, never inside `CreatePlayer`, never across a gender mismatch.

**Effort: M.**

---

### F11 — Scouting Room: the cross-year database as a scouting tool

**Player problem.** The card search is a lookup box. It can't answer the questions a manager actually asks.

**What it does.** Query nine games' worth of players as a scouting database, and compare versions of the same player across years side by side.

**How it works.** `catalog.sqlite` already indexes name / year / ovr. Add computed columns and indexes for age (from `base_players.birthdate`), nationality, position, potential, has-real-face, and a `playerid` join so "every version of this player" is one query. Comparison renders up to four versions with a delta table and a radar over the 34 attribute fields. **Import this version** hands straight to F1 with the age dial pre-set.

Saved presets worth shipping: *U21 with 88+ potential and a real face* · *every GK above 85 in any year* · *wingers faster than anyone currently in my league* · *this player at his peak*.

**UI.** **Scouting** tab — filter rail, results grid, compare tray (drag up to four), one Import button. Reuses the existing favourites and compare code rather than replacing it.

**Risk / safety.** None; read-only. Only cost is a one-off index build, which the catalogue already does.

**Effort: M.**

---

### F12 — Save Doctor

**Player problem.** Careers rot quietly — no keeper at some club, a squad too small to field, an import with a broken name record — and you find out at the worst possible moment.

**What it does.** One scan listing every real problem in the save, each with a one-click fix and a plain-English explanation.

**How it works.** Promote the three existing raw dumps (`find_teams_no_gk`, `small_squad_find`, `list_transfer_bans`) into checks, and add the failures that only appear once you start editing:

- playerids in `teamplayerlinks` with no `players` row;
- `usercaneditname=1` with no `editedplayernames` row (these render blank in-game);
- the same source imported onto multiple target ids;
- squad members whose `contractvaliduntil` is already in the past;
- `overallrating` inconsistent with attributes per `ovr_formula.py`, or a non-zero `modifier`;
- **free-agent pool exhausted or never exported** — the bug currently blocking add-to-team on this very save;
- dummy pool contaminated with real players;
- development plans pinned to players no longer on your team.

**UI.** Home → **Check my save**. Results grouped Critical / Warning / Cosmetic, each with **Fix** and **Explain**.

**Risk / safety.** Auto-fix is per item and reversible. There is deliberately no "fix everything" button.

**Effort: M.**

---

### F13 — Development Plans: control *how* a player grows

**Player problem.** I either accept EA's growth or I cheat the number. There is nothing in between.

**What it does.** Give a player a multi-season plan — target OVR, target position and role, which attributes grow — and let the career deliver it over time instead of instantly.

**How it works.** `PlayerDevelopmentManagerAddPlayer(pid, xp_multiplier, bonus_xp, no_decline)` sets the growth rate; `PlayerSetValueInDevelopementPlan(pid, field, value)` sets per-field XP targets. The plan is stored per `GetSaveUID()`; on `END_OF_SEASON_REACHED` (19), `SEASON_RESET` (23) and `PLAYER_GROWTH` (53) the companion computes the next step and writes only that step's delta. Because it edits the *plan* rather than the players table, the growth reads as natural in-game — that is the entire difference between this and a "set OVR 99" script.

**UI.** Player → **Development**. A curve editor (current OVR → target over N seasons), a position-retrain target, and a per-season checkpoint list showing planned vs actual.

**Risk / safety.** Over-boosted XP produces absurd mid-season jumps; clamp per-season delta by the Realism Dial. Depends on F2's machinery being in place.

**Effort: M.**

---

### F14 — Newgen Control

**Player problem.** Youth intakes are a lottery of forgettable names, wrong nationalities and generic faces.

**What it does.** Shapes what your academy produces — nationality mix, position quotas, potential band, face quality, real names — plus **successor mapping**: when a legend retires, his newgen inherits his face and name.

**How it works.** `YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY` (109), `YOUTH_SCOUT_REPORT_IS_AVAILABLE` (84) and `YOUTH_PLAYER_PROMOTION` (112) fire per intake; `PLAYER_INSERTED_INTO_PLAYERS_TABLE` (58) hands over the new ids directly. On the event, the resident script rewrites those rows to the policy *before you ever see them* — nationality, `potential`, positions, a face from the 5,737 pool matched to the chosen nationality, and a literal name via `editedplayernames`. Successors trigger on `PLAYERS_RETIRED` (60) / `USER_TEAM_PLAYER_RETIRES` (73). There is no dedicated youth API; regens are identified by convention (`playerid >= 460000`), same as `delete_generated_players.lua`.

LE already exposes youth academy toggles. This is not those — this is composition control over the *output*.

**UI.** **Academy** tab — a policy card (nationality weights, position quotas, potential band, "real faces where possible", "real names") plus an intake log showing what the policy actually produced.

**Risk / safety.** Rewriting a row the instant the engine inserted it is timing-sensitive: event-driven only, never polled, and skip any id that already has a contract.

**Effort: M–L.**

---

### F15 — Challenge Presets

**Player problem.** A sandbox with no constraints gets boring by season two.

**What it does.** One-click career setups that impose a shape on the save: a starting condition, rules that enforce it, and a report that scores it.

**How it works.** A preset is data — restore point + a batch of edits + a set of F7 rules + a report definition — so users can author and share them. Examples worth shipping:

- **Rebuild** — squad rewritten to U21, budget zeroed, contracts set to 4 years.
- **Wage Cap** — a rule on `PLAYER_CONTRACT_ACCEPTED` (63) that reverts any wage above the cap.
- **Retirement Tour** — your five best players get `isretiring=1` and one year left.
- **Galáctico Mandate** — one 88+ signing required per window, checked on `TRANSFER_WINDOW_CLOSED` (102).
- **Youth Only** — any signing over 23 is auto-listed.

**UI.** A Challenges gallery, one card per preset: what it changes, what it enforces, how it's scored. **Start challenge** snapshots first.

**Risk / safety.** Presets bundle mass edits, so every F4 guardrail applies unchanged.

**Effort: M.**

---

### F16 — Season Report

**Player problem.** The exports are raw dumps nobody reads.

**What it does.** A real end-of-season review: squad sheet, appearances / goals / assists, transfer ledger with net spend, a development table showing OVR change per player per season, and the edits you made.

**How it works.** `GetPlayersStats()` / `GetPlayerStats(pid)` give per-competition apps, goals, assists, clean sheets and cards; `GetCompetitionNameByObjID` names the competition; the transfer history LE already surfaces gives the ledger; and the companion's own restore points give the OVR-over-time series for free. Rendered to one self-contained HTML file plus CSV. Auto-offered on `END_OF_SEASON_REACHED` (19).

**UI.** **Reports** tab — season picker, Generate, opens in the browser.

**Risk / safety.** Read-only. LE's own docs flag `motm`, `two_yellow`, `goals_conceded` and `saves` as possibly incorrect — mark those columns unverified rather than printing them as fact.

**Effort: S–M.**

---

### F17 — Career Journal and shareable recipes

**Player problem.** Three seasons in I have no idea what I changed, and no way to hand a friend the squad I built.

**What it does.** An automatic, in-game-dated log of every change the companion made — each entry revertable — and an export that turns any selection of it into a portable recipe.

**How it works.** `job_history.json` already records jobs; add `GetCurrentDate()` and `GetSaveUID()` per entry and link each to its restore point. A **recipe** is the same data minus anything save-specific: source refs as `(year, playerid)`, targets as *slots* rather than ids, plus the settings used — so it re-resolves against someone else's squad on import.

**UI.** **Journal** tab, grouped by in-game month. Select entries → Export recipe. Import shows a resolution preview before anything runs.

**Risk / safety.** Recipes are user-authored files that get executed. Parse them as **data** into the same validated builders — never as Lua — and never let a recipe raise the Realism Dial or disable a guardrail.

**Effort: S** (journal) **/ M** (recipes).

---

## 3. Ranking

Ranked by value to a career-mode player, then by whether the thing below it depends on it.

### Tier 1 — build these, in this order

| # | Feature | Effort | Why it's first |
|---|---|---|---|
| 1 | **F5 Two-Minute First Run** | S | Nothing else matters if people never reach LIVE. Cheapest item here and it gates every other feature. |
| 2 | **F2 Make It Stick** | M | Makes *everything that already exists* actually work. Every edit the product has ever applied is currently at risk of silent reversion. Highest value per line in this document. |
| 3 | **F4 Restore Points** | M | The safety net. F3 and F1 are irresponsible to ship without it, and the current "undo" is not one. |
| 4 | **F3 Squad Planner** | L | Turns a single-player editor into a squad tool. This is the feature people would describe to a friend. |
| 5 | **F1 Time Machine Import** | L | The headline. The cross-year database plus the face registry is the one thing no other tool can do. |

Lay the **F9** validation hooks during this tier even though the dial ships in Tier 2 — retrofitting a validation layer through six builders costs several times more than stubbing it now.

### Tier 2 — the differentiators

| # | Feature | Effort | Why |
|---|---|---|---|
| 6 | **F9 Realism Dial** | M | Turns "cheating" into "directing". Only possible because we have nine years of the same playerids. |
| 7 | **F10 Face Coverage** | M | Highest visible-quality-per-effort item in the list, and it fixes a live correctness bug. |
| 8 | **F11 Scouting Room** | M | Read-only, zero risk, directly feeds F1, and makes 339,609 rows finally useful. |
| 9 | **F8 Transfer Fixer** | M | The most common thing a career player wants and cannot currently do. |
| 10 | **F7 Career Autopilot** | M | Removes the alt-tab loop entirely. Ranked below the others only because it is the riskiest surface. |
| 11 | **F6 Legacy XI** | L | The most impressive demo; blocked on the free-agent export fix and on F1 + F4 being solid. |
| 12 | **F12 Save Doctor** | M | Prevents the class of quiet breakage that everything above can cause. |

### Tier 3 — depth, once the core is trustworthy

| # | Feature | Effort |
|---|---|---|
| 13 | **F13 Development Plans** | M |
| 14 | **F16 Season Report** | S–M |
| 15 | **F15 Challenge Presets** | M |
| 16 | **F14 Newgen Control** | M–L |
| 17 | **F17 Journal / Recipes** | S–M |

**Dependency edges:** F3 → F4 · F1 → F2, F4 · F6 → F1, F4, and the free-agent export fix · F13 → F2 · F15 → F7, F4 · F17 → F4.

---

## 4. What I recommend *not* building

Being opinionated is the point of a spec. These are all things somebody will ask for.

**Another player / team / manager / league editor.** LE already does this, does it better, and does it against every patch. Every hour spent here is an hour not spent on the layer above it. Be the thing that *plans and persists* edits, not the thing that types them.

**Our own memory-offset writer.** Racing LE for the same structs means every game patch breaks us and there is nobody to fix the offsets. Stay on the Lua API. The one exception already in the codebase — `SetSquadRole` walking `PLAYERROLE_STRUCT` — is exactly the kind of thing that will silently break, and it should be wrapped in a version check rather than copied.

**Anything that touches inject or anticheat.** LE's `Launcher.exe` owns FakeEAAC backup / install / restore and the primary DLL inject. Auto-arm already broke LE launch once. This boundary is non-negotiable and should be written into the code, not just the docs.

**FUT pricing, chemistry, or market integration.** Career Mode has no chemistry and no market. FutBin prices are noise in this context. Cut the dependency rather than maintain a scraper for a number nobody uses.

**AI player generation as a headline feature.** We have 339,609 real players with real attribute distributions and real appearance data. A generated stat line is strictly worse than a real one and it hallucinates. Keep the AI for what it's genuinely good at — search by description ("a quick left-footed CB under 23"), name and backstory flavour, explaining a diff — and never as the source of numbers.

**A live scraper to replace the card catalogue.** The catalogue is static, complete, and fast. Live scraping adds network failures, rate limits and parser rot in exchange for nothing a career player will notice.

**"One-click 99 everything" as a promoted feature.** It is the fastest way to make a save meaningless, and a player who does it in week one never comes back. It stays available behind Sandbox mode; it does not get a button on the home screen.

**Match result forcing and scoreline editing.** LE already has match-fixing and match setup. Beyond that, we would be building a way to not play the game inside a tool for people who want to play the game more.

**Online, FUT, or anything touching EA servers.** Bans, terms of service, and no upside for a Career Mode player.

**Cloud sync, accounts, or a mobile companion.** The state is a career save on one PC. Keying everything on `GetSaveUID()` in a local folder gives us multi-save support with none of the infrastructure.

**Reading form / morale / sharpness by pointer-walking the manager structs.** There are no getters, only setters. A read path means mapping `PlayerFormManager` (82) and `PlayerMoraleManager` (85) by hand, and it will break on the next patch. Show those fields as set-only and be honest about it — a wrong number is worse than an absent one.

---

## 5. Verify before building

Claims in this document taken from reading code and data, not from a live session. Each should be a ten-minute test before the feature that depends on it is scheduled.

1. `PlayerSetValueInDevelopementPlan` accepts a value that makes the *display* value stick, and does not need a scale conversion between "XP points" and attribute points. **(F2 — the single riskiest assumption here.)**
2. A single Lua body applying fields to ~45 players in one `players` walk completes inside the bridge timeout and does not freeze FC 26. **(F3, F4, F6.)**
3. Writing the full 44-field appearance block onto an existing free-agent playerid is stable, including tattoos and body. **(F1.)**
4. A resident event handler doing per-event work across ~15 pinned players does not stall the career UI. **(F2, F7.)**
5. `GetSaveUID()` is stable across save/load and distinct per career slot. **(All state keying.)**
6. The free-agent walk in `export_user_squad.lua` returns a non-empty pool on a normal save — and if not, why. **(F6, F12, and add-to-team today.)**
7. Event ids `15 / 19 / 29 / 37 / 53 / 102 / 109` fire when expected, given the `enums.lua` ↔ `consts.lua` off-by-one from index 8. **(F7, F13, F14.)**
