/* Test harness for lec_native (LEC_TEST build). Exercises the real timer and
 * APC carriers, thread guard, re-entrancy, kill switches, tick chunk, error
 * counting, LE signature check and config parser with fake Lua functions.
 * Exit code 0 = all checks passed. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "le_sig.h"

typedef struct lua_State lua_State;
typedef const char *(*lua_Reader)(lua_State *, void *, size_t *);
typedef int (*fn_gettop)(lua_State *);
typedef void (*fn_settop)(lua_State *, int);
typedef int (*fn_load)(lua_State *, lua_Reader, void *, const char *, const char *);
typedef int (*fn_pcallk)(lua_State *, int, int, int, intptr_t, void *);
typedef int (*fn_cfunc)(lua_State *);
typedef void (*fn_bind)(fn_gettop, fn_settop, fn_load, fn_pcallk);
typedef long long (*fn_counter)(int);
typedef int (*fn_verify)(uintptr_t);
typedef int (*fn_parse)(const char *, WCHAR *, int);
typedef int (*fn_status)(char *, int);
typedef void (*fn_tune)(int, int);

static lua_State *const FAKE_L = (lua_State *)(uintptr_t)0x1000;
static int g_fail, g_settop_arg = -99, g_pcall_ret, g_load_calls, g_pcall_calls;
static char g_chunk[256];
static char g_chunkname[64];

static int f_gettop(lua_State *L) { return L == FAKE_L ? 7 : -1; }
static void f_settop(lua_State *L, int i) { (void)L; g_settop_arg = i; }
static int f_load(lua_State *L, lua_Reader r, void *ud, const char *name, const char *mode) {
    size_t n = 0;
    const char *s = r(L, ud, &n);
    if (s && n < sizeof g_chunk) { memcpy(g_chunk, s, n); g_chunk[n] = 0; }
    if (r(L, ud, &n) != NULL || n != 0) g_fail++, printf("FAIL reader did not end\n");
    lstrcpynA(g_chunkname, name, sizeof g_chunkname);
    if (strcmp(mode, "t") != 0) g_fail++, printf("FAIL load mode %s\n", mode);
    g_load_calls++;
    return 0;
}
static volatile DWORD g_pcall_tid;
static volatile LONG g_pcall_depth, g_pcall_max_depth;
static volatile DWORD g_pcall_alert_ms; /* > 0: the fake Lua call waits alertably */
static int f_pcallk(lua_State *L, int na, int nr, int ef, intptr_t ctx, void *k) {
    if (L != FAKE_L || na || nr || ef || ctx || k) g_fail++, printf("FAIL pcallk args\n");
    g_pcall_calls++;
    g_pcall_tid = GetCurrentThreadId();
    LONG d = InterlockedIncrement(&g_pcall_depth);
    if (d > g_pcall_max_depth) g_pcall_max_depth = d;
    if (g_pcall_alert_ms) SleepEx(g_pcall_alert_ms, TRUE);
    InterlockedDecrement(&g_pcall_depth);
    return g_pcall_ret;
}

#define CHECK(cond, msg) do { if (cond) printf("PASS %s\n", msg); else { g_fail++; printf("FAIL %s\n", msg); } } while (0)

static void pump(DWORD ms) {
    DWORD end = GetTickCount() + ms;
    MSG m;
    while ((LONG)(end - GetTickCount()) > 0) {
        while (PeekMessageW(&m, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageW(&m); }
        Sleep(5);
    }
}

static fn_cfunc g_mark;
static DWORD WINAPI other_thread(LPVOID p) { (void)p; g_mark(FAKE_L); return 0; }

/* FC's Career thread as observed on 2026-10-17: no window, no message pump.
 * alert_thread waits alertably (so APCs can run), plain_thread never does. */
static volatile DWORD g_alert_tid;
static DWORD WINAPI alert_thread(LPVOID p) {
    DWORD end = GetTickCount() + (DWORD)(uintptr_t)p;
    g_alert_tid = GetCurrentThreadId();
    g_mark(FAKE_L);
    while ((LONG)(end - GetTickCount()) > 0) SleepEx(20, TRUE);
    return 0;
}
static DWORD WINAPI plain_thread(LPVOID p) {
    g_mark(FAKE_L);
    Sleep((DWORD)(uintptr_t)p);
    return 0;
}
static void run_thread(LPTHREAD_START_ROUTINE fn, DWORD ms) {
    HANDLE th = CreateThread(NULL, 0, fn, (LPVOID)(uintptr_t)ms, 0, NULL);
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
}
static void touch(const WCHAR *dir, const WCHAR *name, int create) {
    WCHAR p[MAX_PATH];
    _snwprintf(p, MAX_PATH, L"%ls\\%ls", dir, name);
    if (create) {
        HANDLE f = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
        if (f != INVALID_HANDLE_VALUE) CloseHandle(f);
    } else {
        DeleteFileW(p);
    }
}

int main(int argc, char **argv) {
    const char *dll = argc > 1 ? argv[1] : "lec_native_test.dll";
    HMODULE h = LoadLibraryA(dll);
    if (!h) { printf("FAIL load %s (%lu)\n", dll, GetLastError()); return 2; }
    fn_cfunc open = (fn_cfunc)GetProcAddress(h, "lec_native_open");
    fn_cfunc mark = (fn_cfunc)GetProcAddress(h, "lec_native_mark_career");
    fn_cfunc hotkey = (fn_cfunc)GetProcAddress(h, "lec_native_mark_hotkey");
    fn_bind bind = (fn_bind)GetProcAddress(h, "lec_test_bind");
    fn_counter ctr = (fn_counter)GetProcAddress(h, "lec_test_counter");
    fn_verify verify = (fn_verify)GetProcAddress(h, "lec_test_verify_base");
    fn_parse parse = (fn_parse)GetProcAddress(h, "lec_test_parse");
    fn_status status = (fn_status)GetProcAddress(h, "lec_test_status");
    fn_cfunc no_apc = (fn_cfunc)GetProcAddress(h, "lec_native_no_apc");
    fn_tune tune = (fn_tune)GetProcAddress(h, "lec_test_tune");
    CHECK(open && mark && hotkey && bind && ctr && verify && parse && status && no_apc && tune,
          "exports present");
    if (g_fail) return 1;
    g_mark = mark;
    WCHAR dll_dir[MAX_PATH];
    GetModuleFileNameW(h, dll_dir, MAX_PATH);
    *wcsrchr(dll_dir, L'\\') = 0;
    touch(dll_dir, L"DISABLE", 0);
    touch(dll_dir, L"NO_APC", 0);

    /* Signature: harness exe is not LE. */
    CHECK(verify((uintptr_t)GetModuleHandleW(NULL)) == 0, "rejects a non-LE image");

    /* Signature: a synthetic image carrying the exact LE bytes is accepted. */
    unsigned char *img = VirtualAlloc(NULL, LE_SIG_SIZE_OF_IMAGE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    img[0] = 'M'; img[1] = 'Z'; *(uint32_t *)(img + 0x3C) = 0x80;
    memcpy(img + 0x80, "PE\0\0", 4);
    *(uint32_t *)(img + 0x80 + 8) = LE_SIG_TIMESTAMP;
    *(uint32_t *)(img + 0x80 + 24 + 56) = LE_SIG_SIZE_OF_IMAGE;
    memcpy(img + LE_RVA_LUA_GETTOP, LE_SIG_LUA_GETTOP, LE_SIG_LEN);
    memcpy(img + LE_RVA_LUA_SETTOP, LE_SIG_LUA_SETTOP, LE_SIG_LEN);
    memcpy(img + LE_RVA_LUA_LOAD, LE_SIG_LUA_LOAD, LE_SIG_LEN);
    memcpy(img + LE_RVA_LUA_PCALLK, LE_SIG_LUA_PCALLK, LE_SIG_LEN);
    CHECK(verify((uintptr_t)img) == 1, "accepts the exact LE v26.3.6 signature");
    img[LE_RVA_LUA_PCALLK + 5] ^= 0xFF;
    CHECK(verify((uintptr_t)img) == 0, "rejects one changed code byte");
    img[LE_RVA_LUA_PCALLK + 5] ^= 0xFF;
    *(uint32_t *)(img + 0x80 + 8) = LE_SIG_TIMESTAMP + 1;
    CHECK(verify((uintptr_t)img) == 0, "rejects a different PE timestamp");
    memset(img, 0, 0x200); /* erased headers (manual map): code bytes decide */
    CHECK(verify((uintptr_t)img) == 1, "accepts headerless image with matching code");
    CHECK(verify((uintptr_t)0x10) == 0, "unreadable base is rejected without faulting");
    VirtualFree(img, 0, MEM_RELEASE);

    /* Config parser. */
    WCHAR q[MAX_PATH];
    CHECK(parse("{\"core_version\":\"2.6.38\",\"queue_dir\" : \"C:/Users/x/Fc26 LE/FC26_Companion/queue\"}", q, MAX_PATH)
          && wcscmp(q, L"C:/Users/x/Fc26 LE/FC26_Companion/queue") == 0, "parses queue_dir");
    CHECK(parse("{\"queue_dir\": \"C:\\\\a\\\\b\"}", q, MAX_PATH) && wcscmp(q, L"C:\\a\\b") == 0, "unescapes backslashes");
    CHECK(!parse("{\"other\": 1}", q, MAX_PATH), "missing queue_dir is rejected");

    /* Window on this thread, like FC's main window. */
    HWND wnd = CreateWindowExW(0, L"STATIC", L"fc26-harness", WS_OVERLAPPEDWINDOW, 0, 0, 10, 10, NULL, NULL, NULL, NULL);
    CHECK(wnd != NULL, "harness window");

    open(FAKE_L);
    CHECK(ctr(5) == 0, "real open on a non-LE host stays unverified");
    pump(500);
    CHECK(ctr(0) == 0, "no ticks before verification");

    bind(f_gettop, f_settop, f_load, f_pcallk);
    pump(500);
    CHECK(ctr(0) == 0, "no ticks before the first Career event");
    CHECK(ctr(6) == 0, "no APC before the first Career event");

    /* FC's Career thread as measured on 2026-10-17: no window, no message
     * pump. If it never waits alertably either, nothing may run there, at
     * most one APC may wait, and status must say so. */
    tune(600, 300);
    {
        HANDLE pt = CreateThread(NULL, 0, plain_thread, (LPVOID)(uintptr_t)3000, 0, NULL);
        Sleep(2400);
        CHECK(ctr(11) == 1, "one APC stays pending on a thread that never waits alertably");
        CHECK(ctr(6) == 1, "the carrier never piles up APCs");
        CHECK(ctr(7) == 0 && ctr(0) == 0, "nothing runs on a thread that never waits alertably");
        CHECK(ctr(12) == 1, "no carrier is reported");
        char nb[4096];
        int nn = status(nb, sizeof nb);
        CHECK(nn > 0 && strstr(nb, "\"reason\": \"no_carrier\"") && strstr(nb, "\"pending\": true"),
              "status JSON says no_carrier with the APC pending");
        WaitForSingleObject(pt, INFINITE);
        CloseHandle(pt);
    }

    mark(FAKE_L);
    pump(1300);
    long long t1 = ctr(0);
    CHECK(t1 >= 4, "ticks run on the Career event thread (~200 ms)");
    CHECK(g_load_calls == t1 && g_pcall_calls == t1, "each tick loads and pcalls once");
    CHECK(strcmp(g_chunk, "local f = rawget(_G, 'LECompanion_NativeTick') if f then f() end") == 0, "tick chunk text");
    CHECK(strcmp(g_chunkname, "=lec_native_tick") == 0, "tick chunk name");
    CHECK(g_settop_arg == 7, "stack restored to the pre-tick top");

    /* Career events move to another thread: this thread must stop entering Lua. */
    HANDLE th = CreateThread(NULL, 0, other_thread, NULL, 0, NULL);
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
    long long before = ctr(0), skips = ctr(2);
    pump(800);
    CHECK(ctr(0) == before, "no ticks on a thread that no longer owns Career events");
    CHECK(ctr(2) > skips, "thread mismatch is counted");

    /* A different lua_State on the Career thread is refused. */
    mark((lua_State *)(uintptr_t)0x2000);
    before = ctr(0);
    long long state_skips = ctr(3);
    pump(600);
    CHECK(ctr(0) == before && ctr(3) > state_skips, "different lua_State is refused");

    /* Back to the original state on this thread: ticks resume. */
    mark(FAKE_L);
    before = ctr(0);
    pump(800);
    CHECK(ctr(0) > before, "ticks resume when Career events return");

    /* Lua errors are counted. */
    g_pcall_ret = 2;
    long long errs = ctr(1);
    pump(600);
    CHECK(ctr(1) > errs, "Lua errors are counted");
    g_pcall_ret = 0;

    hotkey(FAKE_L);
    char buf[4096];
    int n = status(buf, sizeof buf);
    CHECK(n > 0 && strstr(buf, "\"same_thread\": true") && strstr(buf, "\"hotkey_events\": 1"), "status JSON");

    /* ---- APC carrier (1.1.0) ---- */
    /* A pump-less Career thread that waits alertably gets the tick by APC. */
    long long apc0 = ctr(8), ticks0 = ctr(0), timer0 = ctr(9);
    long long queued_before = ctr(6), ran_before = ctr(7);
    run_thread(alert_thread, 2000);
    long long apc_ticks = ctr(8) - apc0;
    CHECK(apc_ticks >= 4, "APC ticks run on a pump-less Career thread that waits alertably");
    CHECK(ctr(0) - ticks0 == apc_ticks, "every tick on that thread came from the APC carrier");
    CHECK(ctr(9) == timer0, "the thread timer never fires without a message pump");
    CHECK(g_pcall_tid == g_alert_tid, "the APC tick entered Lua on the Career thread itself");
    /* Queued during this run but not run: at most the one in flight when the
     * thread exited. (APCs left on the earlier, never-alertable threads were
     * queued before this run.) */
    CHECK((ctr(6) - queued_before) - (ctr(7) - ran_before) <= 1, "APCs are delivered, not piled up");
    n = status(buf, sizeof buf);
    CHECK(n > 0 && strstr(buf, "\"carrier\": \"apc\"") && strstr(buf, "\"reason\": \"ok\""),
          "status JSON reports the APC carrier and ok");

    /* A tick that waits alertably inside "Lua" is never entered twice. */
    g_pcall_max_depth = 0;
    g_pcall_alert_ms = 300;
    long long apc1 = ctr(8);
    run_thread(alert_thread, 1500);
    g_pcall_alert_ms = 0;
    CHECK(ctr(8) > apc1, "APC ticks continue while each one waits alertably");
    CHECK(g_pcall_max_depth == 1, "Lua is never entered twice at once");

    /* Kill switches: DISABLE stops every tick, NO_APC only the APC carrier. */
    touch(dll_dir, L"DISABLE", 1);
    Sleep(1300);
    long long off0 = ctr(0);
    run_thread(alert_thread, 1000);
    CHECK(ctr(0) == off0, "DISABLE stops APC ticks within ~1 s");
    touch(dll_dir, L"DISABLE", 0);
    touch(dll_dir, L"NO_APC", 1);
    Sleep(1300);
    long long noapc0 = ctr(8);
    run_thread(alert_thread, 1000);
    CHECK(ctr(8) == noapc0, "NO_APC stops the APC carrier");
    touch(dll_dir, L"NO_APC", 0);
    Sleep(1300);
    long long back0 = ctr(8);
    run_thread(alert_thread, 1500);
    CHECK(ctr(8) > back0, "APC ticks resume when the kill switch is removed");

    /* Lua's switch (config.json "native_apc": false) turns it off for good. */
    no_apc(FAKE_L);
    long long luaoff0 = ctr(8), queued0 = ctr(6);
    run_thread(alert_thread, 1200);
    CHECK(ctr(8) == luaoff0, "lec_native_no_apc stops APC ticks");
    CHECK(ctr(6) - queued0 <= 1, "lec_native_no_apc stops queueing");

    printf(g_fail ? "\n%d CHECK(S) FAILED\n" : "\nALL HARNESS CHECKS PASSED\n", g_fail);
    return g_fail ? 1 : 0;
}
