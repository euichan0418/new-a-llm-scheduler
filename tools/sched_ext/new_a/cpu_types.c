/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE
#include <cpuid.h>
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>

/* Supply architectural type only. The Base detector owns physical topology. */
int main(int argc, char **argv)
{
	cpu_set_t original, one;
	int result = 0;

	if (sched_getaffinity(0, sizeof(original), &original))
		return 1;
	for (int i = 1; i < argc; i++) {
		unsigned int eax, ebx, ecx, edx, type = 0;
		char *end;
		unsigned long cpu;

		errno = 0;
		cpu = strtoul(argv[i], &end, 10);
		if (errno || end == argv[i] || *end || cpu >= CPU_SETSIZE || !CPU_ISSET(cpu, &original)) {
			result = 1;
			break;
		}
		CPU_ZERO(&one);
		CPU_SET(cpu, &one);
		if (sched_setaffinity(0, sizeof(one), &one)) {
			result = 1;
			break;
		}
		if (__get_cpuid_max(0, NULL) >= 0x1a) {
			__cpuid_count(7, 0, eax, ebx, ecx, edx);
			if (edx & (1U << 15)) {
				__cpuid_count(0x1a, 0, eax, ebx, ecx, edx);
				type = eax >> 24;
			}
		}
		printf("%lu %u\n", cpu, type);
	}
	if (sched_setaffinity(0, sizeof(original), &original))
		result = 1;
	return result;
}
