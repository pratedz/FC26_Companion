# V2 Feature Port Catalog — xAranaktu FC 25 Cheat Table → FC 26 LE Companion

**Status:** implementation plan. No code written yet.
**Target:** `LE_Profile_Executor` (Python host + LE Lua job bridge, see `docs/PROTOCOL.md`).
**Date of source snapshot:** FC 25 CT v25.1.6 (2025-03-13) / FC 26 LE v26.3.5 (2025-06-30).

---

## 0. How this document was produced, and what you can trust in it

Every claim below is sourced from one of five places. Absolute paths, cited inline.

| Source | What it gave us |
|---|---|
| `C:\Users\prated\Desktop\FC 25 CT v25.1.6\FC25.CETRAINER` | Plain XML, 24 976 lines. Parsed the whole `CheatEntries` tree: **1112 entries = 113 group headers + 81 `AssemblerScript` nodes + 918 pointer entries.** |
| `C:\Users\prated\Desktop\FC 25 CT v25.1.6\lua\consts.lua` | 41 live AOB signatures (`:40-94`), `MODE_MANAGERS_OFFSETS` (`:7103-7124`), 20 struct-offset tables (`:7126-7422`), `PlayerGrowthManager_Data` (`:7424-7571`), `OVR_FORMULA` (`:7651`), `HEAD_TYPE_GROUPS` (`:8421`), `FORMATIONS_DATA` (`:8892`). |
| `C:\Users\prated\Desktop\FC 25 CT v25.1.6\lua\helpers.lua` | 62 top-level functions; 26 of them touch a career manager. Manager accessor at `:277-293`. |
| `C:\Users\prated\Desktop\FC 25 CT v25.1.6\lua\imports\MemoryManager.lua` | The self-healing offset cache we are going to re-implement (`:159-303`). |
| `C:\Users\prated\Desktop\FC 26 LE v26.3.5\**` | `lua\DOC.MD`, `lua\libs\v2\imports\**`, `lua\scripts\**`, `changelog.txt`, and the **string table of `FCLiveEditor.DLL` at file offsets 0x65063C–0x650EF0**, which is the authoritative list of native Lua globals (DOC.MD is stale — it stops at `SetTransferBudget`, which FC 26 deprecated). |

### 0.1 Corrections to the brief

Three premises in the task brief are slightly off. Stating them up front so the roadmap is honest:

1. **"81 AssemblerScripts (39 real code injection, 42 pure Lua wrappers)"** — the real split is **26 / 12 / 43**:
   - **26** perform actual code injection (call `get_validated_address` → `readBytes` original → `alloc` cave → `jmp`). They consume **28 distinct AOB signatures**.
   - **12** are byte-poke toggles that only write `db 00/01` into a symbol allocated by a *parent* script (e.g. `Free 5/5 Scouts` writes `bFreeScouts: db 1`, scratchpad `aa/012_*.txt`). They contain `{$asm}` but inject nothing.
   - **43** are pure Lua one-liners calling a `helpers.lua` function (e.g. `Heal All Players` → `heal_all_in_player_team()`).
   This matters: **only 26 features are genuinely AOB-expensive**, not 39.
2. **"~42 career manipulator functions in helpers.lua"** — there are 62 functions, of which **26** touch a career manager, **8** are DB lookups and **31** are generic utilities. The "42" is probably the 36-entry `fields_ordered_array` (`consts.lua:7425-7462`).
3. **"~44 AOB signatures"** — **41 are live**, 5 more are commented out (`consts.lua:49, 79, 89-91, 94`).

### 0.2 The single most useful measurement in this document

Bucketing all **918 pointer entries** by which symbol they hang off:

| Anchor symbol | Entries | What that means for the port |
|---|---:|---|
| DB table current-record symbols (`pPlayersTableCurrentRecord` 203, `pTeamsTableCurrentRecord` 105, `pDefaultmentalitiesTableCurrentRecord` 81, `pDefaultteamsheetsTableCurrentRecord` 67, `pLeagueteamlinksTableCurrentRecord` 34, `pManagerTableCurrentRecord` 31, `pUsersTableCurrentRecord` 17, `pTeamplayerlinksTableCurrentRecord` 16, `pCareerPlayercontractTableCurrentRecord` 15, `pEditedplayernamesTableCurrentRecord` 5, `pDcplayernamesTableCurrentRecord` 2) | **576 (63 %)** | **Zero work.** LE's `t3db` (`lua\libs\v2\imports\t3db\table.lua:145,156`) plus the native `EditDBTableField` / `GetDBTableRows` cover all of it, and LE's Player/Team/Manager editors already expose most of it in the GUI. |
| `pModeManagers` | **206 (22 %)** | **PORT VIA MANAGER STRUCT.** All reachable through LE's `GetManagerObjByTypeId` with no AOB scan at all — see §5. This is the unexploited surface. |
| Code-cave data symbols (`arrNeverTiredPlayerIDs` 35, `arr_DevBoostPlayers` 30, `scoutPtr` 30, `arr_YACustomPlayerID` 15, `GameSettingsPtr` 8, `ClubJobOfferStruct` 6, plus 12 scalar singletons) | **136 (15 %)** | Only reachable *after* the parent AOB patch exists. These are the expensive ones. |

**Read that as: 85 % of the cheat table's surface area needs no assembly at all.**

---

## 1. What FC 26 LE gives you natively (the baseline you must not re-implement)

Extracted from `FCLiveEditor.DLL` string table (offsets 0x65063C–0x650EF0) and cross-checked against `lua\libs\v2\imports\**`.

### 1.1 Memory primitives — `lua\libs\v2\imports\core\memory.lua`

| Native global | LE wrapper | Line |
|---|---|---|
| `ReadBytes(addr,count)` → table | `MEMORY:ReadBytes` | `memory.lua:35` |
| `WriteBytes(addr, table)` | `MEMORY:WriteBytes` | `memory.lua:5` |
| `ReadInteger` / `ReadShort` / `ReadFloat` / `ReadQword` / `ReadString` | `MEMORY:ReadInt/ReadShort/ReadFloat/ReadQword/ReadString` | `memory.lua:47-69` |
| `WriteInteger` / `WriteShort` / `WriteFloat` / `WriteQword` / `WriteString` | `MEMORY:WriteInt/...` | `memory.lua:9-27` |
| `WriteJMP(from,to)` | `MEMORY:WriteJMP` | `memory.lua:29-31` |
| `AOBScan(base,size,aob)` | `MEMORY:AOBScanGameModule(aob)` / `AOBScanRegion` | `memory.lua:81-87` |
| `AllocateMemory(addr,size,type,protect)` | `MEMORY:AllocateMemory` — defaults `MEM_COMMIT\|MEM_RESERVE` (12288) + `PAGE_EXECUTE_READWRITE` (64) | `memory.lua:105-115` |
| `DeallocateMemory(addr)` | `MEMORY:DeallocateMemory` | `memory.lua:117` |
| — | `MEMORY:ReadMultilevelPointer(base, offsets)` | `memory.lua:71-79` |
| — | `MEMORY:ResolvePtr(addr, start)` — rel32 → absolute, for `lea`/`mov [rip+x]` | `memory.lua:89-103` |
| `LE_GAME_MODULE_BASE` / `LE_GAME_MODULE_SIZE` / `LE_GAME_MODULE_NAME` | globals | `core\live_editor.lua:59-61` |
| `GetPlugin(djb2_hash)` | service locator, 617 hashes in `imports\services\enums.lua` | `career_mode\helpers.lua:15` |

**What is NOT there** (verified by exhaustive scan of the DLL export/registration name block): no assembler, no `registerSymbol`, no `WriteNOP`, no `WriteCALL`, no detour/hook helper, no `autoAssemble`, no module enumerator besides `LE_GAME_MODULE_*`. Confirms the brief: **all patches must be assembled offline and shipped as byte arrays.**

Also confirmed: `AOBScan` returns the **first** match and no match count and no multi-match list. §4.2 explains why this forces uniqueness proof to move offline. Treat **`result <= 0`** as failure, not `result == 0` — LE's own test asserts `result > 0` (`lua\libs\v2\tests\tests.lua:25-26`), and `GetPlugin` is documented the same way (`career_mode\helpers.lua:16`, `t3db\db.lua:39`). Pattern syntax is CE-style with `??` wildcards.

Two more runtime details that shape the design: `ReadBytes` returns a **1-indexed Lua table**, not a string (`tests\tests.lua:14-16`), and `WriteBytes` takes a table (`t3db\field.lua:114`). `MEMORY:WriteJMP` **discards the native return value** (`memory.lua:29-31`) — so you cannot learn whether the write succeeded from its result, which is precisely why §4.6 mandates read-back verification.

### 1.2 Career manager access — `lua\libs\v2\imports\career_mode\helpers.lua:7-35`

```lua
function GetManagerObjByTypeId(type_id)
    local comm_impl = GetPlugin(ENUM_djb2FeFceGMCommServiceInterface_CLSS)  -- 0x1297F047
    if (comm_impl <= 0)  then return 0 end
    local mode_managers = MEMORY:ReadMultilevelPointer(comm_impl, {0x20, 0x10})
    local mode_manager  = mode_managers + (0x20 * type_id)
    if (MEMORY:ReadInt(mode_manager + 0x10) ~= 1) then return 0 end       -- instance count
    local mode_manager_type = MEMORY:ReadPointer(mode_manager + 0x8)
    if (MEMORY:ReadInt(mode_manager_type + 0x10) ~= 1) then return 0 end  -- type count
    return MEMORY:ReadMultilevelPointer(mode_manager, {0x18, 0x0})
end
```

**155 manager/interface type-ids** are enumerated in `lua\libs\v2\imports\career_mode\enums.lua:279-433` (`ENUM_INoType=0` … `ENUM_ICommonFCEDataManager=154`), of which **135** carry the `ENUM_FCEGameModesFCECareerMode*` prefix.

What has Lua code today:

- **One** wrapper class — `FCECareerModeUserManager` (`career_mode\FCECareerModeUserManager.lua`, type 131).
- **Two** free functions using a manager inline — `SetSquadRole` on `PlayerStatusManager` (89) at `career_mode\helpers.lua:37-67`, and `GetCurrentDate` on `CalendarManager` (26) at `other\helpers.lua:15-21`.
- **One** shipped script — `TransferManager` (129) at `lua\scripts\export_transfer_history.lua:478`.

`lua\libs\v2\imports\career_mode\managers.lua` is a **0-byte file** — the intended aggregate module was never written. That empty file is, in effect, the slot this port fills.

**Four managers touched out of 155. This is the largest unexploited surface in the product.**

### 1.3 DB access — `lua\libs\v2\imports\t3db\`

`LE.db:GetTable(name)` (`t3db\db.lua:82`) → `TABLE:GetFirstRecord()` / `GetNextValidRecord()` (`table.lua:109,127`) → `GetRecordFieldValue(rec, field)` / `SetRecordFieldValue(rec, field, v)` (`table.lua:145,156`). Field-level typed access in `t3db\field.lua:61-130` (`GetInt/SetInt/GetFloat/SetFloat/GetString/SetString`, dispatched by type code: `0`=string, `3`=int, `4`=float at `field.lua:118-140`). Native alternatives: `GetDBTablesNames`, `GetDBTableFields`, `GetDBTableRows`, `InsertDBTableRow`, `DeleteDBTableRowByAddr`, `EditDBTableField`, `GetDBMeta`, `DBFilter`, **`ExecuteSQL`**.

Three things to know before you use it:

- **`LE.db:Load()` is never called during LE init** (`core\live_editor.lua:55-64`), so `LE.db.tables` and `LE.db.tables_count` are empty until you call it yourself. `GetTable(name)` works anyway — but it performs a **linear scan of every DB and every table on each call** (`db.lua:82-108`). Cache the `TABLE` object; do not call `GetTable` inside a loop.
- **`ExecuteSQL` is registered but undocumented and used nowhere** in the shipped Lua tree. Worth a 30-minute spike — if it works, several "bulk edit" ports collapse to one statement.
- `GetNextValidRecord()` returns **`0`** when exhausted, hence the `while rec > 0 do` idiom (`table.lua:127-139`). `FIELD:SetInt` **does no range validation** and always returns `true` (`field.lua:69-89`) — clamp before writing.

### 1.4 Player development (the growth-revert fix) — mostly solved, one thing to verify

- `PlayerHasDevelopementPlan(playerid)` / `PlayerSetValueInDevelopementPlan(playerid, field_name, value)` — `lua\DOC.MD:488-547`. Dev-plan data has **higher priority than the `players` table**, which is exactly why editing `players` alone doesn't stick for your own squad.

  > **⚠ Verify before building on it.** DOC.MD's prose says *"Use this to set corresponding XP points for given field"* (`:524`) — i.e. the caller passes raw XP — but its own example passes `99` for `"composure"` (`:544`), which is plainly a rating. One of the two is wrong. **Run a one-line probe on a live save before Tier 1**: set a known attribute to 99, read it back in-game, and see whether you get 99 or a value derived from 99 XP. If it wants raw XP, port `PlayerGrowthManager_Data.xp_to_attribute` / `xp_to_star` (`consts.lua:7463-7571`) and `get_xp_to_apply_in_player_growth_system` (`helpers.lua:1017-1039`) as a pure Python/Lua lookup — no memory access needed either way.
  >
  > Also: DOC.MD's example calls `ReloadPlayersManager()` first. **That function does not exist in v26.3.5.** Drop it.

- `PlayerDevelopmentManagerAddPlayer(playerid, xp_multiplier, bonus_xp, no_decline)` — `lua\libs\v2\imports\player_development\player_development_manager.lua:24`, persisted to `<LE_DATA>\extensions\careers\<SAVE_ID>\players_development.json` (`:13-21`). Four positional args, no table, no other keys — that is the entire surface. This is the native replacement for the CT's `PGMApplyXp` AOB patch. Native guards raise `"This function can be executed only in career mode"` and `"Player Development must be loaded first before you call Save function"` — so the call order is **`Load()` → `AddPlayer()` → `Save()`**.

### 1.5 Event hooks

`AddEventHandler(name, fn)` / `RemoveEventHandler` / `GetEventHandlers` / `ClearEventHandlersForEvent`. The DLL fires **exactly four** event names (verified by regex over the DLL binary):

```
pre__CareerModeEvent   post__CareerModeEvent   pre__LEInitDoneEvent   post__LEInitDoneEvent
```

`AddEventHandler` does **not** validate the name — registering an invented name succeeds silently and never fires. (The companion bridge already learned this the hard way; see the comment at `lua\scripts\00_le_companion_bridge.lua:307-312`.) Duplicate registration of the same function is a no-op. Removal is **by handler id**, from `GetEventHandlers(name)[i].id`.

Handler signature for career events is `(events_manager, event_id, event)` — all raw pointers/ints. `event` can be read *and written* with `MEMORY:ReadInt/WriteInt` (`tests\event_handlers_test.lua:3-20` mutates both a `pre__` argument and a `post__` result). That mutate-the-argument trick is the closest thing to a hook this API offers, and it covers several cases you'd otherwise need a code cave for.

> **⚠ The two career-event-ID tables disagree with each other.** `career_mode\consts.lua` (used by `GetCMEventNameByID`) and `career_mode\enums.lua` (`ENUM_CM_EVENT_MSG_*`, used by scripts for comparisons) drift apart from id 34 onward — `consts.lua:36` says `[34]="STAGE_STARTED"` while `enums.lua:35-36` says `UNKNOWN_MESSAGE_34=34, STAGE_STARTED=35`. Terminals differ too (`consts.lua:290` `[288]` vs `enums.lua:276` `=275`), and `consts.lua:125-137` has a 13-entry `INTERNATIONAL_JOB_*` block `enums.lua` lacks entirely.
>
> The ids the shipped auto-scripts rely on (`DAY_PASSED=15`, `POST_LOAD_PREPARE=29`) happen to agree. **`ABOUT_TO_ENTER_PREMATCH` does not.** Before shipping any event-driven feature, confirm the id empirically with `lua\scripts\track_cm_events.lua` against the running build. Do not trust either table on faith.

The "reapply after the game resets it" pattern is demonstrated in `lua\scripts\pap_all_playstyles.lua:49-68`. **This replaces roughly half of what the CE table needed code injection for** — instead of hooking the game's writer, hook the career event that follows it and re-write the value.

### 1.6 Other native surface

`SetPlayerForm/Morale/Sharpness/Fitness`, `GetUserTransferBudget`/`SetUserTransferBudget`/`GetCPUTransferBudget`/`SetCPUTransferBudget`, `CreatePlayer`/`DeletePlayer`/`PlayerExists`, `TransferPlayer`/`LoanPlayer`/`TerminateLoan`/`ReleasePlayerFromTeam`/`DeletePresignedContract`, transfer/loan list functions, transfer bans (`cAddTransferBan`/`cRemoveTransferBan`/`cGetTransferBans`/`cSaveTransferBans` via `core\managers\transfer_ban_manager.lua`), `GameplayAttribulator*` (`gameplay\gp_attribulator_manager.lua`), `Aardvark*`, `GetGameLocString`/`SetGameLocString`, `SendHTTPRequest`, `PlayerCapture*`, `SaveVPRO`, `AddCustomGameID`, `GetSaveUID`, `IsInCM`.

**Utilities the CT ships that LE already has — do not port these:**

| CT | LE equivalent |
|---|---|
| `days_to_date` / `date_to_days` (`helpers.lua:1117-1144`) | `DATE:FromGregorianDays` / `ToGregorianDays` (`imports\core\date.lua:48-67`), plus `FromInt`/`ToInt`/`ToString` (`:25-44`) |
| `POS_TO_NAME` (`consts.lua:7613`) | `CONST__PLAYER_PRIMARY_POS_NAME` (`imports\other\consts.lua:1-30`) |
| Trait bit tables (CE tree lines 116-250) | `ENUM_PLAYSTYLE1_*` / `ENUM_PLAYSTYLE2_*` (`imports\other\playstyles_enum.lua:3-47`) |
| `calculate_age` (`helpers.lua:1160`) | `CalculatePlayerAge` (`imports\other\helpers.lua:24-31`) |
| `get_playerids_for_team` (`helpers.lua:227`) | `GetPlayerIDSForTeam` (`imports\other\helpers.lua:34-53`) |
| `split`, `deepcopy` | `imports\core\common.lua:1-13` |

**Known-broken LE code to route around:**

| Landmine | Location |
|---|---|
| `PLAYERS_MANAGER:GetPlayerName(playerid)` returns the literal string `"GetPlayerName"` | `core\managers\players_manager.lua:19-21` — use the native `GetPlayerName` instead |
| `TRANSFER_BAN_MANAGER:RemovePlayer` passes `ENUM_TRANSFER_BAN_TEAM` (0) where it needs `_PLAYER` (1) | `core\managers\transfer_ban_manager.lua:28-30` — call `cRemoveTransferBan(pid, 1)` directly |
| `FCECareerModeUserManager:GetPAPID()` has no null guard, unlike its siblings | `FCECareerModeUserManager.lua:42-44` — check `GetAddr() ~= 0` yourself |
| `GameplayAttribulatorLoadFromFile` is registered but the string `"Not implemented"` sits at its impl site | no Lua wrapper exists; don't rely on it |
| `imports\core\player.lua` is a 17-line unused stub | ignore |

### 1.7 LE C++ features with no Lua binding — ask before you patch

These exist inside `FCLiveEditor.DLL` as native code driven only by LE's own UI. They are **not** callable from Lua today:

| Internal | Relevance |
|---|---|
| Youth-academy report generation | **Directly replaces the CT's `GenNewYAReport` + `fnGenYAReport` double-signature patch (§3 Tier 4).** Requesting a Lua binding from xAranaktu is dramatically cheaper and safer than hand-assembling a call into a game function |
| `LE::CareerModeManager::PAPSetTargetTeam` | Would cover the one Play-As-Player gap in §2.K |
| `[TransferAIManager]` manager-transfer / firing system | Overlaps the job-offer theme |
| `GetSeasonEndDate`, `GetManagerWage`, endless-career contract override, match fixing / fixture rescheduling, staff capacity | Adjacent to Tier-2 objectives and finance work |

**Before committing to any Tier-3 AOB, check whether LE already implements it natively and just hasn't exposed it.** A feature request is a one-line diff on their side and zero maintenance on yours; an AOB signature is a permanent tax.

---

## 2. Complete inventory of CE table features, by theme

Legend for **Verdict**:

| Code | Meaning |
|---|---|
| **LE** | ALREADY IN LE — skip, changelog entry cited |
| **DB** | PORT VIA DB WRITE — easy, no memory patching |
| **MGR** | PORT VIA MANAGER STRUCT — needs `GetManagerObjByTypeId` + FC 26 offset re-derivation |
| **AOB** | PORT VIA AOB PATCH — needs a new FC 26 signature + hand-assembled bytes |
| **EVT** | PORT VIA EVENT HOOK — an AOB feature in CE that becomes a `post__CareerModeEvent` re-write in LE |
| **NO** | NOT PORTABLE |

Scratchpad references of the form `aa/NNN_*.txt` are the extracted `AssemblerScript` bodies (regenerate with the parser described in §4.1).

### A. Gameplay / in-match

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Match score override (home/away) | `aa/070`, AOB `MatchScore` (`consts.lua:56`), pointers `ptrHomeTeamScore/ptrAwayTeamScore +0x226B0` | Set the scoreline mid-match | AOB: cave captures the team-stats base pointer into a symbol, then a plain pointer write | **LE** — changelog v26.2.0 "Gameplay (… match time & score)" |
| Match timer: read / freeze / reset / end half | `aa/071-074`, AOB `MatchTimer` (`consts.lua:57`), `ptrMatchTime+0x24` (float) | Skip to half-time, freeze the clock | AOB pointer-capture + float write | **LE** — v26.2.0 |
| Unlimited substitutions (user, 9 at once) | `aa/075`, AOB `UnlimitedSubstitutions` (`consts.lua:61`) | Sub 9 players | AOB: returns a constant instead of the per-team sub counter | **LE** — v26.2.0 "unlimited subs" |
| Disable substitutions (CPU home/away) | `aa/070`+`aa/076`,`aa/079`, AOB `DisableSubstitutions` (`consts.lua:58`) | Stop the AI subbing | AOB + `bHomeTeam_NoSubs`/`bAwayTeam_NoSubs` byte flags | **AOB** — LE covers unlimited-subs but not CPU-suppression |
| Never-tired / always-tired players, per team and per 35-player ID list | `aa/070`,`077`,`078`,`080`,`081`, AOB `IngameStamina` (`consts.lua:59`), `arrNeverTiredPlayerIDs` (35 slots) | Stamina never drains (or drains instantly for the opponent) | AOB: cave loops the player-ID array, forces the stamina field | **LE** (partly) — v26.2.0 "never tired players" + v26.3.5 bulk-edit "Never Tired". The **per-player ID list and the opponent-side "always tired"** are not in LE → **AOB** |
| Never-injured / always-injured in match | same cave (`bHomeTeam_NeverInjured` etc.) | No in-match injuries | AOB flags in the `IngameStamina` cave | **AOB** — not in LE |
| Force 5-star skill moves in match | same cave (`bHomeTeam_FiveStars`) | Everyone gets 5* SM for the match | AOB flag | **AOB** — low value, skip |
| Side Changer: CPU vs CPU / control home / control away / controller ID | `aa/066-069`, AOB `SideManipulator` (`consts.lua:55`) | Watch CPU vs CPU, or switch which side you control mid-match | AOB: rewrites the controller-assignment table from 4 symbols | **LE** — v26.2.0 "CPU vs CPU" |

### B. Match settings

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Change stadium | `aa/060`, AOB `ChangeStadium` (`consts.lua:83`), symbol `StadiumID` | Play any fixture in any stadium | AOB: overrides the stadium ID as it's read | **LE** — v26.1.5 "Match Setup: Override Stadium" |
| Time of day | `aa/061`, AOBs `MatchTODReal` + `MatchTODDisplay` (`consts.lua:86-87`) | Night matches on demand | Two AOB patches (engine value + HUD value must agree) | **LE** — v26.1.5 "Fixture Time" |
| Weather | `aa/062`, AOB `MatchWeather` (`consts.lua:85`) | Force rain/snow | AOB override | **LE** — v26.1.5 "Weather" |
| Crowd attendance | — (not in CT) | — | — | **LE**-only feature, v26.1.5 |

### C. Career finance

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Transfer budget | tree line 635: `pModeManagers` offsets `E50,0,858,0` | Unlimited transfer kitty | Manager-struct pointer write (FinanceManager region) | **LE** — v26.1.2 "Team Editor → Transfer Budget"; native `SetUserTransferBudget`/`SetCPUTransferBudget`. Already wrapped in `src\career_ops.py:170` |
| Wage budget / club balance / sponsorship / board investment | **not exposed in the CT** | — | — | **MGR** — `FinanceManager` (45), `TcmFinanceManager` (67), `BudgetManager` (25) are all reachable and completely unexplored by both tools. See §7 |

### D. Transfers & negotiation

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Allow transfer approach (buy "unwilling to relocate" players) | `aa/063`, AOB `AllowTransferApp` (`consts.lua:64`) | Approach anyone | AOB: `mov eax,0 / ret` — stub out the eligibility check | **LE** — v26.2.5 "Always allow transfer & loan approach" |
| Allow loan approach | `aa/064`, AOB `AllowLoanApp` (`consts.lua:65`) | Loan anyone | same stub pattern | **LE** — v26.2.5 |
| Global Transfer Network: reveal player data without scouting | `aa/059`, AOB `GTNRevealPlayerData` (`consts.lua:45`) | See true OVR/POT of any player in the GTN | AOB: forces the "is scouted" field | **LE** — v26.1.4 "Reveal Player Data"; manager `PlayerDataRevealManager` (80) also exists |
| Transfer/loan history introspection (offers, requests, actions, accept/reject flags for user-club, user-player, CPU-club, CPU-player, transfer and loan) | `consts.lua:7145-7297` — 11 struct tables hanging off `TransferManager` (129) `+0x1A70` → `NegotiationsStorageDaoImpl` | See and rewrite every pending negotiation | Manager-struct + EASTL vector walks | **MGR** — LE has read-only `export_transfer_history.lua` (`lua\scripts\export_transfer_history.lua:478`) and transfer *history* columns (v26.3.5). **Live negotiation editing is not in LE.** High value, see §7 |
| Transfer list / loan list / transfer bans | CT: DB writes on `players`/`career_playercontract` | — | DB | **LE** — v26.2.2 "Is Transfer/Loan Listed", v26.1.2 "Transfer Bans", native `cAddTransferBan` |

### E. Contracts

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Disable negotiation status checks (renegotiate any time) | `aa/065`, AOB `NegStatusCheck` (`consts.lua:75`) | Re-open a contract talk that just failed | AOB: `call qword ptr [rax+140] / xor eax,eax` — keep the call, discard the status | **LE** — v26.2.5 "Disable negotiation status check" |
| Release clause read/write | AOB `EditReleaseClause` (`consts.lua:63`) + `PLAYERRLC_STRUCT` (`consts.lua:7400-7407`, `_start=0x198`, `size=0xC`) on `PlayerContractManager` (79); lookup at `helpers.lua:657-685` | Set/remove a release clause | Manager-struct linear scan | **LE** — v26.1.2 "Player Editor → Release Clause", v26.3.5 bulk-edit "Release Clause" |
| `career_playercontract` table fields (15 pointer entries) | tree line 468 | Wage, length, squad role, etc. | DB | **LE / DB** — reachable via `LE.db:GetTable("career_playercontract")` |
| Contract-termination management | `ContractTerminationManager` (32) — **not in the CT** | — | — | **MGR** — unexplored by both |

### F. Training & development

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Players Development: XP multiplier, bonus XP, 30-player whitelist | `aa/008`, AOB `PGMApplyXp` (`consts.lua:54`); symbols `fTrainingMultiplier`, `intBonusXP`, `arr_DevBoostPlayers` (30 slots) | Grow your wonderkids 10× faster | **The most complex patch in the table.** The cave intercepts `add [r12],eax`, reads `[rbp+0x80]` for the player ID, walks the whitelist, then `mulss xmm0,[fTrainingMultiplier]` and `add eax,[intBonusXP]` | **LE** — `PlayerDevelopmentManagerAddPlayer(playerid, xp_multiplier, bonus_xp, no_decline)` (`player_development_manager.lua:24`) + v26.3.5 bulk-edit "Player Development Exp Bonus / Exp Multiplier / No Decline" + v26.2.1 "Development exp boost". **LE's version is strictly better — it persists to JSON and survives reload.** |
| Created-player (Player Career) grade XP | `aa/009`, AOB `CreatedPlayerTrainingXP` (`consts.lua:72`) | Max out training-drill grades | AOB override of the XP awarded per drill | **AOB** — not in LE. Moderate value for Player Career users |
| XP↔attribute mirror (so DB edits stick) | `helpers.lua:969-1039` + `PlayerGrowthManager_Data` (`consts.lua:7424-7571`) + writer at `lua\GUI\forms\playerseditorform\manager.lua:183-208` | Attribute edits that don't revert after simming | Manager-struct write into `PlayerGrowthManager` (83), `_start=0x658`, `size=0xAC`, field offset = `index*4` | **LE** — `PlayerSetValueInDevelopementPlan` does the whole thing, including the XP curve. **Do not port the 99-entry table.** |
| Coach-career training manager | `TrainingCoachCareerManager` (127), `TrainingPlayerCareerManager` (128), `TrainingEventsManager` (126) — **not in the CT** | — | — | **MGR** — unexplored |

> **Known trap, inherited from the CT and worth writing down:** `helpers.lua:971,985` caps the growth-system walk at **100 players**. Player #101 silently never gets the XP mirror and their edit reverts on the next sim tick. LE's native call has no such cap, but if you ever hand-roll a growth-system walk, do not copy the cap.

### G. Youth academy

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Generate new scout report on demand | `aa/045`, AOBs `GenNewYAReport` + `fnGenYAReport` (`consts.lua:73-74`) | Skip the wait for youth reports | **AOB + direct in-game function call** — resolves the report-generator function address and calls it from the cave | **AOB** — not in LE. Highest-effort item in the table; the call-a-game-function pattern also needs a calling-convention-correct stub |
| Generate youth players with custom IDs (15 slots) | `aa/046`, AOB `YouthAcademyGeneratePlayer` (`consts.lua:70`), `arr_YACustomPlayerID` | Regen a specific real player into your academy | AOB: substitutes the generated ID from the array | **AOB** — not in LE. Very high player value |
| Reveal youth OVR & POT | `aa/047` → `ya_reveal_data()` (`helpers.lua:574-598`) | See true ratings in scout reports | **Pure Lua** — zeroes 13 potential-variance values at `YouthPlayerUtil` (133) `settings_offset_1=0x18` + `pot_var_off=0x5DC`, raises display ceiling at `max_display_val_offset=0x610` (`consts.lua:7328-7337`) | **MGR** — no AOB needed. **Tier 1 candidate** |
| Multiple scouts in one country | `aa/048`, AOB `YACountryIsBeingScouted` (`consts.lua:52`) | Stack scouts on the best nation | AOB: stub the "country already scouted" check | **AOB** |
| Missions cost nothing | `aa/049` → `ya_free_missions()` (`helpers.lua:602-614`) | Free scouting | **Pure Lua** — zeroes 6 mission-cost tiers at `ScoutManager` (98) `+0x2C` (`SCOUTMANAGER_STRUCT`, `consts.lua:7343-7346`) | **MGR** — trivial. **Tier 1** |
| 15 players per report (up from 5) | `aa/050`, AOB `YAMaxPlayersPerReport` (`consts.lua:50`) | More prospects per report | AOB constant override | **AOB** |
| Force player tier (Bronze…GODLIKE) | `aa/051-056` → `ya_apply_attr_range(tier)` (`helpers.lua:400-488`) | All prospects come out world-class | **Pure Lua bulk** — rewrites 40 ints of the attribute-range table at `YouthPlayerUtil` `settings_offset=0x38` + `attr_range_off=0x13C`, across 5 knowledge levels × 4 attribute classes | **LE** (partially) — v26.1.8/v26.1.4 "force player tier config" with separate GK/field boosts. Verify whether LE covers all 6 tiers; if not, the remainder is **MGR** |
| 100 % 5* weak foot | `aa/057` → `ya_always_best_wf()` (`helpers.lua:541-571`) | Every youth has 5* WF | Pure Lua at `wf_offset=0x34` (5×4 B) | **MGR** — trivial. **Tier 1** |
| 100 % 5* skill moves | `aa/058` → `ya_always_best_sm()` (`helpers.lua:492-538`) | Every youth has 5* SM | Pure Lua at `sm_mod_offset=0x48` (11×4 B) + `sm_offset=0x74` (50×4 B) | **MGR** — trivial. **Tier 1** |
| Age range low/high | tree 1015-1016: `pModeManagers` `28/2C,38,0,1078,0` | Only scout 15-year-olds | Manager-struct write, `YouthPlayerUtil` `settings_offset=0x38 +0x28/+0x2C` | **MGR** |
| Potential range per tier (Bronze/Silver/Gold/Platinum, low+high) | tree 1024-1036: `pModeManagers` `…,18,0,1078,0` | Guarantee 85+ potential | Manager-struct writes at `settings_offset_1=0x18` | **MGR** |
| Min age for promotion | tree 1039: `pModeManagers` `21C,0,C18,0` | Promote 15-year-olds to the senior squad | Manager-struct write (`YouthPlayerManager`, 132) | **MGR** |

> **Youth Academy is the single best theme to port.** 8 of its 12 features are pure manager-struct writes with **zero** assembly, and LE covers almost none of them (only `changelog.txt` v26.1.3 "Youth Academy" section + force-tier). See §3 Tier 1.

### H. Scouting (senior scouts)

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Hire scouts: capture the scout array | `aa/011`, AOB `HireScout` (`consts.lua:49`), symbol `scoutPtr` | Enables the 30 detail pointers below | AOB pointer capture | **AOB** — but see next row |
| Free 5/5 scouts | `aa/012` — `bFreeScouts: db 1` | Every hireable scout is 5-star and costs £0 | Byte flag consumed by the `HireScout` cave | **AOB** (depends on the above). **LE** has the *adjacent* feature: v26.1.4 "Make every scout perfect" (existing scouts). The CT's version affects the *hire screen* candidates |
| Scout detail edit (nationality, experience, judgment, cost, first/last name × 5 scouts) | tree 883-918, `scoutPtr` +0x8/0xC/0x10/0x18/0x28/0x55, stride 0x84 | Hand-pick your scouts | Pointer writes into the captured array | **AOB**-gated. Alternative: reach the same array through `ScoutManager` (98) → **MGR**, which removes the AOB dependency entirely. **Do this instead.** |
| Mission costs | `helpers.lua:602-614` | Free missions | `ScoutManager` `+0x2C` | **MGR** (listed under G) |

### I. Staff / coaches

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Make every coach perfect | `aa/010` → `make_all_coaches_perfect()` (`helpers.lua:308-343`) | All hired coaches 5-star in every discipline + Expert tactical knowledge | **Pure Lua bulk.** `CoachManager` (29); dept vector base `0x2A0`, dept stride `0x20`, coach struct `0x80`, skills at `+0x70/+0x74/+0x78/+0x7C`, tactical knowledge via `writeBytes(readPointer(coach+0x50)+1, 3)`. **All offsets are inline literals with no `consts.lua` entry** — `helpers.lua:312-338` | **LE** — v26.1.4 "Make every coach perfect" (and the CT fixed it in v25.1.3, so it's fragile even there) |
| Per-coach edit: GK/DEF/MID/ATT skills, tactical-knowledge ID+level, wage, first/last name — 4 departments × 5 coaches × 9 fields = **180 entries** | tree 677-880, `pModeManagers` `…+0x80*n, {0x300\|0x2E0\|…}, 0, 3B8, 0` | Fine-grained coach control | Manager-struct pointer writes | **MGR** — LE has a Coaches tab (v26.1.4) but not per-field editing. Medium value |
| Staff manager | `StaffManager` (109) — **not in the CT** | — | — | **MGR** — unexplored |

### J. Board, objectives & job offers

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Clear sack risk | `helpers.lua:296-306` → `clear_sack()` | Never get fired | **Pure Lua.** `BoardManager` (24) `+0x94` = `BoardManager_STRUCT.SACK_BOOL_1` (`consts.lua:7139-7143`). The 4-byte `writeInteger` incidentally clears `SACK_BOOL_2`/`_3` at 0x95/0x96 too | **LE** — v26.2.9 "Manager Editor → Job Security / Unsackable", v26.1.5 "Manager: Unsackable" |
| Fabricate a club job offer (team ID, league ID, table position, W/D/L, wage) | `aa/013`, AOBs `ClubJobOffer` + `ClubJobOfferAlwaysAccept` (`consts.lua:66-67`), symbol `ClubJobOfferStruct` (32 B) | Get hired by any club, any time | **AOB, and a hard one** — the cave writes a 32-byte offer record into `[rbx+0xF8]` and hand-advances the vector `mpEnd` at `[rbx+0x100]`; the second patch forces `eax=100 / ebp=-14` so the board always accepts | **LE** — v26.2.1 "Team Editor → Create Job Offer" + v26.2.9 "Transfer Manager / Fire Manager". **Skip.** |
| Fabricate a national-team job offer + set World Cup / Continental Cup objectives | `aa/014` → `international_job_offer(bool)` (`helpers.lua:349-386`) | Manage a national side | **Pure Lua.** `InternationalsManager` (54): `to_offers=0x28`, `offers_begin=0x180`, `offer_sz=0x3C` (`consts.lua:7126-7130`). Synthesises a 60-byte record and bumps the vector's `mpEnd` | **MGR** — not in LE. **Note the CT bug:** the disable path (`helpers.lua:384`) advances `mpBegin` instead of rewinding `mpEnd`, leaking the allocation base. Fix on port |
| Season objectives | `SeasonObjectiveManager` (100), `ManagerModeSeasonObjectiveManager` (96), `ClubObjectivesManager` (135), `CompetitionObjectivesManager` (134), `ObjectivesHistoryManager` (102) — **not in the CT** | Rewrite "finish top 4" to "don't get relegated" | — | **MGR** — completely unexplored by both tools. See §7 |

### K. Play As Player (Player Career)

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Agent objectives bypass (force contract/transfer status) | `aa/015`, AOB `PAPAgentContractStatusResult` (`consts.lua:80`), symbol `PAPAgentContractFakeStatus` | Move to any club regardless of unmet objectives | AOB: substitute the status result unless the sentinel `0xFFFFFFFF` is set | **LE** — v26.2.1 "Player Career (… Contract Objectives Bypass)" |
| Target team ID / team slot for the agent offer | tree 936-937: `pModeManagers` `90/98,FB0,0,998,0` | Choose your destination club | Manager-struct (`PAPAgentManager`, 77) | **MGR** — verify against LE v26.2.1 coverage first |
| PAP wage & funds | tree 939-940: `pModeManagers` `460/464,0,918,0` | Player-career money | Manager-struct (`PlayAsPlayerManager`, 73) | **LE** — v26.2.1 "Player Career (Funds, Wage…)" |
| Personality: Maverick / Heartbeat / Virtuoso | tree 942-944: `pModeManagers` `14/18/1C,0,958,0` | Max all three personality tracks | Manager-struct (`PAPPersonalityManager`, 75) | **LE** — v26.2.1 "Personality" |
| Unlock all playstyle slots | `aa/016` → `vpro_unlock_playstyle_slots()` (`helpers.lua:1045-1053`) | Equip every playstyle | Pure Lua: `PAPPersonalityManager` count at `+0x100`, array ptr at `+0x58`, stride `0xC`, unlocked flag at slot `+0x7`. **Inline literals, no consts entry** | **LE** — v26.2.1 "Unlock Playstyles Slots"; also `lua\scripts\pap_all_playstyles.lua` |
| Club / nation reputation + squad role | tree 946-951: `pModeManagers` `44c/450/454/455,0,918,0` | Be a club legend on day one | Manager-struct | **LE** — v26.2.6 "Club Reputation & National Team Reputation" |
| 999 skill points | `aa/017`, AOB `VProSkillPoints` (`consts.lua:71`) | Max out the skill tree | AOB: writes `999` to max and `0` to spent | **LE** — v26.2.1 "Attribute Points" |
| Attribute points, playstyle slots for VPRO | — | — | — | **LE** — v26.2.1 |

> **Play As Player is essentially fully covered by LE.** Only the agent target-team fields might be worth checking. Deprioritise this whole theme.

### L. Mass edit (fitness / morale / form / sharpness)

| Feature | CE location | What the player gets | CE mechanism | Verdict |
|---|---|---|---|---|
| Heal all players in your team (fitness 100, injury cleared, fit-dates back-dated) | `aa/019` → `heal_all_in_player_team()` (`helpers.lua:702-727`) | Full squad, no injuries | Pure Lua bulk over `FitnessManager` (47), `PLAYERFITESS_STRUCT` (`consts.lua:7376-7398`), `fitness_start_offset=0x3B48`, `size=0x18`. Claims a free row on sentinel pid `0xFFFFFFFF` | **LE** — v26.3.5 bulk-edit "Heal Player (Full fitness + remove injury)" |
| Refill energy | `aa/020` → `refill_stamina_in_player_team()` | nothing | **Dead code** — the only write is commented out at `helpers.lua:736` | **NO** — don't port a no-op |
| Restore fitness after every training session | `aa/018`, AOB `OnStaminaChange1` (`consts.lua:69`) | Training never tires anyone | AOB on the stamina-change writer | **EVT** — reimplement as `post__CareerModeEvent` → `SetPlayerFitness(pid,100)` for the squad. LE already ships `lua\scripts\auto_max_user_team_fitness.lua:15` doing exactly this. **Zero AOB needed** |
| Mass morale (6 levels incl. Complacent) | `aa/021-026` → `set_players_morale(1..6)` (`helpers.lua:855-880`) | Whole squad euphoric | Pure Lua over `PlayerMoraleManager` (85), `_start=0x518`, `size=0x60`; writes `morale_val=0x2C`, `contract=0x28`, `playtime=0x34` from `values_array={0,20,45,70,90,120}` | **LE** — native `SetPlayerMorale`, `UserTeamSetPlayersMorale` (`career_mode\helpers.lua:125`), `lua\scripts\auto_max_user_team_morale.lua` |
| Mass form (5 levels) | `aa/027-031` → `set_players_form(1..5)` (`helpers.lua:929-964`) | Whole squad in form | Pure Lua over `PlayerFormManager` (82) `+0x130` → vector, `size=0x64`; back-fills the last-10-match ratings so the UI doesn't re-derive | **LE** — native `SetPlayerForm`, `auto_max_user_team_form.lua` |
| Mass sharpness (whole game / your team, 6 preset values) | `aa/033-044` → `set_all_players_sharpness` / `set_sharpness_for_pid` (`helpers.lua:776-818`) | 100 sharpness for everyone | Pure Lua **red-black-tree DFS** over `FitnessManager` `sharpness_start_offset=0x3B98`, node `{next=0x0,prev=0x8,pid=0x20,value=0x24}` | **LE** — native `SetPlayerSharpness`, `UserTeamSetPlayersSharpness`. **Note:** the CT's recursion (`helpers.lua:800-810`) has no visited-set and no depth limit — a stack-overflow risk if the sentinel isn't null. Don't port it |
| Sharpness auto-set on change | `aa/032`, AOB `OnSharpnessChange` (`consts.lua:68`), symbol `byteSharpnessValue` | Sharpness locked at N forever | AOB on the sharpness writer | **EVT** — same treatment as fitness; `lua\scripts\auto_max_user_team_sharpness.lua` is the template |

### M. Editors (DB tables)

| Feature | CE location | Entries | Verdict |
|---|---|---:|---|
| Players table: info, CARD, 34 attributes, traits ×4 banks, positions, roles, names, dates | tree 32-250 | 203 | **LE** — Player Editor since v26.1.0, expanded through v26.3.5 |
| Teams table | tree 277-382 | 105 | **LE** — Team Editor v26.1.0+ |
| `default_mentalities` (tactics presets) | tree 484-565 | 81 | **DB** — reachable via `LE.db:GetTable("default_mentalities")`, **not** in LE's GUI. Low-moderate value |
| `default_teamsheets` | tree 566-633 | 67 | **DB** — same; **not** in LE's GUI |
| `leagueteamlinks` | tree 383-417 | 34 | **DB** / **LE** partial (v26.1.7 League Standings) |
| `manager` table | tree 418-449 | 31 | **LE** — dedicated Manager Editor v26.2.7-v26.3.1 |
| `career_users` | tree 450-467 | 17 | **DB** |
| `teamplayerlinks` | tree 260-276 | 16 | **LE** |
| `career_playercontract` | tree 468-483 | 15 | **DB** |
| `editedplayernames` / `dcplayernames` | tree 251-259 | 7 | **LE** — v26.1.5 "Change Name" |
| Formations (`FORMATIONS_DATA`, `consts.lua:8892-10186`, 30+ formations × 11 positions + x/y offsets) | consts only | — | **LE** — v26.2.2 "Team Editor → Formation Editor" |
| OVR recompute formula (`OVR_FORMULA`, `consts.lua:7651-8406`, per-position attribute weights) | consts only | — | **PORT AS PURE DATA** — no memory access at all. `src\ovr_formula.py` already exists in the companion; cross-check the weights against the CT table |
| Head-type groups (`HEAD_TYPE_GROUPS`, `consts.lua:8421-8886`) | consts only | — | **LE** — v26.1.5 "Generate Miniface", plus `lua\scripts\set_generic_heads.lua` |
| DB table metadata (`DB_TABLE_STRUCT_OFFSETS`, `consts.lua:138-149`; `DB_TABLES_META_MAP`, `:151-789`; `DB_TABLES_META`, `:790-6914`) | consts only | — | **LE** — `t3db` does all of this natively; `GetDBMeta` exists. **Do not port 6000 lines of table metadata.** |

### N. Non-CM / infrastructure

| Feature | CE location | Verdict |
|---|---|---|
| Don't pause on Alt-Tab | `aa/006`, AOB `AltTab` (`consts.lua:42`) | **AOB** — quality-of-life, not in LE |
| Camera settings (height, zoom, far-side focus, ball-tracking speed, penalty-area zoom, pro-camera zoom/speed/swing) | `aa/007`, AOB `GameSettings` (`consts.lua:76`), symbol `GameSettingsPtr`, offsets `2F8..338,58`; plus AOBs `CAM_ROTATE`/`FULL_ANGLE_ROTV`/`CAM_TARGET`/`CAM_Z_BOUNDARY` (`consts.lua:82,83,84,94`) | **AOB** — not in LE, and it's a *settings struct* capture rather than a behaviour change, so it's one of the cheaper AOBs |
| `pModeManagers` resolution | AOB (`consts.lua:43`) + `MemoryManager:get_validated_resolved_ptr("pModeManagers", 3)` (`CheatTableManager.lua:356`) | **NOT NEEDED** — `GetManagerObjByTypeId` + `GetPlugin(0x1297F047)` replaces it with a hash lookup that survives patches |
| `DatabaseService` / `fnGetColData` / `ScreenID` | AOBs (`consts.lua:44,41,45`) | **NOT NEEDED** — `LE.db` and `t3db` replace them |
| `ReleasePlayerMsgBox` / `ReleasePlayerFee` | AOBs (`consts.lua:47-48`) | **LE** — v26.1.4 "Release Player From Team" |
| Speedhack | not in the CT | **LE** — v26.1.0 / v26.1.1 |

---

## 3. Ranked implementation roadmap

Ranking is **value-to-a-career-player ÷ effort**, where effort is:

| Class | Effort | Version fragility |
|---|---|---|
| Pure data (Python only) | ~hours | none |
| DB write | ~hours | very low — field names are stable, `t3db` resolves them by name |
| Event hook | ~hours | very low — 4 event names, stable since v26.1.0 |
| Manager struct | ~1-2 days per manager | **medium** — type-ids are stable across a season, field offsets move on major patches. Detectable and self-healing (§5.3) |
| **AOB patch** | **~1-3 days per signature, plus a re-derivation session on every game patch** | **high** — a `.exe` update can invalidate a signature with no warning, and you have no assembler to iterate with |

> **Be honest with yourself about AOB work.** Each signature is: (1) find the FC 26 instruction that corresponds to the FC 25 one, by function-level reasoning not by pattern search — the FC 25 bytes will not match; (2) prove the new pattern is unique across the whole module; (3) hand-assemble the cave, because there is no assembler; (4) get the instruction-boundary padding right or the game crashes; (5) repeat every patch. Budget 1-3 days *each*, and expect a maintenance tail. A Tier-1 feature that needs no signature is worth more than a Tier-3 feature that needs two.

### Tier 1 — ship first (no assembly, high value, LE doesn't have it)

| # | Feature | Class | Manager | Why now |
|---|---|---|---|---|
| 1 | **Youth Academy tuning pack**: reveal OVR/POT, 5* weak foot, 5* skill moves, age range, per-tier potential range, min promotion age | MGR | `YouthPlayerUtil` (133), `YouthPlayerManager` (132) | 6 features, one manager, one offset-derivation session. LE covers almost none of it. Reference implementations at `helpers.lua:400-598` |
| 2 | **Free scouting missions** | MGR | `ScoutManager` (98) `+0x2C`, 6 ints | 12 lines of Lua. `helpers.lua:602-614` |
| 3 | **Auto-restore fitness / sharpness after training** | EVT | — | Replaces two AOB patches with a `post__CareerModeEvent` handler. LE already ships the template: `lua\scripts\auto_max_user_team_fitness.lua:15` |
| 4 | **OVR recompute on edit** | Pure data | — | `OVR_FORMULA` (`consts.lua:7651-8406`) is a plain table. Cross-check the existing `src\ovr_formula.py` against it and surface "your edit changes OVR from 78 → 84" in the Editor tab before applying |
| 5 | **Manager-struct declarative registry + offset prober** | Infra | — | §5. Everything in Tiers 1-2 depends on it. Build it once, and features become table rows |

### Tier 2 — high value, moderate effort (manager struct, one new manager each)

| # | Feature | Class | Manager | Note |
|---|---|---|---|---|
| 6 | **Live negotiation editor** — see and rewrite pending transfer/loan offers, requests, accept/reject flags | MGR | `TransferManager` (129) `+0x1A70` | 11 struct tables already mapped for FC 25 at `consts.lua:7145-7297`. LE has read-only history only. **This is the single biggest gap.** Big offset-derivation job but zero assembly |
| 7 | **Season-objective rewriting** | MGR | `SeasonObjectiveManager` (100), `ManagerModeSeasonObjectiveManager` (96), `ClubObjectivesManager` (135) | Nobody has touched this — not the CT, not LE. Directly addresses "I got sacked for missing an impossible target" |
| 8 | **Full club finance panel** — wage budget, balance, sponsorship, board investment | MGR | `FinanceManager` (45), `TcmFinanceManager` (67), `BudgetManager` (25) | The CT only ever exposed transfer budget. LE only exposes transfer budget |
| 9 | **National-team job offer** | MGR | `InternationalsManager` (54) | `helpers.lua:349-386`; **fix the `mpBegin`/`mpEnd` bug at `:384` on the way in** |
| 10 | **Per-coach field editing** (4 depts × 5 coaches × 9 fields) | MGR | `CoachManager` (29) | LE has a Coaches tab but not per-field edit. 180 pointer entries mapped in the CE tree |
| 11 | **Scout roster editing via `ScoutManager`** instead of the `HireScout` cave | MGR | `ScoutManager` (98) | Delivers the CT's `scoutPtr` 30-entry feature set **without** the AOB |
| 12 | **Tactics/teamsheet presets** (`default_mentalities` 81 + `default_teamsheets` 67) | DB | — | Pure `LE.db` work, not in LE's GUI |

### Tier 3 — worth it, but each costs a signature

Do these only after §4's infrastructure is proven on **one** signature end to end.

| # | Feature | Signatures needed | Value |
|---|---|---|---|
| 13 | **Youth: generate players with custom IDs** | `YouthAcademyGeneratePlayer` | Very high — "regen Messi into my academy" is the most-requested CT feature |
| 14 | **Youth: 15 players per report** | `YAMaxPlayersPerReport` | High, and it's a one-constant patch — the cheapest possible AOB |
| 15 | **Youth: multiple scouts per country** | `YACountryIsBeingScouted` | Medium; simple check-stub |
| 16 | **Camera settings** | `GameSettings` (+ optionally 4 `CAM_*`) | Medium; pointer-capture only, no behaviour change, low crash risk |
| 17 | **Don't pause on Alt-Tab** | `AltTab` | Medium QoL |
| 18 | **CPU no-subs / never-injured / per-player never-tired** | `DisableSubstitutions`, `IngameStamina` | Medium; `IngameStamina` is a big cave with 10 flags |
| 19 | **Created-player training XP** (Player Career) | `CreatedPlayerTrainingXP` | Medium, niche audience |

### Tier 4 — do not port

| Feature | Why |
|---|---|
| Youth: generate report on demand (`GenNewYAReport` + `fnGenYAReport`) | Needs **two** signatures *and* a calling-convention-correct call into a game function from a hand-assembled cave, with no assembler and no debugger loop. Worst effort-to-value ratio in the table. **And `FCLiveEditor.DLL` already implements youth-academy report generation natively — it just has no Lua binding (§1.7). File a feature request instead.** |
| Club job offer fabrication (`ClubJobOffer` + `ClubJobOfferAlwaysAccept`) | Two signatures, hand-managed EASTL vector surgery inside the cave — and **LE already ships it** (v26.2.1, v26.2.9) |
| Players Development XP patch (`PGMApplyXp`) | **LE's `PlayerDevelopmentManagerAddPlayer` is better** — persists to JSON, survives reload, no signature |
| Everything in §2.K except agent target-team | LE covers Play As Player |
| `DB_TABLES_META` (`consts.lua:790-6914`) | 6 000 lines of table metadata that `t3db` derives at runtime |
| `PlayerGrowthManager_Data.xp_to_attribute` (99 entries) | `PlayerSetValueInDevelopementPlan` does the conversion |
| `refill_stamina_in_player_team` | Dead code in the source (`helpers.lua:736`) |
| Force 5* skill moves in match | Cosmetic, needs a signature |

---

## 4. AOB infrastructure design — CE-style patching from LE Lua without an assembler

This section is the contract between `LE_Profile_Executor` (Python, offline) and the LE Lua job worker (online). It follows the queue protocol in `docs/PROTOCOL.md`: Python writes a `.lua` job, the in-LE worker `load`s and `pcall`s it.

### 4.0 The split

```
  ┌── PYTHON HOST (offline, has full toolchain) ───────────────┐
  │  • parse FC26 .exe as a PE                                 │
  │  • find + PROVE-UNIQUE the signature                       │
  │  • assemble the cave (keystone-engine or a hand table)     │
  │  • compute instruction-boundary padding                    │
  │  • emit patch_manifest.json  { bytes[], relocs[], rva }    │
  └──────────────────────┬─────────────────────────────────────┘
                         │  queue/<job>.lua  (byte arrays inline)
  ┌──────────────────────▼─────────────────────────────────────┐
  │  LE LUA (online, only ReadBytes/WriteBytes/WriteJMP/…)     │
  │  • resolve address (cache → verify → rescan)               │
  │  • AllocateMemory near the site                            │
  │  • apply relocations to the byte array                     │
  │  • WriteBytes cave, save originals, WriteJMP               │
  │  • verify by read-back; roll back on any mismatch          │
  └────────────────────────────────────────────────────────────┘
```

### 4.1 Offline assembly in Python

**Do not write an assembler.** Use `keystone-engine` (`ks = Ks(KS_ARCH_X86, KS_MODE_64)`) at **authoring time**, in a `tools/build_patches.py` step, and commit the resulting byte arrays. The runtime never assembles.

A patch is authored as a template with named holes:

```python
PATCHES["ya_max_per_report"] = Patch(
    aob      = "45 8B 44 CE ??",              # FC26 signature, re-derived
    site_len = 5,                             # bytes covered at the inject point
    cave_asm = """
        mov r8d, 15
        jmp  QWORD PTR [rip+0]          ; RELOC:ret_abs
    """,
    relocs   = [Reloc(kind="abs64", symbol="ret_abs", at=<auto>)],
)
```

The build step emits, per patch:

| Field | Meaning |
|---|---|
| `aob` | the FC 26 signature, `??` wildcards, space-separated hex |
| `rva` | module-relative address of the match, computed from the PE on disk (§4.2) |
| `site_len` | how many bytes the jmp + padding must cover — **whole instructions only** |
| `cave_bytes` | fully assembled cave, with 8 zero bytes at each reloc slot |
| `relocs` | `[{kind: "abs64"|"rel32", slot: <offset in cave_bytes>, target: "site_return"|"data:<name>"|"cave+<n>"}]` |
| `data_syms` | extra allocations the feature needs (`arr_DevBoostPlayers`-style), with sizes |
| `restore_len` | bytes to snapshot before patching (== `site_len`) |

The Lua job carries these as plain tables. No parsing, no assembly, no arithmetic beyond adding a base address.

The **CE-tree extractor** used to produce §2 is worth keeping as `tools/extract_ce_tree.py` — it is ~40 lines of `xml.etree` and it is how you re-audit the next CT release.

### 4.2 Signature derivation and uniqueness — do it against the file, not the process

`AOBScan(base, size, aob)` returns the **first** match and `0` on failure. It cannot tell you there were three matches. The FC 25 CT could — `MemoryManager:AOBScanModule` returns a `foundlist` with `getCount()` and warns on `res_count > 1` (`MemoryManager.lua:185-198`). **You lose that safety net, so you must recover it offline.**

The Python host reads `FC26.exe` from disk and:

1. Maps file offsets ↔ RVAs using the PE section headers (`pefile`, or 60 lines of `struct`).
2. Counts pattern matches across the executable sections. **Reject any pattern with `count != 1`** at build time. Widen the pattern until it is unique.
3. Records the RVA of the unique match.

Consequence: at runtime the *primary* resolution path is `LE_GAME_MODULE_BASE + rva` — a single addition, no scan, no ambiguity. `AOBScan` becomes the **fallback**, used only when byte verification fails. This is both faster and safer than what the CT does.

Store the manifest keyed by game build, mirroring LE's own `le_offsets.json` (which is keyed by `"GAME_VER": "1.0.138.57785"`):

```
LE_Profile_Executor/patches/
  manifest_1.6.3.json      # { game_ver, exe_sha256, patches: {...} }
  manifest_1.6.2.json
```

Also record the **SHA-256 of `FC26.exe`**. That single field turns "the signature didn't match, why?" into "you're on build X, this manifest is for build Y".

### 4.3 The self-healing offset cache

Port `MemoryManager:verify_offset` / `update_offset` / `get_validated_address` (`MemoryManager.lua:159-296`) with the improvements above. The cache file lives beside the manifest and holds **module-relative offsets only** — never absolute addresses, because ASLR moves the module every launch.

```
resolve(name):
  rva = cache[name] or manifest[name].rva
  addr = LE_GAME_MODULE_BASE + rva
  if verify_bytes(addr, manifest[name].aob):        -- ?? wildcards skipped
      return addr
  -- byte mismatch: either a patch moved the code, or we left a jmp behind
  if first_byte(addr) == 0xE9:
      FAIL_LOUD("Patch bytes are still present at <name>. LE was closed without "
                "disabling this feature, or the game was not restarted. "
                "Restart FC 26 (and LE) to clear the leftover jump.")
  addr = AOBScan(LE_GAME_MODULE_BASE, LE_GAME_MODULE_SIZE, manifest[name].aob)
  if addr <= 0:                                   -- not `== 0`; see §1.1
      FAIL_SAFE("Signature <name> not found in game build <ver>. "
                "Feature disabled. Nothing was written.")
  cache[name] = addr - LE_GAME_MODULE_BASE
  save_cache()
  return addr
```

**Byte verification with `??` skipping** is a direct port of `MemoryManager.lua:208-246`:

```lua
local function verify_bytes(addr, aob)
    local toks = {}
    for t in string.gmatch(aob, "%S+") do toks[#toks+1] = t end
    local mem = ReadBytes(addr, #toks)
    if not mem then return false end
    for i = 1, #toks do
        if toks[i] ~= "??" and toks[i] ~= "?" then
            if tonumber(toks[i], 16) ~= mem[i] then return false, mem end
        end
    end
    return true
end
```

**The leftover-`0xE9` detection is not optional.** The CT hits this constantly (`MemoryManager.lua:237-239`) because CE can be closed with scripts still active. The companion has the same exposure: a crash between `WriteJMP` and the disable path leaves a jump into a cave that no longer exists, and the *next* session's verification sees `E9` where it expected the original opcode. Detecting it and printing the exact user action ("restart the game") is worth more than any amount of clever recovery.

### 4.4 Save-and-restore so disable really reverts

The CT pattern (`aa/063_*.txt`):

```
[ENABLE]  ORG = readBytes(INJECT, 12, true)   ...   jmp newmem
[DISABLE] writeBytes(INJECT, ORG)             ...   dealloc(newmem)
```

Port it, but **persist the originals to disk**, not just to a Lua local, because the companion's Lua job worker is stateless between jobs:

```
<LE_DATA_PATH>/companion/patch_state.json
{
  "game_ver": "1.6.3",
  "exe_sha256": "…",
  "active": {
    "ya_max_per_report": {
      "rva": 4587234,
      "orig_bytes": [69,139,68,206,96,...],
      "cave_addr": 140712345600000,
      "data_addrs": {"arr_ids": 140712345602048},
      "applied_at": 1753500000
    }
  }
}
```

Disable order matters — **restore the code page first, then free the cave**, never the reverse:

```
1. WriteBytes(site, orig_bytes)
2. read back and confirm it matches orig_bytes   -- if not, STOP and report; do NOT free
3. DeallocateMemory(cave_addr); DeallocateMemory(each data_addr)
4. remove the entry from patch_state.json
```

Freeing first leaves a live jump into unmapped memory for the duration of the window, which is a guaranteed crash if the game executes that path.

On startup, for each entry still in `active`: if `exe_sha256` differs from the running build, **do not attempt restore** (the RVA means nothing now) — clear the state file and warn. If it matches, verify the site currently holds a `0xE9`; if it does, the previous session died mid-flight and restoration is safe and correct.

### 4.5 The ±2 GB rel32 constraint

`WriteJMP` emits `E9 <rel32>`, so `cave - (site + 5)` must fit in a signed 32-bit int: the cave must be within ±2 GB of the inject site.

`AllocateMemory(addr, size, type, protect)` (`memory.lua:105-115`) passes `addr` straight to `VirtualAlloc` as a **hint**, and `VirtualAlloc` rounds hints down to the 64 KB allocation granularity and fails if the region is taken. So the mitigation is a **search loop, not a single call**:

```lua
local function alloc_near(site, size)
    local GRAN, SPAN = 0x10000, 0x7FF00000     -- 64KB, ~2GB with headroom
    -- try the page containing the site first, then walk outward in both directions
    for delta = 0, SPAN, GRAN do
        for _, cand in ipairs({ site - delta, site + delta }) do
            if cand > 0x10000 then
                local p = MEMORY:AllocateMemory(cand & ~(GRAN-1), size)
                if p and p ~= 0 then
                    if math.abs(p - site) < SPAN then return p end
                    MEMORY:DeallocateMemory(p)          -- too far, give it back
                end
            end
        end
    end
    return 0
end
```

Walk outward from the site rather than scanning from zero — the game module's neighbourhood usually has free 64 KB holes, and you will normally succeed within a few megabytes.

**If `alloc_near` fails**, you have exactly two options and one of them is usually wrong:

| Option | Requires | Verdict |
|---|---|---|
| **14-byte absolute jump** — `FF 25 00 00 00 00 <qword target>` written with `WriteBytes` instead of `WriteJMP` | **≥14 clobberable bytes** of whole instructions at the site, and `WriteBytes` must handle page protection (verify by read-back — see §4.6) | Use when the site allows it. Author `site_len >= 14` variants offline for the patches where the instruction stream permits |
| Two-stage trampoline via a nearby scratch region | A writable+executable region already inside ±2 GB | You don't have one; LE gives you no way to find one. **Don't** |

**If neither works, fail the feature.** Report "could not place a code cave within 2 GB of the patch site — this feature is unavailable on this run" and write nothing. A half-applied patch is worse than an unavailable feature.

### 4.6 Instruction-boundary padding, and the protection question

`WriteJMP` writes 5 bytes. If the instruction(s) at the site total 7 bytes, the trailing 2 bytes are now the tail of a dead instruction and **will be executed as garbage** when control returns. The CT handles this by explicitly padding with `nop` in the `{$asm}` block — see `aa/065_*.txt` (`jmp newmem_NegStatusCheck` + one `nop`) and `aa/017_*.txt` (six `nop`s).

You have no assembler, so `site_len` is computed **offline** by disassembling from the match address (`capstone`, in the same `tools/build_patches.py` step) and summing instruction lengths until you reach ≥5. Ship `site_len` and a `pad_bytes` array of `0x90`s.

**Protection uncertainty.** `WriteJMP` is documented as handling page protection. Whether `WriteBytes` does is **not** documented and must not be assumed. Use this order and always verify:

```
1. try WriteBytes(site, jmp5 .. pads)          -- one write, simplest
2. read back site_len bytes; if it matches, done
3. else: WriteJMP(site, cave)                  -- known to handle protection
4. WriteBytes(site+5, pads)                    -- page may now be writable
5. read back; if it still doesn't match  →  restore orig_bytes, FAIL the feature
```

Step 5 is the point of the whole thing: **never leave the site in a state you did not verify by reading it back.**

### 4.7 Relocation application in Lua

The only arithmetic the Lua side does:

```lua
local function apply_relocs(bytes, relocs, cave, site, site_len, data)
    for _, r in ipairs(relocs) do
        local target
        if     r.target == "site_return" then target = site + site_len
        elseif r.target:sub(1,5) == "data:" then target = data[r.target:sub(6)]
        else   target = cave + tonumber(r.target:match("cave%+(%d+)"))
        end
        if r.kind == "abs64" then
            for i = 0, 7 do bytes[r.slot + i + 1] = (target >> (8*i)) & 0xFF end
        else -- rel32, relative to the end of the field
            local rel = target - (cave + r.slot + 4)
            assert(rel >= -0x80000000 and rel <= 0x7FFFFFFF, "rel32 out of range")
            for i = 0, 3 do bytes[r.slot + i + 1] = (rel >> (8*i)) & 0xFF end
        end
    end
    return bytes
end
```

Prefer `abs64` for the return jump (`FF 25 00 00 00 00 <qword>` inside the cave) — the cave is `PAGE_EXECUTE_READWRITE` and has room, so there is no reason to risk a rel32 there.

### 4.8 End-to-end apply, in order

```
1. resolve(name)                       → site   (cache → verify → rescan → fail-safe)
2. orig = ReadBytes(site, site_len)    → snapshot BEFORE anything
3. cave = alloc_near(site, cave_size)  → fail-safe if 0
4. for each data_sym: alloc (near not required unless a rel32 references it)
5. bytes = apply_relocs(copy(cave_bytes), relocs, cave, site, site_len, data)
6. WriteBytes(cave, bytes); read back and verify
7. persist {rva, orig, cave, data} to patch_state.json   ← BEFORE the site write
8. write the jmp + pads per §4.6, verify by read-back
9. on ANY failure in 6-8: WriteBytes(site, orig); dealloc everything; clear state; report
```

Step 7 before step 8 is deliberate: if the process dies during step 8, the state file already knows where the cave is and what the original bytes were.

---

## 5. Manager-struct access design — features as data, not code

### 5.1 The key finding: FC 25 offsets convert to FC 26 type-ids arithmetically

FC 25 reaches a manager as `[[[pModeManagers]+0] + MODE_MANAGERS_OFFSETS[name] + 0]` (`helpers.lua:277-293`). FC 26 LE reaches it as `mode_managers + 0x20*type_id`, then `{0x18, 0x0}` (`career_mode\helpers.lua:19,32`).

Those are the same array. The FC 25 constants are `type_id * 0x20 + 0x18` — the `+0x18` **is** LE's final deref offset. So:

```
FC25_offset / 0x20  ==  FC25_type_id  ≈  FC26_type_id
```

Verified against `career_mode\enums.lua`:

| Manager | FC 25 (`consts.lua:7103-7124`) | `/0x20` | FC 26 type-id (`enums.lua`) | drift |
|---|---|---:|---:|---:|
| IFCEInterface | `0x20 + 0x18` | 1 | 1 (`:279`) | 0 |
| TransferIO | `0x240 + 0x18` | 18 | 18 (`:296`) | 0 |
| BoardManager | `0x300 + 0x18` | 24 | 24 (`:303`) | 0 |
| CalendarManager | `0x340 + 0x18` | 26 | 26 (`:305`) | 0 |
| CoachManager | `0x3A0 + 0x18` | 29 | 29 (`:308`) | 0 |
| FinanceManager | `0x5A0 + 0x18` | 45 | 45 (`:324`) | 0 |
| FitnessManager | `0x5E0 + 0x18` | 47 | 47 (`:326`) | 0 |
| InternationalsManager | `0x6C0 + 0x18` | 54 | 54 (`:333`) | 0 |
| TcmFinanceManager | `0x840 + 0x18` | 66 | 67 (`:346`) | +1 |
| PlayAsPlayerManager | `0x900 + 0x18` | 72 | 73 (`:352`) | +1 |
| PAPPersonalityManager | `0x940 + 0x18` | 74 | 75 (`:354`) | +1 |
| PlayerContractManager | `0x9A0 + 0x18` | 77 | 79 (`:358`) | +2 |
| PlayerDataRevealManager | `0x9C0 + 0x18` | 78 | 80 (`:359`) | +2 |
| PlayerFormManager | `0xA00 + 0x18` | 80 | 82 (`:361`) | +2 |
| PlayerGrowthManager | `0xA20 + 0x18` | 81 | 83 (`:362`) | +2 |
| PlayerMoraleManager | `0xA60 + 0x18` | 83 | 85 (`:364`) | +2 |
| PlayerStatusManager | `0xAE0 + 0x18` | 87 | 89 (`:368`) | +2 |
| ScoutManager | `0xC00 + 0x18` | 96 | 98 (`:377`) | +2 |
| TransferManager | `0xFE0 + 0x18` | 127 | 129 (`:408`) | +2 |
| YouthPlayerUtil | `0x1060 + 0x18` | 131 | 133 (`:412`) | +2 |

The drift is monotonic (0 → +1 → +2) because FC 26 inserted managers into an alphabetically-ordered list. **You do not have to guess: `enums.lua:279-433` names all 155 of them.** The table above exists only to prove that the FC 25 *struct-offset tables* (`consts.lua:7126-7422`) describe the same objects and are therefore a legitimate starting hypothesis for FC 26 field offsets.

**This also means every one of the CT's 26 manager functions and 206 manager pointer entries ports with zero AOB work.** `GetManagerObjByTypeId` is anchored on `GetPlugin(0x1297F047)` — a djb2 hash of a class name — which is far more patch-stable than any byte signature.

### 5.2 The declarative registry

Features become rows. Put the table in Lua (shipped in the job body from Python, so the Python side owns the source of truth and can version it):

```lua
-- SPEC := { mgr, path?, kind, ... }
--   mgr   : FC26 type-id (career_mode/enums.lua)
--   path  : extra pointer derefs applied to the manager object before `kind`
--   kind  : "scalar" | "array" | "vector" | "rbtree"
MANAGER_FEATURES = {

  ya_free_missions = {
    mgr = 98,                                   -- ScoutManager
    kind = "array", at = 0x2C, type = "i32", count = 6,
    write = function(i) return 0 end,
    verify = { all_lt = 100000000 },            -- costs must look like money
    label = "Youth scouting missions cost nothing",
  },

  ya_best_weakfoot = {
    mgr = 133,                                  -- YouthPlayerUtil
    path = { 0x38 },                            -- settings_offset
    kind = "array", at = 0x34, type = "i32", count = 5,
    write = function(i) return (i == 5) and 100 or 0 end,
    verify = { sums_to = 100 },                 -- it's a probability distribution
    label = "Youth prospects always 5-star weak foot",
  },

  ya_reveal = {
    mgr = 133,
    path = { 0x18 },                            -- settings_offset_1
    kind = "array", at = 0x5DC, type = "i32", count = 13,
    write = function(i) return 0 end,
    also = { { at = 0x610, type = "i32", write = 99 } },  -- max display value
    label = "Youth scout reports show true OVR & potential",
  },

  board_clear_sack = {
    mgr = 24,                                   -- BoardManager
    kind = "scalar", at = 0x94, type = "i32", write = 0,
    verify = { in_set = {0, 1} },
    confirm = true,
    label = "Clear sack risk",
  },

  squad_morale = {
    mgr = 85,                                   -- PlayerMoraleManager
    kind = "vector", begin = 0x518, stride = 0x60, key = 0x0, max = 1200,
    fields = { morale = 0x2C, contract = 0x28, playtime = 0x34 },
    -- LE has native SetPlayerMorale; this row exists only as the offset-prober
    -- reference so the prober can self-check against a known-good manager.
    label = "(reference) squad morale rows",
  },
}
```

Container kinds and how to walk them (all three appear in the CT):

| kind | Layout | Walk |
|---|---|---|
| `scalar` | value at `mgr + at` | one read/write |
| `array` | `count` elements of `type` at `mgr + at` | indexed |
| `vector` | EASTL: `mpBegin` at `begin`, `mpEnd` at `begin+8`, `mpCapacity` at `begin+16` | `cur = ReadQword(mgr+begin)`, stop at `ReadQword(mgr+begin+8)`, step `stride`. **Always bound by `max`** — the CT does this at `helpers.lua:630, 667, 754, 985`, and LE does the same at `career_mode\helpers.lua:54,57-58`. LE's own convention names it `vec_begin_offset` / `vec_end_offset = begin + 0x8` (`career_mode\helpers.lua:47-51`) — match it so the two codebases read alike |
| `rbtree` | node `{left=0x0, right=0x8, key=0x20, value=0x24}` | **Iterative with an explicit stack and a visited set.** The CT's recursive version (`helpers.lua:800-810`) has neither and will blow the stack if a leaf points at the header sentinel instead of null |

Adding a feature is adding a row plus a UI entry. No new Lua logic.

### 5.3 Re-deriving FC 26 offsets safely

Do **not** trust the FC 25 numbers. Use them as the hypothesis and let a prober confirm or reject.

**Step 0 — prove the manager pointer before you trust any offset.** Two anchors already verified in LE's own code:

- `CalendarManager` (26): day/month/year at `+0x34/+0x38/+0x3C` (`other\helpers.lua:19-21`). If those don't read as a plausible date, `GetManagerObjByTypeId` is not returning what you think.
- `UserManager` (131): `mUserType` at `+0x37`, `mPlayerId` at `+0x3C` (`FCECareerModeUserManager.lua:14-16`).

Ship a **"manager health check"** job that walks all 135 type-ids, reports which return non-zero, and validates those two anchors. Run it once per game build before touching anything else. Its output is also the fastest way to spot a type-id renumber.

**Step 1 — structural probe, not a byte pattern.** For a vector-backed manager, scan `mgr + 0x00 .. mgr + 0x1000` in 8-byte steps for a triple `(b, e, c)` where:

```
b, e, c are all readable pointers          and   b <= e <= c
(e - b) % expected_stride == 0             and   0 < (e-b)/stride <= plausible_count
ReadInt(b + key_off) is a known playerid   (take one from GetUserSeniorTeamPlayerIDs())
```

That last clause is what makes it reliable: you already know a real player ID from `career_mode\helpers.lua:91-111`, so you can confirm you found the *right* vector, not merely *a* vector. Report every candidate with its offset; if exactly one survives, cache it.

For scalar fields, probe by **known value + write-read-write-back**: read the candidate, check it is in the expected domain (`verify` in the registry), write a distinguishable value, read it back, restore. Never leave the probe value in place.

**Step 2 — cache with provenance.**

```json
{
  "game_ver": "1.6.3",
  "exe_sha256": "…",
  "derived_at": "2026-07-26T20:00:00Z",
  "managers": {
    "133": { "settings_offset": 56, "wf_offset": 52, "_method": "probe", "_confidence": "unique-match" },
    "98":  { "base_mission_cost_off": 44, "_method": "fc25-hypothesis-confirmed" }
  }
}
```

`_method` and `_confidence` matter for triage. "It worked last patch" and "the prober found exactly one candidate" are very different levels of trust.

**Step 3 — when a game patch moves them.** The failure ladder, in order:

1. **Detect.** `exe_sha256` changed → mark every cached offset stale; do not write anything using a stale offset.
2. **Re-probe automatically** for every feature that has a structural probe. Most vector-backed features will self-heal with no user action.
3. **Anything that can't self-probe → disable it**, show it greyed out with "needs re-derivation for game build X", and keep working on everything else. **Never fall back to the previous build's offsets.** A stale offset is a write to an unknown field, which is exactly how saves get corrupted.
4. **Report.** One line per stale feature in the job result, so a user's log tells you immediately what a patch broke.

---

## 6. Safety rules

### 6.1 Never port — no exceptions

| Category | Rule | Why |
|---|---|---|
| **Anticheat / EAAC** | Never touch FakeEAAC install/backup/restore, never replace or re-sign `FCLiveEditor.DLL`, never inject into FC 26 by any path the companion owns | `README.md` "Anticheat / inject ownership" and `docs/PROTOCOL.md` already draw this line: **LE `Launcher.exe` owns injection.** The companion runs *inside* LE's Lua, or writes files LE reads. That boundary is the product's safety story — don't cross it for a feature |
| **Online modes** | Career/offline only. No FUT, no Pro Clubs, no Ultimate Team, nothing that touches EA servers | Every feature in §2 is career-mode. Keep it that way |
| **Detection evasion** | No hiding processes, no tampering with integrity checks, no bypassing bot/telemetry detection | Out of scope, and it changes what this tool *is* |
| **Anything with an unverifiable offset** | See §5.3 step 3 | A stale write is silent save corruption |

### 6.2 Requires explicit confirmation before writing

Gate these behind a typed/clicked confirm, showing the exact write:

- Any **AOB patch enable** — it modifies executable code in a live process. Show the feature name, the site RVA, and the byte count.
- **Structural writes**: anything that fabricates a record and hand-adjusts a container's `mpEnd` (national-team job offer, §2.J). Getting this wrong corrupts the heap.
- **Bulk operations over the whole DB** rather than the user's squad — e.g. the CT's `set_all_players_sharpness` touches every player in the save (`helpers.lua:776-780`).
- **Board / objective / finance writes**, because they change the career's difficulty contract irreversibly from the save's point of view.
- **Anything marked `confirm = true`** in the registry (§5.2).

Fitness/morale/form/sharpness on your own squad, DB field edits on a single player, and read-only probes do **not** need a confirm — they're already the companion's normal apply path.

### 6.3 Requires a backup / undo path

The companion already has the machinery: `src\snapshot_store.py`, `src\undo_apply.py`, `snapshots/`, and `--list-snapshots`. Extend it, don't rebuild it.

| Write class | Backup requirement |
|---|---|
| DB field write | Snapshot the affected records **before** the write (already the pattern) |
| Manager-struct scalar/array | Snapshot the raw bytes of the affected range; store alongside the snapshot so undo is a `WriteBytes` |
| Manager-struct container surgery | **Refuse without a save-file backup.** Prompt the user to save the career first — heap surgery is not undoable from a byte snapshot |
| AOB patch | `orig_bytes` in `patch_state.json` (§4.4) **is** the undo. Verify the restore by read-back |
| Anything touching the growth system | Note in the UI that the effect persists in the save file, so undo must run before the next sim tick |

Carry over the CT's own warning verbatim in spirit — it's the first thing in its tree (`FC25.CETRAINER`, tree line 11): *make a backup of your save before you edit anything.*

### 6.4 Failing safe when a signature does not match

The rule: **a failed resolution must produce a disabled feature and a clear message, never a write.**

| Condition | Behaviour |
|---|---|
| `exe_sha256` ≠ manifest | Do not resolve anything from that manifest. "This build (X) has no patch manifest. Memory features are disabled; DB and manager features continue." |
| Cached RVA verifies | Use it. No scan |
| Cached RVA fails, first byte is `0xE9` | **Stop.** "Leftover patch detected at `<name>`. Restart FC 26 to clear it." Do not rescan (you'd find your own cave), do not write |
| Cached RVA fails, `AOBScan` returns 0 | Disable that feature only. Log the signature and the build. Other features unaffected |
| `AOBScan` succeeds but the bytes at the new address still don't verify | Treat as failure — this means the pattern is matching something it shouldn't. Disable |
| `alloc_near` fails | Disable the feature. Write nothing (§4.5) |
| Cave write-back verification fails | Restore `orig_bytes`, dealloc, disable, report |
| Site write-back verification fails | Restore `orig_bytes`, dealloc, disable, report (§4.6 step 5) |
| Manager returns 0 from `GetManagerObjByTypeId` | Not in career mode, or the manager isn't instantiated on this screen. Silent no-op with a log line — this is normal, not an error |
| Offset probe finds 0 or >1 candidates | Disable that feature, keep the rest. Never pick "the first one" |

Two behaviours to explicitly **not** implement, both of which the CT does and both of which are wrong for a background companion:

- `assert(false, ...)` to abort the whole session (`MemoryManager.lua:289`). One bad signature must not take down the queue worker. Every resolution returns a status; the job continues with the features that resolved.
- Silently picking match #1 when there are several (`MemoryManager.lua:192-195`). §4.2 moves uniqueness proof offline precisely so this case cannot arise at runtime.

One more, inherited from `helpers.lua` and worth guarding against in review: **`GetManagerObjByTypeId` returns `0`, not `nil`, and `0` is truthy in Lua.** `if not mgr then return end` is dead code. Always write `if mgr == 0 then return end`. The FC 25 helpers get this wrong in at least eight places (`helpers.lua:621, 658, 742, 824, 892, 932, 974, 1046`), which is why several of its features fail by silently doing nothing instead of reporting.

---

## 7. What the CE table does that FC 26 LE does not — the highest-value ports

Ordered by value ÷ effort. The first five need **no assembly at all**.

| # | Gap | Why it matters to a career player | Cost |
|---|---|---|---|
| 1 | **Youth academy prospect tuning** — reveal true OVR/POT in scout reports, guarantee 5* weak foot and 5* skill moves, set the scouted age range, set per-tier potential floors, drop the promotion age | The youth academy is the most-played long-term career loop and it is almost entirely opaque and RNG-driven. The CT makes it deterministic. LE's changelog covers "Youth Academy" (v26.1.3) and force-tier (v26.1.4/v26.1.8) but not reveal, weak foot, skill moves, age range, or potential range | **MGR**, one manager (`YouthPlayerUtil` 133), six features. Reference: `helpers.lua:400-598` |
| 2 | **Free scouting missions** | Removes the "I can't afford to scout Brazil" wall in the first two seasons | **MGR**, `ScoutManager` (98) `+0x2C`, six ints. `helpers.lua:602-614` |
| 3 | **Live negotiation editing** — inspect and rewrite pending transfer/loan offers, requests, and accept/reject flags for user-club, user-player, CPU-club and CPU-player | "The AI won't sell me anyone" is the single loudest career-mode complaint. LE only *reports* transfer history (v26.1.8, v26.3.5); it can't change a live negotiation | **MGR**, `TransferManager` (129) `+0x1A70`. Eleven structs already mapped for FC 25 at `consts.lua:7145-7297`. Large derivation job, zero assembly |
| 4 | **Season objectives & board expectations** | "Sacked for finishing 5th when the board demanded 4th" ends careers. Neither tool touches it | **MGR**, `SeasonObjectiveManager` (100) / `ManagerModeSeasonObjectiveManager` (96) / `ClubObjectivesManager` (135). Greenfield — no FC 25 reference offsets exist |
| 5 | **Full club finances** — wage budget, balance, sponsorship, board investment | LE and the CT both expose only the transfer budget. Wage budget is the actual constraint on signing anyone good | **MGR**, `FinanceManager` (45), `TcmFinanceManager` (67), `BudgetManager` (25). Greenfield |
| 6 | **National-team job offer** with World Cup / Continental Cup objectives | Managing a country is a whole career mode LE can't currently start | **MGR**, `InternationalsManager` (54). `helpers.lua:349-386`, and fix the `mpBegin`/`mpEnd` bug at `:384` |
| 7 | **Per-coach detailed editing** (4 departments × 5 coaches × 9 fields) | LE has "make every coach perfect" but not "give me one elite GK coach and spend the rest on wages" | **MGR**, `CoachManager` (29). 180 entries mapped in the CE tree at lines 677-880 |
| 8 | **Youth: generate players with custom IDs** | "Regen a specific real player into my academy" — the most-requested single CT feature | **AOB**, one signature (`YouthAcademyGeneratePlayer`). First AOB to attempt, after §4 is proven |
| 9 | **Youth: 15 players per report** | Triples prospect throughput | **AOB**, one signature (`YAMaxPlayersPerReport`), one constant. **The cheapest possible AOB — use it as the pilot for §4** |
| 10 | **Tactics & teamsheet preset editing** (`default_mentalities`, `default_teamsheets`) | 148 pointer entries the CT exposes and LE's GUI doesn't reach at all | **DB**, pure `LE.db` work |
| 11 | **CPU no-subs / never-injured / per-player never-tired list** | Match-level control LE only partially has | **AOB**, two signatures (`DisableSubstitutions`, `IngameStamina`) |
| 12 | **Camera settings & don't-pause-on-Alt-Tab** | Pure QoL, but genuinely absent | **AOB**, two signatures. Low crash risk (pointer capture only) |

### Suggested first sprint

0. **Three probes, an hour each, before any code.** (a) `PlayerSetValueInDevelopementPlan` — XP or rating? (§1.4). (b) `track_cm_events.lua` on a live save — confirm the real career-event ids, since `consts.lua` and `enums.lua` disagree (§1.5). (c) `ExecuteSQL` — does it work? (§1.3). Each answer changes what you build.
1. §5.2 registry + §5.3 prober + the manager health-check job (walk all 155 type-ids, validate the `CalendarManager` and `UserManager` anchors). *(infrastructure)*
2. Gaps **1, 2, 6** — all `YouthPlayerUtil` / `ScoutManager` / `InternationalsManager`. Six to eight user-visible features, no assembly.
3. Fitness/sharpness auto-restore as `post__CareerModeEvent` handlers, replacing two AOB patches with event hooks (§2.L). Template already in the repo: `lua\scripts\auto_max_user_team_fitness.lua`.
4. **Send xAranaktu a feature-request list before writing a single AOB** (§1.7) — youth report generation and `PAPSetTargetTeam` already exist natively in the DLL. Anything he exposes is work you never have to maintain.
5. Only then: §4 infrastructure, piloted on gap **9** (`YAMaxPlayersPerReport`) — one signature, one constant, trivial cave. Prove resolve → verify → alloc-near → patch → verify → disable → restore end to end on the easiest possible target before attempting gap 8.

---

## Appendix A — the 41 live FC 25 AOB signatures and their fate

`consts.lua:40-94`. **All are FC-25-specific and must be re-derived. Treat as a template, not as data.**

| # | Signature | Used by | FC 26 fate |
|---:|---|---|---|
| 1 | `fnGetColData` (`:41`) | DB column reader | **Not needed** — `t3db` |
| 2 | `AltTab` (`:42`) | `aa/006` | **Port** (Tier 3) |
| 3 | `pModeManagers` (`:43`) | manager base | **Not needed** — `GetPlugin(0x1297F047)` |
| 4 | `DatabaseService` (`:44`) | DB base | **Not needed** — `LE.db` |
| 5 | `ScreenID` (`:45`) | screen gating | **Not needed** — `post__CareerModeEvent` |
| 6 | `GTNRevealPlayerData` (`:45`) | `aa/059` | **LE** v26.1.4 |
| 7 | `ReleasePlayerMsgBox` (`:47`) | editor | **LE** v26.1.4 |
| 8 | `ReleasePlayerFee` (`:48`) | editor | **LE** v26.1.4 |
| 9 | `HireScout` (`:49`) | `aa/011-012` | **Replace with `ScoutManager` (98)** — no AOB |
| 10 | `YAMaxPlayersPerReport` (`:50`) | `aa/050` | **Port — pilot signature** |
| 11 | `YACountryIsBeingScouted` (`:51`) | `aa/048` | **Port** (Tier 3) |
| 12 | `SideManipulator` (`:52`) | `aa/066-069` | **LE** v26.2.0 |
| 13 | `PGMApplyXp` (`:54`) | `aa/008` | **LE** — `PlayerDevelopmentManagerAddPlayer` |
| 14 | `MatchScore` (`:56`) | `aa/070` | **LE** v26.2.0 |
| 15 | `MatchTimer` (`:57`) | `aa/071-074` | **LE** v26.2.0 |
| 16 | `DisableSubstitutions` (`:58`) | `aa/070,076,079` | **Port** (Tier 3) |
| 17 | `IngameStamina` (`:59`) | `aa/070,077-081` | **Port** (Tier 3), partly LE |
| 18 | `UnlimitedSubstitutions` (`:61`) | `aa/075` | **LE** v26.2.0 |
| 19 | `EditReleaseClause` (`:63`) | editor | **LE** v26.1.2 |
| 20 | `AllowTransferApp` (`:64`) | `aa/063` | **LE** v26.2.5 |
| 21 | `AllowLoanApp` (`:65`) | `aa/064` | **LE** v26.2.5 |
| 22 | `ClubJobOfferAlwaysAccept` (`:66`) | `aa/013` | **LE** v26.2.1/v26.2.9 |
| 23 | `ClubJobOffer` (`:67`) | `aa/013` | **LE** v26.2.1/v26.2.9 |
| 24 | `OnSharpnessChange` (`:68`) | `aa/032` | **Replace with event hook** |
| 25 | `OnStaminaChange1` (`:69`) | `aa/018` | **Replace with event hook** |
| 26 | `ChangeStadium` (`:83`) | `aa/060` | **LE** v26.1.5 |
| 27 | `MatchWeather` (`:85`) | `aa/062` | **LE** v26.1.5 |
| 28 | `MatchTODDisplay` (`:86`) | `aa/061` | **LE** v26.1.5 |
| 29 | `MatchTODReal` (`:87`) | `aa/061` | **LE** v26.1.5 |
| 30 | `VProSkillPoints` (`:71`) | `aa/017` | **LE** v26.2.1 |
| 31 | `CreatedPlayerTrainingXP` (`:72`) | `aa/009` | **Port** (Tier 3) |
| 32 | `GenNewYAReport` (`:73`) | `aa/045` | **Skip** (Tier 4) |
| 33 | `fnGenYAReport` (`:74`) | `aa/045` | **Skip** (Tier 4) |
| 34 | `YouthAcademyGeneratePlayer` (`:70`) | `aa/046` | **Port** (Tier 3) — highest-value AOB |
| 35 | `NegStatusCheck` (`:75`) | `aa/065` | **LE** v26.2.5 |
| 36 | `GameSettings` (`:76`) | `aa/007` | **Port** (Tier 3) |
| 37 | `PAPAgentContractStatusResult` (`:80`) | `aa/015` | **LE** v26.2.1 |
| 38 | `CAM_ROTATE` (`:82`) | camera | **Port** (Tier 3, optional) |
| 39 | `FULL_ANGLE_ROTV` (`:83`) | camera | **Port** (Tier 3, optional) |
| 40 | `CAM_TARGET` (`:84`) | camera | **Port** (Tier 3, optional) |
| 41 | `CAM_Z_BOUNDARY` (`:94`) | camera | **Port** (Tier 3, optional) |

**Net: of 41 signatures, 17 are already covered by LE, 6 are unnecessary because LE's APIs replace them, 2 become event hooks, 2 are Tier-4 skips, and 14 remain as genuine AOB candidates — of which only 2 (`YAMaxPlayersPerReport`, `YouthAcademyGeneratePlayer`) are Tier-3 priorities.**

## Appendix B — regenerating this analysis

```python
# tools/extract_ce_tree.py — dumps the full CE feature tree with mechanisms
import xml.etree.ElementTree as ET
root = ET.parse(r"...\FC25.CETRAINER").getroot()
def txt(e, t):
    n = e.find(t); return (n.text or "") if n is not None else ""
def walk(e, d=0):
    vt, addr = txt(e, "VariableType").strip(), txt(e, "Address").strip()
    offs = [o.text for o in e.findall("Offsets/Offset")]
    kind = "AA" if vt == "Auto Assembler Script" else (f"PTR {vt} {addr} {','.join(offs)}" if addr else "GROUP")
    print("  " * d + txt(e, "Description").strip().strip('"') + " | " + kind)
    ce = e.find("CheatEntries")
    if ce is not None:
        for c in ce.findall("CheatEntry"): walk(c, d + 1)
for c in root.find("CheatEntries").findall("CheatEntry"): walk(c)
```

`AssemblerScript` bodies extract the same way — write each to `aa/NNN_<path>.txt` and grep for `get_validated_address` to separate real injections (26) from symbol pokes (12) and pure-Lua wrappers (43).

The FC 26 native Lua API list in §1 came from the `FCLiveEditor.DLL` string table; re-derive with a regex for `[A-Za-z_][A-Za-z0-9_]{2,48}\x00` over file offsets 0x65063C–0x650EF0, anchored on known names (`AOBScan`, `WriteJMP`, `IsInCM`). The four event names come from `(pre__|post__)[A-Za-z0-9_]+` over the whole DLL.
