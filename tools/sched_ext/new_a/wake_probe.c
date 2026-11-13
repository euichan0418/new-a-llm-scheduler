/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>

#define MAX_WORKERS 128
static pthread_barrier_t barrier;
static int narrow_cpu = -1;

struct affinity_trace {
	int tgid, tid, active;
	int cpu_before, cpu_after, result, error;
	int before_mask_result, after_mask_result;
	uint64_t request_begin_ns, request_return_ns, post_begin_ns, post_end_ns;
	cpu_set_t before, requested, after;
	int observations, violations, first_bad_cpu, first_bad_iteration, first_bad_mask_result;
	uint64_t first_bad_ns;
	cpu_set_t first_bad_mask;
};

static struct affinity_trace traces[MAX_WORKERS];

__attribute__((noinline, visibility("default")))
void llama_scx_phase_v1(uint32_t phase)
{
	__asm__ __volatile__("" : : "r"(phase) : "memory");
}

static uint64_t now_ns(void)
{
	struct timespec time;

	clock_gettime(CLOCK_MONOTONIC, &time);
	return (uint64_t)time.tv_sec * 1000000000ULL + (uint64_t)time.tv_nsec;
}

static void *worker(void *unused)
{
	struct affinity_trace *trace = &traces[(uintptr_t)unused];
	unsigned long observed[CPU_SETSIZE] = {};
	cpu_set_t allowed;
	int violations = 0;

	trace->tgid = getpid();
	trace->tid = (int)syscall(SYS_gettid);
	pthread_barrier_wait(&barrier);
	for (int i = 0; i < 160; i++) {
		struct timespec delay = { .tv_nsec = 10000000 };
		uint64_t end;
		int cpu;

		if (i == 80 && narrow_cpu >= 0) {
			CPU_ZERO(&allowed);
			CPU_SET(narrow_cpu, &allowed);
			trace->requested = allowed;
			trace->before_mask_result = sched_getaffinity(0, sizeof(trace->before), &trace->before);
			trace->cpu_before = sched_getcpu();
			trace->request_begin_ns = now_ns();
			trace->result = sched_setaffinity(0, sizeof(allowed), &allowed);
			trace->error = trace->result ? errno : 0;
			trace->request_return_ns = now_ns();
			trace->active = 1;
			trace->post_begin_ns = now_ns();
			trace->cpu_after = sched_getcpu();
			trace->after_mask_result = sched_getaffinity(0, sizeof(trace->after), &trace->after);
			trace->post_end_ns = now_ns();
			if (trace->result)
				return (void *)1;
		}
		while (nanosleep(&delay, &delay) && errno == EINTR)
			;
		cpu = sched_getcpu();
		int mask_result = sched_getaffinity(0, sizeof(allowed), &allowed);

		trace->observations++;
		if (mask_result || cpu < 0 || cpu >= CPU_SETSIZE || !CPU_ISSET(cpu, &allowed)) {
			violations++;
			trace->violations++;
			if (!trace->first_bad_ns) {
				trace->first_bad_ns = now_ns();
				trace->first_bad_cpu = cpu;
				trace->first_bad_iteration = i;
				trace->first_bad_mask_result = mask_result;
				if (!mask_result)
					trace->first_bad_mask = allowed;
			}
		} else
			observed[cpu]++;
		end = now_ns() + 1000000;
		while (now_ns() < end)
			__asm__ __volatile__("" ::: "memory");
	}
	flockfile(stdout);
	printf("worker iterations=160 violations=%d cpus=", violations);
	for (int cpu = 0; cpu < CPU_SETSIZE; cpu++)
		if (observed[cpu])
			printf("%d:%lu,", cpu, observed[cpu]);
	putchar('\n');
	funlockfile(stdout);
	return (void *)(uintptr_t)(violations != 0);
}

static void print_mask(const cpu_set_t *mask)
{
	int comma = 0;

	putchar('[');
	for (int cpu = 0; cpu < CPU_SETSIZE; cpu++) {
		if (CPU_ISSET(cpu, mask)) {
			printf("%s%d", comma ? "," : "", cpu);
			comma = 1;
		}
	}
	putchar(']');
}

static void print_trace(const struct affinity_trace *trace)
{
	if (!trace->active)
		return;
	printf("affinity_trace {\"clock\":\"CLOCK_MONOTONIC\",\"tgid\":%d,\"tid\":%d,\"sequence\":1,\"cpu_before\":%d,\"cpu_after\":%d,\"syscall_result\":%d,\"errno\":%d,\"before_mask_result\":%d,\"after_mask_result\":%d,\"request_begin_ns\":%" PRIu64 ",\"request_return_ns\":%" PRIu64 ",\"post_begin_ns\":%" PRIu64 ",\"post_end_ns\":%" PRIu64 ",\"observations\":%d,\"violations\":%d,\"first_bad_ns\":%" PRIu64 ",\"first_bad_cpu\":%d,\"first_bad_iteration\":%d,\"first_bad_mask_result\":%d,\"mask_before\":",
		trace->tgid, trace->tid, trace->cpu_before, trace->cpu_after, trace->result, trace->error,
		trace->before_mask_result, trace->after_mask_result, trace->request_begin_ns, trace->request_return_ns,
		trace->post_begin_ns, trace->post_end_ns, trace->observations, trace->violations, trace->first_bad_ns,
		trace->first_bad_cpu, trace->first_bad_iteration, trace->first_bad_mask_result);
	print_mask(&trace->before);
	printf(",\"mask_requested\":");
	print_mask(&trace->requested);
	printf(",\"mask_after\":");
	print_mask(&trace->after);
	printf(",\"first_bad_mask\":");
	print_mask(&trace->first_bad_mask);
	puts("}");
}

int main(int argc, char **argv)
{
	pthread_t workers[MAX_WORKERS];
	int threads, gate, failed = 0;
	char release;

	if (argc != 4)
		return 2;
	gate = atoi(argv[1]);
	threads = atoi(argv[2]);
	narrow_cpu = atoi(argv[3]);
	if (threads < 1 || threads > MAX_WORKERS || narrow_cpu >= CPU_SETSIZE)
		return 2;
	if (read(gate, &release, 1) != 1)
		return 3;
	close(gate);
	llama_scx_phase_v1(1);
	if (pthread_barrier_init(&barrier, NULL, (unsigned int)threads + 1))
		return 4;
	for (int i = 0; i < threads; i++)
		if (pthread_create(&workers[i], NULL, worker, (void *)(uintptr_t)i))
			_exit(5);
	pthread_barrier_wait(&barrier);
	for (int i = 0; i < threads; i++) {
		void *result;

		if (pthread_join(workers[i], &result) || result)
			failed = 1;
	}
	for (int i = 0; i < threads; i++)
		print_trace(&traces[i]);
	llama_scx_phase_v1(2);
	/* Keep DECODE observable for the external correctness observer. */
	usleep(500000);
	pthread_barrier_destroy(&barrier);
	return failed;
}
