# SPDX-License-Identifier: GPL-2.0
"""Batch consent and fresh evidence admission; no keepalive or automatic retry."""
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time
import uuid
from runtime_core import read,save,require,space,sha
import contracts
import evidence
import runner
import boundary_auth


def token():return time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]


def completed(root,campaign,stage,resume_auth=False):
    found={};pending=False
    for slot in runner.slots(stage):
        slot=dict(slot,stage=stage);paths=sorted((Path(root)/'attempts'/stage/slot['id']).glob('*'));valid=[]
        for path in paths:
            if (path/'completion.json').exists():
                require(not(path/'failure.json').exists(),'Failed attempt cannot become complete')
                evidence.verify_session(path,campaign,stage,slot);valid.append(path)
        require(len(valid)<=1,'Duplicate completed sessions')
        if valid:
            require(not pending and valid[0]==paths[-1],'Completion order is not a prefix')
            found[slot['id']]=valid[0]
        else:
            pending=True
            if paths:
                path=paths[-1];failure=read(path/'failure.json');cleanup=read(path/'cleanup.json')
                require(resume_auth and safe_auth_resume(path,campaign,failure,cleanup),'Previous failure is not eligible for explicit auth resume: '+str(path))
    return found


def safe_auth_resume(path,campaign,failure,cleanup):
    if failure['kind']!='SUDO_AUTH_REQUIRED_OR_EXPIRED' or cleanup['status']!='PASS' or cleanup['errors']:return False
    auth=[]
    for p in Path(path).glob('*-command.json'):
        value=read(p)
        if isinstance(value,dict) and value.get('returncode',0)!=0 and 'sudo:' in value.get('stderr','') and 'a password is required' in value['stderr']:auth.append(p)
    if not auth:return False
    if not(Path(path)/'server-command.json').exists() and not(Path(path)/'loader-command.json').exists() and not(Path(path)/'kernel-watch.stderr.log').exists():return True
    if read(Path(path)/'kernel-watch-result.json')['status']!='PASS':return False
    if (Path(path)/'loader-command.json').exists():
        value=read(Path(path)/'scheduler-stats.json')
        for row in value['cpus']:
            if row['mask_violations'] or row['tracking_failures'] or row['events_dropped']:return False
            if row['cpu'] not in campaign['domain']['selected'] and (row['running'] or row['stopping'] or row['runtime_ns']):return False
        evidence.loaded_identity((Path(path)/'loader.log').read_text(),read(Path(path)/'struct-ops-dump.json'),campaign['build']['full_object']['sha256'])
    return True


def gate(root,stage,resume_auth=False):
    campaign=contracts.load_campaign(root,current=True)
    # Historical failures in every stage remain blocking unless the exact auth case is approved.
    all_done={mode:completed(root,campaign,mode,resume_auth) for mode in ['FUNCTIONAL','PERFORMANCE']}
    if stage=='PERFORMANCE':require(len(all_done['FUNCTIONAL'])==4,'All four fresh Audit/Clean proofs required before authentication')
    return campaign,all_done[stage]


def foreign_processes():
    found=[]
    for path in Path('/proc').glob('[0-9]*'):
        try:
            name=(path/'comm').read_text().strip()
            if name.startswith(('llama-server','llama_scx','llama_new_a','loader-E-full','kernel-watch','observer','wake_probe','route-probe','rapl')):found.append(int(path.name))
        except (FileNotFoundError,ProcessLookupError,PermissionError):pass
    require(not found,'Existing server/loader/kernel monitor processes require review: '+str(found))


def quiet_boundary():
    require(contracts.scheduler_state()=='disabled','AUTH_BOUNDARY_SCHED_EXT_ACTIVE; no password prompt')
    foreign_processes()


def execute(root,stage,ack,resume_auth=False):
    require(ack,'Explicit --acknowledge-existing-safety-fail required')
    require(os.getuid()!=0 and os.isatty(0),'Use a normal-user controlling terminal')
    root=Path(root);campaign,done=gate(root,stage,resume_auth);foreign_processes()
    visits=[dict(v,stage=stage) for v in runner.slots(stage) if v['id'] not in done]
    if not visits:return dict(status='ALREADY_COMPLETE',sessions=0)
    consent=root/('consent-'+token()+'.json');save(consent,dict(stage=stage,slots=visits,maximum_sessions=len(visits),maximum_requests=3*len(visits),deadline_seconds=1800,existing_safety_fail_ack=True,automatic_retry=False,credential_keepalive=False,foreground_boundary_user_reauthentication=True))
    auth=boundary_auth.BoundaryAuth(root,quiet_boundary)
    auth.initial()
    runner.root_command(['true'],root/('post-auth-'+token()+'-command.json'))
    deadline=time.monotonic()+1800
    for slot in visits:
        require(time.monotonic()<deadline,'Controller boundary deadline exceeded; preserved completed sessions')
        campaign=contracts.load_campaign(root,current=True);foreign_processes()
        auth.before_session(slot['id'],ready=lambda:require(time.monotonic()<deadline,'Controller boundary deadline exceeded while waiting for user authentication'),after_auth=lambda:contracts.load_campaign(root,current=True))
        folder=root/'attempts'/stage/slot['id'];folder.mkdir(parents=True,exist_ok=True)
        path=folder/token()
        print(stage+' '+slot['id']+' '+str(path),flush=True)
        runner.acquire_session(campaign,root,path,slot)
        evidence.verify_session(path,campaign,stage,slot)
        auth.completed(path,campaign,slot)
        print(slot['id']+': raw COMPLETE; cleanup PASS',flush=True)
    return analyze(root)


def analyze(root):
    root=Path(root);campaign=contracts.load_campaign(root,current=False);rows=[];functional={}
    for stage in ['FUNCTIONAL','PERFORMANCE']:
        for slot in runner.slots(stage):
            slot=dict(slot,stage=stage)
            for path in sorted((root/'attempts'/stage/slot['id']).glob('*')):
                if not(path/'completion.json').exists():continue
                value=evidence.verify_session(path,campaign,stage,slot)
                if stage=='FUNCTIONAL':functional[slot['id']]='AUDIT_PASS' if slot['audit'] else 'CLEAN_PASS'
                else:rows.append(dict(slot=slot,path=str(path),sample=value))
    require(len({r['slot']['id'] for r in rows})==len(rows),'Duplicate performance samples')
    metrics=['http_ms','process_cpu_s','prefill_tps','decode_tps','prefill_ms','decode_ms'];means={}
    for policy in ['C','E']:
        values=[r['sample'] for r in rows if r['slot']['policy']==policy]
        means[policy]={m:dict(n=len(values),mean=statistics.mean(x[m] for x in values) if values else None,sd=statistics.stdev(x[m] for x in values) if len(values)>1 else None) for m in metrics}
    paired=[]
    for block in [1,2]:
        pair={r['slot']['policy']:r for r in rows if r['slot']['block']==block}
        if set(pair)=={'C','E'}:paired.append(dict(block=block,difference={m:pair['E']['sample'][m]-pair['C']['sample'][m] for m in metrics},change_pct={m:100*(pair['E']['sample'][m]/pair['C']['sample'][m]-1) for m in metrics}))
    value=dict(functional=functional,functional_complete=len(functional)==4,performance_complete=len(rows)==4,rows=rows,statistics=means,paired_blocks=paired,primary='http_ms',superiority='NOT_ESTABLISHED_N2',energy='NOT_MEASURED_RAPL_OFF',historical_pooling=False,**{k:campaign[k] for k in ['strict_acceptance','safety_verdict','overhead_review']})
    folder=root/'analysis';folder.mkdir(exist_ok=True);save(folder/(token()+'.json'),value)
    return value
