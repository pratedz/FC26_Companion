/**
 * Queue protocol consumer — mirrors docs/PROTOCOL.md / src/protocol.py.
 * File I/O only. No FakeEAAC. No process inject.
 */
#include "protocol_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#  include <windows.h>
#  include <direct.h>
#  define PATH_SEP '\\'
#else
#  include <sys/stat.h>
#  include <unistd.h>
#  define PATH_SEP '/'
#endif

void pc_path_join(char *out, size_t out_sz, const char *a, const char *b) {
    if (!out || out_sz == 0) return;
    out[0] = '\0';
    if (!a || !a[0]) {
        if (b) snprintf(out, out_sz, "%s", b);
        return;
    }
    size_t la = strlen(a);
    int need_sep = 1;
    if (la > 0) {
        char last = a[la - 1];
        if (last == '/' || last == '\\') need_sep = 0;
    }
    if (need_sep)
        snprintf(out, out_sz, "%s%c%s", a, PATH_SEP, b ? b : "");
    else
        snprintf(out, out_sz, "%s%s", a, b ? b : "");
}

int pc_file_exists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

int pc_read_file(const char *path, char *buf, size_t buf_sz, size_t *out_len) {
    if (out_len) *out_len = 0;
    if (!path || !buf || buf_sz < 2) return -1;
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    size_t n = fread(buf, 1, buf_sz - 1, f);
    fclose(f);
    buf[n] = '\0';
    if (out_len) *out_len = n;
    return 0;
}

int pc_write_file(const char *path, const char *data, size_t len) {
    if (!path) return -1;
    FILE *f = fopen(path, "wb");
    if (!f) return -1;
    if (data && len > 0) {
        if (fwrite(data, 1, len, f) != len) {
            fclose(f);
            return -1;
        }
    }
    fclose(f);
    return 0;
}

int pc_delete_file(const char *path) {
    if (!path) return -1;
#ifdef _WIN32
    DeleteFileA(path);
#else
    unlink(path);
#endif
    return 0;
}

int pc_ensure_dir(const char *path) {
    if (!path || !path[0]) return -1;
#ifdef _WIN32
    /* CreateDirectoryA is non-recursive; build path segments. */
    char tmp[PC_PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", path);
    size_t n = strlen(tmp);
    for (size_t i = 0; i < n; i++) {
        if (tmp[i] == '/') tmp[i] = '\\';
    }
    for (size_t i = 3; i < n; i++) {
        if (tmp[i] == '\\') {
            tmp[i] = '\0';
            CreateDirectoryA(tmp, NULL);
            tmp[i] = '\\';
        }
    }
    CreateDirectoryA(tmp, NULL);
    return 0;
#else
    mkdir(path, 0755);
    return 0;
#endif
}

int pc_ensure_queue_layout(const char *queue_dir) {
    char done[PC_PATH_MAX];
    char pending[PC_PATH_MAX];
    if (!queue_dir || !queue_dir[0]) return -1;
    pc_ensure_dir(queue_dir);
    pc_path_join(done, sizeof(done), queue_dir, PC_DONE);
    pc_ensure_dir(done);
    pc_path_join(pending, sizeof(pending), queue_dir, PC_PENDING);
    if (!pc_file_exists(pending)) {
        pc_write_file(pending, "", 0);
    }
    return 0;
}

int pc_heartbeat(const char *queue_dir, const char *note) {
    char path[PC_PATH_MAX];
    char msg[512];
    long ts = (long)time(NULL);
    if (pc_ensure_queue_layout(queue_dir) != 0) return -1;
    snprintf(msg, sizeof(msg), "alive %ld %s\n", ts, note ? note : "");
    pc_path_join(path, sizeof(path), queue_dir, PC_ALIVE);
    if (pc_write_file(path, msg, strlen(msg)) != 0) return -1;
    snprintf(msg, sizeof(msg), "armed %ld\n", ts);
    pc_path_join(path, sizeof(path), queue_dir, PC_ARMED);
    return pc_write_file(path, msg, strlen(msg));
}

int pc_write_result(const char *queue_dir, const char *text) {
    char path[PC_PATH_MAX];
    char line[600];
    snprintf(line, sizeof(line), "%s\n", text ? text : "");
    pc_path_join(path, sizeof(path), queue_dir, PC_RESULT);
    return pc_write_file(path, line, strlen(line));
}

int pc_finish_job(const char *queue_dir, const char *full_path, const char *name) {
    char done_dir[PC_PATH_MAX];
    char dest[PC_PATH_MAX];
    char body[65536];
    size_t blen = 0;
    pc_path_join(done_dir, sizeof(done_dir), queue_dir, PC_DONE);
    pc_ensure_dir(done_dir);
    pc_path_join(dest, sizeof(dest), done_dir, name);
    if (pc_file_exists(dest)) {
        char alt[PC_PATH_MAX];
        snprintf(alt, sizeof(alt), "%ld_%s", (long)time(NULL), name);
        pc_path_join(dest, sizeof(dest), done_dir, alt);
    }
    if (pc_read_file(full_path, body, sizeof(body), &blen) == 0) {
        pc_write_file(dest, body, blen);
    }
    pc_delete_file(full_path);
    return 0;
}

/* Job "execute" without LE: only when dry-run marker present (see pc_process_queue). */
static int pc_exec_job(const char *body, size_t len, const char *name) {
    (void)name;
    return (body != NULL && len > 0) ? 1 : 0;
}

static int pc_dry_run_allowed(const char *queue_dir) {
    char path[PC_PATH_MAX];
    pc_path_join(path, sizeof(path), queue_dir, PC_DRY_RUN);
    return pc_file_exists(path);
}

static int pc_read_pending_list(const char *queue_dir, char names[][256], int max_n) {
    char path[PC_PATH_MAX];
    char buf[16384];
    size_t blen = 0;
    int count = 0;
    pc_path_join(path, sizeof(path), queue_dir, PC_PENDING);
    if (pc_read_file(path, buf, sizeof(buf), &blen) != 0) return 0;
    char *p = buf;
    while (*p && count < max_n) {
        while (*p == '\r' || *p == '\n' || *p == ' ' || *p == '\t') p++;
        if (!*p) break;
        char *start = p;
        while (*p && *p != '\r' && *p != '\n') p++;
        size_t n = (size_t)(p - start);
        if (n > 0 && n < 255 && start[0] != '_') {
            int is_lua = 0;
            if (n >= 4) {
                const char *ext = start + n - 4;
                if ((ext[0] == '.' || ext[0] == '.') &&
                    (ext[1] == 'l' || ext[1] == 'L') &&
                    (ext[2] == 'u' || ext[2] == 'U') &&
                    (ext[3] == 'a' || ext[3] == 'A'))
                    is_lua = 1;
            }
            if (is_lua) {
                memcpy(names[count], start, n);
                names[count][n] = '\0';
                /* dedupe */
                int dup = 0;
                for (int i = 0; i < count; i++) {
                    if (strcmp(names[i], names[count]) == 0) {
                        dup = 1;
                        break;
                    }
                }
                if (!dup) count++;
            }
        }
    }
    return count;
}

int pc_process_queue(const char *queue_dir, int force, pc_drain_stats *stats) {
    char path[PC_PATH_MAX];
    char body[65536];
    char run_now_body[65536];
    size_t blen = 0, rn_len = 0;
    char pending_names[64][256];
    char stuck[64][256];
    int n_pending = 0, n_stuck = 0;
    int run_now_ran = 0;
    (void)force;

    if (!stats) return -1;
    memset(stats, 0, sizeof(*stats));

    if (pc_ensure_queue_layout(queue_dir) != 0) {
        snprintf(stats->note, sizeof(stats->note), "cannot_write_queue");
        return -1;
    }
    /* Refuse to shred production jobs without explicit dry-run marker. */
    if (!pc_dry_run_allowed(queue_dir)) {
        snprintf(stats->note, sizeof(stats->note), "dry_run_required");
        snprintf(stats->result_line, sizeof(stats->result_line), "FAIL dry_run_required");
        pc_write_result(queue_dir, stats->result_line);
        pc_heartbeat(queue_dir, "dry_run_blocked");
        return -2;
    }
    if (pc_heartbeat(queue_dir, "tick") != 0) {
        snprintf(stats->note, sizeof(stats->note), "cannot_write_queue");
        pc_write_result(queue_dir, "FAIL cannot_write_queue");
        snprintf(stats->result_line, sizeof(stats->result_line), "FAIL cannot_write_queue");
        return -1;
    }

    /* 1) _run_now.lua first */
    pc_path_join(path, sizeof(path), queue_dir, PC_RUN_NOW);
    if (pc_file_exists(path)) {
        if (pc_read_file(path, run_now_body, sizeof(run_now_body), &rn_len) != 0) {
            run_now_body[0] = '\0';
            rn_len = 0;
        }
        int ok = pc_exec_job(run_now_body, rn_len, PC_RUN_NOW);
        pc_finish_job(queue_dir, path, PC_RUN_NOW);
        stats->processed++;
        snprintf(stats->last_name, sizeof(stats->last_name), "%s", PC_RUN_NOW);
        run_now_ran = 1;
        if (ok) stats->ok++;
    }

    /* 2) pending + wake */
    n_pending = pc_read_pending_list(queue_dir, pending_names, 64);
    pc_path_join(path, sizeof(path), queue_dir, PC_WAKE);
    if (pc_file_exists(path)) {
        char wake[4096];
        size_t wlen = 0;
        if (pc_read_file(path, wake, sizeof(wake), &wlen) == 0) {
            char *line = wake;
            while (*line && n_pending < 64) {
                while (*line == '\r' || *line == '\n') line++;
                if (!*line) break;
                char *eol = line;
                while (*eol && *eol != '\r' && *eol != '\n') eol++;
                char saved = *eol;
                *eol = '\0';
                const char *wname = NULL;
                if (strncmp(line, "wake ", 5) == 0)
                    wname = line + 5;
                else if (strstr(line, ".lua"))
                    wname = line;
                if (wname) {
                    while (*wname == ' ') wname++;
                    if (wname[0] && wname[0] != '_') {
                        int found = 0;
                        for (int i = 0; i < n_pending; i++) {
                            if (strcmp(pending_names[i], wname) == 0) {
                                found = 1;
                                break;
                            }
                        }
                        if (!found) {
                            snprintf(pending_names[n_pending], 256, "%s", wname);
                            n_pending++;
                        }
                    }
                }
                *eol = saved;
                line = eol;
                if (*line) line++;
            }
        }
    }

    for (int i = 0; i < n_pending; i++) {
        const char *name = pending_names[i];
        char full[PC_PATH_MAX];
        pc_path_join(full, sizeof(full), queue_dir, name);
        if (!pc_file_exists(full)) continue;
        if (pc_read_file(full, body, sizeof(body), &blen) != 0) {
            body[0] = '\0';
            blen = 0;
        }
        if (run_now_ran && rn_len == blen && blen > 0 &&
            memcmp(run_now_body, body, blen) == 0) {
            pc_finish_job(queue_dir, full, name);
            if (pc_file_exists(full) && n_stuck < 64) {
                snprintf(stuck[n_stuck++], 256, "%s", name);
            }
            continue;
        }
        int ok = pc_exec_job(body, blen, name);
        pc_finish_job(queue_dir, full, name);
        stats->processed++;
        snprintf(stats->last_name, sizeof(stats->last_name), "%s", name);
        if (ok) stats->ok++;
        if (pc_file_exists(full) && n_stuck < 64) {
            snprintf(stuck[n_stuck++], 256, "%s", name);
        }
    }

    /* rewrite pending */
    {
        char pending_path[PC_PATH_MAX];
        char plist[8192];
        size_t off = 0;
        pc_path_join(pending_path, sizeof(pending_path), queue_dir, PC_PENDING);
        plist[0] = '\0';
        for (int i = 0; i < n_stuck && off + 260 < sizeof(plist); i++) {
            int wr = snprintf(plist + off, sizeof(plist) - off, "%s\n", stuck[i]);
            if (wr > 0) off += (size_t)wr;
        }
        pc_write_file(pending_path, plist, strlen(plist));
    }
    pc_path_join(path, sizeof(path), queue_dir, PC_WAKE);
    pc_delete_file(path);

    {
        char note[64];
        snprintf(note, sizeof(note), "n=%d ok=%d", stats->processed, stats->ok);
        pc_heartbeat(queue_dir, note);
    }

    if (stats->processed > 0) {
        if (stats->ok < stats->processed) {
            snprintf(stats->result_line, sizeof(stats->result_line),
                     "FAIL processed=%d ok=%d last=%s",
                     stats->processed, stats->ok, stats->last_name);
        } else {
            snprintf(stats->result_line, sizeof(stats->result_line),
                     "OK processed=%d ok=%d last=%s",
                     stats->processed, stats->ok, stats->last_name);
        }
    } else if (n_stuck > 0) {
        snprintf(stats->result_line, sizeof(stats->result_line),
                 "FAIL stuck jobs still on disk");
    } else {
        snprintf(stats->result_line, sizeof(stats->result_line),
                 "OK idle queue_empty");
    }
    pc_write_result(queue_dir, stats->result_line);

    {
        char stpath[PC_PATH_MAX];
        char stline[600];
        snprintf(stline, sizeof(stline), "%s\t%s\n", stats->last_name, stats->result_line);
        pc_path_join(stpath, sizeof(stpath), queue_dir, PC_JOB_STATUS);
        pc_write_file(stpath, stline, strlen(stline));
    }
    return stats->processed;
}
