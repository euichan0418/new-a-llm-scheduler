#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Bounded, nonprivileged compilation and static comparison. Never loads BPF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import struct

sys.path.insert(0, str(Path(__file__).resolve().parent))
from derive import BASE, HERE, REPO, candidate_source, generate_loader, guard_build_module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(path):
    out={}
    for name,s in guard_build_module().old_build.elf_sections(path).items():
        if not s['flags'] & 4:continue
        raw=s['raw'];calls={}
        for off in range(0,len(raw),8):
            op,regs,_offset,imm=struct.unpack_from('<BBhi',raw,off)
            if op==0x85:
                key='helper_'+str(imm) if regs>>4==0 else 'relocated_or_subprogram_call'
                calls[key]=calls.get(key,0)+1
        out[name]=dict(bytes=len(raw),instruction_slots=len(raw)//8,static_call_sites=calls,sha256=hashlib.sha256(raw).hexdigest())
    return out

def verify_sources():
    pins = json.loads((HERE / 'reference-sources.json').read_text())
    for name, digest in pins.items():
        if sha(REPO / name) != digest:
            raise ValueError('Reference changed; explicit review required: ' + name)
    full = (BASE / 'llama_new_a.bpf.c').read_text()
    lite = (HERE / 'llama_new_a_runtime_lite.bpf.c').read_text()
    if lite != candidate_source(full):
        raise ValueError('Candidate differs from the elapsed-only derivation')
    return full, lite


def host():
    result = dict(kernel=platform.release(), uid=os.getuid(), power={}, thermal={})
    for directory in Path('/sys/class/power_supply').glob('*'):
        result['power'][directory.name] = {n: (directory / n).read_text().strip()
            for n in ['type', 'online', 'status', 'capacity'] if (directory / n).exists()}
    for directory in Path('/sys/class/thermal').glob('thermal_zone*'):
        try:
            result['thermal'][directory.name] = dict(type=(directory / 'type').read_text().strip(),
                temp_mC=int((directory / 'temp').read_text()))
        except (OSError, ValueError):
            pass
    for key, name in [('no_turbo', '/sys/devices/system/cpu/intel_pstate/no_turbo'),
                      ('sched_ext', '/sys/kernel/sched_ext/state')]:
        path = Path(name)
        result[key] = path.read_text().strip() if path.exists() else 'MISSING'
    return result


def build(output):
    if os.getuid() == 0:
        raise ValueError('Build as the normal research owner')
    full, lite = verify_sources()
    before = host()
    if any(r['temp_mC'] >= 85000 for r in before['thermal'].values()):
        raise ValueError('Skip compilation at high temperature; analysis files are retained')
    if any(int(r.get('capacity', '100')) < 30 for r in before['power'].values() if r.get('type') == 'Battery'):
        raise ValueError('Skip compilation at low battery')
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(REPO):
        raise ValueError('Use a new build directory outside the source repository')
    output.mkdir(parents=True)
    (output / 'include').mkdir()
    commands = []

    def run(argv):
        argv = [str(x) for x in argv]
        commands.append(argv)
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        with (output / 'compile.log').open('a') as stream:
            stream.write(json.dumps(argv) + '\n' + result.stderr)
            if not (len(argv) > 2 and argv[1:3] == ['btf', 'dump']):
                stream.write(result.stdout)
        if result.returncode:
            raise ValueError('Compilation/check failed; see ' + str(output / 'compile.log'))
        return result.stdout

    kernel_header = Path('/lib/modules') / platform.release() / 'build/include/uapi/linux/sched.h'
    if not kernel_header.is_file() or not re.search(r'^#define\s+SCHED_EXT\s+7\s*$', kernel_header.read_text(), re.M):
        raise ValueError('Matching running-kernel SCHED_EXT=7 UAPI required')
    (output / 'include/vmlinux.h').write_text(run(['bpftool', 'btf', 'dump', 'file', '/sys/kernel/btf/vmlinux', 'format', 'c']))
    target = run(['cc', '-dumpmachine']).strip()
    flags = ['clang', '-target', 'bpf', '-D__TARGET_ARCH_x86', '-O2', '-g', '-Wall', '-Werror',
             '-I' + str(output / 'include'), '-I' + str(REPO / 'tools/sched_ext/include'),
             '-I' + str(BASE), '-I/usr/include/' + target]
    objects = {}
    for name, text in [('E', full), ('L', lite)]:
        source = output / (name + '.bpf.c')
        source.write_text(text)
        for mode in ['diag', 'plain']:
            obj = output / (name + '-' + mode + '.bpf.o')
            run(flags + (['-DNEW_A_DIAGNOSTICS'] if mode == 'diag' else []) + ['-c', source, '-o', obj])
            objects[name + '-' + mode] = obj
    reuse = guard_build_module()
    sections = {name: reuse.old_build.elf_sections(path) for name, path in objects.items()}
    relocs = {name: reuse.elf_relocations(path) for name, path in objects.items()}
    comparisons = {}
    for mode in ['plain', 'diag']:
        checks = {}
        for section, row in sections['E-' + mode].items():
            if not row['flags'] & 4:
                continue
            equal = (row['raw'] == sections['L-' + mode][section]['raw'] and
                     relocs['E-' + mode].get(section) == relocs['L-' + mode].get(section))
            if mode == 'plain' or section not in ['struct_ops/llama_running', 'struct_ops/llama_stopping']:
                if not equal:
                    raise ValueError('Unplanned executable/relocation difference: ' + mode + '/' + section)
            checks[section] = equal
        comparisons[mode] = checks
    provenance = generate_loader(output)
    (output / 'bin').mkdir()
    for name in ['E', 'L']:
        defines = ['-DNEW_A_DIAGNOSTICS', '-DBPF_OBJECT_PATH="' + str(objects[name + '-diag']) + '"']
        if name == 'L':
            defines.append('-DFG_RUNTIME_LITE')
        run(['cc', '-O2', '-g', '-Wall', '-Wextra', '-Werror', '-std=gnu11',
             '-I' + str(BASE), '-I' + str(REPO / 'tools/sched_ext/include'), *defines,
             output / 'loader.c', '-o', output / 'bin' / ('llama_new_a_' + name), '-lbpf', '-lelf', '-lz'])
    sys.path.insert(0, str(REPO / 'tools/sched_ext/support'))
    import static_btf
    signatures = static_btf.inspect({n: p for n, p in objects.items() if n.endswith('diag')}, '/sys/kernel/btf/vmlinux')
    abi = {}
    for name in ['E-diag', 'L-diag']:
        types = static_btf.table(objects[name])
        names = '\n'.join(types.values())
        required = ['new_a_stats', 'new_a_tasks', 'new_a_events', 'new_a_diagnostic_config', 'new_a_topology']
        if not all("VAR '" + key + "'" in names for key in required):
            raise ValueError('Required diagnostic map missing')
        abi[name] = [row.splitlines()[0] for row in types.values()
            if row.startswith("STRUCT 'new_a_task_observation'") or row.startswith("STRUCT 'new_a_stats'") or row.startswith("STRUCT 'new_a_event'")]
    if abi['E-diag'] != abi['L-diag']:
        raise ValueError('Diagnostic structure sizes changed')
    report = dict(status='STATIC_PASS', scope='COMPILE_STATIC_ONLY', host_before=before, host_after=host(),
        source_sha256=dict(E=sha(BASE / 'llama_new_a.bpf.c'), L=sha(HERE / 'llama_new_a_runtime_lite.bpf.c')),
        btf_sha256=sha('/sys/kernel/btf/vmlinux'), signatures=signatures,
        executable_and_relocation_equality=comparisons, diagnostic_structures=abi, loader_derivation=provenance,
        objects={name: dict(path=str(path), sha256=sha(path), sections=inventory(path)) for name, path in objects.items()},
        loaders={name: dict(path=str(output / 'bin' / ('llama_new_a_' + name)), sha256=sha(output / 'bin' / ('llama_new_a_' + name))) for name in ['E', 'L']},
        commands=commands, verifier='NOT_RUN', attach='NOT_RUN', inference='NOT_RUN', performance='NOT_RUN',
        baseline_admission='NOT_ESTABLISHED_BY_PUBLIC_STATIC_BUILD', policy_note='Static policy identity is not identical live scheduling or safety approval',
        strict_acceptance='FAILED', safety_verdict='FAIL', energy_review='OVERHEAD_REVIEW_REQUIRED')
    (output / 'static-report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps(dict(status=result['status'], output=str(args.output), verifier='NOT_RUN', attach='NOT_RUN')))
