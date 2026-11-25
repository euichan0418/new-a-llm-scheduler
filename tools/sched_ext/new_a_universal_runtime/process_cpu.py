# SPDX-License-Identifier: GPL-2.0
"""Linux process CPU clocks, pinned identity and independent proc accounting."""
import ctypes
import os
from pathlib import Path
import select
import time

SCOPE = 'WHOLE_PROCESS_CPU_CLOCK_ALL_THREADS_REQUEST_BRACKET'
API = 'libc.clock_getcpuclockid+time.clock_gettime_ns'


class ProcessCPUError(ValueError):pass


def require(ok, message):
    if not ok:raise ProcessCPUError(message)


def process_clock_id(pid):
    require(type(pid) is int and 0 < pid < 2**31, 'PROCESS_CPU_INVALID_PID')
    try:
        function = ctypes.CDLL(None).clock_getcpuclockid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        function.restype = ctypes.c_int
        clock = ctypes.c_int()
        code = function(pid, ctypes.byref(clock))
    except (AttributeError, OSError) as error:
        raise ProcessCPUError('PROCESS_CPU_LIBC_UNAVAILABLE: '+str(error)) from error
    # This API returns an error number, not -1 with errno.
    require(code == 0, 'PROCESS_CPU_CLOCK_ID_FAILED: '+str(code)+' '+(os.strerror(code) if code > 0 else 'invalid return'))
    return clock.value


def proc_stat(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rfind(')')+2:].split()
        row = dict(pid=int(raw.split('(',1)[0]), state=fields[0], starttime=int(fields[19]),
                   utime_ticks=int(fields[11]), stime_ticks=int(fields[12]), uid=Path(f'/proc/{pid}').stat().st_uid)
        require(row['pid'] == pid and row['state'] not in ['Z','X','x'], 'PROCESS_CPU_TARGET_EXITED_OR_INVALID')
        require(all(row[k] >= 0 for k in ['utime_ticks','stime_ticks']), 'PROCESS_CPU_BAD_PROC_COUNTER')
        return row
    except (OSError, IndexError, ValueError) as error:
        if isinstance(error, ProcessCPUError):raise
        raise ProcessCPUError('PROCESS_CPU_PROC_UNAVAILABLE: '+str(error)) from error


def validate_point(point, target, hz):
    require(type(hz) is int and hz > 0, 'PROCESS_CPU_BAD_CLK_TCK')
    require(type(point['ns']) is int and point['ns'] >= 0, 'PROCESS_CPU_BAD_CLOCK_VALUE')
    require(type(point['before_mono_ns']) is int and type(point['after_mono_ns']) is int and
            0 < point['before_mono_ns'] <= point['after_mono_ns'], 'PROCESS_CPU_BAD_SAMPLE_BRACKET')
    totals = []
    for row in [point['proc_before'], point['proc_after']]:
        require(row['pid'] == target['pid'] and row['starttime'] == target['starttime'] and
                row['uid'] == target['uid'] and row['state'] not in ['Z','X','x'], 'PROCESS_CPU_IDENTITY_CHANGED')
        require(all(type(row[k]) is int and row[k] >= 0 for k in ['utime_ticks','stime_ticks']), 'PROCESS_CPU_BAD_PROC_COUNTER')
        totals.append(row['utime_ticks']+row['stime_ticks'])
    require(totals[1] >= totals[0], 'PROCESS_CPU_PROC_COUNTER_DECREASED')
    # utime and stime are separately quantized to USER_HZ ticks.
    require((totals[0]-2)*10**9 <= point['ns']*hz <= (totals[1]+2)*10**9, 'PROCESS_CPU_PROC_CLOCK_MISMATCH')


def validate_record(value, server, client):
    require(value['scope'] == SCOPE and value['clock_api'] == API, 'PROCESS_CPU_WRONG_SCOPE_OR_API')
    require(type(value['clock_id']) is int and value['pid'] == server['pid'] and
            value['starttime'] == server['starttime'], 'PROCESS_CPU_IDENTITY_CHANGED')
    proof = value['clock_verification']
    require(proof['schema'] == 1 and proof['pidfd_alive_before_after'] is True, 'PROCESS_CPU_LIVENESS_NOT_VERIFIED')
    for when in ['before','after']:
        point = proof[when]
        validate_point(point, server, proof['clk_tck'])
        require(value[when+'_ns'] == point['ns'], 'PROCESS_CPU_SAMPLE_MISMATCH')
    require(value['before_mono_ns'] == proof['before']['before_mono_ns'] and
            value['after_mono_ns'] == proof['after']['after_mono_ns'] and
            proof['before']['after_mono_ns'] <= client['begin_ns'] < client['end_ns'] <= proof['after']['before_mono_ns'],
            'PROCESS_CPU_HTTP_BRACKET_INVALID')
    require(value['after_ns'] > value['before_ns'] and
            proof['after']['proc_before']['utime_ticks']+proof['after']['proc_before']['stime_ticks'] >=
            proof['before']['proc_after']['utime_ticks']+proof['before']['proc_after']['stime_ticks'], 'PROCESS_CPU_COUNTER_NOT_INCREASING')


class ProcessCPUClock:
    def __init__(self, target):
        self.target = dict(target)
        self.pidfd = None
        self.samples = {}
        require(type(target.get('pid')) is int and 0 < target['pid'] < 2**31 and
                type(target.get('starttime')) is int and target['starttime'] > 0 and
                type(target.get('uid')) is int, 'PROCESS_CPU_INVALID_TARGET')
        try:
            self.pidfd = os.pidfd_open(target['pid'])
            self.hz = os.sysconf('SC_CLK_TCK')
            self.live_stat()
            self.clock_id = process_clock_id(target['pid'])
            self.live_stat()
        except BaseException as error:
            self.close()
            if isinstance(error, (OSError, AttributeError)):
                raise ProcessCPUError('PROCESS_CPU_BIND_FAILED: '+str(error)) from error
            raise

    def alive(self):
        poll = select.poll()
        poll.register(self.pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        require(not poll.poll(0), 'PROCESS_CPU_TARGET_EXITED_OR_PIDFD_INVALID')

    def live_stat(self):
        self.alive()
        row = proc_stat(self.target['pid'])
        require(all(row[k] == self.target[k] for k in ['pid','starttime','uid']), 'PROCESS_CPU_IDENTITY_CHANGED')
        self.alive()
        return row

    def sample(self, when):
        try:
            point = dict(before_mono_ns=time.monotonic_ns(), proc_before=self.live_stat())
            point['ns'] = time.clock_gettime_ns(self.clock_id)
            point['proc_after'] = self.live_stat()
            point['after_mono_ns'] = time.monotonic_ns()
            validate_point(point, self.target, self.hz)
        except OSError as error:
            raise ProcessCPUError('PROCESS_CPU_READ_FAILED: '+str(error)) from error
        self.samples[when] = point
        return point

    def begin(self):
        require(not self.samples, 'PROCESS_CPU_ALREADY_BEGUN')
        self.sample('before')

    def finish(self, begin_ns, end_ns):
        require(set(self.samples) == {'before'}, 'PROCESS_CPU_BEGIN_REQUIRED')
        self.sample('after')
        before, after = self.samples['before'], self.samples['after']
        value = dict(pid=self.target['pid'], starttime=self.target['starttime'], clock_id=self.clock_id,
                     before_ns=before['ns'], after_ns=after['ns'], before_mono_ns=before['before_mono_ns'],
                     after_mono_ns=after['after_mono_ns'], scope=SCOPE, clock_api=API,
                     clock_verification=dict(schema=1, clk_tck=self.hz, pidfd_alive_before_after=True, **self.samples))
        validate_record(value, self.target, dict(begin_ns=begin_ns, end_ns=end_ns))
        return value

    def close(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None
