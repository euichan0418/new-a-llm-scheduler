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
    result=subprocess.run(command,input=data,text=True,capture_output=True,timeout=timeout)
    with Path(log).open('a')as f:f.write(json.dumps(dict(argv=command,returncode=result.returncode,stdout=result.stdout,stderr=result.stderr),ensure_ascii=False)+'\n')
    Path(log).chmod(0o600)
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
    return False


def snapshot():
    values={}
    candidates=[Path('/sys/devices/system/cpu/intel_pstate/no_turbo'),Path('/sys/devices/system/cpu/cpufreq/boost'),*Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor')]
    for p in candidates:
        if p.is_file():
            path=str(p.resolve());value=p.read_text().strip()
            if allowed_setting(path,value):values[path]=value
    profile=None
    if Path('/usr/bin/powerprofilesctl').is_file():
        try:profile=subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,stderr=subprocess.DEVNULL,timeout=10).strip()
        except (OSError,subprocess.SubprocessError):pass
    return dict(sysfs=values,profile=profile,package_installation_not_automatically_rolled_back=True)


def power_plan(original=None):
    changes=[];original=original or dict(sysfs={},profile=None)
    turbo=Path('/sys/devices/system/cpu/intel_pstate/no_turbo');target='1'
    if not turbo.is_file():turbo=Path('/sys/devices/system/cpu/cpufreq/boost');target='0'
    require(turbo.is_file(),'TURBO_CONTROL_NOT_VERIFIED; no alternate kernel interface guessed')
    paths=[turbo,*sorted({p.resolve()for p in Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor')})]
    require(len(paths)>1,'Governor interfaces missing')
    for path in paths:
        before=original['sysfs'].get(str(path.resolve()),path.read_text().strip());after=target if path==turbo else'performance'
        require(allowed_setting(path,before)and allowed_setting(path,after),'Unsupported reversible power setting: '+str(path))
        if path!=turbo:require('performance'in path.with_name('scaling_available_governors').read_text().split(),'performance governor unavailable')
        changes.append(dict(kind='sysfs',path=str(path.resolve()),before=before,after=after))
    command='/usr/bin/powerprofilesctl'
    require(Path(command).is_file(),'System powerprofilesctl required')
    current_profile=subprocess.check_output([command,'get'],text=True,timeout=10).strip()
    before=original.get('profile')or current_profile
    require(before in ['performance','balanced','power-saver'],'Original power profile not restorable by this tool')
    if before!='performance'or current_profile!='performance':changes.insert(0,dict(kind='profile',before=before,after='performance'))
    actual_mismatch=current_profile!='performance'or any(Path(r['path']).read_text().strip()!=r['after']for r in changes if r['kind']=='sysfs')
    return changes if actual_mismatch or any(r['before']!=r['after']for r in changes)else []


def write_setting(row,value,log):
    if row['kind']=='profile':
        require(value in ['performance','balanced','power-saver'],'Invalid profile');root_command(['/usr/bin/powerprofilesctl','set',value],log,timeout=30)
        require(subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,timeout=10).strip()==value,'Profile change not verified')
    else:
        require(str(Path(row['path']).resolve())==row['path'] and allowed_setting(row['path'],value),'Unapproved canonical sysfs path/value')
        root_command(['/usr/bin/tee',row['path']],log,data=value+'\n',timeout=30)
        require(Path(row['path']).read_text().strip()==value,'Sysfs change not verified')


def preregister(changes,journal):
    affected=[]
    for i,row in enumerate(changes):
        current=subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,timeout=10).strip()if row['kind']=='profile'else Path(row['path']).read_text().strip()
        if current!=row['before']:affected.append(i)
    save(journal,dict(schema=1,changes=changes,attempted=affected,restored=[],status='INSTALL_SIDE_EFFECTS'if affected else'NOT_APPLIED',automatic_credentials_refresh=False))


def apply(changes, journal, approve=None):
    approve=approve or consent
    state=json.loads(Path(journal).read_text())if Path(journal).exists()else dict(schema=1,changes=changes,attempted=[],restored=[],status='NOT_APPLIED',automatic_credentials_refresh=False)
    require(state['changes']==changes,'Environment plan changed after registration');save(journal,state)
    if not changes:return state
    approve('실험용 전원 설정 변경 및 종료 후 복원 계획:\n'+json.dumps(changes,ensure_ascii=False,indent=2)+'\nAC/온도/커널 검사는 우회하지 않습니다. 인증이 만료되면 복원도 중단될 수 있으며 복구 명령을 제공합니다.')
    state['user_approved']=True;save(journal,state)
    authenticate()
    for i,row in enumerate(changes):
        if i not in state['attempted']:state['attempted'].append(i)
        state['status']='APPLYING';save(journal,state)
        write_setting(row,row['after'],Path(journal).with_suffix('.commands.private.jsonl'))
    state['status']='APPLIED';save(journal,state);return state


def restore(journal, quiet, *, reauthenticate=False, approve=None):
    approve=approve or consent
    journal=Path(journal);state=json.loads(journal.read_text())
    require(state.get('schema')==1 and isinstance(state['changes'],list),'Invalid power journal')
    if not state['attempted']or state['status']=='RESTORED':return
    require(quiet(),'CLEANUP_NOT_VERIFIED: power restoration deferred until server/loader cleanup is confirmed')
    if reauthenticate:
        approve('기록된 원래 전원 설정 복원만 승인합니다. 추론/재시도는 하지 않습니다.');authenticate()
    errors=[]
    order=[i for i in state['attempted']if state['changes'][i]['kind']=='profile']+[i for i in reversed(state['attempted'])if state['changes'][i]['kind']!='profile']
    for i in order:
        if i in state['restored']:continue
        row=state['changes'][i]
        try:
            write_setting(row,row['before'],journal.with_suffix('.restore.private.jsonl'));state['restored'].append(i);save(journal,state)
        except (ValueError,OSError,subprocess.SubprocessError)as error:errors.append(str(error))
    state['status']='RESTORE_REQUIRED'if errors else'RESTORED';state['restore_errors']=errors;save(journal,state)
    require(not errors,'ENVIRONMENT_RESTORE_REQUIRED: '+str(errors))
