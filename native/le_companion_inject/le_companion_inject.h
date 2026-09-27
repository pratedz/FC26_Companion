/**
 * LECompanionInject — inject-side protocol module for LE Profile Executor.
 *
 * This DLL implements the queue arm/drain control protocol (see docs/PROTOCOL.md).
 * It does NOT install/restore FakeEAAC and does NOT replace FCLiveEditor.DLL.
 * LE Launcher.exe remains the sole anticheat-swap + primary game inject path.
 *
 * Loadable via LoadLibrary / DllMain. External program issues jobs into queue/;
 * this module (or the in-LE Lua worker) drains them and writes result markers.
 */
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#ifdef LE_COMPANION_INJECT_EXPORTS
#  define LE_API __declspec(dllexport)
#else
#  define LE_API __declspec(dllimport)
#endif

#ifndef LE_API
#  define LE_API
#endif

/** Protocol version (must match docs/PROTOCOL.md and src/protocol.py). */
#define LE_COMPANION_PROTOCOL_VERSION 2

/** Max path length for queue directory. */
#define LE_COMPANION_PATH_MAX 1024

/**
 * Set absolute queue directory (UTF-8, forward or back slashes).
 * Returns 0 on success, non-zero on error.
 */
LE_API int LECompanion_SetQueueDir(const char *path_utf8);

/** Copy current queue dir into buf (NUL-terminated). Returns 0 on success. */
LE_API int LECompanion_GetQueueDir(char *buf, int buf_len);

/**
 * Arm inject-side: write _bridge_alive.txt + _bridge_armed.txt heartbeats.
 * Returns 0 on success.
 */
LE_API int LECompanion_Arm(void);

/**
 * Drain queue protocol (see PROTOCOL.md) — DRY RUN ONLY.
 * Requires queue/_protocol_dry_run marker; otherwise returns -2 and leaves jobs
 * untouched (prevents false-OK shred of production pending applies).
 * force != 0 ignores throttle (always full drain when allowed).
 * Returns number of jobs processed (>=0), -1 hard error, -2 dry_run_required.
 */
LE_API int LECompanion_ProcessQueue(int force);

/**
 * Write _protocol_dry_run marker into the current queue dir.
 * Returns 0 on success.
 */
LE_API int LECompanion_EnableDryRun(void);

/**
 * Copy last result line into buf. Returns 0 on success.
 */
LE_API int LECompanion_GetLastResult(char *buf, int buf_len);

/** Protocol version integer. */
LE_API int LECompanion_ProtocolVersion(void);

/**
 * Module identity string (NUL-terminated into buf).
 * e.g. "LECompanionInject/1.0 protocol=2 no-fakeeaac"
 */
LE_API int LECompanion_GetModuleInfo(char *buf, int buf_len);

#ifdef __cplusplus
}
#endif
