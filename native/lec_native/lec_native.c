/* lec_native.dll — native plugin for the FC26 Companion resident Lua core.
 *
 * Loaded inside FC26 by the core itself:  package.loadlib(path, "lec_native_open")
 * (Live Editor's embedded Lua 5.4 keeps the standard loadlib; LE has no
 * native plugin API of its own — GetPlugin only looks up game objects.)
 *
 * What it adds: a ~200 ms tick on the thread that delivers Career Mode
 * events, which calls the Lua global LECompanion_NativeTick(). Live Editor
 * gives Lua no timer, so without this the core only runs when FC fires an
 * allow-listed hub event.
 *
 * Two carriers deliver that tick, both on the Career event thread itself:
 *   timer  SetTimer on that thread (1.0.0). Needs a message pump there. The
 *          2026-10-17 session proved FC's Career thread has none: 3,246
 *          Career events, 0 ticks.
 *   apc    (1.1.0) A background thread queues one user APC (QueueUserAPC)
 *          to the Career thread every TICK_MS while none is outstanding.
 *          Windows runs it only when that thread itself enters an alertable
 *          wait, i.e. when it is idle in a wait call and not executing Lua or
 *          game code. A thread that never waits alertably simply never runs
 *          it; nothing piles up (one APC at most), and status says so
 *          ("no_carrier").
 *
 * Guards (all cheap, all automatic):
 *   G1  LE build check: the four Lua C functions used are found by RVA and
 *       their first LE_SIG_LEN bytes must match le_sig.h (read with
 *       ReadProcessMemory, so a bad base can never fault). PE timestamp and
 *       SizeOfImage are checked when the header is present. Any mismatch
 *       disables the tick for the whole session.
 *   G2  Thread check: the tick only runs on the thread that delivered the
 *       most recent Career Mode event, with the same lua_State. Lua is never
 *       entered from a second thread.
 *   G3  Re-entrancy: one tick at a time; a tick that arrives while one runs
 *       (an alertable wait inside a tick) is skipped and counted.
 *   G4  Kill switches: <dll dir>\DISABLE or <queue>\native_disable stops ticks
 *       within ~1 s; <dll dir>\NO_APC, <queue>\native_no_apc or Lua
 *       lec_native_no_apc() stops the APC carrier only. 50 consecutive Lua
 *       errors stop ticks.
 *   G5  The module pins itself so Lua's CLIBS finaliser can never unload code
 *       a pending WM_TIMER or APC still points at.
 *
 * The Lua side (le_companion/native.lua) decides what a tick may do; the
 * runner keeps its own simulate/hub/busy/quiet gates. This file never touches
 * game memory itself.
 *
 * Exports (all lua_CFunction, no Lua C API use, return 0 results):
 *   lec_native_open, lec_native_mark_career, lec_native_mark_hotkey,
 *   lec_native_no_apc
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "le_sig.h"

#define LEC_NATIVE_VERSION "1.1.0"
#define TICK_MS 200
#define STATUS_EVERY_MS 1000
#define TIMER_ID 0x4C454331u /* 'LEC1' */
#define MAX_CONSECUTIVE_ERRORS 50
#define REARM_AFTER_MS 5000

typedef struct lua_State lua_State;
typedef const char *(*lua_Reader)(lua_State *, void *, size_t *);
typedef int (*fn_gettop)(lua_State *);
typedef void (*fn_settop)(lua_State *, int);
typedef int (*fn_load)(lua_State *, lua_Reader, void *, const char *, const char *);
typedef int (*fn_pcallk)(lua_State *, int, int, int, intptr_t, void *);

typedef struct {
    fn_gettop gettop;
    fn_settop settop;
    fn_load load;
    fn_pcallk pcallk;
} lua_api;

static lua_api api;
static HMODULE g_self;

/* Written on the game thread, read by the status thread (word-sized). */
static volatile LONG g_opened, g_verified, g_disabled, g_shutdown, g_in_tick;
static volatile LONG g_errors_in_row, g_error_stop;
static lua_State *volatile g_L, *volatile g_career_L;
static volatile DWORD g_open_tid, g_career_tid, g_hotkey_tid, g_timer_tid;
static volatile LONG64 g_career_events, g_hotkey_events, g_ticks, g_tick_errors;
static volatile LONG64 g_skip_thread, g_skip_state, g_skip_busy, g_last_tick_ms, g_last_career_ms;
static volatile int g_last_status;
static HWND g_timer_hwnd;
static UINT_PTR g_timer_handle;
static uintptr_t g_le_base;
static const char *volatile g_reason = "not_opened";
static const char *g_base_via = "none";
static const char *g_header = "unchecked";
static const char *g_timer_kind = "none";
static WCHAR g_dll_dir[MAX_PATH], g_queue_dir[MAX_PATH];
static HANDLE g_status_thread, g_stop_event;

/* APC carrier. g_apc_pending is 1 from QueueUserAPC until the APC carrying
 * generation g_apc_gen has run; only the carrier thread queues. */
static volatile LONG g_apc_off, g_apc_file_off, g_apc_pending, g_apc_gen;
static volatile LONG64 g_apc_queued, g_apc_ran, g_apc_ticks, g_apc_queue_failed;
static volatile LONG64 g_apc_pending_since, g_timer_ticks;
static HANDLE g_apc_thread;           /* THREAD_SET_CONTEXT on g_apc_tid */
static volatile DWORD g_apc_tid;
static const char *volatile g_carrier = "none"; /* last carrier that ticked */
static LONG64 g_no_carrier_ms = 10000;          /* pending this long, 0 ticks */
static LONG64 g_apc_requeue_ms = 5000;         /* Career events moved threads */
static volatile LONG g_no_carrier;

static const char TICK_SRC[] =
    "local f = rawget(_G, 'LECompanion_NativeTick') if f then f() end";

/* ---- helpers ---------------------------------------------------------- */

static LONG64 now_ms(void) { return (LONG64)GetTickCount64(); }

static int read_mem(uintptr_t addr, void *out, size_t n) {
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), (LPCVOID)addr, out, n, &got) && got == n;
}

static int bytes_match(uintptr_t base, uint32_t rva, const unsigned char *sig) {
    unsigned char buf[LE_SIG_LEN];
    return read_mem(base + rva, buf, sizeof buf) && memcmp(buf, sig, sizeof buf) == 0;
}

/* Returns 1 when base looks like the exact LE build in le_sig.h. */
static int verify_le(uintptr_t base) {
    if (!base) { g_reason = "le_base_not_found"; return 0; }
    unsigned char hdr[0x200];
    if (read_mem(base, hdr, sizeof hdr) && hdr[0] == 'M' && hdr[1] == 'Z') {
        uint32_t pe = *(uint32_t *)(hdr + 0x3C);
        unsigned char nt[0x60];
        if (pe < 0x1000 && read_mem(base + pe, nt, sizeof nt) && memcmp(nt, "PE\0\0", 4) == 0) {
            uint32_t ts = *(uint32_t *)(nt + 8);
            uint32_t soi = *(uint32_t *)(nt + 24 + 56);
            if (ts != LE_SIG_TIMESTAMP || soi != LE_SIG_SIZE_OF_IMAGE) {
                g_header = "mismatch";
                g_reason = "le_build_mismatch_header";
                return 0;
            }
            g_header = "match";
        } else {
            g_header = "absent";
        }
    } else {
        g_header = "absent"; /* manually mapped image with erased headers */
    }
    if (!bytes_match(base, LE_RVA_LUA_GETTOP, LE_SIG_LUA_GETTOP) ||
        !bytes_match(base, LE_RVA_LUA_SETTOP, LE_SIG_LUA_SETTOP) ||
        !bytes_match(base, LE_RVA_LUA_LOAD, LE_SIG_LUA_LOAD) ||
        !bytes_match(base, LE_RVA_LUA_PCALLK, LE_SIG_LUA_PCALLK)) {
        g_reason = "le_build_mismatch_code";
        return 0;
    }
    api.gettop = (fn_gettop)(base + LE_RVA_LUA_GETTOP);
    api.settop = (fn_settop)(base + LE_RVA_LUA_SETTOP);
    api.load = (fn_load)(base + LE_RVA_LUA_LOAD);
    api.pcallk = (fn_pcallk)(base + LE_RVA_LUA_PCALLK);
    return 1;
}

/* Minimal reader for "queue_dir": "<utf-8 path>" in config.json. */
int lec_parse_queue_dir(const char *json, WCHAR *out, int out_len) {
    const char *p = strstr(json, "\"queue_dir\"");
    if (!p) return 0;
    p = strchr(p + 11, ':');
    if (!p) return 0;
    while (*++p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {}
    if (*p != '"') return 0;
    char buf[MAX_PATH * 3];
    int n = 0;
    for (++p; *p && *p != '"' && n < (int)sizeof buf - 1; ++p) {
        if (*p == '\\' && p[1]) {
            ++p;
            buf[n++] = (*p == '/' || *p == '\\' || *p == '"') ? *p : '?';
        } else {
            buf[n++] = *p;
        }
    }
    if (*p != '"' || n == 0) return 0;
    buf[n] = 0;
    return MultiByteToWideChar(CP_UTF8, 0, buf, -1, out, out_len) > 0;
}

static void load_paths(void) {
    WCHAR path[MAX_PATH];
    DWORD n = GetModuleFileNameW(g_self, path, MAX_PATH);
    if (!n || n >= MAX_PATH) return;
    WCHAR *slash = wcsrchr(path, L'\\');
    if (!slash) return;
    *slash = 0;
    lstrcpynW(g_dll_dir, path, MAX_PATH);
    /* <core>\native\lec_native.dll -> <core>\config.json */
    WCHAR cfg[MAX_PATH];
    lstrcpynW(cfg, path, MAX_PATH);
    WCHAR *up = wcsrchr(cfg, L'\\');
    if (!up) return;
    lstrcpynW(up, L"\\config.json", (int)(MAX_PATH - (up - cfg)));
    HANDLE h = CreateFileW(cfg, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                           OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    char buf[16384];
    DWORD got = 0;
    if (ReadFile(h, buf, sizeof buf - 1, &got, NULL)) {
        buf[got] = 0;
        lec_parse_queue_dir(buf, g_queue_dir, MAX_PATH);
    }
    CloseHandle(h);
}

static int file_exists(const WCHAR *dir, const WCHAR *name) {
    if (!dir[0]) return 0;
    WCHAR p[MAX_PATH];
    if (_snwprintf(p, MAX_PATH, L"%ls\\%ls", dir, name) < 0) return 0;
    return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES;
}

/* ---- status thread (never touches Lua or game memory) ------------------ */

/* Ticks only ever run on the Career thread (G2), so a recent tick is live. */
static int tick_is_live(LONG64 t) {
    LONG64 last = g_last_tick_ms;
    return g_verified && !g_disabled && !g_error_stop && g_career_tid && last &&
           (t - last) < 3000;
}

int lec_format_status(char *out, size_t cap) {
    LONG64 t = now_ms();
    LONG64 last = g_last_tick_ms;
    int tick_live = tick_is_live(t);
    LONG64 since = g_apc_pending_since;
    int apc_enabled = !g_apc_off && !g_apc_file_off;
    return snprintf(out, cap,
        "{\n"
        "  \"schema\": 1,\n"
        "  \"plugin\": \"lec_native\",\n"
        "  \"version\": \"%s\",\n"
        "  \"le_build\": \"%s\",\n"
        "  \"pid\": %lu,\n"
        "  \"verified\": %s,\n"
        "  \"reason\": \"%s\",\n"
        "  \"le_base\": \"0x%llx\",\n"
        "  \"le_base_via\": \"%s\",\n"
        "  \"pe_header\": \"%s\",\n"
        "  \"disabled\": %s,\n"
        "  \"error_stop\": %s,\n"
        "  \"tick_live\": %s,\n"
        "  \"tick_ms\": %d,\n"
        "  \"timer_kind\": \"%s\",\n"
        "  \"threads\": {\"open\": %lu, \"career\": %lu, \"hotkey\": %lu, \"timer\": %lu},\n"
        "  \"same_thread\": %s,\n"
        "  \"same_state\": %s,\n"
        "  \"counts\": {\"career_events\": %lld, \"hotkey_events\": %lld, \"ticks\": %lld,"
        " \"tick_errors\": %lld, \"skip_thread\": %lld, \"skip_state\": %lld, \"skip_busy\": %lld,"
        " \"timer_ticks\": %lld, \"apc_ticks\": %lld},\n"
        "  \"carrier\": \"%s\",\n"
        "  \"apc\": {\"enabled\": %s, \"tid\": %lu, \"queued\": %lld, \"ran\": %lld,"
        " \"queue_failed\": %lld, \"pending\": %s, \"pending_ms\": %lld},\n"
        "  \"last_tick_age_ms\": %lld,\n"
        "  \"last_career_age_ms\": %lld,\n"
        "  \"last_status\": %d,\n"
        "  \"uptime_ms\": %lld\n"
        "}\n",
        LEC_NATIVE_VERSION, LE_SIG_VERSION, (unsigned long)GetCurrentProcessId(),
        g_verified ? "true" : "false", g_reason, (unsigned long long)g_le_base, g_base_via,
        g_header, g_disabled ? "true" : "false", g_error_stop ? "true" : "false",
        tick_live ? "true" : "false", TICK_MS, g_timer_kind,
        (unsigned long)g_open_tid, (unsigned long)g_career_tid, (unsigned long)g_hotkey_tid,
        (unsigned long)g_timer_tid,
        ((g_timer_tid && g_timer_tid == g_career_tid) ||
         (g_apc_tid && g_apc_tid == g_career_tid)) ? "true" : "false",
        (g_L && g_L == g_career_L) ? "true" : "false",
        (long long)g_career_events, (long long)g_hotkey_events, (long long)g_ticks,
        (long long)g_tick_errors, (long long)g_skip_thread, (long long)g_skip_state,
        (long long)g_skip_busy, (long long)g_timer_ticks, (long long)g_apc_ticks,
        g_carrier,
        apc_enabled ? "true" : "false", (unsigned long)g_apc_tid, (long long)g_apc_queued,
        (long long)g_apc_ran, (long long)g_apc_queue_failed,
        g_apc_pending ? "true" : "false",
        (g_apc_pending && since) ? (long long)(t - since) : 0LL,
        last ? (long long)(t - last) : -1LL,
        g_last_career_ms ? (long long)(t - g_last_career_ms) : -1LL, g_last_status,
        (long long)t);
}

static void write_status(void) {
    if (!g_queue_dir[0]) return;
    char body[4096];
    int n = lec_format_status(body, sizeof body);
    if (n <= 0 || n >= (int)sizeof body) return;
    WCHAR tmp[MAX_PATH], dst[MAX_PATH];
    _snwprintf(tmp, MAX_PATH, L"%ls\\native_status.json.tmp", g_queue_dir);
    _snwprintf(dst, MAX_PATH, L"%ls\\native_status.json", g_queue_dir);
    HANDLE h = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD wrote = 0;
    BOOL ok = WriteFile(h, body, (DWORD)n, &wrote, NULL) && wrote == (DWORD)n;
    CloseHandle(h);
    if (ok) MoveFileExW(tmp, dst, MOVEFILE_REPLACE_EXISTING);
}

static void queue_apc_tick(LONG64 t);

/* Carrier thread: queues the APC tick every TICK_MS and refreshes the kill
 * switches and native_status.json every STATUS_EVERY_MS. Never touches Lua
 * or game memory. */
static DWORD WINAPI status_main(LPVOID unused) {
    (void)unused;
    LONG64 last_status = 0;
    while (WaitForSingleObject(g_stop_event, TICK_MS) == WAIT_TIMEOUT) {
        LONG64 t = now_ms();
        if (!last_status || t - last_status >= STATUS_EVERY_MS) {
            LONG off = file_exists(g_dll_dir, L"DISABLE") || file_exists(g_queue_dir, L"native_disable");
            InterlockedExchange(&g_disabled, off);
            LONG no_apc = file_exists(g_dll_dir, L"NO_APC") || file_exists(g_queue_dir, L"native_no_apc");
            InterlockedExchange(&g_apc_file_off, no_apc);
            /* Honest state: queued but never delivered, and no timer tick either. */
            if (g_verified && !g_error_stop && g_career_tid && !g_ticks && g_apc_pending &&
                g_apc_pending_since && t - g_apc_pending_since >= g_no_carrier_ms) {
                g_no_carrier = 1;
                g_reason = "no_carrier";
            } else if (g_no_carrier && g_ticks && !g_error_stop) {
                g_no_carrier = 0;
                g_reason = "ok";
            }
            write_status();
            last_status = t;
        }
        queue_apc_tick(t);
    }
    return 0;
}

/* ---- tick -------------------------------------------------------------- */

typedef struct { const char *s; size_t n; } chunk;

static const char *chunk_reader(lua_State *L, void *ud, size_t *sz) {
    (void)L;
    chunk *c = (chunk *)ud;
    if (!c->n) { *sz = 0; return NULL; }
    *sz = c->n;
    c->n = 0;
    return c->s;
}

/* Run one tick on L. Caller guarantees the thread/state guards. */
int lec_run_tick(lua_State *L) {
    int top = api.gettop(L);
    chunk c = { TICK_SRC, sizeof TICK_SRC - 1 };
    int st = api.load(L, chunk_reader, &c, "=lec_native_tick", "t");
    if (st == 0) st = api.pcallk(L, 0, 0, 0, 0, NULL);
    api.settop(L, top);
    return st;
}

/* One tick from either carrier, behind guards G1-G4. */
static void guarded_tick(int via_apc) {
    if (g_shutdown || !g_verified || g_disabled || g_error_stop) return;
    if (GetCurrentThreadId() != g_career_tid) { g_skip_thread++; return; }
    lua_State *L = g_L;
    if (!L || L != g_career_L) { g_skip_state++; return; }
    if (InterlockedCompareExchange(&g_in_tick, 1, 0) != 0) { g_skip_busy++; return; }
    int st = lec_run_tick(L);
    g_last_status = st;
    g_ticks++;
    if (via_apc) g_apc_ticks++; else g_timer_ticks++;
    g_carrier = via_apc ? "apc" : "timer";
    g_last_tick_ms = now_ms();
    if (st != 0) {
        g_tick_errors++;
        if (++g_errors_in_row >= MAX_CONSECUTIVE_ERRORS) {
            g_error_stop = 1;
            g_reason = "tick_error_stop";
        }
    } else {
        g_errors_in_row = 0;
    }
    InterlockedExchange(&g_in_tick, 0);
}

static VOID CALLBACK tick_proc(HWND hwnd, UINT msg, UINT_PTR id, DWORD t) {
    (void)hwnd; (void)msg; (void)id; (void)t;
    guarded_tick(0);
}

/* Runs on the Career thread, inside one of its own alertable waits. */
static VOID CALLBACK apc_proc(ULONG_PTR gen) {
    g_apc_ran++;
    if (!g_apc_off && !g_apc_file_off) guarded_tick(1);
    /* Only the APC of the current generation frees the slot: an old one that
     * finally runs on a former Career thread must not double-queue. */
    if ((LONG)gen == g_apc_gen) InterlockedExchange(&g_apc_pending, 0);
}

/* Carrier thread only. At most one APC outstanding. */
static void queue_apc_tick(LONG64 t) {
    if (g_shutdown || !g_verified || g_disabled || g_error_stop) return;
    if (g_apc_off || g_apc_file_off) return;
    DWORD tid = g_career_tid;
    if (!tid) return;
    if (g_apc_pending) {
        /* Still queued: that thread has not waited alertably since. */
        if (tid == g_apc_tid || t - g_apc_pending_since < g_apc_requeue_ms) return;
    }
    if (tid != g_apc_tid || !g_apc_thread) {
        if (g_apc_thread) CloseHandle(g_apc_thread);
        g_apc_thread = OpenThread(THREAD_SET_CONTEXT, FALSE, tid);
        g_apc_tid = tid;
        if (!g_apc_thread) { g_apc_queue_failed++; return; }
    }
    LONG gen = InterlockedIncrement(&g_apc_gen);
    InterlockedExchange(&g_apc_pending, 1);
    g_apc_pending_since = t;
    if (QueueUserAPC(apc_proc, g_apc_thread, (ULONG_PTR)gen)) {
        g_apc_queued++;
    } else {
        InterlockedExchange(&g_apc_pending, 0);
        g_apc_queue_failed++;
    }
}

static BOOL CALLBACK find_window(HWND h, LPARAM out) {
    if (GetParent(h) == NULL) { *(HWND *)out = h; return FALSE; }
    return TRUE;
}

/* Arms the timer on the calling thread (the Career event thread). */
static void arm_timer(void) {
    DWORD tid = GetCurrentThreadId();
    if (g_timer_tid == tid && g_timer_handle) {
        LONG64 last = g_last_tick_ms;
        if (!last || now_ms() - last < REARM_AFTER_MS) return;
        /* Armed but silent (window recreated?): re-arm below. */
        KillTimer(g_timer_hwnd, g_timer_handle);
        g_timer_handle = 0;
    }
    HWND hwnd = NULL;
    EnumThreadWindows(tid, find_window, (LPARAM)&hwnd);
    UINT_PTR h = SetTimer(hwnd, hwnd ? TIMER_ID : 0, TICK_MS, tick_proc);
    if (!h) { g_reason = "set_timer_failed"; return; }
    g_timer_hwnd = hwnd;
    g_timer_handle = h;
    g_timer_kind = hwnd ? "window" : "thread";
    g_timer_tid = tid;
    if (g_verified) g_reason = "ok";
}

/* ---- exports ----------------------------------------------------------- */

__declspec(dllexport) int lec_native_open(lua_State *L) {
    if (InterlockedCompareExchange(&g_opened, 1, 0) != 0) return 0;
    g_L = L;
    g_open_tid = GetCurrentThreadId();
    HMODULE le = GetModuleHandleW(L"FCLiveEditor.DLL");
    if (le) {
        g_le_base = (uintptr_t)le;
        g_base_via = "module_list";
    } else {
        /* The caller is Lua's precallC inside LE's image. */
        MEMORY_BASIC_INFORMATION mbi;
        if (VirtualQuery(__builtin_return_address(0), &mbi, sizeof mbi))
            g_le_base = (uintptr_t)mbi.AllocationBase;
        g_base_via = "return_address";
    }
    g_verified = verify_le(g_le_base);
    if (g_verified) g_reason = "waiting_for_career_event";
    load_paths();
    g_stop_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    g_status_thread = CreateThread(NULL, 0, status_main, NULL, 0, NULL);
    return 0;
}

__declspec(dllexport) int lec_native_mark_career(lua_State *L) {
    g_career_tid = GetCurrentThreadId();
    g_career_L = L;
    g_career_events++;
    g_last_career_ms = now_ms();
    if (g_verified && !g_shutdown) arm_timer();
    return 0;
}

__declspec(dllexport) int lec_native_mark_hotkey(lua_State *L) {
    (void)L;
    g_hotkey_tid = GetCurrentThreadId();
    g_hotkey_events++;
    return 0;
}

/* config.json "native_apc": false — the APC carrier stays off this session.
 * Already-queued APCs then return without ticking. */
__declspec(dllexport) int lec_native_no_apc(lua_State *L) {
    (void)L;
    InterlockedExchange(&g_apc_off, 1);
    return 0;
}

#ifdef LEC_TEST
/* Test-only: bind fake Lua functions and read counters. Not in release builds. */
__declspec(dllexport) void lec_test_bind(fn_gettop a, fn_settop b, fn_load c, fn_pcallk d) {
    api.gettop = a; api.settop = b; api.load = c; api.pcallk = d;
    g_verified = 1;
    g_reason = "test_bound";
}
__declspec(dllexport) long long lec_test_counter(int which) {
    switch (which) {
    case 0: return g_ticks;
    case 1: return g_tick_errors;
    case 2: return g_skip_thread;
    case 3: return g_skip_state;
    case 4: return g_error_stop;
    case 5: return g_verified;
    case 6: return g_apc_queued;
    case 7: return g_apc_ran;
    case 8: return g_apc_ticks;
    case 9: return g_timer_ticks;
    case 10: return g_skip_busy;
    case 11: return g_apc_pending;
    case 12: return g_no_carrier;
    default: return -1;
    }
}
__declspec(dllexport) void lec_test_tune(int no_carrier_ms, int requeue_ms) {
    g_no_carrier_ms = no_carrier_ms;
    g_apc_requeue_ms = requeue_ms;
}
__declspec(dllexport) int lec_test_verify_base(uintptr_t base) { return verify_le(base); }
__declspec(dllexport) int lec_test_parse(const char *json, WCHAR *out, int n) {
    return lec_parse_queue_dir(json, out, n);
}
__declspec(dllexport) int lec_test_status(char *out, int cap) { return lec_format_status(out, (size_t)cap); }
#endif

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = inst;
        DisableThreadLibraryCalls(inst);
        HMODULE pinned;
        GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           (LPCWSTR)&DllMain, &pinned);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_shutdown = 1;
        if (g_stop_event) SetEvent(g_stop_event);
    }
    return TRUE;
}
