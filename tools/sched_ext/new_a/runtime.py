# SPDX-License-Identifier: GPL-2.0
"""Opt-in real scheduling acceptance, separate from performance measurements."""
import argparse
import datetime
import json
import os
from pathlib import Path
import uuid

import topology
from topology import HERE
from session import Session, check_privileges
from session import active_experiments
import affinity_diagnostics
from common import REPO, command, live_project_processes, require, sha, state, write_json


def validate_stats(value, allowed, expected_tier=None, expect_fallback=False):
    rows = value['cpus']
    require(sum(r['runtime_ns'] for r in rows) > 0, 'No actual running/stopping runtime observed')
    require(all(not r['mask_violations'] and not r['tracking_failures'] for r in rows), 'Mask or task tracking failure')
    require(all(r['cpu'] in allowed or not r['runtime_ns'] for r in rows), 'Actual execution outside testcase CPUs')
    selected = [sum(r['selected'][tier] for r in rows) for tier in range(4)]
    require(sum(selected) > 0, 'No real select_cpu callback observed')
    if expected_tier is not None:
        require(selected[expected_tier] > 0, 'Expected idle tier was never used')
        require(not any(selected[t] for t in range(3) if t != expected_tier), 'Disallowed tier hit')
    if expect_fallback:
        require(selected[3] > 0 and sum(selected[:3]) == 0, 'Disabled topology did not use only fallback')
    phases = 0
    for row in rows:
        phases |= row['phase_seen']
    require(phases & 6 == 6, 'PID-scoped uprobe did not observe both PREFILL and DECODE')
    return dict(selected=selected, runtime_ns=sum(r['runtime_ns'] for r in rows),
                migrations=sum(r['migrations'] for r in rows), phase_seen=phases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Load partial sched_ext and execute bounded synthetic tests')
    parser.add_argument('--base-smoke', action='store_true', help='Also run the unchanged Base inference smoke in a new isolated output directory')
    parser.add_argument('--case', choices=('live-affinity',), help='Run only the live-affinity diagnostic; never a full acceptance')
    args = parser.parse_args()
    if args.case and args.base_smoke:
        parser.error('--case cannot be combined with --base-smoke')
    value = topology.discover()
    usable = value['topology']['usable_cpus'] if value['topology'] else sorted(os.sched_getaffinity(0))
    require(len(usable) > 1, 'At least two CPUs required for separate housekeeping')
    house = {usable[-1]}
    allowed = set(usable) - house
    tiers = [sorted(set(t) & allowed) for t in value['policy']['tiers']]
    cases = [dict(name='neutral', policy='D', cpus=sorted(allowed), threads=1)]
    if value['policy']['enabled']:
        cases += [dict(name=f'tier-{i+1}', policy='E', cpus=tier, threads=1, expected_tier=i)
                  for i, tier in enumerate(tiers) if tier]
        cases += [dict(name='full-mask', policy='E', cpus=sorted(allowed), threads=1),
                  dict(name='oversubscribed', policy='E', cpus=sorted(allowed), threads=min(128, len(allowed)+2)),
                  dict(name='live-affinity', policy='E', cpus=sorted(allowed), threads=2, narrow=min(allowed))]
        sparse = [tier[0] for tier in tiers if tier]
        cases += [dict(name='sparse-mask', policy='E', cpus=sparse, threads=1)]
    cases += [dict(name='fallback', policy='E', cpus=sorted(allowed), threads=1, fallback=True)]
    if args.case:
        cases = [case for case in cases if case['name'] == args.case]
        require(cases, 'Requested diagnostic is unavailable while topology is in fallback')
    print(json.dumps(dict(policy=value['policy'], housekeeping=sorted(house), cases=cases, scope=args.case or 'full',
                          estimated_seconds=4 * len(cases), execution=args.execute), indent=2))
    if not args.execute:
        return
    check_privileges()
    require(not live_project_processes(), 'An existing project runtime is active')
    run = REPO / 'benchmark-results/season2/new-a' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-validation-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    write_json(run / 'topology.json', value)
    write_json(run / 'manifest.json', dict(status='STARTED', cases=cases, scope=args.case or 'full',
                                          preflight=dict(sched_ext=state(), active_experiments=active_experiments()),
                                          boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                          kernel=os.uname().release, sources={p.name: sha(p) for p in HERE.glob('*') if p.is_file()},
                                          artifacts={str(p.relative_to(HERE)): sha(p) for p in
                                                     list((HERE / 'build/bin').iterdir()) + list((HERE / 'build').glob('*.bpf.o')) if p.is_file()}))
    config = run / 'topology.bin'
    config.write_bytes(topology.encode(value['policy']))
    fallback = run / 'fallback.bin'
    fallback.write_bytes(topology.encode(topology.fallback('explicit fallback acceptance test')))
    results = []
    try:
        for case in cases:
            read_fd, write_fd = os.pipe()
            path = run / case['name']
            argv = [HERE / 'build/bin/wake_probe', str(read_fd), str(case['threads']), str(case.get('narrow', -1))]
            clocks = {}
            try:
                session = Session(path, argv, case['cpus'], house, policy=case['policy'], diagnostics=True,
                                  topology=fallback if case.get('fallback') else config, pass_fds=(read_fd,))
                with session:
                    session.natural = True
                    if case['name'] == 'live-affinity':
                        clocks['before'] = affinity_diagnostics.clock_snapshot(session.server['pid'])
                        write_json(path / 'clock.json', clocks)
                    os.close(read_fd)
                    read_fd = -1
                    os.write(write_fd, b'1')
                    os.close(write_fd)
                    write_fd = -1
                    require(session.process.wait(timeout=20) == 0, 'Finite workload failed or hung')
                output = (path / 'server.stdout.log').read_text()
                require(output.count('iterations=160 violations=0') == case['threads'], 'Worker missing or observed outside affinity')
                observed = validate_stats(json.loads((path / 'scheduler-stats.json').read_text()), set(case['cpus']),
                                          case.get('expected_tier'), case.get('fallback', False)) if case['policy'] == 'E' else {}
                results.append(dict(case=case['name'], status='PASS', **observed))
                write_json(run / 'results.json', results)
            finally:
                for fd in (read_fd, write_fd):
                    if fd >= 0:
                        os.close(fd)
                if case['name'] == 'live-affinity' and path.exists():
                    clocks['after'] = affinity_diagnostics.clock_snapshot(os.getpid())
                    write_json(path / 'clock.json', clocks)
                    try:
                        diagnosis = affinity_diagnostics.analyze_path(path)
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        diagnosis = dict(conclusion='inconclusive', error=str(error), acceptance_unchanged=True)
                    write_json(path / 'diagnosis.json', diagnosis)
        if args.base_smoke:
            import common
            host = common.load_profile()
            build = run / 'base-smoke'
            build.mkdir()
            (build / 'bin').symlink_to(REPO / 'build-lightweight-dev/bin', target_is_directory=True)
            env_path = build / 'topology.env'
            command([HERE.parent / 'bench/detect_host_topology.sh', '--env', env_path], log=build / 'topology.log')
            env = dict(os.environ, MODEL=host['model']['path'], TOPOLOGY_ENV=str(env_path), BUILD_DIR=str(build))
            command(['bash', str(HERE.parent / 'bench/portability_smoke.sh')], env=env, log=build / 'smoke.log', capture=False)
            results.append(dict(case='unchanged-base-inference-smoke', status='PASS', output=str(build)))
            write_json(run / 'results.json', results)
        write_json(run / 'acceptance.json', dict(status='PASS', results=results, scope=args.case or 'full',
                                                limitations=['No physical CPU hotplug was performed',
                                                             'Tier restrictions test live allowed masks; full-mask run does not prove strict runtime partitioning']))
    except BaseException as error:
        write_json(run / 'acceptance.json', dict(status='FAILED', error=str(error), results=results, scope=args.case or 'full'))
        raise
    finally:
        print('RESULTS=' + str(run))


if __name__ == '__main__':
    main()
