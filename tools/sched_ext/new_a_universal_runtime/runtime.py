#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Universal research runtime: read-only preparation and explicit separate run."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from runtime_core import collect,profiles,read,save,require,sha,ROOT
import contracts


def main():
    p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
    for cmd in ['detect','recommend','validate-config']:
        q=commands.add_parser(cmd);q.add_argument('--probe',type=Path,required=True)
        if cmd=='validate-config':q.add_argument('--config',type=Path,required=True)
        if cmd=='recommend':q.add_argument('--placement',choices=['compact','spread','lowest'],default='compact')
    q=commands.add_parser('prepare');q.add_argument('--build-report',type=Path,required=True);q.add_argument('--config',type=Path,required=True);q.add_argument('--setup',type=Path,required=True);q.add_argument('--request',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--ack-shared-housekeeping',action='store_true')
    commands.add_parser('inspect-host')
    q=commands.add_parser('request-template');q.add_argument('--output',type=Path,required=True)
    for cmd in ['preflight','functional','performance','analyze','guard-verify']:
        q=commands.add_parser(cmd);q.add_argument('--campaign',type=Path,required=True)
        if cmd in ['functional','performance']:
            q.add_argument('--execute',action='store_true');q.add_argument('--acknowledge-existing-safety-fail',action='store_true');q.add_argument('--resume-auth',action='store_true')
        if cmd=='guard-verify':q.add_argument('--diagnostics',type=Path,required=True)
    a=p.parse_args()
    if a.command in ['detect','recommend','validate-config']:
        config=read(a.config) if a.command=='validate-config' else None
        detected=collect.inspect(a.probe,config['requested_cpus'] if config else None)
        return detected if a.command=='detect' else profiles.validate(config,detected) if config else profiles.recommend(detected,a.placement)
    if a.command=='inspect-host':return dict(identity=contracts.host_identity(),environment=collect.environment(),runtime_admission='NOT_GRANTED',private_output=True)
    if a.command=='request-template':
        from runtime_core import module,HERE
        common=module('universal_frozen_request',HERE.parent/'bench/common.py');save(a.output,common.REQUEST);return dict(status='REQUEST_TEMPLATE_WRITTEN',request_sha256=sha(a.output))
    if a.command=='prepare':return dict(status=contracts.prepare(a.output,a.build_report,a.config,a.setup,a.request,a.ack_shared_housekeeping)['status'],actual_functional='RUNTIME_FUNCTIONAL_NOT_RUN')
    if a.command=='guard-verify':
        import evidence
        campaign=contracts.load_campaign(a.campaign,current=False)
        return dict(status='FULL_DIAGNOSTIC_RAW_VALIDATED_ONLY',summary=evidence.diagnostics(read(a.diagnostics),campaign['domain']),runtime_admission=False)
    import controller
    if a.command=='analyze':return controller.analyze(a.campaign)
    stage='PERFORMANCE' if a.command=='performance' else 'FUNCTIONAL'
    if not getattr(a,'execute',False):
        campaign,done=controller.gate(a.campaign,stage,getattr(a,'resume_auth',False))
        return dict(status='HOST_PREFLIGHT_PASS',configuration='CONFIGURATION_VALID',build='RUNTIME_STATIC_READY',completed=len(done),sudo='NOT_RUN',attach='NOT_RUN',inference='NOT_RUN')
    # A user-wide lock is not a substitute for the foreign-process and kernel-state checks.
    name=Path('/tmp')/('new-a-universal-'+str(os.getuid())+'.lock')
    fd=os.open(name,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    require(os.fstat(fd).st_uid==os.getuid(),'Foreign lock owner')
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def stop(signum,frame):raise KeyboardInterrupt('Signal '+str(signum)+'; cleanup required before any next session')
        for sig in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:signal.signal(sig,stop)
        return controller.execute(a.campaign,stage,a.acknowledge_existing_safety_fail,a.resume_auth)
    finally:os.close(fd)


if __name__=='__main__':
    try:print(json.dumps(main(),indent=2,sort_keys=True,allow_nan=False))
    except (ValueError,OSError,KeyError,TypeError) as error:print('BLOCKED: '+str(error),file=sys.stderr);raise SystemExit(2)
