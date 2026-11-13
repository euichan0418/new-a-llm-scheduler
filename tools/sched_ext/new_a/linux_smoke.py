# SPDX-License-Identifier: GPL-2.0
"""One short unprivileged inference correctness check, not a performance sample."""
import datetime
import http.client
import json
import os
import uuid

import topology
import common
import compare
import step10
from session import Session, wait_until


def main():
    common.load_profile()
    step10.MODEL = common.MODEL
    base = common.active_run()
    _, bundle = compare.validate_bundle(base)
    common.require(os.getuid() != 0 and os.sched_getscheduler(0) == 0, 'Run as the normal user')
    common.require(not common.live_project_processes() and common.state() == 'disabled', 'An existing runtime is active')
    common.port_free()
    run = common.REPO / 'benchmark-results/season2/new-a' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-linux-smoke-' + uuid.uuid4().hex[:8])
    argv, _ = step10.server_argv(base, 'C')
    original = os.sched_getaffinity(0)
    try:
        os.sched_setaffinity(0, common.HOUSE_CPUS)
        with Session(run, argv, common.SERVER_CPUS, common.HOUSE_CPUS, policy='C', env=common.bundle_env(bundle)) as session:
            def ready():
                common.require(session.process.poll() is None, 'Server exited during health check')
                return step10.health()
            wait_until(ready, 120, 'Linux smoke health deadline exceeded')
            common.write_json(run / 'policies.json', compare.policy_snapshot(session, bundle))
            payload = common.canonical(dict(prompt='Explain briefly why a public library is useful.', n_predict=32,
                                            temperature=0, seed=424242, cache_prompt=False, ignore_eos=True, stream=False))
            common.write_json(run / 'request.json', json.loads(payload))
            client = http.client.HTTPConnection('127.0.0.1', common.PORT, timeout=120)
            try:
                client.request('POST', '/completion', body=payload, headers={'Content-Type': 'application/json', 'Connection': 'close'})
                response = client.getresponse()
                data = json.loads(response.read())
            finally:
                client.close()
            common.write_json(run / 'response.json', data)
            common.require(response.status == 200 and data.get('tokens_predicted') == 32 and bool(data.get('content')), 'Linux response invalid')
        common.require(not common.live_project_processes() and common.state() == 'disabled', 'Runtime survived cleanup')
        common.port_free()
        common.write_json(run / 'result.json', dict(status='PASS', http_status=response.status, tokens_predicted=data['tokens_predicted'],
                                                   source='Frozen C bundle', scheduler='Linux default',
                                                   purpose='Correctness only; not a C/D/E performance sample; power invariants are not required',
                                                   phase_uprobe='NOT_TESTED; no BPF loaded'))
        print('LINUX_INFERENCE_SMOKE=PASS; 32 tokens; clean shutdown; sched_ext disabled')
    except BaseException as error:
        if run.exists():
            common.write_json(run / 'result.json', dict(status='FAILED', error=str(error)))
        raise
    finally:
        os.sched_setaffinity(0, original)
        print('RESULTS=' + str(run))


if __name__ == '__main__':
    main()
