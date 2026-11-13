# SPDX-License-Identifier: GPL-2.0
"""CPU 허용 집합 요인만 C/E에서 비교한다."""
from pathlib import Path
import json
import sys
from records import read

MASKS = {'11': [0,2,4,6,8,9,10,11,12,13,14], '8': [0,2,4,6,8,9,10,11]}
ORDERS = [['C11','E11','E8','C8'], ['C8','E8','E11','C11']]
METRICS = ['prefill_tps','prefill_ms','http_request_ms','internal_inference_ms','decode_tps','decode_ms','cpu_time_s','migrations','migration_per_cpu_s','context_switches','p_mean_busy_pct','e_mean_busy_pct']

def plans():
    return [dict(round=r,position=p,ordinal=(r-1)*4+p,policy=cell[0],cell=cell,server_cpus=MASKS[cell[1:]],visit_id=f'r{r:02d}-{cell}',relative_dir=f'visits/round-{r:02d}/{cell}',external_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED') for r,order in enumerate(ORDERS,1) for p,cell in enumerate(order,1)]

def load_visit(root, visit):
    import validation as v
    path = Path(root) / visit['relative_dir']
    row = dict(**visit, visit_status='NOT_RUN', valid=False, sample={})
    if (path / 'result.json').exists():
        try:
            result, checked = v.complete_visit(path, visit)
            timing = read(path / 'visit-timing.json')
            before, after = read(path / 'counters-before.json'), read(path / 'counters-after.json')
            v.require(timing['start_monotonic_ns'] <= before['monotonic_ns'] < after['monotonic_ns'] <= timing['end_monotonic_ns'], '세션과 측정 시간 순서가 다릅니다')
            row.update(result, valid=True, timing=timing, diagnostics=checked)
        except (ValueError, OSError, KeyError, TypeError, ZeroDivisionError) as error:
            row.update(visit_status='FAILED_EVIDENCE', error=str(error))
    elif path.exists():
        row['visit_status'] = 'INCOMPLETE'
    return row

def summarize(root):
    root = Path(root)
    sys.path.append(str(Path(read(root / 'setup.json')['repo']) / 'tools/sched_ext/new_a_full_guard'))
    import fg_analyze as old
    from fg_config import LABELS
    rows = [load_visit(root, visit) for visit in plans()]
    blocks = [r for r in [1, 2] if all(x['valid'] for x in rows if x['round'] == r)]
    primary = [x for x in rows if x['round'] in blocks]
    valid = [x for x in rows if x['valid']]
    stats = {policy: {metric: old.describe([x['sample'][metric] for x in primary if x['cell'] == policy]) for metric in METRICS} for policy in ['C11','E11','E8','C8']}
    paired = {}
    for first, second in [('C11','C8'),('E11','E8'),('C11','E11'),('C8','E8')]:
        comparison = second + '_over_' + first
        paired[comparison] = {}
        for metric in METRICS:
            pairs = [(next(x for x in primary if x['round']==r and x['cell']==first)['sample'][metric],next(x for x in primary if x['round']==r and x['cell']==second)['sample'][metric]) for r in blocks]
            value = old.pairing(pairs)
            value['paired_difference_second_minus_first'] = value.pop('paired_differences_guard_minus_full')
            value['ratios_second_over_first'] = value.pop('ratios_guard_over_full')
            value['paired_change_pct'] = value.pop('change_pct')
            value['rounds'] = blocks
            value['per_round_change_pct'] = [100*(b/a-1) if a else 'NOT_AVAILABLE' for a,b in pairs]
            paired[comparison][metric] = value
    chronological = [dict(visit_id=x['visit_id'], round=x['round'], position=x['position'], ordinal=x['ordinal'], policy=x['policy'], cell=x['cell'], timing=x['timing'], metrics={m: x['sample'][m] for m in METRICS}) for x in valid]
    return dict(**LABELS, complete=len(blocks) == 2, completed_rounds=blocks, performance_samples=len(primary), valid_measurements=len(valid), planned_servers=8, planned_warmups=16, planned_measurements=8, planned_requests=24, statistics=stats, paired=paired, visits=rows, chronology=chronological,
                automatic_next_stage_admission=False, recommendation='REVIEW_REQUIRED', phase_cpu_metrics='NOT_AVAILABLE', wakeup_latency='NOT_AVAILABLE', queued_selection_skip_count='NOT_AVAILABLE',
                analysis_order=['Prefill 처리량', 'HTTP 전체 요청 시간', 'Decode 처리량', '프로세스 CPU 시간', 'Migration/CPU초', 'P/E-core 활용률'],
                scopes=dict(http='측정 POST부터 응답 본문 완료까지. 서버 시작·모델 로딩·워밍업 제외', process='측정 요청 전후 /proc 경계의 안정된 TID CPU 시간 합계. 단계별 분리 불가', busy='시스템 전체 CPU busy이며 서버 전용 P/E 실행 비중이 아님', diagnostics='FULL 내부 통계는 서버 전체 수명 구간', observer='외부 관측기·Probe 프로세스 자체를 실행하지 않음'),
                interpretation='각 정책·마스크 조건당 두 독립 표본의 탐색적 Affinity 비교다. 워밍업·과거 B 결과는 반복 표본에 포함하지 않는다. 고정 역순은 선형 순서 영향을 줄이지만 비선형 시간 변화와 배경 부하를 제거하지 못한다. 동등성 허용오차가 사전에 정해지지 않아 유의하지 않다는 이유로 사실상 동일을 확정하지 않는다. Prefill·HTTP 개선과 Decode 회귀를 함께 검토한 뒤 A/B/C 방향을 판단한다. E8/C8과 E11/C11을 구분한다. Affinity·L2 용량·지원 스레드 경쟁 효과를 분리한 인과 증명은 아니다. V4 정책은 구현하거나 비교하지 않는다.')

def print_summary(value):
    print('탐색적 재확인 결과: 측정 요청 ' + str(value['performance_samples']) + '개; 완료 라운드 ' + str(value['completed_rounds']))
    for policy in ['C11','E11','E8','C8']:
        s = value['statistics'][policy]
        print(policy + ': Prefill 평균=' + str(s['prefill_tps']['mean']) + ' tok/s; HTTP 평균=' + str(s['http_request_ms']['mean']) + ' ms; Decode 평균=' + str(s['decode_tps']['mean']) + ' tok/s')
    print('조건당 표본 2회이며 통계적 우위·동등성·다음 단계 승인을 확정하지 않습니다. STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    print(json.dumps(summarize(parser.parse_args().directory), indent=2, sort_keys=True, allow_nan=False))
