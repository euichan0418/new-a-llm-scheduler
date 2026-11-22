# SPDX-License-Identifier: GPL-2.0
"""Dynamic FULL evidence checks; no old fixed-mask validator is modified."""
import json
import math
import re
from pathlib import Path
from runtime_core import require, read, sha, digest

SYMBOLS={'llama_select_cpu','llama_enqueue','llama_dispatch','llama_running','llama_stopping','new_a_set_cpumask','llama_enable','llama_init','llama_exit','llama_phase'}
CALLBACKS={'select_cpu':'llama_select_cpu','enqueue':'llama_enqueue','dispatch':'llama_dispatch','running':'llama_running','stopping':'llama_stopping'}


def integer(value):return type(value) is int and value>=0


def diagnostics(value,domain):
    require(value['instrumentation_mode']=='FULL' and value['optional_metrics']==dict(runtime_ns='COLLECTED',migrations='COLLECTED'),'FULL diagnostic provenance required')
    rows=value['cpus'];require(len(rows)==len(domain['possible']) and {r['cpu'] for r in rows}==set(domain['possible']),'Possible CPU slot/ID coverage changed')
    totals=[0]*4;running=runtime=migrations=phases=0
    for row in rows:
        for key in ['cpu','running','stopping','runtime_ns','migrations','mask_violations','tracking_failures','phase_seen','events_attempted','events_dropped']:require(integer(row[key]),'Invalid FULL counter')
        require(row['running']==row['stopping'],'Unbalanced running/stopping')
        require(row['mask_violations']==row['tracking_failures']==row['events_dropped']==0,'Mask/Tracking/export failure; no transition exemption')
        require(row['cpu'] in domain['selected'] or row['running']==row['stopping']==row['runtime_ns']==0,'Execution outside selected mask')
        require(len(row['selected'])==4 and all(integer(n) for n in row['selected']),'Invalid tier counters')
        totals=[a+b for a,b in zip(totals,row['selected'])];running+=row['running'];runtime+=row['runtime_ns'];migrations+=row['migrations'];phases|=row['phase_seen']
    require(running>0 and runtime>0 and sum(totals)>0 and phases&6==6,'Missing actual FULL runtime/phase/selection')
    require(totals[2]==0,'SMT selected although profile excludes siblings')
    require(all(any(r['cpu']==c and r['running']>0 for r in rows) for c in domain['selected']),'Selected CPUs not all observed; functional coverage insufficient')
    require(value['event_schema']==2 and value['mask_cpu_limit']==domain['mask_bits'] and value['events_export_error']==0,'Mask/event domain mismatch')
    events=value['events'];require(sum(r['events_attempted'] for r in rows)==len(events),'Missing error event records')
    for event in events:
        require(event['kind']==3 and type(event['timestamp_ns'])is int and event['timestamp_ns']>0,'Error event or timestamp missing')
        require(all(event[k]is True for k in ['allowed_complete','regular_complete','requested_complete']),'Incomplete mask evidence')
        require(all(event[k]==domain['selected'] for k in ['allowed_cpus','regular_cpus','requested_cpus']),'Unexpected task mask transition')
    live=value['live_monitor'];require(live['status']=='PASS' and live['kernel_opened']is True and live['failures']==0 and live['samples']>=2,'Live/kernel guard incomplete')
    return dict(execution_callbacks=running,task_observed_runtime_ns=runtime,migrations=migrations,phase_seen=phases,selected=totals)


def loaded_identity(text,dump,expected_sha):
    prefix='UNIVERSAL_OBJECT_IDENTITY '
    lines=[line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    require(len(lines)==1,'Loaded object attestation missing/ambiguous')
    value=json.loads(lines[0]);require(value['object_sha256']==expected_sha,'Loaded ELF hash differs')
    programs=value['programs'];require(len(programs)==10 and {p['symbol'] for p in programs}==SYMBOLS and len({p['id'] for p in programs})==10,'Loaded callback set missing/duplicated')
    require(all(type(p['id'])is int and p['id']>0 and p['btf_id']>0 and re.fullmatch('[0-9a-f]{16}',p['tag']) for p in programs),'Invalid program identity')
    ops=[];btfs=[]
    def walk(x):
        if isinstance(x,dict):
            if x.get('name')=='llama_new_a' and 'select_cpu' in x:ops.append(x)
            info=x.get('bpf_map_info',{})
            if info.get('name')=='llama_ops' and info.get('type')==26:btfs.append(info['btf_id'])
            for child in x.values():walk(child)
        elif isinstance(x,list):
            for child in x:walk(child)
    walk(dump);require(len(ops)==len(btfs)==1,'Active struct_ops/BTF binding ambiguous')
    flags=ops[0]['flags'];require((int(flags,0) if isinstance(flags,str) else flags)==8,'Partial flag changed')
    by_name={p['symbol']:p for p in programs};seen=set()
    for text in ops[0].values():
        match=re.search(r'\b(\w+)/prog_id:(\d+)$',str(text))
        if match:
            symbol,n=match[1],int(match[2]);require(symbol in by_name and by_name[symbol]['id']==n and by_name[symbol]['btf_id']==btfs[0],'Attached callback not from attested object');seen.add(symbol)
    require(seen==SYMBOLS-{'llama_phase'},'Active auxiliary callback coverage incomplete')
    return value


def response(path,role,request_sha):
    raw=(path/(role+'-response.json')).read_bytes();value=json.loads(raw);client=read(path/(role+'-client.json'))
    require(client['role']==role and client['http_status']==200 and client['request_sha256']==request_sha and client['response_sha256']==sha(path/(role+'-response.json')),'HTTP/request/hash binding failed')
    require(value['tokens_evaluated']==920 and value['tokens_predicted']==128 and bool(value['content']),'Unexpected tokens or empty response')
    require(type(client['begin_ns'])is int and type(client['end_ns'])is int and 0<client['begin_ns']<client['end_ns'],'Invalid HTTP monotonic clock')
    require(type(client['request_wall_ms'])in [int,float] and math.isfinite(client['request_wall_ms']) and client['request_wall_ms']>0 and abs(client['request_wall_ms']-(client['end_ns']-client['begin_ns'])/1e6)<1e-6,'HTTP wall/clock mismatch')
    t=value['timings'];require(t['prompt_n']==920 and t['predicted_n']==128 and all(type(t[k])in [int,float] and 0<t[k]<1e9 for k in ['prompt_ms','predicted_ms','prompt_per_second','predicted_per_second']),'Invalid response timing')
    return value,client


def verify_session(path,campaign,stage,slot,prospective=None):
    path=Path(path);proof=read(path/'completion.json') if prospective is None else prospective
    require(proof['contract_binding']==digest({k:campaign[k] for k in ['id','host','domain','baseline']}),'Evidence belongs to a different Host/baseline/contract')
    require(set(proof['files'])=={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and p.name!='completion.json'},'Raw proof file coverage incomplete')
    require(proof['status']=='COMPLETE' and proof['campaign_id']==campaign['id'] and proof['stage']==stage and proof['slot']==slot,'Completion identity differs')
    for name,expected in proof['files'].items():require(sha(path/name)==expected,'Raw evidence changed: '+name)
    require(read(path/'cleanup.json')==dict(status='PASS',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True),'Cleanup absent or failed')
    kernel=read(path/'kernel-watch-result.json');require(kernel['status']=='PASS' and kernel['kernel_opened']is True and kernel['fd_closed']is True and kernel['bpf_loaded']is False,'Common kernel guard incomplete')
    samples=[json.loads(line) for line in (path/'monitor.jsonl').read_text().splitlines()];require(len(samples)>=2,'Monitor incomplete')
    require(any(sample['tasks'] for sample in samples),'No actual TID evidence')
    for sample in samples:
        require(sample['power_binding']==campaign['host']['power_binding'] and sample['max_temperature_c']<85 and sample['throttle_unchanged']is True,'Power/thermal changed')
        require(sample['online']==campaign['domain']['online'] and sample['allowed']==campaign['domain']['allowed'],'Online/allowed changed')
        require(sample['stats_sysctl']==0,'Runtime Statistics activated')
        if sample['tasks']:
            require(all(t['uid']==sample['uid']==campaign['uid'] and t['policy']==(7 if slot['policy']=='E' else 0) and t['affinity']==campaign['domain']['selected'] for t in sample['tasks']),'TID UID/policy/mask changed')
    server=read(path/'server-identity.json');require(server['uid']==campaign['uid'] and server['policy']==(7 if slot['policy']=='E' else 0) and server['affinity']==campaign['domain']['selected'],'Selected target identity mismatch')
    config=campaign['configuration'];contract=read(path/'thread-contract.json');env=dict(campaign['baseline']['server_env'],OMP_DISPLAY_AFFINITY='TRUE' if slot['audit'] else 'FALSE')
    require(contract['argv']==campaign['baseline']['server_argv'] and contract['environment']==env,'Thread argv/environment differs')
    if slot['audit']:
        text=(path/'server.stderr.log').read_text();rows=[(int(a),int(b),int(c)) for a,b,c in re.findall(r'NEWA_TEAM pid=(\d+) team=(\d+) index=(\d+) native=\S+',text)]
        require(rows and len(rows)==text.count('NEWA_TEAM') and all(pid==server['pid'] and n in {config['compute_threads'],config['batch_threads']} and 0<=i<n for pid,n,i in rows),'Audit team display incomplete')
        for n in {config['compute_threads'],config['batch_threads']}:require({i for pid,team,i in rows if team==n}==set(range(n)),'Required Compute/Batch team not observed')
    else:require('NEWA_TEAM' not in (path/'server.stderr.log').read_text(),'Clean contaminated by audit instrumentation')
    mappings=read(path/'mapping-proof.json');require(mappings['required_files_present']is True and set(campaign['baseline']['required_server_files'])<=set(mappings['files']),'Actual server mapping evidence missing')
    for name,row in mappings['files'].items():require(row==campaign['baseline']['files'][name],'Mapped ELF identity differs')
    require(set(mappings['files'])<=set(campaign['baseline']['server_files']),'Unknown executable mapping')
    actual=set()
    import os
    for line in mappings['raw_maps'].splitlines():
        fields=line.split(None,5)
        if len(fields)<6 or 'x' not in fields[1] or not fields[5].startswith('/'):continue
        name=fields[5];require(name in mappings['files'],'Unknown raw executable mapping')
        item=mappings['files'][name]
        require(int(fields[4])==item['inode'] and os.makedev(*[int(v,16) for v in fields[3].split(':')])==item['device'],'Raw mapping inode/device differs')
        actual.add(name)
    require(actual==set(mappings['files']),'Raw mapping/file coverage differs')
    if slot['policy']=='E':
        registrations=re.findall(r'llama_scx: registered tgid=(\d+) uid=(\d+)',(path/'loader.log').read_text())
        require(registrations==[(str(server['pid']),str(server['uid']))],'Selected TGID registration differs')
        identity=loaded_identity((path/'loader.log').read_text(),read(path/'struct-ops-dump.json'),campaign['build']['full_object']['sha256'])
        diagnostics(read(path/'scheduler-stats.json'),campaign['domain'])
    else:identity=None
    # Reuse the exact Statistics OFF counter semantics, replacing no criterion.
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'new_a_portable_runtime'))
    import stats_off
    binding=stats_off.targets(read(path/'struct-ops-dump.json')) if identity else None
    previous=None
    for when in ['attached','before-measured','after-measured']:
        raw=read(path/('stats-off-'+when+'-command.json'));require(raw['returncode']==0,'Statistics helper failed, actual OFF NOT_VERIFIED')
        value=json.loads(raw['stdout']);stats_off.validate(value,slot['policy'],binding)
        inventory=read(path/('program-inventory-'+when+'-command.json'));require(inventory['returncode']==0,'BPF inventory unavailable')
        own={p['id'] for p in identity['programs']} if identity else set()
        require(all(p['id'] in own or p.get('type') not in ['tracing','raw_tracepoint','raw_tracepoint_writable','tracepoint','kprobe'] for p in json.loads(inventory['stdout'])),'Unapproved tracing program in raw evidence')
        if identity:
            for item,symbol in zip(value['programs'],CALLBACKS.values()):require(all(item[k]==next(x for x in identity['programs'] if x['symbol']==symbol)[k] for k in ['id','btf_id','tag']),'Live BPF tag/ID mismatch')
        if previous is not None:require(value['programs']==previous,'Program identity changed across request')
        previous=value['programs']
    results=[response(path,role,campaign['request_sha256']) for role in ['warmup-1','warmup-2','measured']]
    cpu=read(path/'process-cpu.json');require(cpu['pid']==server['pid'] and cpu['starttime']==server['starttime'] and cpu['after_ns']>cpu['before_ns'] and cpu['before_mono_ns']<=results[-1][1]['begin_ns']<results[-1][1]['end_ns']<=cpu['after_mono_ns'],'Whole-process CPU clock bracket invalid')
    data,client=results[-1];t=data['timings']
    resources=resource_metrics(read(path/'metrics-before.json'),read(path/'metrics-after.json'),campaign['domain']['selected'])
    return dict(resources=resources,status='FUNCTIONAL_PASS' if stage=='FUNCTIONAL' else 'RAW_COMPLETE',http_ms=client['request_wall_ms'],process_cpu_s=(cpu['after_ns']-cpu['before_ns'])/1e9,
        prefill_tps=t['prompt_per_second'],decode_tps=t['predicted_per_second'],prefill_ms=t['prompt_ms'],decode_ms=t['predicted_ms'],openmp_roles='NOT_VERIFIED',bpf_policy='UNCHANGED_FULL',is_safety_approval=False)


def resource_metrics(before,after,selected):
    a,b=before['tids'],after['tids'];require(a and a.keys()==b.keys(),'Stable boundary TID set required')
    totals={key:0 for key in ['migrations','context_switches','runtime_ns']}
    for tid in a:
        require(a[tid]['starttime']==b[tid]['starttime'],'TID identity changed')
        for key in totals:
            require(integer(a[tid][key]) and integer(b[tid][key]) and b[tid][key]>=a[tid][key],'Missing/decreasing resource counter')
            totals[key]+=b[tid][key]-a[tid][key]
    busy={}
    for c in selected:
        x,y=before['cpus']['cpu'+str(c)],after['cpus']['cpu'+str(c)];delta=y['total']-x['total'];idle=y['idle']-x['idle']
        require(delta>0 and 0<=idle<=delta,'Invalid CPU busy interval');busy[str(c)]=100*(1-idle/delta)
    return dict(**totals,per_cpu_busy_pct=busy,scope='STABLE_BOUNDARY_TIDS;_SYSTEM_BUSY_NOT_TARGET_RESIDENCE')
