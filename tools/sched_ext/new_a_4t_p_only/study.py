# SPDX-License-Identifier: GPL-2.0
"""4T 고정 MIX/P-only 비교. 과거8T는 대응 표본이 아닌 참고값이다."""
from pathlib import Path
import json
import sys
from records import read,require

CPUS=[0,2,4,6,8,9,10,11]
MASKS={'MIX':CPUS,'P':[0,2,4,6]}
ORDERS=[['C4-MIX','E4-MIX','E4-P','C4-P'],['C4-P','E4-P','E4-MIX','C4-MIX']]
METRICS=['prefill_tps','prefill_ms','http_request_ms','internal_inference_ms','decode_tps','decode_ms','cpu_time_s','migrations','migration_per_cpu_s','context_switches','p_mean_busy_pct','e_mean_busy_pct']
OMP_FORMAT='NEWA_TEAM pid=%P team=%N index=%n native=%i'


def omp_options():
    return dict(OMP_DISPLAY_AFFINITY='TRUE',OMP_AFFINITY_FORMAT=OMP_FORMAT)


def plans():
    return [dict(round=r,position=p,ordinal=(r-1)*4+p,policy=cell[0],cell=cell,affinity=cell.split('-')[1],compute_threads=4,batch_threads=4,http_threads=2,
                 server_cpus=MASKS[cell.split('-')[1]],visit_id=f'r{r:02d}-{cell}',relative_dir=f'visits/round-{r:02d}/{cell}',
                 external_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED',fexit_observer='DISABLED_NOT_STARTED',runtime_statistics='DISABLED_NOT_ENABLED')
            for r,order in enumerate(ORDERS,1)for p,cell in enumerate(order,1)]


def server_config(setup,threads):
    require(type(threads)is int and threads==4,'이번 비교는 4/4/2만 허용합니다')
    argv=list(setup['server_argv'])
    for option,n in [('--threads',4),('--threads-batch',4),('--threads-http',2)]:
        require(argv.count(option)==1,'서버 스레드 인자가 누락/중복됐습니다: '+option)
        argv[argv.index(option)+1]=str(n)
    return argv,dict(setup['server_env'],OMP_NUM_THREADS='4',OMP_DYNAMIC='FALSE',**omp_options())


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
    rows=[load_visit(root,v)for v in plans()]
    blocks=[r for r in [1,2]if all(v['valid']for v in rows if v['round']==r)]
    primary=[v for v in rows if v['round']in blocks]
    valid=[v for v in rows if v['valid']]
    cells=['C4-MIX','E4-MIX','C4-P','E4-P']
    statistics={cell:{m:old.describe([v['sample'][m]for v in primary if v['cell']==cell])for m in METRICS}for cell in cells}
    paired={}
    for first,second in [('E4-MIX','E4-P'),('C4-MIX','C4-P'),('C4-P','E4-P'),('C4-MIX','E4-MIX')]:
        result={}
        for metric in METRICS:
            pairs=[(next(v for v in primary if v['round']==r and v['cell']==first)['sample'][metric],
                    next(v for v in primary if v['round']==r and v['cell']==second)['sample'][metric])for r in blocks]
            value=old.pairing(pairs)
            value['paired_difference_second_minus_first']=value.pop('paired_differences_guard_minus_full')
            value['ratios_second_over_first']=value.pop('ratios_guard_over_full')
            value['paired_change_pct']=value.pop('change_pct')
            value.update(rounds=blocks,per_round_change_pct=[100*(b/a-1)if a else 'NOT_AVAILABLE'for a,b in pairs])
            result[metric]=value
        paired[second+'_over_'+first]=result
    interaction={}
    for metric in METRICS:
        values=[];ratios=[]
        for r in blocks:
            x={v['cell']:v['sample'][metric]for v in primary if v['round']==r}
            values.append((x['E4-P']-x['E4-MIX'])-(x['C4-P']-x['C4-MIX']))
            ratios.append(100*((x['E4-P']/x['E4-MIX'])/(x['C4-P']/x['C4-MIX'])-1)if x['E4-MIX']>0 and x['C4-P']>0 and x['C4-MIX']>0 else None)
        interaction[metric]=dict(additive_E_affinity_effect_minus_C=old.describe(values),
                                 multiplicative_interaction_pct=old.describe([x for x in ratios if x is not None]),
                                 rounds=blocks,interpretation='같은 블록 안의 기술적 상호작용. n=2로 인과관계/유의성/동등성을 확정하지 않음')
    reference=read(root/'source-review.json')if(root/'source-review.json').exists()else None
    retention={}
    if reference:
        for cell in cells:
            policy=cell[0];past=reference['statistics'][policy+'8'];current=statistics[cell]
            def ratio(m):
                a,b=past[m]['mean'],current[m]['mean']
                return 100*b/a if isinstance(a,(int,float))and a>0 and isinstance(b,(int,float))else 'NOT_AVAILABLE'
            pp,dec,http,cpu=map(ratio,['prefill_tps','decode_tps','http_request_ms','cpu_time_s'])
            retention[cell]=dict(prefill_reference_retention_pct=pp,decode_reference_retention_pct=dec,
                                 http_reference_increase_pct=http-100 if isinstance(http,(int,float))else http,
                                 cpu_reference_reduction_pct=100-cpu if isinstance(cpu,(int,float))else cpu,
                                 scope='REFERENCE_ONLY_DIFFERENT_CAMPAIGN_NOT_PAIRED',source=reference['source'],
                                 is_success=False,warning='이번 캠페인에는 8T가 없음. 과거8T와 대응 표본으로 합산/통계검정하지 않음')
    return dict(**LABELS,complete=len(blocks)==2,completed_rounds=blocks,performance_samples=len(primary),valid_measurements=len(valid),
                planned_servers=8,planned_warmups=16,planned_measurements=8,planned_requests=24,statistics=statistics,paired=paired,
                affinity_policy_interaction=interaction,historical_8t_reference=retention,visits=rows,
                chronology=[dict(visit_id=v['visit_id'],ordinal=v['ordinal'],timing=v['timing'],sample=v['sample'])for v in valid],
                automatic_next_stage_admission=False,recommendation='REVIEW_PREFILL_HTTP_DECODE_AND_CPU_COST',
                external_wakeup_observer='DISABLED_NOT_STARTED',fexit_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED',runtime_statistics='DISABLED_NOT_ENABLED',
                scopes=dict(cpu='동일 요청 전후 안정된 TID schedstat 합계. Prefill/Decode 분리 불가',
                            busy='공통 P4+E4 집합의 시스템 busy. P-only에서도 다른 프로세스의 E busy는 존재할 수 있음',
                            internal_diagnostics='E FULL 서버 수명 전체. P-only의 허용 집합 밖 실행은 거부',
                            workers='실제 libgomp 팀4. OSTID별 역할과 유효 활성 계산 작업자 수는 NOT_AVAILABLE',energy='NOT_MEASURED'),
                interpretation='4T와 OpenMP 관측 구성을 고정하고 서버 전체 허용 mask만 변경한다. HTTP/보조 스레드 경쟁과 E fallback 제거를 함께 바꾸므로 순수 계산 작업자 배치 효과로 해석하지 않는다. 각 조건 n=2. 과거8T는 참고다. 모든 성능/자원 지표와 불확실성을 검토하기 전 성공 승인 불가.')


def print_summary(value):
    print('4T P-only 비교: 유효 측정 '+str(value['performance_samples'])+'개; 완료 블록 '+str(value['completed_rounds']))
    for cell in ['C4-MIX','E4-MIX','C4-P','E4-P']:
        s=value['statistics'][cell]
        print(cell+': Prefill='+str(s['prefill_tps']['mean'])+' tok/s; Decode='+str(s['decode_tps']['mean'])+' tok/s; HTTP='+str(s['http_request_ms']['mean'])+' ms')
    print('같은 캠페인 P/MIX 효과를 우선 검토합니다. 과거8T는 대응 표본이 아닙니다. STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parent)
    print(json.dumps(summarize(parser.parse_args().directory),ensure_ascii=False,indent=2,allow_nan=False))

