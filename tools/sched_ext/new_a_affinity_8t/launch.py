#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""봉인 검사 뒤 새 실행기를 시작한다. 별도 승인 문구는 요구하지 않는다."""
import argparse
from pathlib import Path
import runpy
import sys
from records import seal_check

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    seal_check(root)
    sys.argv = [str(root / 'campaign.py')] + ([] if args.preflight else ['--execute'])
    runpy.run_path(str(root / 'campaign.py'), run_name='__main__')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('준비물 검사에서 중단했습니다: ' + type(error).__name__ + ': ' + str(error))
