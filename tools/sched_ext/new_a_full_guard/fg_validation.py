# SPDX-License-Identifier: GPL-2.0
"""Pure evidence checks. Guard optional metrics are never fabricated zeros."""
import hashlib
import json
import os
import math
from pathlib import Path
from fg_config import LABELS,SERVER_CPUS,NA,REQUEST_SHA


def require(ok,message):
    if not ok:raise ValueError(message)


def read(path):return json.loads(Path(path).read_text())


def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def diagnostics(value,policy,*,probe=False):
    require(policy in ['FULL','GUARD'],'Unknown instrumentation mode')
    guard=policy=='GUARD';mode='GUARD_NO_DETAIL' if guard else 'FULL'
    require(value.get('instrumentation_mode')==mode,'Diagnostic mode provenance mismatch')
    expected=NA if guard else 'COLLECTED'
    require(value.get('optional_metrics')==dict(runtime_ns=expected,migrations=expected),'Optional metric provenance mismatch')
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
        else:
            require(all(isinstance(r[k],int) and r[k]>=0 for k in ['runtime_ns','migrations']),'Invalid FULL detail')
            require(r['cpu'] in SERVER_CPUS or r['runtime_ns']==0,'FULL runtime outside affinity')
            runtime+=r['runtime_ns'];migrations+=r['migrations']
    require(running>0 and sum(tiers)>0 and phases&6==6,'Missing real execution/selection/Prefill/Decode evidence')
    require(tiers[2]==0,'SMT selected despite fixed mask excluding SMT')
    if not guard:require(runtime>0,'No FULL running interval evidence')
    require(value.get('event_schema')==2 and value.get('mask_cpu_limit')==16 and value.get('events_export_error')==0,'Event export/configuration mismatch')
    events=value.get('events');require(isinstance(events,list),'Missing event export')
    require(sum(r['events_attempted'] for r in rows)==len(events),'Incomplete event export')
    for event in events:
        require(event.get('kind')==3 and all(event.get(k) is True for k in ['allowed_complete','regular_complete','requested_complete']),'Unexplained/incomplete event')
    monitor=value.get('live_monitor',{})
    require(monitor.get('status')=='PASS' and monitor.get('failures')==0 and monitor.get('kernel_opened') is True
            and isinstance(monitor.get('samples'),int) and monitor['samples']>=2,'Mandatory live/kernel monitor incomplete')
    return dict(**LABELS,instrumentation_mode=mode,execution_callbacks=running,selected=tiers,phase_seen=phases,
                mask_violations=0,tracking_failures=0,task_observed_runtime_ns=NA if guard else runtime,
                observed_cpu_changes=NA if guard else migrations,bpf_program_execution_ns=NA,
                scope='Whole session, including startup/warmups/measurement/shutdown; not per-phase or request-only')


def response(path,role):
    raw=(Path(path)/(role+'-response.json')).read_bytes();data=json.loads(raw);client=read(Path(path)/(role+'-client.json'))
    require(client.get('request_sha256')==REQUEST_SHA and client.get('response_sha256')==hashlib.sha256(raw).hexdigest(),'Request/response hash mismatch')
    require(client['http_status']==200 and client['role']==role,'HTTP/role verification failed')
    require(data['tokens_evaluated']==920 and data['tokens_predicted']==128 and bool(data['content']),'Token/content verification failed')
    timing=data['timings'];require(timing['prompt_n']==920 and timing['predicted_n']==128,'Timing token mismatch')
    for k in ['prompt_ms','predicted_ms','prompt_per_second','predicted_per_second']:
        require(finite(timing[k]) and timing[k]>0,'Missing/nonfinite response timing')
    require(finite(client['request_wall_ms']) and client['request_wall_ms']>0,'Bad HTTP timing')
    return data,client


def complete_visit(path,expected,*,functional=False):
    path=Path(path);r=read(path/'result.json')
    require(all(r.get(k)==v for k,v in expected.items()),'Visit identity changed')
    require(all(r.get(k)==v for k,v in LABELS.items()),'Evidence label changed')
    require(r['visit_status']=='COMPLETE' and r['valid_for_comparison'] is (not functional) and r['errors']==[],'Visit incomplete/invalid')
    require(r['sched_ext_after']=='disabled' and read(path/'cleanup.json')==dict(status='PASS',errors=[],sched_ext='disabled'),'Cleanup failed')
    require(r['configuration_guard']['status']=='COMPLETED' and r['configuration_guard']['errors']==[],'Power guard incomplete')
    require(r['thermal_throttle_whole_visit']['status']=='NO_INCREMENT_OBSERVED','Thermal throttling')
    attach=read(path/'kernel-load-attach.json')
    require(attach.get('verifier')=='LOAD_SUCCEEDED' and attach.get('attach')=='OBSERVED' and attach.get('ops')=='llama_new_a' and attach.get('live_monitor')=='READY','Verifier/attach/live evidence incomplete')
    processes=read(path/'processes.json');server=processes['server']
    require(server['uid']==os.getuid() and server['policy']==7 and set(server['affinity'])==SERVER_CPUS,'Selected server identity/mask/policy changed')
    require(read(path/'guard-result.json')==r['configuration_guard'] and r['configuration_guard'].get('samples',0)>=2,'Guard export incomplete/inconsistent')
    snapshots=['policies-before.json','policies-workers.json'] if r.get('kind')=='tracking-probe' else ['policies-before.json','policies-pre-measured.json','policies-after.json']
    for name in snapshots:
        tasks=read(path/name)
        require(bool(tasks) and all(x['uid']==os.getuid() and x['policy']==7 and set(x['affinity'])==SERVER_CPUS for x in tasks),'TID policy/mask evidence incomplete')
        if name=='policies-workers.json':require(len(tasks)==9,'Eight-worker plus coordinator evidence missing')
    checked=diagnostics(read(path/'scheduler-stats.json'),r['policy'],probe=r.get('kind')=='tracking-probe')
    if r.get('kind')=='tracking-probe':
        require(r.get('probe_output_verified') is True and r['requests_completed']==[],'Synthetic tracking probe incomplete')
        output=(path/'server.stdout.log').read_text()
        require(output.count('worker iterations=160 violations=0')==8 and 'affinity_trace ' not in output,'Raw probe output invalid')
        require(read(path/'command.json')['argv'][-2:]==['8','-1'],'Probe thread count/affinity transition arguments changed')
    else:
        roles=['warmup-1','warmup-2','functional' if functional else 'measured']
        require(r['requests_completed']==roles,'Request roles incomplete')
        for role in roles:response(path,role)
        require(r['sample']['measured_thermal_throttle']['status']=='NO_INCREMENT_OBSERVED','Request thermal evidence invalid')
        if not functional:
            from fg_config import METRICS
            require(all(finite(r['sample'].get(k)) for k in METRICS),'Required request metrics missing')
            require(all(r['sample'][k]>0 for k in ['prefill_tps','prefill_ms','decode_tps','decode_ms','http_request_ms','internal_inference_ms','proc_window_ms','cpu_time_s']),'Invalid performance timing')
            require(all(r['sample'][k]>=0 for k in ['context_switches','migrations','migration_per_cpu_s','context_switches_per_cpu_s']),'Negative metric')
    return r,checked


def request_metrics(before,after,data,client,system_busy,thermal):
    a,b=before['threads'],after['threads']
    require(bool(a) and a.keys()==b.keys() and all(a[t]['starttime']==b[t]['starttime'] for t in a),'Stable TID/starttime set required')
    sums={}
    for key in ['runtime_ns','context_switches','nr_switches','se.nr_migrations']:
        require(all(isinstance(a[t].get(key),int) and isinstance(b[t].get(key),int) and b[t][key]>=a[t][key] for t in a),'Missing/decreasing proc counter: '+key)
        sums[key]=sum(b[t][key]-a[t][key] for t in a)
    elapsed=(after['monotonic_ns']-before['monotonic_ns'])/1e9;cpu=sums['runtime_ns']/1e9
    require(elapsed>0 and cpu>0,'Invalid proc timing')
    per_cpu={}
    for c in sorted(SERVER_CPUS):
        x,y=before['cpus']['cpu'+str(c)],after['cpus']['cpu'+str(c)]
        total,idle=y['total']-x['total'],y['idle']-x['idle']
        require(total>0 and 0<=idle<=total,'Invalid system CPU counters')
        per_cpu[str(c)]=100*(1-idle/total)
    t=data['timings'];prefill,decode=t['prompt_ms'],t['predicted_ms'];internal=prefill+decode;http=client['request_wall_ms']
    require(all(finite(x) and x>0 for x in [prefill,decode,http,t['prompt_per_second'],t['predicted_per_second']]),'Invalid response metric')
    require(finite(system_busy) and 0<=system_busy<=100,'Invalid system busy')
    return dict(prefill_tps=t['prompt_per_second'],prefill_ms=prefill,decode_tps=t['predicted_per_second'],decode_ms=decode,
        internal_inference_ms=internal,http_request_ms=http,proc_window_ms=elapsed*1000,cpu_time_s=cpu,
        prefill_internal_share_pct=100*prefill/internal,decode_internal_share_pct=100*decode/internal,
        prefill_http_share_pct=100*prefill/http,decode_http_share_pct=100*decode/http,
        http_minus_internal_ms=http-internal,context_switches=sums['context_switches'],nr_switches=sums['nr_switches'],
        migrations=sums['se.nr_migrations'],migration_per_cpu_s=sums['se.nr_migrations']/cpu,
        context_switches_per_cpu_s=sums['context_switches']/cpu,system_busy_pct=system_busy,
        p_mean_busy_pct=sum(per_cpu[str(c)] for c in [0,2,4,6])/4,
        e_mean_busy_pct=sum(per_cpu[str(c)] for c in range(8,15))/7,per_cpu_system_busy_pct=per_cpu,
        measured_thermal_throttle=thermal,core_metrics_complete=True,
        stage_metrics={stage:{k:NA for k in ['cpu_time_s','p_runtime_share','e_runtime_share','migrations','context_switches','worker_imbalance','per_cpu_process_runtime']} for stage in ['prefill','decode']},
        bpf_program_execution_ns=NA,process_window_scope='Both stages plus proc/power/serialization boundary overhead; summed stable-TID schedstat CPU seconds',
        stage_timing_scope='Frozen response timing; internal_inference_ms is prompt_ms+predicted_ms, not independent end-to-end timing',
        http_scope='Blocking HTTP POST through full response body read; excludes startup/model load/warmups/observation gaps',
        busy_scope='Whole-system activity, including background applications; not process CPU residence')
