#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Frozen 서버를 실행하지 않고 Thread/Affinity 설정과 기존 E topology 입력을 생성한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

import topology

HERE = Path(__file__).resolve().parent
THREAD_OPTIONS = {'--threads':'compute_threads', '-t':'compute_threads', '--threads-batch':'batch_threads', '-tb':'batch_threads', '--threads-http':'http_threads'}
CANONICAL = {'compute_threads':'--threads', 'batch_threads':'--threads-batch', 'http_threads':'--threads-http'}
AFFINITY_OPTIONS = {'--cpu-mask','-C','--cpu-range','-Cr','--cpu-strict','--cpu-mask-batch','-Cb','--cpu-range-batch','-Crb','--cpu-strict-batch','--numa'}
AFFINITY_ENV = {'GOMP_CPU_AFFINITY','OMP_PLACES','OMP_PROC_BIND','KMP_AFFINITY','OMP_THREAD_LIMIT','OMP_TEAMS_THREAD_LIMIT',
                'LLAMA_ARG_CPU_MASK','LLAMA_ARG_CPU_RANGE','LLAMA_ARG_CPU_MASK_BATCH','LLAMA_ARG_CPU_RANGE_BATCH','LLAMA_ARG_NUMA'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def host_identity():
    btf = Path('/sys/kernel/btf/vmlinux')
    return dict(kernel=platform.release(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                architecture=platform.machine(), btf_sha256=sha(btf) if btf.exists() else 'NOT_AVAILABLE')


def server_config(selection, argv, environment):
    header = HERE.parents[2]/'ggml/include/ggml.h'
    limits = re.findall(r'^#define\s+GGML_MAX_N_THREADS\s+(\d+)\s*$',header.read_text(),re.M)
    topology.require(len(limits)==1, '서버 소스의 Thread 지원 한계를 확인할 수 없습니다')
    limit = int(limits[0])
    topology.require(all(type(selection[k]) is int and 1 <= selection[k] <= limit for k in ['compute_threads','batch_threads']), '필요한 Thread 수가 서버 소스 지원 한계를 초과합니다. 조용히 줄이지 않습니다')
    topology.require(selection['http_threads']==2, '기본 HTTP Thread 설정이 다릅니다')
    topology.require(isinstance(argv,list) and argv and all(isinstance(v,str) and v and '\0' not in v for v in argv), '서버 argv 형식이 잘못됐습니다')
    topology.require(isinstance(environment,dict) and all(isinstance(k,str) and isinstance(v,str) and '\0' not in k+v for k,v in environment.items()), '서버 환경 형식이 잘못됐습니다')
    topology.require(not AFFINITY_ENV.intersection(environment), 'OpenMP Affinity/Thread 제한 환경이 충돌할 수 있습니다')
    seen = set()
    result = [argv[0]]
    index = 1
    while index < len(argv):
        token = argv[index]
        topology.require(token != '--', 'positional 인자 구분자는 자동 Thread 인자 생성과 혼합하지 않습니다')
        option = token.split('=',1)[0]
        topology.require(re.fullmatch(r'-t(?:b)?\d+',option) is None, '붙여 쓴 Thread 별칭은 명시적인 인자로 분리하세요')
        topology.require(option not in AFFINITY_OPTIONS, '기존 서버 Affinity/NUMA 인자와 자동 Affinity를 혼합하지 않습니다: ' + option)
        if option in THREAD_OPTIONS:
            role = THREAD_OPTIONS[option]
            topology.require(role not in seen, '서버 Thread 인자가 중복됐습니다: ' + role)
            seen.add(role)
            if '=' not in token:
                topology.require(index+1 < len(argv) and not argv[index+1].startswith('-'), 'Thread 인자 값이 없습니다')
                index += 1
            else:
                topology.require(bool(token.split('=',1)[1]), 'Thread 인자 값이 없습니다')
            result += [CANONICAL[role], str(selection[role])]
        else:
            result.append(token)
        index += 1
    for role in CANONICAL:
        if role not in seen:
            result += [CANONICAL[role], str(selection[role])]
    env = dict(environment, OMP_NUM_THREADS=str(selection['compute_threads']), OMP_DYNAMIC='FALSE')
    return result, env


def sources():
    paths = [HERE/name for name in ['topology.py','config.py','cpu_probe.c']]
    paths += [HERE.parent/'new_a'/name for name in ['topology.py','llama_new_a.bpf.c','new_a.h','loader.c','mask_dump.h']]
    paths += [HERE.parent/'llama_scx.c', HERE.parent/'include/llama_scx_state.h']
    repo = HERE.parents[2]
    paths += [repo/'ggml/include/ggml.h']
    return {str(p.relative_to(repo)):sha(p) for p in paths}


def make(selection, argv, environment, helper, host=None):
    checked = topology.plan(selection['snapshot'],selection['cpuid'])
    topology.require(checked == selection, '선택 결과가 원시 topology/CPUID와 일치하지 않습니다')
    argv, env = server_config(selection,argv,environment)
    return dict(schema=1,scope='CONFIGURATION_ONLY_NO_SERVER_OR_BPF_EXECUTION',selection=selection,server_argv=argv,server_env=env,
                server_cpus=selection['selected_cpus'],host=host or host_identity(),helper_sha256=sha(helper),input_sources=sources(),
                topology_binary_sha256=hashlib.sha256(topology.original.encode(selection['policy'])).hexdigest(),
                strict_acceptance='FAILED',safety_verdict='FAIL',execution_admitted=False,
                required_runtime_gates=['FRESH_HOST_PROFILE','POWER_TURBO_TOPOLOGY','FROZEN_MODEL_REQUEST_IDENTITY','VERIFIER_ATTACH','MASK_TRACKING','THERMAL','HTTP_TOKENS_HASH','DETACH_CLEANUP_DISABLED'],
                inherited_affinity_environment='REJECT_CONFLICTS_AT_CHECK_CURRENT')


def write_output(root, config, base):
    root = Path(root)
    topology.require(not root.exists(), '출력 디렉터리가 이미 있습니다. 기존 파일을 덮어쓰지 않습니다')
    root.mkdir(parents=True)
    with (root/'configuration.json').open('x') as f:
        json.dump(config,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    with (root/'base-server.json').open('x') as f:
        json.dump(base,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    with (root/'topology.bin').open('xb') as f:
        f.write(topology.original.encode(config['selection']['policy']))
    manifest = dict(files={name:sha(root/name) for name in ['configuration.json','base-server.json','topology.bin']},execution_admitted=False)
    with (root/'manifest.json').open('x') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
        f.write('\n')


def check_current(root, helper, discovery=None):
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text())
    topology.require(manifest['execution_admitted'] is False and set(manifest['files']) == {'configuration.json','base-server.json','topology.bin'}, '설정 manifest 형식이 다릅니다')
    topology.require(all(sha(root/name)==value for name,value in manifest['files'].items()), '저장된 설정/바이너리 topology가 변경됐습니다')
    stored = json.loads((root/'configuration.json').read_text())
    base = json.loads((root/'base-server.json').read_text())
    requested = base['requested_cpus']
    current = (discovery or topology.discover)(helper, requested=requested)
    expected = make(current,base['server_argv'],base['server_env'],helper)
    topology.require(expected == stored, '부팅/커널/BTF/허용 CPU/topology/코드/서버 설정이 변경됐습니다. 새 설정이 필요합니다')
    topology.require(not AFFINITY_ENV.intersection(os.environ), '현재 환경에 OpenMP Affinity/Thread 제한 설정이 있습니다')
    return dict(status='CONFIGURATION_REVALIDATED_NOT_EXECUTION_ADMITTED',execution_admitted=False,server_cpus=current['selected_cpus'],compute_threads=current['compute_threads'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--cpus',help='실제 허용 집합 안에서 추가 제한할 CPU 목록')
    parser.add_argument('--server-config',type=Path,help='server_argv/server_env가 있는 기존 JSON')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--check-current',type=Path)
    args = parser.parse_args()
    if args.check_current:
        topology.require(args.server_config is None and args.output is None and args.cpus is None, '재검증 명령과 생성 인자를 혼합하지 않습니다')
        print(json.dumps(check_current(args.check_current,args.probe),ensure_ascii=False,indent=2))
        return
    topology.require(not AFFINITY_ENV.intersection(os.environ), '현재 환경에 OpenMP Affinity/Thread 제한 설정이 있습니다')
    requested = topology.cpulist(args.cpus) if args.cpus is not None else None
    value = topology.discover(args.probe,requested=requested)
    if args.server_config:
        base = json.loads(args.server_config.read_text())
        env = base.get('server_env',{})
        config = make(value,base['server_argv'],env,args.probe)
        if args.output:
            write_output(args.output,config,dict(server_argv=base['server_argv'],server_env=env,requested_cpus=requested))
        print(json.dumps(config,ensure_ascii=False,indent=2))
    else:
        topology.require(args.output is None, '설정 파일 생성에는 --server-config가 필요합니다')
        print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except (ValueError,OSError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        raise SystemExit('PORTABLE_TOPOLOGY_BLOCKED: ' + str(error))
