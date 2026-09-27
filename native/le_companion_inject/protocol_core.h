/* Internal queue protocol helpers (ANSI/UTF-8 paths). */
#pragma once

#include <stddef.h>

#define PC_PATH_MAX 1024
#define PC_PENDING "_pending.txt"
#define PC_RUN_NOW "_run_now.lua"
#define PC_WAKE "_wake.txt"
#define PC_ALIVE "_bridge_alive.txt"
#define PC_ARMED "_bridge_armed.txt"
#define PC_RESULT "_last_result.txt"
#define PC_JOB_STATUS "_job_status.txt"
#define PC_DONE "done"
/* Required for native dry ProcessQueue (protects production jobs). */
#define PC_DRY_RUN "_protocol_dry_run"

typedef struct {
    int processed;
    int ok;
    char last_name[256];
    char result_line[512];
    char note[128];
} pc_drain_stats;

void pc_path_join(char *out, size_t out_sz, const char *a, const char *b);
int pc_file_exists(const char *path);
int pc_read_file(const char *path, char *buf, size_t buf_sz, size_t *out_len);
int pc_write_file(const char *path, const char *data, size_t len);
int pc_delete_file(const char *path);
int pc_ensure_dir(const char *path);
int pc_ensure_queue_layout(const char *queue_dir);
int pc_heartbeat(const char *queue_dir, const char *note);
int pc_write_result(const char *queue_dir, const char *text);
int pc_finish_job(const char *queue_dir, const char *full_path, const char *name);
int pc_process_queue(const char *queue_dir, int force, pc_drain_stats *stats);
