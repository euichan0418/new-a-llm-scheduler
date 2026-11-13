# SPDX-License-Identifier: GPL-2.0
"""외부 추적 없이 실제 argv/env와 libgomp 팀 크기를 검증한다."""
import os
from pathlib import Path
import re
from records import read, require, save
import study

PATTERN=re.compile(r'NEWA_TEAM pid=(\d+) team=(\d+) index=(\d+) native=(\S+)')
MAX_LINES=256


def team(text,threads,pid):
    rows=[dict(pid=int(m[1]),team=int(m[2]),index=int(m[3]),native=m[4])for m in PATTERN.finditer(text)]
    require(len(rows)==text.count('NEWA_TEAM') and 0<len(rows)<=MAX_LINES, 'OpenMP 표시 증거가 누락/손상됐거나 상한을 넘었습니다')
    require(all(x['pid']==pid and x['team']==threads and 0<=x['index']<threads for x in rows), '실제 OpenMP 팀 크기/프로세스가 계획과 다릅니다')
    require({x['index']for x in rows}==set(range(threads)), '실제 OpenMP 팀 구성원 인덱스가 불완전합니다')
    return dict(source='LIBGOMP_OMP_DISPLAY_AFFINITY',expected_team_size=threads,observed_team_sizes=sorted({x['team']for x in rows}),
                team_indexes=sorted({x['index']for x in rows}),records=len(rows),native_handles=sorted({x['native']for x in rows}),
                native_handle_is_os_tid=False,phase_team_sizes='NOT_AVAILABLE',active_compute_workers='NOT_AVAILABLE',
                limitation='팀 크기와 구성원 표시는 실제 관측이다. 각 작업자가 유효 연산을 수행한 시간이나 OSTID/phase 대응은 증명하지 않는다.')


def live_contract(path,item,setup,threads):
    pid=item.server['pid']
    argv,env=study.server_config(setup,threads)
    actual=(Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0'))
    require(actual==argv, '실제 서버 cmdline의 Compute/Batch/HTTP 설정이 다릅니다')
    raw=Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')
    inherited={x.split(b'=',1)[0].decode():x.split(b'=',1)[1].decode()for x in raw if b'='in x}
    keys=['OMP_NUM_THREADS','OMP_DYNAMIC',*study.omp_options()]
    require(all(inherited.get(k)==env[k] for k in keys), '실제 OpenMP 환경이 계획과 다릅니다')
    require(not any(k in inherited for k in ['GOMP_CPU_AFFINITY','OMP_PLACES','OMP_PROC_BIND','OMP_THREAD_LIMIT','OMP_NUM_TEAMS']), '계획에 없는 OpenMP 배치/제한 환경이 있습니다')
    value=dict(server_identity=item.server,argv=actual,openmp={k:inherited[k]for k in keys},
               compute_threads=threads,batch_threads=threads,http_threads=2,entire_server_thread_limit=False)
    save(Path(path)/'thread-contract.json',value)
    return value


def validate_contract(value,expected,identity):
    n=expected['compute_threads']
    require(n==expected['batch_threads'] and expected['http_threads']==2 and value['server_identity']==identity, '서버 스레드 계약 또는 Identity가 다릅니다')
    require(value['compute_threads']==value['batch_threads']==n and value['http_threads']==2 and value['entire_server_thread_limit']is False, '계산 스레드 수를 전체 서버 제한과 혼동했거나 계약이 변경됐습니다')
    argv=value['argv']
    for option,want in [('--threads',n),('--threads-batch',n),('--threads-http',2)]:
        require(argv.count(option)==1 and argv[argv.index(option)+1]==str(want), '실제 argv 스레드 수가 다릅니다: '+option)
    require(value['openmp']==dict(OMP_NUM_THREADS=str(n),OMP_DYNAMIC='FALSE',**study.omp_options()), 'OpenMP 계약이 변경됐습니다')
