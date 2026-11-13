#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""안전 조건 확인 뒤 새 프로필을 만들고 4T 기준선만 실행한다."""
import argparse
import importlib.util
from pathlib import Path
import runpy
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    if not (root/'pending.json').exists():
        raise ValueError('일회성 실행 디렉터리의 run-terminal.sh를 사용하세요')
    import json
    pending=json.loads((root/'pending.json').read_text())
    code=Path(pending['repo'])/'tools/sched_ext/new_a_4t_baseline'
    sys.path.insert(0,str(code))
    import prepare
    prepare.verify_pending(root)
    if not (root/'comparison/seal.json').exists():prepare.seal(root)
    sys.path.insert(0,str(root/'comparison'))
    from records import seal_check
    seal_check(root/'comparison')
    sys.argv=[str(root/'comparison/campaign.py')]+([]if args.preflight else ['--execute'])
    runpy.run_path(str(root/'comparison/campaign.py'),run_name='__main__')


if __name__=='__main__':
    try:main()
    except Exception as error:raise SystemExit('실행 준비 검사에서 중단: '+type(error).__name__+': '+str(error))
