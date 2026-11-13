#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 E FULL의 수동/자동 설정 비교를 준비한다. 전원 설정이나 BPF를 변경하지 않는다."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import math
import json
from records import read,save,sha,require,verify
import study
import integration

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
SOURCE=REPO/'benchmark-results/season2/new-a-final/20261113T114612Z-policy-selection/release/performance/comparison'
NAMES=['admission.py','records.py','campaign.py','validation.py','study.py','workers.py','kernel_guard.py','stats_off.py','integration.py']


def review_source():
    seal=read(SOURCE/'seal.json')
    require(sha(SOURCE/'prepared.json')==seal['prepared_sha256'] and sha(SOURCE/'host-profile.exploratory.json')==seal['profile_sha256'],'기존 4T 준비물의 봉인 해시가 다릅니다')
    verify(read(SOURCE/'prepared.json'))
    code="import json,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import study;root=Path(sys.argv[1]);fresh=study.summarize(root);old=json.loads((root/'summary.json').read_text());assert fresh=={k:v for k,v in old.items()if k!='terminal'};assert fresh['complete']and fresh['performance_samples']==12 and all(x['valid']for x in fresh['visits']);print(json.dumps({k:fresh[k]for k in ['statistics','complete','performance_samples']}))"
    result=json.loads(subprocess.check_output([sys.executable,'-B','-c',code,str(SOURCE)],text=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')))
    requests=0
    for path in (SOURCE/'visits').glob('*/*'):
        for role in ['warmup-1','warmup-2','measured']:
            response=read(path/(role+'-response.json'))
            t=response['timings']
            require(t['cache_n']==0 and math.isclose(t['prompt_per_second'],920000/t['prompt_ms'],rel_tol=1e-10) and math.isclose(t['predicted_per_second'],128000/t['predicted_ms'],rel_tol=1e-10),'기존 기준선의 cache/timer 정의가 일치하지 않습니다')
            requests+=1
    require(requests==36,'기존 기준선의 요청36개를 확인할 수 없습니다')
    terminal=read(SOURCE/'terminal-logs/20261113T125829Z-25fae83f/terminal-result.json')
    require(terminal['errors']==[] and terminal['status']=='FINAL_COMPARISON_COMPLETE_NOT_SAFETY_APPROVED' and not(SOURCE/'STOPPED.json').exists(),'기존 4T 기준선 완료 증거가 유효하지 않습니다')
    result.update(status='SOURCE_REVALIDATED',source=str(SOURCE),source_summary_sha256=sha(SOURCE/'summary.json'),historical_rows_pooled=False,verified_requests=requests)
    return result


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
    for directory in [HERE,root/'comparison',root/'build',root.parent/'configuration']:
        for p in directory.rglob('*'):
            if '__pycache__' in p.parts:continue
            if p.is_symlink():links[str(p)]=os.readlink(p)
            elif p.is_file():files[str(p)]=sha(p)
    for p in [SOURCE/'prepared.json',SOURCE/'seal.json',SOURCE/'completion.json',SOURCE/'summary.json']:
        files[str(p)]=sha(p)
    if (root/'build').is_symlink():links[str(root/'build')]=os.readlink(root/'build')
    return dict(files=files,symlinks=links)


def make_pending(root, mode, functional_dir):
    integration.policy_identity()
    root=Path(root).resolve()
    require(mode in ['FUNCTIONAL','PERFORMANCE'],'기능/성능 단계가 필요합니다')
    require(root.is_dir() and not(root/'comparison').exists(),'새 일회성 출력 경로가 필요합니다')
    build_path=(root/'build/static-report.json').resolve();build=read(build_path)
    stats=read(root/'build/stats-off-static.json')
    require(stats['status']=='PASS' and sha(stats['binary'])==stats['sha256'],'통계 OFF 검사 빌드가 없습니다')
    require(build['status']=='PASS' and build['scope']=='COMPILE_STATIC_ONLY' and build['ops_flags']=={'E':8},'E 원본과 동일한 FULL 정적 검사가 필요합니다')
    run=root/'comparison';run.mkdir()
    save(run/'source-review.json',review_source())
    for name in ['host_checks.py','environment.py','observations.py','turbo_guard.py']:
        shutil.copyfile(SOURCE/name,run/name)
    for name in NAMES:shutil.copyfile(HERE/name,run/name)
    content=(run/'environment.py').read_text();a=content.index('LIMITATION = (');b=content.index('\n\n\ndef applications()',a)
    limitation="LIMITATION = ('4/4/2 및 P-only 0,2,4,6을 고정한다. 같은 E FULL 수동/자동 기능 2세션과 회귀 4세션은 별도 단계다. ' '외부 Wakeup/fexit/Probe 및 Runtime Statistics는 OFF다. 양쪽 E FULL 진단을 유지한다. ' '기능 결과를 성능 우위의 근거로 사용하지 않는다. 성능 비교는 실제 최종 E 기능 증거를 재검증한 뒤만 실행한다. ' '시스템 busy는 다른 작업도 포함하며 정확한 Prefill 작업자 CPU 시간은 NOT_AVAILABLE, 에너지는 NOT_MEASURED다. 조건별 n=2, 기존 strict FAILED/safety FAIL 유지.')"
    (run/'environment.py').write_text(content[:a]+limitation+content[b:])
    for candidate in ['E']:
        folder=run/'layout'/candidate/'build/bin';folder.mkdir(parents=True)
        (folder/'llama_new_a_diag').symlink_to(build['binaries'][candidate]['path'])
    seed=read(SOURCE/'setup.json');seed['server_argv'],seed['server_env']=study.server_config(seed,4)
    auto=root.parent/'configuration'
    require((auto/'configuration.json').is_file(),'자동 설정 생성이 필요합니다')
    manual=dict(server_argv=seed['server_argv'],server_env=seed['server_env'],server_cpus=study.CPUS)
    setup={k:seed[k]for k in ['repo','uid','base_run','head','server_argv','server_env','request_sha256','possible_cpus','original_acceptance','original_diagnosis','strict_acceptance','safety_verdict','formal_benchmark','record_type','suite_acceptance','ttwu_features_path']}
    plan=study.plans(mode)
    setup.update(phase='PORTABLE_RUNTIME',mode=mode,authorization='USER_REQUEST_PORTABLE_RUNTIME',comparison_plan=plan,
                 planned_sessions=len(plan),planned_warmups=2*len(plan),planned_measurements=0 if mode=='FUNCTIONAL'else len(plan),planned_requests=3*len(plan),
                 workload_constraints=dict(ac_required=True,no_turbo='1',power_profile='performance',gpu_offload=0,compute_threads=4,batch_threads=4,http_threads=2,server_cpus=study.CPUS,housekeeping_cpus=[15]),
                 instrumentation=dict(seed['instrumentation'],internal_diagnostics='E_FULL_C_NOT_APPLICABLE'),kernel_watch_binary=build['binaries']['kernel-watch']['path'],stats_off_binary=stats['binary'],
                 automatic_retry=False,automatic_next_stage_admission=False,build_static=str(build_path),policy_binaries=build['binaries'],source_result=str(SOURCE),required_functional_dir=str(Path(functional_dir).resolve()))
    import hashlib
    setup.update(manual_configuration=manual,manual_configuration_sha256=hashlib.sha256(json.dumps(manual,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                 manual_topology=str(run/'topology.bin'),portable_configuration=str(auto),portable_configuration_sha256=sha(auto/'configuration.json'),cpu_probe_binary=str((root/'build/cpu-probe').resolve()))
    save(run/'setup.json',setup)
    shell='#!/usr/bin/env bash\nset -euo pipefail\nexport PYTHONDONTWRITEBYTECODE=1\nTASK_RUN="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\nexec python3 -B "$TASK_RUN/launch.py" "$@"\n'
    shutil.copyfile(HERE/'launch.py',root/'launch.py');(root/'run-terminal.sh').write_text(shell)
    mod=parent();live=mod.live()
    try:mod.power_gate(live);state='WAITING_FOR_EXECUTION_PROFILE'
    except ValueError as error:state='WAITING_FOR_REQUIRED_POWER_ENVIRONMENT: '+str(error)
    save(root/'preparation-state.json',dict(status=state,current=live,profile_generated=False,sudo='NOT_RUN',bpf_load='NOT_RUN',inference='NOT_RUN'))
    manifest=input_manifest(root)
    for path in [root/'launch.py',root/'run-terminal.sh',HERE/'prepare.py',root/'preparation-state.json']:
        manifest['files'][str(path)]=sha(path)
    save(root/'pending.json',dict(state='WAITING_FOR_EXECUTION_PROFILE',repo=str(REPO),source=str(SOURCE),compiled_kernel=build['kernel'],compiled_btf=build['btf_sha256'],inputs=manifest,strict_acceptance='FAILED',safety_verdict='FAIL',execution='NOT_RUN'))
    print('준비 상태='+state+'; 실제 BPF/추론 NOT_RUN')


def verify_pending(root):
    integration.policy_identity()
    pending=read(root/'pending.json')
    require(pending['state']=='WAITING_FOR_EXECUTION_PROFILE' and pending['repo']==str(REPO),'잘못된 준비물입니다')
    verify(pending['inputs'])
    return pending


def seal(root):
    root=Path(root).resolve();pending=verify_pending(root);run=root/'comparison'
    require(not any((run/name).exists()for name in ['seal.json','seal-attempt.json','seal-failed.json','terminal-attempt.json','visits','STOPPED.json']), '이미 봉인 또는 실행을 시도한 준비물입니다. 재사용하지 마세요')
    mod=parent();current=mod.live()
    mod.power_gate(current)
    require(Path('/proc/sys/kernel/bpf_stats_enabled').read_text().strip()=='0','기존 Runtime Statistics가 활성화됐습니다. 상태를 변경하지 않습니다')
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
        profile['preparation_scope']='PORTABLE_RUNTIME_CURRENT_BOOT_TURBO_OFF'
        save(run/'host-profile.exploratory.json',profile)
        (run/'host-profile.exploratory.json').chmod(0o444)
        discovered=topology.discover()
        require(discovered['topology']==profile['topology'] and discovered['policy']['enabled'] and discovered['policy']['tiers']==reference['policy']['tiers'], '현재 CPUID/물리 토폴로지/tier를 확인할 수 없습니다')
        require(len(discovered['policy']['tiers'][0])==4 and set(discovered['policy']['tiers'][0])=={0,2,4,6}, '이번 4 P-core 연구 장비와 다릅니다')
        save(run/'topology.json',discovered)
        with (run/'topology.bin').open('xb')as f:f.write(topology.encode(discovered['policy']))
        integration.verify_inputs(seed)
        after=mod.live();mod.power_gate(after)
        require(after['host']==profile['host'] and hp.execution_state(after['capabilities'])==hp.execution_state(profile['capabilities']), '프로필 생성 도중 환경이 변경됐습니다')
        files=dict(pending['inputs']['files']);links=dict(pending['inputs']['symlinks'])
        for p in run.rglob('*'):
            if p.is_symlink():links[str(p)]=os.readlink(p)
            elif p.is_file():files[str(p)]=sha(p)
        save(run/'prepared.json',dict(files=files,symlinks=links))
        save(run/'seal.json',dict(status='READY_FOR_PORTABLE_RUNTIME',prepared_sha256=sha(run/'prepared.json'),profile_sha256=sha(run/'host-profile.exploratory.json'),host=profile['host'],execution='NOT_RUN',strict_acceptance='FAILED',safety_verdict='FAIL'))
        print('새 Host Profile 봉인 완료. 실제 실행 전 모든 기술 검사를 다시 수행합니다.')
    except BaseException as error:
        save(run/'seal-failed.json',dict(status='FAILED',error=type(error).__name__+': '+str(error),execution='NOT_RUN'))
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['FUNCTIONAL','PERFORMANCE'],required=True)
    parser.add_argument('--functional-dir',type=Path,required=True)
    args=parser.parse_args();make_pending(args.output,args.mode,args.functional_dir)
