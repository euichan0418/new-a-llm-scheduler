# SPDX-License-Identifier: GPL-2.0
"""Explicit Ubuntu installation and reversible performance-setting changes."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

TOOLS={'cc':'build-essential','make':'build-essential','clang':'clang','bpftool':'bpftool','nm':'binutils','readelf':'binutils','powerprofilesctl':'power-profiles-daemon'}
DEVEL=['libbpf-dev','libelf-dev','zlib1g-dev']


def require(ok,message):
    if not ok:raise ValueError(message)


def save(path,value):
    temp=Path(str(path)+'.tmp');fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w')as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)


def consent(message):
    require(os.isatty(0),'INTERACTIVE_APPROVAL_REQUIRED: use a normal terminal; no automatic yes')
    print(message+'\n승인하려면 YES를 입력하십시오. 그 외 입력은 중단합니다.',flush=True)
    try:answer=input().strip()
    except EOFError:raise ValueError('USER_NO_APPROVAL: terminal input closed')
    require(answer=='YES','USER_DECLINED; no dependent action')


def authenticate():
    require(subprocess.run(['/usr/bin/sudo','-v'],check=False).returncode==0,'SUDO_AUTH_REQUIRED; no password stored')


def root_command(argv, log, *, data=None, timeout=900):
    command=['/usr/bin/sudo','-N','-n',*argv]
    def record(value):
        with Path(log).open('a')as f:f.write(json.dumps(dict(argv=command,**value),ensure_ascii=False)+'\n')
        Path(log).chmod(0o600)
    record(dict(stage='STARTED',returncode=None))
    try:result=subprocess.run(command,input=data,text=True,capture_output=True,timeout=timeout)
    except BaseException as error:
        def text(value):return value.decode(errors='replace')if isinstance(value,bytes)else(value or'')
        record(dict(stage='NOT_VERIFIED',returncode=None,error=type(error).__name__+': '+str(error),stdout=text(getattr(error,'stdout',None)),stderr=text(getattr(error,'stderr',None))))
        raise
    record(dict(stage='FINISHED',returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
    require(result.returncode==0,'PRIVILEGED_PREPARATION_FAILED: '+result.stderr[-1000:])


def missing_packages():
    packages={package for tool,package in TOOLS.items()if not shutil.which(tool)}
    for file,package in [('/usr/bin/sudo','sudo'),('/usr/bin/taskset','util-linux'),('/usr/bin/timeout','coreutils')]:
        if not Path(file).is_file():packages.add(package)
    for package in DEVEL:
        try:
            r=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${db:Status-Status}',package],capture_output=True,text=True,timeout=10)
            if r.returncode or r.stdout.strip()!='installed':packages.add(package)
        except FileNotFoundError:packages.add(package)
    release=os.uname().release
    if not(Path('/lib/modules')/release/'build/include/uapi/linux/sched.h').is_file():
        require(re.fullmatch('[A-Za-z0-9.+-]+',release),'Invalid kernel release');packages.add('linux-headers-'+release)
    return sorted(packages)


def installation_space(plan):
    total=0;rows=[]
    for name,version in re.findall(r'^Inst (\S+) \((\S+)',plan,re.M):
        metadata=subprocess.check_output(['/usr/bin/apt-cache','show',name+'='+version],text=True,timeout=15)
        installed=re.search(r'^Installed-Size:\s*(\d+)\s*$',metadata,re.M);archive=re.search(r'^Size:\s*(\d+)\s*$',metadata,re.M)
        require(installed is not None and archive is not None,'APT_DISK_REQUIREMENT_NOT_VERIFIED: '+name)
        required=int(installed[1])*1024+int(archive[1]);total+=required;rows.append(dict(package=name,version=version,conservative_bytes=required))
    return dict(packages=rows,estimated_new_bytes=total,required_free_bytes=total+512*1024*1024)


def install(packages, session, approve=None):
    approve=approve or consent
    if not packages:return
    os_release=Path('/etc/os-release').read_text()
    require(re.search(r'^ID=[\"\']?ubuntu[\"\']?$',os_release,re.M),'AUTO_INSTALL_BLOCKED: only Ubuntu repositories are supported')
    require(Path('/usr/bin/sudo').is_file(),'SUDO_MISSING: administrator must provision sudo; no root-shell fallback')
    require(all(re.fullmatch('[a-z0-9][a-z0-9.+-]*',p)for p in packages),'Invalid package name')
    flags=['--no-remove','--no-upgrade','--no-install-recommends']
    cmd=['/usr/bin/apt-get','--simulate',*flags,'install',*packages]
    simulated=subprocess.run(cmd,capture_output=True,text=True,timeout=60,env=dict(os.environ,LC_ALL='C',LANG='C'))
    save(Path(session)/'apt-plan.private.json',dict(command=cmd,returncode=simulated.returncode,stdout=simulated.stdout,stderr=simulated.stderr,persistent=True,automatic_uninstall=False))
    require(simulated.returncode==0,'APT_PLAN_UNAVAILABLE: missing/obsolete repository candidate; no repository or kernel replacement')
    require(not re.search(r'^Remv |^Inst \S+ \[',simulated.stdout,re.M),'APT_UPGRADE_OR_REMOVAL_BLOCKED: review manually')
    space=installation_space(simulated.stdout);save(Path(session)/'apt-disk-plan.private.json',space)
    require(shutil.disk_usage(session).free>=space['required_free_bytes'],'DISK_SPACE_BLOCKED: installation footprint plus 512MiB reserve required')
    approve('Ubuntu 의존성 설치 계획(기존 패키지 업그레이드/제거 금지):\n'+simulated.stdout+'\n설치 항목은 종료 후에도 남습니다. 제거는 기록을 검토해 관리자가 수행해야 합니다. 자동 apt update/저장소 추가는 하지 않습니다.')
    save(Path(session)/'apt-approval.private.json',dict(approved=True,packages=packages,persistent=True,no_automatic_uninstall=True))
    authenticate();root_command(['/usr/bin/apt-get','-y',*flags,'install',*packages],Path(session)/'apt-install.private.jsonl')
    require(shutil.disk_usage(session).free>=512*1024*1024,'DISK_SPACE_BLOCKED after install; no inference')


def allowed_setting(path,value):
    path=str(Path(path).resolve())
    if path=='/sys/devices/system/cpu/intel_pstate/no_turbo':return value in ['0','1']
    if path=='/sys/devices/system/cpu/cpufreq/boost':return value in ['0','1']
    if re.fullmatch(r'/sys/devices/system/cpu/cpufreq/policy\d+/scaling_governor',path):return value in ['performance','powersave','schedutil','ondemand','conservative','userspace']
    if re.fullmatch(r'/sys/devices/system/cpu/cpufreq/policy\d+/energy_performance_preference',path):return value in ['default','performance','balance_performance','balance_power','power']or(bool(re.fullmatch(r'[0-9]{1,3}',value))and 0<=int(value)<=255)
    return False


def snapshot():
    import power_state
    return power_state.capture()


def power_plan(original=None):
    import power_state
    return power_state.plan(original)


def read_setting(row):
    if row['kind']=='profile':return subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,timeout=10).strip()
    require(str(Path(row['path']).resolve())==row['path'],'Unapproved canonical power path')
    return Path(row['path']).read_text().strip()


def write_setting(row,value,log):
    if row['kind']=='profile':
        require(value in ['performance','balanced','power-saver'],'Invalid profile');root_command(['/usr/bin/powerprofilesctl','set',value],log,timeout=30)
        require(subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,timeout=10).strip()==value,'Profile change not verified')
    else:
        require(str(Path(row['path']).resolve())==row['path'] and allowed_setting(row['path'],value),'Unapproved canonical sysfs path/value')
        root_command(['/usr/bin/tee',row['path']],log,data=value+'\n',timeout=30)
        require(Path(row['path']).read_text().strip()==value,'Sysfs change not verified')


def preregister(changes,journal):
    import power_state
    return power_state.preregister(changes,journal)


def apply(changes,journal,approve=None):
    import power_state
    return power_state.apply(changes,journal,approve)


def restore(journal,quiet,*,reauthenticate=False,approve=None):
    import power_state
    return power_state.restore(journal,quiet,reauthenticate,approve)


def pending_recovery(journal,quiet,*,readonly=False,approve=None):
    import power_state
    return power_state.pending(journal,quiet,readonly=readonly,approve=approve)


def audit_restoration(journal):
    import power_state
    return power_state.audit_restoration(journal)
