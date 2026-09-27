# Dual-mode architecture: inject-side DLL + external control

## Before (Python-only companion)

```
External (Python/exe GUI) → queue/*.lua → LE Lua bridge (armed in session) → game DB
```

User still launched via LE Launcher (FakeEAAC + `FCLiveEditor.DLL`). Companion
never injected.

## After (dual-mode)

```
                    ┌─────────────────────────────────────┐
                    │ LE Launcher.exe                     │
                    │  · FakeEAAC swap (sole AC path)     │
                    │  · Inject FCLiveEditor.DLL only     │
                    └──────────────┬──────────────────────┘
                                   │ session
                    ┌──────────────▼──────────────────────┐
                    │ FC26 + FCLiveEditor.DLL             │
                    │  · Lua Engine + DB APIs             │
                    │  · 00_le_companion_bridge.lua       │
                    │  · LECompanionInject.dll (helper)   │
                    └──────────────▲──────────────────────┘
                                   │ protocol (queue files)
                    ┌──────────────┴──────────────────────┐
                    │ External control/config             │
                    │  LE_Profile_Executor.exe / main.py  │
                    │  · profiles, cards, editor, config  │
                    │  · issues apply/profile jobs        │
                    └─────────────────────────────────────┘
```

## Responsibility split

| Concern | Owner |
|---------|--------|
| Anticheat bypass / FakeEAAC | **LE Launcher only** |
| Primary game inject | **`FCLiveEditor.DLL` only** |
| In-session job drain (real LE APIs) | Lua bridge worker |
| Native protocol consumer / inject-side helper | `LECompanionInject.dll` |
| Customization UX + config | External program |
| Generate LE-compatible Lua | External (`card_to_lua`, profiles, editor) |

## Why both DLL and external program

- **External** stays required for search, edit, profiles, favorites, AI, config.
- **Inject-side DLL** makes the protocol a first-class native module (loadable,
  testable, installable next to LE scripts) so session-side arm/drain is not
  “Python only.”
- Real attribute/DB writes still require LE’s Lua APIs inside the game process;
  the DLL does not replace `FCLiveEditor.DLL` or reimplement EAAC.

## Install / arm (LE-first)

1. Start game with **LE `Launcher.exe`** (not companion).
2. External: **Install worker** → copies Lua bridge + inject DLL under LE tree.
3. LE Lua Engine: paste/execute bridge **once** → LIVE heartbeat.
4. External: Apply profile/card → queue → inject-side drains → `_last_result.txt`.

Auto-arm into `live_editor.lua` remains **disabled** (froze LE launch).

## Protocol

See [PROTOCOL.md](PROTOCOL.md). File queue remains the control plane so dry-run
tests work without FC26.
