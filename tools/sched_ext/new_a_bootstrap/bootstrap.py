# SPDX-License-Identifier: GPL-2.0
"""GitHub-first Linux tuning with independent source/model provisioning."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid
import auto_run
import auto_system as system
import provision
require=provision.require


def write_report(folder,state):
    system.save(folder/'bootstrap-result.private.json',state)
    lines=['# Intel Hybrid Auto-Tuning Bootstrap','', '상태: '+state['status'],'', 'Linux 설정 탐색이며 NEW_A BPF 정책 우위 검증이 아니다. BPF 자동 attach 없음.','', '기본 Compute=P / Batch=P+floor(E/2), compact E-half, SMT 제외. 예외 추천은 별도 최소6대응블록의 사전 관문을 요구한다.','']
    for key in ['engine','model','cpu','prior_manual_recovery_evidence','power_restore_status','power_restore_journal','tuning_output','error','restore_error','disk']:
        if key in state:lines.extend([key+': '+json.dumps(state[key],ensure_ascii=False),''])
    lines.extend(['새 빌드와 과거 Frozen의 byte identity/성능 동등성은 주장하지 않는다.','상세 표와 추천: tuning/analysis/*.md, *.csv, *.json','STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED'])
    (folder/'REPORT.ko.md').write_text('\n'.join(lines)+'\n')


def run(driver,base,check=False,prepare_only=False,guard=None):
    recipe=json.loads((driver.ROOT/'config/autotune-provision.json').read_text())
    require(os.getuid()!=0 and os.sched_getscheduler(0)==0,'Normal SCHED_OTHER user required')
    if not check:require(os.isatty(0),'Use a normal terminal for approvals; --check-bootstrap performs no experiment')
    base=Path(base).resolve();base.mkdir(mode=0o700,parents=True,exist_ok=True)
    prior_recoveries=[]
    for old in (base/'runs').glob('*/environment.private.json'):
        value=json.loads(old.read_text())
        if value.get('attempted')and value.get('schema')==2 and value['status']=='RESTORED':
            system.audit_restoration(old)
        if value.get('attempted')and value['status']!='RESTORED':
            evidence=system.pending_recovery(old,lambda:auto_run.quiet(driver.engine()),readonly=check)
            if evidence:prior_recoveries.append(str(evidence))
    folder=base/'runs'/('bootstrap-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]);folder.mkdir(mode=0o700,parents=True)
    print('BOOTSTRAP_RESULT_DIRECTORY='+str(folder),flush=True)
    state=dict(prior_manual_recovery_evidence=prior_recoveries,status='PREPARING',bpf_attach='NOT_RUN',inference='NOT_RUN',git_source=recipe['engine']['commit'],model_transfer='NOT_RUN',scheduler='linux')
    journal=folder/'environment.private.json';module=None;restore_error=None
    try:
        free=shutil.disk_usage(base).free;state['disk']=dict(free=free,required=recipe['minimum_free_bytes'])
        packages=system.missing_packages()
        for tool,package in [('cmake','cmake'),('c++','build-essential'),('git','git'),('pkg-config','pkg-config')]:
            if not shutil.which(tool):packages.append(package)
        state['packages_required']=sorted(set(packages))
        if check:
            state['status']='CHECK_ONLY_NOT_RUNTIME_ADMISSION';state['source_recipe_verified_by_manifest']=True
            state['disk_sufficient']=free>=recipe['minimum_free_bytes'];return 0
        require(free>=recipe['minimum_free_bytes'],'DISK_SPACE_BLOCKED: at least 6GiB free required for source/build/model/results; no existing data deleted')
        module=driver.engine();require(module.b.contracts.scheduler_state()=='disabled','Compatible sched_ext kernel required; no kernel install or scheduler detach')
        module.b.controller.foreign_processes()
        env=module.b.collect.environment();require(env['ac_online'],'AC required before provisioning/build; connect AC manually')
        temperatures=[int(r['value'])/1000 for r in env['temperature_millic']if r['value']is not None]
        require(temperatures and max(temperatures)<85,'THERMAL_NOT_READY before provisioning/build')
        original=system.snapshot();system.save(folder/'before-install-environment.private.json',original)
        system.install(sorted(set(packages)),folder)
        initial_changes=system.power_plan(original);system.preregister(initial_changes,journal)
        restore='#!/usr/bin/env bash\nexec bash '+shlex.quote(str(driver.ROOT/'scripts/autotune-bootstrap.sh'))+' --restore-environment '+shlex.quote(str(journal))+'\n';(folder/'restore-environment.sh').write_text(restore)
        kernel_build=module.b.universal_build(folder/'kernel-build')
        state['kernel_static_build']=str(kernel_build)
        # Approval covers fixed sources/weights and bounded build, not inference.
        system.consent('고정 리비전 엔진 소스와 정확한 GGUF를 확보하고 CPU/OpenMP 서버를 직렬 빌드합니다.\n소스: '+recipe['engine']['repository']+' @ '+recipe['engine']['commit']+'\n모델: '+recipe['model']['url']+'\n크기: '+str(recipe['model']['size'])+' bytes; SHA256='+recipe['model']['sha256']+'\n라이선스: '+recipe['model']['license']+'\n모델/바이너리는 Git에 업로드하지 않습니다. 빌드 중 추론은 하지 않습니다. 이 준비 작업을 승인합니까?')
        source=provision.source(base/'engine-source',recipe)
        binary=provision.build(source,base/'engine-build',recipe,module.b.contracts)
        state['engine']=dict(source_commit=recipe['engine']['commit'],build_proof=str(binary.parent/'reproduction.private.json'),build='PASS',historical_byte_identity=False)
        model=provision.model(base/'model-cache',recipe);state['model_transfer']='HASH_VERIFIED'
        state['model']=dict(path=str(model),sha256=recipe['model']['sha256'],identity='HASH_AND_GGUF_VERIFIED')
        if prepare_only:
            state['status']='ASSETS_PREPARED_FUNCTIONAL_NOT_RUN';return 0
        probe=module.b.probe_build(folder/'probe');detected=module.b.collect.inspect(probe,None)
        default=module.p.default(detected);state['cpu']=dict(summary=detected['summary'],default=default)
        changes=system.power_plan(original);require(changes==initial_changes,'Power plan changed during provisioning; new review required')
        restore='#!/usr/bin/env bash\nexec bash '+shlex.quote(str(driver.ROOT/'scripts/autotune-bootstrap.sh'))+' --restore-environment '+shlex.quote(str(journal))+'\n';(folder/'restore-environment.sh').write_text(restore)
        system.consent('Linux Auto-Tuning 범위를 승인합니다: 최대160 독립 서버/7200초, 각 워밍업2+측정1, 2블록 선별 및 유망 후보의 새 최소6블록.\nCPU/default: '+str(state['cpu'])+'\nSTRICT_ACCEPTANCE=FAILED / safety_verdict=FAIL / OVERHEAD_REVIEW_REQUIRED 유지.\n실패 자동 재시도/백그라운드 인증 갱신/BPF 자동 attach 없음. 정리 확인된 세션 경계에서만 직접 비밀번호 재인증을 요청합니다. 기능 검증 실패 시 측정 차단.')
        require(auto_run.quiet(module),'CLEANUP_NOT_VERIFIED before power apply')
        system.apply(changes,journal)
        target=folder/'tuning';state['tuning_output']=str(target)
        arguments=['--build-report',str(kernel_build),'--server',str(binary/'llama-server'),'--server-sha256',provision.sha(binary/'llama-server'),'--model',str(model),'--model-sha256',recipe['model']['sha256'],'--library-dir',str(binary),'--marker-elf',str(binary/'libllama-server-impl.so'),'--output',str(target),'--execute','--acknowledge-existing-safety-fail']
        args,command=driver.action('run',arguments,module)
        ready=driver.dependencies(args,True,module);driver.admission_record(args,command,driver.verify(),ready)
        if guard is not None:fcntl.flock(guard,fcntl.LOCK_UN)
        code=auto_run.child(command);state['inference']='SEE_NATIVE_RAW_EVIDENCE';state['child_returncode']=code
        require(code==0,'AUTO_TUNING_STOPPED; no automatic retry, preserve raw')
        require((target/'DONE.json').is_file(),'No native completion evidence; cannot report complete')
        state['status']='COMPLETE';return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,KeyboardInterrupt)as error:
        state['status']='BLOCKED_OR_STOPPED';state['error']=type(error).__name__+': '+str(error);raise
    finally:
        if journal.is_file():
            try:
                if guard is not None:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
                system.restore(journal,lambda:module is not None and auto_run.quiet(module))
            except (OSError,ValueError,subprocess.SubprocessError,KeyboardInterrupt)as error:
                restore_error=str(error);state['restore_error']=restore_error;state['status']='ENVIRONMENT_RESTORE_REQUIRED'
                print('복원 필요: bash '+shlex.quote(str(folder/'restore-environment.sh')),flush=True)
        if journal.is_file():
            state['power_restore_journal']=str(journal);state['power_restore_status']=json.loads(journal.read_text()).get('status','NOT_VERIFIED')
        write_report(folder,state);print('한국어 보고서: '+str(folder/'REPORT.ko.md'),flush=True)
        if restore_error:raise ValueError('ENVIRONMENT_RESTORE_REQUIRED: '+restore_error)


def entry(driver,workspace=None,check=False,prepare_only=False):
    base=workspace or driver.ROOT/'.autotune-work'
    if check:return run(driver,base,check=True)
    fd=os.open(Path('/tmp')/('new-a-auto-prepare-'+str(os.getuid())+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    guard=os.open(Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(fd).st_uid==os.getuid()and os.fstat(guard).st_uid==os.getuid(),'Foreign lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with auto_run.preparation_signals():
            return run(driver,base,prepare_only=prepare_only,guard=guard)
    finally:os.close(guard);os.close(fd)
