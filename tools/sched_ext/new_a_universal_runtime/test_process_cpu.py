#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Real short-lived external processes and synthetic clock failure cases."""
import ast
import copy
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import process_cpu as cpu
import runner

CHILD = r'''
import json,sys,threading,time
begin=threading.Event();done=threading.Barrier(3);started=threading.Barrier(3);workers=[]
def work():
    started.wait();begin.wait();start=time.thread_time_ns();value=0
    while time.thread_time_ns()-start<60000000:
        for i in range(1000):value+=i
    workers.append(time.thread_time_ns()-start);done.wait()
threads=[threading.Thread(target=work,daemon=True)for _ in range(2)]
for thread in threads:thread.start()
started.wait();print('READY',flush=True)
if sys.stdin.readline().strip()!='RUN':raise SystemExit(2)
start=time.process_time_ns();begin.set();done.wait()
print(json.dumps(dict(workers_ns=workers,process_ns=time.process_time_ns()-start)),flush=True)
sys.stdin.readline()
for thread in threads:thread.join()
'''


class ExternalProcess(unittest.TestCase):
    def setUp(self):
        self.child=subprocess.Popen([sys.executable,'-I','-B','-c',CHILD],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.assertEqual(self.child.stdout.readline().strip(),'READY')
        self.target=cpu.proc_stat(self.child.pid);self.counter=None
    def tearDown(self):
        if self.counter:self.counter.close()
        if self.child.poll()is None:
            self.child.stdin.close()
            try:self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=3)
        for stream in [self.child.stdout,self.child.stderr]:stream.close()
        if not self.child.stdin.closed:self.child.stdin.close()
    def exercise(self):
        self.counter=cpu.ProcessCPUClock(self.target);self.counter.begin()
        a=cpu.proc_stat(self.child.pid);leader_a=Path(f'/proc/{self.child.pid}/task/{self.child.pid}/stat').read_text().rsplit(')',1)[1].split()
        begin=time.monotonic_ns();self.child.stdin.write('RUN\n');self.child.stdin.flush();reported=json.loads(self.child.stdout.readline());end=time.monotonic_ns()
        b=cpu.proc_stat(self.child.pid);leader_b=Path(f'/proc/{self.child.pid}/task/{self.child.pid}/stat').read_text().rsplit(')',1)[1].split()
        value=self.counter.finish(begin,end)
        delta=value['after_ns']-value['before_ns'];hz=os.sysconf('SC_CLK_TCK');proc_delta=((b['utime_ticks']+b['stime_ticks'])-(a['utime_ticks']+a['stime_ticks']))*10**9/hz
        leader_delta=((int(leader_b[11])+int(leader_b[12]))-(int(leader_a[11])+int(leader_a[12])))*10**9/hz
        self.assertGreater(sum(reported['workers_ns']),100000000)
        self.assertLess(abs(delta-reported['process_ns']),4*10**9/hz)
        self.assertLess(abs(delta-proc_delta),4*10**9/hz)
        self.assertGreater(delta,leader_delta+60000000)
        cpu.validate_record(value,self.target,dict(begin_ns=begin,end_ns=end))
        reported.update(external_clock_delta_ns=delta,proc_delta_ns=proc_delta,leader_delta_ns=leader_delta)
        return value,reported
    def test_external_multithread_process_clock_and_independent_proc(self):
        value,reported=self.exercise();self.assertEqual(value['pid'],self.child.pid);self.assertNotEqual(value['pid'],os.getpid())
        self.assertEqual(value['scope'],cpu.SCOPE);self.assertEqual(len(reported['workers_ns']),2)
        print('EXTERNAL_PROCESS_CPU_CLOCK_PROOF='+json.dumps(dict(python=sys.version.split()[0],**reported)),flush=True)
    def test_no_thread_clock_or_caller_process_clock_substitution(self):
        with patch.object(time,'pthread_getcpuclockid',side_effect=AssertionError('THREAD_NOT_ALLOWED')),patch.object(time,'process_time_ns',side_effect=AssertionError('CALLER_NOT_TARGET')):
            self.exercise()
    def test_exit_before_finish_is_rejected_and_fd_closed(self):
        self.counter=cpu.ProcessCPUClock(self.target);self.counter.begin();fd=self.counter.pidfd
        self.child.terminate();self.child.wait(timeout=3)
        with self.assertRaisesRegex(cpu.ProcessCPUError,'EXITED'):self.counter.finish(time.monotonic_ns(),time.monotonic_ns()+1)
        self.counter.close()
        with self.assertRaises(OSError):os.fstat(fd)
    def test_wrong_starttime_blocks_binding(self):
        bad=dict(self.target,starttime=self.target['starttime']+1)
        with self.assertRaisesRegex(cpu.ProcessCPUError,'IDENTITY_CHANGED'):cpu.ProcessCPUClock(bad)
    def test_pid_reuse_during_sampling_is_rejected(self):
        self.counter=cpu.ProcessCPUClock(self.target);self.counter.begin()
        with patch.object(cpu,'proc_stat',return_value=dict(self.target,starttime=self.target['starttime']+1)):
            with self.assertRaisesRegex(cpu.ProcessCPUError,'IDENTITY_CHANGED'):self.counter.sample('after')
    def test_clock_read_failure_is_not_zero(self):
        self.counter=cpu.ProcessCPUClock(self.target)
        with patch.object(cpu.time,'clock_gettime_ns',side_effect=OSError(errno.EINVAL,'invalid clock')):
            with self.assertRaisesRegex(cpu.ProcessCPUError,'READ_FAILED'):self.counter.begin()
        self.assertEqual(self.counter.samples,{})
    def test_clock_id_failure_closes_binding_fd(self):
        descriptors=[];original=os.pidfd_open
        def record(pid):
            result=original(pid);descriptors.append(result);return result
        with patch.object(cpu.os,'pidfd_open',side_effect=record),patch.object(cpu,'process_clock_id',side_effect=cpu.ProcessCPUError('LIBC_FAIL')):
            with self.assertRaisesRegex(cpu.ProcessCPUError,'LIBC_FAIL'):cpu.ProcessCPUClock(self.target)
        self.assertEqual(len(descriptors),1)
        with self.assertRaises(OSError):os.fstat(descriptors[0])
    def test_failed_clock_before_http_preserves_error_and_sends_no_request(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(runner.http.client,'HTTPConnection')as connection,patch.object(cpu,'process_clock_id',side_effect=cpu.ProcessCPUError('ESRCH_INJECTED')):
            p=Path(tmp)
            with self.assertRaisesRegex(cpu.ProcessCPUError,'ESRCH_INJECTED'):
                runner.request(dict(baseline=dict(server_argv=['SYNTHETIC','--port','1']),request={},request_sha256='SYNTHETIC'),p,'measured',lambda:None,self.target)
            connection.assert_not_called();self.assertFalse((p/'process-cpu.json').exists())
            self.assertEqual(json.loads((p/'measured-process-cpu-failure.json').read_text())['status'],'FAILED_NOT_A_SAMPLE')
    def test_real_external_clock_through_common_request_path_without_http_or_inference(self):
        data=dict(content='SYNTHETIC_NO_INFERENCE',tokens_evaluated=920,tokens_predicted=128,
                  timings=dict(prompt_n=920,predicted_n=128,prompt_ms=1,predicted_ms=1,prompt_per_second=920000,predicted_per_second=128000))
        def send(*args):self.child.stdin.write('RUN\n');self.child.stdin.flush()
        def receive(*args):
            json.loads(self.child.stdout.readline())
            return json.dumps(data).encode()
        response=Mock(status=200,read=Mock(side_effect=receive))
        connection=Mock(request=Mock(side_effect=send),getresponse=Mock(return_value=response))
        with tempfile.TemporaryDirectory()as tmp,patch.object(runner.http.client,'HTTPConnection',return_value=connection):
            path=Path(tmp)
            runner.request(dict(baseline=dict(server_argv=['SYNTHETIC','--port','1']),request={},request_sha256='a'*64),path,'measured',lambda:None,self.target)
            value=json.loads((path/'process-cpu.json').read_text());client=json.loads((path/'measured-client.json').read_text())
            cpu.validate_record(value,self.target,client);self.assertGreater(value['after_ns']-value['before_ns'],100000000)
            self.assertEqual(json.loads((path/'measured-response.json').read_text()),data)
            self.assertAlmostEqual(client['request_wall_ms'],(client['end_ns']-client['begin_ns'])/1e6)
            connection.close.assert_called_once()
    def test_connection_close_error_still_closes_process_clock(self):
        connection=Mock(getresponse=Mock(return_value=Mock(status=200,read=Mock(return_value=b'{}'))),close=Mock(side_effect=OSError('SYNTHETIC_CLOSE_FAILURE')))
        counter=Mock()
        with tempfile.TemporaryDirectory()as tmp,patch.object(runner.http.client,'HTTPConnection',return_value=connection),patch.object(cpu,'ProcessCPUClock',return_value=counter):
            with self.assertRaisesRegex(OSError,'CLOSE_FAILURE'):
                runner.request(dict(baseline=dict(server_argv=['SYNTHETIC','--port','1']),request={}),Path(tmp),'measured',lambda:None,self.target)
            counter.close.assert_called_once()


class BindContract(unittest.TestCase):
    def test_libc_return_code_is_used_not_errno(self):
        for code in [errno.ESRCH,errno.EPERM,errno.ENOSYS]:
            function=Mock(return_value=code)
            with self.subTest(code=code),patch.object(cpu.ctypes,'CDLL',return_value=Mock(clock_getcpuclockid=function)):
                ctypes.set_errno(0)
                with self.assertRaisesRegex(cpu.ProcessCPUError,str(code)):cpu.process_clock_id(123)
                self.assertEqual(function.argtypes,[ctypes.c_int,ctypes.POINTER(ctypes.c_int)])
    def test_signed_linux_clock_id_from_libc(self):
        def answer(pid,pointer):pointer._obj.value=-1234;return 0
        function=Mock(side_effect=answer)
        with patch.object(cpu.ctypes,'CDLL',return_value=Mock(clock_getcpuclockid=function)):
            self.assertEqual(cpu.process_clock_id(123),-1234)
    def test_invalid_pid_never_calls_libc(self):
        for pid in [0,-1,True,2**31,'123']:
            with self.subTest(pid=pid),patch.object(cpu.ctypes,'CDLL')as library:
                with self.assertRaises(cpu.ProcessCPUError):cpu.process_clock_id(pid)
                library.assert_not_called()
    def test_missing_libc_symbol_fails_closed(self):
        with patch.object(cpu.ctypes,'CDLL',return_value=object()):
            with self.assertRaisesRegex(cpu.ProcessCPUError,'LIBC_UNAVAILABLE'):cpu.process_clock_id(123)
    def test_bad_proc_parse_is_explicit_failure(self):
        with patch.object(Path,'read_text',return_value='unparseable'):
            with self.assertRaisesRegex(cpu.ProcessCPUError,'PROC_UNAVAILABLE'):cpu.proc_stat(123)
    def test_proc_clock_disagreement_is_not_accepted(self):
        row=dict(pid=123,starttime=5,uid=os.getuid(),state='S',utime_ticks=100,stime_ticks=0)
        point=dict(ns=1,before_mono_ns=1,after_mono_ns=2,proc_before=row,proc_after=row)
        with self.assertRaisesRegex(cpu.ProcessCPUError,'PROC_CLOCK_MISMATCH'):cpu.validate_point(point,row,100)
    def test_python311_syntax_and_no_nonexistent_time_api(self):
        for name in ['process_cpu.py','runner.py']:
            text=(Path(__file__).parent/name).read_text();ast.parse(text,feature_version=(3,11))
            self.assertNotIn('time.clock_getcpuclockid(',text);self.assertNotIn('time.pthread_getcpuclockid(',text)


if __name__=='__main__':unittest.main(verbosity=2)
