#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read-only local power recovery review; never authenticates or starts a run."""
import argparse
import json
from pathlib import Path
import re
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools/sched_ext/new_a_bootstrap'))
import auto_system as system
import power_state


def review(workspace,run,current=False):
    system.require(re.fullmatch(r'bootstrap-[A-Za-z0-9-]+',run)is not None,'Invalid run name')
    root=Path(workspace).resolve()/'runs'/run;journal=root/'environment.private.json'
    system.require(journal.is_file(),'RECOVERY_NOT_VERIFIED: requested team run is not present on this PC')
    result=system.audit_restoration(journal)
    report=root/'bootstrap-result.private.json'
    result.update(run=run,historical_run_status=json.loads(report.read_text()).get('status')if report.is_file()else'REPORT_NOT_AVAILABLE',
                  historical_failure_reclassified=False,current_state='NOT_CHECKED',experiment='NOT_RUN')
    if current:
        power_state.require(not power_state.mismatch(system.snapshot(),result['original']),'CURRENT_POWER_OR_BOOT_DIFFERS_FROM_RESTORED_BASELINE')
        result['current_state']='MATCHES_SAVED_ORIGINAL'
    result.pop('original')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workspace',type=Path,default=ROOT/'.autotune-work')
    parser.add_argument('--run',required=True);parser.add_argument('--current',action='store_true');args=parser.parse_args()
    try:result=review(args.workspace,args.run,args.current);code=0
    except (ValueError,OSError,KeyError)as error:result=dict(status='RECOVERY_NOT_VERIFIED',error=str(error),experiment='BLOCKED');code=2
    print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(code)
