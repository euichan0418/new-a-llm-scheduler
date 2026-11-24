# SPDX-License-Identifier: GPL-2.0
"""Deterministic search and conservative default-policy selection."""
import itertools
import math
import statistics
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'new_a_hybrid'))
import collect,profiles
require=collect.require
fingerprint=collect.portable.fingerprint

DEFAULT_RULES=dict(min_http_gain_pct=2.0,max_phase_regression_pct=1.0,max_cpu_increase_pct=0.0,confirmation_blocks=6,finalists=3)
T_CRITICAL={6:2.570581835636305,12:2.200985160082949,18:2.109815577833181}


def rules(value):
    require(set(value)==set(DEFAULT_RULES),'Unknown/missing selection rule')
    require(all(type(value[k])in [int,float] and math.isfinite(value[k]) for k in ['min_http_gain_pct','max_phase_regression_pct','max_cpu_increase_pct']),'Nonfinite rule')
    require(value['min_http_gain_pct']>0 and 0<=value['max_phase_regression_pct']<=5 and 0<=value['max_cpu_increase_pct']<=5,'Invalid predeclared margin')
    require(value['confirmation_blocks']in T_CRITICAL and value['finalists']in [2,3],'Use 6/12/18 confirmation blocks and 2/3 finalists')
    return dict(value)


def candidate(detection,e_count,compute=None):
    base=profiles.recommend(detection,'compact',e_count)
    require(base['architecture']=='INTEL_HYBRID','Verified Intel Hybrid CPU required')
    require(e_count==0 or bool(base['selected_e_l2_groups']),'Verified compact E-core L2 groups required for this tuning study')
    c=base['compute_threads'] if compute is None else compute
    require(type(c)is int and 1<=c<=len(base['affinity_cpus']),'Compute exceeds selected execution CPUs')
    value=dict(compute_threads=c,batch_threads=base['batch_threads'],http_threads=2,affinity_cpus=base['affinity_cpus'],e_count=e_count,
        e_l2_groups=base['selected_e_l2_groups'],e_placement=base['e_placement_effective'],topology_fingerprint=detection['fingerprint'])
    value['id']='c'+str(c)+'-b'+str(value['batch_threads'])+'-'+fingerprint(value)[:10]
    return value


def default(detection):
    p=profiles.checked_plan(detection)
    require(p['architecture']=='INTEL_HYBRID' and p['physical_p_cores']>0 and p['physical_e_cores']>0,'Both usable Intel P and E cores required')
    return candidate(detection,p['physical_e_cores']//2)


def prefill(detection):
    e=profiles.checked_plan(detection)['physical_e_cores']
    # The formula control is included even when E/2 falls between the step-2 grid.
    counts=sorted(set(range(0,e+1,2))|{e,e//2})
    return [candidate(detection,n)for n in counts]


def decode(detection,prefill_candidate):
    p=profiles.checked_plan(detection)['physical_p_cores'];n=len(prefill_candidate['affinity_cpus'])
    return [candidate(detection,prefill_candidate['e_count'],c)for c in range(max(1,p-2),min(n,p+2)+1)]


def possible(detection):
    values={v['id']:v for b in prefill(detection)for v in decode(detection,b)}
    values.update({v['id']:v for v in prefill(detection)})
    return sorted(values.values(),key=lambda v:(v['batch_threads'],v['compute_threads'],v['id']))


def balanced(candidates,blocks):
    values=list(candidates);require(values and len({x['id']for x in values})==len(values),'Duplicate/empty candidates')
    out=[]
    for block in range(1,blocks+1):
        if len(values)<=3:
            orders=list(itertools.permutations(values));order=orders[(block-1)%len(orders)]
            if blocks==2:order=values if block==1 else list(reversed(values))
        else:
            offset=((block-1)//2)%len(values);order=values[offset:]+values[:offset]
            if block%2==0:order=list(reversed(order))
        out.extend(dict(block=block,position=i,candidate=v)for i,v in enumerate(order,1))
    return out

METRICS=['http_ms','process_cpu_s','prefill_tps','decode_tps','prefill_ms','decode_ms']


def means(rows):
    grouped={}
    for row in rows:
        require(all(type(row['metrics'][m])in [int,float] and math.isfinite(row['metrics'][m]) and row['metrics'][m]>0 for m in METRICS),'Invalid measured metric')
        grouped.setdefault(row['candidate']['id'],[]).append(row)
    return {k:dict(candidate=v[0]['candidate'],n=len(v),**{m:dict(mean=statistics.mean(r['metrics'][m]for r in v),sd=statistics.stdev(r['metrics'][m]for r in v)if len(v)>1 else None)for m in METRICS})for k,v in grouped.items()}


def paired(rows,base_id,target_id):
    blocks={}
    for r in rows:
        k=(r['block'],r['candidate']['id']);require(k not in blocks,'Duplicate block/config sample');blocks[k]=r
    result=[]
    for b in sorted({r['block']for r in rows}):
        if (b,base_id)in blocks and (b,target_id)in blocks:
            x,y=blocks[b,base_id]['metrics'],blocks[b,target_id]['metrics']
            result.append(dict(block=b,**{m:100*(y[m]/x[m]-1)for m in METRICS},http_difference_ms=y['http_ms']-x['http_ms']))
    return result


def screening(rows,base,r):
    result=[];group=means(rows)
    for key,value in group.items():
        if key==base['id']:continue
        pairs=paired(rows,base['id'],key)
        # Screening is deliberately not a policy-promotion test.
        if len(pairs)==2 and all(p['http_ms']<0 for p in pairs) and -statistics.mean(p['http_ms']for p in pairs)>=r['min_http_gain_pct'] and all(statistics.mean(p[m]for p in pairs)<=r['max_phase_regression_pct'] for m in ['prefill_ms','decode_ms']) and statistics.mean(p['process_cpu_s']for p in pairs)<=r['max_cpu_increase_pct']:
            result.append(value['candidate'])
    return sorted(result,key=lambda c:group[c['id']]['http_ms']['mean'])


def interval(values):
    require(len(values)in T_CRITICAL,'Unsupported independent-block CI size')
    mean=statistics.mean(values);half=T_CRITICAL[len(values)]*statistics.stdev(values)/math.sqrt(len(values))
    return dict(mean=mean,low=mean-half,high=mean+half,n=len(values),method='PAIRED_STUDENT_T_PERCENT_CHANGE_POINTWISE_NOT_GLOBAL_OPTIMALITY')


def promotion(rows,base,target,r):
    p=paired(rows,base['id'],target['id'])
    if len(p)!=r['confirmation_blocks']:return dict(eligible=False,reason='INDEPENDENT_CONFIRMATION_INCOMPLETE')
    ci={m:interval([x[m]for x in p])for m in METRICS}
    conditions=dict(http_consistent=all(x['http_ms']<0 for x in p),practical_http_ci=ci['http_ms']['high'] < -r['min_http_gain_pct'],prefill_ci=ci['prefill_ms']['high']<=r['max_phase_regression_pct'],decode_ci=ci['decode_ms']['high']<=r['max_phase_regression_pct'],cpu_ci=ci['process_cpu_s']['high']<=r['max_cpu_increase_pct'])
    return dict(eligible=all(conditions.values()),conditions=conditions,ci=ci,paired_blocks=p,multiplicity='EXPLORATORY_POINTWISE;_FINALISTS_SELECTED_ON_SEPARATE_DATA')


def report(detection,rows,r,scheduler,complete):
    base=default(detection);groups=means(rows);confirmation=[x for x in rows if x['stage']=='CONFIRMATION'];cgroup=means(confirmation)
    # Report stage-specific observations instead of pooling adaptively repeated samples.
    by_stage={s:means([x for x in rows if x['stage']==s])for s in ['PREFILL','DECODE','CONFIRMATION']}
    checks={k:promotion(confirmation,base,v['candidate'],r)for k,v in cgroup.items()if k!=base['id']}
    winners=[cgroup[k]for k,v in checks.items()if v['eligible']]
    candidate_winner=min(winners,key=lambda x:x['http_ms']['mean'])['candidate']if winners and complete else base
    final=candidate_winner if scheduler=='new-a' else base
    observed={}
    for stage,g in by_stage.items():
        if g:observed[stage]={name:min(g.values(),key=lambda x:sign*x[metric]['mean'])['candidate'] for name,metric,sign in [('http_lowest_mean','http_ms',1),('cpu_lowest_mean','process_cpu_s',1),('prefill_highest_mean','prefill_tps',-1),('decode_highest_mean','decode_tps',-1)]}
    single=min(rows,key=lambda x:x['metrics']['http_ms'])if rows else None
    return dict(cpu=detection['summary'],default_policy=base,observed_by_stage=observed,fastest_single_observation=single,statistics_by_stage=by_stage,
        final_new_a_recommendation=final,linux_execution_recommendation=candidate_winner if scheduler=='linux' else None,
        reason='CONFIRMED_CPU_EXCEPTION'if final['id']!=base['id']else'DEFAULT_RETAINED_NO_SUFFICIENT_NEW_A_EXCEPTION_EVIDENCE',
        policy_equivalence_proven=False,global_optimum_proven=False,complete=complete,scheduler=scheduler,confirmation_checks=checks,
        gain_scope=scheduler,default_formula_gain_pct=(-checks[candidate_winner['id']]['ci']['http_ms']['mean']if candidate_winner['id']in checks else None),
        affinity_effect='PREFILL_CONFIG_CHANGES_BATCH_AND_MASK;_DECODE_GRID_FIXES_BATCH_AND_MASK;_FORMULA_CONTROL_MAY_HAVE_DIFFERENT_MASK',
        performance_claim='NO_MEASUREMENTS'if not rows else'OBSERVED_ONLY_WITH_DECLARED_CONFIRMATION_GATES',
        strict_acceptance='FAILED',safety_verdict='FAIL',overhead_review='OVERHEAD_REVIEW_REQUIRED')
