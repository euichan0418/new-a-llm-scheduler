#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Intel Hybrid inference auto-tuning; explicit execution, default-policy priority."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import planning as p
import backend as b
import experiment as x
import boundary_auth
RUN_ROOT=None


def parser():
    a=argparse.ArgumentParser(description=__doc__);sub=a.add_subparsers(dest='command',required=True)
    for cmd in ['plan','run','prepare-functional']:
        s=sub.add_parser(cmd);s.add_argument('--output',type=Path,required=True);s.add_argument('--probe',type=Path)
        s.add_argument('--build-report',type=Path);s.add_argument('--server',required=cmd!='plan');s.add_argument('--model',required=cmd!='plan')
        s.add_argument('--server-sha256');s.add_argument('--model-sha256');s.add_argument('--library-dir',action='append',default=[]);s.add_argument('--elf-root',action='append',default=[]);s.add_argument('--marker-elf');s.add_argument('--request',type=Path)
        s.add_argument('--port',type=int,default=8096);s.add_argument('--ctx-size',type=int,default=2048);s.add_argument('--batch-size',type=int,default=512);s.add_argument('--ubatch-size',type=int,default=256)
        s.add_argument('--scheduler',choices=['linux','new-a'],default='linux');s.add_argument('--functional-root',type=Path)
        s.add_argument('--min-http-gain-pct',type=float,default=2.0);s.add_argument('--max-phase-regression-pct',type=float,default=1.0);s.add_argument('--max-cpu-increase-pct',type=float,default=0.0)
        s.add_argument('--confirmation-blocks',type=int,choices=[6,12,18],default=6);s.add_argument('--finalists',type=int,choices=[2,3],default=3)
        s.add_argument('--max-sessions',type=int,default=160);s.add_argument('--max-seconds',type=int,default=7200);s.add_argument('--ack-shared-housekeeping',action='store_true')
        if cmd=='run':
            s.add_argument('--execute',action='store_true');s.add_argument('--acknowledge-existing-safety-fail',action='store_true');s.add_argument('--resume',action='store_true');s.add_argument('--resume-auth',action='store_true')
    s=sub.add_parser('analyze');s.add_argument('--output',type=Path,required=True)
    return a


def selection_rules(args):return p.rules({k:getattr(args,k)for k in p.DEFAULT_RULES})


def options(args):return {k:str(v)if isinstance(v,Path)else v for k,v in vars(args).items()if k not in ['output','execute','resume','resume_auth','acknowledge_existing_safety_fail']}


def analyze(root):
    tune=x.load(root);rows=[]
    for record in x.records(root):
        path=Path(record['raw'])
        if not(path/'completion.json').exists():continue
        require= b.require
        require(not(path/'failure.json').exists(),'Failed attempt cannot be counted')
        b.require(record['slot']['decision_rules_sha256']==p.fingerprint(tune['rules']),'Selection rules differ from measured raw contract')
        campaign=b.contracts.load_campaign(record['variant'],current=False);x.validate_record(record,campaign);metrics=b.verify(path,campaign,record['slot'])
        rows.append(dict(stage=record['stage'],candidate=record['candidate'],block=record['block'],position=record['position'],metrics=metrics,raw=record['raw']))
    complete=(Path(root)/'DONE.json').exists()
    return x.export(Path(root),tune,rows,complete)


def execute(args):
    global RUN_ROOT
    root=args.output.resolve();r=selection_rules(args);resumed=getattr(args,'resume',False)or getattr(args,'resume_auth',False)
    b.require(os.getuid()!=0 and os.sched_getscheduler(0)==0,'Normal user SCHED_OTHER controller required')
    if args.command=='run'and args.execute:b.require(args.acknowledge_existing_safety_fail and os.isatty(0),'Use a normal terminal and --acknowledge-existing-safety-fail; no sudo attempted')
    b.require(1<=args.max_sessions<=1024 and 60<=args.max_seconds<=21600,'Bounded approved sessions/time required')
    if resumed:
        tune=x.load(root);b.require(tune['options']==options(args),'Arguments/selection margins changed; new campaign required');RUN_ROOT=root;b.current(tune)
        if not args.execute or (root/'DONE.json').exists():return analyze(root)
    else:
        b.require(not root.exists(),'New output required; use explicit --resume for preserved campaign')
        ancestor=root.parent
        while not ancestor.exists():ancestor=ancestor.parent
        b.space(ancestor);root.mkdir(mode=0o700,parents=True);RUN_ROOT=root
        actual=args.command=='prepare-functional'or(args.command=='run'and args.execute)
        if actual:
            b.require(Path(args.server).is_file() and os.access(args.server,os.X_OK) and Path(args.model).is_file(),'Explicit existing server/model required before build; no fallback')
            if args.command=='prepare-functional' or args.scheduler=='new-a':b.require(args.marker_elf is not None and Path(args.marker_elf).is_file(),'Explicit existing --marker-elf required for NEW_A preparation/admission')
            b.contracts.power();b.require(b.contracts.scheduler_state()=='disabled','sched_ext must be disabled')
            build=args.build_report
            if build is None and args.scheduler=='new-a'and args.command=='run':
                b.require(args.functional_root is not None,'Candidate-specific functional root required')
                build=Path(b.read(args.functional_root/'prepared.private.json')['build_report'])
            if build is None:
                build=b.universal_build(root/'build')
            build=b.normalize_build(build,root/'normalized-static-report.json')
            info=b.contracts.build_check(build);probe=info['cpu_probe']['path']
            if args.probe:b.require(b.sha(args.probe)==info['cpu_probe']['sha256'],'Explicit probe differs from admitted build')
        else:
            probe=args.probe or b.probe_build(root/'probe')
        detected=b.collect.inspect(probe,None)
        proposed=dict(default=p.default(detected),prefill=p.prefill(detected),all_possible_configs=p.possible(detected),rules=r,
            initial_policy='P / (P+floor(E/2));_COMPACT;_SMT_EXCLUDED',performance='NOT_RUN',config_affinity_confound=True,
            selection='Observed winners are not policy recommendations; >=6 fresh independent confirmation blocks required')
        b.save(root/'plan.private.json',proposed)
        if not actual:
            print(json.dumps(proposed,indent=2));return
        tune=b.capture(args,root,detected,build);tune.update(plan_sha256=b.sha(root/'plan.private.json'),scheduler=args.scheduler,rules=r,limits=dict(sessions=args.max_sessions,seconds=args.max_seconds),options=options(args),functional_root=str(args.functional_root.resolve())if args.functional_root else None)
        x.seal(root,tune)
    (root/'variants').mkdir(exist_ok=True)
    if args.command=='prepare-functional':
        b.require(tune['base']['marker'],'--marker-elf with existing phase symbol required')
        symbols=b.contracts.subprocess.check_output(['nm','-D','--defined-only',tune['base']['marker']],text=True,timeout=15)
        b.require('llama_scx_phase_v1'in symbols,'Frozen marker symbol missing; do not rebuild Frozen')
        for config in p.possible(tune['host']['detection']):b.variant(tune,config,root/'variants'/config['id'])
        b.save(root/'prepared.private.json',dict(build_report=tune['build_report'],configurations=p.possible(tune['host']['detection']),functional='NOT_RUN'))
        print('PREPARED_NOT_EXECUTED '+str(root));return
    # NEW_A admission is checked for the entire possible adaptive range before authentication.
    if args.scheduler=='new-a':
        b.require(tune['functional_root']is not None,'No NEW_A functional evidence; blocked before authentication')
        for config in p.possible(tune['host']['detection']):b.admit_newa(Path(tune['functional_root'])/'variants'/config['id'],tune,config)
    b.current(tune)
    b.save(root/('consent-'+b.token()+'.json'),dict(scheduler=args.scheduler,approved_search_plan=b.sha(root/'plan.private.json'),rules=r,limits=tune['limits'],sudo_keepalive=False,automatic_retry=False,resume=resumed,foreground_boundary_user_reauthentication=True))
    auth=boundary_auth.BoundaryAuth(root,b.controller.quiet_boundary)
    auth.initial()
    b.runner.root_command(['true'],root/('post-auth-'+b.token()+'-command.json'))
    result=x.Experiment(root,tune,args.resume_auth,auth=auth).run()
    if not(root/'DONE.json').exists():b.save(root/'DONE.json',dict(status='COMPLETE',global_optimum=False,policy=result['reason']))
    return result


def main():
    args=parser().parse_args()
    if args.command=='analyze':return analyze(args.output.resolve())
    if args.command=='run'and getattr(args,'execute',False):
        lock=Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock');fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        b.require(os.fstat(fd).st_uid==os.getuid(),'Foreign runtime lock')
        handlers={}
        def stop(signum,frame):
            for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:signal.signal(sig,signal.SIG_IGN)
            raise KeyboardInterrupt('Signal '+str(signum)+'; cleanup required')
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            handlers={sig:signal.signal(sig,stop)for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]}
            return execute(args)
        finally:
            for sig,handler in handlers.items():signal.signal(sig,handler)
            os.close(fd)
    return execute(args)


if __name__=='__main__':
    try:
        result=main()
        if result is not None:print(json.dumps(result,indent=2,allow_nan=False))
    except (ValueError,OSError,KeyError,TypeError,KeyboardInterrupt)as error:
        if RUN_ROOT is not None and RUN_ROOT.is_dir():b.save(RUN_ROOT/('stopped-'+b.token()+'.json'),dict(status='STOPPED',error=type(error).__name__+': '+str(error),automatic_retry=False))
        print('STOPPED: '+type(error).__name__+': '+str(error)+'; raw retained, no automatic retry',file=sys.stderr);raise SystemExit(2)
