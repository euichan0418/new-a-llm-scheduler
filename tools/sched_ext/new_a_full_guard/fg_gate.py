# SPDX-License-Identifier: GPL-2.0
"""Bind B admission to A's actual evidence, exact host and exact artifacts."""
import hashlib
import json
from pathlib import Path
from fg_config import LABELS
import fg_validation as v


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def check(functional,binding):
    root=Path(functional).resolve();record=v.read(root/'functional-result.json')
    v.require(record.get('status')=='FUNCTIONAL_COMPLETE' and record.get('errors')==[] and record.get('is_safety_approval') is False,'A functional result not complete')
    v.require(all(record.get(k)==x for k,x in LABELS.items()),'A labels changed')
    v.require(record['binding']==binding,'A belongs to another host/workload/object preparation')
    v.require(not (root/'STOPPED.json').exists(),'A stopped or failed')
    attempt=v.read(root/'terminal-attempt.json')
    v.require(attempt['attempt_id']==record['attempt_id'] and attempt['mode']=='FUNCTIONAL','A execution attempt absent/mismatch')
    required=['visits/tracking-probe/result.json','visits/tracking-probe/cleanup.json','visits/tracking-probe/scheduler-stats.json',
              'visits/llm-functional/result.json','visits/llm-functional/cleanup.json','visits/llm-functional/scheduler-stats.json','summary.json']
    files=record['evidence_files'];v.require(all(k in files for k in required),'A evidence inventory incomplete')
    for relative,digest in files.items():
        path=(root/relative).resolve();v.require(path.is_relative_to(root) and path.is_file() and sha(path)==digest,'A evidence changed: '+relative)
    v.require(v.read(root/'summary.json')['terminal']['status']=='FUNCTIONAL_COMPLETE','A terminal finalization incomplete')
    v.complete_visit(root/'visits/tracking-probe',dict(kind='tracking-probe',policy='GUARD'),functional=True)
    r,diag=v.complete_visit(root/'visits/llm-functional',dict(kind='llm-functional',policy='GUARD'),functional=True)
    v.require(diag['selected'][0]>0 and diag['selected'][1]>0,'A did not observe both allowed P and E tier activity')
    v.require(record['cpu_priority_scope']=='STATIC_NATIVE_AND_FIXED_MASK_LIVE' and record['live_smt']=='NOT_TESTED_AFFINITY_EXCLUDES_SMT','A scope misrepresented')
    return record


def commit(root,binding,attempt_id):
    root=Path(root)
    v.complete_visit(root/'visits/tracking-probe',dict(kind='tracking-probe',policy='GUARD'),functional=True)
    _,diag=v.complete_visit(root/'visits/llm-functional',dict(kind='llm-functional',policy='GUARD'),functional=True)
    v.require(diag['selected'][0]>0 and diag['selected'][1]>0,'Fixed-mask LLM did not exercise both allowed tiers')
    files={str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file() and
           (p.is_relative_to(root/'visits') or p.is_relative_to(root/'stages') or p.name=='summary.json' or p.name=='terminal-result.json')}
    result=dict(**LABELS,status='FUNCTIONAL_COMPLETE',errors=[],is_safety_approval=False,binding=binding,attempt_id=attempt_id,
                cpu_priority_scope='STATIC_NATIVE_AND_FIXED_MASK_LIVE',live_smt='NOT_TESTED_AFFINITY_EXCLUDES_SMT',
                inference_requests=3,evidence_files=files,guard_scope='Optional BPF elapsed runtime/residence/CPU-change detail NOT_AVAILABLE; mandatory checks retained')
    with (root/'functional-result.json').open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
    return check(root,binding)
