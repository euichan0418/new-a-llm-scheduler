# SPDX-License-Identifier: GPL-2.0
"""Concurrent development tests, no sudo/BPF/LLM/performance experiments."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parent/'new_a_universal_runtime'),str(HERE.parent/'new_a_hybrid_autotune')]
import snapshot_launcher,provision,interference,controller,runner,evidence
import backend,experiment,planning
from test_autotune import detection
import test_cleanup as cleanup_fixture
from test_raw import fixture,finish
from test_auth_unmanaged import Sudo


def tick(t,busy=0,dev=0,dev_cpus=None):
    return dict(monotonic=float(t),hz=100,cpus={0:(t*100,t*100-busy)},tasks={(600,1):dict(ticks=dev,name='codex-development',cpus=[0]if dev_cpus is None else dev_cpus)})


class Snapshot(unittest.TestCase):
    def fixture(self,root):
        (root/'tools').mkdir();file=root/'tools/source.py';file.write_text('FROZEN = 1\n')
        manifest={'files':[dict(path='tools/source.py',sha256=snapshot_launcher.sha(file),size=file.stat().st_size,executable=False)]}
        (root/'PUBLICATION_MANIFEST.json').write_text(json.dumps(manifest));return file
    def test_editing_development_source_does_not_change_frozen_input(self):
        with tempfile.TemporaryDirectory()as tmp:
            dev=Path(tmp)/'development';dev.mkdir();file=self.fixture(dev)
            frozen=snapshot_launcher.freeze(dev,Path(tmp)/'experiments');expected=snapshot_launcher.sha(frozen/'tools/source.py')
            file.write_text('NEW_DEVELOPMENT = 2\n')
            self.assertEqual(snapshot_launcher.sha(frozen/'tools/source.py'),expected)
            self.assertNotEqual(snapshot_launcher.sha(file),expected)
            with self.assertRaisesRegex(ValueError,'IDENTITY_CHANGED'):snapshot_launcher.freeze(dev,Path(tmp)/'another-campaign')
    def test_snapshot_reuse_and_tampering_detection(self):
        with tempfile.TemporaryDirectory()as tmp:
            dev=Path(tmp)/'dev';dev.mkdir();self.fixture(dev);workspace=Path(tmp)/'experiments'
            first=snapshot_launcher.freeze(dev,workspace);self.assertEqual(snapshot_launcher.freeze(dev,workspace),first)
            (first/'tools/source.py').chmod(0o600);(first/'tools/source.py').write_text('CORRUPTION')
            with self.assertRaisesRegex(ValueError,'IDENTITY_CHANGED'):snapshot_launcher.freeze(dev,workspace)
    def test_runtime_files_read_only_and_hashes_preserved(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);binary=root/'bin';binary.mkdir();server=binary/'llama-server';server.write_bytes(b'SYNTHETIC_NOT_EXECUTABLE');server.chmod(0o700)
            lib=binary/'libfixture.so';lib.write_bytes(b'SYNTHETIC_LIBRARY');model=root/'model.gguf';model.write_bytes(b'SYNTHETIC_MODEL')
            before={p:provision.sha(p)for p in [server,lib,model]};result=provision.seal_runtime(binary,model)
            for p in before:
                self.assertEqual(provision.sha(p),before[p]);self.assertFalse(p.stat().st_mode&0o222)
                with self.assertRaises(PermissionError):p.open('wb')
            self.assertFalse(result['hostile_same_uid_protection'])
    def test_runtime_alias_cannot_change_system_libraries(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);binary=root/'bin';binary.mkdir();outside=root/'outside';outside.write_text('SYNTHETIC');model=root/'model';model.write_text('SYNTHETIC');(binary/'lib.so').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'escapes'):provision.seal_runtime(binary,model)
            self.assertTrue(outside.stat().st_mode&0o200)
    def test_resume_dispatch_uses_original_snapshot_after_development_changes(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);dev=root/'dev';dev.mkdir();file=self.fixture(dev);workspace=root/'work'
            frozen=snapshot_launcher.freeze(dev,workspace);campaign=workspace/'runs/bootstrap-SYNTHETIC';tuning=campaign/'tuning';tuning.mkdir(parents=True)
            tune=tuning/'tuning.private.json';tune.write_text(json.dumps(dict(source_snapshot=dict(root=str(frozen),manifest_sha256=snapshot_launcher.sha(frozen/'PUBLICATION_MANIFEST.json')))))
            (tuning/'seal.json').write_text(json.dumps(dict(sha256=snapshot_launcher.sha(tune))))
            file.write_text('UNRELATED_DEV_CHANGE')
            with patch.object(snapshot_launcher,'__file__',str(dev/'tools/sched_ext/new_a_bootstrap/snapshot_launcher.py')),patch.object(sys,'argv',['launcher','--resume-campaign',str(campaign),'--workspace',str(workspace)]),patch.object(snapshot_launcher.os,'execv')as execute:
                snapshot_launcher.main()
            self.assertEqual(execute.call_count,1);self.assertIn(str(frozen/'tools/sched_ext/new_a_bootstrap/package_driver.py'),execute.call_args.args[1])
    def test_live_admission_ignores_unrelated_dev_edits_but_detects_frozen_input_changes(self):
        with tempfile.TemporaryDirectory()as tmp,ExitStack()as stack:
            root=Path(tmp);source=root/'frozen-source.py';source.write_text('FROZEN')
            binary=root/'frozen.bin';binary.write_bytes(b'SYNTHETIC_ELF');dev=root/'dev.py';dev.write_text('DEVELOPMENT')
            host=dict(kernel='SYNTHETIC',boot_id='SYNTHETIC',btf_sha256='SYNTHETIC')
            tune=dict(uid=os.getuid(),inputs={str(source):backend.sha(source)},host=dict(host,detection={'SYNTHETIC':True}),build=dict(cpu_probe=dict(path='SYNTHETIC')),build_report='SYNTHETIC',base=dict(aliases={},files={str(binary):backend.contracts.record(binary)},resolutions={},helper_resolutions={},server_argv=['SYNTHETIC'],server_env={}))
            stack.enter_context(patch.object(backend,'inputs',side_effect=lambda:{str(source):backend.sha(source)}))
            stack.enter_context(patch.object(backend.contracts,'host_identity',return_value=host))
            stack.enter_context(patch.object(backend.collect,'inspect',return_value={'SYNTHETIC':True}))
            stack.enter_context(patch.object(backend.contracts,'conditions'))
            stack.enter_context(patch.object(backend.contracts,'scheduler_state',return_value='disabled'))
            stack.enter_context(patch.object(backend.contracts,'build_check'))
            stack.enter_context(patch.object(backend.controller,'foreign_processes'))
            backend.current(tune);dev.write_text('NEW_DEVELOPMENT');backend.current(tune)
            binary.write_bytes(b'MODIFIED_ELF')
            with self.assertRaisesRegex(ValueError,'dependency changed'):backend.current(tune)
            binary.write_bytes(b'SYNTHETIC_ELF');source.write_text('MODIFIED_SOURCE')
            with self.assertRaisesRegex(ValueError,'source root/revision changed'):backend.current(tune)


class Coexistence(unittest.TestCase):
    def test_codex_like_developer_survives_experiment_cleanup_failure(self):
        # The only real child is a sleeping normal-user development placeholder.
        developer=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(30)'])
        try:
            # Restrict the fixture view, not the production guard. Other real
            # researchers may concurrently own a server/monitor on this host.
            original=Path.glob
            def paths(path,pattern):
                return iter([Path('/proc')/str(developer.pid)])if str(path)=='/proc'and pattern=='[0-9]*'else original(path,pattern)
            with patch.object(Path,'glob',paths):controller.foreign_processes()
            self.assertIsNone(developer.poll())
            with tempfile.TemporaryDirectory()as tmp:cleanup_fixture.Cleanup().run_fault(tmp)
            self.assertIsNone(developer.poll())
        finally:developer.terminate();developer.wait(timeout=5)
    def test_detects_load_not_process_name_and_respects_separate_cpus(self):
        quiet=interference.compare(tick(1),tick(2),[0]);self.assertEqual(quiet['foreign_cpu_seconds_per_second'],0)
        noisy=interference.compare(tick(1),tick(2,busy=60,dev=60),[0]);self.assertGreater(noisy['foreign_cpu_seconds_per_second'],interference.DEFAULT['foreign_cpu_seconds_per_second'])
        separate=interference.compare(tick(1,dev_cpus=[4]),tick(2,dev=60,dev_cpus=[4]),[0]);self.assertEqual(separate['foreign_cpu_seconds_per_second'],0)
    def test_safe_boundary_pauses_then_proceeds_without_killing_development(self):
        with tempfile.TemporaryDirectory()as tmp:
            frames=iter([tick(1),tick(2,busy=60,dev=60),tick(3,busy=60,dev=60),tick(4,busy=60,dev=60)])
            quiet=Mock();ready=Mock()
            interference.wait_quiet(dict(interference_contract=dict(interference.DEFAULT)),tmp,[0],quiet,ready,reader=lambda:next(frames),sleep=lambda _:None,clock=lambda:0)
            rows=[json.loads(s)for s in (Path(tmp)/'interference-boundaries.private.jsonl').read_text().splitlines()]
            self.assertEqual([r['status']for r in rows],['PAUSED','READY']);self.assertGreaterEqual(quiet.call_count,4)
    def test_persistent_load_stops_before_creating_measurement_and_can_resume(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);s=Sudo(root);d=detection();c=planning.default(d)
            campaign=dict(domain=dict(selected=c['affinity_cpus']),interference_contract=dict(interference.DEFAULT))
            tune=dict(host=dict(detection=d),rules=planning.DEFAULT_RULES,limits=dict(sessions=160,seconds=7200),scheduler='linux')
            run=experiment.Experiment(root,tune,auth=s.auth)
            with patch.object(experiment,'variant',return_value=(root,campaign)),patch.object(run,'functionals'),patch.object(backend,'current'),patch.object(backend,'acquire')as acquire,patch.object(interference,'wait_quiet',side_effect=interference.BoundaryPause('BUSY')):
                with self.assertRaises(interference.BoundaryPause):run.measure('PREFILL',dict(candidate=c,block=1,position=1))
                acquire.assert_not_called();self.assertEqual(experiment.records(root),[])
            # No failed attempt was created; the same explicit campaign can retry
            # boundary admission later without rerunning any completed slot.
            self.assertFalse(list((root/'raw').glob('*')))
    def test_long_busy_timeout_has_preserved_boundary_record(self):
        with tempfile.TemporaryDirectory()as tmp:
            frames=iter([tick(1),tick(2,busy=60,dev=60)]);times=iter([0,31])
            with self.assertRaises(interference.BoundaryPause):interference.wait_quiet(dict(interference_contract=dict(interference.DEFAULT)),tmp,[0],lambda:None,lambda:None,reader=lambda:next(frames),sleep=lambda _:None,clock=lambda:next(times))
            self.assertEqual(json.loads((Path(tmp)/'interference-boundaries.private.jsonl').read_text())['status'],'PAUSED')
    def test_contaminated_raw_cannot_be_counted_even_if_cleanup_pass(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'raw';campaign,slot=fixture(path,'C',False);campaign['interference_contract']=dict(interference.DEFAULT)
            rows=[json.loads(s)for s in (path/'monitor.jsonl').read_text().splitlines()]
            for r in rows:r['interference']=dict(contaminated=False,status='SAMPLED')
            rows[-1]['interference']['contaminated']=True
            (path/'monitor.jsonl').write_text('\n'.join(json.dumps(r)for r in rows)+'\n');finish(path,campaign,slot)
            with self.assertRaisesRegex(ValueError,'contaminated'):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
            campaign.pop('interference_contract')
            with self.assertRaisesRegex(ValueError,'different Host/baseline/contract'):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_interference_resume_is_explicit_and_requires_cleanup(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp);campaign=dict(interference_contract=dict(interference.DEFAULT));failure=dict(kind='CPU_INTERFERENCE_DETECTED')
            cleanup=dict(status='PASS',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True)
            (path/'monitor.jsonl').write_text(json.dumps(dict(interference=dict(contaminated=True)))+'\n')
            self.assertTrue(controller.safe_interference_resume(path,campaign,failure,cleanup))
            self.assertFalse(controller.safe_auth_resume(path,campaign,failure,cleanup))
            cleanup['server_exited']=False;self.assertFalse(controller.safe_interference_resume(path,campaign,failure,cleanup))
    def test_contaminated_failed_slot_requires_its_own_resume_approval(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp);campaign=dict(interference_contract=dict(interference.DEFAULT))
            (path/'failure.json').write_text(json.dumps(dict(kind='CPU_INTERFERENCE_DETECTED')))
            (path/'cleanup.json').write_text(json.dumps(dict(status='PASS',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True)))
            (path/'monitor.jsonl').write_text(json.dumps(dict(interference=dict(contaminated=True)))+'\n')
            original={p:p.read_bytes()for p in path.iterdir()}
            record=dict(raw=str(path),variant='SYNTHETIC')
            with patch.object(backend.contracts,'load_campaign',return_value=campaign):
                with self.assertRaisesRegex(ValueError,'automatic retry prohibited'):experiment.recovered(record,{},False)
                with self.assertRaisesRegex(ValueError,'automatic retry prohibited'):experiment.recovered(record,{},True)
                self.assertIsNone(experiment.recovered(record,{},False,True))
            self.assertTrue(all(p.read_bytes()==v for p,v in original.items()))
    def test_spare_e_housekeeping_and_full_physical_core_limit_reported(self):
        d=detection(6,8);tune=dict(host=dict(detection=d),housekeeping=backend.housekeeping(d))
        config=planning.default(d);hk,spare=backend.helper_cpus(tune,config)
        self.assertEqual(spare,[16,17,18,19]);self.assertEqual(hk,[19]);self.assertFalse(set(hk)&set(config['affinity_cpus']))
        config=planning.candidate(d,8);_,spare=backend.helper_cpus(tune,config);self.assertEqual(spare,[])
    def test_measured_foreign_load_is_never_a_successful_sample(self):
        frames=iter([tick(1),tick(2,dev=70)])
        with patch.object(interference,'snapshot',side_effect=lambda:next(frames)):
            sampler=interference.Sampler(dict(interference.DEFAULT));self.assertTrue(sampler.sample([0],exclude=[])['contaminated'])
    def test_contract_cannot_be_relaxed_after_observing_results(self):
        changed=dict(interference.DEFAULT,pre_cpu_busy_pct=100)
        with self.assertRaisesRegex(ValueError,'changed'):interference.validate(changed)


if __name__=='__main__':unittest.main(verbosity=2)
