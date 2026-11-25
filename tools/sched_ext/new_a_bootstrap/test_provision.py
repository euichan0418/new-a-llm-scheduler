# SPDX-License-Identifier: GPL-2.0
"""Pinned provisioning tests, with no real network/build/privileged actions."""
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import provision as p,bootstrap as b,package_driver as driver


def blob():
    def string(v):v=v.encode();return struct.pack('<Q',len(v))+v
    return b'GGUF'+struct.pack('<IQQ',3,0,2)+string('general.architecture')+struct.pack('<I',8)+string('SYNTHETIC')+string('general.name')+struct.pack('<I',8)+string('NOT_MEASURED')


def recipe():
    data=blob();return dict(model=dict(size=len(data),sha256=hashlib.sha256(data).hexdigest(),architecture='SYNTHETIC',name='NOT_MEASURED',revision='PINNED',url='https://huggingface.co/test/resolve/PINNED/model.gguf',license='SYNTHETIC_NOT_LICENSE_ADVICE'))


def response(data):
    r=io.BytesIO(data);r.headers={'Content-Length':str(len(data))};return r


class Downloads(unittest.TestCase):
    def test_download_verified_then_atomic_publish(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)):
            opener=SimpleNamespace(open=Mock(return_value=response(blob())));root=Path(tmp)
            result=p.model(root,recipe(),opener=opener);self.assertTrue(result.is_file());self.assertTrue(json.loads((root/'download.private.json').read_text())['verified'])
    def test_corruption_retains_partial_not_success(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)):
            data=blob()[:-1]+b'X';root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'INTEGRITY_FAILED'):p.model(root,recipe(),opener=SimpleNamespace(open=Mock(return_value=response(data))))
            self.assertFalse((root/'model.gguf').exists());self.assertEqual(len(list(root.glob('*.partial-*'))),1)
    def test_insufficient_disk_does_not_start_transfer(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            opener=SimpleNamespace(open=Mock())
            with self.assertRaisesRegex(ValueError,'DOWNLOAD_DISK_BLOCKED'):p.model(tmp,recipe(),opener=opener)
            opener.open.assert_not_called()
    def test_cached_hash_revalidated_without_network(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);(root/'model.gguf').write_bytes(blob());opener=SimpleNamespace(open=Mock())
            self.assertEqual(p.model(root,recipe(),opener=opener),root/'model.gguf');opener.open.assert_not_called()
    def test_unpinned_url_blocked(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)):
            r=recipe();r['model']['url']='http://example.invalid/model';opener=SimpleNamespace(open=Mock())
            with self.assertRaisesRegex(ValueError,'Pinned HTTPS'):p.model(tmp,r,opener=opener)
            opener.open.assert_not_called()
    def test_partial_download_blocks_implicit_retry(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)):
            (Path(tmp)/'model.gguf.partial-old').write_bytes(b'partial')
            with self.assertRaisesRegex(ValueError,'Partial model download preserved'):p.model(tmp,recipe(),opener=SimpleNamespace(open=Mock()))
    def test_redirect_to_untrusted_or_cleartext_rejected(self):
        for url in ['http://huggingface.co/x','https://example.invalid/x']:
            with self.assertRaisesRegex(ValueError,'Unapproved'):p.Redirect().redirect_request(None,None,302,'',{},url)


class BuildReuse(unittest.TestCase):
    def test_incomplete_build_not_overwritten(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p,'build_identity',return_value={'SYNTHETIC':True}),patch.object(p,'command')as command:
            with self.assertRaisesRegex(ValueError,'Incomplete engine build'):p.build('/SYNTHETIC',tmp,{},None)
            command.assert_not_called()
    def test_changed_cpu_or_compiler_not_reused(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p,'build_identity',return_value={'cpu':'NEW'}),patch.object(p,'command')as command:
            (Path(tmp)/'reproduction.private.json').write_text(json.dumps(dict(input={'cpu':'OLD'},outputs={})))
            with self.assertRaisesRegex(ValueError,'Build input/CPU/compiler'):p.build('/SYNTHETIC',tmp,{},None)
            command.assert_not_called()
    def test_existing_non_git_source_not_replaced(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(p,'command')as command:
            with self.assertRaisesRegex(ValueError,'without expected Git'):p.source(tmp,{'engine':{'commit':'PINNED'}})
            command.assert_not_called()
    def test_pinned_recipe_preserves_full_and_legacy_contract(self):
        r=json.loads((driver.ROOT/'config/autotune-provision.json').read_text());options=r['engine']['cmake_options']
        self.assertEqual(r['engine']['commit'],'c8b9d61d12b4997d902a03e5dc55a185d18c3bc9');self.assertIn('-DGGML_OPENMP=ON',options);self.assertIn('-DLLAMA_SCX_PHASE_HINT=ON',options);self.assertIn('-DLLAMA_SCX_PHASE_TRACE=OFF',options);self.assertEqual(r['default_scheduler'],'linux')


class Bootstrap(unittest.TestCase):
    def test_check_does_not_acquire_or_build_assets(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(b.system,'missing_packages',return_value=[]),patch.object(p,'source')as source,patch.object(p,'build')as build,patch.object(p,'model')as model:
            self.assertEqual(b.run(driver,tmp,check=True),0);source.assert_not_called();build.assert_not_called();model.assert_not_called()
            row=json.loads(next(Path(tmp).glob('runs/*/bootstrap-result.private.json')).read_text());self.assertEqual(row['status'],'CHECK_ONLY_NOT_RUNTIME_ADMISSION')
    def test_low_disk_blocks_before_install_or_network(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(b.os,'isatty',return_value=True),patch.object(b.shutil,'disk_usage',return_value=SimpleNamespace(free=1)),patch.object(b.system,'missing_packages',return_value=[]),patch.object(b.system,'install')as install,patch.object(p,'source')as source:
            with self.assertRaisesRegex(ValueError,'6GiB'):b.run(driver,tmp)
            install.assert_not_called();source.assert_not_called()
    def test_prepare_success_is_not_experiment_complete_and_failure_preserved(self):
        for failed in [False,True]:
            with self.subTest(failed=failed),tempfile.TemporaryDirectory()as tmp:
                contracts=SimpleNamespace(scheduler_state=Mock(return_value='disabled'),thermal=Mock(return_value=dict(temperatures_c=[40])))
                module=SimpleNamespace(b=SimpleNamespace(universal_build=Mock(return_value=Path('/SYNTHETIC_STATIC_REPORT')),contracts=contracts,controller=SimpleNamespace(foreign_processes=Mock()),collect=SimpleNamespace(environment=Mock(return_value=dict(ac_online=True,temperature_millic=[dict(value='40000')])))))
                fake=SimpleNamespace(ROOT=driver.ROOT,engine=Mock(return_value=module))
                with patch.object(b.os,'isatty',return_value=True),patch.object(b.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),patch.object(b.system,'missing_packages',return_value=[]),patch.object(b.system,'install'),patch.object(b.system,'snapshot',return_value=dict(sysfs={},profile=None)),patch.object(b.system,'power_plan',return_value=[]),patch.object(b.system,'consent'),patch.object(p,'source',return_value=Path('/SYNTHETIC_SOURCE')),patch.object(p,'build',return_value=Path('/SYNTHETIC_BUILD/bin')),patch.object(p,'model',return_value=Path('/SYNTHETIC_MODEL'),side_effect=ValueError('MODEL_INTEGRITY_FAILED')if failed else None),patch.object(b.auto_run,'child')as child:
                    if failed:
                        with self.assertRaisesRegex(ValueError,'MODEL_INTEGRITY_FAILED'):b.run(fake,tmp,prepare_only=True)
                    else:self.assertEqual(b.run(fake,tmp,prepare_only=True),0)
                    child.assert_not_called()
                    row=json.loads(next(Path(tmp).glob('runs/*/bootstrap-result.private.json')).read_text())
                    self.assertEqual(row['status'],'BLOCKED_OR_STOPPED'if failed else'ASSETS_PREPARED_FUNCTIONAL_NOT_RUN')
    def test_no_terminal_no_auto_run(self):
        with patch.object(b.os,'isatty',return_value=False):
            with self.assertRaisesRegex(ValueError,'normal terminal'):b.run(driver,'/NEVER_CREATED')


if __name__=='__main__':unittest.main(verbosity=2)
