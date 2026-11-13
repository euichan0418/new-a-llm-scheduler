# SPDX-License-Identifier: GPL-2.0
"""Offline proposed guard-format validation; never accepts a historical full export as guard."""

def validate_guard(value, allowed, possible):
    def require(ok, message):
        if not ok:raise ValueError(message)
    require(value.get('instrumentation_mode')=='GUARD_NO_DETAIL', 'Guard mode provenance required')
    require(value.get('optional_metrics')==dict(runtime_ns='NOT_AVAILABLE',migrations='NOT_AVAILABLE'), 'Optional metrics must be unavailable')
    rows=value['cpus']
    require(len(rows)==len(possible) and {r['cpu'] for r in rows}==set(possible), 'Complete unique possible-CPU export required')
    phases=running=selected=0
    for row in rows:
        for key in ['running','stopping','mask_violations','tracking_failures','events_attempted','events_dropped','phase_seen','runtime_ns','migrations']:
            require(isinstance(row[key],int) and row[key]>=0,'Missing/negative counter')
        require(row['running']==row['stopping'], 'Unbalanced callbacks')
        require(row['cpu'] in allowed or row['running']==row['stopping']==0, 'Actual execution outside prepared CPUs')
        require(row['mask_violations']==row['tracking_failures']==row['events_dropped']==0, 'Mandatory safety/export failure')
        require(row['runtime_ns']==row['migrations']==0, 'Guard placeholder fields contain unexpected data')
        require(len(row['selected'])==4 and all(isinstance(n,int) and n>=0 for n in row['selected']),'Invalid selection counters')
        phases|=row['phase_seen'];running+=row['running'];selected+=sum(row['selected'])
    require(running>0 and selected>0 and phases&6==6, 'Missing execution/selection/phase evidence')
    require(value.get('event_schema')==2 and value.get('events_export_error')==0, 'Incomplete event export')
    require(isinstance(value.get('events'),list), 'Missing event list')
    require(sum(r['events_attempted'] for r in rows)==len(value['events']), 'Incomplete event coverage')
    for event in value['events']:
        require(event.get('kind')==3 and event.get('allowed_complete') is True and event.get('regular_complete') is True and event.get('requested_complete') is True,'Unexplained/incomplete event')
    return dict(scope='DIAGNOSTIC_EVIDENCE_ONLY',record_type='EXPLORATORY_ONLY',strict_acceptance='FAILED',safety_verdict='FAIL',
                suite_acceptance='NOT_EVALUATED',running=running,selection=selected,phase_seen=phases,
                runtime_ns='NOT_AVAILABLE',migrations='NOT_AVAILABLE',kernel_validation='NOT_RUN',
                remaining_gates='Identity, current masks/policies, host, request, thermal, attach and cleanup must still pass in a separate harness')
