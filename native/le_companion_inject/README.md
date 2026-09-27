# LECompanionInject.dll (inject-side)

Native Windows DLL implementing the companion **queue protocol** (arm / drain /
result markers). Part of dual-mode LE_Profile_Executor.

## What this is

- Session-side **protocol consumer** for jobs written by the external control app
- `DllMain`-loadable; exports `LECompanion_*` API (see `le_companion_inject.h`)

## What this is NOT

- **Not** a FakeEAAC installer — LE `Launcher.exe` owns anticheat swap
- **Not** a replacement for `FCLiveEditor.DLL` — LE remains primary inject
- Does not reimplement EAAC/Javelin bypass

## Build

```bat
build.bat
```

Output: `LE_Profile_Executor/dist_native/LECompanionInject.dll`

Requires MSVC (`cl`), MinGW (`gcc`), or `clang`. If none present, build exits
with code 2 and a clear message; sources remain complete.

Or:

```bat
cmake -B build -A x64
cmake --build build --config Release
```

## Exports

| Export | Role |
|--------|------|
| `LECompanion_SetQueueDir` | Point at companion `queue/` |
| `LECompanion_Arm` | Write alive/armed heartbeats |
| `LECompanion_ProcessQueue` | Drain `_run_now` + pending (protocol dry exec) |
| `LECompanion_GetLastResult` | Last `OK`/`FAIL` line |
| `LECompanion_ProtocolVersion` | `2` |
| `LECompanion_GetModuleInfo` | Identity string including `no-fakeeaac` |

In a live LE session, the **Lua bridge** still executes real LE DB APIs; the DLL
mirrors the same file protocol for native/tests/install layout.
