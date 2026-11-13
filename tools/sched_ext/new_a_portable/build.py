#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""원본 E FULL과 CPU probe를 새 경로에서 빌드한다. 커널 로딩 기능은 없다."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0,str(ROOT/'support'))


import build_static
import prepare_build
import static_btf


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(output, header=None, reference=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('새 빌드 경로가 필요합니다. 기존 파일은 덮어쓰지 않습니다')
    output.mkdir(parents=True)
    commands = []
    def run(argv):
        argv = list(map(str,argv))
        commands.append(argv)
        p = subprocess.run(argv,capture_output=True,text=True)
        with (output/'compile.log').open('a') as f:
            f.write(repr(argv)+'\n'+p.stdout+p.stderr)
        if p.returncode:
            raise ValueError('컴파일/정적 검사 실패: '+str(output/'compile.log'))
        return p.stdout
    rendered = subprocess.check_output(['bpftool','btf','dump','file','/sys/kernel/btf/vmlinux','format','c'])
    if header is None:
        (output/'include').mkdir()
        header = output/'include/vmlinux.h'
        with header.open('xb') as f:
            f.write(rendered)
    else:
        header = Path(header).resolve()
        if header.read_bytes() != rendered:
            raise ValueError('재사용 헤더와 현재 BTF의 C 표현이 다릅니다')
    kernel_header = Path('/lib/modules')/Path('/proc/sys/kernel/osrelease').read_text().strip()/'build/include/uapi/linux/sched.h'
    if not kernel_header.is_file() or not re.search(r'^#define\s+SCHED_EXT\s+7\s*$',kernel_header.read_text(),re.M):
        raise ValueError('현재 커널의 SCHED_EXT UAPI를 확인할 수 없습니다')
    child = output/'llama_scx_child'
    run(['cc','-O2','-g','-Wall','-Wextra','-Werror','-std=gnu11','-I'+str(ROOT/'include'),ROOT/'llama_scx_child.c','-o',child])
    run([child,'--help'])
    probe = output/'cpu-probe'
    run(['cc','-O2','-g','-Wall','-Wextra','-Werror','-std=gnu11','-I'+str(ROOT/'new_a'),HERE/'cpu_probe.c','-o',probe])
    run([probe,'--help'])
    machine = run(['cc','-dumpmachine']).strip()
    flags = ['clang','-target','bpf','-D__TARGET_ARCH_x86','-O2','-g','-Wall','-Werror',
             '-I'+str(header.parent),'-I'+str(ROOT/'include'),'-I'+str(ROOT/'new_a'),'-I/usr/include/'+machine]
    source = ROOT/'new_a/llama_new_a.bpf.c'
    obj = output/'E-full.bpf.o'
    run(flags+['-DNEW_A_DIAGNOSTICS','-c',source,'-o',obj])
    plain = output/'E-plain.bpf.o'
    run(flags+['-c',source,'-o',plain])
    derivation = prepare_build.generate_loader(output)
    loader = output/'loader-E-full'
    run(['cc','-O2','-g','-Wall','-Wextra','-Werror','-std=gnu11','-I'+str(ROOT/'new_a'),'-I'+str(ROOT/'include'),
         '-DNEW_A_DIAGNOSTICS','-DBPF_OBJECT_PATH="'+str(obj)+'"',output/'loader.c','-o',loader,'-lbpf','-lelf','-lz'])
    run([loader,'--help'])
    if (str(obj)+'\0').encode() not in loader.read_bytes():
        raise ValueError('로더의 BPF 객체 경로가 다릅니다')
    signatures = static_btf.inspect({'E_FULL':obj,'E_PLAIN':plain},'/sys/kernel/btf/vmlinux')
    types = static_btf.table(obj)
    ops = next(v for v in types.values() if v.startswith("STRUCT 'sched_ext_ops'"))
    offset = int(re.search(r"'flags'.*bits_offset=(\d+)",ops)[1])//8
    sections = build_static.elf_sections(obj)
    ops_flags = struct.unpack_from('<Q',sections['.struct_ops.link']['raw'],offset)[0]
    if ops_flags != 8 or len(sections['.data']['raw']) != 12:
        raise ValueError('기존 Partial 플래그 또는 control ABI가 다릅니다')
    comparison = dict(status='NOT_REQUESTED')
    if reference is not None:
        reference = Path(reference).resolve()
        old = build_static.elf_sections(reference)
        executable = lambda s:{k:v['raw'] for k,v in s.items() if v['flags'] & 4}
        equal = executable(sections) == executable(old)
        relocs = prepare_build.elf_relocations(obj) == prepare_build.elf_relocations(reference)
        if not equal or not relocs:
            raise ValueError('기존 E FULL과 실행 section/relocation이 다릅니다')
        comparison = dict(status='PASS',reference=str(reference),reference_sha256=sha(reference),executable_sections_equal=equal,relocations_equal=relocs)
    report = dict(status='PASS',scope='COMPILE_AND_STATIC_ONLY',kernel=Path('/proc/sys/kernel/osrelease').read_text().strip(),
                  btf_sha256=sha('/sys/kernel/btf/vmlinux'),header=str(header),header_sha256=sha(header),
                  cpu_probe=dict(path=str(probe),sha256=sha(probe)),source=dict(path=str(source),sha256=sha(source)),
                  full_object=dict(path=str(obj),sha256=sha(obj)),plain_object=dict(path=str(plain),sha256=sha(plain)),
                  loader=dict(path=str(loader),sha256=sha(loader)),child=dict(path=str(child),sha256=sha(child)),loader_derivation=derivation,ops_flags=ops_flags,kfunc_signatures=signatures,reference=comparison,
                  commands=commands,verifier='NOT_RUN',attach='NOT_RUN',inference='NOT_RUN',strict_acceptance='FAILED',safety_verdict='FAIL')
    with (output/'static-report.json').open('x') as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
        f.write('\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--vmlinux-header',type=Path)
    parser.add_argument('--reference-object',type=Path)
    args=parser.parse_args()
    try:
        result=build(args.output,args.vmlinux_header,args.reference_object)
        print('BPF/CPU probe 컴파일 및 정적 검사 '+result['status']+'; 실제 커널 실행 NOT_RUN')
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        raise SystemExit('PORTABLE_TOPOLOGY_BLOCKED: '+str(error))
