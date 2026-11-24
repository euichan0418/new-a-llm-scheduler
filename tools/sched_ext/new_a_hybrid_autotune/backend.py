# SPDX-License-Identifier: GPL-2.0
"""Bind variable thread configurations to the unchanged universal FULL runtime."""
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import uuid
from planning import candidate,possible,profiles,collect,require,fingerprint
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE.parent/'new_a_universal_runtime'))
import contracts,runner,evidence,controller
from runtime_core import sha,read,save,space,domain,module
from build import build as upstream_build
from build_probe import build as probe_build


def normalize_build(report,output):
    value=contracts.build_check(report)
    source=HERE.parent/'new_a/llama_new_a.bpf.c'
    policy=read(HERE.parent/'new_a_final/policy-manifest.json')
    require(sha(source)==policy['files']['tools/sched_ext/new_a/llama_new_a.bpf.c'],'Original E FULL source identity changed')
    if value['source']['path']==str(source) and value['source']['sha256']==sha(source):return Path(report).resolve()
    # Older builder shadows its source variable in the helper loop. Do not edit its report.
    commands=value['commands'];target=value['full_object']['path']
    require(any(isinstance(argv,list) and str(source)in argv and '-DNEW_A_DIAGNOSTICS'in argv and '-o'in argv and argv[argv.index('-o')+1]==target for argv in commands),'Cannot recover explicit FULL source/output build identity')
    fixed=dict(value,source=dict(path=str(source),sha256=sha(source)),metadata_origin=dict(path=str(Path(report).resolve()),sha256=sha(report),reason='UPSTREAM_HELPER_LOOP_SOURCE_SHADOW;_NO_BINARY_OR_POLICY_CHANGE'))
    save(output,fixed);contracts.build_check(output);return Path(output).resolve()


def universal_build(output):
    upstream_build(output)
    return normalize_build(Path(output)/'static-report.json',Path(output)/'tuning-static-report.json')


def token():return time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:10]


def inputs():
    files={}
    for folder in [HERE,HERE.parent/'new_a_universal_runtime',HERE.parent/'new_a_hybrid',HERE.parent/'new_a_portable',HERE.parent/'new_a',HERE.parent/'support']:
        for p in folder.iterdir():
            if p.suffix in ['.py','.h','.c'] or p.name=='new-a-topology-config':files[str(p)]=sha(p)
    return files


def setup(args):
    server=Path(args.server).resolve();model=Path(args.model).resolve()
    require(server.is_file() and os.access(server,os.X_OK) and model.is_file(),'Explicit server/model files required; no download or fallback')
    with server.open('rb')as f:require(f.read(4)==b'\x7fELF','Reviewed Frozen ELF executable required')
    libdirs=[str(Path(p).resolve())for p in args.library_dir]
    require(all(Path(p).is_dir()for p in libdirs),'Missing explicit library directory')
    aliases={str(v):str(Path(v).resolve())for v in [args.server,args.model,*args.elf_root]}
    roots=[str(server),*map(lambda p:str(Path(p).resolve()),args.elf_root)]
    for folder in libdirs:
        for file in Path(folder).glob('*.so*'):
            if file.is_file():roots.append(str(file.resolve()));aliases[str(file)]=str(file.resolve())
    marker=str(Path(args.marker_elf).resolve())if args.marker_elf else None
    if marker:roots.append(marker)
    env=dict(PATH='/usr/bin:/bin',LC_ALL='C',OMP_NUM_THREADS='1',OMP_DYNAMIC='FALSE')
    if libdirs:env['LD_LIBRARY_PATH']=':'.join(libdirs)
    argv=[str(server),'-m',str(model),'--host','127.0.0.1','--port',str(args.port),'--n-gpu-layers','0','--threads','1','--threads-batch','1','--threads-http','2','--ctx-size',str(args.ctx_size),'--parallel','1','--batch-size',str(args.batch_size),'--ubatch-size',str(args.ubatch_size),'--flash-attn','off','--no-ui','--server-compute-profile-mode','legacy']
    server_sha,model_sha=sha(server),sha(model)
    require(args.server_sha256 is None or server_sha==args.server_sha256,'Pinned server hash mismatch')
    require(args.model_sha256 is None or model_sha==args.model_sha256,'Pinned model hash mismatch')
    if args.request:request=read(args.request)
    else:request=module('tuner_frozen_request',HERE.parent/'bench/common.py').REQUEST
    require(request.get('n_predict')==128 and request.get('cache_prompt')is False and request.get('stream')is False and isinstance(request.get('prompt'),str),'Require fixed uncached nonstream 920/128 workload')
    loader=contracts.interpreter(server);resolutions={p:contracts.resolve(p,loader,env)for p in sorted(set(roots))}
    files=sorted({p for v in resolutions.values()for p in v})
    return dict(server_argv=argv,server_env=env,marker=marker,server_sha=server_sha,model_sha=model_sha,model=str(model),server_files=files,files={p:contracts.record(p)for p in sorted(set(files)|{str(model)})},required_server_files=[str(server)],resolutions=resolutions,aliases=aliases,request=request,request_sha256=fingerprint(request))


def housekeeping(detection,shared=False):
    union=set(c for v in possible(detection)for c in v['affinity_cpus']);available=set(detection['portable_plan']['snapshot']['usable_cpus'])
    cpus=sorted(available-union)
    require(cpus or shared,'No common separate housekeeping CPU; explicit --ack-shared-housekeeping required')
    return [cpus[-1]]if cpus else [profiles.checked_plan(detection)['selected_cpus'][-1]]


def capture(args,root,detection,build):
    base=setup(args);power=contracts.power();require(contracts.scheduler_state()=='disabled','sched_ext must be disabled')
    contracts.build_check(build)
    value=dict(schema=1,id='HYBRID_TUNE-'+token(),uid=os.getuid(),host=dict(**contracts.host_identity(),detection=detection,power_binding=contracts.power_binding(power),initial_thermal=power),
        base=base,build=read(build),build_report=str(Path(build).resolve()),housekeeping=housekeeping(detection,args.ack_shared_housekeeping),inputs=inputs(),strict_acceptance='FAILED',safety_verdict='FAIL',overhead_review='OVERHEAD_REVIEW_REQUIRED')
    base['helper_resolutions']={}
    for row in [value['build']['loader'],value['build']['child'],*value['build']['helpers'].values()]:
        resolved=contracts.resolve(row['path'],contracts.interpreter(row['path']),dict(PATH='/usr/bin:/bin',LC_ALL='C'));base['helper_resolutions'][row['path']]=resolved
        for p in resolved:base['files'][p]=contracts.record(p)
    require(value['build']['source']['sha256']==sha(HERE.parent/'new_a/llama_new_a.bpf.c'),'Only original E FULL policy accepted')
    value['inputs']['/etc/ld.so.cache']=sha('/etc/ld.so.cache')
    if 'metadata_origin'in value['build']:value['inputs'][value['build']['metadata_origin']['path']]=value['build']['metadata_origin']['sha256']
    return value


def current(tune):
    require(os.getuid()==tune['uid']!=0,'Normal owner UID required')
    require(contracts.host_identity()=={k:tune['host'][k]for k in contracts.host_identity()},'Kernel/Boot/BTF changed; new campaign required')
    require(collect.inspect(tune['build']['cpu_probe']['path'],None)==tune['host']['detection'],'Topology/online/allowed/cpuset/cache/quota changed')
    require(contracts.power_binding(contracts.power())==tune['host']['power_binding'],'Power conditions changed')
    require(contracts.scheduler_state()=='disabled','sched_ext not disabled; stop')
    for p,expected in tune['inputs'].items():require(sha(p)==expected,'Source/input changed: '+p)
    for alias,target in tune['base']['aliases'].items():require(str(Path(alias).resolve())==target,'Input symlink target changed')
    for p,expected in tune['base']['files'].items():require(contracts.record(p)==expected,'Frozen/model/dependency changed: '+p)
    for path,expected in tune['base']['resolutions'].items():require(contracts.resolve(path,contracts.interpreter(tune['base']['server_argv'][0]),tune['base']['server_env'])==expected,'Frozen dependency graph changed')
    for path,expected in tune['base']['helper_resolutions'].items():require(contracts.resolve(path,contracts.interpreter(path),dict(PATH='/usr/bin:/bin',LC_ALL='C'))==expected,'Helper dependency graph changed')
    contracts.build_check(tune['build_report']);controller.foreign_processes()


def variant(tune,config,path):
    expected=candidate(tune['host']['detection'],config['e_count'],config['compute_threads']);require(config==expected,'Altered configuration')
    basecfg=profiles.recommend(tune['host']['detection'],'compact',config['e_count'])
    g=domain(tune['host']['detection'],basecfg,True);g.update(housekeeping=tune['housekeeping'],housekeeping_shared=bool(set(tune['housekeeping'])&set(g['selected'])))
    configuration=dict(config,cpu_model=basecfg['cpu_model'],requested_cpus=basecfg['requested_cpus'],profile='HYBRID_AUTOTUNE_RESEARCH_CONFIGURATION')
    argv,env=contracts.argv_contract(tune['base'],configuration)
    b=tune['base'];baseline=dict(id=tune['id']+'-'+config['id'],automatic_refresh=False,files=b['files'],resolutions=b['resolutions'],server_files=b['server_files'],model=b['model'],required_server_files=b['required_server_files'],aliases=b['aliases'],server_argv=argv,server_env=env,marker=b['marker'])
    value=dict(schema=1,id=baseline['id'],uid=tune['uid'],host=tune['host'],domain=g,configuration=configuration,build=tune['build'],build_report=tune['build_report'],baseline=baseline,request=b['request'],request_sha256=b['request_sha256'],inputs=tune['inputs'],strict_acceptance='FAILED',safety_verdict='FAIL',overhead_review='OVERHEAD_REVIEW_REQUIRED')
    path=Path(path);path.mkdir(mode=0o700)
    save(path/'campaign.private.json',value);save(path/'host-profile.private.json',value['host']);save(path/'runtime-baseline.private.json',baseline)
    (path/'topology.bin').write_bytes(collect.portable.original.encode(tune['host']['detection']['portable_plan']['policy']))
    save(path/'seal.json',dict(campaign_sha256=sha(path/'campaign.private.json'),host_sha256=sha(path/'host-profile.private.json'),baseline_sha256=sha(path/'runtime-baseline.private.json'),topology_sha256=sha(path/'topology.bin')))
    return value


def admit_newa(path,tune,config):
    campaign=contracts.load_campaign(path,current=True)
    require(campaign['uid']==tune['uid'] and campaign['host']['detection']==tune['host']['detection'],'Foreign functional host')
    require(all(campaign['configuration'][k]==config[k]for k in ['compute_threads','batch_threads','http_threads']),'Functional thread configuration differs')
    require(campaign['domain']['selected']==config['affinity_cpus'] and campaign['domain']['housekeeping']==tune['housekeeping'],'Functional affinity/housekeeping differs')
    b=tune['base'];argv,env=contracts.argv_contract(b,config)
    require(campaign['baseline']['server_argv']==argv and campaign['baseline']['server_env']==env and campaign['baseline']['marker']==b['marker'] and campaign['request_sha256']==b['request_sha256'],'Functional workload/env/marker differs')
    require(campaign['baseline']['files']==b['files'] and campaign['baseline']['aliases']==b['aliases'] and campaign['baseline']['resolutions']==b['resolutions'] and campaign['build']['full_object']==tune['build']['full_object'],'Functional Frozen/model/ELF/build differs')
    require(len(controller.completed(path,campaign,'FUNCTIONAL',False))==4,'Four fresh C/E Audit/Clean raw proofs required; no automatic attach')
    return campaign


def verify(path,campaign,slot):
    result=evidence.verify_session(path,campaign,slot['stage'],slot)
    monitor=[json.loads(x)for x in (Path(path)/'monitor.jsonl').read_text().splitlines()]
    result.update(peak_temperature_c=max(x['max_temperature_c']for x in monitor),observed_process_tid_count=max(len(x['tasks'])for x in monitor),observed_tid_counts=sorted({len(x['tasks'])for x in monitor}),configured_compute=campaign['configuration']['compute_threads'],configured_batch=campaign['configuration']['batch_threads'],mask_tracking='FULL_RAW_VALIDATED'if slot['policy']=='E'else'NOT_APPLICABLE_LINUX',actual_thread_roles='OPENMP_TEAM_SIZES_AUDITED_SEPARATELY;_PROCESS_TID_COUNT_IS_NOT_COMPUTE_THREADS')
    return result


def acquire(path,campaign,root,slot):
    runner.acquire_session(campaign,root,Path(path),slot)
    return verify(path,campaign,slot)
