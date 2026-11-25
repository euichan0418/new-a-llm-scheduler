# SPDX-License-Identifier: GPL-2.0
"""Private Host/Baseline capture and read-only runtime admission."""
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid
from runtime_core import HERE, ROOT, LABELS, collect, profiles, domain, read, save, sha, digest, require, space, module

ENV_KEYS={'PATH','LC_ALL','LD_LIBRARY_PATH','OMP_NUM_THREADS','OMP_DYNAMIC','OMP_DISPLAY_AFFINITY','OMP_AFFINITY_FORMAT'}
OMP_FORMAT='NEWA_TEAM pid=%P team=%N index=%n native=%i'
UNMANAGED_POWER='USER_MANAGED_UNCONTROLLED_V1'


def unmanaged(value):
    mode=value.get('power_management')
    require(mode in [None,UNMANAGED_POWER],'Unknown power contract')
    return mode==UNMANAGED_POWER


def evidence_binding(value):
    binding={k:value[k]for k in ['id','host','domain','baseline']}
    for k in ['power_management','interference_contract','cpu_isolation']:
        if k in value:binding[k]=value[k]
    return digest(binding)


def thermal():
    """Thermal safety only: never read governor/EPP/Turbo/profile/AC controls."""
    temperatures=[]
    for path in Path('/sys/class/thermal').glob('thermal_zone*/temp'):
        n=int(path.read_text().strip())
        require(-10000<=n<=150000,'Invalid thermal sensor')
        temperatures.append(n/1000)
    require(temperatures and max(temperatures)<85,'Thermal missing or >=85C')
    return dict(temperatures_c=temperatures,throttle_counts=throttle_counts())


def conditions(value):
    if unmanaged(value):
        require('power_binding' not in value['host'],'Unmanaged campaign contains managed power binding')
        return thermal()
    current=power()
    require(power_binding(current)==value['host']['power_binding'],'Power binding changed')
    return current


def scheduler_state():return Path('/sys/kernel/sched_ext/state').read_text().strip()

def host_identity():
    return dict(kernel=platform.release(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),btf_sha256=sha('/sys/kernel/btf/vmlinux'))


def throttle_counts():
    return {str(p):int(p.read_text()) for p in Path('/sys/devices/system/cpu').glob('cpu*/thermal_throttle/*_throttle_count')}


def power():
    value=collect.environment()
    temperatures=[]
    for row in value['temperature_millic']:
        if row['value'] is not None:
            n=int(row['value']);require(-10000<=n<=150000,'Invalid thermal sensor');temperatures.append(n/1000)
    require(value['ac_online'],'AC required')
    require(value['no_turbo']=='1' if value['no_turbo'] is not None else value['boost']=='0','Turbo OFF must be verified via no_turbo=1 or boost=0')
    require(value['governors'] and set(value['governors'].values())=={'performance'},'Governor performance required')
    require(value['power_profile']=='performance','Power Profile performance required')
    require(temperatures and max(temperatures)<85,'Thermal missing or >=85C')
    return dict(ac=True,no_turbo=value['no_turbo'],boost=value['boost'],governors=value['governors'],power_profile=value['power_profile'],temperatures_c=temperatures,throttle_counts=throttle_counts())


def power_binding(value):
    return {k:v for k,v in value.items() if k not in ['temperatures_c','throttle_counts']}


def argv_contract(setup,config):
    previous=sys.modules.get('topology');sys.modules['topology']=collect.portable
    try:sys_config=module('universal_portable_arguments',HERE.parent/'new_a_portable/config.py')
    finally:
        if previous is None:sys.modules.pop('topology',None)
        else:sys.modules['topology']=previous
    argv,env=sys_config.server_config(config,setup['server_argv'],setup['server_env'])
    require(set(env)<=ENV_KEYS,'Unreviewed server environment key')
    require(env.get('PATH')=='/usr/bin:/bin' and env.get('LC_ALL')=='C','Fixed PATH/locale required')
    def arg(name):
        require(argv.count(name)==1 and argv.index(name)+1<len(argv),'Missing/duplicate '+name)
        return argv[argv.index(name)+1]
    require(arg('--host')=='127.0.0.1','Loopback-only server required')
    require(1024<=int(arg('--port'))<=65535,'Invalid server port')
    require(arg('--n-gpu-layers')=='0','GPU offload must be zero')
    require(Path(argv[0]).is_absolute() and Path(arg('-m')).is_absolute(),'Absolute Frozen/model paths required')
    env.update(OMP_DISPLAY_AFFINITY='FALSE',OMP_AFFINITY_FORMAT=OMP_FORMAT)
    return argv,env


def interpreter(path):
    text=subprocess.check_output(['readelf','-l',str(path)],text=True,timeout=15)
    match=re.findall(r'Requesting program interpreter: ([^\]]+)\]',text)
    require(len(match)==1 and Path(match[0]).is_file(),'ELF interpreter not uniquely identified')
    return str(Path(match[0]).resolve())


def resolve(path,loader,env):
    result=subprocess.run([loader,'--list',str(path)],env=env,capture_output=True,text=True,timeout=15)
    require(result.returncode==0 and 'not found' not in result.stdout+result.stderr,'ELF dependency resolution failed')
    files={str(Path(path).resolve()),loader}
    for line in result.stdout.splitlines():
        match=re.search(r'(?:=>\s*)?(/[^\s]+)\s+\(',line)
        if match:files.add(str(Path(match[1]).resolve()))
    require(all(Path(p).is_file() for p in files),'Missing ELF dependency')
    return sorted(files)


def record(path):
    path=Path(path).resolve();st=path.stat()
    return dict(sha256=sha(path),inode=st.st_ino,device=st.st_dev,size=st.st_size)


def build_check(report):
    value=read(report)
    require(value.get('runtime')=='UNIVERSAL_RESEARCH' and value['status']=='PASS' and value['ops_flags']==8,'Universal FULL build/Partial flag proof required')
    host=host_identity();require(value['kernel']==host['kernel'] and value['btf_sha256']==host['btf_sha256'],'Build kernel/BTF changed')
    require(value['kfunc_signatures']['status']=='PASS','kfunc static signature failure')
    for item in [value['source'],value['full_object'],value['loader'],value['child'],value['cpu_probe'],*value['helpers'].values()]:require(sha(item['path'])==item['sha256'],'Build artifact changed')
    for path,expected in value['source_files'].items():require(sha(path)==expected,'Build source changed: '+path)
    return value


def prepare(output,report,config_path,setup_path,request_path,allow_shared=False):
    require(os.getuid()!=0 and os.sched_getscheduler(0)==0,'Normal-user controller required')
    output=Path(output).resolve();require(not output.exists(),'New campaign directory required')
    space(output.parent)
    current_power=power();require(scheduler_state()=='disabled','sched_ext must be disabled')
    build=build_check(report);config=read(config_path)
    detection=collect.inspect(build['cpu_probe']['path'],config['requested_cpus']);guard=domain(detection,config,allow_shared)
    setup=read(setup_path);argv,env=argv_contract(setup,config)
    model=argv[argv.index('-m')+1]
    for key,path in [('expected_server_sha256',argv[0]),('expected_model_sha256',model)]:
        require(re.fullmatch('[0-9a-f]{64}',setup[key]) is not None and sha(path)==setup[key],'Explicit expected hash mismatch: '+key)
    marker=Path(setup['marker_elf']).resolve();require(marker.is_file(),'Phase marker ELF required')
    symbols=subprocess.check_output(['nm','-D','--defined-only',str(marker)],text=True,timeout=15)
    require(re.search(r'\bllama_scx_phase_v1$',symbols,re.M),'Frozen marker symbol missing; do not rebuild Frozen silently')
    request=read(request_path);require(request.get('n_predict')==128 and request.get('cache_prompt')is False and request.get('stream')is False and isinstance(request.get('prompt'),str),'Fixed 128-token nonstream uncached request required')
    loader=interpreter(argv[0]);roots=sorted({str(Path(p).resolve()) for p in [argv[0],str(marker),*setup['elf_roots']]})
    resolutions={p:resolve(p,loader,env) for p in roots};server_files=sorted({p for rows in resolutions.values() for p in rows})
    all_paths=set(server_files)|{str(Path(model).resolve())}
    for item in [build['loader'],build['child'],*build['helpers'].values()]:all_paths.update(resolve(item['path'],interpreter(item['path']),dict(PATH='/usr/bin:/bin',LC_ALL='C')))
    inputs={str(Path(p).resolve()):sha(p) for p in [report,config_path,setup_path,request_path]}
    for folder in [HERE,HERE.parent/'new_a_hybrid',HERE.parent/'new_a_portable',HERE.parent/'new_a',HERE.parent/'support',HERE.parent/'new_a_portable_runtime']:
        for p in folder.iterdir():
            if p.suffix in ['.py','.h','.c'] or p.name=='new-a-topology-config':inputs[str(p)]=sha(p)
    for name in ['source','full_object','loader','child','cpu_probe']:
        item=build[name];inputs[item['path']]=item['sha256']
    for item in build['helpers'].values():inputs[item['path']]=item['sha256']
    for p in [HERE.parent/'llama_scx.c',HERE.parent/'llama_scx_child.c',HERE.parent/'include/llama_scx_state.h',Path('/etc/ld.so.cache')]:inputs[str(p)]=sha(p)
    value=dict(schema=1,id='NEW_A_UNIVERSAL-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8],
        status='RUNTIME_STATIC_READY',uid=os.getuid(),host=dict(**host_identity(),cpu_model=config['cpu_model'],detection=detection,power_binding=power_binding(current_power),initial_thermal=current_power),
        domain=guard,configuration=config,build_report=str(Path(report).resolve()),build=build,
        baseline=dict(id='UNIVERSAL_RUNTIME_BASELINE-'+uuid.uuid4().hex,automatic_refresh=False,files={p:record(p) for p in sorted(all_paths)},resolutions=resolutions,server_files=server_files,model=str(Path(model).resolve()),required_server_files=sorted(set(roots)),aliases={str(p):str(Path(p).resolve()) for p in [argv[0],model,setup['marker_elf'],*setup['elf_roots']]},
            server_argv=argv,server_env=env,marker=str(marker),frozen_historical_identity='USER_PINNED_HASHES_NOT_AUTOMATICALLY_I5_EQUIVALENT'),
        request=request,request_sha256=digest(request),inputs=inputs,source_root=str(ROOT),
        functional='RUNTIME_FUNCTIONAL_NOT_RUN',performance='PERFORMANCE_NOT_TESTED',team_cpu='TEAM_CPU_RUNTIME_NOT_TESTED',amd_runtime='AMD_RUNTIME_NOT_TESTED',**LABELS)
    require(host_identity()=={k:value['host'][k] for k in host_identity()},'Host changed during prepare')
    require(detection==collect.inspect(build['cpu_probe']['path'],config['requested_cpus']),'Topology changed during prepare')
    require(power_binding(power())==value['host']['power_binding'],'Power changed during prepare')
    output.mkdir(mode=0o700)
    save(output/'campaign.private.json',value)
    save(output/'host-profile.private.json',value['host']);save(output/'runtime-baseline.private.json',value['baseline'])
    (output/'topology.bin').write_bytes(collect.portable.original.encode(detection['portable_plan']['policy']))
    (output/'topology.bin').chmod(0o400)
    save(output/'seal.json',dict(campaign_sha256=sha(output/'campaign.private.json'),host_sha256=sha(output/'host-profile.private.json'),baseline_sha256=sha(output/'runtime-baseline.private.json'),topology_sha256=sha(output/'topology.bin')))
    return value


def load_campaign(root,*,current=True):
    root=Path(root);seal=read(root/'seal.json')
    for name,key in [('campaign.private.json','campaign_sha256'),('host-profile.private.json','host_sha256'),('runtime-baseline.private.json','baseline_sha256'),('topology.bin','topology_sha256')]:require(sha(root/name)==seal[key],'Campaign seal changed: '+name)
    value=read(root/'campaign.private.json');require(all(value.get(k)==v for k,v in LABELS.items()),'Historical safety labels changed')
    for path,expected in value['inputs'].items():require(sha(path)==expected,'Input changed: '+path)
    for alias,target in value['baseline']['aliases'].items():require(str(Path(alias).resolve())==target,'Frozen/library/model path target changed')
    for path,expected in value['baseline']['files'].items():require(record(path)==expected,'Runtime dependency changed: '+path)
    if current:
        require(host_identity()=={k:value['host'][k] for k in host_identity()},'Kernel/Boot/BTF changed; independent new campaign required')
        build_check(value['build_report'])
        loader=interpreter(value['baseline']['server_argv'][0])
        for path,expected in value['baseline']['resolutions'].items():require(resolve(path,loader,value['baseline']['server_env'])==expected,'ELF dependency resolution changed')
        detection=collect.inspect(value['build']['cpu_probe']['path'],value['configuration']['requested_cpus'])
        require(detection==value['host']['detection'],'Topology/online/allowed/cpuset/quota changed')
        conditions(value)
        require(scheduler_state()=='disabled','sched_ext must be disabled before authentication')
    return value
