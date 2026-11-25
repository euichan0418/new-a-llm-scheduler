# SPDX-License-Identifier: GPL-2.0
"""Automatic Linux configuration experiments; explicit approvals, no BPF auto attach."""
from contextlib import contextmanager
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


@contextmanager
def preparation_signals():
    previous={}
    def stop(sig,frame):
        for item in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:signal.signal(item,signal.SIG_IGN)
        raise KeyboardInterrupt('Signal '+str(sig)+'; preserve evidence and verify process cleanup')
    try:
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:previous[sig]=signal.signal(sig,stop)
        yield
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


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
        if json.loads(path.read_text()).get('schema')!=2:
            system.pending_recovery(path,lambda:quiet(module))
            print('수동 복구 상태 검증/새 기준선 승인만 기록했습니다. 과거 실패 저널은 보존했습니다.');return 0
        system.restore(path,lambda:quiet(module),reauthenticate=True)
    finally:os.close(fd)
    print('원래 전원 설정 복원 완료: '+str(path));return 0


def run(driver,*,check=False,search_roots=None,guard_fd=None):
    # Legacy asset-discovery auto entry delegates to the same unmanaged bootstrap.
    # The historical journal restore tool above is explicitly manual and separate.
    import bootstrap
    return bootstrap.run(driver,driver.ROOT/'.autotune-work',check=check,guard=guard_fd)


def locked_run(driver,check=False):
    if check:return run(driver,check=True)
    lock=Path('/tmp')/('new-a-auto-prepare-'+str(os.getuid())+'.lock')
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    guard=os.open(Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        require(os.fstat(fd).st_uid==os.getuid()and os.fstat(guard).st_uid==os.getuid(),'Foreign runtime lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with preparation_signals():
            return run(driver,guard_fd=guard)
    finally:os.close(guard);os.close(fd)
