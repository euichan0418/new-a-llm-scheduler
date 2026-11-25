# SPDX-License-Identifier: GPL-2.0
"""Light /proc interference evidence; never suspend or re-affinitize developers."""
import json
import os
from pathlib import Path
import time
from runtime_core import require

DEFAULT=dict(schema=1,pre_cpu_busy_pct=15.0,foreign_cpu_seconds_per_second=0.20,sample_seconds=1.0,boundary_wait_seconds=30.0,
             enforcement='FAIL_CONTAMINATED_ATTEMPT_EXPLICIT_RESUME_ONLY',power_comparability='NOT_GUARANTEED',dev_affinity='NEVER_CHANGED')


class InterferenceDetected(ValueError):pass
class BoundaryPause(ValueError):pass


def optional_telemetry():
    """Nonbinding read-only telemetry; missing power interfaces never block."""
    def read(path):
        try:return Path(path).read_text().strip()
        except OSError:return 'NOT_READABLE'
    cpu=Path('/sys/devices/system/cpu')
    return dict(governors={p.name:read(p/'scaling_governor')for p in (cpu/'cpufreq').glob('policy*')},
                epp={p.name:read(p/'energy_performance_preference')for p in (cpu/'cpufreq').glob('policy*')},
                frequency_khz={p.name:read(p/'scaling_cur_freq')for p in (cpu/'cpufreq').glob('policy*')},
                no_turbo=read(cpu/'intel_pstate/no_turbo'),power_profile='NOT_COLLECTED_NO_DAEMON_DEPENDENCY',binding=False,settings_changed=False)


def snapshot():
    cpus={}
    for line in Path('/proc/stat').read_text().splitlines():
        fields=line.split()
        if fields[0].startswith('cpu')and fields[0][3:].isdigit():
            v=list(map(int,fields[1:9]));cpus[int(fields[0][3:])]=(sum(v),v[3]+v[4])
    tasks={}
    for path in Path('/proc').glob('[0-9]*'):
        try:
            if path.stat().st_uid!=os.getuid():continue
            raw=(path/'stat').read_text();fields=raw[raw.rfind(')')+2:].split();pid=int(path.name)
            tasks[(pid,int(fields[19]))]=dict(ticks=int(fields[11])+int(fields[12]),cpus=sorted(os.sched_getaffinity(pid)),name=(path/'comm').read_text().strip())
        except (OSError,ValueError,IndexError):continue
    return dict(monotonic=time.monotonic(),cpus=cpus,tasks=tasks,hz=os.sysconf('SC_CLK_TCK'))


def compare(before,after,selected,exclude=()):
    elapsed=after['monotonic']-before['monotonic'];require(elapsed>0,'Interference clock invalid')
    busy={}
    for cpu in selected:
        a,b=before['cpus'][cpu],after['cpus'][cpu];total=b[0]-a[0];idle=b[1]-a[1]
        require(total>0 and 0<=idle<=total,'CPU interference counters invalid')
        busy[str(cpu)]=100*(1-idle/total)
    offenders=[];excluded=set(exclude)
    for key,after_task in after['tasks'].items():
        if key[0]in excluded or not set(after_task['cpus'])&set(selected):continue
        # New or vanished tasks have no exact interval baseline. Do not claim
        # complete attribution for them; total pre-session CPU busy still applies.
        if key not in before['tasks']:continue
        ticks=after_task['ticks']-before['tasks'][key]['ticks'];require(ticks>=0,'Interference PID clock decreased')
        usage=ticks/after['hz']/elapsed
        if usage>0:offenders.append(dict(pid=key[0],starttime=key[1],name=after_task['name'],cpu_seconds_per_second=usage,allowed_cpus=after_task['cpus']))
    return dict(elapsed_s=elapsed,per_cpu_busy_pct=busy,foreign_cpu_seconds_per_second=sum(x['cpu_seconds_per_second']for x in offenders),processes=offenders,
                scope='VISIBLE_SAME_UID_PROCESS_CLOCKS_WITH_OVERLAPPING_AFFINITY; NOT_EXACT_PER_CPU_ATTRIBUTION',new_or_exited_task_attribution='NOT_VERIFIED')


def validate(contract):
    require(contract==DEFAULT,'Pre-registered interference contract changed')


def wait_quiet(campaign,root,selected,quiet,ready,*,reader=None,sleep=None,clock=None):
    contract=campaign.get('interference_contract')
    if contract is None:return
    validate(contract);reader=reader or snapshot;sleep=sleep or time.sleep;clock=clock or time.monotonic
    begin=clock();path=Path(root)/'interference-boundaries.private.jsonl';telemetry=optional_telemetry()
    while True:
        quiet();ready();before=reader();sleep(contract['sample_seconds']);after=reader()
        row=compare(before,after,selected,exclude=[os.getpid()])
        clean=max(row['per_cpu_busy_pct'].values())<=contract['pre_cpu_busy_pct']and row['foreign_cpu_seconds_per_second']<=contract['foreign_cpu_seconds_per_second']
        with path.open('a')as f:f.write(json.dumps(dict(status='READY'if clean else'PAUSED',evidence=row,limits=contract,optional_telemetry=telemetry))+'\n')
        quiet();ready()
        if clean:return
        print('CPU_INTERFERENCE_PAUSED: 개발 프로세스는 유지합니다. 안전한 경계에서 부하 해소를 기다립니다.',flush=True)
        if clock()-begin>=contract['boundary_wait_seconds']:raise BoundaryPause('CPU_INTERFERENCE_BOUNDARY_PAUSED; completed raw preserved; explicit resume required')


class Sampler:
    def __init__(self,contract):validate(contract);self.contract=contract;self.previous=snapshot()
    def sample(self,selected,exclude):
        after=snapshot()
        if after['monotonic']-self.previous['monotonic']<self.contract['sample_seconds']:return dict(status='BASELINE_OR_SHORT_INTERVAL',contaminated=False)
        row=compare(self.previous,after,selected,exclude);self.previous=after
        return dict(status='SAMPLED',contaminated=row['foreign_cpu_seconds_per_second']>self.contract['foreign_cpu_seconds_per_second'],evidence=row)
