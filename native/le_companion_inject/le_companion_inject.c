/**
 * LECompanionInject.dll — inject-side companion module.
 *
 * Loaded as a native DLL (DllMain). Implements queue protocol only.
 * Does not reimplement FakeEAAC or replace FCLiveEditor.DLL.
 */
#ifndef LE_COMPANION_INJECT_EXPORTS
#define LE_COMPANION_INJECT_EXPORTS
#endif
#include "le_companion_inject.h"
#include "protocol_core.h"

#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#  include <windows.h>
#endif

static char g_queue_dir[LE_COMPANION_PATH_MAX] = {0};
static char g_last_result[512] = {0};

#ifdef _WIN32
BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved) {
    (void)hinst;
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
    }
    return TRUE;
}
#endif

LE_API int LECompanion_SetQueueDir(const char *path_utf8) {
    if (!path_utf8 || !path_utf8[0]) return 1;
    if (strlen(path_utf8) >= LE_COMPANION_PATH_MAX) return 2;
    snprintf(g_queue_dir, sizeof(g_queue_dir), "%s", path_utf8);
    /* normalize to backslash on Windows for CreateDirectory */
    for (char *p = g_queue_dir; *p; p++) {
        if (*p == '/') *p = '\\';
    }
    /* strip trailing slash */
    size_t n = strlen(g_queue_dir);
    while (n > 1 && (g_queue_dir[n - 1] == '\\' || g_queue_dir[n - 1] == '/')) {
        g_queue_dir[--n] = '\0';
    }
    return 0;
}

LE_API int LECompanion_GetQueueDir(char *buf, int buf_len) {
    if (!buf || buf_len < 2) return 1;
    if (!g_queue_dir[0]) {
        buf[0] = '\0';
        return 2;
    }
    snprintf(buf, (size_t)buf_len, "%s", g_queue_dir);
    return 0;
}

LE_API int LECompanion_Arm(void) {
    if (!g_queue_dir[0]) return 1;
    return pc_heartbeat(g_queue_dir, "arm") == 0 ? 0 : 2;
}

LE_API int LECompanion_EnableDryRun(void) {
    char path[LE_COMPANION_PATH_MAX];
    if (!g_queue_dir[0]) return 1;
    if (pc_ensure_queue_layout(g_queue_dir) != 0) return 2;
    pc_path_join(path, sizeof(path), g_queue_dir, PC_DRY_RUN);
    return pc_write_file(path, "dry_run=1\n", 11) == 0 ? 0 : 3;
}

LE_API int LECompanion_ProcessQueue(int force) {
    pc_drain_stats st;
    int n;
    if (!g_queue_dir[0]) return -1;
    n = pc_process_queue(g_queue_dir, force, &st);
    snprintf(g_last_result, sizeof(g_last_result), "%s", st.result_line);
    return n;
}

LE_API int LECompanion_GetLastResult(char *buf, int buf_len) {
    if (!buf || buf_len < 2) return 1;
    if (g_last_result[0]) {
        snprintf(buf, (size_t)buf_len, "%s", g_last_result);
        return 0;
    }
    /* fall back to file */
    if (g_queue_dir[0]) {
        char path[LE_COMPANION_PATH_MAX];
        char body[512];
        size_t blen = 0;
        pc_path_join(path, sizeof(path), g_queue_dir, PC_RESULT);
        if (pc_read_file(path, body, sizeof(body), &blen) == 0) {
            /* strip trailing newline */
            while (blen > 0 && (body[blen - 1] == '\n' || body[blen - 1] == '\r')) {
                body[--blen] = '\0';
            }
            snprintf(buf, (size_t)buf_len, "%s", body);
            return 0;
        }
    }
    buf[0] = '\0';
    return 2;
}

LE_API int LECompanion_ProtocolVersion(void) {
    return LE_COMPANION_PROTOCOL_VERSION;
}

LE_API int LECompanion_GetModuleInfo(char *buf, int buf_len) {
    if (!buf || buf_len < 2) return 1;
    snprintf(buf, (size_t)buf_len,
             "LECompanionInject/1.0 protocol=%d no-fakeeaac le-launcher-owns-ac",
             LE_COMPANION_PROTOCOL_VERSION);
    return 0;
}
