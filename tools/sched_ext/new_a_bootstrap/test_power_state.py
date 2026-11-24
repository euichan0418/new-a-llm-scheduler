# SPDX-License-Identifier: GPL-2.0
"""Simulated Intel HWP/PPD behavior only. No real sysfs writes, sudo, or inference."""
from contextlib import contextmanager,ExitStack
import copy
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
import auto_system as s,power_state as p,auto_run


class FakePower:
    def __init__(self,n=20,ids=None):
        self.folders=['/sys/devices/system/cpu/cpufreq/policy'+str(i)for i in (ids or range(n))]
        self.turbo='/sys/devices/system/cpu/intel_pstate/no_turbo'
        values={self.turbo:'0'};policies={}
        for folder in self.folders:
            values[folder+'/'+p.GOV]='powersave';values[folder+'/'+p.EPP]='balance_performance'
            policies[folder]=dict(driver='intel_pstate',cpus=[folder.rsplit('policy',1)[1]],governors=['powersave','performance'],epp_available=['default','performance','balance_performance','balance_power','power'])
        self.state=dict(schema=2,profile='balanced',sysfs=values,binding=dict(policies=policies,controls=sorted(values),boot='SYNTHETIC_NOT_HOST_ID',online='SYNTHETIC',intel_pstate_status='active'),package_installation_not_automatically_rolled_back=True)
        self.trace=[];self.fail=None;self.skip=None;self.original=self.snapshot()
    def snapshot(self):return copy.deepcopy(self.state)
    def read(self,row):return self.state['profile']if row['kind']=='profile'else self.state['sysfs'][row['path']]
    def write(self,row,value,log):
        key='profile'if row['kind']=='profile'else row['path'];self.trace.append((key,value,self.snapshot()))
        if self.fail and self.fail(key,value):raise ValueError('SYNTHETIC injected write failure')
        if self.skip and self.skip(key,value):return
        if key=='profile':
            pref='performance'if value=='performance'else'balance_performance'if value=='balanced'else'power'
            for folder in self.folders:
                if self.state['sysfs'][folder+'/'+p.GOV]=='performance'and pref!='performance':
                    raise ValueError("Failed to activate CPU driver 'intel_pstate': Error writing energy_performance_preference: Device or resource busy (26)")
                self.state['sysfs'][folder+'/'+p.EPP]=pref
            self.state['profile']=value
        elif key.endswith('/'+p.EPP):
            folder=key.rsplit('/',1)[0]
            if self.state['sysfs'][folder+'/'+p.GOV]=='performance'and value not in ['performance','0']:raise ValueError('EBUSY: HWP performance pins EPP')
            self.state['sysfs'][key]=value
        else:
            self.state['sysfs'][key]=value
            if key.endswith('/'+p.GOV)and value=='performance':self.state['sysfs'][key.rsplit('/',1)[0]+'/'+p.EPP]='performance'
    @contextmanager
    def patched(self):
        with ExitStack()as stack:
            stack.enter_context(patch.object(s,'snapshot',side_effect=self.snapshot));stack.enter_context(patch.object(s,'read_setting',side_effect=self.read));stack.enter_context(patch.object(s,'write_setting',side_effect=self.write));stack.enter_context(patch.object(s,'authenticate'));stack.enter_context(patch.object(p.time,'sleep'))
            yield self
    def performance(self):
        self.state['profile']='performance';self.state['sysfs'][self.turbo]='1'
        for folder in self.folders:self.state['sysfs'][folder+'/'+p.GOV]='performance';self.state['sysfs'][folder+'/'+p.EPP]='performance'


def trial(fake,tmp):
    journal=Path(tmp)/'environment.private.json';rows=s.power_plan();s.preregister(rows,journal);s.apply(rows,journal,approve=Mock());return journal


def same(fake,expected):
    return all(fake.state[k]==expected[k]for k in ['profile','sysfs','binding'])


class IntelTransactions(unittest.TestCase):
    def test_old_profile_first_order_reproduces_reported_busy(self):
        f=FakePower();f.performance()
        with self.assertRaisesRegex(ValueError,'Device or resource busy'):f.write(dict(kind='profile'),'balanced','NOT_A_REAL_LOG')
    def test_twenty_policy_apply_and_restore_exactly(self):
        f=FakePower()
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);self.assertEqual(f.state['profile'],'performance');self.assertEqual(f.state['sysfs'][f.turbo],'1')
            s.restore(j,lambda:True);self.assertTrue(same(f,f.original));self.assertEqual(json.loads(j.read_text())['status'],'RESTORED')
    def test_hwp_unlock_precedes_profile_on_every_policy(self):
        f=FakePower()
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.trace=[];s.restore(j,lambda:True)
            transition=next(t for t in f.trace if t[0]=='profile')
            self.assertTrue(all(transition[2]['sysfs'][folder+'/'+p.GOV]=='powersave'for folder in f.folders))
    def test_epp_restored_before_original_performance_governor(self):
        f=FakePower(2);f.performance();f.state['sysfs'][f.turbo]='0';f.original=f.snapshot()
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);s.restore(j,lambda:True);self.assertTrue(same(f,f.original))
    def test_turbo_enabled_only_after_policy_restoration(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.trace=[];s.restore(j,lambda:True)
            self.assertEqual(f.trace[-1][:2],(f.turbo,'0'));self.assertEqual(f.trace[-1][2]['profile'],'balanced')
    def test_profile_failure_stays_failed_and_turbo_stays_off(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.fail=lambda key,value:key=='profile'and value=='balanced'
            with self.assertRaises(ValueError):s.restore(j,lambda:True)
            self.assertEqual(json.loads(j.read_text())['status'],'RESTORE_REQUIRED');self.assertEqual(f.state['sysfs'][f.turbo],'1')
    def test_silent_epp_write_failure_cannot_pass_readback(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.skip=lambda key,value:key.endswith('/'+p.EPP)and value=='balance_performance'
            # Simulate a daemon that reports profile change but leaves one EPP at performance.
            write=f.write
            def faulty(row,value,log):
                write(row,value,log)
                if row['kind']=='profile'and value=='balanced':f.state['sysfs'][f.folders[-1]+'/'+p.EPP]='performance'
            with patch.object(s,'write_setting',side_effect=faulty):
                with self.assertRaisesRegex(ValueError,'READBACK_MISMATCH'):s.restore(j,lambda:True)
            self.assertEqual(json.loads(j.read_text())['status'],'RESTORE_REQUIRED')
    def test_silent_governor_failure_cannot_pass(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.skip=lambda key,value:key==f.folders[-1]+'/'+p.GOV and value=='powersave'
            with self.assertRaises(ValueError):s.restore(j,lambda:True)
            self.assertNotEqual(json.loads(j.read_text())['status'],'RESTORED')
    def test_partial_apply_marks_full_daemon_scope_before_write(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            rows=s.power_plan();j=Path(tmp)/'j';s.preregister(rows,j);f.fail=lambda key,value:key=='profile'
            with self.assertRaises(ValueError):s.apply(rows,j,approve=Mock())
            self.assertEqual(json.loads(j.read_text())['attempted'],list(range(len(rows))))
            f.fail=None;s.restore(j,lambda:True);self.assertTrue(same(f,f.original))
    def test_command_failure_is_not_hidden_even_if_final_values_match(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);write=f.write
            def failure_after_effect(row,value,log):
                write(row,value,log)
                if row['kind']=='profile'and value=='balanced':raise ValueError('driver returned failure after partial effects')
            with patch.object(s,'write_setting',side_effect=failure_after_effect):
                with self.assertRaises(ValueError):s.restore(j,lambda:True)
            records=[json.loads(Path(x).read_text())for x in json.loads(j.read_text())['attempt_history']]
            self.assertEqual(records[-1]['status'],'FAIL')
    def test_failed_attempt_survives_later_verified_restore(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.fail=lambda key,value:key=='profile'
            with self.assertRaises(ValueError):s.restore(j,lambda:True)
            failed=Path(json.loads(j.read_text())['attempt_history'][-1]);raw=failed.read_bytes();f.fail=None
            s.restore(j,lambda:True);self.assertEqual(failed.read_bytes(),raw);self.assertEqual(json.loads(j.read_text())['status'],'RESTORED')
    def test_auth_expired_restore_does_not_claim_success(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp)
            with patch.object(s,'write_setting',side_effect=ValueError('sudo: a password is required')):
                with self.assertRaises(ValueError):s.restore(j,lambda:True)
            self.assertEqual(json.loads(j.read_text())['status'],'RESTORE_REQUIRED')
    def test_unknown_cleanup_prevents_all_power_writes(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.trace=[]
            with self.assertRaisesRegex(ValueError,'CLEANUP_NOT_VERIFIED'):s.restore(j,lambda:False)
            self.assertEqual(f.trace,[])
    def test_stale_restored_indices_do_not_skip_readback_or_writes(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);v=json.loads(j.read_text());v['restored']=list(range(len(v['changes'])));s.save(j,v)
            s.restore(j,lambda:True);self.assertTrue(same(f,f.original))
    def test_already_restored_drift_is_detected(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);s.restore(j,lambda:True);f.state['sysfs'][f.turbo]='1'
            with self.assertRaisesRegex(ValueError,'READBACK_MISMATCH'):s.restore(j,lambda:True)
    def test_driver_mode_change_is_blocked_without_switching_it(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.state['binding']['intel_pstate_status']='passive';f.trace=[]
            with self.assertRaisesRegex(ValueError,'DOMAIN_CHANGED'):s.restore(j,lambda:True)
            self.assertEqual(f.trace,[])
    def test_boot_change_is_blocked(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);f.state['binding']['boot']='OTHER_BOOT';f.trace=[]
            with self.assertRaisesRegex(ValueError,'DOMAIN_CHANGED'):s.restore(j,lambda:True)
            self.assertEqual(f.trace,[])
    def test_hotplug_or_epp_scope_change_blocks(self):
        f=FakePower(2)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);del f.state['sysfs'][f.folders[-1]+'/'+p.EPP];f.trace=[]
            with self.assertRaisesRegex(ValueError,'DOMAIN_CHANGED'):s.restore(j,lambda:True)
            self.assertEqual(f.trace,[])
    def test_noncontiguous_cpu_ids_above_63(self):
        f=FakePower(ids=[0,2,64,127])
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);s.restore(j,lambda:True);self.assertTrue(same(f,f.original))
    def test_different_original_epp_values_preserved(self):
        f=FakePower(3);f.state['sysfs'][f.folders[1]+'/'+p.EPP]='balance_power';f.state['sysfs'][f.folders[2]+'/'+p.EPP]='140';f.original=f.snapshot()
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);s.restore(j,lambda:True);self.assertTrue(same(f,f.original))
    def test_inconsistent_original_performance_epp_is_rejected(self):
        f=FakePower(1);f.state['sysfs'][f.folders[0]+'/'+p.GOV]='performance'
        with f.patched():
            with self.assertRaisesRegex(ValueError,'Inconsistent'):s.power_plan()
    def test_old_snapshot_cannot_start_unrecorded_epp_trial(self):
        f=FakePower(1)
        with f.patched():
            with self.assertRaisesRegex(ValueError,'NOT_RECORDED'):s.power_plan(dict(sysfs={},profile='balanced'))
    def test_incomplete_v2_scope_rejected(self):
        f=FakePower(1)
        with f.patched():
            rows=s.power_plan();rows=[r for r in rows if r.get('path')!=f.folders[0]+'/'+p.EPP]
            with self.assertRaisesRegex(ValueError,'scope incomplete'):p.unpack(rows)
    def test_path_and_value_allowlist(self):
        self.assertTrue(s.allowed_setting('/sys/devices/system/cpu/cpufreq/policy19/'+p.EPP,'balance_performance'))
        self.assertFalse(s.allowed_setting('/sys/devices/system/cpu/cpufreq/policy0/'+p.EPP,'256'))
        self.assertFalse(s.allowed_setting('/sys/devices/system/cpu/intel_pstate/status','active'))
        self.assertFalse(s.allowed_setting('/etc/sudoers','1'))
    def test_snapshot_captures_epp_driver_and_policy_scope(self):
        actual_path=Path
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp)
            def mapped(value):return root/str(value).lstrip('/')
            cpu=mapped('/sys/devices/system/cpu');policy=cpu/'cpufreq/policy0';policy.mkdir(parents=True)
            for name,value in {p.GOV:'powersave','scaling_driver':'intel_pstate','related_cpus':'0', 'scaling_available_governors':'powersave performance',p.EPP:'balance_performance','energy_performance_available_preferences':'performance balance_performance power'}.items():(policy/name).write_text(value)
            (cpu/'cpu0').mkdir();(cpu/'cpu0/cpufreq').symlink_to(policy);(cpu/'online').write_text('0')
            (cpu/'intel_pstate').mkdir();(cpu/'intel_pstate/no_turbo').write_text('0');(cpu/'intel_pstate/status').write_text('active')
            boot=mapped('/proc/sys/kernel/random/boot_id');boot.parent.mkdir(parents=True);boot.write_text('SYNTHETIC')
            with patch.object(p,'Path',side_effect=mapped),patch.object(s,'allowed_setting',return_value=True):v=p.capture()
            self.assertEqual(v['sysfs'][str(policy/p.EPP)],'balance_performance');self.assertEqual(v['binding']['policies'][str(policy)]['driver'],'intel_pstate');self.assertEqual(v['binding']['intel_pstate_status'],'active')
    def test_interrupt_during_apply_preserves_scope_for_restore(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=Path(tmp)/'j';rows=s.power_plan()
            with patch.object(s,'write_setting',side_effect=KeyboardInterrupt('SYNTHETIC_INTERRUPT')):
                with self.assertRaises(KeyboardInterrupt):s.apply(rows,j,approve=Mock())
            self.assertEqual(json.loads(j.read_text())['status'],'APPLY_FAILED');s.restore(j,lambda:True)
            self.assertTrue(same(f,f.original))
    def test_failure_to_record_plan_prevents_power_write(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=Path(tmp)/'j';rows=s.power_plan();s.preregister(rows,j)
            with patch.object(s,'save',side_effect=OSError('SYNTHETIC_DISK_FULL')):
                with self.assertRaises(OSError):s.apply(rows,j,approve=Mock())
            self.assertEqual(f.trace,[])
    def test_noop_keeps_no_auth_or_write(self):
        f=FakePower(1);f.performance()
        with tempfile.TemporaryDirectory()as tmp,f.patched(),patch.object(s,'authenticate')as auth:
            self.assertEqual(s.power_plan(),[]);j=Path(tmp)/'j';s.preregister([],j);s.apply([],j);s.restore(j,lambda:False);auth.assert_not_called();self.assertEqual(f.trace,[])


class ManualRecovery(unittest.TestCase):
    def legacy(self,tmp,f):
        changes=[dict(kind='profile',before='balanced',after='performance')]+[dict(kind='sysfs',path=path,before=value,after='performance'if path.endswith('/'+p.GOV)else'1')for path,value in f.original['sysfs'].items()if not path.endswith('/'+p.EPP)]
        j=Path(tmp)/'environment.private.json';s.save(j,dict(schema=1,changes=changes,attempted=list(range(len(changes))),restored=[],status='RESTORE_REQUIRED',restore_errors=['ORIGINAL_REPORTED_EBUSY']));return j
    def test_legacy_restore_refuses_to_guess_original_epp(self):
        f=FakePower(20)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);raw=j.read_bytes()
            with self.assertRaisesRegex(ValueError,'LEGACY_EPP'):s.restore(j,lambda:True)
            self.assertEqual(j.read_bytes(),raw);self.assertEqual(f.trace,[])
    def test_manual_healthy_twenty_policy_baseline_keeps_failure_bytes(self):
        f=FakePower(20)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);raw=j.read_bytes();e=s.pending_recovery(j,lambda:True,approve=Mock())
            self.assertEqual(j.read_bytes(),raw);v=json.loads(e.read_text());self.assertEqual(v['original_epp'],'NOT_RECORDED');self.assertFalse(v['historical_failure_reclassified']);self.assertEqual(f.trace,[])
    def test_manual_review_decline_keeps_pending(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f)
            with self.assertRaisesRegex(ValueError,'DECLINED'):s.pending_recovery(j,lambda:True,approve=Mock(side_effect=ValueError('DECLINED')))
            self.assertEqual(list(Path(tmp).glob('*.manual-baseline-*')),[])
    def test_wrong_current_governor_not_accepted_as_manual_recovery(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);f.performance()
            with self.assertRaisesRegex(ValueError,'NOT_VERIFIED'):s.pending_recovery(j,lambda:True,approve=Mock())
    def test_check_only_cannot_approve_legacy(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f)
            with self.assertRaisesRegex(ValueError,'REVIEW_REQUIRED'):s.pending_recovery(j,lambda:True,readonly=True)
    def test_attestation_revalidated_against_journal_hash_and_live_state(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);e=s.pending_recovery(j,lambda:True,approve=Mock())
            self.assertEqual(s.pending_recovery(j,lambda:True,readonly=True),e)
            v=json.loads(j.read_text());v['restore_errors'].append('ADDITIONAL_EVIDENCE');s.save(j,v)
            with self.assertRaisesRegex(ValueError,'REVIEW_REQUIRED'):s.pending_recovery(j,lambda:True,readonly=True)
    def test_attestation_drift_requires_new_review(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);s.pending_recovery(j,lambda:True,approve=Mock());f.state['sysfs'][f.folders[0]+'/'+p.EPP]='balance_power'
            with self.assertRaisesRegex(ValueError,'REVIEW_REQUIRED'):s.pending_recovery(j,lambda:True,readonly=True)
    def test_v2_manual_recovery_requires_original_epp(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);v=json.loads(j.read_text());v['status']='RESTORE_REQUIRED';s.save(j,v)
            f.state=copy.deepcopy(f.original);f.state['sysfs'][f.folders[0]+'/'+p.EPP]='balance_power'
            with self.assertRaisesRegex(ValueError,'NOT_VERIFIED'):s.pending_recovery(j,lambda:True,approve=Mock())
    def test_v2_manual_recovery_is_distinct_from_restoring_failed_attempt(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=trial(f,tmp);v=json.loads(j.read_text());v['status']='RESTORE_REQUIRED';s.save(j,v);raw=j.read_bytes();f.state=copy.deepcopy(f.original)
            e=s.pending_recovery(j,lambda:True,approve=Mock());self.assertEqual(j.read_bytes(),raw);self.assertEqual(json.loads(e.read_text())['original_epp'],'VERIFIED_AGAINST_V2')
    def test_unknown_original_profile_not_inferred(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f);v=json.loads(j.read_text());v['changes']=v['changes'][1:];s.save(j,v)
            with self.assertRaisesRegex(ValueError,'BASELINE_INCOMPLETE'):s.pending_recovery(j,lambda:True,approve=Mock())
    def test_manual_confirmation_race_is_rejected(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f)
            def change(_):f.state['sysfs'][f.turbo]='1'
            with self.assertRaisesRegex(ValueError,'changed during approval'):s.pending_recovery(j,lambda:True,approve=change)
    def test_unclean_host_cannot_accept_manual_baseline(self):
        f=FakePower(1)
        with tempfile.TemporaryDirectory()as tmp,f.patched():
            j=self.legacy(tmp,f)
            with self.assertRaisesRegex(ValueError,'CLEANUP_NOT_VERIFIED'):s.pending_recovery(j,lambda:False,approve=Mock())


class Signals(unittest.TestCase):
    def test_sigterm_preparation_raises_and_restores_handlers(self):
        previous=signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt):
            with auto_run.preparation_signals():signal.raise_signal(signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM),previous)


if __name__=='__main__':unittest.main(verbosity=2)
