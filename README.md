# FC26 Companion — Career Studio synergy

Companion for **FC 26 Live Editor**. Dual-mode + **workflow packs**, **SAFE free-agent Add team**, **cross-tab handoffs** (Cards ↔ Add team ↔ Editor ↔ Squad), **batch apply**, turbo queue.

## Primary UI (desktop EXE)

The **primary interactive GUI** is the CustomTkinter desktop app (windowed `.exe` / `python main.py`). Optional browser UI is still available via `--web`.

| Launch | Command |
|--------|---------|
| **Desktop GUI (primary)** | `python main.py` or `python main.py --gui` |
| No flags / double-click | `LE_Profile_Executor.exe` → desktop window |
| Optional web UI | `python main.py --web` → `http://127.0.0.1:8765/` |
| Headless API only | `python main.py --web --no-browser` |
| Custom port | `python main.py --web --port 8765` |

Desktop surfaces: **Home**, **Cards**, **Boost**, **Add team**, **Squad**, **Editor**, **Catalog**, **About**, plus the always-visible **Apply dock** / queue status.

| Side | Artifact | Role |
|------|----------|------|
| **External control/config** | `LE_Profile_Executor.exe` / `main.py` | Studio: packs, cards, batch, config + **desktop GUI** |
| **Inject-side** | `LECompanionInject.dll` + Lua queue worker | Fast arm/drain (MIN_INTERVAL 0.08s) |

## Product features (v1.14)

| Feature | How |
|---------|-----|
| **Workflow packs** | Boost → Match Ready / Signing Settle / Season Kickoff / Club Refresh |
| **SAFE Add team** | Free-agent overwrite + transfer (CreatePlayer advanced-only) |
| **Cards → Add team** | Select variant → **Send → Add team** (shared card, no re-search) |
| **Squad hub** | Click locks Target + Career ops ID; Match Ready from board |
| **Post-signing chain** | After Add team → optional New Signing Settle pack |
| **Match Day / Squad Boost** | Boost tab or `--run-pack matchday` |
| **Turbo apply** | Clear stale + meta + short poll; default for card/profile |
| **Batch squad** | Ops bar **Batch squad** — one card → all exported playerids |
| **Best match** | Ops bar **Best match** or `--best-match "Messi"` |
| **Snapshot history** | Auto on apply; `--list-snapshots` / Undo last |
| **Turbo arm** | Ops **Turbo arm** or `--turbo-arm` |

```bat
python main.py --list-packs
python main.py --run-pack squad_boost
python main.py --turbo-arm
python main.py --best-match Neymar --year 26
python main.py --apply-card --query Messi --year 26 --target-playerid 158023
python main.py --batch-targets 158023,20801 --apply-card --query Messi --year 26 --target-playerid 158023
```

## Anticheat / inject ownership (important)

| Who | Owns |
|-----|------|
| **LE `Launcher.exe`** | FakeEAAC backup/install/restore + primary inject of `FCLiveEditor.DLL` |
| **Companion** | Does **not** swap EAAC files and does **not** replace `FCLiveEditor.DLL` |

Launch the game with **LE Launcher** only. See [docs/ARCHITECTURE_DUAL_MODE.md](docs/ARCHITECTURE_DUAL_MODE.md) and [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Features

| Tab / tool | What it does |
|------------|----------------|
| **Home** | LIVE-first setup + goals |
| **Boost** | Team scripts (fitness etc.) via queue worker |
| **Cards** | Search (SQLite index), filters, favorites, edit surface, AI chat, Apply |
| **Squad** | Board of exported squad — click to lock target ID |
| **Editor** | Full topics + presets + AI fill + Apply |
| **Ops bar** | Force drain · Queue · History · Health · Undo last |
| **Apply dock** | Always-visible apply status + button (every tab) |
| **Header** | Install worker · Copy bridge · How to arm · LIVE pill |

### Hotkeys
- `F5` — Copy bridge  
- `Ctrl+Enter` — Apply card  
- `Ctrl+F` — Jump to Cards  

## Sync (arm once)

1. Start FC26 with **LE `Launcher.exe`** (FakeEAAC path).  
2. **Install worker** (GUI or `python main.py --install-worker`) — installs Lua bridge + `LECompanionInject.dll` under `lua/scripts/`.  
3. **Copy bridge** → LE Lua Engine → paste → Execute **once per session**  
4. Wait for **LIVE ✓**  
5. Export squad → Apply from external app  

Auto-arm stays **OFF** (broke LE launch).

## CLI (external control, no `python.exe` required when using packaged exe)

```bat
LE_Profile_Executor.exe --headless-check
LE_Profile_Executor.exe --protocol-self-check
LE_Profile_Executor.exe --install-worker
LE_Profile_Executor.exe --build-inject-dll
LE_Profile_Executor.exe --integration-status
LE_Profile_Executor.exe --run-profile full_fitness
LE_Profile_Executor.exe --gui
```

Dev:

```bat
python main.py --headless-check
python main.py --native-self-check
```

## Inject-side native DLL

```bat
cd native\le_companion_inject
build.bat
```

Produces `dist_native\LECompanionInject.dll` with exports:

- `LECompanion_SetQueueDir` / `Arm` / `ProcessQueue` / `GetLastResult` / `ProtocolVersion` / `GetModuleInfo`

Protocol version **2** (shared with `src/protocol.py` and Lua bridge).

## Performance notes

- Card search builds `card_db/catalog.sqlite` once (then fast substring queries)
- Tabs load lazily (Home first; Cards/Editor/… on first open)
- Build uses PyInstaller **onedir** (faster cold start than onefile)

## Build external exe

```bat
python build_exe.py
```

Preferred run: `LE_Profile_Executor_app/LE_Profile_Executor.exe` next to `card_db/` and `profiles.json`.

## Safety

- LE owns FakeEAAC + primary inject  
- Companion inject DLL = protocol helper only (`no-fakeeaac` in module info)  
- Game DB writes in live session go through LE Lua APIs via the queue worker  
- No second independent anticheat bypass  

## Development setup

Use Python 3.10 or newer. From the repository folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Card databases and local Live Editor data are not bundled. Add card data you are entitled to use under `card_db/`; the app can also import supported card files. Personal settings, credentials, profiles, and generated databases stay in your local app folder and are excluded from Git.

## Contributing

Bug reports and pull requests are welcome. Please avoid including access tokens, player saves, logs, or downloaded datasets in issues and pull requests.

## License and project status

The project source is released under the MIT License; see [LICENSE](LICENSE). FC 26 and Live Editor are third-party products. This community project is unofficial and is not affiliated with or endorsed by their owners.
