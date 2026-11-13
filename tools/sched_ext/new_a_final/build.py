#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 E FULL 빌드를 새 디렉터리에서 수행하며 원본 정책은 변경하지 않는다."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import policy_identity

HERE = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    policy_identity.verify()
    if args.output.exists():
        raise ValueError('기존 빌드 디렉터리는 사용하지 않습니다')
    subprocess.run([sys.executable, '-B', str(HERE.parent / 'new_a_4t_p_only/build.py'), '--output', str(args.output.resolve())], check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

if __name__ == '__main__':
    main()
