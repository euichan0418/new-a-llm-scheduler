#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read saved visits only. Never import or execute a campaign."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

NA = 'NOT_AVAILABLE'
ORDERS = [('C','E','V2'),('C','V2','E'),('E','C','V2'),('E','V2','C'),('V2','C','E'),('V2','E','C')]
SERVER = [0,2,4,6,8,9,10,11,12,13,14]
LABELS = dict(record_type='EXPLORATORY_ONLY', strict_acceptance='FAILED', safety_verdict='FAIL', suite_acceptance='NOT_EVALUATED')


def read(path):
    return json.loads(Path(path).read_text())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def describe(values):
    require(bool(values) and all(isinstance(x,(float,int)) and math.isfinite(x) for x in values), 'Missing/nonfinite metric')
    return dict(n=len(values), mean=statistics.mean(values), sample_stddev=statistics.stdev(values) if len(values)>1 else NA,
                median=statistics.median(values), minimum=min(values), maximum=max(values))


def thread_deltas(before, after):
    a,b=before['threads'],after['threads']
    require(bool(a) and a.keys()==b.keys(), 'Thread set changed')
    rows=[]
    for tid in sorted(a,key=int):
        require(a[tid]['starttime']==b[tid]['starttime'], 'TID reused')
        row=dict(tid=int(tid),starttime=a[tid]['starttime'])
        for key in ['runtime_ns','context_switches','nr_switches','se.nr_migrations']:
            x,y=a[tid].get(key),b[tid].get(key)
            require(isinstance(x,int) and isinstance(y,int) and y>=x, 'Incomplete/decreased task counter: '+key)
            row[key]=y-x
        rows.append(row)
    return rows


def visit(path, policy, round_no, position):
    r=read(path/'result.json'); sample=r['sample']; cleanup=read(path/'cleanup.json')
    require((r['policy'],r['round'],r['position'])==(policy,round_no,position), 'Visit identity/order mismatch')
    require(sample['core_metrics_complete'] is True, 'Incomplete core metrics')
    require(r['visit_status']=='COMPLETE' and r['valid_for_comparison'] is True and not r['errors'], 'Invalid visit: '+str(path))
    require(r['requests_completed']==['warmup-1','warmup-2','measured'], 'Missing response role')
    require(cleanup==dict(status='PASS',errors=[],sched_ext='disabled'), 'Cleanup not PASS')
    require(r['thermal_throttle_whole_visit']['status']=='NO_INCREMENT_OBSERVED', 'Visit throttled')
    require(sample['measured_thermal_throttle']['status']=='NO_INCREMENT_OBSERVED', 'Measured throttling')
    require(r['configuration_guard']['status']=='COMPLETED' and not r['configuration_guard']['errors'], 'Incomplete guard')
    for role in ['warmup-1','warmup-2','measured']:
        response=read(path/(role+'-response.json'))
        require(response['timings']['prompt_n']==920 and response['timings']['predicted_n']==128, 'Token count changed')
    timings=read(path/'measured-response.json')['timings']
    before,after=read(path/'counters-before.json'),read(path/'counters-after.json')
    threads=thread_deltas(before,after)
    cpu_s=sum(t['runtime_ns'] for t in threads)/1e9
    elapsed=(after['monotonic_ns']-before['monotonic_ns'])/1e9
    migrations=sum(t['se.nr_migrations'] for t in threads)
    require(cpu_s>0 and elapsed>0, 'Invalid interval')
    require(math.isclose(cpu_s,sample['cpu_time_s'],abs_tol=1e-9) and migrations==sample['migrations'], 'Saved/raw metric mismatch')
    require(math.isclose(migrations/cpu_s,sample['migration_per_cpu_s'],rel_tol=1e-12), 'Migration denominator changed')
    per_cpu={}
    for cpu in SERVER:
        a,b=before['cpus']['cpu'+str(cpu)],after['cpus']['cpu'+str(cpu)]
        total,idle=b['total']-a['total'],b['idle']-a['idle']
        require(total>0 and 0<=idle<=total,'Bad CPU counters')
        per_cpu[str(cpu)]=100*(1-idle/total)
    request=dict(prefill_tps=timings['prompt_per_second'],decode_tps=timings['predicted_per_second'],
        prefill_ms=timings['prompt_ms'],decode_ms=timings['predicted_ms'],proc_window_s=elapsed,process_cpu_s=cpu_s,
        context_switches=sum(t['context_switches'] for t in threads),nr_switches=sum(t['nr_switches'] for t in threads),
        migrations=migrations,migration_per_cpu_s=migrations/cpu_s,migration_per_wall_s=migrations/elapsed,
        context_switches_per_cpu_s=sum(t['context_switches'] for t in threads)/cpu_s,
        average_running_cpus=cpu_s/elapsed,system_busy_pct=sample['system_busy_pct'],
        p_system_busy_pct=statistics.mean(per_cpu[str(c)] for c in [0,2,4,6]),
        e_system_busy_pct=statistics.mean(per_cpu[str(c)] for c in range(8,15)),per_cpu_system_busy_pct=per_cpu,threads=threads)
    lifetime=NA
    if policy!='C':
        stats=read(path/'scheduler-stats.json'); rows=stats['cpus']
        require(stats['events_export_error']==0 and stats['event_schema']==2,'Bad diagnostic export')
        sums={k:sum(x[k] for x in rows) for k in ['running','stopping','runtime_ns','migrations','mask_violations','tracking_failures','events_attempted','events_dropped']}
        require(sums['running']==sums['stopping'] and sums['mask_violations']==sums['tracking_failures']==sums['events_dropped']==0,'Diagnostics failed')
        selected=[sum(x['selected'][i] for x in rows) for i in range(4)]
        n=sum(selected); guaranteed_local=sum(selected[:3])
        lifetime=dict(**sums,select_cpu_calls=n,tier_selections=selected,
            direct_local_share_of_select_calls_lower_pct=100*guaranteed_local/n if n else NA,
            direct_local_share_of_select_calls_upper_pct=100 if n else NA,
            mean_observed_running_segment_us=sums['runtime_ns']/sums['running']/1000 if sums['running'] else NA,
            task_observed_runtime_s=sums['runtime_ns']/1e9,bpf_program_runtime_ns=NA,
            enqueue_calls=NA,dispatch_calls=NA,per_cpu_callback_stats=rows,
            shared_dsq_fraction=NA,shared_dsq_wait_ns=NA,runnable_to_running_ns=NA,
            p_runtime_share_pct=100*sum(x['runtime_ns'] for x in rows if x['cpu'] in [0,2,4,6])/sums['runtime_ns'],
            p_segment_us=sum(x['runtime_ns'] for x in rows if x['cpu'] in [0,2,4,6])/sum(x['running'] for x in rows if x['cpu'] in [0,2,4,6])/1000,
            e_segment_us=sum(x['runtime_ns'] for x in rows if x['cpu'] in range(8,15))/sum(x['running'] for x in rows if x['cpu'] in range(8,15))/1000)
    return dict(round=round_no,position=position,policy=policy,relative_path=str(path),request=request,lifetime=lifetime)


def extract(root):
    root=Path(root).resolve(); saved=read(root/'summary.json')
    require(saved['complete'] is True and saved['completed_rounds']==list(range(1,7)), 'Incomplete campaign')
    require(saved['terminal']['status']=='COMPLETE' and saved['terminal']['sched_ext_after']=='disabled','Bad final status')
    setup=read(root/'setup.json'); argv=setup['server_argv']
    for flag,value in [('--threads','8'),('--threads-batch','8'),('--threads-http','2'),('--n-gpu-layers','0')]:
        require(argv[argv.index(flag)+1]==value, 'Workload changed: '+flag)
    rows=[visit(root/'visits'/f'round-{i:02d}'/p,p,i,j) for i,order in enumerate(ORDERS,1) for j,p in enumerate(order,1)]
    numeric=[k for k,v in rows[0]['request'].items() if isinstance(v,(float,int))]
    summary={}
    for p in ['C','E','V2']:
        subset=[v for v in rows if v['policy']==p]
        request={k:describe([v['request'][k] for v in subset]) for k in numeric}
        per_cpu={str(c):describe([v['request']['per_cpu_system_busy_pct'][str(c)] for v in subset]) for c in SERVER}
        lifetime=NA
        if p!='C':
            numeric_life=[k for k,v in subset[0]['lifetime'].items() if isinstance(v,(int,float))]
            lifetime={k:describe([v['lifetime'][k] for v in subset]) for k in numeric_life}
            lifetime['tier_selections_total']=[sum(v['lifetime']['tier_selections'][i] for v in subset) for i in range(4)]
        summary[p]=dict(request=request,per_cpu_system_busy_pct=per_cpu,lifetime=lifetime)
    paired={}
    for numerator,denominator in [('E','C'),('V2','C'),('V2','E')]:
        comparisons={}
        for metric in ['prefill_tps','decode_tps','migration_per_cpu_s','context_switches_per_cpu_s']:
            ratios=[]
            for i in range(1,7):
                a=next(x for x in rows if x['round']==i and x['policy']==numerator)['request'][metric]
                b=next(x for x in rows if x['round']==i and x['policy']==denominator)['request'][metric]
                require(a>0 and b>0,'Nonpositive paired value')
                ratios.append(a/b)
            logs=[math.log(v) for v in ratios]; mean=statistics.mean(logs); half=2.570581835636305*statistics.stdev(logs)/math.sqrt(6)
            comparisons[metric]=dict(ratios=ratios,geometric_ratio=math.exp(mean),geometric_change_pct=100*math.expm1(mean),
                exploratory_95pct_log_t_interval_ratio=[math.exp(mean-half),math.exp(mean+half)])
        paired[numerator+'_vs_'+denominator]=comparisons
    return dict(**LABELS,source=str(root),scopes=dict(request='Proc snapshots around ONE measured request, both stages plus boundary overhead; CPU seconds are sum over stable TIDs',
        timings='Frozen response prompt_ms/predicted_ms; no stage CPU time or stage busy',system_busy='Whole system, includes other applications; not process residence',
        lifetime='E/V2 initialization, two warmups, measured request, gaps and shutdown; runtime_ns is task observed runtime, NOT BPF program cost',
        selected='Per callback execution CPU, NOT destination CPU; tier3 includes default-selector direct-local successes'),
        valid_visits=18,validated_responses=54,summary=summary,paired=paired,visits=rows,
        unavailable=['BPF program CPU time','enqueue/dispatch exact call count','exact local/shared ratio among all enqueues','DSQ queue depth/wait','wakeup/requeue-to-switch latency',
        'phase-specific context switches/CPU runtime/busy','request-window per-CPU process runtime','P/E migration endpoints','useful compute versus OpenMP spin/wait'],
        inference='Descriptive six-round results; no causal attribution or performance guarantee')


def write_outputs(result, output):
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    with (output/'extracted.json').open('x') as f:
        json.dump(result,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
    fields=['round','position','policy']+[k for k,v in result['visits'][0]['request'].items() if isinstance(v,(float,int))]
    with (output/'request-metrics.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for v in result['visits']:writer.writerow({k:v[k] if k in v else v['request'][k] for k in fields})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    require(args.output.resolve()!=args.source.resolve() and args.source.resolve() not in args.output.resolve().parents,'Output must be outside the original results')
    write_outputs(extract(args.source),args.output)
