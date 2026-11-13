# SPDX-License-Identifier: GPL-2.0
"""같은 E FULL의 수동/자동 기능 요청과 회귀 측정 요청을 구분한다."""
from pathlib import Path
import sys
from records import read,require
CPUS=[0,2,4,6]
BUSY_CPUS=[0,2,4,6,8,9,10,11]
METRICS=['prefill_tps','prefill_ms','http_request_ms','internal_inference_ms','decode_tps','decode_ms','cpu_time_s','migrations','migration_per_cpu_s','context_switches','p_mean_busy_pct','e_mean_busy_pct']
OMP_FORMAT='NEWA_TEAM pid=%P team=%N index=%n native=%i'

def omp_options():
    return dict(OMP_DISPLAY_AFFINITY='TRUE',OMP_AFFINITY_FORMAT=OMP_FORMAT)

def plans(mode):
    require(mode in ['FUNCTIONAL','PERFORMANCE'],'허용하지 않은 실행 단계입니다')
    orders=[['MANUAL','AUTO']] if mode=='FUNCTIONAL' else [['MANUAL','AUTO'],['AUTO','MANUAL']]
    return [dict(round=r,position=p,ordinal=(r-1)*len(order)+p,policy='E',candidate=c,configuration_mode=c,cell=c+'4-P',affinity='P',kind=mode,
                 compute_threads=4,batch_threads=4,http_threads=2,server_cpus=CPUS,visit_id=f'r{r:02d}-{c}-P',relative_dir=f'visits/round-{r:02d}/{c}-P',
                 external_observer='DISABLED_NOT_STARTED',route_probe='DISABLED_NOT_STARTED',fexit_observer='DISABLED_NOT_STARTED',runtime_statistics='DISABLED_NOT_ENABLED')
            for r,order in enumerate(orders,1)for p,c in enumerate(order,1)]

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
            result, checked = v.complete_visit(path, visit, functional=visit['kind']=='FUNCTIONAL')
            import integration
            signature=integration.validate_runtime(path,visit,read(Path(root)/'setup.json'))
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
            row.update(result, valid=True, timing=timing, diagnostics=checked, resources=resources,runtime_signature=signature)
        except (ValueError,OSError,KeyError,TypeError,ZeroDivisionError) as error:
            row.update(visit_status='FAILED_EVIDENCE',error=str(error))
    elif path.exists():
        row['visit_status']='INCOMPLETE'
    return row


def summarize(root):
    root=Path(root);setup=read(root/'setup.json');mode=setup['mode']
    sys.path.append(str(Path(setup['repo'])/'tools/sched_ext/new_a_full_guard'))
    import fg_analyze as old
    from fg_config import LABELS
    rows=[load_visit(root,v)for v in plans(mode)]
    blocks=[r for r in sorted({v['round']for v in rows})if all(v['valid']for v in rows if v['round']==r)]
    primary=[v for v in rows if v['round']in blocks]
    stats={c:{m:old.describe([v['sample'][m]for v in primary if v['candidate']==c])for m in METRICS}for c in ['MANUAL','AUTO']} if mode=='PERFORMANCE'else {}
    paired={}
    if mode=='PERFORMANCE':
        for first,second in [('MANUAL','AUTO')]:
            paired[second+'_over_'+first]={}
            for metric in METRICS:
                pairs=[(next(v for v in primary if v['round']==r and v['candidate']==first)['sample'][metric],next(v for v in primary if v['round']==r and v['candidate']==second)['sample'][metric])for r in blocks]
                paired[second+'_over_'+first][metric]=dict(rounds=blocks,differences=[b-a for a,b in pairs],change_pct=[100*(b/a-1)if a else 'NOT_AVAILABLE'for a,b in pairs])
    return dict(**LABELS,mode=mode,complete=len(primary)==len(rows),visits=rows,completed_rounds=blocks,statistics=stats,paired=paired,
                performance_samples=len(primary)if mode=='PERFORMANCE'else 0,functional_samples=len(primary)if mode=='FUNCTIONAL'else 0,
                planned_servers=len(rows),planned_warmups=2*len(rows),planned_requests=3*len(rows),
                automatic_next_stage_admission=False,is_safety_approval=False,
                interpretation='같은 E FULL의 수동/자동 설정 비교다. 조건별 n=2이며 동등성/성능 향상을 확정하지 않는다. 기능 요청은 성능 분석에서 제외한다. CPU 시간은 요청 전후 proc 스냅샷 구간, 시스템 busy는 다른 작업도 포함한다. Prefill 작업자별 CPU 시간 NOT_AVAILABLE, 에너지 NOT_MEASURED. 과거8T는 대응 표본이 아니다.')

def print_summary(value):
    print('단계='+value['mode']+'; 완료='+str(value['complete'])+'; 성능 표본='+str(value['performance_samples']))
    for cell,s in value['statistics'].items():
        print(cell+': Prefill='+str(s['prefill_tps']['mean'])+' tok/s; Decode='+str(s['decode_tps']['mean'])+' tok/s; HTTP='+str(s['http_request_ms']['mean'])+' ms')
    print('STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; 실제 성능 우위는 반복 측정 후 판단합니다.')
