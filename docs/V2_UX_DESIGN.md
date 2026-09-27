# LE Companion v2 — UX / UI / Playflow Design Spec

| Field | Value |
|-------|-------|
| **Document** | v2 UX / IA / interaction build spec |
| **Status** | Proposed — build spec, not a mood board |
| **Supersedes** | `feedback/REDESIGN_v1.10.md`, `feedback/DESIGN_VISUAL_POLISH_v1.11.md` (both stay valid as *tactics*; this replaces the *structure*) |
| **Baseline audited** | v1.11 shell — 8 tabs (`src/gui.py:275-282`), 51 profiles (`profiles.json`), 19 packs (`src/product.py:32-174`), 6 career ops (`src/career_ops.py:309-346`) |
| **Reference product** | xAranaktu FC 25 Cheat Table (same author as Live Editor) — treated as the interaction gold standard for player editing |
| **Stack assumption** | Unchanged: Python 3 + CustomTkinter + PyInstaller onedir + Lua queue bridge. Every pattern below is specified to be buildable in CTk. Where CTk cannot do it, the fallback is named inline. |
| **Non-negotiable** | The APPLY pipeline (Write → Queue → Bridge → Game) survives verbatim. No injection. No auto-arm regression. |

---

## 0. What this document decides

1. The app stops being organised by **Live Editor mechanism** (Cards / Editor / Add team / Boost / Catalog) and becomes organised by **career-manager job** (Club / Player / Transfers / Automations / Library).
2. There is exactly **one** meaning of "Apply", exactly **one** primary button per screen, and **one** staging model (Change Set) that every mutation flows through.
3. Nothing that exists today is deleted. §2.4 is an exhaustive migration table. If a feature is not in that table, it has not been designed and must not ship.
4. The app must look like a football product (crests, faces, OVR colour, position colour, card art) while staying a dark, dense, fast desktop tool.
5. The app must never claim a change landed in the save file unless the worker returned proof.

---

## 1. Design principles

Seven principles. Each names the **concrete v1 failure** it exists to prevent.

### P1 — Organise by job, not by mechanism

**Rule:** Top-level navigation names things the user wants to *achieve* ("Player", "Transfers"), never things the tool *does* ("Cards", "Catalog", "Add team").

**Prevents:** Today a user who wants to "make Cole Palmer better" must guess between **Cards** (put a FUT card on him), **Editor** (type his attributes), and **Add team** (put a card-derived player into a team). Three tabs, one job, no signposting. Users reported doing the work twice because they did not realise Cards and Editor write the same `PlayerEditSurface`.

**Test:** For each of the 7 flows in §4, the user's first click is unambiguous from the tab label alone.

---

### P2 — One verb, one meaning, one button

**Rule:** "Apply" means exactly one thing: *commit the currently staged Change Set to the queue*. It is triggered from exactly one control — the APPLY dock. Every other control that today "applies" becomes a **Stage** action ("Add to changes", "Use this card", "Queue transfer") that produces a reviewable diff.

**Prevents:** In v1, "Apply" means (a) run a profile/pack Lua script — Boost `Run` × 51 cards, (b) write a player card onto a target ID — Cards + Editor `Apply to game` + dock `Apply to game`, (c) create/insert a player into a team — Add team. On the Boost screen alone there are up to **nine** equally-loud buttons that all queue Lua. The user cannot predict what the green button will do, so they stop trusting green.

**Test:** Screenshot any v2 screen. Count solid-success-green buttons. The answer is 0 or 1, and if 1 it is the dock.

---

### P3 — Never display a value you did not read

**Rule:** Every editable field has a **provenance** state: `read` (came from the game or a card, value trustworthy), `edited` (user changed it, will be written), `unknown` (never read — renders as `—`, is dimmed, and is *structurally excluded* from the write set). Only `edited` fields are ever written.

**Prevents:** The worst bug in v1. "Load from target" populates only name / OVR / POT, but the form *looks* fully populated because every other entry shows a default or a stale value from the previous player. The user hits Apply believing they are writing one field and silently writes ~40 defaults into a career save. There is no undo prompt and the damage is invisible until they load the game.

**Test:** Load from target, then Apply with no edits. The diff preview must read "No changes staged." and the Apply button must be disabled.

---

### P4 — Show the change before you make it

**Rule:** Apply is always preceded by a plain-English diff. Not a field dump — a sentence per semantic change: `OVR 82 → 91`, `6 PlayStyles added (Finesse Shot, Rapid, …)`, `Name unchanged`, `Age not touched`. Destructive operations (Release, mass edit over >1 player, budget change) require typed or explicit confirmation naming the blast radius.

**Prevents:** v1 `Release` fires `ReleasePlayerFromTeam` on a career save with zero confirmation. v1 mass-edit profiles ("99 OVR + 99 Pot (All)", "Mass Edit Age (16)") run instantly against *every player in the database* from a card that looks identical to "Full Fitness".

**Test:** No queue write happens anywhere in the app without the user having seen a diff or an explicit blast-radius confirmation in the preceding interaction.

---

### P5 — Be honest about where a change actually is

**Rule:** Five states, never conflated: **Staged** (in the app only, nothing written) → **Queued** (`.lua` written to `queue/`, worker has not run it) → **Running** (worker picked it up) → **Applied** (worker returned a result token) → **Failed**. "Queued" is a *legitimate resting state* and the UI must say why: *"Queued. Runs the next time a Career Mode event fires — advance a day, open your squad, or enter a menu."*

**Prevents:** The auto-arm change (LE `lua/autorun`) removed the manual arming step, but jobs still only execute on a Career Mode event. If the UI collapses Queued into Applied, the user sees green, tabs into the game, sees nothing changed, and concludes the tool is broken. Conversely if it shows a spinner forever, they conclude it hung.

**Test:** Queue a job with FC26 running but the user sitting in the main menu. UI shows Queued + trigger hint + elapsed time. It never shows Applied. It never shows an error.

---

### P6 — Make invalid states unreachable, not merely rejected

**Rule:** Prefer constrained pickers over free text. Team = picker, never a raw ID box. Head model = ethnic-group combo chained to a valid-code combo. PlayStyles = 34 labelled checkboxes in 6 groups, never a hex bitfield. Enum captions carry units and meaning (`183 cm / 6 ft 1 in`, `Hunter (+3 PAC, +3 SHO)`, `4-2-3-1 WIDE`).

**Prevents:** v1 career ops are *unusable*, not merely awkward: `Transfer Player` requires a destination `Team ID` and the app contains **no way anywhere to discover another club's team ID**. Loan length is hardcoded to 12 months in the UI path. Trait masks are raw integer fields. This is the single largest functional gap in the product.

**Test:** No numeric ID entry box appears anywhere in v2 except as an *advanced override* behind a disclosure, pre-filled by the picker.

---

### P7 — Instant paint, deferred work, named next action

**Rule:** The window paints in one frame with a visible loading panel; heavy work (SQLite index build, catalog load, network, image fetch) is off the UI thread and cancellable. Every terminal state — empty, error, success — names the next action as a real control, never as prose.

**Prevents:** v1 builds the Editor's ~6,800 px form and blocks; the SQLite catalog index builds on first search with no progress; FUT.GG / Futbin calls are blocking network I/O (also the reference cheat table's known weakness); errors used to vanish entirely due to the deferred-closure bug; the Catalog tab was wholly dead. All four failures share a root cause: the UI thread doing work it should have handed off, and terminal states with no exit.

**Test:** Cold start to interactive < 400 ms. No operation blocks input for > 100 ms. Every empty/error panel contains a button.

---

## 2. Information architecture

### 2.1 The jobs

Derived from what a career-mode player actually sits down to do:

| # | Job (user's words) | Frequency | Today's tabs involved |
|---|--------------------|-----------|-----------------------|
| J1 | "Get this thing talking to my game" | Once | Home, header (4 buttons) |
| J2 | "Make my squad match-ready before I play" | Every session | Boost, Home |
| J3 | "Make this player better / different" | Very often | **Cards + Editor + Add team** |
| J4 | "Change who is in my squad" (sign / sell / loan / release / budget) | Often | Boost (buried panel), Add team |
| J5 | "Find a card / a legend / a player" | Often | Cards, Catalog |
| J6 | "Run a bulk operation on the whole save" | Occasional | Boost |
| J7 | "What did I just do, and can I take it back?" | On mistakes | Ops bar (Undo/Health/History/Queue) |
| J8 | "Configure the tool" | Rare | About, header, hidden config |

### 2.2 The v2 structure

**Five primary surfaces + one drawer + one menu.** 8 tabs → 5 tabs.

```
┌─ GLOBAL CHROME ────────────────────────────────────────────────────────────────┐
│  ◈ LE Companion    [ Club ] [ Player ] [ Transfers ] [ Automations ] [Library] │
│                                       ● IN CAREER · Chelsea    ⌘K    ☰   ⧉ 3   │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│                              ACTIVE SURFACE                                    │
│                                                                          ┌───┐ │
│                                                                          │ A │ │
│                                                                          │ c │ │
│                                                                          │ t │ │
│                                                                          │ i │ │
│                                                                          │ v │ │
│                                                                          │ t │ │
│                                                                          │ y │ │
│                                                                          └───┘ │
├────────────────────────────────────────────────────────────────────────────────┤
│  CHANGES (2)  Cole Palmer · OVR 86→91 · +4 PlayStyles      [Review]  [ Apply ] │
└────────────────────────────────────────────────────────────────────────────────┘
```

| Surface | Job | Answers |
|---------|-----|---------|
| **Club** | J1, J2, J7(partly) | "What is my squad, and is it ready?" |
| **Player** | J3 | "Change this one player." |
| **Transfers** | J4 | "Change who is in the squad." |
| **Automations** | J2, J6 | "Run a scripted operation on many players." |
| **Library** | J5 | "Find a card, a player, or more data." |
| **Activity drawer** (⧉, `Ctrl+J`) | J7 | Queue · History · Undo · Health · Snapshots |
| **Settings menu** (☰) | J8 | Paths · Worker · AI · Catalog sources · About |

### 2.3 Justifying every merge and split against the current 8 tabs

| v1 tab | v2 disposition | Justification |
|--------|----------------|---------------|
| **Home** | **Dissolved into Club.** First-run becomes a *state* of Club, not a tab. | Home is a permanent tab serving a one-time job. After day one it is a dead click that duplicates the header (Install worker, Copy bridge) and the Boost tab (goal cards deep-link to tabs the user can already see). A checklist that never completes is worse than no checklist; a tab that is only useful once should be a state, not navigation. |
| **Boost** | **Renamed Automations**, split: player-scoped career ops **move out** to Transfers; the 51 profiles + 19 packs stay. | "Boost" describes 5 of 51 profiles. The tab actually contains fitness boosts, mass DB edits, visual scripts, unlocks, exports, debug dumps, *and* a transfer/loan/release/budget panel. Career ops are per-player mutations of squad membership — a completely different job from "run a script over the whole save" — and burying them mid-page on a boost screen is why they are undiscoverable. |
| **Cards** | **Merged into Player** as the *Card* source lane. | Cards is not a destination, it is a *way of sourcing values* for a player edit. Its Target panel, edit surface, write-topic checkboxes, and Apply are literally the same `PlayerEditSurface` as Editor (`src/ui/player_edit.py`). Two tabs, one component, one job. |
| **Add team** | **Split.** "Insert this player into a team" → **Transfers**. "Build the player to insert" → **Player**. | Add team is two jobs welded together: *compose a player* (its Catalog / Editor / Manual source picker is a third copy of the Cards search + a fourth copy of the edit surface) and *place them in a club* (a roster operation). Splitting removes the duplicated composer and gives the roster half a home next to transfer/loan, where the same team picker serves both. |
| **Squad** | **Promoted to Club** — becomes the app's home screen and the entry point for every player-scoped flow. | The squad board is the only screen with the user's actual save data in it, and in v1 it is the 4th tab, a monospace `tk.Listbox` dump with a filter box. It should be the first thing you see and the origin of every "change a player" flow (row → Player; multi-select → bulk edit; row → Transfers). |
| **Editor** | **Merged into Player** as the manual/AI lanes. | Same component as Cards (see above). Editor's real contributions — write-topic gating, presets, Load from target, AI fill — become properties of the Player workspace, not a separate tab. |
| **Catalog** | **Merged into Library** as the *Sources* panel. | Catalog is the maintenance half of card search (download / sync / import / rebuild index). Separating "get the data" from "search the data" means the empty-search state on Cards cannot offer the fix, and the Catalog tab has no reason to exist once data is present. Merging lets the empty state say "No FC26 cards yet → [Download catalog]" inline. |
| **About** | **Settings menu.** | Version + three paths + a safety statement is not a top-level destination. It joins worker paths, AI credentials, catalog sources and preferences under ☰. |
| — | **New: Transfers** | Absorbs career ops + add-to-team + the **team picker that does not currently exist anywhere in the app**. This is a new surface because it is a real job (J4) that v1 makes impossible to complete. |
| — | **New: Activity drawer** | The v1 ops bar (Undo last · Health · History · Queue · Force drain) is five ghost buttons that each open a modal. As a drawer it becomes a single readable timeline: what is queued, what ran, what it changed, undo it. |

**Net:** 8 tabs → **5 tabs + 1 drawer + 1 menu**. Three tabs meaning "change a player" → **1**. One job with no home (change the roster) → **1 surface with a working team picker**.

### 2.4 Complete feature migration map

Exhaustive. Every feature reachable in v1 GUI, CLI, or web UI. **Nothing is dropped.**

#### 2.4.1 Global chrome

| v1 feature | v1 location | v2 home | Change |
|---|---|---|---|
| Install worker | Header button | Settings ▸ Worker; auto-run in Setup state | Automatic via LE `lua/autorun`; manual button retained as repair |
| Copy bridge (`F5`) | Header button | Settings ▸ Worker ▸ Repair | Demoted; only needed when autorun fails |
| How to arm | Header button | Setup state + Connection popover | Contextual, not permanent chrome |
| Copy apply Lua | Header button | Review Changes dialog ▸ `Copy Lua` | Moves next to the thing it copies |
| LIVE / WORKER OFF pill | Header | **Connection chip** (§3.1) | Gains 5 states, not 2 |
| Undo last | Ops bar | Activity drawer ▸ per-entry `Undo`; `Ctrl+Z` | Becomes a stack, not a single slot |
| Health | Ops bar | Activity drawer ▸ Health tab | Same `health_check.format_report()` |
| History | Ops bar | Activity drawer ▸ History tab | `job_history` rendered as timeline |
| Queue | Ops bar | Activity drawer ▸ Queue tab + ⧉ badge count | Live count always visible in chrome |
| Force drain | Ops bar | Activity drawer ▸ Queue ▸ `Force drain` | Kept; labelled "advanced" |
| APPLY dock (Write→Queue→Bridge→Game) | Bottom dock | **Changes bar** (§5) | **Survives.** Gains diff preview + honest state model |
| Footer status + path | Footer | Removed as chrome; status → Changes bar, path → Settings | Kills the triple-status-essay problem |
| `Ctrl+Enter` apply, `Ctrl+F` search | Hotkeys | Retained + expanded (§8.1) | |

#### 2.4.2 Home

| v1 feature | v2 home | Change |
|---|---|---|
| 4-step first-time checklist | **Club ▸ Setup state** (§3.2) | Only rendered when not connected; auto-advances |
| 4 goal cards (Boost/Cards/Editor/Catalog) | **Club ▸ Suggested actions** strip | Contextual (e.g. "Squad fitness is low → Match Ready"), not static |
| "How Apply works" explainer | Changes bar ▸ `?` popover + Setup state | Shown where Apply happens |
| Squad summary line | Club header (club crest, name, squad size, mode) | Promoted to a real club header |

#### 2.4.3 Boost — 51 profiles, 19 packs

| v1 feature | v2 home | Change |
|---|---|---|
| **All 51 `profiles.json` profiles** | **Automations ▸ Library of automations**, grouped by the existing 12 `category` values | None dropped. Grid → filterable list with blast-radius labels |
| ↳ `user_team` (5) | Automations ▸ **Squad condition** lane | Also surfaced as Club quick actions |
| ↳ `user_team_event` (5) | Automations ▸ **Recurring** lane | New: explicit "keeps running" badge — these are event scripts, not one-shots, and v1 never says so |
| ↳ `contracts` (2) | Automations ▸ **Contracts** lane | |
| ↳ `mass_edit` (11) | Automations ▸ **Mass edit** lane | Gated: blast-radius confirm (P4) |
| ↳ `visual` (10) | Automations ▸ **Appearance** lane | |
| ↳ `unlocks` (2) | Automations ▸ **Unlocks** lane | |
| ↳ `squad` (1, export) | Club ▸ `Refresh squad` **and** Automations | Same profile, two entry points |
| ↳ `export` (6) | Automations ▸ **Exports** lane; results land in Activity ▸ Files | New: exports produce a visible artefact, not a silent file |
| ↳ `career_cleanup` (4) | Automations ▸ **Save maintenance** lane | |
| ↳ `play_as_player` (2) | Automations ▸ **Play as Player** lane | Hidden unless a VPro is detected (progressive disclosure) |
| ↳ `debug` (1) | Automations ▸ **Diagnostics**, behind `Show advanced` | |
| ↳ `packs` (2 snippets) | Backing snippets for packs; not separately listed | |
| **All 19 `product.PACKS`** | **Automations ▸ Packs** rail (top of screen) + Club quick actions | None dropped |
| ↳ boost (3), career (3), mass (2), visual (2), squad (1), export (1), unlocks (1) | Packs rail, filtered by category chip | |
| ↳ workflow (5: match_ready, signing_settle, season_kickoff, pre_match_full, club_refresh) | **Club ▸ Suggested actions** — these are the "one click does the right thing" packs | Promoted; they are the product's best feature and were a button row on tab 2 |
| Boost queue mode (`boost_queue`) + queue UI | Automations ▸ `Run` always enqueues; Activity ▸ Queue shows depth | Mode toggle removed — one behaviour |
| **Career ops panel** (pid/teamid/fee/budget + 6 ops) | **Transfers** (§3.4) | Rebuilt: team picker, per-op forms, confirmations |
| Cross-tab shortcuts (`Add team →`, `Cards →`) | Deleted | Navigation is the tab strip |

#### 2.4.4 Cards

| v1 feature | v2 home | Change |
|---|---|---|
| Target find by squad name | **Club** row click, or Player ▸ target switcher | Target is a first-class app-level selection, not a per-tab form |
| Player ID box | Player identity header (read-only, copyable) + advanced override | |
| Card search (name / year / OVR min/max / pos) | **Library ▸ Search** and **Player ▸ Card lane** (embedded search) | One search component, two mounts |
| Variants list | Library results grid / Player card-lane list | Rows become card rows with art, OVR chip, year, rarity |
| ★ Fav toggle, Fav list (`favorites.py`) | **Library ▸ Watchlist** + ★ on every card row everywhere | Promoted to a real collection |
| Card compare (`card_compare.py`) | **Library ▸ Compare** (multi-select 2–4) + inline "vs current" in Player diff | Currently near-invisible |
| Best card for target (`card_match` / `best_card_for_playerid`) | Player ▸ Card lane ▸ **"Best match for this player"** suggestion row | Promoted from CLI-only |
| AI chat (Grok) editing selected card | Player ▸ **AI lane** (collapsed by default) | Same `grok_client`; edits stage as diffs like any other source |
| Edit surface (OVR/POT/SM/WF/foot/H/W/body/attrs/PS) | **Player ▸ Attributes / PlayStyles / Appearance panes** | Full-height; group deltas + randomize (§7) |
| Write-topic checkboxes (12 `EDIT_CATEGORIES`) | **Replaced by dirty-field tracking** (P3); categories survive as *diff grouping* and as an advanced "force-write category" override | Topics were a manual workaround for not tracking dirty fields |
| Apply to game (`Ctrl+Enter`) | Changes bar Apply | |

#### 2.4.5 Add team

| v1 feature | v2 home | Change |
|---|---|---|
| Source: Catalog card | **Transfers ▸ Sign a player** ▸ pick from Library | |
| Source: Editor card | Transfers ▸ Sign ▸ "Use current Player draft" | |
| Source: Manual | Transfers ▸ Sign ▸ `Create blank player` → opens Player workspace | |
| Team ID entry | **Team picker** (§7.5) | The critical fix |
| Readiness chips | Sign panel ▸ preflight checklist | Retained — it is good |
| Preview (card render) | Sign panel ▸ live card preview | Retained, upgraded to real card art |
| Add to team (`add_card_to_user_team`, `add_team_lua`, `add_player`) | Transfers ▸ `Sign player` → staged → Apply | |
| Settle pack / Match Ready shortcuts | Post-apply success panel ▸ "Next: settle the new signing" | Contextual follow-up, not buttons that always sit there |
| Copy last playerid | Success panel ▸ copy chip | |
| Open Cards for last | Success panel ▸ `Edit this player →` | |

#### 2.4.6 Squad

| v1 feature | v2 home | Change |
|---|---|---|
| Refresh / Export squad | **Club ▸ toolbar `Refresh`** | Same `export_user_squad` profile |
| Player list (Listbox) | **Club ▸ Squad grid** (§7.1) | Sortable, filterable, multi-select, position/OVR coloured |
| Click → lock target | Row click → selects; `Enter`/double-click → Player | |
| Filter box | Grid filter + facet chips (position, OVR band, age band, contract) | |
| Free agent pool (`target_players.free_agent_pool`) | Club ▸ view switch: **Squad / Youth / Free agents** | Currently backend-only |

#### 2.4.7 Editor

| v1 feature | v2 home | Change |
|---|---|---|
| Target search / Find target / Player ID | Player identity header | |
| Build label | Player ▸ Draft name (used for saved edit profiles, §7.7) | |
| `Apply to game` (local button) | Deleted — dock only | Removes a duplicate Apply (P2) |
| **Load from target** | **Player ▸ auto-loads on open**, with provenance marks | The silent-partial-write fix (P3) |
| Presets (`editor_presets`, `max_99`, …) | Player ▸ `Presets ▾` menu + user-saved **Edit profiles** (§7.7) | |
| Clear form | Player ▸ `Discard changes` (only enabled when dirty) | |
| Grok AI fill / Connect Grok | Player ▸ AI lane; credentials in Settings ▸ AI | Email never rendered in chrome (v1.9.1 regression guard) |
| 12 write-topic checkboxes | See §2.4.4 — replaced by dirty tracking, survive as diff grouping | |
| Accordion sections + Expand/Collapse all | Player pane navigation (left rail) + section collapse | Same lazy-build contract |
| Preview line (OVR · positions · PS count) | Player identity header (always visible, live) | Promoted to the cheat table's always-visible identity header |
| Field set (`player_schema`, `field_map`) | Unchanged backend | |
| Face-stat composites, head/face/hair, tattoos, kit/boots | Player ▸ **Appearance** pane, with chained validity combos (P6) + catalogues for boots/kits/tattoos | Beats the reference table, which leaves these as bare integers |

#### 2.4.8 Catalog

| v1 feature | v2 home | Change |
|---|---|---|
| Download FC26 catalog | **Library ▸ Sources ▸ FC 26** | Runs off-thread with progress + cancel |
| Years 23–26 bulk | Library ▸ Sources ▸ per-year rows with row-level state | Each year shows: rows, last synced, `Sync` |
| Sync player… | Library ▸ result row ▸ `⟳ Refresh this card` | Contextual |
| Probe Futbin | Library ▸ Sources ▸ Advanced ▸ `Diagnose Futbin` | Kept; Cloudflare failure explained in-panel |
| Import HTML… / Import JSON… | Library ▸ Sources ▸ `Import file…` | |
| Activity log textbox | Library ▸ Sources ▸ inline log + Activity drawer mirror | |
| SQLite index rebuild (`card_index`, `rebuild_catalog`) | Automatic; Library ▸ Sources ▸ Advanced ▸ `Rebuild index` | Prebuilt at install; never blocks first search |
| Local years FIFA 18–25 (`card_db/*.csv`) | **Library ▸ Era filter** (18…26) — the legend-import source | Currently reachable only by typing a year into one combo |

#### 2.4.9 Backend features with no v1 GUI home

| Feature | Module | v2 home |
|---|---|---|
| Snapshots list / restore | `snapshot_store`, `product.restore_snapshot` | Activity ▸ History ▸ per-entry `Restore` |
| Undo Lua generation | `undo_apply` | Activity ▸ `Undo` / `Ctrl+Z` |
| Job history | `job_history` | Activity ▸ History timeline |
| Health report | `health_check` | Activity ▸ Health |
| Best-at positions | `ovr_formula.best_at_positions` | **Player identity header — top-3 "Best at" chips, live** (reference-table parity) |
| Position OVR calculator | `ovr_formula.calculate_all_position_ovrs` | Player ▸ Attributes ▸ position OVR matrix |
| Legend import FIFA 18–25 | `import_player`, `le_import_categories`, `build_import_plan` | **Library ▸ Era filter → Transfers ▸ Sign** (flow §4.7) |
| Real head/face resolution | `base_players` | Player ▸ Appearance ▸ "Real face available ✓" + auto-fill |
| Free agent pool | `target_players.free_agent_pool` | Club ▸ Free agents view |
| Protocol / job meta | `protocol`, `product.write_job_meta` | Activity ▸ Queue row detail |
| Config | `companion_config` | Settings |
| **CLI** (`main.py`, ~30 flags) | — | **Retained verbatim.** Settings ▸ Automation shows the exact CLI for the last action ("Copy as CLI") so the GUI teaches the CLI |
| **Web UI** (`web_server`, `web_api`, `web/app.js`) | — | **Retained.** Settings ▸ Remote ▸ `Start web UI` + QR/URL. v2 IA is specified as surface-parity so the web client can adopt the same 5 surfaces against the same `/api/*` routes |

---

## 3. Screen-by-screen specification

Conventions used in the wireframes: `[ Button ]` primary, `( Button )` secondary, `‹ Button ›` ghost, `▾` menu, `◉/○` radio, `☑/☐` checkbox, `▸` disclosure.

Every screen declares: **purpose · layout · controls · empty · loading · error**.

---

### 3.1 Global chrome

**Purpose:** Answer, at all times and without a click: *where am I*, *is the tool connected*, *what have I staged*, *is anything running*.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ ◈ LE Companion   Club   Player   Transfers   Automations   Library                   │
│                  ────                                                                 │
│                                        ● IN CAREER · Chelsea    ⌘K   ⧉ 2   ☰         │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                          └── connection chip  │    │    └ settings
                                                        palette┘    └ activity + queue count
```

**Connection chip — 5 states (v1 had 2).**

| State | Chip | Colour | Sub-text on hover/popover | Apply enabled? |
|-------|------|--------|---------------------------|----------------|
| `no_le` | `○ LE NOT RUNNING` | muted grey | "Start FC 26 through the Live Editor launcher." + `How?` | No — Apply reads `Start the game first` |
| `le_no_worker` | `▲ WORKER NOT ARMED` | amber | "LE is running but the companion script has not loaded." + `Repair worker` | No |
| `menus` | `● CONNECTED · MENUS` | cyan | "Worker alive. You are not in Career Mode — jobs will wait." | **Yes**, but Apply reads `Queue for later` |
| `career` | `● IN CAREER · Chelsea` | green | "Worker alive in Career Mode. Jobs run on the next career event." | Yes — Apply reads `Apply to game` |
| `working` | `◐ APPLYING…` | cyan, static | current step | Disabled while in flight |

The chip is a button; clicking opens the connection popover containing worker path, last pulse age, `Repair worker`, `How to arm`, `Force drain`.

**Command palette (`⌘K` / `Ctrl+K`).** Fuzzy over: every player in the squad, all 51 profiles, all 19 packs, all 5 surfaces, all 6 career ops, recent cards. This is the escape hatch that lets power users skip navigation entirely and is the cheapest way to make a dense tool feel fast.

**Changes bar** — see §5. Always present, one primary button, never more.

**Empty / loading / error for chrome:** the chip is never empty (it always has a state); it shows `◌ CHECKING…` for at most 1.5 s at cold start, then falls to `no_le`. It never shows an error dialog — connection problems are a *state*, not an exception.

---

### 3.2 Club — Setup state (first run)

**Purpose:** Get from "just installed" to "connected" with the fewest possible user actions, and be explicit that most of it is automatic now.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                                                                                      │
│                          Let's connect to your career save                           │
│                                                                                      │
│      ┌────────────────────────────────────────────────────────────────────────┐      │
│      │  ✓  1  Companion worker installed                        automatic     │      │
│      │        LE autorun will load it the next time the game starts.          │      │
│      ├────────────────────────────────────────────────────────────────────────┤      │
│      │  ◌  2  Start FC 26 from the Live Editor launcher            waiting…   │      │
│      │        Watching for the game…                        ‹ Open launcher ›  │      │
│      ├────────────────────────────────────────────────────────────────────────┤      │
│      │  ○  3  Load your Career Mode save                                      │      │
│      ├────────────────────────────────────────────────────────────────────────┤      │
│      │  ○  4  Read your squad                            [ Read my squad ]    │      │
│      └────────────────────────────────────────────────────────────────────────┘      │
│                                                                                      │
│      ▸ How this works — no injection, no memory writes            ▸ Something broke  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Controls:** step 2 `Open launcher` (ghost, opens LE launcher path from config); step 4 `Read my squad` (primary — the *only* primary); two disclosures. Steps auto-tick from the connection poller; the user never confirms a step manually.

**Empty:** n/a — this *is* the empty state of Club.
**Loading:** step rows show `◌` with a static shimmer bar; no spinner animation loops (perf rule §8.2).
**Error:** step 1 can fail (LE path not found) → row turns amber with `Choose Live Editor folder…`. Errors render **inside the step row** and persist until resolved. They never toast, never auto-dismiss (this is the deferred-closure bug class — errors must be state, not events).

---

### 3.3 Club — connected

**Purpose:** The home screen. Show the real squad as a real squad, make it the launchpad for every player-scoped job, and surface the one or two things worth doing right now.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  ⬢  CHELSEA          Career · Season 1 · 45 players · avg OVR 79 · avg age 24.6      │
│  crest              Budget £98.4M                    ( Refresh squad )  ( Formation )│
├──────────────────────────────────────────────────────────────────────────────────────┤
│  Suggested   [ Match Ready ]  ( Full Squad Boost )  ( Club Refresh )        ▸ all 19 │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  Squad ▾   Filter: [ palmer          ]  POS ▾  OVR ▾  AGE ▾  ★    3 selected  ⌫ clear│
├──────┬────────────────────────┬─────┬─────┬─────┬─────┬──────┬───────┬───────┬───────┤
│      │ NAME              ▲    │ POS │ OVR │ POT │ AGE │ FIT  │ FORM  │ CONTR │ ID    │
├──────┼────────────────────────┼─────┼─────┼─────┼─────┼──────┼───────┼───────┼───────┤
│ ☑ ◍  │ Cole Palmer            │ CAM │  86 │  89 │  22 │ ▓▓▓▓ │ ▓▓▓░  │ 2029  │257534 │
│ ☐ ◍  │ Enzo Fernández         │ CM  │  84 │  89 │  24 │ ▓▓▓░ │ ▓▓░░  │ 2031  │247635 │
│ ☑ ◍  │ Didier Drogba          │ ST  │  89 │  89 │  34 │ ▓▓░░ │ ▓▓▓▓  │ 2027  │ 76687 │
│ ☐ ◍  │ Aarón Anselmino        │ CB  │  74 │  84 │  19 │ ▓▓▓▓ │ ▓▓▓░  │ 2030  │278455 │
│ …                                                                                    │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  3 selected   [ Edit 3 players… ]  ( Boost these )  ( Transfer… )  ( Compare )        │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

`◍` = 24 px headshot (or monogram fallback). `▾` on "Squad" switches view: **Squad / Youth / Free agents / Whole database**.

**Controls**

| Control | Behaviour |
|---|---|
| Club header crest + name | From `current_squad.json` `teamid`/`teamname`; crest from local cache (§6.4) |
| `Refresh squad` | Runs `export_user_squad`; the *only* place the user needs that profile by name |
| `Formation` | Opens the pitch view (§3.3.1) |
| Suggested actions | The 5 `workflow` packs, filtered by state (e.g. hide *Signing Settle* unless a player was added this session) |
| Filter box | Debounced 120 ms, matches name / position / ID |
| Facet menus | POS (GK/DEF/MID/ATT + exact), OVR band, AGE band, ★ watchlist only |
| Column header click | Sort asc/desc; sort state persisted |
| Row click | Select (single). `Space` toggles checkbox. `Shift+click` range. `Ctrl+A` all-visible |
| Row double-click / `Enter` | → **Player** workspace for that player |
| Row right-click | Context menu: Edit · Compare · Transfer · Loan · Release · Copy ID · Watchlist |
| Selection bar | Appears only when ≥1 checked. `Edit N players…` is primary when N≥2 (bulk edit, §7.2) |

**Empty (no squad read yet):**

```
        ⬡   No squad yet
            Read your squad from the game to see your players here.
            [ Read my squad ]      ‹ What does this do? ›
```

**Empty (filter matches nothing):** `No players match "palmer" in MID.` + `‹ Clear filters ›`.

**Loading:** 8 static skeleton rows (no animation); club header shows the last known club greyed with `◌ refreshing`. Grid stays interactive with stale data — never blanked.

**Error:** an inline amber strip above the grid: `Couldn't read your squad — the game didn't respond in 45 s.` with `( Retry )` and `‹ Why? ›`. Stale rows remain visible and are marked `stale` in the header. **Never** replace real data with an error screen.

#### 3.3.1 Club ▸ Formation (pitch view)

**Purpose:** Match the reference table's best screen — a rendered pitch, players placed from formation offsets, **click-to-swap** (explicitly *not* drag-and-drop; CTk cannot do smooth DnD and the reference proves click-to-swap is sufficient).

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Formation  4-2-3-1 WIDE ▾           Click a player, then click who to swap with.    │
│  ┌──────────────────────────────────────────────┐  Reserves (34)                     │
│  │                    ◍ 89                      │  ┌──────────────────────────────┐  │
│  │                   Drogba                     │  │ ◍ 84 Enzo Fernández   CM     │  │
│  │      ◍ 82        ◍ 86        ◍ 81            │  │ ◍ 77 Axel Disasi      CB     │  │
│  │     Neto        Palmer       Sancho          │  │ ◍ 74 Anselmino        CB     │  │
│  │            ◍ 84       ◍ 80                   │  │ …                            │  │
│  │           Caicedo    Fofana                  │  └──────────────────────────────┘  │
│  │   ◍ 79    ◍ 82    ◍ 88    ◍ 80               │  Available players (34)            │
│  │  Cucu.   Colwill  A.Cole  James              │                                    │
│  │                  ◍ 83                        │  ‹ Reset ›   ( Save order )        │
│  │                 Sánchez                      │                                    │
│  └──────────────────────────────────────────────┘                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Empty:** "Read your squad to see the pitch." → `[ Read my squad ]`.
**Loading:** pitch drawn, positions filled with grey discs.
**Error:** "Formation data not available for this save — showing a default 4-3-3." (degrade, don't block).

---

### 3.4 Player — the one place a player changes

**Purpose:** Replace Cards + Editor + Add team's composer with a single workspace whose header always tells you who you are editing and whose body is organised by *what kind of change*, not by *where the values came from*.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ ┌────┐  Cole Palmer                                    ⬢ Chelsea   🏴 England        │
│ │ ◍  │  ID 257534 · CAM · Age 22 · 6 ft 1 in (185 cm) · Right                        │
│ │face│  OVR ⟨86⟩ → ⟨91⟩    POT ⟨89⟩    Best at: CAM 91 · RW 89 · CM 87               │
│ └────┘  ● 4 changes staged                       ( Discard )   ‹ Presets ▾ ›  ★      │
├───────────────┬──────────────────────────────────────────────────────────────────────┤
│ ▸ Source      │  ATTRIBUTES                                    Total stats  ▓▓▓▓▓▓░  │
│   Card        │  ┌──────────────────────────────────────────────────────────────┐    │
│   AI          │  │ PACE          ⟨92⟩  [───────●──]  ±0   ⟳     Acceleration 91 │    │
│   Preset      │  │                                              Sprint speed  93 │    │
│ ─────────────  │  ├──────────────────────────────────────────────────────────────┤    │
│ ▪ Overview    │  │ SHOOTING      ⟨88⟩  [──────●───]  +3   ⟳     Positioning   90 │    │
│ ▪ Attributes  │  │                                              Finishing     89 │    │
│ ▪ PlayStyles  │  │                                              Shot power    86 │    │
│ ▪ Positions   │  │                                              …               │    │
│ ▪ Appearance  │  ├──────────────────────────────────────────────────────────────┤    │
│ ▪ Career      │  │ PASSING       ⟨85⟩  [─────●────]  ±0   ⟳                      │    │
│               │  └──────────────────────────────────────────────────────────────┘    │
│               │  ‹ Randomize all ›  ‹ Reset group ›            edited · read · —     │
└───────────────┴──────────────────────────────────────────────────────────────────────┘
```

**Identity header (always visible, never scrolls away)** — direct parity with the reference table, plus the OVR *delta* it lacks:

| Element | Source |
|---|---|
| Headshot | `card_db` image URL → local cache; monogram fallback |
| Club crest | `club_team_id` → crest cache |
| Nation flag | `nationality_id` → flag cache |
| Name, ID, position, age, height (dual unit), foot | `current_squad.json` + `base_players` + card |
| OVR `⟨86⟩ → ⟨91⟩` | Live: current vs staged. Arrow only appears when staged differs |
| POT | same |
| **Best at: top-3** | `ovr_formula.best_at_positions()` recomputed live as attributes change |
| Change counter | Dirty-field count; clicking it opens the diff (§5.2) |
| ★ | Watchlist toggle |

**Left rail** = pane navigation, *not* accordion sections. Two groups:
- **Source** (how values get in): `Card` · `AI` · `Preset`. These *stage* values; they never write.
- **Panes** (what you are changing): Overview · Attributes · PlayStyles · Positions · Appearance · Career.

**Pane: Attributes.** Per the reference table: each of the 6 groups (+GK) has a **group delta slider** that shifts every member attribute, a **per-group randomize** (`⟳`), the group's derived value in a chip, and expandable member fields. A **Total stats** bar sits in the pane header. Below, a position-OVR matrix (`ovr_formula.calculate_all_position_ovrs`) so the user can see what a change does to every position, not just the preferred one.

**Pane: PlayStyles.** 34 checkboxes in 6 semantic groups (Scoring / Passing / Ball control / Defending / Physical / Goalkeeping), each with a `+` toggle for PlayStyle+. Four bitfields are computed, never shown. An `▸ Advanced` disclosure exposes the raw masks read-only, with `Copy`.

**Pane: Positions.** Preferred + alternate positions as chips; roles/role++ as grouped checkboxes; live "Best at" recompute.

**Pane: Appearance.** Chained validity: `Ethnic group ▾` → `Head model ▾` (only valid codes). `Real face available ✓` from `base_players` with `Use real face`. Boots / kits / tattoos get **catalogue pickers with names and thumbnails** — the reference table's clearest weakness, left as bare integers there. Hair, skin tone, body type, run style, celebration all as captioned enums.

**Pane: Career.** Contract, wage, release clause, squad role, retirement flag, morale/form/fitness/sharpness. **Progressive disclosure:** this pane is hidden when the connection state is not `career`; injury fields are hidden unless the player is injured.

**Pane: Overview.** The card render, the diff summary, and the primary follow-ups (`Transfer this player`, `Compare to a card`).

**Controls summary**

| Control | Type | Note |
|---|---|---|
| Group delta slider | −20…+20, snaps to 0 | Applies to all members; each member becomes `edited` |
| `⟳` per group | Randomize within ±N | N configurable in Settings; default ±5 |
| Attribute field | Numeric 1–99 + inline stepper | Provenance-coloured (§6.5) |
| `Presets ▾` | Menu | Built-in `editor_presets` + user-saved edit profiles (§7.7) |
| `Discard` | Secondary, disabled when clean | Confirms if >5 changes staged |
| `★` | Toggle | Watchlist |
| Source ▸ Card | Embedded Library search scoped to this player's name, with **"Best match for this player"** pinned first | |
| Source ▸ Card ▸ per-category "don't copy" | ☑ Age ☑ Head model ☑ Attributes ☑ Name IDs ☑ Nationality ☑ PlayStyles ☑ Body | Reference-table parity; defaults remembered |
| Source ▸ AI | Prompt + `AI fill` | Result stages as a normal diff, reviewable and discardable |

**Empty (no player selected):**

```
        ⬡   No player selected
            Pick someone from your squad, or search every player in the game.
            [ Choose from squad ]      ( Search all players )
```

**Loading:** the identity header paints immediately from the squad row (name / ID / OVR / POT are already known); panes show skeletons while `base_players` / card enrichment resolve off-thread. **The window is never blank** — this is the reference table's deferred-load pattern.

**Error:** if enrichment fails, the header keeps what it knows and shows `Some details unavailable ‹ retry ›`. Unresolved fields stay `unknown` and are excluded from writes (P3). This turns v1's silent-partial-write bug into a visible, harmless state.

---

### 3.5 Transfers

**Purpose:** The surface that does not exist today. Move players between clubs, sign players from any era, and manage the budget — with a team picker, because without one none of it is usable.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Transfers            Budget £98.4M   ( Change budget… )        last read 2 min ago  │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  ◉ Sign a player      ○ Transfer out     ○ Loan      ○ Release      ○ Budget          │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  WHO                                    │  WHERE                                     │
│  ┌────────────────────────────────────┐ │  ┌────────────────────────────────────────┐│
│  │ ◍ Ronaldinho          FIFA 19  ⟨94⟩│ │  │ To club                                ││
│  │   CAM · Age 25 · Brazil            │ │  │ [ ⬢ Chelsea            ] ( Change… )   ││
│  │   ‹ Choose someone else ›          │ │  │ Team ID 5 · Premier League             ││
│  └────────────────────────────────────┘ │  ├────────────────────────────────────────┤│
│                                          │  │ Fee        [ 0            ] £         ││
│  PREFLIGHT                               │  │ Wage       [ 250,000      ] £/wk      ││
│  ✓ In Career Mode                        │  │ Contract   [ 5 ] years  (60 months)   ││
│  ✓ Destination club has 45/52 slots      │  │ Release cl.[ none        ]            ││
│  ▲ Squad already has 4 CAMs              │  │ ▸ Advanced (from-club, presigned…)    ││
│  ✓ Card data complete                    │  └────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────────────────────────────────┤
│                                             [ Stage this signing ]   ‹ Copy as CLI › │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**The five operations** map 1:1 onto `career_ops.CAREER_OPS`, plus `terminate_loan` and `get_budget` folded in:

| Mode | Backend | Required inputs | Confirmation |
|---|---|---|---|
| Sign a player | `add_card_to_user_team` / `import_player` / `TransferPlayer` | player + destination club | Diff preview |
| Transfer out | `career_transfer` | squad player + destination club + fee/wage/contract | Diff preview |
| Loan | `career_loan` | squad player + destination club + **length (1–48 months, editable)** + loan-to-buy | Diff preview |
| Release | `career_release` | squad player | **Typed confirmation** — "Type RELEASE to confirm" + names the player and states it is irreversible without an undo snapshot |
| Budget | `career_set_budget` / `career_get_budget` | amount | Shows `£98.4M → £250.0M` and a `Read current budget` refresh |
| Terminate loan | `career_terminate_loan` | loaned-out player | Diff preview; only shown for players whose loan state is known |

**Loan length is a real control** (`1–48`, default 12, with quick chips `6 mo` `1 yr` `18 mo` `2 yr`). v1 hardcodes 12 in the GUI path even though `generate_loan_lua` accepts 1–48.

**Preflight** is a live checklist, not a validation-on-submit. It answers "will this work?" before the user commits, and it is the honest place to say *what the tool cannot verify* (e.g. `? Couldn't check destination squad size — will attempt anyway`).

**Empty:** mode selected, no player chosen →

```
        ⬡   Who are you signing?
            [ From my watchlist ]   ( Search all players )   ( Search FIFA 18–25 )
```

**Loading:** the WHERE panel keeps its shape and greys; the team picker is prefetched at app start so it is never a loading state.

**Error:** operation-specific and *actionable*: `Transfer needs Career Mode. You're in the menus right now.` + `( Stage it anyway — it'll run when you load your save )`. This is the honest expression of P5: a career op *can* be staged from the menus; it just cannot run yet.

---

### 3.6 Automations

**Purpose:** Run scripted, many-player operations — the 51 profiles and 19 packs — with visible blast radius and a queue you can watch.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Automations       Packs:  [ Match Ready ] ( Full Squad Boost ) ( Season Kickoff ) …  │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  Filter [ fitness         ]   Scope: ● My team  ○ Everyone  ○ Other   ☐ Show advanced │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  SQUAD CONDITION                                                        5 automations│
│  ┌──────────────────────────────────────────────────────────────────────────────────┐│
│  │ ⚡ Full Fitness              my team · 45 players · one-shot          ( Run )     ││
│  │    Set all user senior team players fitness to max (95).                          ││
│  ├──────────────────────────────────────────────────────────────────────────────────┤│
│  │ ⚡ Auto Full Fitness (Daily) my team · 45 players · ⟳ RECURRING       ( Run )     ││
│  │    Re-applies every day / pre-match / after load until you remove it.             ││
│  └──────────────────────────────────────────────────────────────────────────────────┘│
│  MASS EDIT                                                            11 automations │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐│
│  │ ⚠ 99 OVR + 99 Pot (All)     EVERY PLAYER IN THE SAVE · irreversible  ( Run )     ││
│  └──────────────────────────────────────────────────────────────────────────────────┘│
│  … Recurring · Contracts · Appearance · Unlocks · Exports · Save maintenance ·        │
│    Play as Player · Diagnostics                                                       │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  Queue: 2 waiting                                            ‹ View queue ›           │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Every automation row carries three facts v1 never showed:** *scope* (my team / every player / listed IDs / a chosen team), *count* (resolved live from the squad where possible), and *lifetime* (one-shot vs `⟳ RECURRING` for the 5 `user_team_event` scripts + `auto_max_vpro_fitness`). A user running "Auto Full Fitness (Daily)" today has no idea they have installed a persistent event hook.

**Blast-radius gate.** Rows in `mass_edit` and `career_cleanup` render with `⚠` and an amber left border. `Run` opens a confirmation naming the scope in words: *"This changes **every player in the save file**, not just your club. There is no undo for mass edits. Continue?"* — with `Cancel` as the default focus.

**Scope selector.** `My team` / `Everyone` / `Other team…` (opens the team picker). This makes `99pot_in_given_team` and `99ovr_99pot_in_given_team` usable — today they require editing the Lua file by hand, as their own descriptions admit ("edit script first if needed").

**Controls:** pack rail (19, category-chipped, one primary = the most contextually useful), filter box, scope radio, `Show advanced` (reveals `debug`, `set_generic_heads*`, `players_list_retiring`, `custom_*_map` — the six that require editing a script list), per-row `Run`, per-row `▸` detail (shows the exact script path and, for list-based scripts, an editable ID list so they stop being "edit the file yourself").

**Empty:** filter matches nothing → `No automations match "xyz". ‹ Clear filter ›`.
**Loading:** rows are cheap; the *counts* resolve async and show `· …` until known.
**Error:** a failed run posts an inline error strip *on that row* with the worker's message and `( Retry )` / `‹ View log ›`. It also appears in Activity. It does not disappear.

---

### 3.7 Library

**Purpose:** One search over every player and card the app knows about — FC 26, FUT specials, and the local FIFA 18–25 CSVs — plus the data-management panel that keeps it fed.

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Library    [ ronaldinho                                      ]  ⌕   ★ Watchlist (7) │
│  Era: ☑26 ☐25 ☐24 ☐23 ☐22 ☐21 ☐20 ☐19 ☑18   OVR 85–99   POS: CAM ×   Rarity: any ▾  │
├─────────────────────────────────────────────────────────┬────────────────────────────┤
│  312 results · sorted by OVR ▾                          │  ┌──────────────────────┐  │
│ ┌─────┬──────────────────┬────┬─────┬─────┬──────┬────┐ │  │      ╔══════════╗    │  │
│ │ ART │ NAME             │POS │ OVR │ YR  │RARITY│ ★  │ │  │      ║  ◍  94   ║    │  │
│ ├─────┼──────────────────┼────┼─────┼─────┼──────┼────┤ │  │      ║ Ronaldi. ║    │  │
│ │ ▣   │ Ronaldinho       │CAM │  94 │ 19  │ ICON │ ★  │ │  │      ║ CAM ⬢ 🏴  ║    │  │
│ │ ▣   │ Ronaldinho       │CAM │  91 │ 18  │ ICON │ ☆  │ │  │      ╚══════════╝    │  │
│ │ ▣   │ Ronaldinho       │CF  │  89 │ 26  │ HERO │ ☆  │ │  │  PAC 92  DRI 96      │  │
│ └─────┴──────────────────┴────┴─────┴─────┴──────┴────┘ │  │  SHO 89  DEF 40      │  │
│                                                          │  │  PAS 92  PHY 72      │  │
│  ☑ 2 selected    ( Compare 2 )                           │  │  8 PlayStyles        │  │
│                                                          │  └──────────────────────┘  │
│                                                          │  [ Use on a player ]       │
│                                                          │  ( Sign into a club )      │
│                                                          │  ‹ Refresh this card ›     │
├──────────────────────────────────────────────────────────┴────────────────────────────┤
│  ▸ Sources — FC 26 ✓ 19,412 rows · synced 2 h ago  │  FIFA 18–25 ✓ local  │  Sync…   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

**Search is one component** (`card_index` SQLite), mounted here full-size and inside Player ▸ Card lane scoped down. Era chips replace the single "Year" combo — this is what makes "sign a legend from FIFA 18" a discoverable action rather than trivia.

**Sources panel** (`▸` collapsed by default, the merged Catalog tab):

```
│  SOURCES                                                                   ‹ Advanced ›│
│  FC 26 (FUT.GG)      19,412 rows   synced 2 h ago              ( Sync )               │
│  FC 25               14,220 rows   local CSV                   ( Sync )               │
│  FIFA 24 … FIFA 18   local CSVs    9 files                     ‹ Details ›            │
│  Import a file…      HTML / JSON                               ( Import… )            │
│  Advanced: Diagnose Futbin · Rebuild search index · Open card_db folder                │
```

**Controls:** search box (`Ctrl+F`), era chips (multi), OVR range, position, rarity, `★ only`, sort menu, row `★`, row multi-select → `Compare 2–4`, detail card actions (`Use on a player` primary → stages onto the current/chosen target; `Sign into a club` → Transfers; `Refresh this card` → `sync player`).

**Empty (no query):** recent searches + watchlist + "Top rated in FC 26" — never a blank panel.
**Empty (no results):** `No cards match. Try widening the era filter, or ( Sync FC 26 ) — you have no 26 data yet.` The empty state offers the *fix*, which is only possible because Catalog was merged in.
**Loading:** determinate progress bar for index build with a row count, `( Cancel )`, and the grid usable against whatever is already indexed. Never a modal.
**Error:** `FUT.GG unreachable (Cloudflare).` + `( Retry )` `‹ Import a file instead ›` `‹ Diagnose ›`. Network failures never block the UI thread and never lose the user's query.

---

### 3.8 Activity drawer (`Ctrl+J`, ⧉)

**Purpose:** Answer "what did I do, what is happening, and can I undo it" in one place, replacing five ops-bar modals.

```
                                        ┌──────────────────────────────────────────┐
                                        │ Activity        Queue(2) History Health  │
                                        ├──────────────────────────────────────────┤
                                        │ ◐ QUEUED · 12 s                          │
                                        │   Cole Palmer · OVR 86→91, +4 PlayStyles │
                                        │   Waiting for a Career Mode event.       │
                                        │   ▸ What triggers it?      ‹ Cancel job › │
                                        ├──────────────────────────────────────────┤
                                        │ ◐ QUEUED · 12 s                          │
                                        │   Match Ready pack · 45 players          │
                                        ├──────────────────────────────────────────┤
                                        │ ✓ APPLIED · 4 min ago                    │
                                        │   Full Fitness · 45 players              │
                                        │   worker: OK 45 rows            ‹ Undo › │
                                        ├──────────────────────────────────────────┤
                                        │ ✗ FAILED · 11 min ago                    │
                                        │   Transfer · Ronaldinho → Chelsea        │
                                        │   "not in career mode"                   │
                                        │   ( Retry )                     ‹ Lua ›  │
                                        └──────────────────────────────────────────┘
```

**Tabs:** Queue (pending `.lua` + meta), History (`job_history` + `snapshot_store`), Health (`health_check.format_report()`), Files (artefacts produced by the 6 `export` profiles + the squad export).

**Controls:** per-entry `Undo` (only where a snapshot exists — otherwise the control is absent, not disabled-with-no-reason), `Retry`, `Cancel job`, `View Lua`, `Restore snapshot`, and at the panel level `Force drain` under `‹ Advanced ›`.

**Empty:** `Nothing has happened yet. Changes you apply will show up here.`
**Loading:** n/a — reads local files; if the queue folder is unreadable, that is an error state.
**Error:** `Can't read the queue folder.` + path + `( Open folder )`.

---

### 3.9 Settings (☰)

**Purpose:** Everything configurable and everything factual, in one flat, scannable list. Absorbs About.

Sections: **Game & worker** (LE root, queue path, worker status, Repair, How to arm) · **Catalog** (sources, index location, rebuild) · **AI** (Grok connection — status only, never the email) · **Editing** (randomize range, default "don't copy" categories, confirm thresholds) · **Remote** (`Start web UI`, URL, port) · **Automation** (`Copy last action as CLI`, link to CLI reference) · **About** (version, all three paths in full, the no-injection statement as a `Safety` block).

**Empty/loading/error:** each row owns its own state (e.g. LE root row shows `not found` + `Choose folder…`).

---

## 4. Primary flows

Click counts assume keyboard is not used and the app is already running. `→` is one click.

### 4.1 First-run setup — target: **1 click**

1. Launch companion. It paints Club ▸ Setup instantly; step 1 is already `✓` (LE autorun installs the worker).
2. Start FC 26 from the LE launcher (outside the app; step 2 auto-ticks).
3. Load a career save (outside the app; step 3 auto-ticks).
4. → `[ Read my squad ]`.
5. Club renders with the squad. Setup state is gone permanently.

**v1 equivalent:** 4 checklist "Do it" buttons + a header button + reading three status essays. **Target: 1 click, 0 modals.**

---

### 4.2 Make my squad match-ready — target: **1 click**

1. Club (app opens here).
2. → `[ Match Ready ]` in Suggested actions.
3. Confirmation is skipped — this pack is non-destructive and scoped to the user's own team, and the diff is shown in the Changes bar as `Match Ready · 45 players · fitness + sharpness`.
4. Changes bar shows **Queued** with the trigger hint. When the worker runs it, it flips to **Applied ✓ 45 players**.

**Target: 1 click.** (v1: Boost tab → scroll → find the right card among 51 → Run, then read the dock. 3–4 clicks plus a scan.)

---

### 4.3 Upgrade a player with a card — target: **4 clicks**

1. Club → double-click **Cole Palmer** *(1)*.
2. Player opens; identity header already populated; left rail → **Source ▸ Card** *(2)*. The embedded search is pre-filled with "Cole Palmer" and the **Best match for this player** row is pinned first.
3. → the desired variant, e.g. `TOTY Cole Palmer 91` *(3)*. Values stage; the header immediately shows `OVR ⟨86⟩ → ⟨91⟩` and `● 12 changes staged`; "don't copy" toggles (Age / Head model / Name IDs / Nationality) are remembered from last time.
4. → `[ Apply ]` in the Changes bar *(4)* → diff sheet → `Apply` (confirm click).

**Target: 4 clicks + 1 confirm.** (v1: Cards tab → type target name → Find → type card name → Search → pick variant → "Edit selected" → check write topics → Apply. ~8 clicks + 2 text entries, and the user cannot see what will change.)

---

### 4.4 Sign a player from any year — target: **5 clicks**

1. Library *(1)*, type a name.
2. Era chips → tick `19` *(2)* (or leave all on).
3. → the result row *(3)*; detail panel renders the real card.
4. → `( Sign into a club )` *(4)* → Transfers opens in **Sign** mode with WHO pre-filled.
5. Destination defaults to the user's club; fee/wage/contract have sane defaults; → `[ Stage this signing ]` *(5)*.
6. → `[ Apply ]` → diff (`New player Ronaldinho (ICON 94) joins Chelsea · fee £0 · wage £250k · 5 yr`) → `Apply`.

**Target: 5 clicks + 1 confirm.** (v1: Add team tab → choose source → run a separate search → select → **find a team ID with no tool to find it** → Add. Effectively impossible for any club but your own.)

---

### 4.5 Edit attributes manually — target: **3 clicks**

1. Club → double-click the player *(1)*.
2. Left rail → **Attributes** *(2)* (default pane, so usually 0).
3. Drag the **Shooting** group delta to `+3` — every shooting attribute shifts, the group chip and Total-stats bar update, "Best at" recomputes live. Or type into a single field.
4. → `[ Apply ]` *(3)* → diff (`Shooting +3 across 6 attributes · OVR 86 → 87 · nothing else changed`) → `Apply`.

**Target: 3 clicks + 1 confirm.** Critically, the diff proves only the fields the user touched are written — the v1 silent-partial-write class is structurally impossible.

---

### 4.6 Run a transfer / loan / budget operation — target: **4 clicks**

**Transfer out:**
1. Club → right-click the player → `Transfer…` *(1–2)*.
2. Transfers opens in **Transfer out** mode with WHO filled. → `( Change… )` next to *To club* *(3)* → team picker → type "Newc" → → `Newcastle United` *(4)*.
3. Fee/wage/contract defaults are shown and editable; preflight confirms Career Mode.
4. → `[ Stage this transfer ]` → `[ Apply ]` → diff → `Apply`.

**Loan:** identical, plus a length chip (`6 mo` / `1 yr` / `18 mo` / `2 yr` / custom 1–48).
**Budget:** Transfers → `Budget` mode *(1)* → `( Read current budget )` *(2)* → type amount → `[ Stage ]` *(3)* → `[ Apply ]` *(4)*. Diff reads `Transfer budget £98.4M → £250.0M`.
**Release:** requires typing `RELEASE`; the confirm names the player and states no snapshot-free undo exists.

**Target: 4 clicks + 1 confirm.** (v1: Boost tab → scroll past 51 cards → find the career-ops panel → type a player ID → **type a team ID you have no way to obtain** → Release fires with no confirmation at all.)

---

### 4.7 Import a legend from FIFA 18–25 — target: **6 clicks**

1. Library *(1)*, type "Ronaldinho".
2. Era chips → `18` *(2)*.
3. → the `FIFA 18 · ICON 91` row *(3)*.
4. → `( Sign into a club )` *(4)*.
5. Transfers ▸ Sign shows an **Import plan** panel unique to cross-era players (`import_player.build_import_plan`):
   ```
   IMPORT PLAN — Ronaldinho (FIFA 18)
   ✓ Attributes, PlayStyles, positions      will be copied
   ✓ Real face found (base player 20801)    will be used
   ▲ Nationality mapped Brazil → id 54       ‹ change ›
   ☐ Don't copy: Age  ☐ Head model  ☐ Name IDs
   ```
   Adjust if wanted → `[ Stage this signing ]` *(5)*.
6. → `[ Apply ]` *(6)* → diff → `Apply`.
7. Success panel offers `Edit this player →` and `New Signing Settle` as follow-ups.

**Target: 6 clicks + 1 confirm.** (v1: this flow exists only as `import_player` backend code plus CLI; there is no GUI path.)

---

## 5. The apply model

### 5.1 One meaning of Apply

Everything the app can do is expressed as a **Change** appended to a single **Change Set**:

| Change kind | Produced by | Example diff line |
|---|---|---|
| `player_edit` | Player panes, Card lane, AI, Preset | `Cole Palmer · OVR 86→91, 6 PlayStyles added` |
| `automation` | Automations row / pack | `Full Fitness · 45 players in your club` |
| `roster` | Transfers | `Ronaldinho joins Chelsea · fee £0 · 5 yr` |
| `budget` | Transfers ▸ Budget | `Transfer budget £98.4M → £250.0M` |
| `restore` | Activity ▸ Undo / Restore | `Undo: revert Cole Palmer to OVR 86` |

**Apply** = *serialise the Change Set to Lua, write it to `queue/`, and track it to completion.* It is available from exactly one control (the Changes bar) plus `Ctrl+Enter`. Nothing else in the app writes to `queue/`.

The Changes bar:

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  CHANGES (2)   Cole Palmer · OVR 86→91 · +4 PlayStyles                                │
│                Match Ready · 45 players                    ( Review )   [  Apply  ]   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

When empty it is a thin, quiet strip: `No changes staged. Pick a player or run an automation.` — it never disappears (layout stability) and never shows a green button with nothing to do.

### 5.2 The diff preview — mandatory, plain English

`Review` (and the confirm step of `Apply`) opens a sheet that describes the write in football language, grouped by the 12 `EDIT_CATEGORIES` but *phrased*, not dumped:

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Review 2 changes before applying                                                    │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  ① COLE PALMER  (id 257534)                                          ‹ remove ›      │
│     Overall           82 → 91                                                        │
│     Potential         89 → 94                                                        │
│     Shooting          +3 across 6 attributes  (Finishing 89→92, Shot power 86→89, …) │
│     PlayStyles        6 added — Finesse Shot+, Rapid, Incisive Pass, Trickster,       │
│                       Press Proven, Whipped Pass                                      │
│     Name              unchanged                                                       │
│     Age               unchanged  (you chose "don't copy age")                          │
│     Appearance        not touched                                                     │
│     ▸ 14 raw fields · view Lua                                                        │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  ② MATCH READY PACK                                                  ‹ remove ›      │
│     Fitness → 95 and Sharpness → 100 for all 45 players in Chelsea                    │
│     Nothing outside your club is touched.                                             │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  ↩ An undo snapshot will be saved for ①. ② cannot be undone automatically.            │
│                                            ‹ Copy Lua ›  ( Cancel )  [ Apply 2 ]     │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Rules for diff copy:
- **Say what did *not* change** for the fields users worry about: name, age, nationality, appearance. Silence is what caused the v1 trust problem.
- **Count, don't enumerate** past 6 items; the `▸` disclosure holds the raw field list and the generated Lua.
- **Name the blast radius** for automations, in players, and say explicitly when nothing outside the user's club is affected.
- **State undo availability per change**, before the user commits — not after.
- The button is `Apply 2`, not `OK`. The count is part of the label.

### 5.3 Queued vs applied — the honesty model

Five states, each with distinct copy, colour, and control. This is the model that makes the auto-arm reality legible.

| State | Chip | What actually happened | Copy shown | Controls |
|---|---|---|---|---|
| **Staged** | `● N changes` cyan | Nothing written. App memory only. | `2 changes ready. Nothing has been written yet.` | Review · Apply · Discard |
| **Queued** | `◐ QUEUED · 12 s` cyan | `.lua` is in `queue/`. Worker armed but idle. | **`Queued. This runs the next time a Career Mode event fires — advance a day, open your squad screen, or enter a menu.`** | ▸ What triggers it? · Cancel job |
| **Running** | `◐ APPLYING…` cyan | Worker picked the job up (heartbeat/step evidence). | `The game is applying your changes…` | — (Apply disabled) |
| **Applied** | `✓ APPLIED` green | Worker returned a result token for *this* job id. | `Applied · 45 players updated.` + `Verify` | Undo · Verify · Dismiss |
| **Failed** | `✗ FAILED` red | Worker returned an error, or the job was rejected. | Worker's own message, verbatim, plus a plain-English translation. | Retry · View Lua · Copy error |

Additional honesty rules:

1. **Queued is not a failure and not a timeout.** After the wait window elapses with no career event, the state stays `Queued` and the copy changes to `Still queued (2 min). Nothing is wrong — the game hasn't fired a career event yet.` It never flips to an error.
2. **"Applied" requires proof.** The only thing that may set `Applied` is a worker result matching the job id (`protocol` / `_last_result` / job meta). Heartbeat alone is not proof. Elapsed time is never proof.
3. **`Verify` is a real, offered action.** For player edits, `Verify` re-reads the squad and shows `Confirmed in save: OVR 91 ✓` or `Couldn't confirm — the save still reads 86.` The app is allowed to say it does not know.
4. **The trigger hint is specific.** `▸ What triggers it?` lists the actual events (advance day, enter squad screen, pre-match, post-match, load save) rather than "interact with the game".
5. **Menus state is not blocked.** With connection `menus`, Apply is enabled and its label reads `Queue for later` — because queueing is legitimate. Only `no_le` and `le_no_worker` disable Apply, and both explain the fix in the button's own tooltip.

---

## 6. Visual design

### 6.1 The brief in one line

**A broadcast-graphics football product rendered as a dark, dense desktop tool.** Every screen should contain at least one piece of real football imagery (crest, face, card, pitch, kit colour) so it reads as a football app in a 200 ms glance — but data density, not decoration, drives the layout.

### 6.2 Tokens

Extends the existing `src/ui_theme.py` palette. No new hex outside this table.

```python
# Surfaces (elevation ladder — CTk has no shadows; use bg steps + 1px border)
BG          = "#0a0e14"   # app background
PANEL       = "#0f1620"   # panel
CARD        = "#141d2a"   # card / row
CARD_HOVER  = "#1a2534"
ROW_ALT     = "#0d1420"   # zebra
BORDER      = "#1e2a3a"
SKELETON    = "#1a2433"

# Semantics
ACCENT      = "#22d3ee"   # information / selection / secondary CTA
SUCCESS     = "#22c55e"   # THE primary action colour — used at most once per screen
WARNING     = "#f59e0b"
DANGER      = "#ef4444"
TEXT        = "#e6edf5"
MUTED       = "#8b9bb0"
MUTED_DIM   = "#5b6a7d"

# Provenance (P3)
PROV_READ    = TEXT       # read from game/card
PROV_EDITED  = ACCENT     # user changed → will be written
PROV_UNKNOWN = MUTED_DIM  # never read → renders "—", never written

# Spacing  SP1 4 · SP2 8 · SP3 12 · SP4 16 · SP5 24 · SP6 32 · SP7 48
# Radius   XS 4 · SM 6 · MD 10 · Pill 999
```

### 6.3 OVR colour ramp

OVR is the number users read first. It must be colour-coded everywhere it appears (grid, card row, identity header, diff, card art).

| OVR | Name | Fill | Text | Where |
|---|---|---|---|---|
| ≤ 59 | Basic | `#3a4553` | `#c9d4e2` | grid, chips |
| 60–69 | Bronze | `#7a4a22` | `#ffd9b3` | |
| 70–74 | Silver | `#5c6672` | `#eef3f8` | |
| 75–79 | Gold low | `#8a6b1f` | `#ffeeb8` | |
| 80–84 | Gold | `#b8901f` | `#1a1200` | |
| 85–89 | Gold high | `#e0b02a` | `#1a1200` | |
| 90–94 | Elite | `#f0d264` | `#1a1200` + 1px `#fff2c0` ring | |
| 95–99 | Special | `#e8f4ff` on `#0e7490` gradient | `#062b36` | reserve for genuine 95+ |

The **delta** form `⟨86⟩ → ⟨91⟩` colours each chip by its own band, so an upgrade is visible as a colour jump, not just a number change.

### 6.4 Football imagery — sources and the caching contract

| Asset | Source | Fallback | Cache |
|---|---|---|---|
| Player headshot | `card_db` image URL (e.g. sofifa CDN column already present in `fc26_datahub.csv`), FUT.GG card image | Circular monogram: initials on a position-coloured disc | `card_db/_img/heads/{id}.png` |
| Club crest | `club_team_id` → crest URL; user-supplied crest pack folder | Circle with club initials, tinted from kit colours if known | `card_db/_img/crests/{teamid}.png` |
| Nation flag | `nationality_id` | 2-letter code chip | `card_db/_img/flags/{natid}.png` |
| Card art | Rarity → local card frame template + composited OVR/pos/name | Flat rarity-coloured panel with the same text layout | `assets/cards/{rarity}.png` |

**Contract (non-negotiable, and the fix for the reference table's blocking-I/O weakness):**
1. Images are **never** fetched on the UI thread.
2. A row renders its fallback immediately and swaps in the real image when it arrives; layout does not shift (fixed box, `24 px` in grids, `96 px` in the identity header, `160 px` on card detail).
3. Nothing in the app *waits* for an image. An offline user sees monograms and a fully functional app.
4. Fetches are batched per visible viewport, capped in flight, and cancelled on scroll-away.
5. `Settings ▸ Catalog ▸ ☐ Download player images` lets a user turn the whole subsystem off.

### 6.5 Position colours

Applied to the position chip, the pitch discs, and the monogram fallback.

| Group | Positions | Fill | Text |
|---|---|---|---|
| GK | GK | `#eab308` | `#1a1200` |
| DEF | CB LB RB LWB RWB | `#3b82f6` | `#f0f6ff` |
| MID | CDM CM CAM LM RM | `#22c55e` | `#04240f` |
| ATT | LW RW CF ST | `#f43f5e` | `#fff0f3` |

### 6.6 Component set

Everything in v2 is built from these. Adding a component requires adding it here first.

| # | Component | Props | Used on |
|---|---|---|---|
| C1 | `Button` — `primary` \| `accent` \| `secondary` \| `ghost` \| `danger` | icon, size, loading, disabled+reason | everywhere |
| C2 | `ConnectionChip` | 5 states + popover | chrome |
| C3 | `OvrChip` | value, delta?, size | grid, header, diff, cards |
| C4 | `PositionChip` | pos, size | grid, pitch, header |
| C5 | `Avatar` | player id, size, fallback monogram | grid, header, pitch, cards |
| C6 | `Crest` / `Flag` | id, size | club header, cards, transfers |
| C7 | `PlayerCard` | card dict, size `sm|md|lg` | Library detail, Transfers preview, Player Overview |
| C8 | `DataGrid` | columns, rows, sort, filter, multi-select, zebra, sticky header | Club, Library |
| C9 | `IdentityHeader` | player, staged diff | Player |
| C10 | `AttributeGroup` | group, delta slider, randomize, members | Player ▸ Attributes |
| C11 | `StatBar` | value, max, tone | Total stats, fitness/form columns |
| C12 | `CheckboxGroup` | 6 semantic groups, `+` variants | PlayStyles, roles, don't-copy |
| C13 | `ChainedCombo` | parent enum → filtered child enum | Appearance (head model), formation |
| C14 | `CaptionedEnum` | value → meaning (`183 cm / 6 ft 1 in`) | Body, accelerate type, formation |
| C15 | `TeamPicker` | search, recent, league grouping | Transfers, Automations scope |
| C16 | `DiffSheet` | change set → phrased lines + raw disclosure | Apply |
| C17 | `ChangesBar` | change set, state machine | chrome |
| C18 | `ActivityEntry` | job, state, controls | Activity drawer |
| C19 | `EmptyState` | icon, title, body, **required** CTA | every scrollable region |
| C20 | `Skeleton` | rows/blocks, static | every async region |
| C21 | `InlineError` | message, cause, actions, persistent | rows, panels, steps |
| C22 | `AutomationRow` | scope, count, lifetime, blast-radius tone | Automations |
| C23 | `Pitch` | formation, players, click-to-swap | Club ▸ Formation |
| C24 | `FacetChips` | multi-select filter chips | Club, Library, Automations |
| C25 | `CommandPalette` | fuzzy index | chrome |

### 6.7 The one-primary-per-screen rule

**Exactly one control per screen may use `SUCCESS` solid fill.** In practice that is the Changes bar's `Apply` on every screen that can stage changes.

Consequences, enforced at review:
- Automations' 51 `Run` buttons are `secondary`, not green. The pack rail's single most contextually relevant pack may be `accent`.
- Club's `Refresh squad` is `secondary`. The Suggested-actions strip has at most one `accent`.
- Transfers' `Stage this signing` is `accent` — it stages, it does not apply.
- Setup is the one screen with no Changes bar, so its single step CTA may be `primary`.
- Dialogs are their own screen: `DiffSheet` may have one `primary` (`Apply N`); destructive dialogs use `danger` and default focus to `Cancel`.

**Review test:** a screenshot with two green fills fails review.

### 6.8 Typography and density

| Role | Font | Size | Colour |
|---|---|---|---|
| Screen title | Segoe UI Semibold | 20 | TEXT |
| Player name (header) | Segoe UI Bold | 22 | TEXT |
| Section | Segoe UI Semibold | 14 | TEXT |
| Body | Segoe UI | 13 | TEXT |
| Meta / helper | Segoe UI | 11 | MUTED |
| Badge / chip | Segoe UI Bold | 10 | tone |
| Numeric data (grid, attributes, IDs) | **Consolas / tabular** | 12 | TEXT |

Density: grids and attribute panes tight (`SP2`, 26 px rows); guidance surfaces (Setup, empty states, diff) airy (`SP4`–`SP5`). Numbers are always tabular so columns align without a monospace *layout*.

---

## 7. New UX capabilities

These do not exist in v1 and are the reason v2 beats both the current app and the reference cheat table.

### 7.1 Squad grid with sort and filter

Replaces the `tk.Listbox` dump. Columns: `☐ · avatar · name · pos · OVR · POT · age · fitness · form · contract · ID`. Click-to-sort on every column, persisted. Free-text filter (debounced) plus facet chips (position group, OVR band, age band, watchlist). Views: Squad / Youth / Free agents / Whole database.

*CTk note:* CTk has no virtualised grid. Build on a `CTkScrollableFrame` with **windowed rendering** — instantiate only the ~40 visible row frames and rebind their content on scroll. A 45-player squad needs no windowing at all; the whole-database view does. This is the only place v2 requires new low-level widget work, and it is contained in `C8`.

### 7.2 Multi-select and bulk edit

Check any number of rows → `Edit N players…` opens a scoped Player workspace:

```
Editing 3 players — Palmer, Drogba, Enzo
Changes apply to all 3. Fields that differ between them show "—".

  Overall        [ —    ]  ← leave blank to keep each player's own value
  Potential      [ 94   ]  ← will set all 3 to 94
  Shooting group [ +3   ]  ← relative: shifts each player's own values
  PlayStyles     ☑ Rapid (adds to all 3, keeps existing)
```

Rules: absolute vs relative is explicit; blank means "don't touch"; the diff enumerates per-player consequences (`Palmer OVR 86→86 (unchanged), Drogba 89→89 (unchanged), Enzo 84→84`); one undo snapshot covers the whole batch. This is the reference table's single biggest gap — it has no player list, no multi-select, and no bulk edit in the GUI at all.

### 7.3 Undo / redo

- `Ctrl+Z` undoes the last **applied** change set by queueing generated undo Lua (`undo_apply.generate_undo_lua`) from the pre-apply snapshot (`snapshot_store`).
- Undo is itself a change set: it is staged, diffed (`Revert Cole Palmer to OVR 86, remove 6 PlayStyles`), and applied. Undo is never silent.
- `Ctrl+Shift+Z` redoes.
- History depth: 20 applied sets, persisted across restarts.
- **Where undo is impossible, say so before applying** — mass edits over the whole database cannot be snapshotted, and the diff sheet states this in the `↩` line (§5.2).
- Staged-but-unapplied changes are undone with `Discard`, which is a different, cheaper action and is labelled differently.

### 7.4 Diff preview

Covered in §5.2. It is listed here too because it is a *capability*, not a dialog: the diff engine (change set → phrased lines) is a shared service used by the Changes bar summary, the Review sheet, the Activity entries, and the `Verify` comparison.

### 7.5 Team picker — the unblocker

**Data.** Build `card_db/teams.sqlite` at index time from three sources, merged by `teamid`:
1. `club_team_id` + `club_name` + `league_name` + `league_id` from every `card_db/*.csv` (present today — `fc26_datahub.csv` has all four columns).
2. FUT.GG `clubEaId` / `uniqueClubSlug` from `card_db/futgg/`.
3. Live truth from the game: a new `list_teams` export job written alongside the existing `export_user_squad` bridge script, so the picker reflects the *actual save* (including custom/edited clubs) rather than only the shipped database.

**UI.**

```
┌──────────────── Choose a club ────────────────┐
│ [ newc                        ]               │
│ RECENT   ⬢ Chelsea (5)   ⬢ Real Madrid (243)  │
├───────────────────────────────────────────────┤
│ ⬢ Newcastle United      13  Premier League    │
│ ⬢ Newcastle Jets       112  A-League          │
├───────────────────────────────────────────────┤
│ ▸ Enter a team ID manually                    │
└───────────────────────────────────────────────┘
```

Search by name, league or ID; grouped by league; recents pinned; manual ID entry survives as a disclosure for teams the databases do not know. Used by Transfers (all 4 roster ops), Automations (scope = "Other team", which finally makes `99pot_in_given_team` usable), and Library (club filter).

### 7.6 Favourites / watchlist

`favorites.py` exists and is nearly invisible. In v2 the `★` appears on every player and card row in the app, and **Library ▸ Watchlist** is a first-class collection with its own count in the chrome. Watchlist entries carry a note field and feed the Transfers "WHO" picker as the default first option ("From my watchlist"), which is how a user plans a transfer window across sessions.

### 7.7 Edit profiles — export and re-apply

A saved, named, portable set of field changes (`{name, fields[], provenance, created}`) stored as JSON in `profiles/edits/`.

- **Save:** Player ▸ `Presets ▾` ▸ `Save these changes as…` — captures only `edited` fields, so the profile is inherently minimal and safe to re-apply.
- **Apply:** to the current player, or to **N selected players** from the Club grid (composes with §7.2).
- **Export / Import:** a single `.json` file, shareable. This is what turns "I built the perfect meta CDM template" into something a user can keep, version, and give away.
- Ships with the existing `editor_presets` (`max_99`, …) converted into this format, so there is one concept, not two.

---

## 8. Accessibility, performance and honesty rules

These are acceptance criteria. A PR that violates one does not merge.

### 8.1 Accessibility and input

| # | Rule |
|---|---|
| A1 | **Every action reachable by keyboard.** Tab order follows visual order; `Enter` activates the focused control; `Esc` closes any sheet/drawer without applying. |
| A2 | **Visible focus ring** (2 px `ACCENT`) on every focusable control. CTk requires setting this explicitly on `CTkEntry`/`CTkButton` focus events — it is not free. |
| A3 | **Global shortcuts:** `Ctrl+K` palette · `Ctrl+F` search on the active surface · `Ctrl+Enter` Apply · `Ctrl+Z`/`Ctrl+Shift+Z` undo/redo · `Ctrl+J` Activity · `1`–`5` switch surface · `Esc` close · `Space` toggle row selection · `Ctrl+A` select all visible. All are listed in Settings ▸ Shortcuts. |
| A4 | **Contrast ≥ 4.5:1** for all body text and ≥ 3:1 for chips and borders. The gold OVR bands use dark text on light fill for exactly this reason. |
| A5 | **Colour is never the only signal.** Provenance uses colour *and* a glyph/`—`. Connection state uses colour *and* a word. Blast-radius uses colour *and* the `⚠` glyph *and* the word "every player". |
| A6 | **Hit targets ≥ 28 px** high; grid rows 26 px with a 32 px click band. |
| A7 | **Text scales.** `Settings ▸ Display ▸ Text size` (S/M/L) drives the font scale; layouts use relative padding so nothing clips. Minimum supported window: **1040 × 720** with the Apply button fully visible. |
| A8 | **No motion-dependent information.** No content is conveyed only by an animation. |
| A9 | **Disabled controls state why** — every disabled control has a tooltip giving the reason and, where possible, the fix. "Disabled with no explanation" is a bug. |

### 8.2 Performance

| # | Rule |
|---|---|
| P-1 | **Cold start to interactive < 400 ms.** Only the Club shell is built at startup; other surfaces build lazily on first visit (keep the existing `_ensure_tab` contract). |
| P-2 | **The UI thread never blocks > 100 ms.** All disk scans, SQLite index builds, CSV loads, network calls (FUT.GG / Futbin / images) and Lua generation run on workers. Results marshal back via `after(0, …)`; **Tk variables are only ever read or written on the UI thread** (the existing snapshot pattern in `boost.py:307` is the correct model — generalise it). |
| P-3 | **Every long operation is cancellable and shows determinate progress** where a total is knowable (index rows, catalog pages). Indeterminate work shows elapsed time, never a fake percentage. |
| P-4 | **The SQLite catalog index is prebuilt at install** and rebuilt in the background on source change. First search is never the thing that triggers a multi-second build. |
| P-5 | **Skeletons are static.** No `after()` animation loops, no opacity thrash, no scroll interpolation, no `yview` monkey-patching. This is a hard prohibition carried over from the documented v1.10 freeze incident (`src/ui_perf.py`). |
| P-6 | **No eager construction of large forms.** Player panes build on first reveal; attribute groups build members on expand. The full field set (`player_schema`) must never be instantiated in one synchronous pass. |
| P-7 | **Images obey §6.4** — off-thread, cached to disk, fallback-first, viewport-batched, cancellable, and switchable off entirely. |
| P-8 | **Grids window their rows** above 200 items (§7.1). Below that, render directly. |
| P-9 | **Status paint is throttled** (≤ 4 Hz) and never calls `root.update()`. |
| P-10 | **Memory ceiling:** the whole-database view must not materialise every row — it queries the index with `LIMIT`/`OFFSET` per window. |

### 8.3 Honesty

| # | Rule |
|---|---|
| H1 | **Never claim "Applied" without proof.** Only a worker result token matching the job id may set `Applied`. Not a heartbeat. Not elapsed time. Not the absence of an error. |
| H2 | **Never write a field the user did not touch.** Only `edited`-provenance fields are serialised. `unknown` fields are structurally excluded and render as `—`. |
| H3 | **Queued is stated as queued, with its trigger.** The exact copy — *"Runs the next time a Career Mode event fires — advance a day, open your squad screen, or enter a menu."* — is the product's most important sentence. Never soften it to "processing". |
| H4 | **Errors persist until resolved or dismissed.** They render inline at the point of failure, keep the underlying data visible, and carry the raw worker message plus a plain-English translation. No toasts, no auto-dismiss, no silent swallowing. (Root cause of the v1 vanishing-error bug: closures deferred past the widget's life. The structural fix is that error state lives in the surface's model, not in a callback.) |
| H5 | **Every terminal state names the next action as a control.** Empty, error, and success panels each contain at least one button. Prose alone is not an exit. |
| H6 | **Say what the tool does not know.** `Couldn't confirm — the save still reads 86.` and `? Couldn't check destination squad size` are correct, shippable strings. Guessing is not. |
| H7 | **Name the blast radius before acting**, in players and in scope words, and state whether undo is available — before the user commits, not after. |
| H8 | **Never surface secrets in chrome.** AI account identity shows as `signed in` only; the v1.9.1 email-leak fix is a permanent regression test. |
| H9 | **Never imply memory injection.** All copy describes the queue/worker model. The Safety block in Settings states it plainly. |
| H10 | **Recurring means recurring.** The 6 event-hook automations are labelled `⟳ RECURRING`, explain that they persist, and offer a `Remove` action. Installing a permanent hook without saying so is dishonest. |

---

## 9. Build order

Nine slices. Each leaves the app shippable.

| Slice | Contents | Unblocks |
|---|---|---|
| **B1** | Change Set model + Changes bar + 5-state machine + `DiffSheet` skeleton | Every flow; kills the 6-Apply-controls problem immediately |
| **B2** | Provenance/dirty-field tracking in `player_edit`; `unknown` rendering; write-set derived from dirty fields | Fixes the silent-partial-write bug (P3, H2) — highest severity |
| **B3** | `TeamPicker` + `teams.sqlite` builder + `list_teams` bridge job | Unblocks all of Transfers |
| **B4** | Component kit C1–C8, C19–C21; tokens; OVR/position ramps | All surfaces |
| **B5** | Club surface: squad grid, facets, multi-select, club header, Setup state | Home + Squad merge |
| **B6** | Player workspace: identity header, panes, group deltas, PlayStyle checkboxes, chained appearance combos, sources | Cards + Editor + Add-team-composer merge |
| **B7** | Transfers surface: 6 ops, preflight, confirmations, import plan | Career ops become usable for the first time |
| **B8** | Automations + Library (+ merged Sources), watchlist, compare, era filter | Boost + Catalog merge |
| **B9** | Activity drawer, undo/redo stack, verify, edit profiles, command palette, images subsystem | J7 + polish |

**Regression guards to keep green throughout:** the 66+ existing unit tests; no scroll monkey-patching; no eager form construction; no Tk variable access off the UI thread; the AI-email leak test; queue/bridge Lua generation byte-identical for unchanged inputs.

---

## 10. What we are deliberately taking from — and beating — the reference

| Reference (xAranaktu FC 25 Cheat Table) | v2 |
|---|---|
| Always-visible identity header (crest, headshot, name, ID, OVR/POT/age, live Best-At top-3) | **Adopted verbatim** (§3.4), plus a live `OVR 86 → 91` staged-delta the reference lacks |
| Attribute group delta sliders + per-group randomize + Total Stats bar | **Adopted** (C10) |
| 34 PlayStyles as 6 semantic checkbox groups over 4 bitfields | **Adopted** (C12); raw masks read-only behind a disclosure |
| Chained validity combos (ethnic group → head codes) | **Adopted** (C13) |
| Meaningful enum captions (`183 cm / 6 ft 1 in`, `Hunter (+3 PAC, +3 SHO)`, `4-2-3-1 WIDE`) | **Adopted** (C14) |
| Clone-from-FUT panel with real card render + per-category "don't copy" | **Adopted** (§3.4 Source ▸ Card), extended across all 9 eras |
| Formation pitch, click-to-swap, reserves, "Available Players (N)" | **Adopted** (§3.3.1) |
| Dirty-field tracking; only touched fields written; unsaved-changes prompt | **Adopted as a core principle** (P3) — this is also our top bug fix |
| Deferred load with a visible loading panel | **Adopted** (P-1, C20) |
| Progressive disclosure (career fields only in career; injury fields only when injured) | **Adopted** (§3.4 Career pane, Automations advanced) |
| ✗ No undo | **§7.3** undo/redo stack, diffed and reversible |
| ✗ No player list / grid / filtering / sorting | **§7.1** sortable, filterable, faceted squad + database grid |
| ✗ No multi-select, no bulk edit in the GUI | **§7.2** multi-select + scoped bulk edit + batch undo |
| ✗ Boots / kits / tattoos are bare integers | **§3.4 Appearance** catalogue pickers with names and thumbnails |
| ✗ Blocking network I/O | **§6.4 / P-2 / P-7** everything off-thread, cached, cancellable, fallback-first |

---

*End of spec. Every screen, control, state and rule above is intended to be implementable on CustomTkinter with the existing queue/bridge pipeline unchanged.*
