# SPDX-License-Identifier: GPL-2.0
"""통계를 켜지 않고 외부 stats FD 및 연결된 E 프로그램의 OFF 증거를 검사한다."""
import json
from pathlib import Path
import re
import subprocess
from records import read,save,require

CALLBACKS={'select_cpu':'llama_select_cpu','enqueue':'llama_enqueue','dispatch':'llama_dispatch','running':'llama_running','stopping':'llama_stopping'}


def targets(dump):
    data=[];btfs=[]
    def walk(value):
        if isinstance(value,dict):
            if value.get('name')=='llama_new_a' and 'select_cpu'in value:data.append(value)
            info=value.get('bpf_map_info',{})
            if info.get('name')=='llama_ops' and info.get('type')==26:btfs.append(info['btf_id'])
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    walk(dump)
    require(len(data)==len(btfs)==1,'활성 E의 struct_ops 및 BTF를 고유하게 연결할 수 없습니다')
    ids={}
    for name,symbol in CALLBACKS.items():
        match=re.search(r'\b'+re.escape(symbol)+r'/prog_id:(\d+)$',str(data[0].get(name,'')))
        require(match is not None,'실제 callback Program ID를 확인할 수 없습니다: '+name)
        ids[name]=int(match[1])
    require(len(set(ids.values()))==5 and all(v>0 for v in ids.values()),'실제 callback Program ID가 중복/누락됐습니다')
    return dict(ids=ids,btf_id=btfs[0])


def validate(value,policy,binding=None):
    require(all(type(value.get(k))is int for k in ['sysctl','stats_fd_references','fds_scanned']),'통계 OFF 전역 카운터 형식이 잘못됐습니다')
    require(value.get('read_only')is True and value.get('enable_stats_called')is False and value.get('sysctl')==0 and value.get('stats_fd_references')==0 and value.get('fds_scanned',0)>0,'BPF Runtime Statistics OFF 증거가 불완전합니다')
    programs=value.get('programs')
    require(isinstance(programs,list),'프로그램 정보 배열이 없습니다')
    if policy=='C':require(programs==[],'Linux 조건에 E 프로그램 통계가 섞였습니다');return
    require(policy=='E' and binding is not None and len(programs)==5,'E 통계 OFF 대상이 불완전합니다')
    for row,(name,symbol)in zip(programs,CALLBACKS.items()):
        require(all(type(row.get(k))is int for k in ['id','btf_id','run_cnt','run_time_ns']),'프로그램 카운터/Identity 형식이 잘못됐습니다')
        require(row.get('id')==binding['ids'][name] and row.get('name')==symbol[:15] and row.get('struct_ops_type')is True and row.get('btf_id')==binding['btf_id'],'연결된 E 프로그램 Identity가 다릅니다')
        require(re.fullmatch('[0-9a-f]{16}',row.get('tag',''))is not None and row.get('run_cnt')==row.get('run_time_ns')==0,'외부 Runtime Statistics 활성화 흔적 또는 불완전한 정보입니다')


def snapshot(path,policy,stage,binary):
    binding=targets(read(Path(path)/'struct-ops-dump.json'))if policy=='E' else None
    args=[str(x)for x in binding['ids'].values()]if binding else ['--global']
    command=['sudo','-n','taskset','-c','15',str(binary),*args]
    proc=subprocess.run(command,capture_output=True,text=True,check=False,timeout=15)
    save(Path(path)/('stats-off-'+stage+'-command.json'),dict(argv=command,returncode=proc.returncode,stdout=proc.stdout,stderr=proc.stderr,enable_stats_called=False))
    require(proc.returncode==0,'읽기 전용 Runtime Statistics OFF 검사가 실패했습니다. 다음 세션을 진행하지 않습니다')
    value=json.loads(proc.stdout)
    validate(value,policy,binding)
    save(Path(path)/('stats-off-'+stage+'.json'),value)
    return value


def verify_visit(path,policy):
    path=Path(path)
    binding=targets(read(path/'struct-ops-dump.json'))if policy=='E'else None
    rows=[]
    for stage in ['attached','before-measured','after-measured']:
        row=read(path/('stats-off-'+stage+'.json'));validate(row,policy,binding);rows.append(row)
        command=read(path/('stats-off-'+stage+'-command.json'))
        require(command['returncode']==0 and command['enable_stats_called']is False,'통계 OFF 검사 종료/활성화 상태가 다릅니다')
        require(json.loads(command['stdout'])==row,'통계 OFF 원시 출력과 저장 결과가 다릅니다')
    require(all(x['programs']==rows[0]['programs']for x in rows),'통계 OFF 검사 사이 프로그램 Identity가 변경됐습니다')
