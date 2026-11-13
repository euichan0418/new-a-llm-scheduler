#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 E FULL 재컴파일 및 읽기 전용 통계 OFF 보조 코드 정적 빌드."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from records import read,save,sha,require

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]


def build(output):
    output=Path(output).resolve()
    subprocess.run([sys.executable,'-B',str(REPO/'tools/sched_ext/new_a_affinity_8t/build.py'),'--output',str(output)],check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    binary=output/'stats-off'
    argv=['cc','-O2','-g','-Wall','-Wextra','-Werror','-std=gnu11',str(HERE/'stats_off.c'),'-o',str(binary),'-lbpf']
    p=subprocess.run(argv,capture_output=True,text=True)
    (output/'stats-off-compile.log').write_text(p.stdout+p.stderr)
    require(p.returncode==0,'통계 OFF 보조 코드 컴파일 실패')
    help_result=subprocess.run([str(binary),'--help'],capture_output=True,text=True,check=True)
    source=(HERE/'stats_off.c').read_text()
    require('bpf_enable_stats'not in source and 'BPF_ENABLE_STATS'not in source,'통계 활성화 코드가 포함됐습니다')
    save(output/'stats-off-static.json',dict(status='PASS',binary=str(binary),sha256=sha(binary),source=str(HERE/'stats_off.c'),source_sha256=sha(HERE/'stats_off.c'),command=argv,help=help_result.stdout,
                                           actual_global_fd_scan='NOT_RUN',actual_program_info='NOT_RUN',bpf_load='NOT_RUN',enable_stats='NOT_IMPLEMENTED'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    build(parser.parse_args().output)
