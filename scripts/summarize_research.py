#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Recalculate public numeric summaries. No sudo, BPF, server or network calls."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ['block','position','condition','compute_threads','batch_threads','http_threads','affinity','prefill_tps','decode_tps','prefill_ms','decode_ms','http_request_ms','process_cpu_time_s','context_switches','migrations','p_mean_busy_pct','e_mean_busy_pct']
METRICS = FIELDS[7:]
PLANS = {
    'asymmetric': [['C88','E88','C48','E48'], ['E48','C48','E88','C88'], ['C48','E48','C88','E88'], ['E88','C88','E48','C48'], ['C88','E88','E48','C48'], ['C48','E48','E88','C88']],
    'runtime-lite': [['C48','E48','L48'], ['L48','E48','C48']],
}


def load(path, campaign):
    with Path(path).open(newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError('Only the reviewed public numeric schema is allowed')
        rows = list(reader)
    expected = {(b, p, c) for b, order in enumerate(PLANS[campaign], 1) for p, c in enumerate(order, 1)}
    observed = set()
    for row in rows:
        for key in ['block','position','compute_threads','batch_threads','http_threads']:
            row[key] = int(row[key])
        identity = (row['block'], row['position'], row['condition'])
        if identity in observed or identity not in expected:
            raise ValueError('Duplicate or unexpected block/slot')
        observed.add(identity)
        if (row['compute_threads'], row['batch_threads'], row['http_threads'], row['affinity']) != (8 if row['condition'].endswith('88') else 4, 8, 2, '0,2,4,6,8,9,10,11'):
            raise ValueError('Thread/affinity contract changed')
        for key in METRICS:
            row[key] = float(row[key])
            if not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError('Invalid numeric metric')
        if row['prefill_ms'] <= 0 or row['decode_ms'] <= 0:
            raise ValueError('Positive phase times required')
        for rate, duration, tokens in [('prefill_tps','prefill_ms',920), ('decode_tps','decode_ms',128)]:
            if not math.isclose(row[rate], tokens * 1000 / row[duration], rel_tol=1e-9):
                raise ValueError('Token/timing arithmetic changed')
    if observed != expected:
        raise ValueError('Incomplete campaign')
    return rows


def describe(values):
    return dict(n=len(values), mean=statistics.mean(values), sample_sd=statistics.stdev(values), median=statistics.median(values), minimum=min(values), maximum=max(values))


def summarize(rows, campaign):
    cells = sorted({r['condition'] for r in rows})
    stats = {c: {m: describe([r[m] for r in rows if r['condition'] == c]) for m in METRICS} for c in cells}
    comparisons = [('E48','E88'),('E48','C88'),('E48','C48'),('C48','C88')] if campaign == 'asymmetric' else [('L48','E48'),('L48','C48'),('E48','C48')]
    result = {}
    for a, b in comparisons:
        pairs = []
        for block in range(1, len(PLANS[campaign]) + 1):
            x = next(r for r in rows if r['condition'] == a and r['block'] == block)
            y = next(r for r in rows if r['condition'] == b and r['block'] == block)
            pairs.append(dict(block=block, difference={m:x[m]-y[m] for m in METRICS}, change_pct={m:100*(x[m]/y[m]-1) if y[m] else None for m in METRICS}))
        diffs = [p['difference']['http_request_ms'] for p in pairs]
        mean = statistics.mean(diffs)
        ci = None
        if len(diffs) == 6:
            # Student t, df=5. Exploratory n=2 is not assigned a superiority claim.
            half = 2.570581835636305 * statistics.stdev(diffs) / math.sqrt(6)
            ci = [mean-half, mean+half]
        result[a+'_vs_'+b] = dict(mean_difference={m:stats[a][m]['mean']-stats[b][m]['mean'] for m in METRICS}, mean_change_pct={m:100*(stats[a][m]['mean']/stats[b][m]['mean']-1) if stats[b][m]['mean'] else None for m in METRICS}, pairs=pairs, http_lower_count=sum(x<0 for x in diffs), cpu_lower_count=sum(p['difference']['process_cpu_time_s']<0 for p in pairs), paired_http_95ci_ms=ci)
    return dict(campaign=campaign, sample_count=len(rows), statistics=stats, comparisons=result, pooled_with_other_campaigns=False, process_cpu_scope='WHOLE_PROCESS_CPU_CLOCK_REQUEST_BRACKET', cs_migration_scope='REQUEST_WINDOW_STABLE_BOUNDARY_TIDS_ONLY', busy_scope='SYSTEM_WIDE_NOT_SERVER_RESIDENCE', ci_assumptions='Paired t interval for n=6; approximate independent blocks/normal differences; no multiple-comparison adjustment; not an equivalence test', scheduler_superiority='NOT_PROVEN', energy='NOT_MEASURED_RAPL_OFF', strict_acceptance='FAILED', safety_verdict='FAIL', energy_review='OVERHEAD_REVIEW_REQUIRED')


def summaries():
    return {name:summarize(load(ROOT/'results'/file,name),name) for name,file in [('asymmetric','asymmetric-24.csv'),('runtime-lite','runtime-lite-6.csv')]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = summaries()
    if args.check:
        for name, value in result.items():
            expected = json.loads((ROOT/'results'/(name+'-summary.json')).read_text())
            if value != expected:
                raise ValueError('Published summary differs from public numeric inputs: '+name)
        print('PUBLIC_NUMERIC_RECALCULATION=PASS; private evidence is not revalidated by this script')
    else:
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
