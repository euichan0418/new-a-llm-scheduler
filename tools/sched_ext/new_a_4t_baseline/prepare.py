#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""별도 4T/8T 비교를 준비하며 전원 설정이나 BPF를 변경하지 않는다."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from records import read,save,sha,require,verify
import study

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
SOURCE=REPO/'benchmark-results/season2/new-a-v4-research/20261110T145453Z-287918-affinity-first/comparison'
NAMES=['records.py','campaign.py','validation.py','study.py','workers.py','kernel_guard.py']


def parent():
    path=REPO/'tools/sched_ext/support/profile_preparation.py'
    spec=importlib.util.spec_from_file_location('profile_preparation',path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def input_manifest(root):
    source=read(SOURCE/'prepared.json')
    verify(source)
    files=dict(source['files']);links=dict(source['symlinks'])
    for directory in [HERE,root/'comparison',root/'build']:
        for p in directory.rglob('*'):
            if '__pycache__' in p.parts:continue
            if p.is_symlink():links[str(p)]=os.readlink(p)
            elif p.is_file():files[str(p)]=sha(p)
    for p in [SOURCE/'prepared.json',SOURCE/'seal.json',SOURCE/'completion.json',SOURCE/'summary.json']:
        files[str(p)]=sha(p)
    return dict(files=files,symlinks=links)


def make_pending(root):
    root=Path(root).resolve()
    require(root.is_dir() and not (root/'comparison').exists(), '새 일회성 출력 경로가 필요합니다')
    build=read(root/'build/static-report.json')
    require(build['status']=='PASS' and build['scope']=='COMPILE_STATIC_ONLY' and build['ops_flags']=={'E':8}, '동일 E FULL 컴파일·정적 검사가 필요합니다')
    completion=read(SOURCE/'completion.json')
    require(completion['status']=='AFFINITY_FACTOR_COMPLETE_NOT_V4_OR_SAFETY_APPROVED' and completion['safety_verdict']=='FAIL' and completion['strict_acceptance']=='FAILED', '기존 Affinity 캠페인 완료·실패 판정 증거가 다릅니다')
    run=root/'comparison';run.mkdir()
    for name in ['host_checks.py','environment.py','observations.py','turbo_guard.py']:
        shutil.copyfile(SOURCE/name,run/name)
    for name in NAMES:shutil.copyfile(HERE/name,run/name)
    content=(run/'environment.py').read_text();a=content.index('LIMITATION = (');b=content.index('\n\n\ndef applications()',a)
    limitation="LIMITATION = ('동일 8CPU 허용 집합에서 C/E의 8T/4T를 비교하는 탐색이다. ' '8서버, 워밍업16회, 측정8회이며 조건당 n=2다. 외부 Wakeup/fexit/Probe는 실행하지 않는다. ' '실제 OpenMP 팀 크기는 libgomp 표시로 확인하되 측정 요청 중 표시가 증가하면 거부한다. ' '시스템 busy는 서버 전용 실행 비중이 아니다. 에너지는 NOT_MEASURED이며 계산 스레드 반감이 CPU/에너지 반감은 아니다. ' 'Firefox와 Codex 등 일반 앱·온도·부하는 observe-only다. strict FAILED와 safety FAIL을 유지한다.')"
    (run/'environment.py').write_text(content[:a]+limitation+content[b:])
    folder=run/'layout/E/build/bin';folder.mkdir(parents=True)
    (folder/'llama_new_a_diag').symlink_to(build['binaries']['E']['path'])
    seed=read(SOURCE/'setup.json')
    setup={k:seed[k]for k in ['repo','uid','base_run','head','server_argv','server_env','request_sha256','possible_cpus','original_acceptance','original_diagnosis','strict_acceptance','safety_verdict','formal_benchmark','record_type','suite_acceptance','ttwu_features_path']}
    setup['server_env']=dict(seed['server_env'],**study.omp_options())
    setup.update(phase='FOUR_THREAD_BASELINE',authorization='USER_REQUEST_PCORE_COUNT_FOUR_THREAD_BASELINE',comparison_plan=study.plans(),
                 planned_sessions=8,planned_warmups=16,planned_measurements=8,planned_requests=24,
                 workload_constraints=dict(ac_required=True,no_turbo='1',power_profile='performance',gpu_offload=0,
                                           compute_threads=[8,4],batch_threads=[8,4],http_threads=2,
                                           server_cpus=study.CPUS,housekeeping_cpus=[15],
                                           thread_conditions={x:str(x)+'/'+str(x)+'/2'for x in [4,8]}),
                 instrumentation=dict(external_wakeup_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED',
                                      internal_diagnostics='E_FULL_C_NOT_APPLICABLE',wakeup_latency='NOT_AVAILABLE',selection_skip_count='NOT_AVAILABLE'),
                 kernel_watch_binary=build['binaries']['kernel-watch']['path'],automatic_retry=False,automatic_next_stage_admission=False,
                 build_static=str(root/'build/static-report.json'),policy_binaries=build['binaries'],source_result=str(SOURCE))
    save(run/'setup.json',setup)
    shell='#!/usr/bin/env bash\nset -euo pipefail\nexport PYTHONDONTWRITEBYTECODE=1\nTASK_RUN="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\nexec python3 -B "$TASK_RUN/launch.py" "$@"\n'
    shutil.copyfile(HERE/'launch.py',root/'launch.py')
    (root/'run-terminal.sh').write_text(shell)
    mod=parent();live=mod.live()
    state='WAITING_FOR_TURBO_OFF' if live['no_turbo']!='1' else 'WAITING_FOR_EXECUTION_PROFILE'
    save(root/'preparation-state.json',dict(status=state,current=live,profile_generated=False,sudo='NOT_RUN',bpf_load='NOT_RUN',inference='NOT_RUN'))
    manifest=input_manifest(root)
    for p in [root/'launch.py',root/'run-terminal.sh',HERE/'prepare.py',root/'preparation-state.json']:
        manifest['files'][str(p)]=sha(p)
    save(root/'pending.json',dict(state='WAITING_FOR_EXECUTION_PROFILE',repo=str(REPO),source=str(SOURCE),
                                compiled_kernel=build['kernel'],compiled_btf=build['btf_sha256'],inputs=manifest,
                                strict_acceptance='FAILED',safety_verdict='FAIL',execution='NOT_RUN'))
    print('준비 상태='+state+'; 실행용 Host Profile 미생성; sudo/BPF/추론 NOT_RUN')


def verify_pending(root):
    pending=read(root/'pending.json')
    require(pending['state']=='WAITING_FOR_EXECUTION_PROFILE' and pending['repo']==str(REPO),'잘못된 준비물입니다')
    verify(pending['inputs'])
    return pending


def seal(root):
    root=Path(root).resolve();pending=verify_pending(root);run=root/'comparison'
    require(not any((run/name).exists()for name in ['seal.json','seal-attempt.json','seal-failed.json','terminal-attempt.json','visits','STOPPED.json']), '이미 봉인 또는 실행을 시도한 준비물입니다. 재사용하지 마세요')
    mod=parent();current=mod.live()
    mod.power_gate(current)
    require(current['host']['kernel']==pending['compiled_kernel'] and current['host']['btf']['sha256']==pending['compiled_btf'], '현재 kernel/BTF와 컴파일 객체가 다릅니다. 새 준비물이 필요합니다')
    require(os.getuid()==REPO.stat().st_uid!=0,'일반 저장소 소유자 계정으로 실행하세요')
    topology,common,hp=mod.modules()
    import session
    require(not session.active_experiments(),'기존 실험/서버가 실행 중입니다')
    require(subprocess.check_output(['git','branch','--show-current'],cwd=REPO,text=True).strip()=='feature/new-a-scheduler','연구 브랜치가 다릅니다')
    seed=read(run/'setup.json')
    require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()==seed['head'],'준비 이후 commit이 변경됐습니다')
    save(run/'seal-attempt.json',dict(execution='NOT_RUN',sudo='NOT_RUN',host=current['host']))
    try:
        profile=hp.generate(seed['server_argv'][2])
        reference=read(SOURCE/'topology.json')
        mod.physical_gate(profile['topology'],reference['topology'])
        require(profile['allocation']['housekeeping_cpus']==[15] and set(profile['allocation']['server_cpus'])==mod.CPUS, '기본 물리 CPU 배정이 다릅니다')
        profile['workload_constraints']=seed['workload_constraints']
        profile['preparation_scope']='FOUR_THREAD_BASELINE_CURRENT_BOOT_TURBO_OFF'
        save(run/'host-profile.exploratory.json',profile)
        (run/'host-profile.exploratory.json').chmod(0o444)
        discovered=topology.discover()
        require(discovered['topology']==profile['topology'] and discovered['policy']['enabled'] and discovered['policy']['tiers']==reference['policy']['tiers'], '현재 CPUID/물리 토폴로지/tier를 확인할 수 없습니다')
        require(len(discovered['policy']['tiers'][0])==4 and set(discovered['policy']['tiers'][0])=={0,2,4,6}, '이번 4 P-core 연구 장비와 다릅니다')
        save(run/'topology.json',discovered)
        with (run/'topology.bin').open('xb')as f:f.write(topology.encode(discovered['policy']))
        after=mod.live();mod.power_gate(after)
        require(after['host']==profile['host'] and hp.execution_state(after['capabilities'])==hp.execution_state(profile['capabilities']), '프로필 생성 도중 환경이 변경됐습니다')
        files=dict(pending['inputs']['files']);links=dict(pending['inputs']['symlinks'])
        for p in run.rglob('*'):
            if p.is_symlink():links[str(p)]=os.readlink(p)
            elif p.is_file():files[str(p)]=sha(p)
        save(run/'prepared.json',dict(files=files,symlinks=links))
        save(run/'seal.json',dict(status='READY_FOR_FOUR_THREAD_BASELINE',prepared_sha256=sha(run/'prepared.json'),profile_sha256=sha(run/'host-profile.exploratory.json'),host=profile['host'],execution='NOT_RUN',strict_acceptance='FAILED',safety_verdict='FAIL'))
        print('새 Host Profile 봉인 완료. 실제 실행 전 모든 기술 검사를 다시 수행합니다.')
    except BaseException as error:
        save(run/'seal-failed.json',dict(status='FAILED',error=type(error).__name__+': '+str(error),execution='NOT_RUN'))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    make_pending(parser.parse_args().output)
