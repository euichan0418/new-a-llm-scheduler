# SPDX-License-Identifier: GPL-2.0
"""Package-only mocked tests. No sudo/BPF/server/inference."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('package_under_test',Path(__file__).with_name('package_driver.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def tiny(root):
    f=root/'runtime/a.py';f.parent.mkdir();f.write_text('# synthetic\n')
    value=dict(files=[dict(path='runtime/a.py',size=f.stat().st_size,sha256=p.sha(f))])
    m=root/'PACKAGE_MANIFEST.json';m.write_text(json.dumps(value))
    (root/'SHA256SUMS').write_text('\n'.join(p.sha(root/x)+'  '+x for x in ['runtime/a.py','PACKAGE_MANIFEST.json'])+'\n')
    return value


class Integrity(unittest.TestCase):
    def test_complete_package(self):
        self.assertEqual(p.verify()['status'],'PACKAGE_INTEGRITY_PASS')
    def test_copied_file_edit_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);tiny(root);(root/'runtime/a.py').write_text('tampered')
            with self.assertRaisesRegex(ValueError,'integrity mismatch'):p.verify(root)
    def test_missing_file_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);tiny(root);(root/'runtime/a.py').unlink()
            with self.assertRaises(ValueError):p.verify(root)
    def test_extra_file_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);tiny(root);(root/'private.json').write_text('not allowed')
            with self.assertRaisesRegex(ValueError,'Unexpected/missing'):p.verify(root)
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);tiny(root);target=root/'runtime/a.py';text=target.read_text();target.unlink();other=root/'other';other.write_text(text);target.symlink_to(other)
            with self.assertRaisesRegex(ValueError,'symlink'):p.verify(root)
    def test_parent_traversal_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);v=tiny(root);v['files'][0]['path']='../escape';(root/'PACKAGE_MANIFEST.json').write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError,'Unsafe/duplicate'):p.verify(root)
    def test_checksum_manifest_edit_rejected(self):
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);tiny(root);(root/'PACKAGE_MANIFEST.json').write_text((root/'PACKAGE_MANIFEST.json').read_text()+' ')
            with self.assertRaisesRegex(ValueError,'SHA256SUMS'):p.verify(root)


class Routing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.engine=p.engine()
    def args(self,*args):return ['--server','/FROZEN/server','--model','/MODELS/model.gguf',*args]
    def test_linux_default_real_run_route(self):
        a,c=p.action('run',self.args('--execute','--acknowledge-existing-safety-fail'),self.engine)
        self.assertTrue(a.execute);self.assertEqual(a.scheduler,'linux');self.assertFalse(p.contained(a.output));self.assertEqual(c[3],'run')
    def test_new_a_not_silently_converted_to_linux(self):
        a,c=p.action('run',self.args('--scheduler','new-a','--functional-root','/FRESH/proofs'),self.engine)
        self.assertEqual(a.scheduler,'new-a');self.assertEqual(str(a.functional_root),'/FRESH/proofs')
    def test_explicit_paths_with_spaces_preserved(self):
        a,c=p.action('run',['--server','/path with space/server','--model','/path with space/model.gguf','--library-dir','/path with space/lib'],self.engine)
        self.assertEqual(a.server,'/path with space/server');self.assertIn('/path with space/lib',c)
    def test_plan_never_requests_execution(self):
        a,c=p.action('plan',[],self.engine);self.assertEqual(a.command,'plan');self.assertNotIn('--execute',c)
    def test_check_rejects_execute(self):
        with self.assertRaisesRegex(ValueError,'never executes'):p.action('check',self.args('--execute'),self.engine)
    def test_output_inside_package_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside the package'):p.action('run',self.args('--output',str(p.ROOT/'results')),self.engine)
    def test_analyze_existing_explicit_output_only(self):
        with self.assertRaisesRegex(ValueError,'existing --output'):p.action('analyze',[],self.engine)
        a,c=p.action('analyze',['--output=/RESULTS/run'],self.engine);self.assertEqual(str(a.output),'/RESULTS/run')
    def test_newa_functional_route_preserves_ack_and_auth_resume(self):
        a,c=p.action('functional',['--campaign','/FRESH/campaign','--execute','--acknowledge-existing-safety-fail','--resume-auth'],self.engine)
        self.assertTrue(a.resume_auth and a.execute and a.acknowledge_existing_safety_fail);self.assertIn('functional',c)
    def test_missing_new_a_functional_evidence_still_blocks(self):
        with patch.object(self.engine.b.contracts,'load_campaign',side_effect=ValueError('missing fresh proof')):
            with self.assertRaisesRegex(ValueError,'missing fresh proof'):self.engine.b.admit_newa('/missing',{}, {})
    def test_resume_flags_preserved(self):
        a,c=p.action('run',self.args('--resume-auth','--execute'),self.engine);self.assertTrue(a.resume_auth);self.assertIn('--resume-auth',c)
    def test_default_policy_and_rules_unchanged(self):
        a,c=p.action('run',self.args(),self.engine);self.assertEqual(a.min_http_gain_pct,2);self.assertEqual(a.confirmation_blocks,6);self.assertEqual(a.max_cpu_increase_pct,0)


class DependencyAndDispatch(unittest.TestCase):
    def test_missing_explicit_files_rejected(self):
        a=SimpleNamespace(server='/DOES_NOT_EXIST',model=None,library_dir=[],elf_root=[],marker_elf=None)
        with self.assertRaisesRegex(ValueError,'Frozen server'):p.check_inputs(a)
    def test_low_disk_blocks_before_any_compile(self):
        a=SimpleNamespace(output=p.ROOT.parent)
        with patch.object(p.os,'getuid',return_value=1000),patch.object(p.os,'sched_getscheduler',return_value=0),patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=1)),patch.object(p.subprocess,'run')as run:
            with self.assertRaisesRegex(ValueError,'DISK_SPACE_BLOCKED'):p.dependencies(a,False)
            run.assert_not_called()
    def test_missing_dependencies_not_auto_installed(self):
        a=SimpleNamespace(output=p.ROOT.parent)
        with patch.object(p.os,'getuid',return_value=1000),patch.object(p.os,'sched_getscheduler',return_value=0),patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=2*p.RESERVE)),patch.object(p.shutil,'which',return_value=None),patch.object(p.subprocess,'run')as run:
            with self.assertRaisesRegex(ValueError,'Missing dependencies'):p.dependencies(a,False)
            run.assert_not_called()
    def test_root_controller_rejected(self):
        with patch.object(p.os,'getuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'normal SCHED_OTHER'):p.dependencies(SimpleNamespace(),False)
    def test_check_mode_cannot_launch_engine(self):
        with patch.object(p,'dependencies',return_value={'status':'SYNTHETIC'}),patch.object(p.os,'execv')as call:
            self.assertEqual(p.main(['--check','--server','/FROZEN','--model','/MODEL']),0);call.assert_not_called()
    def test_run_exec_argv_preserves_terminal_and_records_package(self):
        with tempfile.TemporaryDirectory()as folder,patch.object(p,'dependencies',return_value={'status':'SYNTHETIC_NOT_ADMISSION'}),patch.object(p.os,'execv')as call:
            output=Path(folder)/'session'
            p.main(['--server','/FROZEN','--model','/MODEL','--output',str(output),'--execute','--acknowledge-existing-safety-fail'])
            self.assertFalse(output.exists());self.assertTrue(call.called)
            rows=list(Path(folder).glob('*.private.json'));self.assertEqual(len(rows),1)
            r=json.loads(rows[0].read_text());self.assertEqual(r['package']['status'],'PACKAGE_INTEGRITY_PASS');self.assertFalse(r['automatic_retry'])
    def test_original_source_has_no_bpf_policy_changes(self):
        policy=json.loads((p.RUNTIME/'tools/sched_ext/new_a_final/policy-manifest.json').read_text())
        for name,digest in policy['files'].items():self.assertEqual(p.sha(p.RUNTIME/name),digest)


if __name__=='__main__':unittest.main(verbosity=2)
