# lec_native.dll — native plugin for the resident Lua core

Live Editor's Lua has no timer. LE runs Lua only at autorun, LE init, Career
Mode events and hotkeys (`Fc26 LE/re/LE_LUA_RUNTIME_SPEC.md`). So the core
used to run jobs only when FC fired an allow-listed hub event. That is why
the app needed "refresh" clicks and visits to Team Management.

This plugin adds a **~200 ms tick on the thread that delivers Career Mode
events**. Each tick calls the Lua global `LECompanion_NativeTick()`, which goes
to `runner.on_native_tick()`. Reads and plain field edits then run on a quiet,
settled hub without waiting for the next event. Signing, release and the other
heavy jobs still wait for a Team Management visit (core 2.6.42).

## Carriers (1.1.0)

The tick needs something that runs code *on the Career thread itself*, so Lua
is never entered from a second thread. Two carriers try, and
`native_status.json` reports which one delivered (`carrier`):

| Carrier | How | Works when |
|---|---|---|
| `timer` (1.0.0) | `SetTimer` on the Career thread | that thread pumps window messages. On 2026-10-17 FC's did not: 3,246 Career events, 0 ticks, no window on the thread. |
| `apc` (1.1.0) | A background thread queues one user APC (`QueueUserAPC`) to the Career thread every 200 ms while none is outstanding. | Windows runs an APC only inside that thread's *own* alertable wait (`SleepEx`, `WaitFor…Ex`, … with alertable set): the thread is idle in a wait call, not in the middle of Lua or game code. |

If the Career thread never waits alertably, the single queued APC never runs,
nothing piles up, and after 10 s `reason` becomes `no_carrier`. The app then
shows "Instant mode: off — … jobs run when you open Team Management", which is
the 2.6.42 one-visit behaviour.

**The APC carrier is off by default** (core 2.6.42 review): an alertable wait
can sit deep inside game code, and LE's single `lua_State` has no lock against
its console or hotkey scripts. `native.lua` calls `lec_native_no_apc` right
after `lec_native_open` unless `config.json` says `"native_apc": true`, so the
status shows `apc.enabled: false` and only the timer carrier is armed.

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
| G3 | One tick at a time. At most one APC is outstanding, and the slot is freed only when that APC has finished, so an alertable wait inside a tick cannot nest another one. | Skipped (`skip_busy`). |
| G4 | Kill switches: `native/DISABLE`, `<queue>/native_disable`, `config.json "native": false`. APC only: `native/NO_APC`, `<queue>/native_no_apc`, `config.json "native_apc": false` (Lua calls `lec_native_no_apc`). 50 Lua errors in a row also stop ticks. | Ticks stop within ~1 s. |
| G5 | The module pins itself, so Lua's CLIBS finaliser can't unload code a pending timer or APC still points at. | — |
| Lua | `runner.on_native_tick` returns before any host call or file IO while simulating or off-hub, from inside a job or hub drain, while a Career event is open (pre seen, post not yet) and within `LIMITS.quiet_ms` of the last event. Writes also need 1.5 s of quiet, no job awaiting a post-transfer event, and existing work, and run read and light jobs only. `config.json "native_tick_writes": false` turns writes off. | Tick does nothing. |

## Status

A background thread, which never touches Lua or game memory, queues the APC
every 200 ms and writes `<queue>/native_status.json` about once a second. It
reports `verified`, `reason` (`no_carrier` when neither carrier reaches the
Career thread), `tick_live`, `carrier`, thread ids, `same_thread`,
`same_state`, counters (including `timer_ticks` and `apc_ticks`) and an `apc`
block (`enabled`, `tid`, `queued`, `ran`, `queue_failed`, `pending`,
`pending_ms`).

## Build and test

```powershell
python build.py            # check le_sig.h vs FCLiveEditor.DLL, build, run harness, copy DLL into ingame/
python build.py --no-copy  # build + test only
python gen_le_sig.py "C:\Users\prated\Desktop\Fc26 LE\FCLiveEditor.DLL"   # regenerate le_sig.h
```

Toolchain: Zig 0.16 (`pip install --user ziglang`), target `x86_64-windows-gnu`.
`harness.exe` loads a `-DLEC_TEST` build. That build adds `lec_test_*` exports;
the release DLL exports only `lec_native_open`, `lec_native_mark_career`,
`lec_native_mark_hotkey` and `lec_native_no_apc`. The harness drives the real
timer and APC carriers on real threads (a window thread, a pump-less thread
that waits alertably, and one that never does), the thread, state and
re-entrancy guards, every kill switch, the tick chunk, error counting, the
`no_carrier` report, the signature check on a synthetic image, and the config
parser.

## A new Live Editor version

`gen_le_sig.py` refuses any FCLiveEditor.DLL whose SHA-256 differs. To support
a new build:

1. Re-locate the four functions with REA.
2. Update `SHA256`/`FUNCS` in `gen_le_sig.py`.
3. Regenerate `le_sig.h`.
4. Rebuild.

Until then, the old plugin detects the mismatch and stays idle. The core keeps
working on hub events as before.
