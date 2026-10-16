# FC26 Companion for FC 26 Live Editor

A Windows desktop app for EA SPORTS FC 26 **Career Mode** that works alongside
xAranaktu's [FC 26 Live Editor](https://github.com/xAranaktu/FC-26-Live-Editor)
(LE). Read your live squad, upgrade players from a FUT card library, sign
players, plan several players with AI, heal injuries and prepare the squad
before a match — without the Companion ever touching game memory itself.

Current version: **2.11.7** (protocol 3, resident Lua core 2.6.37).

## How it works

```
FC26_Companion.exe (Python / CustomTkinter)
        │  writes  queue/jobs/<ULID>.json  (+ jobs/index.json)
        ▼
  queue/  ── shared folder ──────────────────────────────────────────────┐
        ▲                                                                │
        │  writes  queue/results/<ULID>.json, session.json, drain.json   │
        │                                                                │
Resident Lua core "le_companion" running inside Live Editor's Lua engine ┘
        │  calls LE Lua APIs (TransferPlayer, SetPlayerFitness, DB tables…)
        ▼
FC 26 (Career Mode save)  ←  FCLiveEditor.DLL injected by LE Launcher.exe
```

- **The Companion never injects.** LE's `Launcher.exe` owns the anti-cheat
  swap and injects `FCLiveEditor.DLL`. The Companion only writes job files.
- The Lua core is installed into LE's data dir
  (`C:\FC 26 Live Editor\lua\scripts\le_companion\`) and auto-arms from
  `lua\autorun\01_le_companion_v2_autorun.lua`. Arming registers event
  handlers only; it never runs jobs while LE is loading.
- Jobs run only on a **safe Career event** (entering the hub or Team
  Management). Advancing days, saving and loading are blacked out. "Queued"
  is therefore a normal state until you return to the hub.
- Jobs are claimed by rename into `claimed/`, retried up to 3 times, and
  moved to `poison/` if they crash a session. A job is reported "applied"
  only when the worker returns verified read-back proof.

Wire format: [docs/PROTOCOL.md](docs/PROTOCOL.md),
[docs/schema/job.schema.json](docs/schema/job.schema.json),
[docs/schema/result.schema.json](docs/schema/result.schema.json).
Design rationale: [companion/DECISIONS.md](companion/DECISIONS.md),
[docs/V2_ARCHITECTURE.md](docs/V2_ARCHITECTURE.md),
[docs/V2_INGAME_CORE.md](docs/V2_INGAME_CORE.md).

## Features

| Page (Ctrl+1…6) | What it does |
|---|---|
| **Club** | Live squad board read from the open save; setup checklist; release a player; signed-player portraits; **Plan these players** (AI proposes builds for several players, you review, then apply with crash-safe recovery and undo). |
| **Player** | One-player workstation: apply a FUT card's stats, edit fields manually, or use AI presets. Review before apply. |
| **Sign** | Search the card library (year menu FC 18–FC 27, collections such as TOTY/TOTW/Icons, Best/Base/All versions, surname search), add cards to a signing bag, check out. Signing converts a vetted low-rated free agent into the card (`add_to_team` op — experimental). |
| **Injuries** | Scan the senior squad and heal one or all injured players (guarded native reader, LE v26.3.5/v26.3.6 only). |
| **Matchday** | **Prepare squad**: fitness / sharpness / form / morale targets, optional injury recovery; remembered routine; duplicate-submit guard. |
| **Library** | Card catalogue search, variants, favourites, "Send to Sign". |
| **Activity drawer** (Ctrl+J) | Queue, History, Plans, Health, and **Assets** (check/export game files, the raw game DB, player portraits as PNG previews). Undo (type `UNDO`). |
| **Command palette** (Ctrl+K) | Jump anywhere. |
| **Settings** | Install/repair the Lua worker, health check, AI provider, UI scale. |

AI features use either a **Codex account** login (`~/.codex/auth.json`) or an
**OpenAI API key** (environment variable or Windows Credential Manager entry
`FC26Companion/OpenAI`).

## Requirements

- Windows, FC 26 (PC) and Live Editor v26.3.5 or v26.3.6, launched with LE's
  `Launcher.exe`.
- Python 3.10+ for development (the shipped build uses 3.13).

## Using the app

1. Start FC 26 with LE's `Launcher.exe` and load your Career save.
2. Open `FC26_Companion.exe`. On first run, use **Settings → Install worker**
   (or `FC26_Companion.exe install`), then restart FC with LE so the core loads.
3. In **Club**, refresh the squad. Make a change, review it, apply.
4. Go to the Career hub or open Team Management so the worker picks up the job.

## Development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app_entry_v2.py            # GUI
python app_entry_v2.py doctor     # CLI (any argument switches to CLI mode)
python -m pytest tests/v2 -q      # headless; GUI tests need COMPANION_ALLOW_GUI_TESTS=1
python build_exe_v2.py            # PyInstaller onedir -> FC26_Companion.exe + _internal_v2/
```

CLI commands (`companion/cli.py`): `version`, `status`, `doctor`, `queue`,
`sweep`, `force-drain`, `ping`, `submit`, `result`, `install`, `migrate`,
`assets {check,export,database,portrait,reports}`, `profiles`, `run-profile`,
`packs`, `run-pack`, `search-cards`, `best-match`, `search-clubs`, `budget`,
`sync-futgg`, `rebuild-catalog`, `gui`.

Card databases (`card_db/*.sqlite`), `profiles.json`, `assets/` and personal
state (`state.sqlite`, logs, queue) are gitignored and not bundled in the
repository; only the FC27 FUT.GG dataset under `card_db/futgg/` is tracked.
A fresh clone therefore cannot build the exe until those files are supplied.

## Repository layout

| Path | Contents |
|---|---|
| `companion/` | **v2 app** (current). `core/` paths, `state.sqlite`, queue transport · `domain/` pure logic (jobs, catalog, planning, signing) · `app/` store + commands · `platform/` Windows / LE install · `integrations/` AI provider · `ui/` CustomTkinter shell and pages. |
| `ingame/le_companion/` | Resident Lua core (runner, events, ops). Installed into LE's `lua/scripts/`. |
| `app_entry_v2.py`, `build_exe_v2.py` | v2 exe entry point and build script. |
| `tests/v2/` | v2 pytest suite (~1,100 tests). |
| `tools/` | Catalog builders, FUT.GG sync, verification and one-off deploy scripts. |
| `docs/` | Architecture, protocol, per-release notes. |
| `main.py`, `src/`, `bridge/`, `web/`, `native/`, `app_entry.py`, `build_exe.py` | **v1 "LE_Profile_Executor"** (legacy). Text-file queue (`_run_now.lua`, `_pending.txt`). Kept working; some v2 code still imports from `src/`. |

## License and project status

The project source is released under the MIT License; see [LICENSE](LICENSE).
FC 26 and Live Editor are third-party products. This community project is
unofficial and is not affiliated with or endorsed by their owners. Please do
not include access tokens, saves, logs or downloaded datasets in issues or
pull requests.
