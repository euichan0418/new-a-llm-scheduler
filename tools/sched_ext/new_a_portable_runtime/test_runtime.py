# SPDX-License-Identifier: GPL-2.0
"""실행 연결과 기능 gate를 합성 자료로 검증한다. 실제 커널/추론 실행은 없다."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import admission
import integration
import records
import study

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[2]
sys.path.append(str(HERE.parent/'new_a_full_guard'))
import test_fixture as existing


def write(p,value):Path(p).write_text(json.dumps(value,allow_nan=False))


class PlanTests(unittest.TestCase):
    def test_two_functional_modes(self):
        p=study.plans('FUNCTIONAL')
        self.assertEqual([x['configuration_mode']for x in p],['MANUAL','AUTO'])
        self.assertTrue(all(x['policy']=='E' and x['server_cpus']==[0,2,4,6] for x in p))

    def test_four_regression_sessions_balanced(self):
        p=study.plans('PERFORMANCE')
        self.assertEqual([x['configuration_mode']for x in p],['MANUAL','AUTO','AUTO','MANUAL'])
        self.assertTrue(all(x['compute_threads']==x['batch_threads']==4 and x['http_threads']==2 for x in p))
        for m in ['MANUAL','AUTO']:self.assertEqual(sum(x['ordinal']for x in p if x['configuration_mode']==m),5)

    def test_gate_before_sudo(self):
        s=(HERE/'campaign.py').read_text();pre=s[s.index('def preflight'):s.index('def new_session')]
        self.assertIn('integration.verify_inputs(setup)',pre);self.assertIn('admission.verify(setup,profile)',pre)
        terminal=s[s.index('def terminal'):];self.assertLess(terminal.index('preflight()'),terminal.index('authenticate('))

    def test_launch_namespace(self):
        self.assertIn('tools/sched_ext/new_a_portable_runtime',(HERE/'launch.py').read_text())

    def test_actual_session_constructor_consumes_auto_arguments(self):
        code=(HERE/'campaign.py').read_text();code=code[code.index('def new_session'):code.index('\ndef attached')]
        supplied=dict(configuration_mode='AUTO',server_argv=['AUTO_PROVENANCE'],server_env={'AUTO':'1'},server_cpus=[2,6],topology='/AUTO.bin')
        session=SimpleNamespace(Session=Mock(return_value='MOCK_SESSION'))
        adapter=SimpleNamespace(case=Mock(return_value=supplied))
        namespace=dict(h=SimpleNamespace(read=lambda _:{} ,session=session),integration=adapter,CURRENT=dict(configuration_mode='AUTO'),
                       PREP=Path('/PREP'),REPO=REPO,HOUSE_CPUS={15},Path=Path,save=Mock())
        exec(code,namespace)
        self.assertEqual(namespace['new_session']('/visit','E',Path('/bundle')),'MOCK_SESSION')
        args,kw=session.Session.call_args
        self.assertEqual(args[1],['AUTO_PROVENANCE']);self.assertEqual(args[2],{2,6})
        self.assertEqual(kw['env'],{'AUTO':'1'});self.assertEqual(kw['topology'],Path('/AUTO.bin'))
        adapter.case.assert_called_once_with({},'AUTO')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.plan=study.plans('FUNCTIONAL');self.paths=[existing.fixture(self.root,v)for v in self.plan]
        contract=records.read(self.paths[0]/'thread-contract.json')
        manual=dict(server_argv=contract['argv'],server_env=contract['openmp'],server_cpus=[0,2,4,6])
        auto=self.root/'configuration';auto.mkdir()
        write(auto/'configuration.json',dict(server_argv=manual['server_argv'],server_env=manual['server_env'],server_cpus=[0,2,4,6],execution_admitted=False,strict_acceptance='FAILED',safety_verdict='FAIL'))
        (auto/'topology.bin').write_bytes(b'OFFLINE_SYNTHETIC')
        (self.root/'topology.bin').write_bytes(b'OFFLINE_SYNTHETIC')
        self.setup=dict(repo=str(REPO),head='fixture',base_run='fixture',server_argv=manual['server_argv'],server_env=manual['server_env'],request_sha256='fixture',
                        workload_constraints={},instrumentation={},policy_binaries={'E':'fixture'},build_static='fixture',mode='FUNCTIONAL',required_functional_dir=str(self.root),
                        manual_configuration=manual,manual_configuration_sha256=hashlib.sha256(json.dumps(manual,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                        manual_topology=str(self.root/'topology.bin'),portable_configuration=str(auto),portable_configuration_sha256=records.sha(auto/'configuration.json'),cpu_probe_binary='OFFLINE_SYNTHETIC')
        write(self.root/'setup.json',self.setup)
        for path,visit in zip(self.paths,self.plan):write(path/'configuration-runtime.json',integration.case(self.setup,visit['configuration_mode']))
        self.profile=dict(host=dict(kernel='OFFLINE_SYNTHETIC',boot_id='fixture'))
        write(self.root/'completion.json',dict(status='PORTABLE_RUNTIME_FUNCTIONAL_COMPLETE_NOT_SAFETY_APPROVED',strict_acceptance='FAILED',safety_verdict='FAIL'))
        write(self.root/'summary.json',study.summarize(self.root))

    def tearDown(self):self.temp.cleanup()

    def change(self,name,fn,index=1):
        p=self.paths[index]/name;x=records.read(p);fn(x);write(p,x)

    def verify(self,setup=None,profile=None):
        with patch.object(admission,'seal_check'):
            return admission.verify(setup or dict(self.setup,mode='PERFORMANCE'),profile or self.profile)

    def test_both_runtime_visits_valid(self):
        result=study.summarize(self.root)
        self.assertTrue(result['complete'],str(result['visits']))
        self.assertEqual((result['functional_samples'],result['performance_samples']),(2,0))
        self.assertEqual(integration.equivalence(result)['status'],'OBSERVED_RUNTIME_CONFIGURATION_MATCH')

    def test_file_equality_without_runtime_proof_never_passes(self):
        with self.assertRaises(ValueError):self.verify()

    def test_actual_schema_proof_allows_regression_gate(self):
        admission.record(self.root,self.profile)
        self.assertEqual(self.verify()['status'],'FUNCTIONAL_EVIDENCE_REVALIDATED')

    def test_changed_boot_blocks(self):
        admission.record(self.root,self.profile)
        with self.assertRaises(ValueError):self.verify(profile=dict(host=dict(kernel='OFFLINE_SYNTHETIC',boot_id='other')))

    def test_changed_auto_config_blocks(self):
        admission.record(self.root,self.profile)
        x=records.read(Path(self.setup['portable_configuration'])/'configuration.json');x['server_cpus'].append(8)
        write(Path(self.setup['portable_configuration'])/'configuration.json',x)
        with self.assertRaises(ValueError):self.verify()

    def test_auto_source_provenance_missing_blocks(self):
        (self.paths[1]/'configuration-runtime.json').unlink()
        self.assertFalse(study.summarize(self.root)['complete'])

    def test_wrong_actual_thread_count_blocks(self):
        self.change('thread-contract.json',lambda x:x.update(compute_threads=8))
        self.assertFalse(study.summarize(self.root)['complete'])

    def test_wrong_actual_affinity_blocks(self):
        self.change('policies-pre-measured.json',lambda x:x[0]['affinity'].append(8))
        self.assertFalse(study.summarize(self.root)['complete'])

    def test_fair_tid_inside_e_blocks(self):
        self.change('policies-after.json',lambda x:x[0].update(policy=0))
        self.assertFalse(study.summarize(self.root)['complete'])

    def test_cleanup_failure_never_creates_proof(self):
        self.change('cleanup.json',lambda x:x.update(status='FAIL'))
        with self.assertRaises(ValueError):admission.record(self.root,self.profile)
        self.assertFalse((self.root/'functional-evidence.json').exists())

    def test_tracking_failure_blocks(self):
        self.change('scheduler-stats.json',lambda x:x['live_monitor'].update(status='FAIL'))
        self.assertFalse(study.summarize(self.root)['complete'])

    def test_stats_enabled_blocks(self):
        self.change('stats-off-before-measured.json',lambda x:x.update(sysctl=1))
        self.assertFalse(study.summarize(self.root)['complete'])


if __name__=='__main__':unittest.main()
