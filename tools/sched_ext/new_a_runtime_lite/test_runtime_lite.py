# SPDX-License-Identifier: GPL-2.0
"""Pure evidence tests and short native mocks; no BPF load or server execution."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from derive import BASE, HERE, REPO, candidate_source, generate_loader
from build import verify_sources
import validation as v
from prepare_study import plans


def diagnostic(policy='RUNTIME_LITE'):
    lite = policy == 'RUNTIME_LITE'
    rows = []
    for cpu in range(16):
        active = cpu in [0, 8]
        rows.append(dict(cpu=cpu, selected=[10, 4, 0, 0] if cpu == 0 else [0]*4,
            running=4 if active else 0, stopping=4 if active else 0,
            runtime_ns=v.NA if lite else 1000 if active else 0, migrations=2 if active else 0,
            mask_violations=0, tracking_failures=0, phase_seen=6 if cpu == 0 else 0,
            events_attempted=0, events_dropped=0))
    return dict(instrumentation_mode='FULL_NO_ELAPSED_RESEARCH' if lite else 'FULL',
        optional_metrics=dict(runtime_ns=v.NA if lite else 'COLLECTED', migrations='COLLECTED'),
        cpus=rows, event_schema=2, mask_cpu_limit=16, events_export_error=0, events=[],
        live_monitor=dict(samples=3, failures=0, kernel_opened=True, status='PASS'))


class SourceTests(unittest.TestCase):
    def test_pinned_reference_and_single_derivation(self):
        full, lite = verify_sources()
        self.assertEqual(candidate_source(full), lite)
        start = 'static __always_inline void observe_runtime('
        end = '\nvoid BPF_STRUCT_OPS(new_a_set_cpumask'
        self.assertEqual(full.split(start)[0], lite.split(start)[0])
        self.assertEqual(full.split(end)[1], lite.split(end)[1])

    def test_elapsed_only_not_migration_or_task_storage(self):
        full, lite = verify_sources()
        block = lite[lite.index('static __always_inline void observe_runtime('):lite.index('\nvoid BPF_STRUCT_OPS(new_a_set_cpumask')]
        self.assertNotIn('start_ns', block)
        self.assertNotIn('runtime_ns', block)
        self.assertNotIn('u64 now', block)
        for token in ['bpf_task_storage_get', 'BPF_LOCAL_STORAGE_GET_F_CREATE', 'stats->migrations++',
                      'stats->tracking_failures++', 'stats->mask_violations++', 'task->last_cpu_plus_one = cpu + 1']:
            self.assertEqual(full.count(token), lite.count(token))
        self.assertEqual(block.count('bpf_ktime_get_ns()'), 2)

    def test_reference_drift_is_rejected(self):
        text = (BASE / 'llama_new_a.bpf.c').read_text().replace('task->start_ns = now;', 'task->start_ns = 1;')
        with self.assertRaises(ValueError):
            candidate_source(text)

    def test_loader_and_common_monitor_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate_loader(tmp)
            text = (Path(tmp) / 'loader.c').read_text()
            monitor = (Path(tmp) / 'monitor.h').read_text()
            self.assertIn('FULL_NO_ELAPSED_RESEARCH', text)
            self.assertIn('FG_RUNTIME_LITE', text)
            self.assertIn('migrations\\\":%llu', text)
            self.assertIn('cpu <= 11', monitor)
            self.assertNotIn('cpu <= 14', monitor)
            self.assertIn('s->mask_violations || s->tracking_failures || s->events_dropped', monitor)
            self.assertIn('if (s->runtime_ns)', monitor)
            self.assertEqual(text.count('analysis_monitor_close();'), 1)
            self.assertIn('O_RDONLY | O_NONBLOCK | O_CLOEXEC', monitor)


class EvidenceTests(unittest.TestCase):
    def test_lite_is_unavailable_but_migrations_collected(self):
        result = v.diagnostics(diagnostic(), 'RUNTIME_LITE')
        self.assertEqual(result['task_observed_runtime_ns'], v.NA)
        self.assertEqual(result['observed_cpu_changes'], 4)

    def test_full_contract_remains_full(self):
        self.assertEqual(v.diagnostics(diagnostic('FULL'), 'FULL')['task_observed_runtime_ns'], 2000)
        with self.assertRaises(ValueError):
            v.diagnostics(diagnostic(), 'FULL')
        value = diagnostic('FULL')
        for row in value['cpus']:
            row['runtime_ns'] = 0
        with self.assertRaises(ValueError):
            v.diagnostics(value, 'FULL')

    def test_na_is_not_numeric_zero_or_null(self):
        for replacement in [0, None, False, 1]:
            with self.subTest(value=replacement):
                value = diagnostic()
                value['cpus'][0]['runtime_ns'] = replacement
                with self.assertRaises(ValueError):
                    v.diagnostics(value, 'RUNTIME_LITE')

    def test_migration_must_remain_measured(self):
        for replacement in [v.NA, None, False, -1]:
            with self.subTest(value=replacement):
                value = diagnostic()
                value['cpus'][0]['migrations'] = replacement
                with self.assertRaises(ValueError):
                    v.diagnostics(value, 'RUNTIME_LITE')

    def test_each_mandatory_failure_is_rejected(self):
        for failure in ['mask', 'tracking', 'drop', 'outside', 'e12', 'missing', 'duplicate', 'balance',
                        'phase', 'smt', 'kernel', 'samples', 'schema', 'event_count', 'export', 'mode', 'availability']:
            with self.subTest(failure=failure):
                value = diagnostic()
                if failure in ['mask', 'tracking', 'drop']:
                    value['cpus'][0][{'mask':'mask_violations','tracking':'tracking_failures','drop':'events_dropped'}[failure]] = 1
                elif failure in ['outside', 'e12']:
                    cpu = 1 if failure == 'outside' else 12
                    value['cpus'][cpu]['running'] = value['cpus'][cpu]['stopping'] = 1
                elif failure == 'missing': value['cpus'].pop()
                elif failure == 'duplicate': value['cpus'][-1]['cpu'] = 0
                elif failure == 'balance': value['cpus'][0]['stopping'] += 1
                elif failure == 'phase': value['cpus'][0]['phase_seen'] = 0
                elif failure == 'smt': value['cpus'][0]['selected'][2] = 1
                elif failure == 'kernel': value['live_monitor']['failures'] = 1
                elif failure == 'samples': value['live_monitor']['samples'] = 1
                elif failure == 'schema': value['event_schema'] = 1
                elif failure == 'event_count': value['cpus'][0]['events_attempted'] = 1
                elif failure == 'export': value['events_export_error'] = 1
                elif failure == 'mode': value['instrumentation_mode'] = 'GUARD_NO_DETAIL'
                else: value['optional_metrics']['migrations'] = v.NA
                with self.assertRaises(ValueError):
                    v.diagnostics(value, 'RUNTIME_LITE')

    def test_error_event_not_accepted_as_success(self):
        for kind in [1, 2]:
            value = diagnostic()
            value['cpus'][0]['events_attempted'] = 1
            value['events'] = [dict(kind=kind, allowed_complete=True, regular_complete=True, requested_complete=True)]
            with self.assertRaises(ValueError):
                v.diagnostics(value, 'RUNTIME_LITE')

    def test_legacy_full_validator_is_unchanged_and_rejects_lite(self):
        directory = REPO / 'tools/sched_ext/new_a_full_guard'
        sys.path.insert(0, str(directory))
        spec = importlib.util.spec_from_file_location('runtime_lite_legacy_validation', directory / 'fg_validation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertGreater(module.diagnostics(diagnostic('FULL'), 'FULL')['task_observed_runtime_ns'], 0)
        with self.assertRaises(ValueError):
            module.diagnostics(diagnostic(), 'FULL')


class StudyTests(unittest.TestCase):
    def test_fresh_audit_and_clean_for_all_three_cells(self):
        visits = plans()['FUNCTIONAL']
        self.assertEqual(len(visits), 6)
        self.assertEqual({r['id'] for r in visits}, {c+'-'+m for c in ['C48','E48','L48'] for m in ['audit','clean']})
        self.assertTrue(all(r['performance_sample'] is False for r in visits))

    def test_exact_two_block_order_and_request_counts(self):
        visits = plans()['PERFORMANCE']
        self.assertEqual([r['cell'] for r in visits], ['C48','E48','L48','L48','E48','C48'])
        self.assertEqual(sum(r['warmups']+r['measured_requests'] for r in visits), 18)
        for row in visits:
            self.assertEqual((row['compute'], row['batch'], row['http']), (4,8,2))
            self.assertEqual(set(row['cpus']), v.SERVER_CPUS)


class NativeTests(unittest.TestCase):
    def test_real_observer_branches_with_mock_helpers(self):
        full, lite = verify_sources()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name, source in [('E', full), ('L', lite)]:
                start = source.index('static __always_inline void observe_runtime(')
                end = source.index('\nvoid BPF_STRUCT_OPS(new_a_set_cpumask', start)
                block = source[start:end]
                prefix = r'''
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#define NEW_A_DIAGNOSTICS
#include "new_a.h"
typedef __u32 u32;
typedef __u64 u64;
#undef __always_inline
#define __always_inline inline
#define BPF_LOCAL_STORAGE_GET_F_CREATE 1
struct task_struct { void *cpus_ptr; };
struct new_a_task_observation { u64 start_ns; u32 last_cpu_plus_one; };
static int new_a_stats, new_a_tasks, cpu, clock_calls, storage_calls, missing_stats, missing_task, allowed = 1;
static int event_count, event_kind, event_callback;
static u64 event_time;
static struct new_a_stats stats;
static struct new_a_task_observation observation;
static u32 bpf_get_smp_processor_id(void) { return cpu; }
static void *bpf_map_lookup_elem(void *map, void *key) { (void)map; (void)key; return missing_stats ? NULL : &stats; }
static void *bpf_task_storage_get(void *map, void *p, void *value, int flags) {
    (void)map; (void)p; (void)value; assert(flags == 1); storage_calls++;
    return missing_task ? NULL : &observation;
}
static u64 bpf_ktime_get_ns(void) { return 100 + 10 * ++clock_calls; }
static bool bpf_cpumask_test_cpu(u32 c, void *mask) { (void)c; (void)mask; return allowed; }
static void observe_event(struct task_struct *p, struct new_a_stats *s, u32 kind, u32 callback, u64 now, void *requested) {
    (void)p; (void)s; (void)requested;
    event_count++; event_kind=kind; event_callback=callback; event_time=now;
}
static void reset(void) {
    memset(&stats,0,sizeof(stats)); memset(&observation,0,sizeof(observation));
    cpu=clock_calls=storage_calls=missing_stats=missing_task=event_count=0; allowed=1;
}
'''
                suffix = r'''
int main(void) {
    struct task_struct p = {0};
    reset();
    observe_runtime(&p,true); observe_runtime(&p,false);
    cpu=8; observe_runtime(&p,true); observe_runtime(&p,false);
    assert(stats.running==2 && stats.stopping==2 && stats.migrations==1);
    assert(storage_calls==4 && observation.last_cpu_plus_one==9 && event_count==0);
#ifdef LITE_TEST
    assert(clock_calls==0 && stats.runtime_ns==0 && observation.start_ns==0);
#else
    assert(clock_calls==4 && stats.runtime_ns==20 && observation.start_ns==0);
#endif
    for(int running=0;running<=1;running++) {
        reset(); missing_task=1; observe_runtime(&p,running);
        assert(stats.tracking_failures==1 && event_count==1 && event_kind==NEW_A_EVENT_TRACKING_FAILURE);
        assert(event_time>0 && event_callback==(running?NEW_A_CALLBACK_RUNNING:NEW_A_CALLBACK_STOPPING));
    }
    reset(); allowed=0; observe_runtime(&p,true);
    assert(stats.mask_violations==1 && event_count==1 && event_kind==NEW_A_EVENT_MASK_VIOLATION);
    assert(event_time>0 && event_callback==NEW_A_CALLBACK_RUNNING && stats.running==1);
    reset(); missing_stats=1; observe_runtime(&p,true);
    assert(event_count==0 && storage_calls==0);
    return 0;
}
'''
                path = directory / (name + '.c')
                path.write_text(prefix + block + suffix)
                binary = directory / name
                subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I'+str(BASE),
                    *(['-DLITE_TEST'] if name == 'L' else []), str(path), '-o', str(binary)], check=True, timeout=20)
                subprocess.run([str(binary)], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
