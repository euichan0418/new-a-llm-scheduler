# SPDX-License-Identifier: GPL-2.0
"""수동/자동 설정의 실제 Session 입력과 저장된 런타임 증거를 연결한다."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from records import read, require, sha

HERE=Path(__file__).resolve().parent


def policy_identity():
    repo=HERE.parents[2]
    manifest=read(repo/'tools/sched_ext/new_a_final/policy-manifest.json')
    require(manifest['policy_change']=='NONE' and manifest['strict_acceptance']=='FAILED' and manifest['safety_verdict']=='FAIL','원본 최종 정책 판정이 다릅니다')
    for name,digest in manifest['files'].items():require(sha(repo/name)==digest,'기존 E 원본이 변경됐습니다: '+name)


def case(setup,mode):
    require(mode in ['MANUAL','AUTO'],'지원하지 않는 설정 경로입니다')
    if mode=='MANUAL':
        manual=setup['manual_configuration']
        return dict(configuration_mode=mode,server_argv=list(manual['server_argv']),server_env=dict(manual['server_env']),
                    server_cpus=list(manual['server_cpus']),topology=setup['manual_topology'],
                    configuration_sha256=setup['manual_configuration_sha256'])
    root=Path(setup['portable_configuration'])
    auto=read(root/'configuration.json')
    require(auto['execution_admitted']is False and auto['strict_acceptance']=='FAILED' and auto['safety_verdict']=='FAIL','자동 설정을 안전성 승인으로 바꾸지 않습니다')
    return dict(configuration_mode=mode,server_argv=list(auto['server_argv']),server_env=dict(auto['server_env']),server_cpus=list(auto['server_cpus']),
                topology=str(root/'topology.bin'),configuration_sha256=sha(root/'configuration.json'))


def verify_inputs(setup):
    command=[sys.executable,'-B',str(Path(setup['repo'])/'tools/sched_ext/new_a_portable/config.py'),
             '--probe',setup['cpu_probe_binary'],'--check-current',setup['portable_configuration']]
    result=subprocess.run(command,capture_output=True,text=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),timeout=45)
    require(result.returncode==0,'자동 설정 현재 환경 재검증 실패: '+result.stderr.strip())
    checked=json.loads(result.stdout)
    require(checked['status']=='CONFIGURATION_REVALIDATED_NOT_EXECUTION_ADMITTED' and checked['execution_admitted']is False,'자동 설정 재검증 범위가 다릅니다')
    manual,auto=[case(setup,x)for x in ['MANUAL','AUTO']]
    require(manual['server_argv']==setup['server_argv'] and manual['server_env']==setup['server_env'],'수동 제어군의 Frozen 설정이 변경됐습니다')
    require(hashlib.sha256(json.dumps(setup['manual_configuration'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==setup['manual_configuration_sha256'],'수동 구성 해시가 다릅니다')
    require(auto['configuration_sha256']==setup['portable_configuration_sha256'],'자동 구성 해시가 다릅니다')
    require(all(manual[k]==auto[k]for k in ['server_argv','server_env','server_cpus']),'자동 설정과 수동 제어 조건이 다릅니다')
    require(manual['server_cpus']==[0,2,4,6],'이번 실제 검증 장비는 i5 P4만 허용합니다')
    require(Path(manual['topology']).read_bytes()==Path(auto['topology']).read_bytes(),'수동/자동 tier 입력이 다릅니다')
    return checked


def validate_runtime(path,visit,setup):
    path=Path(path);wanted=case(setup,visit['configuration_mode']);record=read(path/'configuration-runtime.json')
    require(record==wanted,'실제 Session에 전달한 설정 provenance가 다릅니다')
    contract=read(path/'thread-contract.json')
    require(contract['argv']==wanted['server_argv'],'실제 서버 argv가 선택된 설정과 다릅니다')
    require(all(contract['openmp'][k]==wanted['server_env'][k]for k in contract['openmp']),'실제 OpenMP 환경이 선택한 설정과 다릅니다')
    stages=[read(path/name)for name in ['policies-before.json','policies-pre-measured.json','policies-after.json']]
    require(all(stage and all(row['policy']==7 and row['affinity']==wanted['server_cpus']for row in stage)for stage in stages),'실제 TID 정책 또는 mask가 다릅니다')
    team=read(path/'openmp-team.json');require(team['expected_team_size']==4 and team['team_indexes']==[0,1,2,3],'실제 OpenMP 팀 증거가 다릅니다')
    stats=read(path/'scheduler-stats.json')
    require(all(any(row['cpu']==cpu and row['runtime_ns']>0 for row in stats['cpus'])for cpu in wanted['server_cpus']),'기능 요청 세션에서 P4 전체 실행 증거를 확인하지 못했습니다')
    return dict(argv=contract['argv'],openmp=contract['openmp'],server_cpus=wanted['server_cpus'],policy=7,
                compute_threads=contract['compute_threads'],batch_threads=contract['batch_threads'],http_threads=contract['http_threads'],
                team_size=team['expected_team_size'],team_indexes=team['team_indexes'],ops='llama_new_a',flags=8,diagnostics='FULL')


def equivalence(summary):
    require(summary['mode']=='FUNCTIONAL' and summary['complete'] and summary['functional_samples']==2,'수동/자동 실제 기능 세션이 완결되지 않았습니다')
    rows={row['configuration_mode']:row for row in summary['visits']}
    require(set(rows)=={'MANUAL','AUTO'} and all(row['valid']for row in rows.values()),'기능 비교 설정 세션이 누락됐습니다')
    require(rows['MANUAL']['runtime_signature']==rows['AUTO']['runtime_signature'],'실제 런타임 실행 구성 비교가 일치하지 않습니다')
    return dict(status='OBSERVED_RUNTIME_CONFIGURATION_MATCH',signature=rows['AUTO']['runtime_signature'],
                limitation='TID/스케줄 이벤트/성능이 동일하다는 뜻은 아니다. OpenMP native handle과 OSTID 역할 대응은 NOT_AVAILABLE다.')
