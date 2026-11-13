#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 FULL 소스를 별도 경로에서 컴파일한다. 커널 로딩은 하지 않는다."""
import argparse
from pathlib import Path
import struct
import subprocess
import sys
import re
from records import read, save, sha, require

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = REPO / 'tools/sched_ext'
REFERENCE = REPO / 'benchmark-results/season2/new-a-v3-functional/20261109T050531Z-ca1710-battery-preparation/offline-build-final/static-report.json'
sys.path.insert(0, str(ROOT / 'new_a_analysis'))
import build_static
sys.path.insert(0, str(ROOT / 'new_a_full_guard'))
import prepare_build
sys.path.insert(0, str(ROOT / 'support'))
import static_btf

def build(output):
    output = Path(output).resolve()
    require(not output.exists(), '새 빌드 디렉터리가 필요합니다')
    reference = read(REFERENCE)
    require(reference['btf_sha256'] == sha('/sys/kernel/btf/vmlinux') and reference['kernel'] == Path('/proc/sys/kernel/osrelease').read_text().strip(), '기존 검증 객체와 현재 커널/BTF가 다릅니다')
    output.mkdir(parents=True)
    header = Path(reference['reused_header'])
    require(sha(header) == reference['header_sha256'], '재사용 BTF 헤더가 변경됐습니다')
    commands = []
    def run(argv):
        argv = list(map(str, argv))
        commands.append(argv)
        result = subprocess.run(argv, capture_output=True, text=True)
        with (output / 'compile.log').open('a') as log:
            log.write(str(argv) + '\n' + result.stdout + result.stderr)
        require(result.returncode == 0, '컴파일 또는 정적 명령 실패: ' + str(output / 'compile.log'))
        return result.stdout
    machine = run(['cc', '-dumpmachine']).strip()
    flags = ['clang', '-target', 'bpf', '-D__TARGET_ARCH_x86', '-DNEW_A_DIAGNOSTICS', '-O2', '-g', '-Wall', '-Werror', '-I' + str(header.parent), '-I' + str(ROOT / 'include'), '-I' + str(ROOT / 'new_a'), '-I/usr/include/' + machine]
    objects = {}
    identities = {}
    for policy, folder, name in [('E', 'new_a', 'llama_new_a.bpf.c')]:
        source = ROOT / folder / name
        obj = output / (policy + '.bpf.o')
        run(flags + ['-c', source, '-o', obj])
        old = Path(reference['objects'][policy]['path'])
        require(sha(old) == reference['objects'][policy]['sha256'], '기존 기능 검증 객체가 변경됐습니다')
        a, b = build_static.elf_sections(obj), build_static.elf_sections(old)
        executable = {key: value['raw'] for key, value in a.items() if value['flags'] & 4}
        require(executable and executable == {key: value['raw'] for key, value in b.items() if value['flags'] & 4}, '기존 객체와 실행 코드가 다릅니다: ' + policy)
        require(prepare_build.elf_relocations(obj) == prepare_build.elf_relocations(old), '기존 객체와 relocation이 다릅니다: ' + policy)
        objects[policy] = dict(path=str(obj), sha256=sha(obj), source=str(source), source_sha256=sha(source))
        identities[policy] = dict(executable_sections_equal=True, relocations_equal=True)
    derivation = prepare_build.generate_loader(output)
    binaries = {}
    for policy in ['E']:
        binary = output / ('loader-' + policy)
        run(['cc', '-O2', '-g', '-Wall', '-Wextra', '-Werror', '-std=gnu11', '-I' + str(ROOT / 'new_a'), '-I' + str(ROOT / 'include'), '-DNEW_A_DIAGNOSTICS', '-DBPF_OBJECT_PATH="' + objects[policy]['path'] + '"', output / 'loader.c', '-o', binary, '-lbpf', '-lelf', '-lz'])
        run([binary, '--help'])
        require((objects[policy]['path'] + '\0').encode() in binary.read_bytes(), '로더에 다른 BPF 경로가 포함됐습니다')
        binaries[policy] = dict(path=str(binary), sha256=sha(binary))
    kernel = static_btf.table('/sys/kernel/btf/vmlinux')
    ops_flags = {}
    for policy in ['E']:
        obj = Path(objects[policy]['path'])
        types = static_btf.table(obj)
        ops = next(value for value in types.values() if value.startswith("STRUCT 'sched_ext_ops'"))
        offset = int(re.search(r"'flags'.*bits_offset=(\d+)", ops)[1]) // 8
        ops_flags[policy] = struct.unpack_from('<Q', build_static.elf_sections(obj)['.struct_ops.link']['raw'], offset)[0]
    require(ops_flags == dict(E=8), 'partial/queued 플래그가 기존과 다릅니다')
    enum = ''.join(kernel.values())
    require("'SCX_OPS_ALLOW_QUEUED_WAKEUP' val=32" in enum and "'SCX_OPS_SWITCH_PARTIAL' val=8" in enum, '현재 BTF의 sched_ext 플래그 값이 다릅니다')
    watch = output / 'kernel-watch'
    run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(ROOT / 'new_a_full_guard'), HERE / 'kernel_watch.c', '-o', watch])
    run([watch, '--help'])
    binaries['kernel-watch'] = dict(path=str(watch), sha256=sha(watch))
    report = dict(status='PASS', scope='COMPILE_STATIC_ONLY', kernel=reference['kernel'], btf_sha256=reference['btf_sha256'], header=str(header), header_sha256=sha(header), objects=objects, binaries=binaries, identity=identities, ops_flags=ops_flags,
                  kfunc_signatures=static_btf.inspect({key: Path(value['path']) for key, value in objects.items()}, '/sys/kernel/btf/vmlinux'), loader_derivation=derivation, commands=commands,
                  verifier='NOT_RUN', attach='NOT_RUN', inference='NOT_RUN', observer='DISABLED_NOT_STARTED', route_probe='DISABLED_NOT_STARTED', strict_acceptance='FAILED', safety_verdict='FAIL')
    save(output / 'static-report.json', report)
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = build(parser.parse_args().output)
    print('BPF 컴파일 및 정적 검사: ' + result['status'] + '; 실제 로딩·추론: NOT_RUN')
