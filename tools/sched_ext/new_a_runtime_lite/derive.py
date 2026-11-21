# SPDX-License-Identifier: GPL-2.0
"""Derive the elapsed-only candidate without changing the reference sources."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = REPO / 'tools/sched_ext/new_a'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Reference anchor changed: ' + old[:80])
    return text.replace(old, new, 1)


def candidate_source(text):
    start = text.index('static __always_inline void observe_runtime(')
    end = text.index('\nvoid BPF_STRUCT_OPS(new_a_set_cpumask', start)
    block = text[start:end]
    block = replace_once(block, '\tu64 now = bpf_ktime_get_ns();\n', '')
    block = replace_once(block, 'NEW_A_CALLBACK_STOPPING, now, NULL);',
                         'NEW_A_CALLBACK_STOPPING, bpf_ktime_get_ns(), NULL);')
    block = replace_once(block, 'NEW_A_CALLBACK_RUNNING, now, NULL);',
                         'NEW_A_CALLBACK_RUNNING, bpf_ktime_get_ns(), NULL);')
    block = replace_once(block, '\t\ttask->start_ns = now;\n', '')
    block = replace_once(block, '\t\tif (task->start_ns)\n\t\t\tstats->runtime_ns += now - task->start_ns;\n\t\ttask->start_ns = 0;\n', '')
    return text[:start] + block + text[end:]


def guard_build_module():
    path = REPO / 'tools/sched_ext/support/prepare_build.py'
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('runtime_lite_guard_build', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generate_loader(output):
    output = Path(output)
    provenance = guard_build_module().generate_loader(output)
    text = (output / 'loader.c').read_text()
    anchor = '#else\n            fprintf(output, "{\\"instrumentation_mode\\":\\"FULL\\"'
    prefix = '#elif defined(FG_RUNTIME_LITE)\n            fprintf(output, "{\\"instrumentation_mode\\":\\"FULL_NO_ELAPSED_RESEARCH\\",\\"optional_metrics\\":{\\"runtime_ns\\":\\"NOT_AVAILABLE\\",\\"migrations\\":\\"COLLECTED\\"},\\"cpus\\":[");\n'
    text = replace_once(text, anchor, prefix + anchor)
    anchor = '#else\n                fprintf(output, ",\\"runtime_ns\\":%llu,\\"migrations\\":%llu"'
    prefix = '#elif defined(FG_RUNTIME_LITE)\n                fprintf(output, ",\\"runtime_ns\\":\\"NOT_AVAILABLE\\",\\"migrations\\":%llu", (unsigned long long)s->migrations);\n'
    text = replace_once(text, anchor, prefix + anchor)
    (output / 'loader.c').write_text(text)
    monitor = (output / 'monitor.h').read_text()
    monitor = replace_once(monitor, '(cpu >= 8 && cpu <= 14)', '(cpu >= 8 && cpu <= 11)')
    anchor = '#ifdef FG_GUARD\n        if (s->runtime_ns || s->migrations)'
    monitor = replace_once(monitor, anchor,
        '#ifdef FG_RUNTIME_LITE\n        if (s->runtime_ns) return analysis_fail("runtime-lite elapsed counter unexpectedly active");\n#endif\n' + anchor)
    (output / 'monitor.h').write_text(monitor)
    provenance['common_reference_and_candidate_monitor'] = 'Exact P4+E4 mask; historical source remains unchanged'
    provenance['candidate_export'] = 'FULL_NO_ELAPSED_RESEARCH; runtime unavailable; migrations collected'
    return provenance
