#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""현재 AC 환경에서 정책 변경 없는 C/E Affinity 요인 비교를 준비한다."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from records import read, save, sha, require, verify

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SOURCE = REPO / 'benchmark-results/season2/new-a-v3-overhead/20261110T121524Z-c5a870-functional-review/B-overhead'

def review_source():
    verify(read(SOURCE / 'prepared.json'))
    seal = read(SOURCE / 'seal.json')
    require(sha(SOURCE / 'prepared.json') == seal['prepared_sha256'] and sha(SOURCE / 'host-profile.exploratory.json') == seal['profile_sha256'], '기존 B 봉인 값이 다릅니다')
    code = "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);sys.path.append(str(Path(sys.argv[2])/'tools/sched_ext/new_a_analysis'));import study;s=study.summarize(Path(sys.argv[1]));old=json.loads((Path(sys.argv[1])/'summary.json').read_text());assert s=={k:v for k,v in old.items() if k!='terminal'};assert s['complete'] and s['performance_samples']==12;print(json.dumps({k:v for k,v in s.items() if k in ['statistics','complete','performance_samples','strict_acceptance','safety_verdict','admits_c']}))"
    summary = __import__('json').loads(subprocess.check_output([sys.executable, '-B', '-c', code, str(SOURCE), str(REPO)], cwd=REPO, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), text=True))
    terminal = read(SOURCE / 'terminal-logs/20261110T123721Z-4cbfae57/terminal-result.json')
    require(terminal['errors'] == [] and terminal['status'] == 'V3_OBSERVER_OVERHEAD_COMPLETE_NOT_NEXT_STAGE_ADMITTED' and summary['admits_c'] is False and not (SOURCE / 'STOPPED.json').exists(), '기존 B 완료 증거가 유효하지 않습니다')
    return dict(status='SOURCE_REVALIDATED', source=str(SOURCE), source_statistics=summary['statistics'], performance_samples_from_source_used_in_new_study=0,
                original_admits_next_stage=read(SOURCE / 'overhead-completion.json')['admits_next_stage'], strict_acceptance='FAILED', safety_verdict='FAIL')

def prepare(root):
    root = Path(root).resolve()
    require(root.is_dir() and not (root / 'comparison').exists(), '새 출력 디렉터리를 사용해야 합니다')
    require(os.getuid() == REPO.stat().st_uid != 0, '일반 소유자 계정으로 준비해야 합니다')
    spec = importlib.util.spec_from_file_location('functional_preparation', REPO / 'tools/sched_ext/support/profile_preparation.py')
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    current = parent.live()
    parent.power_gate(current)
    review = review_source()
    seed = read(SOURCE / 'setup.json')
    topology, common, hp = parent.modules()
    import session
    require(not session.active_experiments(), '기존 실험 프로세스가 실행 중입니다')
    require(subprocess.check_output(['git', 'branch', '--show-current'], cwd=REPO, text=True).strip() == 'feature/new-a-scheduler', '작업 브랜치가 다릅니다')
    reference = read(SOURCE / 'topology.json')
    profile = hp.generate(seed['server_argv'][2])
    parent.physical_gate(profile['topology'], reference['topology'])
    require(set(profile['allocation']['server_cpus']) == parent.CPUS and profile['allocation']['housekeeping_cpus'] == [15], '기존 CPU 배정과 다릅니다')
    build = read(root / 'build/static-report.json')
    require(build['status'] == 'PASS' and build['kernel'] == profile['host']['kernel'] and build['btf_sha256'] == profile['host']['btf']['sha256'], '현재 커널/BTF와 빌드가 다릅니다')
    run = root / 'comparison'
    run.mkdir()
    for name in ['host_checks.py', 'environment.py', 'observations.py', 'turbo_guard.py']:
        shutil.copyfile(SOURCE / name, run / name)
    environment = (run / 'environment.py').read_text()
    start = environment.index('LIMITATION = (')
    end = environment.index('\n\n\ndef applications()', start)
    environment = environment[:start] + "LIMITATION = ('C/E 각 정책의 고정 11/8 CPU Affinity를 분리 비교하는 8T 탐색 실험이다. ' '새 BPF 정책·4T·6T 실험이 아니다. 8세션, 워밍업 16회, 측정 8회이며 각 조건 n=2다. ' '외부 Wakeup BPF 관측기·경로 Probe는 실행하지 않고 C/E 공통 읽기 전용 커널 경고 감시를 유지한다. ' 'E 내부 FULL 진단은 두 마스크에서 동일하다. 배경 앱·부하·온도는 observe-only다. ' 'P/E busy는 시스템 전체이며 서버 전용 실행 비중이 아니다. 성능 우위·동등성·안전성 승인을 확정하지 않는다.')" + environment[end:]
    (run / 'environment.py').write_text(environment)
    for name in ['records.py', 'campaign.py', 'validation.py', 'study.py', 'launch.py', 'kernel_guard.py']:
        shutil.copyfile(HERE / name, run / name)
    for policy in ['E']:
        directory = run / 'layout' / policy / 'build/bin'
        directory.mkdir(parents=True)
        (directory / 'llama_new_a_diag').symlink_to(build['binaries'][policy]['path'])
    profile['workload_constraints'] = dict(seed['workload_constraints'], affinity_conditions={'11':[0,2,4,6,8,9,10,11,12,13,14],'8':[0,2,4,6,8,9,10,11]})
    cache_inputs = [path for cpu in range(16) for index in Path(f'/sys/devices/system/cpu/cpu{cpu}/cache').glob('index*') for path in [index/'level', index/'type', index/'size', index/'shared_cpu_list'] if path.exists()]
    profile['cache_topology'] = {str(path): path.read_text().strip() for path in cache_inputs}
    profile['preparation_scope'] = 'NEW_AC_PROFILE_AFFINITY_FACTOR_ONLY'
    save(run / 'host-profile.exploratory.json', profile)
    (run / 'host-profile.exploratory.json').chmod(0o444)
    require(profile['topology'] == reference['topology'], '현재 물리 토폴로지가 기존 성공 실험과 다릅니다')
    save(run / 'topology.json', reference)
    with (run / 'topology.bin').open('xb') as target:
        target.write(topology.encode(reference['policy']))
    save(run / 'source-review.json', review)
    sys.path.insert(0, str(HERE))
    import study
    sys.path.append(str(REPO / 'tools/sched_ext/new_a_full_guard'))
    import validation
    setup = {key: seed[key] for key in ['repo', 'uid', 'base_run', 'head', 'server_argv', 'server_env', 'request_sha256', 'possible_cpus', 'workload_constraints', 'original_acceptance', 'original_diagnosis', 'strict_acceptance', 'safety_verdict', 'formal_benchmark', 'record_type', 'suite_acceptance']}
    setup.update(phase='AFFINITY_FACTOR', authorization='USER_REQUEST_AFFINITY_BEFORE_V4', comparison_plan=study.plans(), planned_sessions=8, planned_warmups=16, planned_measurements=8, planned_requests=24,
                 workload_constraints=profile['workload_constraints'], kernel_watch_binary=build['binaries']['kernel-watch']['path'], automatic_retry=False, instrumentation=validation.INSTRUMENTATION, build_static=str(root / 'build/static-report.json'), policy_binaries=build['binaries'], source_result=str(SOURCE),
                 ttwu_features_path=read(SOURCE / 'terminal-logs/20261110T123721Z-4cbfae57/r01-E_ON-runtime-preflight.json')['features_path'], automatic_next_stage_admission=False)
    save(run / 'setup.json', setup)
    after = parent.live()
    parent.power_gate(after)
    require(after['host'] == profile['host'] and hp.execution_state(after['capabilities']) == hp.execution_state(profile['capabilities']), '준비 도중 전원·커널 환경이 변경됐습니다')
    files = dict(read(SOURCE / 'prepared.json')['files'])
    files.update({str(path): sha(path) for path in cache_inputs})
    links = dict(read(SOURCE / 'prepared.json')['symlinks'])
    for folder in [HERE, run, root / 'build', SOURCE]:
        for path in folder.rglob('*'):
            if '__pycache__' in path.parts:
                continue
            if path.is_symlink():
                links[str(path)] = os.readlink(path)
            elif path.is_file():
                files[str(path)] = sha(path)
    (run / 'run-terminal.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\nexport PYTHONDONTWRITEBYTECODE=1\nTASK_RUN="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\nexec python3 -B "$TASK_RUN/launch.py" "$@"\n')
    files[str(run / 'run-terminal.sh')] = sha(run / 'run-terminal.sh')
    save(run / 'prepared.json', dict(files=files, symlinks=links))
    save(run / 'seal.json', dict(status='READY_FOR_AFFINITY_FACTOR_ONLY', prepared_sha256=sha(run / 'prepared.json'), profile_sha256=sha(run / 'host-profile.exploratory.json'), host=profile['host'], execution='NOT_RUN', strict_acceptance='FAILED', safety_verdict='FAIL'))
    verify(read(run / 'prepared.json'))
    print('새 일회성 준비물: ' + str(run))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
