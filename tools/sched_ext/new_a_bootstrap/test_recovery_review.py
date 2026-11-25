#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read-only recovery proofs and blocking gates; all power writes are mocked."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import auto_system as system,power_state as p,bootstrap
from test_power_state import FakePower,trial
spec=importlib.util.spec_from_file_location('recovery_cli',Path(__file__).resolve().parents[3]/'scripts/check-autotune-recovery.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


class Review(unittest.TestCase):
    def recovered(self,base):
        folder=Path(base)/'runs/bootstrap-SYNTHETIC';folder.mkdir(parents=True)
        fake=FakePower(20)
        with fake.patched():
            journal=trial(fake,folder);system.restore(journal,lambda:True)
        (folder/'bootstrap-result.private.json').write_text(json.dumps(dict(status='BLOCKED_OR_STOPPED',error='AttributeError: SYNTHETIC_CLOCK_FAILURE',power_restore_status='RESTORED')))
        return fake,journal
    def test_full_restore_proof_and_old_failure_bytes_preserved(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);before=journal.read_bytes();report=journal.with_name('bootstrap-result.private.json').read_bytes()
            with fake.patched():value=cli.review(tmp,'bootstrap-SYNTHETIC',True)
            self.assertEqual(value['status'],'RESTORE_EVIDENCE_VERIFIED');self.assertEqual(value['current_state'],'MATCHES_SAVED_ORIGINAL')
            self.assertEqual(value['historical_run_status'],'BLOCKED_OR_STOPPED');self.assertFalse(value['historical_failure_reclassified'])
            self.assertEqual(journal.read_bytes(),before);self.assertEqual(journal.with_name('bootstrap-result.private.json').read_bytes(),report)
    def test_restored_label_without_attempt_cannot_pass(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);row=json.loads(journal.read_text());row['attempt_history']=[];journal.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'attempt evidence'):system.audit_restoration(journal)
    def test_missing_restore_proof_is_unverified(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);Path(json.loads(journal.read_text())['attempt_history'][-1]).unlink()
            with self.assertRaises(OSError):system.audit_restoration(journal)
    def test_saved_epp_mismatch_cannot_pass(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);proof=Path(json.loads(journal.read_text())['attempt_history'][-1]);row=json.loads(proof.read_text())
            key=fake.folders[0]+'/'+p.EPP;row['observed'][1]['sysfs'][key]='power';proof.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'readback mismatch'):system.audit_restoration(journal)
    def test_historical_command_error_cannot_be_hidden_by_matching_readback(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);proof=Path(json.loads(journal.read_text())['attempt_history'][-1]);row=json.loads(proof.read_text());row['errors']=['PPD_FAILURE'];proof.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'did not PASS'):system.audit_restoration(journal)
    def test_partial_restored_scope_blocks(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);row=json.loads(journal.read_text());row['restored']=row['restored'][:-1];journal.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'scope incomplete'):system.audit_restoration(journal)
    def test_current_power_drift_blocks_current_review(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);fake.state['sysfs'][fake.folders[0]+'/'+p.EPP]='power'
            with fake.patched(),self.assertRaisesRegex(ValueError,'CURRENT_POWER'):cli.review(tmp,'bootstrap-SYNTHETIC',True)
    def test_unknown_team_run_is_not_reported_recovered(self):
        with tempfile.TemporaryDirectory()as tmp,self.assertRaisesRegex(ValueError,'not present'):cli.review(tmp,'bootstrap-NOT-ON-THIS-PC')
    def test_no_power_change_requires_complete_original_snapshot(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)/'runs/bootstrap-SYNTHETIC';root.mkdir(parents=True);fake=FakePower(2)
            system.preregister([],root/'environment.private.json')
            with self.assertRaises(OSError):cli.review(tmp,'bootstrap-SYNTHETIC')
            system.save(root/'before-install-environment.private.json',fake.snapshot())
            with fake.patched():value=cli.review(tmp,'bootstrap-SYNTHETIC',True)
            self.assertEqual(value['status'],'NO_POWER_CHANGE_RECORDED');self.assertFalse(value['historical_failure_reclassified'])
    def test_empty_attempt_list_cannot_hide_prior_power_transition(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);row=json.loads(journal.read_text());row['attempted']=[];journal.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'inconsistent'):system.audit_restoration(journal)
    def test_unresolved_and_legacy_states_not_claimed_complete(self):
        for schema,status in [(2,'RESTORE_REQUIRED'),(1,'RESTORED')]:
            with self.subTest(schema=schema,status=status),tempfile.TemporaryDirectory()as tmp:
                fake,journal=self.recovered(tmp);row=json.loads(journal.read_text());row.update(schema=schema,status=status);journal.write_text(json.dumps(row))
                with self.assertRaises(ValueError):system.audit_restoration(journal)
    def test_invalid_previous_v2_proof_blocks_new_bootstrap_before_install(self):
        with tempfile.TemporaryDirectory()as tmp:
            fake,journal=self.recovered(tmp);row=json.loads(journal.read_text());row['attempt_history']=[];journal.write_text(json.dumps(row))
            with patch.object(bootstrap.os,'isatty',return_value=True),patch.object(bootstrap.system,'install')as install,patch.object(bootstrap.system,'authenticate')as auth:
                with self.assertRaisesRegex(ValueError,'attempt evidence'):bootstrap.run(Mock(ROOT=Path(__file__).resolve().parents[3]),tmp)
                install.assert_not_called();auth.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
