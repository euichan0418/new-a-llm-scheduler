#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""C/E의 고정 11/8 CPU 허용 집합만 비교하는 일회성 실행기다."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import uuid
from records import read, save, require, seal_check, ttwu_enabled

PREP = Path(__file__).resolve().parent
REPO = Path(read(PREP / 'setup.json')['repo'])
sys.path.append(str(REPO / 'tools/sched_ext/new_a_analysis'))
sys.path.append(str(REPO / 'tools/sched_ext/new_a_full_guard'))
import host_checks as h
import fg_runner as base
from fg_config import LABELS, SERVER_CPUS, HOUSE_CPUS
import validation as v
from kernel_guard import KernelGuard
import study

PLAN = study.plans()
CURRENT = {}
OWNED_WATCH = None
ORIGINAL_LLM = base.run_llm
ORIGINAL_QUIESCENT = h.quiescent

def quiescent():
    ORIGINAL_QUIESCENT()
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        if int(proc.name) == os.getpid():
            continue
        try:
            argv = (proc / 'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if any('/llama.cpp-lightweight/benchmark-results/' in arg and Path(arg).name in {'observer', 'route', 'loader-E', 'loader-V3', 'kernel-watch'} for arg in argv):
                if OWNED_WATCH is not None and OWNED_WATCH.owns(int(proc.name)):
                    continue
                found.append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
        except PermissionError:
            try:
                if (proc / 'comm').read_text().strip() in {'observer', 'route', 'loader-E', 'loader-V3', 'kernel-watch'}:
                    found.append(int(proc.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
    require(not found, '기존 관측기·Probe·로더 프로세스가 남아 있습니다: ' + repr(found))

h.quiescent = quiescent

def preflight():
    seal_check(PREP)
    setup = h.read('setup.json')
    require(os.getuid() == setup['uid'] == REPO.stat().st_uid != 0 and os.sched_getscheduler(0) == 0, '일반 소유자 계정과 Linux 클라이언트 스케줄링으로 실행해야 합니다')
    require(h.common.command(['git', 'rev-parse', 'HEAD']) == setup['head'] and h.common.command(['git', 'branch', '--show-current']) == 'feature/new-a-scheduler', '저장소 또는 브랜치가 변경됐습니다')
    require(setup['phase'] == 'AFFINITY_FACTOR' and setup['comparison_plan'] == PLAN and setup['planned_sessions'] == 8 and setup['planned_requests'] == 24, '8세션 Affinity 비교 범위가 변경됐습니다')
    require(setup['instrumentation'] == v.INSTRUMENTATION and setup['authorization'] == 'USER_REQUEST_AFFINITY_BEFORE_V4', '외부 관측 비활성화 또는 독립 비교 범위가 다릅니다')
    profile = h.common.load_profile(PREP / 'host-profile.exploratory.json')
    h.common.THREADS.update(compute=8, batch=8, http=2)
    h.step10.MODEL = h.common.MODEL
    require(h.common.SERVER_CPUS == SERVER_CPUS and h.common.HOUSE_CPUS == HOUSE_CPUS and profile['workload_constraints'] == setup['workload_constraints'], '8/8/2 또는 CPU 배정이 변경됐습니다')
    state = h.check_host(profile, initial=True)
    stored = h.read('topology.json')
    types = {int(k): value for k, value in stored['architectural_types'].items()}
    require(profile['topology'] == stored['topology'] and h.topology.classify(profile['topology'], types) == stored['policy'] and h.topology.encode(stored['policy']) == (PREP / 'topology.bin').read_bytes(), 'CPU 토폴로지 또는 tier 인코딩이 변경됐습니다')
    h.common.scheduler_check()
    require(h.common.active_run() == Path(setup['base_run']), 'Frozen 서버 포인터가 변경됐습니다')
    _, bundle = h.compare.validate_bundle(Path(setup['base_run']))
    argv, _ = h.step10.server_argv(Path(setup['base_run']), 'C')
    env = h.common.bundle_env(bundle)
    expected = {key: env[key] for key in ['PATH', 'LD_LIBRARY_PATH', 'OMP_NUM_THREADS', 'OMP_DYNAMIC']}
    expected['LC_ALL'] = 'C'
    require(argv == setup['server_argv'] and expected == setup['server_env'] and hashlib.sha256(h.common.canonical(h.common.REQUEST)).hexdigest() == setup['request_sha256'] == h.common.REQUEST_SHA, 'Frozen 서버·모델·요청·환경 설정이 변경됐습니다')
    require(read(setup['original_acceptance'])['status'] == 'FAILED' and read(setup['original_diagnosis'])['safety_verdict'] == 'FAIL', '기존 strict 또는 safety 판정이 변경됐습니다')
    static = read(setup['build_static'])
    require(static['status'] == 'PASS' and static['kernel'] == profile['host']['kernel'] and static['btf_sha256'] == profile['host']['btf']['sha256'] and static['kfunc_signatures']['status'] == 'PASS' and static['ops_flags'] == dict(E=8), '컴파일·정적 호환성 증거가 현재 환경과 다릅니다')
    require(all(x['executable_sections_equal'] and x['relocations_equal'] for x in static['identity'].values()), '기존 FULL 객체와 코드 동일성을 확인할 수 없습니다')
    require(Path('/dev/kmsg').is_char_device(), '필수 커널 감시 장치가 없습니다')
    h.quiescent()
    return profile, bundle, dict(**LABELS, readiness='READY_FOR_AFFINITY_FACTOR_ONLY', current=state, plan=PLAN, instrumentation=v.INSTRUMENTATION, planned_measurements=8, sudo='NOT_RUN', verifier='NOT_RUN', inference='NOT_RUN')

def new_session(path, policy, bundle, **unused):
    setup = h.read('setup.json')
    h.session.HERE = PREP / 'layout' / 'E'
    h.session.CHILD = REPO / 'tools/sched_ext/build/bin/llama_scx_child'
    return h.session.Session(path, setup['server_argv'], set(CURRENT['server_cpus']), HOUSE_CPUS, policy=policy, topology=PREP / 'topology.bin', diagnostics=policy=='E', env=setup['server_env'], marker=bundle / 'bin/libllama-server-impl.so')

def attached(item, path):
    if item.policy == 'C':
        require(item.loader is None and h.common.state()=='disabled', 'Linux 대조군에 sched_ext가 있습니다')
        save(path/'kernel-load-attach.json', dict(scheduler='LINUX',verifier='NOT_APPLICABLE',attach='NOT_APPLICABLE'))
        return
    name = 'llama_new_a'
    require(item.loader.poll() is None and item.process.poll() is None and h.common.state() == 'enabled' and Path('/sys/kernel/sched_ext/root/ops').read_text().strip() == name, '스케줄러 attach 상태가 다릅니다')
    require('FULL_GUARD_MONITOR_READY' in (path / 'loader.log').read_text(), 'FULL Mask·Tracking·커널 감시가 준비되지 않았습니다')
    value = __import__('json').loads(h.common.command(['sudo', '-n', 'bpftool', '-j', 'struct_ops', 'dump']))
    save(path / 'struct-ops-dump.json', value)
    save(path / 'kernel-load-attach.json', dict(verifier='LOAD_SUCCEEDED', attach='OBSERVED', ops=name, flags=v.ops_flags(value, name), live_monitor='READY'))

def healthy(item):
    require(item.process.poll() is None and (item.loader is None or item.loader.poll() is None) and h.common.state() == ('disabled' if item.policy=='C' else 'enabled'), '서버·로더·스케줄러가 예기치 않게 종료됐습니다')
    return h.step10.health()

def run_llm(path, policy, bundle, baseline, sample, result, *, functional=False):
    require(functional is False, '재확인 비교에서는 측정 요청만 허용합니다')
    save(path / 'instrumentation.json', v.INSTRUMENTATION)
    ORIGINAL_LLM(path, policy, bundle, baseline, sample, result, functional=False)
    if policy=='C':save(path/'scheduler-stats.json',dict(instrumentation_mode='LINUX',bpf_counters='NOT_AVAILABLE'))

class SafeMonitor(h.Monitor):
    def __enter__(self):
        global OWNED_WATCH
        self.kernel=KernelGuard(self.path.parent,h.read('setup.json')['kernel_watch_binary'])
        try:
            self.kernel.start()
            OWNED_WATCH=self.kernel
            return super().__enter__()
        except BaseException:
            try:self.kernel.close()
            finally:OWNED_WATCH=None
            raise
    def sample(self):
        self.kernel.check()
        return super().sample()
    def __exit__(self,kind,error,trace):
        global OWNED_WATCH
        try:return super().__exit__(kind,error,trace)
        finally:
            try:self.kernel.close()
            finally:OWNED_WATCH=None

h.Monitor=SafeMonitor
base.v = v
base.new_session = new_session
base.attached = attached
base.healthy = healthy
base.run_llm = run_llm
base.PREP = PREP

def authenticate(logs, name):
    print(name + ': 일반 터미널 sudo 인증을 요청합니다. 자동 재시도·인증 연장은 하지 않습니다.', flush=True)
    code = subprocess.run(['sudo', '-v'], check=False).returncode
    save(logs / (name + '-sudo.json'), dict(returncode=code))
    require(code == 0, 'sudo 인증에 실패했습니다')


def execute_visit(visit, profile, bundle):
    CURRENT.clear(); CURRENT.update(visit)
    prior_mask=h.compare.SERVER_CPUS
    prior_guard=v.ACTIVE_MASK
    h.compare.SERVER_CPUS=set(visit['server_cpus'])
    v.ACTIVE_MASK=set(visit['server_cpus'])
    start = time.monotonic_ns()
    wall = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        base.run_visit(visit, profile, bundle, functional=False)
    finally:
        h.compare.SERVER_CPUS=prior_mask
        v.ACTIVE_MASK=prior_guard
        path = PREP / visit['relative_dir']
        if path.exists():
            save(path / 'visit-timing.json', dict(start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(), start_utc=wall, end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    checked = study.load_visit(PREP, visit)
    require(checked['valid'], '원시 증거 검증 실패로 다음 세션을 중단합니다: ' + str(checked.get('error')))

def terminal(execute, logs):
    out = dict(**LABELS, status='FAILED', errors=[], is_safety_approval=False)
    owns = False
    handlers = {sig: signal.signal(sig, h.interrupted) for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]}
    try:
        h.require_fresh()
        profile, bundle, technical = preflight()
        save(logs / 'preflight.json', technical)
        if not execute:
            out['status'] = 'PREFLIGHT_COMPLETE_NO_EXECUTION'
        else:
            save(PREP / 'terminal-attempt.json', dict(**LABELS, attempt_id=uuid.uuid4().hex, automatic_retry=False))
            owns = True
            for visit in PLAN:
                profile, bundle, _ = preflight()
                authenticate(logs, visit['visit_id'])
                profile, bundle, _ = preflight()
                profile, bundle, _ = preflight()
                base.stage('visit-start', dict(visit=visit))
                execute_visit(visit, profile, bundle)
                h.cleanup_verified(PREP / visit['relative_dir'])
                h.quiescent()
                base.stage('visit-complete', dict(visit=visit, cleanup='PASS', sched_ext='disabled'))
                save(logs / (visit['visit_id'] + '-progress.json'), study.summarize(PREP))
                print(visit['visit_id'] + ': 완료; 외부 관측기·Probe 미실행; cleanup PASS; sched_ext disabled', flush=True)
            require(study.summarize(PREP)['complete'], '두 대응 라운드가 모두 완료되지 않았습니다')
            preflight()
            h.quiescent()
            out['status'] = 'AFFINITY_FACTOR_COMPLETE_NOT_V4_OR_SAFETY_APPROVED'
    except BaseException as error:
        out['errors'].append(type(error).__name__ + ': ' + str(error))
        print('중단: ' + out['errors'][-1], flush=True)
        with (logs / 'failure.log').open('x') as output:
            traceback.print_exc(file=output)
    finally:
        try:
            require(h.common.state() == 'disabled', '최종 sched_ext 상태가 disabled가 아닙니다')
        except BaseException as error:
            out['errors'].append(str(error))
        if out['errors']:
            out['status'] = 'FAILED'
        if owns:
            try:
                summary = study.summarize(PREP)
                summary['terminal'] = out
                save(PREP / 'summary.json', summary)
                study.print_summary(summary)
                if not out['errors']:
                    save(PREP / 'completion.json', dict(**LABELS, status=out['status'], automatic_next_stage_admission=False, is_safety_approval=False))
            except BaseException as error:
                out['errors'].append('요약 저장 실패: ' + str(error))
                out['status'] = 'FAILED'
            if out['errors'] and not (PREP / 'STOPPED.json').exists():
                save(PREP / 'STOPPED.json', out)
        save(logs / 'terminal-result.json', out)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        print('최종 상태=' + out['status'] + '; sched_ext=' + h.common.state() + '; 로그=' + str(logs), flush=True)
    return int(bool(out['errors']))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    with (PREP / '.terminal.lock').open('a') as local, (REPO / 'benchmark-results/.new-a-v2-functional.lock').open('a') as shared:
        fcntl.flock(local, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logs = PREP / 'terminal-logs' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
        logs.mkdir(parents=True)
        with (logs / 'terminal.log').open('x') as output, contextlib.redirect_stdout(h.Tee(sys.stdout, output)), contextlib.redirect_stderr(h.Tee(sys.stderr, output)):
            return terminal(args.execute, logs)

if __name__ == '__main__':
    raise SystemExit(main())
