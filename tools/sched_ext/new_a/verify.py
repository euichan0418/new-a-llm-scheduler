# SPDX-License-Identifier: GPL-2.0
"""Save nonprivileged verification evidence without changing old results."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import uuid

from topology import HERE, discover
from common import REPO, sha, write_json


def main():
    run = REPO / 'benchmark-results/season2/new-a' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-offline-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    commands = [
        ['make', '-C', str(HERE), 'check'],
        ['python3', '-B', 'tools/sched_ext/bench/test_campaign.py'],
        ['python3', '-B', 'tools/sched_ext/bench/test_host_profile.py'],
        ['make', '-C', 'tools/sched_ext', 'all'],
        ['make', '-C', 'tools/sched_ext/validation', 'all'],
        ['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-Itools/server', 'tests/test-server-scx.cpp', '-o', str(run / 'test-server-scx')],
        [str(run / 'test-server-scx')],
        ['python3', '-B', str(HERE / 'runtime.py')],
        ['python3', '-B', str(HERE / 'compare.py')],
        ['git', 'diff', '--check'],
    ]
    results = []
    for i, argv in enumerate(commands):
        log = run / f'{i+1:02d}.log'
        with log.open('xb') as output:
            result = subprocess.run(argv, cwd=REPO, stdout=output, stderr=subprocess.STDOUT, timeout=90)
        results.append(dict(argv=argv, exit=result.returncode, log=log.name))
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {' '.join(argv)}")
    # These preflight checks cannot activate sched_ext or send inference requests.
    preflight = [(['sudo', '-n', 'true'], 'sudo'),
                 (['python3', '-B', 'tools/sched_ext/bench/host_profile.py', '--check'], 'host-profile')]
    for argv, name in preflight:
        with (run / f'{name}.log').open('xb') as output:
            result = subprocess.run(argv, cwd=REPO, stdout=output, stderr=subprocess.STDOUT, timeout=30)
        results.append(dict(argv=argv, exit=result.returncode, log=f'{name}.log', preflight=True))
    write_json(run / 'topology.json', discover())
    write_json(run / 'git-tags.json', subprocess.check_output(['git', 'show-ref', '--tags'], cwd=REPO, text=True).splitlines())
    tracked_changes = subprocess.check_output(['git', 'diff', '--name-only', '59abec45fe0919de20550ce7cdcf363d9f7bbe5a'], cwd=REPO, text=True).splitlines()
    result = dict(status='PASS' if all(c['exit'] == 0 for c in results if not c.get('preflight')) else 'FAILED',
                  runtime_status='NOT_RUN', runtime_reason='See sudo.log and host-profile.log; offline PASS is not verifier/runtime acceptance',
                  head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                  branch=subprocess.check_output(['git', 'branch', '--show-current'], cwd=REPO, text=True).strip(),
                  kernel=os.uname().release, commands=results, tracked_changes=tracked_changes,
                  sources={p.name: sha(p) for p in HERE.glob('*') if p.is_file()})
    write_json(run / 'verification.json', result)
    print('RESULTS=' + str(run))
    if result['status'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
