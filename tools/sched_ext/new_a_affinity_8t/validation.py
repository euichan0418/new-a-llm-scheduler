# SPDX-License-Identifier: GPL-2.0
"""외부 관측기 없이 저장된 원시 응답과 필수 안전성 증거를 검사한다."""
import math
import os
from pathlib import Path
import fg_validation as old
from fg_config import LABELS, SERVER_CPUS, METRICS
ACTIVE_MASK = set(SERVER_CPUS)
from records import read, require

request_metrics = old.request_metrics

def diagnostics(value, policy, **unused):
    require(policy in ['C', 'E'], '허용하지 않은 비교 정책입니다')
    if policy == 'C':
        require(value.get('instrumentation_mode') == 'LINUX' and value.get('bpf_counters') == 'NOT_AVAILABLE', 'Linux 진단 provenance가 다릅니다')
        return dict(instrumentation_mode='LINUX', bpf_counters='NOT_AVAILABLE')
    checked = old.diagnostics(value, 'FULL')
    require(all(row['cpu'] in ACTIVE_MASK or row['running'] == row['stopping'] == row['runtime_ns'] == 0 for row in value['cpus']), '현재 Affinity 밖에서 실제 실행이 관측됐습니다')
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
    mask = set(expected['server_cpus'])
    policy = 0 if expected['policy'] == 'C' else 7
    result = read(path / 'result.json')
    require(functional is False and result['policy'] in ['C', 'E'] and all(result.get(k) == v for k, v in expected.items()), '측정 종류 또는 세션 식별자가 다릅니다')
    require(result['visit_status'] == 'COMPLETE' and result['valid_for_comparison'] is True and result['errors'] == [], '불완전하거나 실패한 세션입니다')
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
    for name in ['policies-before.json', 'policies-pre-measured.json', 'policies-after.json']:
        tasks = read(path / name)
        require(tasks and all(t['uid'] == os.getuid() and t['policy'] == policy and set(t['affinity']) == mask for t in tasks), 'TID별 Identity·정책·Affinity가 다릅니다')
    require(read(path / 'instrumentation.json') == INSTRUMENTATION, '외부 관측 비활성화 증거가 다릅니다')
    require(not list(path.glob('observer*')) and not list(path.glob('route-probe*')) and not list(path.glob('common-wakeup*')), '비활성화 대상 관측기 또는 Probe 실행 기록이 있습니다')
    require(result['requests_completed'] == ['warmup-1', 'warmup-2', 'measured'], '워밍업과 측정 요청이 구분되지 않았습니다')
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
    else:
        checked = diagnostics(value, 'C')
    data, client = old.response(path, 'measured')
    fresh = request_metrics(read(path / 'counters-before.json'), read(path / 'counters-after.json'), data, client, result['sample']['system_busy_pct'], result['sample']['measured_thermal_throttle'])
    for metric in [*METRICS, 'http_minus_internal_ms']:
        require(old.finite(result['sample'].get(metric)) and math.isclose(fresh[metric], result['sample'][metric], rel_tol=1e-10, abs_tol=1e-9), '원시 측정값과 저장값이 다릅니다: ' + metric)
    require(fresh['measured_thermal_throttle']['status'] == 'NO_INCREMENT_OBSERVED', '측정 요청의 Thermal 검사에 실패했습니다')
    return result, checked
