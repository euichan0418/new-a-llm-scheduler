#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""One-time Guard functional admission or paired FULL/Guard acquisition. Default: no execution."""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import traceback
import uuid

import host_checks as h
import environment
import fg_analyze as analyze
import fg_gate as gate
import fg_validation as v
from fg_config import LABELS,ORDERS,PLAN,SERVER_CPUS,HOUSE_CPUS

PREP,REPO=h.PREP,h.REPO


def preflight(*,strict_gate=False):
    setup,prepared=h.read('setup.json'),h.read('prepared.json')
    v.require(os.getuid()==setup['uid']==REPO.stat().st_uid!=0 and os.sched_getscheduler(0)==0,'Use normal repository owner and Linux client scheduling')
    for name,digest in prepared['files'].items():v.require(Path(name).is_file() and h.common.sha(name)==digest,'Sealed input changed: '+name)
    for name,target in prepared['symlinks'].items():v.require(Path(name).is_symlink() and os.readlink(name)==target,'Sealed symlink changed: '+name)
    v.require(h.common.command(['git','rev-parse','HEAD'])==setup['head'] and h.common.command(['git','branch','--show-current'])=='feature/new-a-scheduler','Repository identity changed')
    v.require(setup['mode'] in ['FUNCTIONAL','COMPARISON'] and setup['round_orders']==[list(x) for x in ORDERS],'Plan changed')
    profile=h.common.load_profile(PREP/'host-profile.exploratory.json');state=h.check_host(profile,initial=True)
    stored=h.read('topology.json');types={int(k):x for k,x in stored['architectural_types'].items()}
    v.require(profile['topology']==stored['topology'] and h.topology.classify(profile['topology'],types)==stored['policy'] and h.topology.encode(stored['policy'])==(PREP/'topology.bin').read_bytes(),'Topology/tier encoding changed')
    v.require(h.common.SERVER_CPUS==SERVER_CPUS and h.common.HOUSE_CPUS==HOUSE_CPUS,'Fixed CPU allocation changed')
    v.require(profile['workload_constraints']==setup['workload_constraints'],'Workload profile changed')
    h.common.scheduler_check();base=Path(setup['base_run']);v.require(h.common.active_run()==base,'Frozen pointer changed')
    _,bundle=h.compare.validate_bundle(base)
    h.common.THREADS.update(compute=8,batch=8,http=2);h.step10.MODEL=h.common.MODEL
    argv,_=h.step10.server_argv(base,'C');inherited=h.common.bundle_env(bundle)
    env={k:inherited[k] for k in ['PATH','LD_LIBRARY_PATH','OMP_NUM_THREADS','OMP_DYNAMIC']};env['LC_ALL']='C'
    v.require(argv==setup['server_argv'] and env==setup['server_env'],'Frozen workload changed')
    v.require(hashlib.sha256(h.common.canonical(h.common.REQUEST)).hexdigest()==setup['request_sha256']==h.common.REQUEST_SHA,'Frozen request changed')
    v.require(h.common.read_json(setup['original_acceptance'])['status']=='FAILED' and h.common.read_json(setup['original_diagnosis'])['safety_verdict']=='FAIL','Historical safety verdict changed')
    static=h.read('static-compatibility.json')
    v.require(static['host']==profile['host'] and static['policy_identity']=='PASS' and static['signatures']['status']=='PASS','Static compatibility/identity stale')
    v.require(static['native_selection']['status']=='PASS' and static['full_matches_measured_executable'] is True,'Policy equivalence not established')
    v.require(gate.sha(PREP/'host-profile.exploratory.json')==setup['binding']['host_profile_sha256'],'Profile binding changed')
    for name,digest in setup['binding']['artifacts'].items():v.require(gate.sha(name)==digest,'Bound artifact changed: '+name)
    v.require(Path('/dev/kmsg').is_char_device(),'Required kernel log device absent; no weakened monitor fallback')
    h.quiescent()
    readiness='READY_FOR_USER_EXECUTION';admission=None
    if setup['mode']=='COMPARISON':
        source=Path(setup['required_functional_dir'])
        if strict_gate or (source/'functional-result.json').exists():
            record=gate.check(source,setup['binding'])
            admission=dict(status=record['status'],source=str(source),record_sha256=gate.sha(source/'functional-result.json'),attempt_id=record['attempt_id'],binding=record['binding'],is_safety_approval=False,live_smt=record['live_smt'])
        else:readiness='WAITING_FOR_A_FUNCTIONAL_COMPLETE'
    return profile,bundle,dict(**LABELS,technical_preflight='COMPLETED',readiness=readiness,mode=setup['mode'],execution='NOT_RUN',
        current_state=state,functional_admission=admission,plan=PLAN if setup['mode']=='COMPARISON' else ['tracking-probe','llm-functional'],
        planned_sessions=12 if setup['mode']=='COMPARISON' else 2,planned_llm_server_sessions=12 if setup['mode']=='COMPARISON' else 1,planned_synthetic_sessions=0 if setup['mode']=='COMPARISON' else 1,planned_inference_requests=36 if setup['mode']=='COMPARISON' else 3,
        synthetic_probe_workers=0 if setup['mode']=='COMPARISON' else 8,threads=dict(compute=8,batch=8,http=2),server_argv=argv,server_env=env,
        environment_policy=environment.POLICY,automatic_retry=False,is_safety_approval=False)


def authenticate(logs,name):
    print('Terminal sudo authentication for '+name+'; no automatic retry or keepalive.',flush=True)
    code=subprocess.run(['sudo','-v'],check=False).returncode
    h.save(logs/(name+'-sudo.json'),dict(returncode=code))
    v.require(code==0,'sudo authentication failed')


def stage(name,details):
    root=PREP/'stages';root.mkdir(exist_ok=True)
    index=len(list(root.glob('*.json')))+1
    h.save(root/(f'{index:03d}-'+name+'.json'),dict(**LABELS,stage=name,**details))


def attached(item,path):
    v.require(item.loader is not None and item.loader.poll() is None and item.process.poll() is None,'Owned loader/child exited')
    v.require(h.common.state()=='enabled' and Path('/sys/kernel/sched_ext/root/ops').read_text().strip()=='llama_new_a','Wrong scheduler attachment')
    v.require('FULL_GUARD_MONITOR_READY' in (path/'loader.log').read_text(),'Read-only live/kernel monitor not ready')
    h.save(path/'kernel-load-attach.json',dict(**LABELS,verifier='LOAD_SUCCEEDED',attach='OBSERVED',ops='llama_new_a',live_monitor='READY'))


def healthy(item):
    v.require(item.process.poll() is None and item.loader.poll() is None and h.common.state()=='enabled','Server/loader/scheduler exited')
    return h.step10.health()


def new_session(path,policy,bundle,*,argv=None,pass_fds=(),marker=None):
    setup=h.read('setup.json');h.session.HERE=PREP/'layout'/policy;h.session.CHILD=REPO/'tools/sched_ext/build/bin/llama_scx_child'
    return h.session.Session(path,argv or setup['server_argv'],SERVER_CPUS,HOUSE_CPUS,policy='E',topology=PREP/'topology.bin',
        diagnostics=True,env=setup['server_env'],pass_fds=pass_fds,marker=marker or bundle/'bin/libllama-server-impl.so')


def run_llm(path,policy,bundle,baseline,sample,result,*,functional=False):
    item=new_session(path,policy,bundle)
    with item:
        attached(item,path);h.session.wait_until(lambda:healthy(item),180,'Server health deadline exceeded')
        h.save(path/'policies-before.json',h.compare.policy_snapshot(item,bundle))
        payload=h.common.canonical(h.common.REQUEST)
        for role in ['warmup-1','warmup-2']:
            healthy(item);h.step10.post_request(path,role,payload,920);result['requests_completed'].append(role)
        environment.record_window(path,'environment-after-warmup.json',baseline)
        h.save(path/'policies-pre-measured.json',h.compare.policy_snapshot(item,bundle))
        before_env=environment.boundary(path,'environment-pre-measured.json',baseline)
        healthy(item);throttle_before=h.observations.throttle_counts();before=h.compare.process_counters(item.server['pid'])
        h.save(path/'counters-before.json',before);h.save(path/'power-before-measured.json',h.turbo_guard.check())
        busy_before=h.machine.counters();role='functional' if functional else 'measured'
        _,data,client=h.step10.post_request(path,role,payload,920);result['requests_completed'].append(role)
        busy_after=h.machine.counters();h.save(path/'power-after-measured.json',h.turbo_guard.check())
        after=h.compare.process_counters(item.server['pid']);throttle_after=h.observations.throttle_counts();h.save(path/'counters-after.json',after)
        after_env=environment.boundary(path,'environment-post-measured.json',baseline)
        conditions=environment.difference(before_env,after_env,h.machine.busy(busy_before,busy_after));delta=h.observations.throttle_delta(throttle_before,throttle_after)
        sample.update(v.request_metrics(before,after,data,client,conditions['measured_system_busy_pct'],delta));sample['environment']=conditions
        h.save(path/'measurement.json',dict(**LABELS,policy=policy,scope='FUNCTIONAL_ONLY_EXCLUDED_FROM_PERFORMANCE' if functional else 'ONE_MEASURED_REQUEST',sample=sample))
        v.require(delta['status']=='NO_INCREMENT_OBSERVED','Measured thermal throttling; stop without retry')
        h.save(path/'policies-after.json',h.compare.policy_snapshot(item,bundle))


def probe_snapshot(item):
    rows=[h.common.identity(int(p.name)) for p in Path(f"/proc/{item.server['pid']}/task").iterdir()]
    v.require(rows and all(r['uid']==os.getuid() and r['policy']==7 and set(r['affinity'])==SERVER_CPUS for r in rows),'Probe TID mask/policy mismatch')
    return rows


def run_probe(path,policy,bundle,baseline,sample,result):
    probe=Path(h.read('setup.json')['probe_path']);read_fd,write_fd=os.pipe()
    try:
        item=new_session(path,policy,bundle,argv=[str(probe),str(read_fd),'8','-1'],pass_fds=(read_fd,),marker=probe)
        with item:
            attached(item,path);item.natural=True
            h.save(path/'policies-before.json',probe_snapshot(item))
            os.close(read_fd);read_fd=-1;os.write(write_fd,b'1');os.close(write_fd);write_fd=-1
            snapshots=[]
            def workers_ready():
                v.require(item.process.poll() is None and item.loader.poll() is None,'Probe exited before eight-worker observation')
                rows=probe_snapshot(item)
                if len(rows)==9:snapshots.extend(rows);return True
                return False
            h.session.wait_until(workers_ready,5,'Eight-worker probe readiness deadline exceeded')
            h.save(path/'policies-workers.json',snapshots)
            v.require(item.process.wait(timeout=20)==0,'Finite tracking probe failed/hung')
        output=(path/'server.stdout.log').read_text()
        v.require(output.count('worker iterations=160 violations=0')==8 and 'affinity_trace ' not in output,'Fixed-mask probe failed or affinity transition occurred')
        result['probe_output_verified']=True
    finally:
        for fd in [read_fd,write_fd]:
            if fd>=0:os.close(fd)


def run_visit(visit,profile,bundle,*,functional=False,probe=False):
    path=PREP/visit['relative_dir'];path.mkdir(parents=True,exist_ok=False)
    h.save(path/'attempt.json',dict(**LABELS,**visit,automatic_retry=False))
    h.save(path/'record-type.json',dict(**LABELS,purpose='FUNCTIONAL_ONLY' if functional else 'PERFORMANCE_DIAGNOSTIC_COMPARISON'))
    sample={};result=dict(**LABELS,**visit,visit_status='FAILED',valid_for_comparison=False,errors=[],sample=sample,requests_completed=[])
    original=os.sched_getaffinity(0);thermal_start=h.observations.throttle_counts()
    try:
        os.sched_setaffinity(0,HOUSE_CPUS)
        with h.Monitor(profile,path/'power-thermal-monitor.jsonl'):
            baseline=h.machine.snapshot();h.machine.validate(baseline);h.save(path/'machine-baseline.json',baseline)
            environment.record_window(path,'environment-before-server.json',baseline)
            h.quiescent();h.session.check_privileges()
            if probe:run_probe(path,visit['policy'],bundle,baseline,sample,result)
            else:run_llm(path,visit['policy'],bundle,baseline,sample,result,functional=functional)
            h.cleanup_verified(path)
            result['diagnostic_validation']=v.diagnostics(h.common.read_json(path/'scheduler-stats.json'),visit['policy'],probe=probe)
            h.save(path/'diagnostic-validation.json',result['diagnostic_validation'])
            h.check_host(profile)
    except BaseException as error:
        result['errors'].append(type(error).__name__+': '+str(error))
        with (path/'failure.log').open('x') as output:traceback.print_exc(file=output)
    finally:
        try:
            delta=h.observations.throttle_delta(thermal_start,h.observations.throttle_counts());result['thermal_throttle_whole_visit']=delta
            v.require(delta['status']=='NO_INCREMENT_OBSERVED','Whole-visit thermal throttling or missing evidence')
            h.cleanup_verified(path);h.quiescent()
        except BaseException as error:result['errors'].append('postflight: '+type(error).__name__+': '+str(error))
        try:
            result['sched_ext_after']=h.common.state();v.require(result['sched_ext_after']=='disabled','Scheduler did not detach')
        except BaseException as error:result['errors'].append('state: '+str(error))
        try:os.sched_setaffinity(0,original)
        except BaseException as error:result['errors'].append('restore: '+str(error))
        result['configuration_guard']=h.common.read_json(path/'guard-result.json') if (path/'guard-result.json').exists() else dict(status='FAILED',errors=['Monitor did not finish'])
        if result['configuration_guard']['status']!='COMPLETED' or result['configuration_guard']['errors']:result['errors'].append('Power/thermal guard incomplete')
        expected=[] if probe else ['warmup-1','warmup-2','functional' if functional else 'measured']
        good=(not result['errors'] and result['requests_completed']==expected and result.get('diagnostic_validation') is not None
              and (result.get('probe_output_verified') is True if probe else sample.get('core_metrics_complete') is True))
        if good:result.update(visit_status='COMPLETE',valid_for_comparison=not functional)
        h.save(path/'result.json',result)
    v.require(result['visit_status']=='COMPLETE','Visit failed; no next policy/round: '+visit['visit_id']+'; '+'; '.join(result['errors']))
    v.complete_visit(path,visit,functional=functional)
    return result


def terminal(execute,logs):
    setup=h.read('setup.json');mode=setup['mode'];owns=False;attempt_id=uuid.uuid4().hex
    outcome=dict(**LABELS,status='FAILED',errors=[],logs=str(logs),mode=mode,is_safety_approval=False)
    handlers={sig:signal.signal(sig,h.interrupted) for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]}
    try:
        h.require_fresh()
        if execute:
            h.save(PREP/'terminal-attempt.json',dict(**LABELS,mode=mode,attempt_id=attempt_id,logs=str(logs),automatic_retry=False));owns=True
        profile,bundle,technical=preflight(strict_gate=execute)
        h.save(logs/'preflight.json',technical)
        if not execute:outcome['status']='PREFLIGHT_COMPLETED_NO_EXECUTION';outcome['readiness']=technical['readiness']
        else:
            if mode=='COMPARISON':
                v.require(technical.get('functional_admission',{}).get('status')=='FUNCTIONAL_COMPLETE','A admission evidence absent')
                h.save(PREP/'functional-gate-snapshot.json',technical['functional_admission'])
            stage('technical-preflight',dict(status='COMPLETE',readiness=technical['readiness']))
            visits=[dict(kind='tracking-probe',policy='GUARD',visit_id='tracking-probe',relative_dir='visits/tracking-probe'),
                    dict(kind='llm-functional',policy='GUARD',visit_id='llm-functional',relative_dir='visits/llm-functional')] if mode=='FUNCTIONAL' else PLAN
            for visit in visits:
                profile,bundle,_=preflight(strict_gate=True);authenticate(logs,visit['visit_id']);profile,bundle,_=preflight(strict_gate=True)
                stage('visit-start',dict(visit=visit))
                run_visit(visit,profile,bundle,functional=mode=='FUNCTIONAL',probe=visit.get('kind')=='tracking-probe')
                h.cleanup_verified(PREP/visit['relative_dir']);h.quiescent()
                stage('visit-complete',dict(visit=visit,status='COMPLETE',cleanup='PASS',sched_ext='disabled'))
                if mode=='COMPARISON':h.save(logs/('progress-after-'+visit['visit_id']+'.json'),analyze.summarize(PREP))
                print(visit['visit_id']+': COMPLETE; cleanup=PASS; sched_ext=disabled',flush=True)
            if mode=='COMPARISON':v.require(analyze.summarize(PREP)['complete'],'Six complete paired rounds required')
            profile,bundle,_=preflight(strict_gate=True);h.quiescent()
            outcome['status']='FUNCTIONAL_COMPLETE' if mode=='FUNCTIONAL' else 'COMPLETE'
    except BaseException as error:
        outcome['errors'].append(type(error).__name__+': '+str(error));print('STOP: '+outcome['errors'][-1],file=sys.stderr,flush=True)
        with (logs/'failure.log').open('x') as output:traceback.print_exc(file=output)
    finally:
        try:
            outcome['sched_ext_after']=h.common.state()
            v.require(outcome['sched_ext_after']=='disabled','Final sched_ext is not disabled')
        except BaseException as error:outcome['errors'].append('final: '+str(error));outcome['status']='FAILED'
        if outcome['errors']:outcome['status']='FAILED'
        if owns and outcome['errors'] and not (PREP/'STOPPED.json').exists():h.save(PREP/'STOPPED.json',outcome)
        summary=analyze.summarize(PREP) if mode=='COMPARISON' else dict(**LABELS,scope='FUNCTIONAL_ONLY_NO_PERFORMANCE_SAMPLES',performance_samples=0)
        if mode=='FUNCTIONAL':
            cases={}
            for case in ['tracking-probe','llm-functional']:
                file=PREP/'visits'/case/'result.json'
                cases[case]=h.common.read_json(file) if file.exists() else dict(visit_status='NOT_RUN',requests_completed=[])
            summary['cases']=cases;summary['inference_requests_completed']=sum(len(x['requests_completed']) for x in cases.values())
        summary['terminal']=outcome;h.save(logs/'terminal-result.json',outcome)
        if owns:h.save(PREP/'summary.json',summary)
        if owns and mode=='FUNCTIONAL' and not outcome['errors'] and outcome['status']=='FUNCTIONAL_COMPLETE':
            try:gate.commit(PREP,setup['binding'],attempt_id)
            except BaseException as error:
                outcome['errors'].append('functional gate commit: '+str(error))
                h.save(PREP/'STOPPED.json',dict(**LABELS,status='FAILED',errors=outcome['errors']));print('STOP: functional gate was not admitted: '+str(error),flush=True)
        for sig,handler in handlers.items():signal.signal(sig,handler)
        if mode=='COMPARISON':analyze.print_summary(summary)
        print('STATUS='+('FAILED' if outcome['errors'] else outcome['status'])+'; sched_ext='+outcome.get('sched_ext_after','UNKNOWN')+'; LOGS='+str(logs),flush=True)
    return 1 if outcome['errors'] else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');p.add_argument('--exploratory-only',action='store_true');p.add_argument('--acknowledge-existing-safety-fail',action='store_true');args=p.parse_args()
    if args.execute and not (args.exploratory_only and args.acknowledge_existing_safety_fail):p.error('Execution requires exploratory-only and existing-safety-fail acknowledgement')
    v.require(os.getuid()==REPO.stat().st_uid!=0,'Use normal repository owner')
    with (PREP/'.terminal.lock').open('a') as local,(REPO/'benchmark-results/.new-a-v2-functional.lock').open('a') as shared:
        fcntl.flock(local,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(shared,fcntl.LOCK_EX|fcntl.LOCK_NB)
        logs=PREP/'terminal-logs'/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);logs.mkdir(parents=True,exist_ok=False)
        with (logs/'terminal.log').open('x') as output,contextlib.redirect_stdout(h.Tee(sys.stdout,output)),contextlib.redirect_stderr(h.Tee(sys.stderr,output)):
            return terminal(args.execute,logs)


if __name__=='__main__':raise SystemExit(main())
