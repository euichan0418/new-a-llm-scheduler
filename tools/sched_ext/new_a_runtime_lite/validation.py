# SPDX-License-Identifier: GPL-2.0
"""Independent elapsed-only diagnostic contract; never fabricates FULL runtime.

Derived from new_a_full_guard/fg_validation.py::diagnostics.
Mandatory checks are retained; the prepared mask is exactly P4+E4.
This validator alone is not complete session or safety admission.
"""
NA = 'NOT_AVAILABLE'
SERVER_CPUS = {0, 2, 4, 6, 8, 9, 10, 11}
LABELS = dict(strict_acceptance='FAILED', safety_verdict='FAIL', energy_review='OVERHEAD_REVIEW_REQUIRED')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def diagnostics(value,policy,*,probe=False):
    require(policy in ['FULL','GUARD','RUNTIME_LITE'],'Unknown instrumentation mode')
    guard=policy=='GUARD';lite=policy=='RUNTIME_LITE';mode='FULL_NO_ELAPSED_RESEARCH' if lite else 'GUARD_NO_DETAIL' if guard else 'FULL'
    require(value.get('instrumentation_mode')==mode,'Diagnostic mode provenance mismatch')
    expected=NA if guard else 'COLLECTED'
    require(value.get('optional_metrics')==dict(runtime_ns=NA if lite else expected,migrations=expected),'Optional metric provenance mismatch')
    rows=value['cpus'];require(len(rows)==16 and {r['cpu'] for r in rows}==set(range(16)),'Incomplete/duplicate CPU export')
    running=phases=0;tiers=[0]*4;runtime=migrations=0
    for r in rows:
        for key in ['running','stopping','mask_violations','tracking_failures','events_attempted','events_dropped','phase_seen']:
            require(isinstance(r[key],int) and not isinstance(r[key],bool) and r[key]>=0,'Invalid diagnostic counter')
        require(r['running']==r['stopping'],'Unbalanced running/stopping')
        require(r['cpu'] in SERVER_CPUS or r['running']==r['stopping']==0,'Execution outside fixed prepared affinity')
        require(r['mask_violations']==r['tracking_failures']==r['events_dropped']==0,'Mask/Tracking/export failure; no transition exemption')
        require(len(r['selected'])==4 and all(isinstance(x,int) and x>=0 for x in r['selected']),'Bad selection counters')
        tiers=[a+b for a,b in zip(tiers,r['selected'])];running+=r['running'];phases|=r['phase_seen']
        if guard:require(r['runtime_ns']==r['migrations']==NA,'Guard optional fields must be NOT_AVAILABLE')
        elif lite:
            require(r['runtime_ns']==NA,'Unmeasured elapsed time must be NOT_AVAILABLE')
            require(isinstance(r['migrations'],int) and not isinstance(r['migrations'],bool) and r['migrations']>=0,'Missing/invalid Migration counter')
            migrations+=r['migrations']
        else:
            require(all(isinstance(r[k],int) and r[k]>=0 for k in ['runtime_ns','migrations']),'Invalid FULL detail')
            require(r['cpu'] in SERVER_CPUS or r['runtime_ns']==0,'FULL runtime outside affinity')
            runtime+=r['runtime_ns'];migrations+=r['migrations']
    require(running>0 and sum(tiers)>0 and phases&6==6,'Missing real execution/selection/Prefill/Decode evidence')
    require(tiers[2]==0,'SMT selected despite fixed mask excluding SMT')
    if not guard and not lite:require(runtime>0,'No FULL running interval evidence')
    require(value.get('event_schema')==2 and value.get('mask_cpu_limit')==16 and value.get('events_export_error')==0,'Event export/configuration mismatch')
    events=value.get('events');require(isinstance(events,list),'Missing event export')
    require(sum(r['events_attempted'] for r in rows)==len(events),'Incomplete event export')
    for event in events:
        require(event.get('kind')==3 and all(event.get(k) is True for k in ['allowed_complete','regular_complete','requested_complete']),'Unexplained/incomplete event')
    monitor=value.get('live_monitor',{})
    require(monitor.get('status')=='PASS' and monitor.get('failures')==0 and monitor.get('kernel_opened') is True
            and isinstance(monitor.get('samples'),int) and monitor['samples']>=2,'Mandatory live/kernel monitor incomplete')
    return dict(**LABELS,instrumentation_mode=mode,execution_callbacks=running,selected=tiers,phase_seen=phases,
                mask_violations=0,tracking_failures=0,task_observed_runtime_ns=NA if guard or lite else runtime,
                observed_cpu_changes=NA if guard else migrations,bpf_program_execution_ns=NA,
                scope='Whole session, including startup/warmups/measurement/shutdown; not per-phase or request-only')
