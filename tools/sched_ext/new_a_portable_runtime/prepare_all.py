#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""두 실행 단계를 비특권으로 준비한다. sudo/BPF/추론을 실행하지 않는다."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();root=args.output.resolve()
    if root.exists():raise ValueError('기존 실행 디렉터리는 재사용하지 않습니다')
    root.mkdir(parents=True)
    subprocess.run([sys.executable,'-B',str(HERE/'build.py'),'--output',str(root/'build')],check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    import prepare
    release=root/'release';release.mkdir()
    command=[sys.executable,'-B',str(HERE.parent/'new_a_portable/config.py'),'--probe',str(root/'build/cpu-probe'),
             '--server-config',str(prepare.SOURCE/'setup.json'),'--output',str(release/'configuration')]
    with (root/'configuration-generation.log').open('x')as f:
        subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    for name,mode in [('functional','FUNCTIONAL'),('regression','PERFORMANCE')]:
        stage=release/name;stage.mkdir();(stage/'build').symlink_to(root/'build',target_is_directory=True)
        prepare.make_pending(stage,mode,release/'functional/comparison')
    current=prepare.parent().live()
    try:prepare.parent().power_gate(current)
    except ValueError as error:
        print('실행 Profile 대기: '+str(error));return
    for name in ['functional','regression']:prepare.seal(release/name)
    print('수동/자동 기능 및 회귀 준비 완료. 실제 실행은 하지 않았습니다.')


if __name__=='__main__':main()
