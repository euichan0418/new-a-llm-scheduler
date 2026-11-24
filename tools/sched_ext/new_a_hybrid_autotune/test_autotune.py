# SPDX-License-Identifier: GPL-2.0
"""Nonprivileged synthetic measurement/admission tests. No sudo/BPF/inference."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import planning as p,backend as b,experiment as x,autotune as cli
fixture=b.module('tuning_synthetic_cpu_fixture',b.HERE.parent/'new_a_hybrid/test_hybrid.py')


def detection(*args,**kw):return p.collect.detection(*fixture.fixture(*args,**kw))


def metrics(http=100,cpu=100,prefill=60,decode=40):return dict(http_ms=http,process_cpu_s=cpu,prefill_ms=prefill,decode_ms=decode,prefill_tps=920000/prefill,decode_tps=128000/decode,peak_temperature_c=55,observed_process_tid_count=12)


def rows(d,blocks=6,changes=None,stage='CONFIRMATION'):
    base=p.default(d);target=p.candidate(d,d['portable_plan']['physical_e_cores']);result=[]
    changes=changes or [-5]*blocks
    for n,gain in enumerate(changes,1):
        for position,(c,m)in enumerate([(base,metrics()),(target,metrics(http=100+gain,cpu=98,prefill=58,decode=39))],1):result.append(dict(stage=stage,block=n,position=position,candidate=c,metrics=m,raw='SYNTHETIC_NOT_MEASURED'))
    return base,target,result


class Planning(unittest.TestCase):
    def test_formulas_across_hybrid_sizes(self):
        for a,e in [(2,8),(4,8),(6,8),(8,8),(8,16)]:
            d=detection(a,e);c=p.default(d);self.assertEqual((c['compute_threads'],c['batch_threads']),(a,a+e//2));self.assertEqual(len(c['affinity_cpus']),c['batch_threads'])
    def test_i5_original_affinity(self):self.assertEqual(p.default(detection())['affinity_cpus'],[0,2,4,6,8,9,10,11])
    def test_i7_reported_shape_synthetic_only(self):self.assertEqual(p.default(detection(6,8))['affinity_cpus'],[0,2,4,6,8,10,12,13,14,15])
    def test_prefill_step_two_and_default_control_even_e6(self):
        c=p.prefill(detection(6,6));self.assertEqual([v['e_count']for v in c],[0,2,3,4,6])
    def test_odd_e_includes_last_available(self):self.assertEqual([c['e_count']for c in p.prefill(detection(4,5))],[0,2,4,5])
    def test_decode_p6_example_with_enough_affinity(self):
        d=detection(6,8);grid=p.decode(d,p.default(d));self.assertEqual([x['compute_threads']for x in grid],[4,5,6,7,8])
    def test_decode_clipped_to_affinity(self):
        d=detection(6,8);grid=p.decode(d,p.candidate(d,0));self.assertEqual([x['compute_threads']for x in grid],[4,5,6])
    def test_decode_never_changes_batch_or_mask(self):
        d=detection(8,16);base=p.default(d)
        for c in p.decode(d,base):self.assertEqual((c['batch_threads'],c['affinity_cpus']),(base['batch_threads'],base['affinity_cpus']))
    def test_smt_off_not_dividing_logical_by_two(self):
        d=detection(6,8,smt=False);self.assertEqual(p.default(d)['batch_threads'],10)
    def test_restricted_allowed_online_and_unique_physical(self):
        d=detection(4,8,allowed=[1,3,5,7,8,9,10],offline=[15]);c=p.default(d)
        self.assertEqual(c['compute_threads'],4);self.assertEqual(c['e_count'],1);self.assertTrue(set(c['affinity_cpus'])<=set(d['portable_plan']['snapshot']['usable_cpus']))
    def test_unknown_or_nonhybrid_rejected(self):
        d=detection(8,0,hybrid=False)
        with self.assertRaises(ValueError):p.default(d)
    def test_compact_l2_is_existing_deterministic_rule(self):
        d=detection(6,8);c=p.default(d);self.assertEqual(c['e_placement'],'SINGLE_L2_GROUP');self.assertEqual(c,p.default(d))
    def test_missing_l2_blocks_automatic_tuning(self):
        v,c,h=fixture.fixture(6,8)
        for cpu in h['cpus'].values():cpu['caches']=[]
        d=p.collect.detection(v,c,h)
        with self.assertRaisesRegex(ValueError,'L2'):p.default(d)
    def test_possible_pool_contains_all_adaptive_configs(self):
        d=detection(6,8);pool={c['id']for c in p.possible(d)}
        for b in p.prefill(d):self.assertTrue({c['id']for c in p.decode(d,b)}<=pool)
    def test_reverse_two_blocks(self):
        c=p.prefill(detection(6,8));v=p.balanced(c,2);n=len(c);self.assertEqual([x['candidate']['id']for x in v[:n]],list(reversed([x['candidate']['id']for x in v[n:]])))
    def test_six_confirmation_orders_all_positions_balanced(self):
        d=detection();c=[p.candidate(d,n)for n in [0,4,8]];v=p.balanced(c,6)
        for a in c:
            for pos in [1,2,3]:self.assertEqual(sum(x['position']==pos and x['candidate']['id']==a['id']for x in v),2)
    def test_no_silent_rule_changes_or_nan(self):
        for changed in [dict(p.DEFAULT_RULES,min_http_gain_pct=float('nan')),dict(p.DEFAULT_RULES,confirmation_blocks=2),dict(p.DEFAULT_RULES,finalists=4)]:
            with self.assertRaises(ValueError):p.rules(changed)
    def test_common_housekeeping_for_all_candidates(self):
        d=detection(6,8);hk=b.housekeeping(d);union={c for v in p.possible(d)for c in v['affinity_cpus']};self.assertFalse(set(hk)&union)
    def test_shared_housekeeping_requires_ack(self):
        d=detection(6,8,smt=False)
        with self.assertRaises(ValueError):b.housekeeping(d)
        self.assertTrue(set(b.housekeeping(d,True))<=set(p.default(d)['affinity_cpus']))


class Recommendation(unittest.TestCase):
    def setUp(self):self.d=detection();self.r=dict(p.DEFAULT_RULES)
    def test_two_blocks_never_promote(self):
        base,target,data=rows(self.d,2,stage='PREFILL');r=p.report(self.d,data,self.r,'new-a',True);self.assertEqual(r['final_new_a_recommendation'],base)
    def test_tiny_consistent_gain_keeps_default(self):
        base,target,data=rows(self.d,changes=[-.1]*6);self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_inconsistent_blocks_keep_default(self):
        base,target,data=rows(self.d,changes=[-20,-20,-20,-20,-20,1]);self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_large_uncertainty_keeps_default(self):
        base,target,data=rows(self.d,changes=[-.1,-.1,-.1,-.1,-.1,-30]);self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_serious_decode_regression_rejected(self):
        base,target,data=rows(self.d)
        for x in data:
            if x['candidate']==target:x['metrics']['decode_ms']=50
        self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_cpu_increase_rejected(self):
        base,target,data=rows(self.d)
        for x in data:
            if x['candidate']==target:x['metrics']['process_cpu_s']=102
        self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_independent_large_gain_can_promote_new_a_only(self):
        base,target,data=rows(self.d);r=p.report(self.d,data,self.r,'new-a',True);self.assertEqual(r['final_new_a_recommendation'],target);self.assertFalse(r['global_optimum_proven'])
    def test_linux_winner_cannot_become_new_a_policy(self):
        base,target,data=rows(self.d);r=p.report(self.d,data,self.r,'linux',True);self.assertEqual(r['final_new_a_recommendation'],base);self.assertEqual(r['linux_execution_recommendation'],target)
    def test_incomplete_campaign_cannot_promote(self):
        base,target,data=rows(self.d);self.assertEqual(p.report(self.d,data,self.r,'new-a',False)['final_new_a_recommendation'],base)
    def test_fastest_single_sample_not_recommendation(self):
        base,target,data=rows(self.d,changes=[-30,.1,.1,.1,.1,.1]);r=p.report(self.d,data,self.r,'new-a',True);self.assertEqual(r['fastest_single_observation']['candidate'],target);self.assertEqual(r['final_new_a_recommendation'],base)
    def test_no_pooling_exploration_into_confirmation(self):
        base,target,data=rows(self.d,2,stage='PREFILL');b0,t,c=rows(self.d,6,changes=[0]*6);r=p.report(self.d,data+c,self.r,'new-a',True);self.assertEqual(r['statistics_by_stage']['PREFILL'][target['id']]['n'],2);self.assertEqual(r['statistics_by_stage']['CONFIRMATION'][target['id']]['n'],6);self.assertEqual(r['final_new_a_recommendation'],base)
    def test_prefill_winner_and_http_winner_separate(self):
        base,target,data=rows(self.d,2,changes=[1,1],stage='PREFILL');r=p.report(self.d,data,self.r,'new-a',True);self.assertEqual(r['observed_by_stage']['PREFILL']['prefill_highest_mean'],target);self.assertEqual(r['observed_by_stage']['PREFILL']['http_lowest_mean'],base)
    def test_duplicate_paired_samples_rejected(self):
        base,target,data=rows(self.d)
        with self.assertRaises(ValueError):p.promotion(data+[data[0]],base,target,self.r)
    def test_fewer_than_six_is_not_confirmation(self):
        base,target,data=rows(self.d,5);self.assertFalse(p.promotion(data,base,target,self.r)['eligible'])
    def test_default_retained_is_not_equivalence(self):
        base,target,data=rows(self.d,changes=[0]*6);r=p.report(self.d,data,self.r,'new-a',True);self.assertFalse(r['policy_equivalence_proven'])
    def test_screening_requires_two_matching_directions(self):
        base,target,data=rows(self.d,2,changes=[-10,1],stage='PREFILL');self.assertEqual(p.screening(data,base,self.r),[])


class FailureAndAdmission(unittest.TestCase):
    def test_modified_seal_blocks_before_any_execution(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);x.seal(root,{'SYNTHETIC':True});(root/'tuning.private.json').write_text('{}')
            with self.assertRaises(ValueError):x.load(root)
    def test_missing_functional_cannot_attach(self):
        with patch.object(b.contracts,'load_campaign',side_effect=ValueError('FUNCTIONAL_MISSING')),patch.object(b.runner,'acquire_session')as run:
            with self.assertRaises(ValueError):b.admit_newa('/MISSING',{}, {})
            run.assert_not_called()
    def test_no_automatic_failed_retry(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'load_campaign',return_value={}):
            path=Path(temp);b.save(path/'failure.json',dict(kind='THERMAL_FAILURE'));b.save(path/'cleanup.json',dict(status='PASS',errors=[]));record=dict(raw=str(path),variant='SYNTHETIC',slot={})
            with self.assertRaises(ValueError):x.recovered(record,{},False)
    def test_cleanup_unknown_blocks_auth_resume(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'load_campaign',return_value={}):
            path=Path(temp);b.save(path/'failure.json',dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED'));b.save(path/'cleanup.json',dict(status='FAIL',errors=['UNKNOWN']));record=dict(raw=str(path),variant='SYNTHETIC',slot={})
            with self.assertRaises(ValueError):x.recovered(record,{},True)
    def test_success_reverified_no_rerun(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'load_campaign',return_value={}),patch.object(x,'validate_record'),patch.object(b,'verify',return_value={'RAW':'VALID'})as check:
            path=Path(temp);b.save(path/'completion.json',{});record=dict(raw=str(path),variant='SYNTHETIC',slot={});self.assertEqual(x.recovered(record,{},False),{'RAW':'VALID'});check.assert_called_once()
    def test_failed_record_cannot_be_success(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'load_campaign',return_value={}):
            path=Path(temp);b.save(path/'completion.json',{});b.save(path/'failure.json',{});
            with self.assertRaises(ValueError):x.recovered(dict(raw=str(path),variant='S',slot={}),{},True)
    def test_stage_selection_change_rejected(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);x.stage_plan(root,'PREFILL',[])
            with self.assertRaises(ValueError):x.stage_plan(root,'PREFILL',[{'CHANGED':True}])
    def test_approved_plan_loads_without_rewriting_rules(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);b.save(root/'plan.private.json',dict(rules=p.DEFAULT_RULES));value=dict(rules=p.DEFAULT_RULES,plan_sha256=b.sha(root/'plan.private.json'));x.seal(root,value);self.assertEqual(x.load(root),value)
    def test_modified_plan_is_rejected_even_with_original_tuning_seal(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);b.save(root/'plan.private.json',dict(rules=p.DEFAULT_RULES));value=dict(rules=p.DEFAULT_RULES,plan_sha256=b.sha(root/'plan.private.json'));x.seal(root,value);(root/'plan.private.json').write_text(json.dumps(dict(rules=dict(p.DEFAULT_RULES,min_http_gain_pct=0.01))))
            with self.assertRaises(ValueError):x.load(root)
    def test_missing_server_model_never_replaced(self):
        args=Mock(server='/MISSING_FROZEN',model='/MISSING_MODEL')
        with self.assertRaises(ValueError):b.setup(args)
    def test_auth_resume_is_explicit_and_uses_existing_raw_provenance(self):
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'load_campaign',return_value={}),patch.object(b.controller,'safe_auth_resume',return_value=True)as check:
            path=Path(temp);b.save(path/'failure.json',dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED'));b.save(path/'cleanup.json',dict(status='PASS',errors=[]))
            self.assertIsNone(x.recovered(dict(raw=str(path),variant='S',slot={}),{},True));check.assert_called_once()
    def test_no_execute_resume_does_not_authenticate(self):
        args=cli.parser().parse_args(['run','--server','/S','--model','/M','--output','/SYNTHETIC','--resume'])
        tune={'options':cli.options(args)}
        with patch.object(x,'load',return_value=tune),patch.object(b,'current'),patch.object(cli,'analyze',return_value='SAFE'),patch.object(b.runner.subprocess,'run')as auth:
            self.assertEqual(cli.execute(args),'SAFE');auth.assert_not_called()
    def test_changed_selection_margin_rejected_on_resume(self):
        args=cli.parser().parse_args(['run','--server','/S','--model','/M','--output','/SYNTHETIC','--resume','--min-http-gain-pct','0.1'])
        tune={'options':dict(cli.options(args),min_http_gain_pct=2.0)}
        with patch.object(x,'load',return_value=tune),patch.object(b.runner.subprocess,'run')as auth:
            with self.assertRaises(ValueError):cli.execute(args)
            auth.assert_not_called()


class JournalAndPipeline(unittest.TestCase):
    def test_journal_candidate_and_slot_bound_to_raw(self):
        d=detection();c=p.default(d);campaign=dict(host=dict(detection=d),configuration=c,domain=dict(selected=c['affinity_cpus']))
        record=dict(candidate=c,slot=dict(candidate_id=c['id'],tuning_stage='PREFILL',block=1,position=1),stage='PREFILL',block=1,position=1)
        x.validate_record(record,campaign)
        for field,value in [('batch_threads',999),('affinity_cpus',[0]),('id','forged')]:
            bad=copy.deepcopy(record);bad['candidate'][field]=value
            with self.assertRaises(ValueError):x.validate_record(bad,campaign)
        bad=copy.deepcopy(record);bad['stage']='CONFIRMATION'
        with self.assertRaises(ValueError):x.validate_record(bad,campaign)
    def test_nonfinite_metrics_never_ranked(self):
        base,target,data=rows(detection());data[0]['metrics']['http_ms']=float('nan')
        with self.assertRaises(ValueError):p.report(detection(),data,p.DEFAULT_RULES,'new-a',True)
    def test_complete_adaptive_pipeline_with_synthetic_backend(self):
        d=detection();tune=dict(host=dict(detection=d),rules=dict(p.DEFAULT_RULES),limits=dict(seconds=60,sessions=160),scheduler='linux')
        class Synthetic(x.Experiment):
            def measure(self,stage,visit):
                c=visit['candidate'];base=p.default(d);best=c['e_count']==8 and c['compute_threads']==4
                self.rows.append(dict(stage=stage,candidate=c,block=visit['block'],position=visit['position'],metrics=metrics(http=90 if best else 100,cpu=95 if best else 100,prefill=55 if best else 60,decode=38 if best else 40),raw='SYNTHETIC_NOT_A_SESSION'))
        with tempfile.TemporaryDirectory()as temp,patch.object(x,'export',side_effect=lambda root,tune,data,complete:p.report(d,data,tune['rules'],'linux',complete)):
            run=Synthetic(Path(temp),tune);result=run.run();self.assertTrue(result['complete']);self.assertEqual(result['final_new_a_recommendation'],p.default(d));self.assertTrue(any(r['stage']=='CONFIRMATION'for r in run.rows));self.assertEqual(result['linux_execution_recommendation']['e_count'],8)
    def test_failed_pipeline_stops_and_exports_partial(self):
        d=detection();tune=dict(host=dict(detection=d),rules=dict(p.DEFAULT_RULES),limits=dict(seconds=60,sessions=160),scheduler='linux')
        class Failed(x.Experiment):
            def measure(self,stage,visit):raise ValueError('SYNTHETIC_THERMAL_FAIL')
        calls=[]
        with tempfile.TemporaryDirectory()as temp,patch.object(x,'export',side_effect=lambda root,tune,data,complete:calls.append(complete)):
            with self.assertRaisesRegex(ValueError,'THERMAL'):Failed(Path(temp),tune).run()
        self.assertEqual(calls,[False])

class VariantAdmission(unittest.TestCase):
    def fixture(self,temp):
        d=detection(6,8);c=p.candidate(d,4,5);setup=b.read(b.ROOT/'examples/server-setup.example.json')
        base=dict(setup,marker='/SYNTHETIC_MARKER',files={},server_files=[],required_server_files=[],aliases={},resolutions={},model='/SYNTHETIC_MODEL',request={},request_sha256='SYNTHETIC')
        tune=dict(id='SYNTHETIC_NOT_ADMISSION',uid=1000,host=dict(detection=d,kernel='SYNTHETIC',boot_id='SYNTHETIC',btf_sha256='SYNTHETIC',power_binding={}),base=base,build={'full_object':{'sha256':'SYNTHETIC'},'cpu_probe':{'path':'/SYNTHETIC_PROBE'}},build_report='SYNTHETIC',housekeeping=b.housekeeping(d),inputs={})
        campaign=b.variant(tune,c,Path(temp)/'variant')
        return tune,c,campaign
    def test_variable_compute_is_independently_bound_without_modifying_formula(self):
        with tempfile.TemporaryDirectory()as temp:
            tune,c,value=self.fixture(temp);self.assertEqual(value['configuration']['compute_threads'],5);self.assertEqual(value['baseline']['server_argv'][value['baseline']['server_argv'].index('--threads')+1],'5');self.assertEqual(p.default(tune['host']['detection'])['compute_threads'],6)
    def test_variant_seal_and_original_full_domain(self):
        with tempfile.TemporaryDirectory()as temp:
            tune,c,value=self.fixture(temp);loaded=b.contracts.load_campaign(Path(temp)/'variant',current=False);self.assertEqual(loaded,value);self.assertEqual(value['domain']['selected'],c['affinity_cpus']);self.assertEqual(value['build'],tune['build'])
    def test_variant_rechecks_current_host_with_original_contract(self):
        with tempfile.TemporaryDirectory()as temp:
            tune,c,value=self.fixture(temp)
            identity={k:value['host'][k]for k in ['kernel','boot_id','btf_sha256']}
            with patch.object(b.contracts,'host_identity',return_value=identity),patch.object(b.contracts,'build_check'),patch.object(b.contracts,'interpreter',return_value='/SYNTHETIC_LOADER'),patch.object(b.contracts.collect,'inspect',return_value=tune['host']['detection'])as inspect,patch.object(b.contracts,'power',return_value={}),patch.object(b.contracts,'power_binding',return_value={}),patch.object(b.contracts,'scheduler_state',return_value='disabled'):
                self.assertEqual(b.contracts.load_campaign(Path(temp)/'variant',current=True),value)
                inspect.assert_called_once_with('/SYNTHETIC_PROBE',None)
    def test_two_functional_proofs_cannot_admit_new_a(self):
        with tempfile.TemporaryDirectory()as temp:
            tune,c,value=self.fixture(temp)
            with patch.object(b.contracts,'load_campaign',return_value=value),patch.object(b.controller,'completed',return_value={'C-audit':1,'C-clean':1}),patch.object(b.runner,'acquire_session')as attach:
                with self.assertRaisesRegex(ValueError,'Four fresh'):b.admit_newa('/SYNTHETIC',tune,c)
                attach.assert_not_called()
    def test_four_proofs_bind_correct_config(self):
        with tempfile.TemporaryDirectory()as temp:
            tune,c,value=self.fixture(temp)
            with patch.object(b.contracts,'load_campaign',return_value=value),patch.object(b.controller,'completed',return_value=dict.fromkeys(['C-audit','C-clean','E-audit','E-clean'])):
                self.assertEqual(b.admit_newa('/SYNTHETIC',tune,c),value)
    def test_foreign_thread_mask_model_request_or_object_rejected(self):
        for change in ['thread','mask','model','request','object']:
            with self.subTest(change=change),tempfile.TemporaryDirectory()as temp:
                tune,c,value=self.fixture(temp);bad=copy.deepcopy(value)
                if change=='thread':bad['configuration']['compute_threads']=6
                elif change=='mask':bad['domain']['selected']=[0]
                elif change=='model':bad['baseline']['files']['FOREIGN']={}
                elif change=='request':bad['request_sha256']='FOREIGN'
                else:bad['build']['full_object']['sha256']='FOREIGN'
                with patch.object(b.contracts,'load_campaign',return_value=bad),patch.object(b.controller,'completed',return_value=dict.fromkeys(range(4))):
                    with self.assertRaises(ValueError):b.admit_newa('/SYNTHETIC',tune,c)

class BuildMetadata(unittest.TestCase):
    def test_shadowed_source_metadata_normalized_without_editing_upstream(self):
        source=b.HERE.parent/'new_a/llama_new_a.bpf.c'
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);report=root/'upstream.json';b.save(report,{'SYNTHETIC_METADATA':True});original=report.read_bytes()
            value=dict(source={'path':'/SYNTHETIC_HELPER','sha256':'FAKE'},commands=[['clang','-DNEW_A_DIAGNOSTICS','-c',str(source),'-o','/SYNTHETIC_FULL']],full_object={'path':'/SYNTHETIC_FULL'})
            with patch.object(b.contracts,'build_check',return_value=value):out=b.normalize_build(report,root/'normalized.json')
            self.assertEqual(report.read_bytes(),original);self.assertEqual(b.read(out)['source']['sha256'],b.sha(source));self.assertEqual(b.read(out)['metadata_origin']['sha256'],b.sha(report))
    def test_missing_full_compile_identity_rejected(self):
        value=dict(source={'path':'/WRONG','sha256':'WRONG'},commands=[],full_object={'path':'/SYNTHETIC_FULL'})
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'build_check',return_value=value):
            with self.assertRaises(ValueError):b.normalize_build('/unused',Path(temp)/'out.json')
    def test_already_correct_metadata_not_rewritten(self):
        source=b.HERE.parent/'new_a/llama_new_a.bpf.c';value=dict(source=dict(path=str(source),sha256=b.sha(source)))
        with tempfile.TemporaryDirectory()as temp,patch.object(b.contracts,'build_check',return_value=value):
            root=Path(temp);out=root/'out.json';self.assertEqual(b.normalize_build(root/'report.json',out),root/'report.json');self.assertFalse(out.exists())

if __name__=='__main__':unittest.main(verbosity=2)
