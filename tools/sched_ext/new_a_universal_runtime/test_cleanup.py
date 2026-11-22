# SPDX-License-Identifier: GPL-2.0
"""Acquire/cleanup faults with fake subprocesses only; never sudo or attach."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import runtime_core as core
import controller,runner
from test_universal import domain


class BrokenInput:
    def write(self,value):raise BrokenPipeError('SYNTHETIC_ALREADY_CLOSED')
    def flush(self):raise BrokenPipeError('SYNTHETIC_ALREADY_CLOSED')
    def close(self):pass


class Watch:
    def __init__(self,code=0,bad=False):
        self.stdin=BrokenInput();self.stdout=io.BytesIO(b'READY\n'+json.dumps(dict(status='FAIL' if bad else 'PASS',kernel_opened=True,records=0,bpf_loaded=False,fd_closed=True)).encode());self.code=code
    def poll(self):return None
    def wait(self,timeout):return self.code


class Cleanup(unittest.TestCase):
    def run_fault(self,temp,kind='attach',code=0,bad=False,signal_error=False):
        root=Path(temp);path=root/'attempt';campaign=dict(domain=domain(4,8),build=dict(helpers={'kernel-watch':dict(path='/SYNTHETIC_WATCH')},loader=dict(path='/SYNTHETIC_LOADER')),baseline=dict(server_argv=['/SYNTHETIC_FROZEN'],marker='/SYNTHETIC_MARKER'))
        slot=dict(policy='E',stage='FUNCTIONAL',id='E-audit',audit=True)
        original=Path.read_text
        def read(self,*a,**kw):return 'disabled' if str(self)=='/sys/kernel/sched_ext/state' else original(self,*a,**kw)
        def auth(argv,file):
            core.save(file,dict(returncode=1 if kind=='auth' else 0,stderr='sudo: a password is required' if kind=='auth' else '',stdout=''))
            if kind=='auth':raise runner.AuthRequired('SYNTHETIC_AUTH_EXPIRED')
            return ''
        watch=Watch(code,bad)
        error=KeyboardInterrupt('SYNTHETIC_SIGTERM') if signal_error else ValueError('SYNTHETIC_ATTACH_FAILURE')
        with patch.object(Path,'read_text',read),patch.object(runner,'root_command',side_effect=auth),patch.object(runner,'root_process',side_effect=[watch,error]),patch.object(runner.select,'select',return_value=([watch.stdout],[],[])),patch.object(runner.contracts,'throttle_counts',return_value={}):
            with self.assertRaises(ValueError):runner.acquire_session(campaign,root,path,slot)
        self.assertFalse((path/'completion.json').exists());self.assertTrue((path/'failure.json').is_file())
        return path,campaign
    def test_auth_failure_before_resources_and_explicit_resume(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t,'auth');failure=core.read(path/'failure.json');cleanup=core.read(path/'cleanup.json')
            self.assertEqual(failure['kind'],'SUDO_AUTH_REQUIRED_OR_EXPIRED');self.assertEqual(cleanup['status'],'PASS')
            self.assertTrue(controller.safe_auth_resume(path,c,failure,cleanup));self.assertFalse((path/'loader-command.json').exists())
    def test_attach_failure_preserved_with_confirmed_watch_cleanup(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t);self.assertEqual(core.read(path/'cleanup.json')['status'],'PASS')
            self.assertFalse(controller.safe_auth_resume(path,c,core.read(path/'failure.json'),core.read(path/'cleanup.json')))
    def test_broken_pipe_and_nonzero_watch_exit_never_pass(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t,code=1);self.assertEqual(core.read(path/'cleanup.json')['status'],'FAIL')
    def test_watch_result_failure_never_pass(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t,bad=True);self.assertEqual(core.read(path/'cleanup.json')['status'],'FAIL')
    def test_sigterm_style_interruption_still_runs_cleanup(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t,signal_error=True);self.assertEqual(core.read(path/'kernel-watch-result.json')['status'],'PASS')
    def test_cleanup_fail_blocks_auth_resume(self):
        with tempfile.TemporaryDirectory() as t:
            path,c=self.run_fault(t,'auth');cleanup=core.read(path/'cleanup.json');cleanup.update(status='FAIL',errors=['UNCERTAIN'])
            self.assertFalse(controller.safe_auth_resume(path,c,core.read(path/'failure.json'),cleanup))


if __name__=='__main__':unittest.main(verbosity=2)
