# SPDX-License-Identifier: GPL-2.0
"""Short C/D/E comparison using frozen C bytes and unchanged measurement helpers."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import time
import uuid

import topology
from topology import HERE
from session import Session, check_privileges, wait_until
import common
import host_profile
import machine
import step10
from common import (HOUSE_CPUS, SERVER_CPUS, THREADS, REPO, REQUEST, REQUEST_SHA, TAGS,
                    bundle_env, canonical, command, identity, live_project_processes,
                    require, sha, state, write_json)


def process_counters(pid):
    threads = {}
    for path in Path(f'/proc/{pid}/task').iterdir():
        try:
            status = (path / 'status').read_text()
            sched = dict(re.findall(r'^\s*([\w.]+)\s*:\s*(\S+)', (path / 'sched').read_text(), re.M))
            stat = (path / 'stat').read_text()
            fields = stat[stat.rfind(')') + 2:].split()
            counters = {k: int(sched[k]) if k in sched else None for k in ('se.nr_migrations', 'nr_switches')}
            threads[path.name] = dict(starttime=fields[19], **counters,
                                      runtime_ns=int((path / 'schedstat').read_text().split()[0]),
                                      context_switches=sum(map(int, re.findall(r'^(?:nonvoluntary|voluntary)_ctxt_switches:\s+(\d+)', status, re.M))))
        except (FileNotFoundError, ProcessLookupError):
            continue
    cpus = {}
    for line in Path('/proc/stat').read_text().splitlines():
        if re.match(r'cpu\d+ ', line):
            fields = line.split()
            values = list(map(int, fields[1:9]))
            cpus[fields[0]] = dict(total=sum(values), idle=values[3] + values[4])
    return dict(threads=threads, cpus=cpus, monotonic_ns=time.monotonic_ns())


def counter_delta(before, after):
    same = set(before['threads']) == set(after['threads']) and all(
        before['threads'][t]['starttime'] == after['threads'][t]['starttime'] for t in before['threads'])
    metrics = {}
    for name in ('runtime_ns', 'context_switches', 'se.nr_migrations', 'nr_switches'):
        complete = same and all(before['threads'][t][name] is not None and after['threads'][t][name] is not None for t in before['threads'])
        metrics[name] = sum(after['threads'][t][name] - before['threads'][t][name] for t in before['threads']) if complete else None
    metrics['cpu_utilization_system_pct'] = {}
    for cpu in before['cpus'].keys() & after['cpus'].keys():
        total = after['cpus'][cpu]['total'] - before['cpus'][cpu]['total']
        idle = after['cpus'][cpu]['idle'] - before['cpus'][cpu]['idle']
        metrics['cpu_utilization_system_pct'][cpu] = 100 * (1 - idle / total) if total > 0 else None
    metrics['process_cpu_utilization_pct'] = (100 * metrics['runtime_ns'] / (after['monotonic_ns'] - before['monotonic_ns'])) if metrics['runtime_ns'] is not None else None
    metrics['per_cpu_process_runtime_ns'] = None
    metrics['scope'] = 'Outside-timing proc snapshots bracket the request; per-CPU utilization is system-wide. Per-CPU process runtime requires the separate diagnostic validation.'
    metrics['stable_thread_set'] = same
    return metrics


def policy_snapshot(session, bundle):
    rows = [identity(int(p.name)) for p in Path(f"/proc/{session.server['pid']}/task").iterdir()]
    expected = 0 if session.policy == 'C' else 7
    require(rows and all(r['uid'] == os.getuid() and r['policy'] == expected and set(r['affinity']) == SERVER_CPUS for r in rows),
            'Server TID policy/allowed mask changed')
    require(os.sched_getscheduler(0) == 0 and os.sched_getaffinity(0) == HOUSE_CPUS, 'Client must stay normal on housekeeping')
    require(state() == ('disabled' if expected == 0 else 'enabled'), 'Scheduler state changed')
    maps = Path(f"/proc/{session.server['pid']}/maps").read_text()
    libraries = set(re.findall(r'(/\S+lib(?:llama|ggml|mtmd)[^\s]*)', maps))
    require(libraries and all(Path(p).resolve().is_relative_to(bundle) for p in libraries), 'A project library is outside the frozen bundle')
    return rows


def validate_bundle(base_run):
    artifact = json.loads((base_run / 'artifacts.json').read_text())['C']
    require(artifact['revision'] == TAGS['llama-side-lightweight-v1'] and artifact['hint'] == 'ON' and artifact['trace'] == 'OFF', 'C definition differs')
    bundle = (base_run / 'artifacts/C').resolve()
    require(all(sha(bundle / p) == digest for p, digest in artifact['files'].items()), 'Frozen C artifact changed')
    for path, digest in artifact['system_libraries'].items():
        require(sha(path) == digest, 'System library changed since the C build')
    require(sha(base_run / 'host-profile.json') == sha(common.PROFILE_PATH), 'Existing bundle host profile differs')
    return artifact, bundle


def candidate_identity():
    paths = [HERE / 'build/llama_new_a.bpf.o', HERE / 'build/bin/llama_new_a', HERE / 'build/bin/cpu_types']
    embedded = {p[:-1].decode() for p in re.findall(rb'/[^\x00]+/llama_new_a\.bpf\.o\x00', paths[1].read_bytes())}
    require(embedded == {str(paths[0])}, 'Candidate loader embedded BPF path differs; rebuild after moving the repository')
    return {str(p.relative_to(HERE)): sha(p) for p in paths}


def check_acceptance(path, discovered):
    require(path is not None, '--execute requires --acceptance PATH/acceptance.json from runtime.py')
    value = json.loads(path.read_text())
    manifest = json.loads((path.parent / 'manifest.json').read_text())
    require(value['status'] == 'PASS' and manifest['kernel'] == os.uname().release and
            manifest['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'Functional acceptance is absent or belongs to a different boot')
    require(any(r['case'] == 'unchanged-base-inference-smoke' and r['status'] == 'PASS' for r in value['results']),
            'Run runtime.py --execute --base-smoke before performance measurement')
    require(all(sha(HERE / name) == digest for name, digest in manifest['sources'].items()), 'Sources changed after functional acceptance')
    require(all(sha(HERE / name) == digest for name, digest in manifest['artifacts'].items()), 'Artifacts changed after functional acceptance')
    require(json.loads((path.parent / 'topology.json').read_text()) == json.loads(json.dumps(discovered)),
            'Topology/core classification changed after functional acceptance')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-run', type=Path, help='Existing frozen Step10 preparation; read-only bundle reuse')
    parser.add_argument('--acceptance', type=Path, help='Successful real scheduling validation from this boot')
    parser.add_argument('--rounds', type=int, choices=(1, 2, 3), default=1, help='Initial check only; formal campaigns require a separate plan')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    host = common.load_profile()
    step10.MODEL = common.MODEL
    base_run = (args.base_run or common.active_run()).resolve()
    artifact, bundle = validate_bundle(base_run)
    require(hashlib.sha256(canonical(REQUEST)).hexdigest() == REQUEST_SHA, 'Frozen payload changed')
    discovered = topology.discover(host['topology']['allowed_cpus'])
    require(discovered['topology'] == host['topology'], 'Candidate topology differs from the frozen profile')
    require(discovered['policy']['enabled'], 'NEW_A is in safe fallback; resolve topology before a policy comparison')
    order = [dict(block=b+1, config=c) for b in range(args.rounds) for c in ('CDE'[b % 3:] + 'CDE'[:b % 3])]
    plan = dict(kind='initial C/D/E comparison, not a formal campaign', repetitions=args.rounds, warmups_per_fresh_server=2,
                order=order, threads=THREADS, server_cpus=sorted(SERVER_CPUS), housekeeping=sorted(HOUSE_CPUS),
                model=host['model'], request_sha256=REQUEST_SHA, prompt_sha256=common.PROMPT_SHA,
                base_run=str(base_run), host_profile_sha256=sha(common.PROFILE_PATH), candidate=candidate_identity(),
                candidate_policy=discovered['policy'], power=host_profile.execution_state(host['capabilities']),
                thermal='Unchanged Base readiness: three samples over ten seconds, <=55 C, spread <=2 C, busy <=5%; absent sensor uses recorded reduced observability',
                timing='Unchanged blocking HTTP POST plus full response read; extra proc snapshots remain outside timing',
                estimated_minutes_per_round='3-10, estimate only; readiness can abort after 180 seconds',
                result_root=str(REPO / 'benchmark-results/season2/new-a'),
                differences=['Only C/D/E and 1-3 samples each; descriptive results only',
                             'Proc counter snapshots outside timing added equally to C/D/E',
                             'Per-CPU process runtime is collected only in separate diagnostic acceptance'])
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.execute:
        return
    check_acceptance(args.acceptance, discovered)
    check_privileges()
    host_profile.validate_current(host, initial_affinity=True)
    common.scheduler_check()
    common.model_identity()
    machine.reject_interactive_apps()
    require(not live_project_processes(), 'Existing project runtime is active')
    original_affinity = os.sched_getaffinity(0)
    os.sched_setaffinity(0, HOUSE_CPUS)
    run = REPO / 'benchmark-results/season2/new-a' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-comparison-' + uuid.uuid4().hex[:8])
    rows = []
    try:
        run.mkdir(parents=True, exist_ok=False)
        plan.update(status='STARTED', base_head=command(['git', 'rev-parse', 'HEAD']),
                    sources={str(p.relative_to(REPO)): sha(p) for p in list(HERE.glob('*.py')) + list(HERE.glob('*.c')) + list(HERE.glob('*.h'))},
                    artifact=artifact, acceptance=str(args.acceptance.resolve()))
        write_json(run / 'manifest.json', plan)
        shutil.copyfile(common.PROFILE_PATH, run / 'host-profile.json')
        shutil.copyfile(args.acceptance, run / 'functional-acceptance.json')
        write_json(run / 'topology.json', discovered)
        config = run / 'topology.bin'
        config.write_bytes(topology.encode(discovered['policy']))
        payload = canonical(REQUEST)
        (run / 'request.json').write_bytes(payload)
        baseline = machine.snapshot()
        machine.validate(baseline)
        write_json(run / 'machine-baseline.json', baseline)
        prompt_count = None
        for number, visit in enumerate(order, 1):
            path = run / 'raw' / f"{number:02d}-{visit['config']}"
            path.mkdir(parents=True)
            machine.readiness(path, 'idle-readiness.json', baseline, after_warmup=False)
            require(candidate_identity() == plan['candidate'], 'Candidate changed during comparison')
            common.scheduler_check()
            validate_bundle(base_run)
            common.port_free()
            # D and E deliberately use the same C argv and environment.
            argv, _ = step10.server_argv(base_run, 'C')
            session = Session(path, argv, SERVER_CPUS, HOUSE_CPUS, policy=visit['config'],
                              marker=bundle / 'bin/libllama-server-impl.so', topology=config, env=bundle_env(bundle))
            with session:
                def healthy():
                    require(session.process.poll() is None, 'Server exited during readiness')
                    return step10.health()
                wait_until(healthy, 180, 'Server health deadline exceeded')
                write_json(path / 'policies-before.json', policy_snapshot(session, bundle))
                for warmup in (1, 2):
                    prompt_count, _, _ = step10.post_request(path, f'warmup-{warmup}', payload, prompt_count)
                machine.readiness(path, 'readiness.json', baseline, after_warmup=True)
                write_json(path / 'policies-pre-measured.json', policy_snapshot(session, bundle))
                system_before = machine.snapshot()
                machine.validate(system_before, baseline)
                before = process_counters(session.server['pid'])
                prompt_count, measured, client = step10.post_request(path, 'measured', payload, prompt_count)
                after = process_counters(session.server['pid'])
                system_after = machine.snapshot()
                machine.validate(system_after, baseline)
                write_json(path / 'system-before.json', system_before)
                write_json(path / 'system-after.json', system_after)
                write_json(path / 'counters-before.json', before)
                write_json(path / 'counters-after.json', after)
                write_json(path / 'counter-delta.json', counter_delta(before, after))
                write_json(path / 'policies-after.json', policy_snapshot(session, bundle))
                row = dict(**visit, prefill_tps=measured['timings']['prompt_per_second'],
                           decode_tps=measured['timings']['predicted_per_second'], request_wall_ms=client['request_wall_ms'])
            common.port_free()
            require(not live_project_processes(), 'Project runtime survived cleanup')
            rows.append(row)
            write_json(run / 'rows.json', rows)
        summary = {c: {m: statistics.median(r[m] for r in rows if r['config'] == c)
                       for m in ('prefill_tps', 'decode_tps', 'request_wall_ms')} for c in 'CDE'}
        pairs = {reference + '->E': {metric: 100 * (summary['E'][metric] / summary[reference][metric] - 1)
                                     for metric in ('prefill_tps', 'decode_tps', 'request_wall_ms')} for reference in 'CD'}
        write_json(run / 'summary.json', dict(configurations=summary, raw_delta_pct=pairs))
        step10.analysis.csv_file(run / 'raw-runs.csv', rows)
        lines = ['# Initial NEW_A comparison', '', 'All measured samples retained. Two warm-ups per fresh server excluded. This small check does not establish a performance improvement.', '',
                 '| Policy | Prefill tok/s | Decode tok/s | Request wall ms |', '| --- | ---: | ---: | ---: |']
        for c, values in summary.items():
            lines.append(f"| {c} | {values['prefill_tps']:.3f} | {values['decode_tps']:.3f} | {values['request_wall_ms']:.3f} |")
        lines += ['', '| Comparison | Prefill delta % | Decode delta % | Wall delta % |', '| --- | ---: | ---: | ---: |']
        for pair, values in pairs.items():
            lines.append(f"| {pair} | {values['prefill_tps']:+.3f} | {values['decode_tps']:+.3f} | {values['request_wall_ms']:+.3f} |")
        lines += ['', 'Positive throughput deltas mean faster; positive wall-time deltas mean slower. These small descriptive samples do not establish a general performance advantage.']
        lines += ['', 'Medians above use only this run. See counter-delta.json per visit and the separate diagnostic acceptance for CPU execution/migrations. System CPU utilization is not process attribution.', '']
        (run / 'report.md').write_text('\n'.join(lines))
        plan['status'] = 'COMPLETE'
    except BaseException as error:
        plan.update(status='FAILED', error=str(error))
        raise
    finally:
        if run.exists():
            write_json(run / 'manifest.json', plan)
        os.sched_setaffinity(0, original_affinity)
        print('RESULTS=' + str(run))


if __name__ == '__main__':
    main()
