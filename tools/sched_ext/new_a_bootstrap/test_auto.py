# SPDX-License-Identifier: GPL-2.0
"""Automatic discovery/preparation tests; all privileged operations are mocked."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import auto_assets as a,auto_system as s,auto_run as run,package_driver as driver


def fixture(root):
    paths=[];rows=[]
    for name,role,marker in [('llama-server','server',False),('libfixture.so','library',True)]:
        path=root/name;path.write_bytes(b'\x7fELF\x02\x01'+name.encode());path.chmod(0o755)
        rows.append(dict(name=name,role=role,phase_marker=marker,size=path.stat().st_size,sha256=a.digest(path),soname=name if role=='library'else None));paths.append(path)
    def string(text):b=text.encode();return struct.pack('<Q',len(b))+b
    model=root/'model.gguf';model.write_bytes(b'GGUF'+struct.pack('<IQQ',3,0,2)+string('general.architecture')+struct.pack('<I',8)+string('SYNTHETIC')+string('general.name')+struct.pack('<I',8)+string('NOT_MEASURED'))
    return dict(profile='SYNTHETIC_NOT_RUNTIME',artifacts=rows,model=dict(size=model.stat().st_size,sha256=a.digest(model),architecture='SYNTHETIC',name='NOT_MEASURED'),reproducible_frozen_build=dict(available=False,reason='NO_VERIFIED_BUILD_RECIPE'))


class Discovery(unittest.TestCase):
    def test_valid_assets_and_model_identity(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);v=a.discover(pins,[p]);self.assertEqual(v['model'],str(p/'model.gguf'));self.assertEqual(len(v['artifacts']),2)
    def test_missing_frozen_does_not_build_upstream(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);(p/'llama-server').unlink()
            with self.assertRaisesRegex(ValueError,'FROZEN_ASSETS_MISSING'):a.discover(pins,[p])
    def test_missing_model_does_not_download(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);(p/'model.gguf').unlink()
            with self.assertRaisesRegex(ValueError,'MODEL_NOT_FOUND'):a.discover(pins,[p])
    def test_unapproved_same_size_server_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);f=p/'llama-server';f.write_bytes(b'x'*f.stat().st_size)
            with self.assertRaisesRegex(ValueError,'FROZEN_ASSETS_MISSING'):a.discover(pins,[p])
    def test_equivalent_copies_deduplicated(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);folder=p/'copy';folder.mkdir()
            for f in list(p.iterdir()):
                if f.is_file():(folder/f.name).write_bytes(f.read_bytes())
            v=a.discover(pins,[p]);self.assertEqual(len(v['equivalent_models']),2);self.assertEqual(len(v['artifacts'][0]['equivalent_copies']),2)
    def test_conflicting_approved_servers_blocked(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);row=dict(pins['artifacts'][0],name='other-server');(p/row['name']).write_bytes((p/'llama-server').read_bytes());pins['artifacts'].append(row)
            with self.assertRaisesRegex(ValueError,'AMBIGUOUS_APPROVED_SERVER'):a.discover(pins,[p])
    def test_bounded_search_is_not_partial_success(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p)
            with self.assertRaisesRegex(ValueError,'DISCOVERY_INCOMPLETE'):a.discover(pins,[p],max_entries=1)
    def test_symlink_model_duplicate_no_double_count(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);(p/'same.gguf').symlink_to(p/'model.gguf');v=a.discover(pins,[p]);self.assertEqual(len(v['equivalent_models']),1)
    def test_stage_only_approved_library_aliases(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);v=a.discover(pins,[p]);stage=a.stage_assets(v,pins,p/'staged');self.assertEqual([x.name for x in stage.iterdir()],['libfixture.so']);self.assertTrue((stage/'libfixture.so').is_symlink())
    def test_changed_asset_after_discovery_blocked(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);v=a.discover(pins,[p]);(p/'libfixture.so').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'Frozen changed'):a.stage_assets(v,pins,p/'staged')
    def test_model_metadata_mismatch_blocked(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp);pins=fixture(p);pins['model']['name']='OTHER'
            with self.assertRaisesRegex(ValueError,'MODEL_IDENTITY_MISMATCH'):a.discover(pins,[p])
    def test_gguf_header_truncation(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp)/'broken.gguf';p.write_bytes(b'GGUF')
            with self.assertRaisesRegex(ValueError,'Truncated'):a.gguf_identity(p)
    def test_shared_libraries_use_server_interpreter(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);pins=fixture(root);v=a.discover(pins,[root])
            for row in v['artifacts']:
                with open(row['path'],'ab')as f:f.write(b' server-compute-profile-mode threads-batch threads-http')
            contracts=SimpleNamespace(interpreter=Mock(return_value='/SYNTHETIC_LOADER'),resolve=Mock(return_value=[]))
            with patch.object(a.subprocess,'check_output',return_value='0000 T llama_scx_phase_v1\n'):
                a.validate_elf(v,root,contracts)
            contracts.interpreter.assert_called_once_with(str(root/'llama-server'))
    def test_named_newa_project_directly_under_home_found(self):
        with tempfile.TemporaryDirectory()as tmp:
            h=Path(tmp);(h/'new-a-llm-scheduler').mkdir();package=h/'Downloads/pkg';package.mkdir(parents=True)
            with patch.object(Path,'cwd',return_value=h):v=a.roots(package,h)
            self.assertIn(h/'new-a-llm-scheduler',v);self.assertNotIn(h,v)
    def test_search_does_not_scan_entire_home(self):
        with tempfile.TemporaryDirectory()as tmp:
            h=Path(tmp);(h/'projects').mkdir();(h/'Downloads').mkdir();package=h/'projects/pkg';package.mkdir()
            with patch.object(Path,'cwd',return_value=h):v=a.roots(package,h)
            self.assertNotIn(h,v);self.assertIn(h/'projects',v)


class PowerAndApproval(unittest.TestCase):
    def test_only_known_kernel_controls_permitted(self):
        self.assertFalse(s.allowed_setting('/etc/sudoers','1'));self.assertFalse(s.allowed_setting('/sys/devices/system/cpu/cpufreq/policy0/scaling_governor','malicious'))
        self.assertTrue(s.allowed_setting('/sys/devices/system/cpu/intel_pstate/no_turbo','1'))
    def test_unapproved_symlink_root_write_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            p=Path(tmp)/'alias';p.symlink_to('/sys/devices/system/cpu/intel_pstate/no_turbo')
            with patch.object(s,'root_command')as root:
                with self.assertRaisesRegex(ValueError,'canonical'):s.write_setting(dict(kind='sysfs',path=str(p)),'1',Path(tmp)/'log')
                root.assert_not_called()
    def test_consent_decline_changes_nothing(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'authenticate')as auth,patch.object(s,'write_setting')as write:
            with self.assertRaisesRegex(ValueError,'DECLINED'):s.apply([dict(kind='profile',before='balanced',after='performance')],Path(tmp)/'j',approve=Mock(side_effect=ValueError('DECLINED')))
            auth.assert_not_called();write.assert_not_called()
    def test_auth_failure_does_not_write(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'authenticate',side_effect=ValueError('AUTH_EXPIRED')),patch.object(s,'write_setting')as write:
            with self.assertRaisesRegex(ValueError,'AUTH_EXPIRED'):s.apply([dict(kind='profile',before='balanced',after='performance')],Path(tmp)/'j',approve=Mock())
            write.assert_not_called()
    def test_partial_change_attempt_logged_before_write(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'authenticate'),patch.object(s,'write_setting',side_effect=ValueError('failed write')):
            j=Path(tmp)/'j'
            with self.assertRaises(ValueError):s.apply([dict(kind='profile',before='balanced',after='performance')],j,approve=Mock())
            self.assertEqual(json.loads(j.read_text())['attempted'],[0])
    def test_restore_profile_first_then_governors(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'write_setting')as write:
            j=Path(tmp)/'j';changes=[dict(kind='profile',before='balanced',after='performance'),dict(kind='sysfs',path='/sys/devices/system/cpu/cpufreq/policy0/scaling_governor',before='powersave',after='performance')]
            s.save(j,dict(schema=1,changes=changes,attempted=[0,1],restored=[],status='APPLIED'));s.restore(j,lambda:True)
            self.assertEqual([c.args[0]['kind']for c in write.call_args_list],['profile','sysfs']);self.assertEqual(json.loads(j.read_text())['status'],'RESTORED')
    def test_cleanup_unknown_blocks_restore(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'write_setting')as write:
            j=Path(tmp)/'j';s.save(j,dict(schema=1,changes=[dict(kind='profile',before='balanced',after='performance')],attempted=[0],restored=[],status='APPLIED'))
            with self.assertRaisesRegex(ValueError,'CLEANUP_NOT_VERIFIED'):s.restore(j,lambda:False)
            write.assert_not_called()
    def test_restore_auth_expiry_is_not_success(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'write_setting',side_effect=ValueError('AUTH_EXPIRED')):
            j=Path(tmp)/'j';s.save(j,dict(schema=1,changes=[dict(kind='profile',before='balanced',after='performance')],attempted=[0],restored=[],status='APPLIED'))
            with self.assertRaisesRegex(ValueError,'RESTORE_REQUIRED'):s.restore(j,lambda:True)
            self.assertEqual(json.loads(j.read_text())['status'],'RESTORE_REQUIRED')
    def test_no_changes_need_no_auth(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'authenticate')as auth:
            j=Path(tmp)/'j';s.apply([],j);s.restore(j,lambda:False);auth.assert_not_called()
    def test_failed_privileged_command_recorded(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='sudo: a password is required'))as call:
            log=Path(tmp)/'log'
            with self.assertRaisesRegex(ValueError,'PRIVILEGED_PREPARATION_FAILED'):s.root_command(['/usr/bin/tee','/approved/path'],log,data='1\n')
            self.assertEqual(call.call_args.args[0][:3],['/usr/bin/sudo','-N','-n']);self.assertIn('password is required',log.read_text())
    def test_apt_invalid_package_name_rejected_before_privilege(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s,'authenticate')as auth:
            with self.assertRaises(ValueError):s.install(['bad;command'],tmp)
            auth.assert_not_called()
    def test_installation_space_accounts_for_dependencies_and_archives(self):
        with patch.object(s.subprocess,'check_output',return_value='Installed-Size: 100\nSize: 500\n'):
            v=s.installation_space('Inst libfixture (1.0 Ubuntu)\n')
            self.assertEqual(v['estimated_new_bytes'],102900);self.assertEqual(v['required_free_bytes'],102900+512*1024*1024)
    def test_unknown_installation_footprint_blocks(self):
        with patch.object(s.subprocess,'check_output',return_value='Size: 500\n'):
            with self.assertRaisesRegex(ValueError,'DISK_REQUIREMENT_NOT_VERIFIED'):s.installation_space('Inst libfixture (1.0 Ubuntu)\n')
    def test_apt_upgrade_plan_blocked(self):
        with tempfile.TemporaryDirectory()as tmp,patch.object(s.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Inst libc6 [old] (new)\n',stderr='')),patch.object(s,'authenticate')as auth:
            with self.assertRaisesRegex(ValueError,'UPGRADE_OR_REMOVAL'):s.install(['libbpf-dev'],tmp)
            auth.assert_not_called()


class AutoExecution(unittest.TestCase):
    def test_auto_check_never_install_modify_or_infer(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)/'package';root.mkdir();pins=fixture(root);(root/'AUTO_ASSET_PINS.json').write_text(json.dumps(pins));mockdriver=SimpleNamespace(ROOT=root,nearest=driver.nearest,RESERVE=driver.RESERVE,engine=Mock())
            with patch.object(s,'missing_packages',return_value=['missing']),patch.object(s,'install')as install,patch.object(s,'apply')as apply,patch.object(run,'child')as child:
                self.assertEqual(run.run(mockdriver,check=True,search_roots=[root]),0);install.assert_not_called();apply.assert_not_called();child.assert_not_called()
    def test_missing_files_stops_before_install(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)/'package';root.mkdir();pins=fixture(root);(root/'model.gguf').unlink();(root/'AUTO_ASSET_PINS.json').write_text(json.dumps(pins));d=SimpleNamespace(ROOT=root,nearest=driver.nearest,RESERVE=driver.RESERVE)
            with patch.object(os,'isatty',return_value=True),patch.object(s,'install')as install:
                with self.assertRaisesRegex(ValueError,'MODEL_NOT_FOUND'):run.run(d,search_roots=[root])
                install.assert_not_called();self.assertEqual(len(list(root.parent.glob('new-a-autotune-results/auto-*/REPORT.ko.md'))),1)
    def test_auto_requires_explicit_terminal(self):
        with patch.object(os,'isatty',return_value=False):
            with self.assertRaisesRegex(ValueError,'normal terminal'):run.run(driver)
    def test_existing_pending_restore_blocks_new_experiment(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)/'package';root.mkdir();folder=root.parent/'new-a-autotune-results/auto-old';folder.mkdir(parents=True);(folder/'environment.private.json').write_text(json.dumps(dict(attempted=[0],status='APPLIED')));d=SimpleNamespace(ROOT=root,nearest=driver.nearest,RESERVE=driver.RESERVE)
            with patch.object(os,'isatty',return_value=True),patch.object(a,'discover')as discover:
                with self.assertRaisesRegex(ValueError,'PENDING_ENVIRONMENT_RESTORE'):run.run(d)
                discover.assert_not_called()
    def test_full_auto_handoff_and_restore_with_mocked_experiment(self):
        for code in [0,2,'interrupt']:
            with self.subTest(code=code),tempfile.TemporaryDirectory()as tmp:
                root=Path(tmp)/'package';root.mkdir();pins=fixture(root);(root/'AUTO_ASSET_PINS.json').write_text(json.dumps(pins))
                b=SimpleNamespace(contracts=SimpleNamespace(scheduler_state=Mock(return_value='disabled')),controller=SimpleNamespace(foreign_processes=Mock()),collect=SimpleNamespace(environment=Mock(return_value=dict(ac_online=True,temperature_millic=[dict(value='40000')])),inspect=Mock(return_value=dict(summary='SYNTHETIC'))),probe_build=Mock(return_value='NOT_REAL_PROBE'))
                module=SimpleNamespace(b=b,p=SimpleNamespace(default=Mock(return_value=dict(compute_threads=4,batch_threads=8))))
                def action(mode,argv,module):return SimpleNamespace(output=Path(argv[argv.index('--output')+1])),['SYNTHETIC_NO_SERVER_EXECUTION']
                d=SimpleNamespace(ROOT=root,nearest=driver.nearest,RESERVE=driver.RESERVE,engine=Mock(return_value=module),action=action,dependencies=Mock(return_value={}),admission_record=Mock(),verify=Mock(return_value={}))
                with patch.object(os,'isatty',return_value=True),patch.object(s,'missing_packages',return_value=[]),patch.object(s,'install'),patch.object(a,'validate_elf',return_value=dict(server=str(root/'llama-server'),marker=str(root/'libfixture.so'))),patch.object(s,'consent'),patch.object(s,'power_plan',return_value=[dict(kind='profile',before='balanced',after='performance')]),patch.object(s,'snapshot',return_value=dict(sysfs={},profile=None)),patch.object(s,'preregister'),patch.object(s,'authenticate'),patch.object(s,'write_setting')as write,patch.object(run,'child',return_value=code,side_effect=KeyboardInterrupt()if code=='interrupt'else None):
                    if code==0:self.assertEqual(run.run(d,search_roots=[root]),0)
                    else:
                        with self.assertRaises((ValueError,KeyboardInterrupt)):run.run(d,search_roots=[root])
                    journals=list(root.parent.glob('new-a-autotune-results/auto-*/environment.private.json'))
                    self.assertEqual(json.loads(journals[0].read_text())['status'],'RESTORED');self.assertEqual([c.args[1]for c in write.call_args_list],['performance','balanced'])
    def test_quiet_requires_scheduler_and_no_foreign_processes(self):
        b=SimpleNamespace(contracts=SimpleNamespace(scheduler_state=Mock(return_value='disabled')),controller=SimpleNamespace(foreign_processes=Mock()))
        self.assertTrue(run.quiet(SimpleNamespace(b=b)));b.contracts.scheduler_state.return_value='enabled';self.assertFalse(run.quiet(SimpleNamespace(b=b)))


if __name__=='__main__':unittest.main(verbosity=2)
