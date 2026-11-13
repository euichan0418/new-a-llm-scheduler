/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE
#include <bpf/bpf.h>
#include <linux/bpf.h>
#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* 읽기 전용 검사. 통계 활성화나 타 프로세스 FD 변경은 하지 않는다. */
static int numeric(const char *s)
{
    if (!*s) return 0;
    for (; *s; s++) if (*s < '0' || *s > '9') return 0;
    return 1;
}

static int references(unsigned int *count, unsigned int *scanned)
{
    DIR *proc = opendir("/proc");
    struct dirent *p, *f;
    if (!proc) return -1;
    for (;;) {
        char directory[128];
        errno = 0;
        p = readdir(proc);
        if (!p) { if (errno) goto fail; break; }
        if (!numeric(p->d_name)) continue;
        if (snprintf(directory, sizeof(directory), "/proc/%s/fd", p->d_name) >= (int)sizeof(directory)) goto fail;
        DIR *fds = opendir(directory);
        if (!fds) {
            if (errno == ENOENT || errno == ESRCH) continue;
            goto fail;
        }
        for (;;) {
            char name[384], target[4096];
            ssize_t n;
            errno = 0;
            f = readdir(fds);
            if (!f) { if (errno) { closedir(fds); goto fail; } break; }
            if (!numeric(f->d_name)) continue;
            if (++*scanned > 262144) { closedir(fds); goto fail; }
            if (snprintf(name, sizeof(name), "%s/%s", directory, f->d_name) >= (int)sizeof(name)) { closedir(fds); goto fail; }
            n = readlink(name, target, sizeof(target)-1);
            if (n < 0) {
                if (errno == ENOENT || errno == ESRCH) continue;
                closedir(fds); goto fail;
            }
            if ((size_t)n == sizeof(target)-1) { closedir(fds); goto fail; }
            target[n] = 0;
            if (!strncmp(target, "anon_inode:", 11) && strstr(target, "bpf-stats")) {
                if (*count < 8) fprintf(stderr, "발견한 외부 stats FD: pid=%s fd=%s\n", p->d_name, f->d_name);
                (*count)++;
            }
        }
        closedir(fds);
    }
    closedir(proc);
    return 0;
fail:
    closedir(proc);
    return -1;
}

int main(int argc, char **argv)
{
    unsigned int refs = 0, scanned = 0;
    int enabled = -1;
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("읽기 전용 통계 OFF 검사: --global 또는 program ID 최대5개. BPF 로딩/통계 활성화 없음.");
        return 0;
    }
    if (getuid() || argc < 2 || argc > 6) { fputs("터미널 인증 이후 root로 읽기 전용 검사만 실행해야 합니다\n", stderr); return 2; }
    FILE *s = fopen("/proc/sys/kernel/bpf_stats_enabled", "r");
    if (!s) return 2;
    int parsed = fscanf(s, "%d", &enabled);
    fclose(s);
    if (parsed != 1 || enabled != 0 || references(&refs, &scanned) || refs) {
        fputs("BPF 통계가 켜져 있거나 외부 stats FD 또는 읽기 실패를 발견했습니다. 상태를 변경하지 않습니다\n", stderr);
        return 3;
    }
    int global = argc == 2 && !strcmp(argv[1], "--global");
    printf("{\"read_only\":true,\"enable_stats_called\":false,\"sysctl\":%d,\"stats_fd_references\":%u,\"fds_scanned\":%u,\"programs\":[", enabled, refs, scanned);
    for (int i = 1; !global && i < argc; i++) {
        struct bpf_prog_info info = {0};
        __u32 size = sizeof(info);
        char *end = NULL;
        unsigned long id = strtoul(argv[i], &end, 10);
        if (!numeric(argv[i]) || *end || !id || id > 0xffffffffUL) return 2;
        int fd = bpf_prog_get_fd_by_id((__u32)id);
        if (fd < 0) return 4;
        int rc = bpf_obj_get_info_by_fd(fd, &info, &size);
        close(fd);
        if (rc || size < offsetof(struct bpf_prog_info, run_cnt) + sizeof(info.run_cnt) || info.id != id || info.type != BPF_PROG_TYPE_STRUCT_OPS || info.run_cnt || info.run_time_ns) {
            fputs("대상 struct_ops Identity 또는 통계 OFF 조건을 확인하지 못했습니다\n", stderr);
            fprintf(stderr, "program=%lu info_size=%u type=%u run_cnt=%" PRIu64 " run_time_ns=%" PRIu64 "\n", id, size, info.type, (uint64_t)info.run_cnt, (uint64_t)info.run_time_ns);
            return 4;
        }
        printf("%s{\"id\":%u,\"name\":\"%.15s\",\"struct_ops_type\":true,\"btf_id\":%u,\"run_cnt\":%" PRIu64 ",\"run_time_ns\":%" PRIu64 ",\"tag\":\"", i == 1 ? "" : ",", info.id, info.name, info.btf_id, (uint64_t)info.run_cnt, (uint64_t)info.run_time_ns);
        for (unsigned int j = 0; j < BPF_TAG_SIZE; j++) printf("%02x", info.tag[j]);
        printf("\"}");
    }
    puts("]}");
    return 0;
}
