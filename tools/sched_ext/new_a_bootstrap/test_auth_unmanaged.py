# SPDX-License-Identifier: GPL-2.0
"""Short-lived credentials, unmanaged power and explicit resume, all synthetic."""
from contextlib import ExitStack,contextmanager
import copy
import json
import itertools
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent/'new_a_universal_runtime'),str(HERE.parent/'new_a_hybrid_autotune')]
import package_driver as driver,bootstrap,auto_system,resume_campaign
import boundary_auth as authmod,runner,contracts,controller,evidence,runtime_core as core
import backend as backend,experiment,planning,autotune
from test_autotune import detection
from test_raw import fixture,finish


class Sudo:
    """Timestamp namespaces and a two-second lifetime, without running sudo."""
    def __init__(self,root,ttl=2):
        self.now=1.;self.ttl=ttl;self.until={};self.pid=100;self.busy=False;self.calls=[];self.prompts=[];self.cancel=False;self.race=False
        self.auth=authmod.BoundaryAuth(root,self.quiet,lambda:self.now)
    def context(self):return dict(pid=self.pid,ppid=99,sid=10,tty=11,pgrp=12,foreground=12)
    def quiet(self):
        if self.busy:raise ValueError('RESOURCES_ACTIVE')
    def run(self,argv,**kw):
        self.calls.append(list(argv))
        if argv==['/usr/bin/sudo','-v']:
            self.quiet();self.prompts.append(self.now)
            if self.cancel:raise KeyboardInterrupt('SYNTHETIC_CANCEL')
            self.until[self.pid]=self.now+self.ttl
            return SimpleNamespace(returncode=0)
        if argv==['/usr/bin/sudo','-k']:
            self.quiet();self.until[self.pid]=0;return SimpleNamespace(returncode=0)
        if argv[:3]==['/usr/bin/sudo','-N','-n']:
            if self.race and argv[3:]!=['-v']:self.until[self.pid]=0;self.race=False
            ok=self.now<self.until.get(self.pid,0)
            return SimpleNamespace(returncode=0 if ok else 1,stdout='',stderr=''if ok else'sudo: a password is required')
        raise AssertionError('Unexpected process '+str(argv))
    @contextmanager
    def patched(self):
        with patch.object(authmod,'terminal_context',side_effect=self.context),patch.object(subprocess,'run',side_effect=self.run):yield self


class Credentials(unittest.TestCase):
    def test_python_install_sudo_does_not_use_a_pipeline_parent(self):
        script=(HERE.parents[2]/'scripts/autotune-bootstrap.sh').read_text()
        command=next(line for line in script.splitlines()if 'sudo -N -n /usr/bin/apt-get'in line)
        self.assertNotIn('|',command);self.assertIn('>> "$LOG_FILE"',command)
    def test_expiry_after_boundary_before_actual_operation_prompts(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp)
            with s.patched(),runner.auth_context(s.auth):
                s.auth.initial();s.auth.before_session('session');s.now+=3
                runner.root_command(['true'],Path(tmp)/'command.json')
            self.assertEqual(len(s.prompts),2)
            self.assertEqual(core.read(Path(tmp)/'command.json')['returncode'],0)
    def test_expiry_during_final_ready_reauthenticates_once(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp);ready=Mock(side_effect=[setattr(s,'now',4),None])
            def prepare():
                if ready.call_count==0:s.now+=3
                ready()
            with s.patched():s.auth.initial();s.auth.before_session('s',ready=prepare)
            self.assertEqual(len(s.prompts),2)
    def test_actual_command_race_reauthenticates_only_before_resources(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp)
            with s.patched(),runner.auth_context(s.auth):
                s.auth.initial();s.race=True;runner.root_command(['true'],Path(tmp)/'command.json')
            log=core.read(Path(tmp)/'command.json')
            self.assertEqual([x['returncode']for x in log['authentication_attempts']],[1,0]);self.assertEqual(len(s.prompts),2)
    def test_active_resource_expiry_never_prompts_or_retries(self):
        for phase in ['session-start','warmup','after-measured']:
            with self.subTest(phase=phase),tempfile.TemporaryDirectory()as tmp:
                s=Sudo(tmp)
                with s.patched(),runner.auth_context(s.auth):
                    s.auth.initial();runner.AUTH_EXECUTION.get()['active']=True;s.busy=True;s.now+=3
                    with self.assertRaisesRegex(authmod.CredentialExpired,'cleanup required'):runner.root_command(['true'],Path(tmp)/'command.json')
                self.assertEqual(len(s.prompts),1);self.assertFalse((Path(tmp)/'command.json').exists())
    def test_process_context_change_blocks_without_prompt(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp)
            with s.patched(),runner.auth_context(s.auth):
                s.auth.initial();s.pid+=1
                with self.assertRaisesRegex(ValueError,'CONTEXT_CHANGED'):runner.root_command(['true'],Path(tmp)/'command.json')
            self.assertEqual(len(s.prompts),1)
    def test_very_short_timeout_cannot_infinite_prompt(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp)
            with s.patched():
                s.auth.initial()
                with self.assertRaises(authmod.CredentialExpired):s.auth.before_session('s',ready=lambda:setattr(s,'now',s.now+3))
            self.assertEqual(len(s.prompts),2)
    def test_non_auth_command_failure_not_retried(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp);old=s.run
            def command(argv,**kw):
                if argv[-1]=='BAD':return SimpleNamespace(returncode=2,stdout='',stderr='tool: operation failed')
                return old(argv,**kw)
            with s.patched(),patch.object(subprocess,'run',side_effect=command),runner.auth_context(s.auth):
                s.auth.initial()
                with self.assertRaisesRegex(ValueError,'Privileged command failed'):runner.root_command(['BAD'],Path(tmp)/'command.json')
            self.assertEqual(len(s.prompts),1)
    def test_cancelled_reauthentication_does_not_start_command(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Sudo(tmp)
            with s.patched(),runner.auth_context(s.auth):
                s.auth.initial();s.now+=3;s.cancel=True
                with self.assertRaises(KeyboardInterrupt):runner.root_command(['true'],Path(tmp)/'command.json')
            self.assertFalse((Path(tmp)/'command.json').exists())


class Unmanaged(unittest.TestCase):
    def test_thermal_only_never_reads_power_policy(self):
        original=Path.read_text
        def read(path,*args,**kw):
            self.assertFalse(any(x in str(path)for x in ['cpufreq','intel_pstate','power_supply']))
            return original(path,*args,**kw)
        with patch.object(Path,'read_text',read),patch.object(contracts,'power',side_effect=AssertionError('power read')),patch.object(contracts.collect,'environment',side_effect=AssertionError('power read')):
            result=contracts.conditions(dict(power_management=contracts.UNMANAGED_POWER,host={}))
            self.assertTrue(result['temperatures_c'])
    def test_legacy_contract_stays_strict(self):
        with patch.object(contracts,'power',return_value={'policy':'old'}):
            with self.assertRaisesRegex(ValueError,'Power binding changed'):contracts.conditions(dict(host=dict(power_binding={'policy':'different'})))
    def test_raw_new_contract_keeps_guard_mask_identity_cleanup(self):
        for policy in ['C','E']:
            with self.subTest(policy=policy),tempfile.TemporaryDirectory()as tmp:
                path=Path(tmp)/'raw';campaign,slot=fixture(path,policy)
                campaign['power_management']=contracts.UNMANAGED_POWER;campaign['host'].pop('power_binding')
                import interference
                campaign['interference_contract']=dict(interference.DEFAULT)
                records=[json.loads(s)for s in (path/'monitor.jsonl').read_text().splitlines()]
                for r in records:r.pop('power_binding');r['power_management']=contracts.UNMANAGED_POWER;r['interference']=dict(status='SAMPLED',contaminated=False)
                (path/'monitor.jsonl').write_text('\n'.join(json.dumps(r)for r in records)+'\n');finish(path,campaign,slot)
                self.assertEqual(evidence.verify_session(path,campaign,'FUNCTIONAL',slot)['status'],'FUNCTIONAL_PASS')
                records[0]['tasks'][0]['affinity']=[999]
                (path/'monitor.jsonl').write_text('\n'.join(json.dumps(r)for r in records)+'\n');finish(path,campaign,slot)
                with self.assertRaisesRegex(ValueError,'mask changed'):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_new_bootstrap_finishes_without_power_journal_or_restore(self):
        for outcome in ['success','auth-failure','cancel']:
            with self.subTest(outcome=outcome),tempfile.TemporaryDirectory()as tmp,ExitStack()as stack:
                root=Path(tmp);old=root/'runs/old/environment.private.json';old.parent.mkdir(parents=True);old.write_text('{"status":"RESTORE_REQUIRED","attempted":[0]}');before=old.read_bytes()
                module=SimpleNamespace(b=SimpleNamespace(contracts=SimpleNamespace(scheduler_state=Mock(return_value='disabled'),thermal=Mock(return_value={'temperatures_c':[40]})),controller=SimpleNamespace(foreign_processes=Mock()),universal_build=Mock(return_value=Path('/SYNTHETIC_BUILD')),probe_build=Mock(return_value='/SYNTHETIC_PROBE'),collect=SimpleNamespace(inspect=Mock(return_value={'summary':'SYNTHETIC'}))),p=SimpleNamespace(default=Mock(return_value={'compute_threads':6,'batch_threads':10})))
                fake=SimpleNamespace(ROOT=driver.ROOT,engine=Mock(return_value=module),dependencies=Mock(return_value={}),admission_record=Mock(),verify=Mock(return_value={}))
                target=[]
                def action(mode,args,module):
                    target.append(Path(args[args.index('--output')+1]));return SimpleNamespace(output=target[-1]),['SYNTHETIC_NO_INFERENCE']
                fake.action=action
                def child(argv):
                    if outcome=='cancel':raise KeyboardInterrupt('SYNTHETIC_CANCEL')
                    if outcome=='auth-failure':return 2
                    target[-1].mkdir();core.save(target[-1]/'DONE.json',{'status':'COMPLETE'});return 0
                stack.enter_context(patch.object(bootstrap.os,'isatty',return_value=True))
                stack.enter_context(patch.object(bootstrap.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)))
                stack.enter_context(patch.object(bootstrap.system,'missing_packages',return_value=[]))
                stack.enter_context(patch.object(bootstrap.system,'install'))
                stack.enter_context(patch.object(bootstrap.system,'consent'))
                for name in ['snapshot','power_plan','preregister','apply','restore','pending_recovery','audit_restoration']:
                    stack.enter_context(patch.object(bootstrap.system,name,side_effect=AssertionError('Forbidden power dependency: '+name)))
                stack.enter_context(patch.object(bootstrap.provision,'source',return_value=Path('/SYNTHETIC_SOURCE')))
                stack.enter_context(patch.object(bootstrap.provision,'build',return_value=Path('/SYNTHETIC_BIN')))
                stack.enter_context(patch.object(bootstrap.provision,'model',return_value=Path('/SYNTHETIC_MODEL')))
                stack.enter_context(patch.object(bootstrap.provision,'sha',return_value='synthetic'))
                stack.enter_context(patch.object(bootstrap.provision,'seal_runtime',return_value={'status':'SYNTHETIC'}))
                stack.enter_context(patch.object(bootstrap.auto_run,'quiet',return_value=True))
                stack.enter_context(patch.object(bootstrap.auto_run,'child',side_effect=child))
                if outcome=='success':self.assertEqual(bootstrap.run(fake,root),0)
                else:
                    with self.assertRaises((ValueError,KeyboardInterrupt)):bootstrap.run(fake,root)
                self.assertEqual(before,old.read_bytes());self.assertEqual(list(root.glob('runs/bootstrap-*/environment.private.json')),[])
                state=core.read(next(root.glob('runs/bootstrap-*/bootstrap-result.private.json')))
                self.assertEqual(state['status'],'COMPLETE'if outcome=='success'else'BLOCKED_OR_STOPPED');self.assertNotIn('restore_error',state)


class Resume(unittest.TestCase):
    def test_resume_arguments_preserve_rules_and_explicit_flags(self):
        args=autotune.parser().parse_args(['run','--output','/SYNTHETIC','--server','/SERVER','--model','/MODEL','--library-dir','/LIB'])
        tune=dict(options=autotune.options(args));argv=resume_campaign.arguments(tune,Path('/SYNTHETIC'),True)
        parsed=autotune.parser().parse_args(['run',*argv])
        self.assertEqual(autotune.options(parsed),tune['options']);self.assertTrue(parsed.resume and parsed.resume_auth)
    def test_missing_team_campaign_is_blocked_without_auth_or_inference(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(subprocess,'run',side_effect=AssertionError('No operation permitted')):
            result=resume_campaign.audit(driver,Path(tmp)/'MISSING')
            self.assertEqual(result['decision'],'RESUME_BLOCKED');self.assertTrue(result['errors'])
    def test_legacy_contract_requires_new_campaign_without_reading_old_journal(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(experiment,'load',return_value=dict(inputs={})),patch.object(experiment,'records',return_value=[]):
            folder=Path(tmp);old=folder/'environment.private.json';old.write_text('FAILURE_PRESERVED')
            result=resume_campaign.audit(driver,folder)
            self.assertEqual(result['decision'],'NEW_CAMPAIGN_REQUIRED');self.assertEqual(old.read_text(),'FAILURE_PRESERVED')
    def test_incomplete_cleanup_never_eligible(self):
        with tempfile.TemporaryDirectory()as tmp:
            self.assertFalse(controller.safe_auth_resume(tmp,{},dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED'),dict(status='PASS',errors=[])))
    def test_multiple_short_timestamps_failure_and_explicit_resume_skip_success(self):
        # Real stage journal/recovered/raw verifier; only machine/LLM are synthetic.
        with tempfile.TemporaryDirectory()as tmp,ExitStack()as stack:
            root=Path(tmp);s=Sudo(root);detected=detection(6,8);config=planning.default(detected)
            campaign,_=fixture(root/'template','C',False)
            campaign.update(power_management=contracts.UNMANAGED_POWER)
            campaign['host']=dict(detection=detected,kernel='SYNTHETIC')
            campaign['configuration'].update(config);campaign['domain']['selected']=config['affinity_cpus']
            tune=dict(host=dict(detection=detected),rules=dict(planning.DEFAULT_RULES),limits=dict(seconds=7200,sessions=160),scheduler='linux')
            fail=[True];acquired=[]
            def acquire(path,campaign,variant,slot,auth=None):
                path=Path(path);acquired.append(slot['id'])
                with runner.auth_context(auth):
                    runner.root_command(['true'],root/('preflight-'+str(len(acquired))+'.json'))
                    if slot['block']==2 and fail[0]:
                        path.mkdir();s.busy=True;runner.AUTH_EXECUTION.get()['active']=True;s.now+=3
                        try:runner.root_command(['true'],path/'stats-command.json')
                        except authmod.CredentialExpired:
                            core.save(path/'server-command.json',{});core.save(path/'kernel-watch-result.json',dict(status='PASS'))
                            core.save(path/'credential-gate-failure.json',dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED',phase='STATS_AFTER_MEASURED',operation_started=False))
                            core.save(path/'cleanup.json',dict(status='PASS',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True))
                            core.save(path/'failure.json',dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED',phase='STATS_AFTER_MEASURED',automatic_retry=False))
                            s.busy=False;raise ValueError('SYNTHETIC_AUTH_EXPIRED; NO_RETRY')
                    actual,_=fixture(path,'C',False)
                    (path/'monitor.jsonl').write_text((root/'template/monitor.jsonl').read_text())
                    monitors=[json.loads(line)for line in (path/'monitor.jsonl').read_text().splitlines()]
                    for r in monitors:r.pop('power_binding');r['power_management']=contracts.UNMANAGED_POWER
                    (path/'monitor.jsonl').write_text('\n'.join(json.dumps(r)for r in monitors)+'\n')
                    finish(path,campaign,slot);s.now+=3
                    return backend.verify(path,campaign,slot)
            stack.enter_context(s.patched())
            stack.enter_context(patch.object(experiment,'variant',return_value=(root/'variant',campaign)))
            stack.enter_context(patch.object(experiment.Experiment,'functionals'))
            stack.enter_context(patch.object(backend,'current'))
            stack.enter_context(patch.object(backend,'acquire',side_effect=acquire))
            sequence=itertools.count()
            stack.enter_context(patch.object(backend,'token',side_effect=lambda:f'SYNTHETIC-{next(sequence):06d}'))
            stack.enter_context(patch.object(backend.contracts,'load_campaign',return_value=campaign))
            stack.enter_context(patch.object(experiment,'export',return_value={}))
            s.auth.initial();run=experiment.Experiment(root,tune,auth=s.auth)
            visit=lambda block:dict(candidate=config,block=block,position=1)
            run.measure('PREFILL',visit(1))
            saved={str(p):p.read_bytes()for p in root.glob('raw/**/*')if p.is_file()}
            with self.assertRaisesRegex(ValueError,'NO_RETRY'):run.measure('PREFILL',visit(2))
            self.assertEqual(len(acquired),2)
            failed=next(p for p in root.glob('raw/**/failure.json'));failed_bytes=failed.read_bytes()
            with self.assertRaisesRegex(ValueError,'automatic retry prohibited'):run.measure('PREFILL',visit(2))
            self.assertEqual(len(acquired),2)
            fail[0]=False;resumed=experiment.Experiment(root,tune,resume_auth=True,auth=s.auth)
            resumed.measure('PREFILL',visit(1));self.assertEqual(len(acquired),2)
            for block in range(2,7):resumed.measure('PREFILL',visit(block))
            self.assertEqual(len(acquired),7);self.assertEqual(len(resumed.rows),6)
            self.assertTrue(all(Path(name).read_bytes()==raw for name,raw in saved.items()));self.assertEqual(failed.read_bytes(),failed_bytes)
            self.assertGreaterEqual(len(s.prompts),6)
            for record in experiment.records(root):
                if (Path(record['raw'])/'completion.json').exists():self.assertIsNotNone(experiment.recovered(record,tune,True))


if __name__=='__main__':unittest.main(verbosity=2)
