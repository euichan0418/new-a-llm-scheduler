# SPDX-License-Identifier: GPL-2.0
"""Correlate diagnostic evidence without changing scheduler acceptance."""
import argparse
import json
import os
from pathlib import Path
import time


def clock_snapshot(pid):
    root = Path('/proc') / str(pid)
    errors = []
    try:
        namespace = os.readlink(root / 'ns/time')
        offsets_text = (root / 'timens_offsets').read_text()
        offsets = {name: int(sec) * 1_000_000_000 + int(ns)
                   for name, sec, ns in (line.split() for line in offsets_text.splitlines())}
        offset = offsets['monotonic']
    except (OSError, ValueError, KeyError) as error:
        namespace, offsets_text, offset = None, None, None
        errors.append(str(error))
    return dict(observed_pid=pid, recorded_by_pid=os.getpid(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                time_namespace=namespace, timens_offsets=offsets_text, monotonic_offset_ns=offset,
                user_clock='CLOCK_MONOTONIC', kernel_clock='bpf_ktime_get_ns',
                resolution_ns=max(1, round(time.clock_getres(time.CLOCK_MONOTONIC) * 1e9)),
                errors=errors)


def parse_traces(text):
    return [json.loads(line[len('affinity_trace '):]) for line in text.splitlines() if line.startswith('affinity_trace ')]


def mask_complete(stats, event, name):
    limit = stats.get('mask_cpu_limit')
    cpus = event.get(name + '_cpus', [])
    return (stats.get('event_schema') == 2 and type(limit) is int and 0 < limit <= 1024
            and event.get('mask_bits') == limit and event.get(name + '_complete') is True
            and event.get(name + '_read_error') == 0
            and all(type(cpu) is int and 0 <= cpu < limit for cpu in cpus)
            and cpus == sorted(set(cpus)) and len(cpus) == event.get(name + '_weight'))


def analyze(stats, traces, clocks):
    issues = []
    before, after = clocks.get('before', {}), clocks.get('after', {})
    clock_fields = ('boot_id', 'time_namespace', 'monotonic_offset_ns', 'user_clock', 'kernel_clock')
    clock_ok = (all(before.get(k) is not None and before.get(k) == after.get(k) for k in clock_fields)
                and not before.get('errors') and not after.get('errors')
                and before.get('user_clock') == 'CLOCK_MONOTONIC' and before.get('kernel_clock') == 'bpf_ktime_get_ns')
    if not clock_ok:
        issues.append('Missing or changed boot/time namespace/monotonic offset; cross-domain ordering is unavailable')
    offset = before.get('monotonic_offset_ns', 0) if clock_ok else 0
    resolution = max(before.get('resolution_ns', 1), after.get('resolution_ns', 1))
    rows, events = stats.get('cpus', []), stats.get('events', [])
    mask_count = sum(r['mask_violations'] for r in rows)
    tracking_count = sum(r['tracking_failures'] for r in rows)
    dropped = sum(r.get('events_dropped', 0) for r in rows)
    if stats.get('event_schema') not in (1, 2) or stats.get('events_export_error') != 0:
        issues.append('Missing event schema or event export failed')
    limit = stats.get('mask_cpu_limit')
    if stats.get('event_schema') != 2 or type(limit) is not int or not 0 < limit <= 1024:
        issues.append('Missing schema-2 runtime CPU mask bound')
    if dropped:
        issues.append(f'{dropped} kernel events were dropped')
    if sum(r.get('events_attempted', 0) for r in rows) != len(events) + dropped:
        issues.append('Kernel event accounting is incomplete')
    if sum(e['kind'] == 1 for e in events) != mask_count or sum(e['kind'] == 2 for e in events) != tracking_count:
        issues.append('Aggregate failures and retained failure events disagree')
    for event in events:
        names = ('allowed', 'regular', 'requested') if event['kind'] == 3 else ('allowed', 'regular')
        if not all(mask_complete(stats, event, name) for name in names):
            issues.append('A retained event has incomplete mask bounds/read/weight evidence')
    event_keys = [(e['callback_cpu'], e.get('sequence')) for e in events]
    if len(event_keys) != len(set(event_keys)):
        issues.append('Duplicate per-CPU event identities')
    for row in rows:
        retained = [e for e in events if e['callback_cpu'] == row.get('cpu')]
        if len(retained) + row.get('events_dropped', 0) != row.get('events_attempted', 0):
            issues.append('Per-CPU event accounting is incomplete')
        sequences = sorted(e.get('sequence', -1) for e in retained)
        if sequences != list(range(len(retained))):
            issues.append('Per-CPU event sequence has gaps')
    valid_callbacks = {1: (1,), 2: (1, 2), 3: (3,)}
    if any(e.get('callback') not in valid_callbacks.get(e.get('kind'), ()) for e in events):
        issues.append('Unknown event kind or callback')
    if tracking_count:
        issues.append('Task tracking failed; runtime evidence is incomplete')
    if len(traces) != 2 or len({(t['tgid'], t['tid']) for t in traces}) != len(traces):
        issues.append('Expected two distinct worker affinity traces')
    timeline = []
    for t in traces:
        if (t['clock'] != 'CLOCK_MONOTONIC' or not t['request_begin_ns'] <= t['request_return_ns'] <= t['post_begin_ns'] <= t['post_end_ns']):
            issues.append(f"Invalid userspace clock/order for TID {t['tid']}")
        for role, field in (('affinity_request', 'request_begin_ns'), ('affinity_return', 'request_return_ns'), ('post_observation', 'post_end_ns')):
            timeline.append(dict(source='userspace', kind=role, tid=t['tid'],
                                 timestamp_ns=t[field] - offset if clock_ok else None, user_timestamp_ns=t[field]))
    findings = []
    for e in events:
        timeline.append(dict(source='kernel', kind=e['kind'], tid=e['tid'], timestamp_ns=e['timestamp_ns'],
                             callback=e['callback'], callback_cpu=e['callback_cpu'], task_cpu=e['task_cpu']))
        if e['kind'] != 1:
            continue
        finding = dict(event=e, category='inconclusive', reason='No matching completed affinity operation')
        complete = mask_complete(stats, e, 'allowed') and mask_complete(stats, e, 'regular')
        if not complete:
            finding['reason'] = 'Mask bounds/read/weight evidence is incomplete or inconsistent'
        elif bool(e['callback_allowed']) != (e['callback_cpu'] in e['allowed_cpus']) or bool(e['task_allowed']) != (e['task_cpu'] in e['allowed_cpus']):
            finding['reason'] = 'CPU membership changed or is inconsistent within the sequential snapshot'
        elif e['callback_cpu'] in e['allowed_cpus']:
            finding['reason'] = 'Trigger and snapshot disagree; mask may have changed during capture'
        elif e['callback_cpu'] != e['task_cpu'] and e['task_cpu'] in e['allowed_cpus']:
            finding.update(category='observation_error_supported',
                           reason='The original counter tested callback CPU, but the associated task CPU is allowed')
        elif clock_ok:
            candidates = [t for t in traces if t['tgid'] == e['tgid'] and t['tid'] == e['tid']]
            for t in candidates:
                begin, end = t['request_begin_ns'] - offset, t['request_return_ns'] - offset
                stamp = e['timestamp_ns']
                requested = t['mask_requested']
                settled = (t['syscall_result'] == 0 and t['errno'] == 0
                           and not t['before_mask_result'] and not t['after_mask_result']
                           and requested and requested == sorted(set(requested))
                           and set(requested) < set(t['mask_before'])
                           and t['mask_after'] == requested and t['cpu_after'] in requested
                           and t['observations'] == 160 and not t['violations'])
                identity = (e.get('actor_tgid') == t['tgid'] and e.get('actor_tid') == t['tid']
                            and e['callback_cpu'] == e['task_cpu'] == t['cpu_before']
                            and e['task_cpu'] in t['mask_before'] and e['task_cpu'] not in requested
                            and e.get('on_cpu') == 1 and e.get('on_rq') == 1
                            and e.get('nr_cpus_allowed') == len(requested)
                            and e.get('task_start_time', 0) > 0)
                changes = [c for c in events if c['kind'] == 3 and c['tid'] == e['tid'] and c['tgid'] == e['tgid']]
                matching_callback = (len(changes) == 1 and all(
                    begin + resolution < c['timestamp_ns'] < stamp - resolution
                    and c.get('task_start_time') == e.get('task_start_time')
                    and c.get('actor_tgid') == t['tgid'] and c.get('actor_tid') == t['tid']
                    and c['callback_cpu'] == c['task_cpu'] == e['task_cpu']
                    and c.get('callback_allowed') == 0 and c.get('task_allowed') == 0
                    and c.get('on_cpu') == 1 and c.get('on_rq') == 1
                    and c.get('nr_cpus_allowed') == len(requested)
                    and all(mask_complete(stats, c, name) and c[name + '_cpus'] == requested
                            for name in ('allowed', 'regular', 'requested'))
                    for c in changes))
                if abs(stamp - begin) <= resolution or abs(stamp - end) <= resolution:
                    finding['reason'] = 'Event is too close to a userspace timestamp boundary'
                elif begin < stamp < end and settled and identity and matching_callback and e['allowed_cpus'] == e['regular_cpus'] == requested:
                    finding.update(category='normal_transition_supported',
                                   reason='Complete matching task/mask-change evidence and contraction; all recorded post-return observations respect the requested mask')
                elif stamp > end:
                    finding['reason'] = 'Mismatch was recorded after syscall return; correlate actual execution before blaming policy'
        findings.append(finding)
    for t in traces:
        post_bad = (t['syscall_result'] == 0 and t['errno'] == 0 and not t['after_mask_result']
                    and t['cpu_after'] >= 0 and t['mask_after']
                    and t['cpu_after'] not in t['mask_after'])
        sampled_bad = (t['syscall_result'] == 0 and t['errno'] == 0 and t['violations'] and t['first_bad_cpu'] >= 0
                       and t['first_bad_iteration'] >= 80 and not t['first_bad_mask_result']
                       and t['first_bad_mask'] and t['first_bad_cpu'] not in t['first_bad_mask'])
        if post_bad or sampled_bad:
            findings.append(dict(category='actual_mask_violation_observed', user_trace=t,
                                 reason='Userspace observed a disallowed CPU after the successful affinity call; selector attribution requires further evidence'))
        elif t['mask_after'] != t['mask_requested'] or t.get('observations') != 160 or t['syscall_result'] or t['before_mask_result'] or t['after_mask_result'] or t['cpu_after'] < 0 or t['violations']:
            issues.append(f"Userspace request/observation failed for TID {t['tid']}")
    actual_violation = any(f['category'] == 'actual_mask_violation_observed' for f in findings)
    incomplete = bool(issues) or any(f['category'] == 'inconclusive' for f in findings)
    if incomplete or actual_violation:
        for finding in findings:
            if finding['category'] == 'normal_transition_supported':
                finding.update(category='inconclusive', reason='Incomplete or conflicting evidence prevents a normal-transition classification')
    categories = sorted({f['category'] for f in findings})
    conclusion = ('actual_mask_violation_observed' if actual_violation else
                  'inconclusive' if issues or 'inconclusive' in categories else
                  ','.join(categories) if categories else 'no_violation_observed')
    return dict(conclusion=conclusion, issues=issues, findings=findings,
                safety_verdict='FAIL' if mask_count or tracking_count or actual_violation or any(e['kind'] in (1, 2) for e in events) else
                               'INCONCLUSIVE' if conclusion == 'inconclusive' else 'NO_VIOLATION_OBSERVED',
                mask_violations=mask_count, tracking_failures=tracking_count, events_dropped=dropped,
                clock_comparable=clock_ok, clock_offset_ns=offset if clock_ok else None,
                strict_counter_gate='FAIL' if mask_count or tracking_count else 'UNCHANGED; see acceptance.json for all gates',
                timeline=sorted(timeline, key=lambda e: (e['timestamp_ns'] is None, e['timestamp_ns'] or 0)),
                limitations=['Supported transition/observation explanations do not turn FAIL into PASS',
                             'Equal timestamps and sequential CPU/mask reads do not prove strict ordering',
                             'Actual mask violation observations alone do not establish NEW_A selector causality',
                             'Normal transition support still requires review against the exact kernel affinity path'])


def analyze_path(path):
    path = Path(path)
    return analyze(json.loads((path / 'scheduler-stats.json').read_text()),
                   parse_traces((path / 'server.stdout.log').read_text()),
                   json.loads((path / 'clock.json').read_text()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_path(args.case_directory), indent=2, sort_keys=True))
