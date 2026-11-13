#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read-only Prefill-first paired FULL/Guard analysis. No campaign import."""
import argparse
import json
import math
from pathlib import Path
import statistics
from fg_config import LABELS,ORDERS,PLAN,METRICS,NA
import fg_validation as v

T95={1:12.7062047364,2:4.30265272975,3:3.18244630528,4:2.7764451052,5:2.57058183564}
LIMITATION='Six paired rounds, fixed alternating orders and uncontrolled background; exploratory unadjusted intervals assume independent approximately normal paired log ratios. Instrumentation effects, not a new CPU policy. Historical Linux is not a concurrent control.'


def describe(values):
    values=[x for x in values if v.finite(x)]
    return dict(n=len(values),mean=statistics.mean(values) if values else NA,sample_stddev=statistics.stdev(values) if len(values)>1 else NA,
                median=statistics.median(values) if values else NA,minimum=min(values) if values else NA,maximum=max(values) if values else NA)


def pairing(pairs):
    differences=[b-a for a,b in pairs]
    ratios=[b/a for a,b in pairs if v.finite(a) and v.finite(b) and a>0 and b>=0]
    result=dict(paired_differences_guard_minus_full=describe(differences),differences=differences,
                ratios_guard_over_full=ratios,ratio_descriptives=describe(ratios),geometric_ratio=NA,change_pct=NA,exploratory_95pct_ratio_interval=NA)
    if ratios and all(x>0 for x in ratios):
        logs=[math.log(x) for x in ratios];mean=statistics.mean(logs)
        result.update(geometric_ratio=math.exp(mean),change_pct=100*math.expm1(mean))
        if len(logs)>1:
            half=T95[len(logs)-1]*statistics.stdev(logs)/math.sqrt(len(logs))
            result['exploratory_95pct_ratio_interval']=[math.exp(mean-half),math.exp(mean+half)]
    return result


def load_visits(root):
    rows=[]
    for planned in PLAN:
        path=Path(root)/planned['relative_dir'];row=dict(**planned,visit_status='NOT_RUN',valid_for_comparison=False,sample={})
        if (path/'result.json').exists():
            try:
                raw=v.read(path/'result.json');row.update(raw);row.update(planned)
                if raw.get('valid_for_comparison'):
                    checked,_=v.complete_visit(path,planned)
                    data,client=v.response(path,'measured')
                    reconstructed=v.request_metrics(v.read(path/'counters-before.json'),v.read(path/'counters-after.json'),data,client,
                        checked['sample']['system_busy_pct'],checked['sample']['measured_thermal_throttle'])
                    v.require(all(math.isclose(reconstructed[k],checked['sample'][k],rel_tol=1e-10,abs_tol=1e-9) for k in METRICS),'Raw/result metric mismatch')
                    row['sample']=reconstructed
                else:row['valid_for_comparison']=False
            except (OSError,ValueError,KeyError,TypeError,ZeroDivisionError) as error:
                row.update(valid_for_comparison=False,visit_status='FAILED_EVIDENCE',analysis_exclusion=str(error))
        elif path.exists():row['visit_status']='INCOMPLETE'
        rows.append(row)
    return rows


def policy_stats(rows):
    return {policy:{key:describe([r['sample'][key] for r in rows if r['policy']==policy]) for key in METRICS} for policy in ['FULL','GUARD']}


def summarize(root):
    root=Path(root);rows=load_visits(root);rounds=[];completed=[]
    for n,order in enumerate(ORDERS,1):
        selected=[r for r in rows if r['round']==n];ok=all(r['visit_status']=='COMPLETE' and r['valid_for_comparison'] for r in selected)
        if ok:completed.append(n)
        rounds.append(dict(round=n,order=list(order),status='COMPLETE' if ok else 'NOT_RUN',completion='COMPLETE' if ok else 'PARTIAL_OR_FAILED',actual_visits={r['policy']:r['visit_status'] for r in selected}))
    primary=[r for r in rows if r['round'] in completed];valid=[r for r in rows if r['valid_for_comparison']]
    paired={}
    for key in METRICS:
        pairs=[]
        for n in completed:
            full=next(r for r in primary if r['round']==n and r['policy']=='FULL')['sample'][key]
            guard=next(r for r in primary if r['round']==n and r['policy']=='GUARD')['sample'][key]
            pairs.append((full,guard))
        paired[key]=pairing(pairs)
        paired[key]['rounds']=completed
        if key.endswith('_ms'):
            change=paired[key]['change_pct'];paired[key]['time_reduction_pct']=NA if change==NA else -change
    def direction(key,positive):
        value=paired[key]['change_pct'];return NA if value==NA else (value>0 if positive else value<0)
    return dict(**LABELS,complete=len(completed)==6,completed_rounds=completed,rounds=rounds,visits=rows,
        planned_server_sessions=12,planned_warmups=24,planned_measured_requests=12,planned_inference_requests=36,
        valid_measurements=len(valid),functional_admission=v.read(root/'functional-gate-snapshot.json') if (root/'functional-gate-snapshot.json').exists() else NA,analysis_order=['Prefill','Overall request time','Decode','Migration and CPU efficiency'],
        balanced_complete_rounds=policy_stats(primary),descriptive_all_valid_visits=policy_stats(valid),paired=paired,
        descriptive_flags=dict(prefill_throughput_up=direction('prefill_tps',True),prefill_time_down=direction('prefill_ms',False),
            http_request_time_down=direction('http_request_ms',False),internal_time_down=direction('internal_inference_ms',False),
            decode_throughput_down=direction('decode_tps',False),decode_time_up=direction('decode_ms',True)),
        conclusion='NOT_ESTABLISHED; report Prefill/whole-request/Decode tradeoffs; no automatic winner or Linux superiority claim',
        historical_linux=dict(prefill_tps=73.1168375375,decode_tps=19.9456089441,use='REFERENCE_ONLY_NOT_CONCURRENT_CONTROL'),
        phase_cpu_metrics=NA,guard_bpf_runtime_and_migrations=NA,
        scopes=dict(response='Internal Prefill/Decode timers; internal sum and share denominator explicitly labelled',
            http='Measured POST through body read, excluding model/server startup and warmups',
            proc='Both stages and small boundary overhead; cannot allocate CPU/migration/switches to Prefill or Decode',
            busy='Whole-system, not per-process residence',bpf='Whole-session diagnostics; Guard optional detail unavailable'),limitation=LIMITATION)


def print_summary(data):
    print('EXPLORATORY_ONLY; STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL')
    print('Complete paired rounds: '+str(data['completed_rounds'])+'; valid measured requests='+str(data['valid_measurements']))
    for title,keys in [('Prefill',['prefill_tps','prefill_ms']),('Overall',['http_request_ms','internal_inference_ms']),('Decode',['decode_tps','decode_ms'])]:
        print(title+':')
        for key in keys:
            values={p:data['balanced_complete_rounds'][p][key] for p in ['FULL','GUARD']}
            print('  '+key+' '+json.dumps(values,sort_keys=True)+'; Guard/FULL change_pct='+str(data['paired'][key]['change_pct']))
    for row in data['rounds']:print('round '+str(row['round'])+' '+row['status']+' '+str(row['actual_visits']))
    print(data['conclusion']);print(LIMITATION)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--json',action='store_true');p.add_argument('--directory',type=Path,default=Path(__file__).resolve().parent);a=p.parse_args()
    result=summarize(a.directory)
    if a.json:print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    else:print_summary(result)
