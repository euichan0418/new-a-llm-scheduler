# SPDX-License-Identifier: GPL-2.0
"""외부 관측기 없이 저장된 원시 응답과 필수 안전성 증거를 검사한다."""
import math
import os
from pathlib import Path
import fg_validation as old
from fg_config import LABELS, SERVER_CPUS, METRICS
ACTIVE_MASK = set(SERVER_CPUS)
from records import read, require

def request_metrics(*args, **kwargs):
    value = old.request_metrics(*args, **kwargs)
    value['per_cpu_system_busy_pct'] = {str(c): value['per_cpu_system_busy_pct'][str(c)] for c in [0,2,4,6,8,9,10,11]}
    value['e_mean_busy_pct'] = sum(value['per_cpu_system_busy_pct'][str(c)] for c in [8,9,10,11]) / 4
    value['p_mean_busy_pct'] = sum(value['per_cpu_system_busy_pct'][str(c)] for c in [0,2,4,6]) / 4
    return value

def diagnostics(value, policy, **unused):
    require(policy in ['C', 'E'], '허용하지 않은 비교 정책입니다')
    if policy == 'C':
        require(value.get('instrumentation_mode') == 'LINUX' and value.get('bpf_counters') == 'NOT_AVAILABLE', 'Linux 진단 provenance가 다릅니다')
        return dict(instrumentation_mode='LINUX', bpf_counters='NOT_AVAILABLE')
    checked = old.diagnostics(value, 'FULL')
    require(all(row['cpu'] in ACTIVE_MASK or row['running'] == row['stopping'] == row['runtime_ns'] == 0 for row in value['cpus']), '현재 Affinity 밖에서 실제 실행이 관측됐습니다')
    if ACTIVE_MASK=={0,2,4,6}:
        require(checked['selected'][1]==checked['selected'][2]==0,'P-only에서 E/SMT tier 선택이 관측됐습니다')
    return checked

def ops_flags(value, name):
    found = []
    def visit(item):
        if isinstance(item, dict):
            if item.get('name') == name and 'flags' in item:
                flags = item['flags']
                require(not isinstance(flags, bool), '잘못된 flags 형식입니다')
                found.append(int(flags, 0) if isinstance(flags, str) else flags)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    require(len(found) == 1 and found[0] == (40 if name == 'llama_new_a_v3' else 8), '실제 struct_ops 플래그가 다릅니다')
    return found[0]

INSTRUMENTATION = dict(external_wakeup_observer='DISABLED_NOT_STARTED', route_probe='DISABLED_NOT_STARTED', internal_diagnostics='E_FULL_C_NOT_APPLICABLE', wakeup_latency='NOT_AVAILABLE', selection_skip_count='NOT_AVAILABLE')

def complete_visit(path, expected, *, functional=False):
    path = Path(path)
    require(expected['candidate'] in ['MANUAL','AUTO'] and expected['policy']=='E' and expected['configuration_mode']==expected['candidate'],'후보/실제 실행 프레임워크 정책이 다릅니다')
    mask = set(expected['server_cpus'])
    require(mask=={0,2,4,6} and expected['compute_threads']==expected['batch_threads']==4 and expected['http_threads']==2,'4T P-only 조건이 변경됐습니다')
    policy = 0 if expected['policy'] == 'C' else 7
    result = read(path / 'result.json')
    require(functional == (expected['kind']=='FUNCTIONAL') and result['policy'] in ['C', 'E'] and all(result.get(k) == v for k, v in expected.items()), '측정 종류 또는 세션 식별자가 다릅니다')
    require(result['visit_status'] == 'COMPLETE' and result['valid_for_comparison'] is (not functional) and result['errors'] == [], '불완전하거나 실패한 세션입니다')
    require(all(result.get(k) == v for k, v in LABELS.items()), '기존 안전성 판정이 변경됐습니다')
    require(read(path / 'cleanup.json') == dict(status='PASS', errors=[], sched_ext='disabled') and result['sched_ext_after'] == 'disabled', 'cleanup 또는 sched_ext 종료 확인에 실패했습니다')
    guard = read(path / 'guard-result.json')
    require(guard == result['configuration_guard'] and guard['status'] == 'COMPLETED' and guard['errors'] == [] and guard['samples'] >= 2, '전원·열 감시 결과가 불완전합니다')
    require(result['thermal_throttle_whole_visit']['status'] == 'NO_INCREMENT_OBSERVED', '세션 중 Thermal Throttling이 발생했습니다')
    samples = [__import__('json').loads(line) for line in (path / 'power-thermal-monitor.jsonl').read_text().splitlines()]
    require(len(samples) == guard['samples'], '전원·열 감시 표본 수가 다릅니다')
    for i, sample in enumerate(samples):
        settings = sample['settings']
        require(sample['sample'] == i and settings['no_turbo'] == settings['no_turbo_at_start'] == '1' and settings['power_profile'] == 'performance', '전원·Turbo 설정이 변경됐습니다')
        require(sample['throttle_delta']['status'] == 'NO_INCREMENT_OBSERVED', '감시 중 Thermal Throttling이 발생했습니다')
        require(any(row.get('type') == 'Mains' and row.get('online') == '1' for row in sample['capabilities']['power_supply']['entries'].values()), '감시 중 AC 연결을 확인할 수 없습니다')
    server = read(path / 'processes.json')['server']
    require(server['uid'] == os.getuid() and server['policy'] == policy and set(server['affinity']) == mask, '서버 Identity·정책·Affinity가 다릅니다')
    import workers
    workers.validate_contract(read(path/'thread-contract.json'), expected, server)
    observed = workers.team((path/'server.stderr.log').read_text(), expected['compute_threads'], server['pid'])
    require(observed == read(path/'openmp-team.json') == read(path/'openmp-before-measured.json') == read(path/'openmp-after-measured.json'), 'OpenMP 팀 증거 누락/변경 또는 요청 중 표시 발생')
    for name in ['policies-before.json', 'policies-pre-measured.json', 'policies-after.json']:
        tasks = read(path / name)
        require(tasks and all(t['uid'] == os.getuid() and t['policy'] == policy and set(t['affinity']) == mask for t in tasks), 'TID별 Identity·정책·Affinity가 다릅니다')
    require(read(path / 'instrumentation.json') == INSTRUMENTATION, '외부 관측 비활성화 증거가 다릅니다')
    import stats_off
    stats_off.verify_visit(path,expected['policy'])
    require(not list(path.glob('observer*')) and not list(path.glob('route-probe*')) and not list(path.glob('common-wakeup*')) and not list(path.glob('fexit*')), '비활성화 대상 관측기 또는 Probe 실행 기록이 있습니다')
    require(not list(path.glob('runtime-*.json')) and not list(path.glob('cost-*.json')) and not list(path.glob('stats-enable*')), 'Runtime Statistics 활성화/수집 실행 기록이 있습니다')
    role='functional'if functional else'measured'
    require(read(path/'record-type.json')['purpose']==('FUNCTIONAL_ONLY'if functional else'PERFORMANCE_DIAGNOSTIC_COMPARISON'),'기능/성능 기록 구분이 다릅니다')
    require(read(path/'measurement.json')['scope']==('FUNCTIONAL_ONLY_EXCLUDED_FROM_PERFORMANCE'if functional else'ONE_MEASURED_REQUEST'),'측정의 분석 허용 범위가 다릅니다')
    require(result['requests_completed'] == ['warmup-1', 'warmup-2', role], '워밍업과 측정 요청이 구분되지 않았습니다')
    for role in result['requests_completed']:
        old.response(path, role)
    kernel = read(path / 'kernel-watch-result.json')
    require(kernel['status'] == 'PASS' and kernel['errors'] == [] and kernel['record']['status'] == 'PASS' and kernel['record']['kernel_opened'] is True and kernel['record']['fd_closed'] is True and kernel['record']['bpf_loaded'] is False, '독립 커널 안전성 감시가 실패했습니다')
    attach = read(path / 'kernel-load-attach.json')
    if expected['policy'] == 'C':
        require(attach.get('scheduler') == 'LINUX' and attach.get('attach') == 'NOT_APPLICABLE', 'Linux 대조군의 스케줄러가 다릅니다')
    else:
        require(attach.get('verifier') == 'LOAD_SUCCEEDED' and attach.get('attach') == 'OBSERVED' and attach.get('ops') == 'llama_new_a' and attach.get('flags') == 8 and attach.get('live_monitor') == 'READY', 'E verifier·attach 증거가 불완전합니다')
        ops_flags(read(path / 'struct-ops-dump.json'), 'llama_new_a')
    value = read(path / 'scheduler-stats.json')
    if expected['policy'] == 'E':
        require(all(row['cpu'] in mask or row['running'] == row['stopping'] == row['runtime_ns'] == 0 for row in value['cpus']), '현재 Affinity 밖의 실제 실행 증거입니다')
        checked = old.diagnostics(value, 'FULL')
        if mask=={0,2,4,6}:
            require(checked['selected'][1]==checked['selected'][2]==0,'P-only에서 E/SMT tier 선택이 관측됐습니다')
    else:
        checked = diagnostics(value, 'C')
    data, client = old.response(path, role)
    fresh = request_metrics(read(path / 'counters-before.json'), read(path / 'counters-after.json'), data, client, result['sample']['system_busy_pct'], result['sample']['measured_thermal_throttle'])
    for metric in [*METRICS, 'http_minus_internal_ms']:
        require(old.finite(result['sample'].get(metric)) and math.isclose(fresh[metric], result['sample'][metric], rel_tol=1e-10, abs_tol=1e-9), '원시 측정값과 저장값이 다릅니다: ' + metric)
    require(fresh['measured_thermal_throttle']['status'] == 'NO_INCREMENT_OBSERVED', '측정 요청의 Thermal 검사에 실패했습니다')
    return result, checked
