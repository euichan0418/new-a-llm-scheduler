#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""기존 캠페인을 읽기 전용으로 재검증하고 서로 합산하지 않는다."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CAMPAIGNS = {
    '8T_C_E_V2': ('new-a-v2/20261108T122639Z-compare-c-e-v2-6rounds-8928a7d9-9cb23d', 'analyze'),
    '8T_FULL_Guard': ('new-a-full-guard/20261108T135713Z-615008-B-6rounds', 'fg_analyze'),
    '8T_V3_overhead': ('new-a-v3-overhead/20261110T121524Z-c5a870-functional-review/B-overhead', 'study'),
    '8T_V3_recheck': ('new-a-v3-recheck/20261110T133232Z-d3a801-4sessions/comparison', 'study'),
    '4T_8T_baseline': ('new-a-4t-baseline/20261111T061949Z-c0cc42-thread-factor/comparison', 'study'),
    '4T_affinity': ('new-a-4t-p-only/20261111T070108Z-6209fd-affinity-factor/comparison', 'study'),
    '4T_V4_functional': ('new-a-v4-p-only/20261113T083738Z-boot-profile-refresh/functional/comparison', 'study'),
    '4T_C_E_V4': ('new-a-v4-p-only/20261113T083738Z-boot-profile-refresh/performance/comparison', 'study'),
    '8T_affinity': ('new-a-v4-research/20261110T145453Z-287918-affinity-first/comparison', 'study'),
    'runtime_cost': ('new-a-runtime-cost/20261110T162732Z-9b1ee5-runtime-statistics/ready/comparison', 'study'),
    'handoff': ('new-a-handoff/20261110T182913Z-2733dc-select-return/release/comparison', 'study'),
    'resource_overhead': ('new-a-resource-energy/20261113T091658Z-three-config/overhead/comparison', 'study'),
}


def check_requests(root, rows):
    count = 0
    for row in rows:
        if row.get('visit_status') != 'COMPLETE':
            raise ValueError('불완전한 세션: ' + str(root))
        path = root / row['relative_dir']
        cleanup = json.loads((path / 'cleanup.json').read_text())
        if cleanup != dict(status='PASS', errors=[], sched_ext='disabled'):
            raise ValueError('cleanup 실패: ' + str(path))
        for role in row['requests_completed']:
            raw = (path / (role + '-response.json')).read_bytes()
            data = json.loads(raw)
            client = json.loads((path / (role + '-client.json')).read_text())
            t = data['timings']
            if client['http_status'] != 200 or client['response_sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError('HTTP/응답 해시 오류: ' + str(path))
            if t['prompt_n'] != 920 or t['predicted_n'] != 128 or t['cache_n'] != 0:
                raise ValueError('토큰/cache 오류: ' + str(path))
            if not all(math.isfinite(t[k]) and t[k] > 0 for k in ['prompt_ms', 'predicted_ms']):
                raise ValueError('측정 타이머 오류: ' + str(path))
            if not math.isclose(t['prompt_per_second'], 920000 / t['prompt_ms'], rel_tol=1e-10):
                raise ValueError('Prefill 계산 오류: ' + str(path))
            if not math.isclose(t['predicted_per_second'], 128000 / t['predicted_ms'], rel_tol=1e-10):
                raise ValueError('Decode 계산 오류: ' + str(path))
            count += 1
    return count


def review():
    output = {}
    for name, (relative, module) in CAMPAIGNS.items():
        root = REPO / 'benchmark-results/season2' / relative
        code = """import importlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
sys.path.append(str(Path(sys.argv[3])/'tools/sched_ext/new_a_analysis'))
sys.path.append(str(Path(sys.argv[3])/'tools/sched_ext/new_a_full_guard'))
module=importlib.import_module(sys.argv[2])
fresh=json.loads(json.dumps(module.summarize(root)))
old=json.loads((root/'summary.json').read_text())
assert fresh=={k:v for k,v in old.items() if k!='terminal'}, '기존 summary와 재계산 불일치'
assert fresh['complete'], '불완전 캠페인'
print(json.dumps(fresh))
"""
        fresh = json.loads(subprocess.check_output([sys.executable, '-B', '-c', code, str(root), module, str(REPO)], text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1')))
        requests = check_requests(root, fresh['visits'])
        stats = fresh.get('statistics') or fresh.get('balanced_complete_rounds')
        output[name] = dict(source=str(root.relative_to(REPO)), summary_sha256=hashlib.sha256((root / 'summary.json').read_bytes()).hexdigest(),
                            saved_summary_equal=True, sessions=len(fresh['visits']), verified_requests=requests, statistics=stats,
                            paired=fresh.get('paired', fresh.get('paired_comparisons')), retention=fresh.get('retention'),
                            overhead_review=fresh.get('overhead_review'), strict_acceptance=fresh['strict_acceptance'], safety_verdict=fresh['safety_verdict'])
        print(name + ': 원시 재검증 PASS; 요청=' + str(requests), file=sys.stderr)
    return dict(campaigns=output, pooled=False, new_measurement='NOT_RUN')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('기존 분석 파일은 덮어쓰지 않습니다')
    result = review()
    with args.output.open('x') as f:
        json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')
