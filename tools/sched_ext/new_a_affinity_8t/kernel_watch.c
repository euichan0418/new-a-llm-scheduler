/* SPDX-License-Identifier: GPL-2.0 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define FG_PARSER_ONLY
#include "monitor.h"

/* 기존 FULL 로더와 같은 분류기로 커널 경고만 읽는다. BPF는 사용하지 않는다. */
static unsigned long long records, last;
static int have_last;
static int drain(int fd)
{
    char buffer[65536];
    for (int i = 0; i < 4096; i++) {
        struct fg_kernel_record record;
        ssize_t size = read(fd, buffer, sizeof(buffer) - 1);
        if (size < 0 && errno == EINTR) { i--; continue; }
        if (size < 0 && errno == EAGAIN) return 0;
        if (size <= 0) return 1;
        buffer[size] = 0;
        if (fg_parse_kernel_record(buffer, &record)) return 1;
        if (have_last && record.sequence != last + 1) return 1;
        have_last = 1; last = record.sequence; records++;
        if (record.bad) { fprintf(stderr, "%s", buffer); return 1; }
    }
    return 1;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--help")) {
        puts("읽기 전용 커널 감시: stdin STOP, stdout READY 및 최종 JSON. BPF 미사용.");
        return 0;
    }
    if (argc != 1 || getuid() || geteuid()) return 2;
    int fd = open("/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0 || lseek(fd, 0, SEEK_END) < 0) { if (fd >= 0) close(fd); return 1; }
    puts("READY"); fflush(stdout);
    int failed = 0, stopped = 0;
    while (!stopped) {
        if (drain(fd)) { failed = 1; break; }
        struct pollfd input = { .fd = STDIN_FILENO, .events = POLLIN };
        int n = poll(&input, 1, 1000);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { failed = 1; break; }
        if (n > 0) {
            char command[16];
            if (!fgets(command, sizeof(command), stdin) || strcmp(command, "STOP\n")) failed = 1;
            stopped = 1;
        }
    }
    if (drain(fd)) failed = 1;
    close(fd);
    printf("{\"status\":\"%s\",\"kernel_opened\":true,\"records\":%llu,\"bpf_loaded\":false,\"fd_closed\":true}\n", failed ? "FAIL" : "PASS", records);
    fflush(stdout);
    return failed;
}
