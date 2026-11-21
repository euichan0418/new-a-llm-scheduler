#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check saved diagnostic JSON only; not full-session or performance admission."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validation import diagnostics

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--policy', choices=['FULL', 'RUNTIME_LITE'], required=True)
    args = parser.parse_args()
    result = diagnostics(json.loads(args.input.read_text()), args.policy)
    print(json.dumps(dict(scope='DIAGNOSTICS_ONLY_NOT_SESSION_ADMISSION', diagnostic=result), sort_keys=True))
