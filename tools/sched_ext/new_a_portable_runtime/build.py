#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 봉인 E FULL 빌드와 portable CPU probe를 새 경로에서 준비한다."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent


def build(output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('새 빌드 경로가 필요합니다')
    subprocess.run([sys.executable,'-B',str(HERE.parent/'new_a_final/build.py'),'--output',str(output)],check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    source=HERE.parent/'new_a_portable/cpu_probe.c';binary=output/'cpu-probe'
    command=['cc','-O2','-g','-Wall','-Wextra','-Werror','-std=gnu11','-I'+str(HERE.parent/'new_a'),str(source),'-o',str(binary)]
    result=subprocess.run(command,capture_output=True,text=True)
    (output/'cpu-probe-compile.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise ValueError('CPU probe 컴파일 실패')
    subprocess.run([str(binary),'--help'],check=True,capture_output=True,text=True)
    from records import sha,save
    save(output/'cpu-probe-static.json',dict(status='PASS',source_sha256=sha(source),binary_sha256=sha(binary),command=command,bpf_load='NOT_RUN'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    build(p.parse_args().output)
