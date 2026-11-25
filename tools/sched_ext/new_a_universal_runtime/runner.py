# SPDX-License-Identifier: GPL-2.0
"""Explicit, bounded research sessions. Root is used only for sealed helpers."""
import concurrent.futures
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import threading
import time
import uuid
from runtime_core import HERE, LABELS, collect, read, save, sha, digest, require, space, guard_bytes, ticks, module
import contracts
import evidence
import process_cpu


class AuthRequired(ValueError):pass


def normal_identity(pid):
    status=Path(f'/proc/{pid}/status').read_text();uids=re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',status,re.M)
    require(uids is not None and len(set(map(int,uids.groups())))==1,'Task UID state changed')
    return dict(pid=pid,starttime=ticks(pid),uid=int(uids[1]),policy=os.sched_getscheduler(pid),affinity=sorted(os.sched_getaffinity(pid)))


def tasks(pid,expected):
    result=[]
    for path in sorted(Path(f'/proc/{pid}/task').glob('[0-9]*')):
        try:
            row=normal_identity(int(path.name));require(row['uid']==expected['uid'] and row['policy']==expected['policy'] and row['affinity']==expected['affinity'],'TID UID/policy/mask changed')
            result.append(row)
        except (FileNotFoundError,ProcessLookupError):continue
    require(result and len(result)<=4096,'No target TIDs or excessive TID count')
    return result


def metric_snapshot(pid):
    rows={}
    for p in Path(f'/proc/{pid}/task').glob('[0-9]*'):
        text=(p/'sched').read_text();status=(p/'status').read_text()
        def counter(key,raw):
            match=re.search(r'^\s*'+re.escape(key)+r'\s*:\s*(\d+)\s*$',raw,re.M);require(match is not None,'Missing counter '+key);return int(match[1])
        rows[p.name]=dict(starttime=ticks(int(p.name)),migrations=counter('se.nr_migrations',text),context_switches=counter('voluntary_ctxt_switches',status)+counter('nonvoluntary_ctxt_switches',status),runtime_ns=int((p/'schedstat').read_text().split()[0]))
    cpus={}
    for line in Path('/proc/stat').read_text().splitlines():
        fields=line.split()
        if re.fullmatch(r'cpu\d+',fields[0]):
            values=list(map(int,fields[1:9]));cpus[fields[0]]=dict(total=sum(values),idle=values[3]+values[4])
    return dict(monotonic_ns=time.monotonic_ns(),tids=rows,cpus=cpus)


def root_command(argv,path):
    result=subprocess.run(['/usr/bin/sudo','-N','-n',*map(str,argv)],capture_output=True,text=True,timeout=20,env=dict(os.environ,LC_ALL='C'))
    save(path,dict(argv=['sudo','-N','-n',*map(str,argv)],returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
    if result.returncode and 'sudo:' in result.stderr and 'a password is required' in result.stderr:raise AuthRequired('SUDO_AUTH_REQUIRED_OR_EXPIRED; actual operation NOT_VERIFIED')
    require(result.returncode==0,'Privileged command failed: '+result.stderr)
    return result.stdout


def root_process(argv,**kwargs):
    return subprocess.Popen(['/usr/bin/sudo','-N','-n','/usr/bin/timeout','--signal=TERM','--kill-after=10s','360s',*map(str,argv)],**kwargs)


def taskset(cpus):return ['/usr/bin/taskset','-c',','.join(map(str,cpus))]


def mapping(pid,campaign):
    raw=Path(f'/proc/{pid}/maps').read_text();files={}
    for line in raw.splitlines():
        parts=line.split(None,5)
        if len(parts)<6 or 'x' not in parts[1] or not parts[5].startswith('/'):continue
        name=str(Path(parts[5]).resolve());require(name in campaign['baseline']['server_files'],'Unreviewed executable mapping: '+name)
        expected=campaign['baseline']['files'][name];dev=os.makedev(*[int(x,16) for x in parts[3].split(':')])
        require(int(parts[4])==expected['inode'] and dev==expected['device'] and contracts.record(name)==expected,'Mapped library differs')
        files[name]=expected
    require(set(campaign['baseline']['required_server_files'])<=set(files),'Required Frozen/marker/GGML mapping absent')
    return dict(raw_maps=raw,files=files,required_files_present=True)


def validate_live(pid,campaign,audit):
    argv=Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
    env=dict(part.decode().split('=',1) for part in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in part)
    expected=dict(campaign['baseline']['server_env'],OMP_DISPLAY_AFFINITY='TRUE' if audit else 'FALSE')
    require(argv==campaign['baseline']['server_argv'] and env==expected,'Actual Frozen argv/environment mismatch')
    return dict(argv=argv,environment=env)


def request(campaign,path,role,monitor,cpu_target=None):
    argv=campaign['baseline']['server_argv'];port=int(argv[argv.index('--port')+1])
    body=json.dumps(campaign['request'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    def perform():
        cpu={};counter=None;connection=None
        try:
            if cpu_target is not None:
                counter=process_cpu.ProcessCPUClock(cpu_target);counter.begin()
            begin=time.monotonic_ns()
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=120)
            connection.request('POST','/completion',body,{'Content-Type':'application/json'})
            response=connection.getresponse();data=response.read(4*1024*1024+1);require(len(data)<=4*1024*1024,'Oversized HTTP evidence')
            end=time.monotonic_ns()
            if counter is not None:cpu=counter.finish(begin,end)
            return response.status,data,begin,end,cpu
        except process_cpu.ProcessCPUError as error:
            save(path/(role+'-process-cpu-failure.json'),dict(status='FAILED_NOT_A_SAMPLE',target=cpu_target,error=str(error),partial_samples=counter.samples if counter else {},automatic_retry=False))
            raise
        finally:
            try:
                if connection is not None:connection.close()
            finally:
                if counter is not None:counter.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(perform)
        while not future.done():monitor();time.sleep(.25)
        status,data,begin,end,cpu=future.result()
    (path/(role+'-response.json')).write_bytes(data)
    save(path/(role+'-client.json'),dict(role=role,http_status=status,begin_ns=begin,end_ns=end,request_wall_ms=(end-begin)/1e6,request_sha256=campaign['request_sha256'],response_sha256=sha(path/(role+'-response.json'))))
    evidence.response(path,role,campaign['request_sha256'])
    if cpu_target is not None:save(path/'process-cpu.json',cpu)


def slots(stage):
    if stage=='FUNCTIONAL':return [dict(id=f'{p}-{kind}',policy=p,audit=kind=='audit',block=0,position=i+1) for i,p in enumerate(['C','E']) for kind in ['audit','clean']]
    require(stage=='PERFORMANCE','Unknown stage')
    return [dict(id=f'r{block:02d}-{p}',policy=p,audit=False,block=block,position=i) for block,order in enumerate([['C','E'],['E','C']],1) for i,p in enumerate(order,1)]


def acquire_session(campaign,root,path,slot):
    space(path.parent);path.mkdir(mode=0o700)
    state=lambda:Path('/sys/kernel/sched_ext/state').read_text().strip()
    require(state()=='disabled','Foreign sched_ext scheduler active')
    build=campaign['build'];selected=campaign['domain']['selected'];hk=campaign['domain']['housekeeping']
    logs=[];watch=loader=process=None;server=None;server_pidfd=None;cleanup_errors=[];failure=None;last_sample=0;attached=False
    start_throttle=contracts.throttle_counts();deadline=time.monotonic()+300
    socket='@new-a-universal-'+uuid.uuid4().hex
    def log(name):
        f=(path/name).open('xb');logs.append(f);return f
    def stats(when):
        import sys
        sys.path.insert(0,str(HERE.parent/'new_a_portable_runtime'));import stats_off
        if slot['policy']=='E':
            dump=json.loads(root_command(['bpftool','-j','struct_ops','dump'],path/('struct-ops-'+when+'-command.json')))
            if not(path/'struct-ops-dump.json').exists():save(path/'struct-ops-dump.json',dump)
            identity=evidence.loaded_identity((path/'loader.log').read_text(),dump,build['full_object']['sha256'])
            binding=stats_off.targets(dump);args=[str(n) for n in binding['ids'].values()]
            if not(path/'loaded-object-identity.json').exists():save(path/'loaded-object-identity.json',identity)
        else:binding=None;args=['--global']
        text=root_command(taskset(hk)+[build['helpers']['stats-off']['path'],*args],path/('stats-off-'+when+'-command.json'))
        stats_off.validate(json.loads(text),slot['policy'],binding)
        programs=json.loads(root_command(['bpftool','-j','prog','show'],path/('program-inventory-'+when+'-command.json')))
        own={p['id'] for p in identity['programs']} if slot['policy']=='E' else set()
        require(all(p['id'] in own or p.get('type') not in ['tracing','raw_tracepoint','raw_tracepoint_writable','tracepoint','kprobe'] for p in programs),'Unapproved external tracing program active')
    def monitor(force=False):
        nonlocal last_sample
        require(time.monotonic()<deadline,'Bounded session deadline exceeded')
        require(watch is not None and watch.poll() is None,'Kernel monitor exited')
        if loader is not None:require(loader.poll() is None and (not attached or state()=='enabled'),'Loader/sched_ext unexpectedly exited')
        else:require(state()=='disabled','Foreign sched_ext activated during Linux session')
        if process is not None:require(process.poll() is None,'Server/child launcher unexpectedly exited')
        if not force and time.monotonic()-last_sample<1:return
        current=contracts.power();require(contracts.power_binding(current)==campaign['host']['power_binding'],'Power binding changed')
        require(current['throttle_counts']==start_throttle,'Thermal throttle count increment')
        online=collect.portable.cpulist(Path('/sys/devices/system/cpu/online').read_text());allowed=sorted(os.sched_getaffinity(0))
        require(online==campaign['domain']['online'] and allowed==campaign['domain']['allowed'],'Online/allowed changed')
        require(collect.cgroup_constraints()==campaign['host']['detection']['hardware']['cgroup'],'Cgroup quota/cpuset changed')
        require(Path('/proc/sys/kernel/bpf_stats_enabled').read_text().strip()=='0','BPF statistics enabled')
        rows=tasks(server['pid'],server) if server else []
        if server:require(ticks(server['pid'])==server['starttime'],'Target PID reused')
        with (path/'monitor.jsonl').open('a') as f:f.write(json.dumps(dict(monotonic_ns=time.monotonic_ns(),power_binding=contracts.power_binding(current),max_temperature_c=max(current['temperatures_c']),throttle_unchanged=True,online=online,allowed=allowed,stats_sysctl=0,tasks=rows,uid=os.getuid()))+'\n')
        last_sample=time.monotonic()
    try:
        # This check precedes resources; it never refreshes credentials.
        root_command(['true'],path/'cached-auth-command.json')
        save(path/'slot.json',slot)
        watch=root_process(taskset(hk)+[build['helpers']['kernel-watch']['path']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log('kernel-watch.stderr.log'))
        ready,_,_=select.select([watch.stdout],[],[],8);require(ready and watch.stdout.readline()==b'READY\n','Kernel watch READY missing')
        if slot['policy']=='E':
            guard_path=path/'guard.bin';guard_path.write_bytes(guard_bytes(campaign['domain']));guard_path.chmod(0o400)
            argv=taskset(hk)+[build['loader']['path'],'--guard-config',str(guard_path),'--topology',str(root/'topology.bin'),'--diagnostics-output',str(path/'scheduler-stats.json'),'--phase-uid',str(os.getuid()),'--phase-register-socket',socket,'--server-exe',campaign['baseline']['server_argv'][0],'--marker-elf',campaign['baseline']['marker']]
            save(path/'loader-command.json',argv)
            loader=root_process(argv,stdin=subprocess.DEVNULL,stdout=log('loader.stdout.log'),stderr=log('loader.log'))
            until=time.monotonic()+20
            while state()!='enabled' or 'FULL_GUARD_MONITOR_READY' not in (path/'loader.log').read_text():
                require(loader.poll() is None and time.monotonic()<until,'Verifier/attach failed or timed out');monitor();time.sleep(.05)
            attached=True
        argv=list(campaign['baseline']['server_argv']);env=dict(campaign['baseline']['server_env'],OMP_DISPLAY_AFFINITY='TRUE' if slot['audit'] else 'FALSE')
        child_argv=[build['child']['path'],'--phase-register-socket',socket,'--',*argv] if loader else argv
        save(path/'server-command.json',dict(argv=child_argv,environment=env,affinity=selected))
        process=subprocess.Popen(child_argv,env=env,stdin=subprocess.DEVNULL,stdout=log('server.stdout.log'),stderr=log('server.stderr.log'),preexec_fn=lambda:os.sched_setaffinity(0,selected))
        until=time.monotonic()+30
        while server is None:
            require(process.poll() is None and time.monotonic()<until,'Frozen startup/registration failed')
            if loader:
                text=(path/'server.stdout.log').read_text();found=re.search(r'phase_uprobe_registered_tgid=(\d+)',text)
                if found:server=normal_identity(int(found[1]))
            else:server=normal_identity(process.pid)
            time.sleep(.05)
        require(server['uid']==os.getuid() and server['policy']==(7 if loader else 0) and server['affinity']==selected,'Selected target UID/policy/affinity differs')
        server_pidfd=os.pidfd_open(server['pid']);save(path/'server-identity.json',server)
        if loader:os.sched_setaffinity(process.pid,hk)
        port=int(argv[argv.index('--port')+1]);until=time.monotonic()+90
        while True:
            monitor();connection=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
            try:
                connection.request('GET','/health');reply=connection.getresponse();reply.read()
                if reply.status==200:break
            except (OSError,http.client.HTTPException):pass
            finally:connection.close()
            require(time.monotonic()<until,'Frozen HTTP health deadline exceeded');time.sleep(.1)
        save(path/'thread-contract.json',validate_live(server['pid'],campaign,slot['audit']))
        stats('attached')
        for role in ['warmup-1','warmup-2']:request(campaign,path,role,monitor)
        save(path/'mapping-proof.json',mapping(server['pid'],campaign));stats('before-measured')
        save(path/'metrics-before.json',metric_snapshot(server['pid']))
        request(campaign,path,'measured',monitor,cpu_target=server)
        save(path/'metrics-after.json',metric_snapshot(server['pid']))
        require(mapping(server['pid'],campaign)['files']==read(path/'mapping-proof.json')['files'],'Executable mappings changed during request')
        require(validate_live(server['pid'],campaign,slot['audit'])==read(path/'thread-contract.json'),'Thread contract changed')
        stats('after-measured');monitor(force=True)
    except BaseException as error:
        failure=error
    finally:
        # Stop normal-user targets first. The root loader follows the existing pidfd lifecycle.
        try:
            if process is not None:
                if server is None and loader is not None:
                    found=re.search(r'child_pid=(\d+)',(path/'server.stdout.log').read_text())
                    if found:
                        candidate=normal_identity(int(found[1]));require(candidate['uid']==os.getuid(),'Unknown selected child owner');server=candidate;server_pidfd=os.pidfd_open(candidate['pid'])
                if server_pidfd is not None:
                    try:signal.pidfd_send_signal(server_pidfd,signal.SIGTERM)
                    except ProcessLookupError:pass
                elif loader is None and process.poll() is None:process.terminate()
                try:code=process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if server_pidfd is not None:signal.pidfd_send_signal(server_pidfd,signal.SIGKILL)
                    process.kill();process.wait(timeout=5);raise ValueError('Server cleanup required SIGKILL')
                require(code==0 or loader is None and code==-signal.SIGTERM,'Unexpected server/launcher exit')
        except BaseException as error:cleanup_errors.append(str(error))
        if loader is not None:
            try:require(loader.wait(timeout=20)==0,'Root loader nonzero cleanup/verifier exit')
            except BaseException as error:
                cleanup_errors.append(str(error))
                # No credential assumption or unbounded root process on errors.
                try:loader.wait(timeout=375)
                except subprocess.TimeoutExpired:cleanup_errors.append('Root timeout completion unverified')
        if watch is not None:
            try:
                if watch.poll() is None:
                    try:watch.stdin.write(b'STOP\n');watch.stdin.flush()
                    except (BrokenPipeError,OSError):pass
                watch.stdin.close();require(watch.wait(timeout=12)==0,'Kernel watch nonzero/early exit')
                record=json.loads(watch.stdout.read());save(path/'kernel-watch-result.json',record)
                require(record==dict(status='PASS',kernel_opened=True,records=record['records'],bpf_loaded=False,fd_closed=True),'Kernel watch cleanup incomplete')
            except BaseException as error:
                cleanup_errors.append(str(error))
                try:watch.wait(timeout=375)
                except subprocess.TimeoutExpired:cleanup_errors.append('Kernel watch root timeout unconfirmed')
            finally:watch.stdout.close()
        if server_pidfd is not None:
            ready,_,_=select.select([server_pidfd],[],[],0)
            if not ready:cleanup_errors.append('Selected target pidfd is still alive')
            os.close(server_pidfd)
        for f in logs:f.close()
        if state()!='disabled':cleanup_errors.append('sched_ext not disabled')
        save(path/'cleanup.json',dict(status='FAIL' if cleanup_errors else 'PASS',errors=cleanup_errors,sched_ext=state(),server_exited=process is None or process.poll() is not None,loader_exited=loader is None or loader.poll() is not None))
    if failure or cleanup_errors:
        auth_error=isinstance(failure,AuthRequired) or any(p.read_text().strip()=='sudo: a password is required' for p in path.glob('*.log') if 'stderr' in p.name or p.name=='loader.log')
        kind='SUDO_AUTH_REQUIRED_OR_EXPIRED' if auth_error else 'PROCESS_CPU_CLOCK_FAILURE' if isinstance(failure,process_cpu.ProcessCPUError) and not cleanup_errors else 'SESSION_OR_CLEANUP_FAILURE'
        save(path/'failure.json',dict(kind=kind,error=str(failure),cleanup_errors=cleanup_errors,automatic_retry=False))
        raise ValueError('Session failed; no next session: '+str(failure or cleanup_errors))
    files={str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
    proof=dict(status='COMPLETE',campaign_id=campaign['id'],stage=slot['stage'],slot=slot,files=files,contract_binding=digest({k:campaign[k] for k in ['id','host','domain','baseline']}),**LABELS)
    try:value=evidence.verify_session(path,campaign,slot['stage'],slot,prospective=proof)
    except BaseException as error:
        save(path/'failure.json',dict(kind='RAW_VALIDATION_FAILURE',error=str(error),automatic_retry=False));raise
    save(path/'completion.json',proof)
    return value
