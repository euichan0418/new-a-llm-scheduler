# SPDX-License-Identifier: GPL-2.0
"""동일 8CPU에서 스레드 수와 정책 효과를 분리하는 4T 기준선 분석이다."""
from pathlib import Path
import json
import sys
from records import read, require

CPUS = [0,2,4,6,8,9,10,11]
ORDERS = [['C8','E8','E4','C4'], ['C4','E4','E8','C8']]
METRICS = ['prefill_tps','prefill_ms','http_request_ms','internal_inference_ms','decode_tps','decode_ms',
           'cpu_time_s','migrations','migration_per_cpu_s','context_switches','p_mean_busy_pct','e_mean_busy_pct']
OMP_FORMAT = 'NEWA_TEAM pid=%P team=%N index=%n native=%i'


def omp_options():
    return dict(OMP_DISPLAY_AFFINITY='TRUE', OMP_AFFINITY_FORMAT=OMP_FORMAT)


def plans():
    return [dict(round=r, position=p, ordinal=(r-1)*4+p, policy=cell[0], cell=cell,
                 compute_threads=int(cell[1:]), batch_threads=int(cell[1:]), http_threads=2,
                 server_cpus=CPUS, visit_id=f'r{r:02d}-{cell}', relative_dir=f'visits/round-{r:02d}/{cell}',
                 external_observer='DISABLED_NOT_STARTED', route_probe='DISABLED_NOT_STARTED',
                 fexit_observer='DISABLED_NOT_STARTED')
            for r,order in enumerate(ORDERS,1) for p,cell in enumerate(order,1)]


def server_config(setup, threads):
    require(type(threads) is int and threads in [4,8], '4T/8T 이외의 구성은 허용하지 않습니다')
    argv = list(setup['server_argv'])
    for option, value in [('--threads',threads),('--threads-batch',threads),('--threads-http',2)]:
        require(argv.count(option)==1, '서버 스레드 인자가 누락되거나 중복됐습니다: '+option)
        argv[argv.index(option)+1] = str(value)
    env = dict(setup['server_env'], OMP_NUM_THREADS=str(threads), OMP_DYNAMIC='FALSE', **omp_options())
    return argv, env


def load_visit(root, visit):
    import validation as v
    path = Path(root)/visit['relative_dir']
    row = dict(**visit, visit_status='NOT_RUN', valid=False, sample={})
    if (path/'result.json').exists():
        try:
            result, checked = v.complete_visit(path, visit)
            timing = read(path/'visit-timing.json')
            before, after = read(path/'counters-before.json'), read(path/'counters-after.json')
            v.require(timing['start_monotonic_ns']<=before['monotonic_ns']<after['monotonic_ns']<=timing['end_monotonic_ns'], '세션 및 요청 경계 순서가 다릅니다')
            cpu = result['sample']['cpu_time_s']
            deltas = {tid: after['threads'][tid]['runtime_ns']-value['runtime_ns'] for tid,value in before['threads'].items()}
            resources = dict(average_cpu_equivalents=cpu/(result['sample']['proc_window_ms']/1000),
                             cpu_seconds_per_output_token=cpu/128, cpu_seconds_per_input_plus_output_token=cpu/1048,
                             all_server_tids=len(deltas), all_server_tids_with_positive_cpu=sum(x>0 for x in deltas.values()),
                             per_tid_request_cpu_ns=deltas, active_compute_workers='NOT_AVAILABLE', tid_roles='ROLE_UNCONFIRMED',
                             openmp_team=read(path/'openmp-team.json'), energy_joules='NOT_MEASURED',
                             per_phase_cpu='NOT_AVAILABLE', per_cpu_process_runtime='NOT_AVAILABLE')
            row.update(result, valid=True, timing=timing, diagnostics=checked, resources=resources)
        except (ValueError,OSError,KeyError,TypeError,ZeroDivisionError) as error:
            row.update(visit_status='FAILED_EVIDENCE',error=str(error))
    elif path.exists():
        row['visit_status']='INCOMPLETE'
    return row


def summarize(root):
    root=Path(root)
    sys.path.append(str(Path(read(root/'setup.json')['repo'])/'tools/sched_ext/new_a_full_guard'))
    import fg_analyze as old
    from fg_config import LABELS
    rows=[load_visit(root,v) for v in plans()]
    blocks=[r for r in [1,2] if all(x['valid'] for x in rows if x['round']==r)]
    primary=[x for x in rows if x['round'] in blocks]
    valid=[x for x in rows if x['valid']]
    stats={cell:{m:old.describe([x['sample'][m] for x in primary if x['cell']==cell]) for m in METRICS} for cell in ['C8','E8','C4','E4']}
    paired={}
    for first,second in [('E8','E4'),('C8','C4'),('C4','E4'),('C8','E4'),('C8','E8')]:
        output={}
        for metric in METRICS:
            pairs=[(next(x for x in primary if x['round']==r and x['cell']==first)['sample'][metric],
                    next(x for x in primary if x['round']==r and x['cell']==second)['sample'][metric]) for r in blocks]
            value=old.pairing(pairs)
            value['paired_difference_second_minus_first']=value.pop('paired_differences_guard_minus_full')
            value['ratios_second_over_first']=value.pop('ratios_guard_over_full')
            value['paired_change_pct']=value.pop('change_pct')
            value.update(rounds=blocks,per_round_change_pct=[100*(b/a-1) if a else 'NOT_AVAILABLE' for a,b in pairs])
            output[metric]=value
        paired[second+'_over_'+first]=output
    retention={}
    for policy in ['C','E']:
        a,b=stats[policy+'8'],stats[policy+'4']
        def ratio(metric):
            x,y=a[metric]['mean'],b[metric]['mean']
            return 100*y/x if isinstance(x,(int,float)) and x>0 and isinstance(y,(int,float)) else 'NOT_AVAILABLE'
        pp,dec,http,cpu=map(ratio,['prefill_tps','decode_tps','http_request_ms','cpu_time_s'])
        band='NOT_AVAILABLE'
        if isinstance(pp,(int,float)):
            band='매우 유망' if pp>=95 else '목표 달성 후보' if pp>=90 else '추가 최적화 필요' if pp>=85 else '현재 목표 미달'
        retention[policy]=dict(prefill_retention_pct=pp,decode_retention_pct=dec,
                              http_increase_pct=http-100 if isinstance(http,(int,float)) else http,
                              cpu_time_reduction_pct=100-cpu if isinstance(cpu,(int,float)) else cpu,
                              prefill_planning_band=band,band_is_validated_threshold=False,is_success=False,
                              warning='잠정 Prefill 구간일 뿐 Decode/HTTP/CPU 시간과 n=2 불확실성 검토 전 성공 판정 불가')
    return dict(**LABELS,complete=len(blocks)==2,completed_rounds=blocks,performance_samples=len(primary),valid_measurements=len(valid),
                planned_servers=8,planned_warmups=16,planned_measurements=8,planned_requests=24,statistics=stats,paired=paired,
                retention=retention,visits=rows,chronology=[dict(visit_id=x['visit_id'],ordinal=x['ordinal'],timing=x['timing'],sample=x['sample'])for x in valid],
                automatic_next_stage_admission=False,recommendation='REVIEW_FOUR_THREAD_RETENTION_AND_ACTUAL_CPU_COST',
                external_wakeup_observer='DISABLED_NOT_STARTED',fexit_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED',
                analysis_order=['E4/E8 Prefill 유지율','Decode 유지율','HTTP 시간 증가율','CPU 시간 감소','동일 4T E4/C4 정책 효과'],
                scopes=dict(cpu='안정된 서버 전체 TID의 요청 전후 schedstat 합계, Prefill/Decode 분리 불가',
                            busy='허용 8CPU의 시스템 전체 busy, 해당 프로세스 전용 P/E 실행 비율이 아님',
                            internal_diagnostics='E FULL은 시작/워밍업/측정/종료를 포함한 서버 수명',
                            openmp='libgomp 팀 크기와 native handle, OSTID/phase/유효 계산 참여 여부는 확인하지 않음',
                            energy='NOT_MEASURED'),
                interpretation='동일 캠페인 4조건 n=2의 탐색이다. 과거 8T/4T를 합산하지 않는다. 설정 스레드 반감과 CPU/에너지 반감은 다르다. OpenMP 표시 로그가 측정 요청 중 증가하면 해당 방문을 거부한다. 표시 기능의 고정 검사 비용은 여전히 포함되므로 비표시 과거 캠페인과 동일 계측으로 취급하지 않는다.')


def print_summary(value):
    print('4T 기준선: 측정 '+str(value['performance_samples'])+'개; 완료 블록 '+str(value['completed_rounds']))
    for cell in ['C8','E8','C4','E4']:
        s=value['statistics'][cell]
        print(cell+': Prefill='+str(s['prefill_tps']['mean'])+' tok/s; Decode='+str(s['decode_tps']['mean'])+' tok/s; HTTP='+str(s['http_request_ms']['mean'])+' ms')
    print('정책별 유지율: '+json.dumps(value['retention'],ensure_ascii=False))
    print('조건당 n=2, 안전성 승인 아님. STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parent)
    print(json.dumps(summarize(parser.parse_args().directory),ensure_ascii=False,indent=2,allow_nan=False))
