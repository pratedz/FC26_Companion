# lec_native.dll — native plugin for the resident Lua core

Live Editor's Lua has no timer. LE runs Lua only at autorun, LE init, Career
Mode events and hotkeys (`Fc26 LE/re/LE_LUA_RUNTIME_SPEC.md`). So the core
used to run jobs only when FC fired an allow-listed hub event. That is why
the app needed "refresh" clicks and visits to Team Management.

This plugin adds a **~200 ms tick on the thread that delivers Career Mode
events**. Each tick calls the Lua global `LECompanion_NativeTick()`, which goes
to `runner.on_native_tick()`. Reads and writes then run on a settled hub
without waiting for the next event.

## How it gets in

LE has no native plugin API. `GetPlugin` only looks up game objects. LE's
embedded Lua 5.4 does keep the standard `package.loadlib`, so the core loads
the DLL itself (`ingame/le_companion/native.lua`):

```lua
package.loadlib(core_dir .. "/native/lec_native.dll", "lec_native_open")
```

LE's DLL exports nothing, so the plugin cannot link against a Lua library. A
second Lua copy would corrupt the game's state. Instead it calls four Lua C
functions inside LE by RVA (`le_sig.h`):

| Function | RVA | REA evidence |
|---|---|---|
| `lua_gettop` | `0x51F450` | `ev_da879378…` |
| `lua_settop` | `0x51F490` | `ev_7076135d…` |
| `lua_load` | `0x521FB0` | `ev_815bcf29…` (called by `luaL_loadfilex`) |
| `lua_pcallk` | `0x521D30` | `ev_a64eab61…` |

## Guards

| | Guard | Effect when it fails |
|---|---|---|
| G1 | The first 24 code bytes of all four functions must match LE v26.3.6. The PE timestamp and SizeOfImage are also checked when headers exist. Bytes are read with `ReadProcessMemory`, so a bad base cannot fault. | Plugin stays idle all session. `reason` says why. |
| G2 | Ticks run only on the thread that delivered the latest Career event, with the same `lua_State`. | Tick skipped and counted (`skip_thread`, `skip_state`). |
| G3 | One tick at a time. | Skipped (`skip_busy`). |
| G4 | Kill switches: `native/DISABLE`, `<queue>/native_disable`, `config.json "native": false`. 50 Lua errors in a row also stop ticks. | Ticks stop within ~1 s. |
| G5 | The module pins itself, so Lua's CLIBS finaliser can't unload code a pending timer still points at. | — |
| Lua | `runner.on_native_tick` returns before any host call or file IO while simulating or off-hub. Writes also need 1.5 s of hub quiet, no job awaiting a post-transfer event, and existing work. `config.json "native_tick_writes": false` turns writes off. | Tick does nothing. |

## Status

A background thread, which never touches Lua or game memory, writes
`<queue>/native_status.json` about once a second. It reports `verified`,
`reason`, `tick_live`, thread ids, `same_thread`, `same_state` and counters.

## Build and test

```powershell
python build.py            # check le_sig.h vs FCLiveEditor.DLL, build, run harness, copy DLL into ingame/
python build.py --no-copy  # build + test only
python gen_le_sig.py "C:\Users\prated\Desktop\Fc26 LE\FCLiveEditor.DLL"   # regenerate le_sig.h
```

Toolchain: Zig 0.16 (`pip install --user ziglang`), target `x86_64-windows-gnu`.
`harness.exe` loads a `-DLEC_TEST` build. That build adds `lec_test_*` exports;
the release DLL exports only `lec_native_open`, `lec_native_mark_career` and
`lec_native_mark_hotkey`. The harness drives the real timer, thread and state
guards, the tick chunk, error counting, the signature check on a synthetic
image, and the config parser.

## A new Live Editor version

`gen_le_sig.py` refuses any FCLiveEditor.DLL whose SHA-256 differs. To support
a new build:

1. Re-locate the four functions with REA.
2. Update `SHA256`/`FUNCS` in `gen_le_sig.py`.
3. Regenerate `le_sig.h`.
4. Rebuild.

Until then, the old plugin detects the mismatch and stays idle. The core keeps
working on hub events as before.
