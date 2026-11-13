# SPDX-License-Identifier: GPL-2.0
"""기존 연구 도구에서 필요한 지원 함수만 보존한 공개 사본이다."""
from pathlib import Path
import subprocess
import sys
import shutil
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
CPUS={0,2,4,6,*range(8,15)}


def modules():
    sys.path.insert(0,str(REPO/'tools/sched_ext/new_a'))
    import topology,common,host_profile
    return topology,common,host_profile


def live():
    _,_,hp=modules();caps=hp.capabilities()
    return dict(host=hp.kernel_identity(),capabilities=caps,
                no_turbo=Path('/sys/devices/system/cpu/intel_pstate/no_turbo').read_text().strip(),
                power_profile=subprocess.check_output(['powerprofilesctl','get'],text=True).strip(),
                sched_ext=Path('/sys/kernel/sched_ext/state').read_text().strip(),
                affinity=sorted(os.sched_getaffinity(0)),free_bytes=shutil.disk_usage(REPO).free)


def power_gate(value):
    rows=value['capabilities']['power_supply']['entries']
    require(any(x.get('type')=='Mains' and x.get('online')=='1' for x in rows.values()),'WAITING_FOR_AC_PROFILE: connect AC; no Host Profile was generated')
    require(value['no_turbo']=='1','Turbo must already be OFF; this tool does not change it')
    require(value['power_profile']=='performance','performance must already be active; this tool does not change it')
    require(value['sched_ext']=='disabled','sched_ext must be disabled')
    require(value['free_bytes']>=512*1024*1024,'Less than 512 MiB free')


def physical_gate(fresh, reference):
    require(fresh['online_cpus']==reference['online_cpus'] and fresh['allowed_cpus']==reference['allowed_cpus'],'CPU online/caller allocation changed')
    require(fresh['model']==reference['model'] and fresh['vendor']==reference['vendor'],'CPU identity changed')
    for key,old in reference['cpus'].items():
        require(all(fresh['cpus'][key][k]==old[k] for k in ['core','package','die','siblings_all']),'Physical CPU topology changed')
