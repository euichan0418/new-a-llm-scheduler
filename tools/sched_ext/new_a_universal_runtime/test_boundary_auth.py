#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Virtual long runs and user prompts only. Never executes sudo or a server."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import boundary_auth as a,controller
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'new_a_hybrid_autotune'))
import experiment as x,backend as b,planning as planning
from test_autotune import detection,metrics


class Clock:
    def __init__(self):self.now=1.
    def __call__(self):return self.now
    def advance(self,seconds):self.now+=seconds


class Scenario:
    def __init__(self,root):
        self.clock=Clock();self.busy=False;self.valid_until=0.;self.calls=[];self.prompts=[];self.auth_result=0;self.probe_error=None;self.prompt_wait=0.;self.no_cache=False;self.raw_corrupt=False
        self.context=dict(sid=111,tty=222,pgrp=333,foreground=333)
        self.auth=a.BoundaryAuth(root,self.quiet,self.clock)
    def quiet(self):
        if self.busy:raise ValueError('SYNTHETIC_RESOURCES_STILL_ACTIVE')
    def verify(self,path,campaign,stage,slot):
        value=json.loads((Path(path)/'cleanup.json').read_text())
        if self.raw_corrupt or value['status']!='PASS' or value['errors']:raise ValueError('SYNTHETIC_RAW_OR_CLEANUP_INVALID')
        return dict(status='RAW_COMPLETE')
    def command(self,argv,**kw):
        self.quiet();self.calls.append((list(argv),self.clock(),dict(kw)))
        if argv==['/usr/bin/sudo','-N','-n','-v']:
            if self.probe_error:return SimpleNamespace(returncode=2,stdout='',stderr=self.probe_error)
            valid=not self.no_cache and self.clock()<self.valid_until
            return SimpleNamespace(returncode=0 if valid else 1,stdout='',stderr=''if valid else'sudo: a password is required')
        if argv==['/usr/bin/sudo','-k']:
            self.valid_until=0.;return SimpleNamespace(returncode=0)
        if argv==['/usr/bin/sudo','-v']:
            self.prompts.append(self.clock());self.clock.advance(self.prompt_wait)
            if isinstance(self.auth_result,BaseException):raise self.auth_result
            if self.auth_result==0:self.valid_until=self.clock()+900
            return SimpleNamespace(returncode=self.auth_result)
        raise AssertionError('Unapproved command: '+str(argv))
    @contextmanager
    def patched(self):
        with patch.object(a,'terminal_context',side_effect=lambda:dict(self.context)),patch.object(a.subprocess,'run',side_effect=self.command),patch.object(a.evidence,'verify_session',side_effect=self.verify):yield self
    def completed(self,root,index,cleanup='PASS'):
        path=Path(root)/('raw-'+str(index));path.mkdir()
        (path/'cleanup.json').write_text(json.dumps(dict(status=cleanup,errors=[]if cleanup=='PASS'else['SYNTHETIC'])))
        (path/'raw.json').write_text('SYNTHETIC_PRESERVE_ME')
        slot=dict(stage='PERFORMANCE',id=str(index))
        self.auth.completed(path,{},slot);return path
    def events(self):return [json.loads(p.read_text())for p in self.auth.folder.glob('*.json')]


class Boundaries(unittest.TestCase):
    def test_initial_authentication_and_cache_probe(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():s.auth.initial()
            self.assertEqual(s.prompts,[1.]);self.assertEqual(s.calls[0][0],['/usr/bin/sudo','-v'])
            self.assertEqual(s.auth.issued,1.)
    def test_valid_fresh_cache_does_not_renew(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();expiry=s.valid_until;s.clock.advance(100);s.auth.before_session('next')
            self.assertEqual(len(s.prompts),1);self.assertEqual(s.valid_until,expiry)
            self.assertTrue(all('-N'in call for call,_,_ in s.calls[1:]))
    def test_twenty_sessions_and_multiple_explicit_reauth_over_73_minutes(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp);protected={}
            with s.patched():
                s.auth.initial()
                for index in range(20):
                    s.auth.before_session(str(index));s.busy=True;before=len(s.calls)
                    s.clock.advance(220);self.assertEqual(len(s.calls),before)
                    s.busy=False;path=s.completed(tmp,index);protected[str(path/'raw.json')]=(path/'raw.json').read_bytes()
            self.assertEqual(len(s.prompts),10);self.assertGreater(s.clock(),4400)
            self.assertEqual(sum(v['kind']=='SESSION_BOUNDARY_READY'for v in s.events()),20)
            self.assertTrue(all(Path(p).read_bytes()==raw for p,raw in protected.items()))
            allowed=[['/usr/bin/sudo','-v'],['/usr/bin/sudo','-k'],['/usr/bin/sudo','-N','-n','-v']]
            self.assertTrue(all(command in allowed for command,_,_ in s.calls))
    def test_near_expiry_refreshes_before_expiration(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():s.auth.initial();s.clock.advance(240);s.auth.before_session('next')
            self.assertEqual(len(s.prompts),2);commands=[c for c,_,_ in s.calls]
            index=commands.index(['/usr/bin/sudo','-k']);self.assertEqual(commands[index+1],['/usr/bin/sudo','-v'])
            self.assertNotIn(['/usr/bin/sudo','-k','-v'],commands)
    def test_expired_cache_requests_password_without_failed_session(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():s.auth.initial();s.clock.advance(901);s.auth.before_session('next')
            self.assertEqual(len(s.prompts),2);self.assertFalse(any(c==['/usr/bin/sudo','-k']for c,_,_ in s.calls))
    def test_busy_resources_block_prompt_and_probe(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();count=len(s.calls);s.busy=True;s.clock.advance(300)
                with self.assertRaisesRegex(ValueError,'ACTIVE'):s.auth.before_session('blocked')
            self.assertEqual(len(s.calls),count)
    def test_failed_previous_cleanup_blocks_authentication(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();p=s.completed(tmp,0);(p/'cleanup.json').write_text(json.dumps(dict(status='FAIL',errors=['NOT_CLOSED'])))
                count=len(s.calls);s.clock.advance(300)
                with self.assertRaisesRegex(ValueError,'CLEANUP_INVALID'):s.auth.before_session('blocked')
            self.assertEqual(len(s.calls),count)
    def test_corrupt_completed_raw_cannot_be_skipped(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();s.completed(tmp,0);count=len(s.calls);s.raw_corrupt=True
                with self.assertRaisesRegex(ValueError,'RAW_OR_CLEANUP'):s.auth.before_session('blocked')
            self.assertEqual(len(s.calls),count)
    def test_user_refusal_keeps_previous_raw_and_records_stopped_auth(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();p=s.completed(tmp,0);before=(p/'raw.json').read_bytes();s.clock.advance(300);s.auth_result=1
                with self.assertRaisesRegex(ValueError,'FAILED_OR_CANCELLED'):s.auth.before_session('declined')
            self.assertEqual((p/'raw.json').read_bytes(),before);self.assertIsNone(s.auth.issued)
            self.assertTrue(any(v['kind']=='USER_AUTH_STOPPED'and v['new_session_started']is False for v in s.events()))
    def test_interrupt_during_password_prompt_is_preserved(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();s.clock.advance(901);s.auth_result=KeyboardInterrupt('SYNTHETIC_CTRL_C')
                with self.assertRaises(KeyboardInterrupt):s.auth.before_session('cancelled')
            self.assertTrue(any(v['kind']=='USER_AUTH_STOPPED'for v in s.events()))
    def test_non_auth_cache_error_does_not_prompt(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();s.probe_error='sudo: policy plugin failure'
                with self.assertRaisesRegex(ValueError,'CACHE_CHECK_FAILED'):s.auth.before_session('blocked')
            self.assertEqual(len(s.prompts),1)
    def test_post_auth_cache_missing_stops_before_resources(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp);s.no_cache=True
            with s.patched(),self.assertRaisesRegex(ValueError,'POST_AUTH_CACHE_UNAVAILABLE'):s.auth.initial()
            self.assertIsNone(s.auth.issued)
    def test_authentication_requires_same_terminal_context(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();count=len(s.calls);s.context['sid']=999
                with self.assertRaisesRegex(ValueError,'CONTEXT_CHANGED'):s.auth.before_session('blocked')
            self.assertEqual(len(s.calls),count)
    def test_context_change_during_password_prompt_is_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp);original=s.command
            def changed(command,**kwargs):
                result=original(command,**kwargs)
                if command==['/usr/bin/sudo','-v']:s.context['sid']=999
                return result
            with s.patched(),patch.object(a.subprocess,'run',side_effect=changed),self.assertRaisesRegex(ValueError,'CONTEXT_CHANGED_DURING_PROMPT'):s.auth.initial()
            self.assertIsNone(s.auth.issued)
    def test_deadline_is_not_reset_after_waiting_for_password(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            def ready():a.require(s.clock()<7200,'SYNTHETIC_DEADLINE_EXPIRED')
            with s.patched():
                s.auth.initial();s.clock.advance(300);s.prompt_wait=8000
                with self.assertRaisesRegex(ValueError,'DEADLINE_EXPIRED'):s.auth.before_session('blocked',ready=ready)
            self.assertFalse(any(v['kind']=='SESSION_BOUNDARY_READY'for v in s.events()))
    def test_host_changed_while_waiting_for_password_blocks(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial();s.clock.advance(300)
                with self.assertRaisesRegex(ValueError,'HOST_CHANGED'):
                    s.auth.before_session('blocked',after_auth=Mock(side_effect=ValueError('HOST_CHANGED')))
            self.assertFalse(any(v['kind']=='SESSION_BOUNDARY_READY'for v in s.events()))
    def test_slow_readiness_cannot_consume_reserved_window_and_start(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial()
                with self.assertRaisesRegex(ValueError,'PLANNING_WINDOW_EXCEEDED'):
                    s.auth.before_session('blocked',ready=lambda:s.clock.advance(300))
            self.assertFalse(any(v['kind']=='SESSION_BOUNDARY_READY'for v in s.events()))
    def test_policy_denial_is_not_misclassified_as_expiration(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial()
                with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='sudo: user is not in the sudoers file')),self.assertRaisesRegex(ValueError,'CACHE_CHECK_FAILED'):s.auth.before_session('blocked')
            self.assertEqual(len(s.prompts),1)
    def test_no_reauth_before_session_when_initial_auth_missing(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched(),self.assertRaisesRegex(ValueError,'INITIAL_USER_AUTH_REQUIRED'):s.auth.before_session('blocked')
            self.assertFalse(s.calls)
    def test_password_is_never_piped_or_logged(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():s.auth.initial();s.clock.advance(300);s.auth.before_session('next')
            for command,_,kwargs in s.calls:
                self.assertNotIn('input',kwargs);self.assertNotIn('stdin',kwargs);self.assertNotIn('-S',command)
                if command[-1]=='-v'and'-n'not in command:self.assertNotIn('capture_output',kwargs)
            self.assertTrue(all(v['password_recorded']is False and v['background_keepalive']is False for v in s.events()))
    def test_probe_timeout_preserved_and_no_renewal_attempt(self):
        with tempfile.TemporaryDirectory()as tmp:
            s=Scenario(tmp)
            with s.patched():
                s.auth.initial()
                with patch.object(a.subprocess,'run',side_effect=subprocess.TimeoutExpired(['SYNTHETIC'],20)),self.assertRaises(subprocess.TimeoutExpired):s.auth.before_session('blocked')
            self.assertTrue(any(v['kind']=='CACHE_PROBE_STOPPED'for v in s.events()))
    def test_noninteractive_terminal_is_rejected(self):
        with patch.object(a.os,'getuid',return_value=1000),patch.object(a.os,'isatty',return_value=False),self.assertRaisesRegex(ValueError,'TERMINAL_REQUIRED'):a.terminal_context()
    def test_active_sched_ext_blocks_boundary(self):
        with patch.object(controller.contracts,'scheduler_state',return_value='enabled'),patch.object(controller,'foreign_processes')as foreign:
            with self.assertRaisesRegex(ValueError,'SCHED_EXT_ACTIVE'):controller.quiet_boundary()
            foreign.assert_not_called()


class Integration(unittest.TestCase):
    def setup_experiment(self,root):
        tune=dict(host=dict(detection=detection()),rules=dict(planning.DEFAULT_RULES),limits=dict(seconds=7200,sessions=160),scheduler='linux')
        auth=Mock();return x.Experiment(root,tune,auth=auth),auth
    def test_completed_functional_slot_not_reexecuted(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run,auth=self.setup_experiment(root)
            with patch.object(b.controller,'completed',return_value={'C-audit':Path('/SYNTHETIC_DONE')}),patch.object(b,'current'),patch.object(b,'acquire')as acquire:
                run.functionals(root,{'SYNTHETIC':True})
            self.assertEqual(acquire.call_count,1);self.assertEqual(acquire.call_args.args[-1]['id'],'C-clean');self.assertEqual(auth.before_session.call_count,1)
    def test_cancelled_auth_does_not_create_measurement_attempt(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run,auth=self.setup_experiment(root);auth.before_session.side_effect=ValueError('USER_AUTH_FAILED_OR_CANCELLED')
            visit=dict(candidate=planning.default(run.tune['host']['detection']),block=1,position=1)
            with patch.object(x,'variant',return_value=(root,{})),patch.object(run,'functionals'),patch.object(b,'current'),patch.object(b,'acquire')as acquire:
                with self.assertRaisesRegex(ValueError,'CANCELLED'):run.measure('PREFILL',visit)
            acquire.assert_not_called();self.assertFalse(list((root/'records').glob('*')));self.assertFalse(list((root/'raw').glob('*')))
    def test_completed_measurement_is_reverified_and_not_reexecuted(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run,auth=self.setup_experiment(root);config=planning.default(run.tune['host']['detection']);stage='PREFILL'
            slot=dict(id=stage+'-1-'+config['id'],policy='C',audit=False,stage='PERFORMANCE',block=1,position=1,tuning_stage=stage,candidate_id=config['id'],decision_rules_sha256=planning.fingerprint(run.tune['rules']))
            record=dict(slot=slot,raw='/SYNTHETIC_COMPLETED_RAW')
            with patch.object(x,'variant',return_value=(root,{})),patch.object(run,'functionals'),patch.object(x,'records',return_value=[record]),patch.object(x,'recovered',return_value=metrics())as verify,patch.object(x,'export'),patch.object(b,'acquire')as acquire:
                run.measure(stage,dict(candidate=config,block=1,position=1))
            verify.assert_called_once();acquire.assert_not_called();auth.before_session.assert_not_called();self.assertEqual(len(run.rows),1)
    def test_session_failure_keeps_failed_record_and_never_retries(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);run,auth=self.setup_experiment(root);config=planning.default(run.tune['host']['detection'])
            with patch.object(x,'variant',return_value=(root,{})),patch.object(run,'functionals'),patch.object(b,'current'),patch.object(b,'acquire',side_effect=ValueError('SYNTHETIC_MASK_FAILURE'))as acquire:
                with self.assertRaisesRegex(ValueError,'MASK_FAILURE'):run.measure('PREFILL',dict(candidate=config,block=1,position=1))
            self.assertEqual(acquire.call_count,1);self.assertEqual(len(x.records(root)),1)
            self.assertEqual(len(list((root/'records').glob('*.outcome.json'))),1);auth.completed.assert_not_called()
    def test_existing_universal_functional_controller_uses_same_gate(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);auth=Mock();campaign={'SYNTHETIC':True}
            with patch.object(controller,'gate',return_value=(campaign,{'C-audit':1,'C-clean':1})),patch.object(controller,'foreign_processes'),patch.object(controller.contracts,'load_campaign',return_value=campaign),patch.object(controller.os,'isatty',return_value=True),patch.object(controller.boundary_auth,'BoundaryAuth',return_value=auth),patch.object(controller.runner,'root_command')as root_check,patch.object(controller.runner,'acquire_session')as acquire,patch.object(controller.evidence,'verify_session'),patch.object(controller,'analyze',return_value={}):
                controller.execute(root,'FUNCTIONAL',True)
            auth.initial.assert_called_once();self.assertEqual(acquire.call_count,2);self.assertEqual(auth.before_session.call_count,2);self.assertEqual(auth.completed.call_count,2)
            self.assertEqual(root_check.call_count,1);self.assertEqual(root_check.call_args.args[0],['true'])


if __name__=='__main__':unittest.main(verbosity=2)
