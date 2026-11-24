# SPDX-License-Identifier: GPL-2.0
"""Automatic Linux configuration experiments; explicit approvals, no BPF auto attach."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
import auto_assets as assets
import auto_system as system
require=assets.require


def quiet(module):
    try:
        require(module.b.contracts.scheduler_state()=='disabled','sched_ext active')
        module.b.controller.foreign_processes();return True
    except (OSError,ValueError):return False


def child(command):
    process=subprocess.Popen(command)
    old={}
    def stop(sig,frame):
        if process.poll()is None:process.send_signal(sig)
    try:
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:old[sig]=signal.signal(sig,stop)
        return process.wait()
    except BaseException:
        if process.poll()is None:process.terminate()
        process.wait(timeout=390);raise
    finally:
        for sig,value in old.items():signal.signal(sig,value)


def report(session,state):
    system.save(session/'auto-result.private.json',state)
    text=['# NEW_A Intel Hybrid 자동 실행 결과','', '상태: '+state['status'], '', '이 실행은 Linux 설정 탐색이며 NEW_A BPF 정책 자체의 우위/최적값을 입증하지 않는다.', '', '기본값: Compute=P, Batch=P+floor(E/2), P 대표+E 절반 compact, SMT 제외.', '2블록은 선별용이다. 기본값 예외는 별도의 최소6개 대응 블록과 사전 HTTP/phase/CPU 관문이 필요하다.', '미미하거나 불확실한 결과는 기본값 유지이며 통계적 동등성으로 해석하지 않는다.','']
    for key in ['assets','cpu','packages_required','environment_plan','tuning_output','error','restore_error']:
        if key in state:text += [key+': '+json.dumps(state[key],ensure_ascii=False), '']
    text+=['상세 성능 표/관측상 최고값/최종 추천: tuning/analysis/*.md, *.csv, *.json', '', 'STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED', '', '패키지 준비 검사/모의 테스트는 실제 verifier/attach/성능 검증이 아니다.']
    (session/'REPORT.ko.md').write_text('\n'.join(text)+'\n')


def restore_only(driver,journal):
    path=Path(journal).resolve()
    require(path.is_file()and path.stat().st_uid==os.getuid()and os.getuid()!=0,'Normal owner journal required')
    module=driver.engine()
    fd=os.open(Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(fd).st_uid==os.getuid(),'Foreign runtime lock');fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        system.restore(path,lambda:quiet(module),reauthenticate=True)
    finally:os.close(fd)
    print('원래 전원 설정 복원 완료: '+str(path));return 0


def run(driver,*,check=False,search_roots=None,guard_fd=None):
    require(os.getuid()!=0 and os.sched_getscheduler(0)==0,'일반 사용자 SCHED_OTHER 터미널에서 실행하십시오. sudo로 이 스크립트를 실행하지 마십시오.')
    if not check:require(os.isatty(0),'--auto requires a normal terminal for explicit approvals and sudo; use --auto-check for read-only diagnostics')
    home=Path.home();results=driver.ROOT.parent/'new-a-autotune-results'
    require(shutil.disk_usage(driver.nearest(results)).free>=driver.RESERVE,'DISK_SPACE_BLOCKED: 512MiB reserve required')
    results.mkdir(mode=0o700,exist_ok=True)
    if not check:
        for journal in results.glob('auto-*/environment.private.json'):
            value=json.loads(journal.read_text())
            require(not value.get('attempted')or value['status']=='RESTORED','PENDING_ENVIRONMENT_RESTORE: '+str(journal)+'; run the restore-environment.sh in that directory before a new auto run')
    session=results/('auto-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]);session.mkdir(mode=0o700)
    print('AUTO_RESULT_DIRECTORY='+str(session),flush=True)
    state=dict(status='PREPARING',scheduler='linux',bpf_attach='NOT_RUN',inference='NOT_RUN',session=str(session),automatic_retry=False)
    journal=session/'environment.private.json';module=None;restore_error=None
    try:
        pins=json.loads((driver.ROOT/'AUTO_ASSET_PINS.json').read_text())
        selection=assets.discover(pins,search_roots if search_roots is not None else assets.roots(driver.ROOT))
        state['assets']=selection;system.save(session/'asset-discovery.private.json',selection)
        required=system.missing_packages();state['packages_required']=required
        if check:
            state['environment_plan']='DEPENDENCIES_REQUIRED_BEFORE_COMPLETE_CHECK'if required else system.power_plan()
            if not required:
                module=driver.engine();staged=assets.stage_assets(selection,pins,session/'approved-libraries')
                state['elf']=assets.validate_elf(selection,staged,module.b.contracts)
            state['status']='AUTO_CHECK_ONLY_NO_EXPERIMENT';return 0
        original=system.snapshot();system.save(session/'before-install-environment.private.json',original)
        system.install(required,session)
        require(not system.missing_packages(),'DEPENDENCIES_STILL_MISSING; no experiment')
        module=driver.engine();module.b.controller.foreign_processes()
        require(module.b.contracts.scheduler_state()=='disabled','sched_ext must be disabled')
        env=module.b.collect.environment();require(env['ac_online'],'AC_NOT_CONNECTED; attach AC manually; no automatic bypass')
        temps=[int(r['value'])/1000 for r in env['temperature_millic']if r['value']is not None]
        require(temps and max(temps)<85,'THERMAL_NOT_READY: missing sensor or >=85C; no stress/automatic threshold relaxation')
        probe=module.b.probe_build(session/'probe');detection=module.b.collect.inspect(probe,None)
        default=module.p.default(detection);state['cpu']=dict(summary=detection['summary'],default=default)
        staged=assets.stage_assets(selection,pins,session/'approved-libraries')
        compatible=assets.validate_elf(selection,staged,module.b.contracts);state['elf']=compatible
        changes=system.power_plan(original);state['environment_plan']=changes
        system.preregister(changes,journal)
        restore_script='#!/usr/bin/env bash\nexec bash '+shlex.quote(str(driver.ROOT/'run-autotune.sh'))+' --restore-environment '+shlex.quote(str(journal))+'\n'
        (session/'restore-environment.sh').write_text(restore_script)
        # Permission to run this Linux batch is separate from system preparation consent.
        system.consent('Linux 기본 스케줄러에서 Auto-Tuning을 실행합니다. NEW_A BPF를 attach하지 않습니다.\nCPU: '+str(detection['summary'])+'\n기본 구성: '+str(default)+'\n최대160서버 세션/7200초, 후보별 독립 서버/워밍업2/측정1, 2블록 선별 후 필요할 때 독립6블록 확증.\nSTRICT_ACCEPTANCE=FAILED / safety_verdict=FAIL / OVERHEAD_REVIEW_REQUIRED를 유지합니다.\n실패 자동 재시도·인증 갱신은 없으며 결과를 보존하고 중단합니다.')
        system.save(session/'experiment-consent.private.json',dict(approved=True,scheduler='linux',default=default,maximum_sessions=160,maximum_seconds=7200,automatic_retry=False))
        system.apply(changes,journal)
        output=session/'tuning';state['tuning_output']=str(output)
        arguments=['--server',compatible['server'],'--server-sha256',next(r['sha256']for r in selection['artifacts']if r['role']=='server'),'--model',selection['model'],'--model-sha256',pins['model']['sha256'],'--library-dir',str(staged),'--marker-elf',compatible['marker'],'--output',str(output),'--execute','--acknowledge-existing-safety-fail']
        args,command=driver.action('run',arguments,module)
        preflight=driver.dependencies(args,True,module)
        driver.admission_record(args,command,driver.verify(),preflight)
        state['status']='RUNNING';system.save(session/'auto-state.private.json',state)
        if guard_fd is not None:fcntl.flock(guard_fd,fcntl.LOCK_UN)
        code=child(command);state['child_returncode']=code
        state['inference']='SEE_NATIVE_RAW_EVIDENCE';state['status']='COMPLETE'if code==0 else'EXPERIMENT_STOPPED'
        require(code==0,'AUTO_TUNING_STOPPED: no automatic retry; raw data retained')
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,KeyboardInterrupt)as error:
        state['status']='BLOCKED_OR_STOPPED';state['error']=type(error).__name__+': '+str(error);raise
    finally:
        if journal.is_file():
            try:
                if guard_fd is not None:fcntl.flock(guard_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                system.restore(journal,lambda:module is not None and quiet(module))
            except (OSError,ValueError,KeyError,subprocess.SubprocessError)as error:
                restore_error=str(error);state['restore_error']=restore_error;state['status']='ENVIRONMENT_RESTORE_REQUIRED'
                print('복원 미완료. 새 실험 금지. 정리 확인 후 실행: bash '+shlex.quote(str(session/'restore-environment.sh')),file=sys.stderr)
        report(session,state)
        print('한국어 보고서: '+str(session/'REPORT.ko.md'),flush=True)
        if restore_error:raise ValueError('ENVIRONMENT_RESTORE_REQUIRED: '+restore_error)


def locked_run(driver,check=False):
    if check:return run(driver,check=True)
    lock=Path('/tmp')/('new-a-auto-prepare-'+str(os.getuid())+'.lock')
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    guard=os.open(Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(fd).st_uid==os.getuid()and os.fstat(guard).st_uid==os.getuid(),'Foreign runtime lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return run(driver,guard_fd=guard)
    finally:os.close(guard);os.close(fd)
