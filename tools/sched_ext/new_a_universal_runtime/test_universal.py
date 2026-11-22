# SPDX-License-Identifier: GPL-2.0
"""Synthetic CPU/evidence and native parser tests. No BPF load or inference."""
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parent))
import runtime_core as core
import contracts,evidence,controller,runner,derive
from runtime_core import HERE,ROOT,collect,profiles
fixture_module=core.module('universal_synthetic_hybrid_fixtures',HERE.parent/'new_a_hybrid/test_hybrid.py')


def topology(*args,**kwargs):return collect.detection(*fixture_module.fixture(*args,**kwargs))


def selection(*args,**kwargs):
    value=topology(*args,**kwargs);return value,profiles.recommend(value)


def domain(*args,**kwargs):
    value,config=selection(*args,**kwargs);return core.domain(value,config,allow_shared=True)


def diagnostic(d):
    rows=[]
    for c in d['possible']:
        active=c in d['selected']
        rows.append(dict(cpu=c,running=2 if active else 0,stopping=2 if active else 0,runtime_ns=100 if active else 0,migrations=1 if active else 0,
            selected=[2 if active else 0,0,0,0],mask_violations=0,tracking_failures=0,events_dropped=0,events_attempted=0,phase_seen=6 if active else 0))
    return dict(instrumentation_mode='FULL',optional_metrics=dict(runtime_ns='COLLECTED',migrations='COLLECTED'),cpus=rows,event_schema=2,mask_cpu_limit=d['mask_bits'],events_export_error=0,events=[],live_monitor=dict(status='PASS',kernel_opened=True,failures=0,samples=2))


class CPUShapes(unittest.TestCase):
    def check(self,p,e,vendor='GenuineIntel',hybrid=True,**kwargs):
        detected,config=selection(p,e,vendor=vendor,hybrid=hybrid,**kwargs);d=core.domain(detected,config,True)
        self.assertEqual(d['mask_bits'],max(d['possible'])+1);self.assertEqual(d['per_cpu_slots'],len(d['possible']))
        self.assertEqual(len(core.guard_bytes(d,pid=123,uid=1000,start=5)),4128)
        self.assertTrue(evidence.diagnostics(diagnostic(d),d)['task_observed_runtime_ns']>0)
        self.assertLessEqual(set(d['selected']),set(d['online'])&set(d['allowed']))
        return config,d
    def test_intel_hybrid_shapes(self):
        for p,e in [(2,8),(4,8),(6,8),(8,8),(8,16)]:
            with self.subTest(p=p,e=e):
                c,d=self.check(p,e);self.assertEqual((c['compute_threads'],c['batch_threads']),(p,p+e//2))
    def test_i5_legacy_mask_reproduced(self):self.assertEqual(self.check(4,8)[1]['selected'],[0,2,4,6,8,9,10,11])
    def test_reported_i7_example_synthetic_only(self):self.assertEqual(self.check(6,8)[1]['selected'],[0,2,4,6,8,10,12,13,14,15])
    def test_intel_homogeneous_shapes(self):
        for p in [2,4,6,8,12,16,32,40]:
            with self.subTest(p=p):self.assertEqual(self.check(p,0,hybrid=False)[0]['batch_threads'],p)
    def test_amd_homogeneous_shapes(self):
        for p in [4,6,8,12,16]:
            with self.subTest(p=p):self.assertEqual(self.check(p,0,vendor='AuthenticAMD',hybrid=False)[0]['compute_threads'],p)
    def test_smt_disabled_shared_housekeeping_explicit(self):
        d,c=selection(8,0,hybrid=False,smt=False)
        with self.assertRaisesRegex(ValueError,'ack-shared'):core.domain(d,c)
        result=core.domain(d,c,True);self.assertTrue(result['housekeeping_shared']);self.assertEqual(len(result['selected']),8)
    def test_offline_and_cpuset_sibling_restriction(self):
        self.check(4,8,offline=[0,1,15]);self.check(4,8,allowed=[1,3,5,7,8,9,10,11])
    def test_noncontiguous_possible_domain_above_63(self):
        c,d=self.check(3,0,hybrid=False,groups=[[0,1],[64,65],[128,129]])
        self.assertEqual(d['possible'],[0,1,64,65,128,129]);self.assertEqual(d['selected'],[0,64,128]);self.assertEqual(d['mask_bits'],130)
    def test_multiple_package_and_numa(self):self.check(4,8,packages=[0,0,1,1,0,0,0,0,1,1,1,1])
    def test_amd_multiple_ccd_metadata_not_reclassified(self):
        v,e,x=fixture_module.fixture(8,0,vendor='AuthenticAMD',hybrid=False)
        for cpu,info in v['cpus'].items():info['die']=int(cpu)//8
        for cpu,info in x['cpus'].items():info['caches'][1]['shared_cpus']=list(range(0,8)) if int(cpu)<8 else list(range(8,16));info['numa_node']=int(cpu)//8
        detected=collect.detection(v,e,x);config=profiles.recommend(detected);self.assertEqual(config['batch_threads'],8);self.assertEqual({c['die'] for c in config['cpu_mapping']},{0,1})
    def test_unknown_amd_or_intel_type_blocks_before_runtime(self):
        for amd in [False,True]:
            v,e,x=fixture_module.fixture(8,0,vendor='AuthenticAMD' if amd else 'GenuineIntel',hybrid=not amd)
            e['cpus'][0]['core_type']=1
            with self.assertRaises(ValueError):collect.detection(v,e,x)
    def test_cpu_id_1024_rejected(self):
        with self.assertRaises(ValueError):self.check(1,0,hybrid=False,groups=[[1024]])


class DynamicEvidence(unittest.TestCase):
    def setUp(self):self.d=domain(6,8)
    def test_mask_tracking_drop_runtime_missing_all_rejected(self):
        for key,value in [('mask_violations',1),('tracking_failures',1),('events_dropped',1),('runtime_ns','NOT_AVAILABLE'),('runtime_ns',False),('migrations',None)]:
            obj=diagnostic(self.d);obj['cpus'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_outside_selected_execution_rejected_even_legal_cpu(self):
        obj=diagnostic(self.d);obj['cpus'][1].update(running=1,stopping=1,runtime_ns=1)
        with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_old_16_cpu_export_not_accepted_on_20_cpu(self):
        obj=diagnostic(self.d);obj['cpus']=obj['cpus'][:16]
        with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_duplicate_missing_or_wrong_cpu_id(self):
        for missing in [False,True]:
            obj=diagnostic(self.d)
            if missing:obj['cpus'].pop()
            else:obj['cpus'][-1]['cpu']=0
            with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_mask_bits_use_max_id_not_count(self):
        d=domain(3,0,hybrid=False,groups=[[0,1],[64,65],[128,129]]);obj=diagnostic(d);obj['mask_cpu_limit']=6
        with self.assertRaises(ValueError):evidence.diagnostics(obj,d)
    def test_error_events_and_missing_timestamp_fail(self):
        for kind in [1,2,3]:
            obj=diagnostic(self.d);obj['cpus'][0]['events_attempted']=1;obj['events']=[dict(kind=kind,timestamp_ns=0)]
            with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_full_runtime_and_phase_cannot_be_omitted(self):
        for key in ['runtime_ns','phase_seen','running','selected']:
            obj=diagnostic(self.d)
            for row in obj['cpus']:row[key]=[0,0,0,0] if key=='selected' else 0
            with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_live_guard_required(self):
        for key,value in [('samples',0),('kernel_opened',False),('failures',1),('status','FAIL')]:
            obj=diagnostic(self.d);obj['live_monitor'][key]=value
            with self.assertRaises(ValueError):evidence.diagnostics(obj,self.d)
    def test_whole_process_cpu_separate_from_resource_threads(self):
        a=dict(tids={'1':dict(starttime=3,migrations=1,context_switches=2,runtime_ns=3)},cpus={'cpu0':dict(total=10,idle=2)})
        b=dict(tids={'1':dict(starttime=3,migrations=2,context_switches=5,runtime_ns=10)},cpus={'cpu0':dict(total=20,idle=4)})
        out=evidence.resource_metrics(a,b,[0]);self.assertEqual(out['runtime_ns'],7);self.assertEqual(out['per_cpu_busy_pct']['0'],80)
        b['tids']['1']['starttime']=4
        with self.assertRaises(ValueError):evidence.resource_metrics(a,b,[0])


class NativeDomain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.binary=Path(cls.temp.name)/'parse';path=Path(cls.temp.name)/'test.c'
        path.write_text(r'''
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "guard_domain.h"
#include "mask_dump.h"
int main(int argc,char **argv){unsigned char mask[NEW_A_MAX_CPUS];if(argc!=3)return 2;
 if(!strcmp(argv[1],"load")){printf("%d\n",ug_load(argv[2]));return 0;}
 int r=ug_parse(argv[2],mask);unsigned int count=0;for(int i=0;i<NEW_A_MAX_CPUS;i++)count+=mask[i];
 __u64 bits[NEW_A_MASK_WORDS]={};bits[1]=1;bits[2]=1ULL<<1;
 printf("%d %u %u\n",r,count,new_a_mask_weight(bits,130));return 0;}
''')
        subprocess.run(['cc','-O2','-DNEW_A_DIAGNOSTICS','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-misleading-indentation','-I'+str(HERE),'-I'+str(HERE.parent/'new_a'),str(path),'-o',str(cls.binary)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_bitmap_above_63_and_sparse_possible(self):self.assertEqual(subprocess.check_output([str(self.binary),'parse','0-1,64-65,128-129'],text=True).strip(),'0 6 2')
    def test_native_out_of_range_and_duplicates(self):
        for text in ['1024','1,1','4-2','','0-1024','-1','1,']:
            with self.subTest(text=text):self.assertTrue(subprocess.check_output([str(self.binary),'parse',text],text=True).startswith('-1 '))
    def test_real_readonly_local_controller_guard_load(self):
        possible=collect.portable.cpulist(Path('/sys/devices/system/cpu/possible').read_text());online=collect.portable.cpulist(Path('/sys/devices/system/cpu/online').read_text());allowed=sorted(os.sched_getaffinity(0))
        d=dict(possible=possible,online=online,allowed=allowed,selected=[allowed[0]],mask_bits=max(possible)+1,per_cpu_slots=len(possible))
        path=Path(self.temp.name)/'guard';path.write_bytes(core.guard_bytes(d));path.chmod(0o400)
        # Guard owner is this Python process; no policy or affinity changes.
        self.assertEqual(subprocess.check_output([str(self.binary),'load',str(path)],text=True).strip(),'0')
        raw=bytearray(path.read_bytes());raw[0]=0;path.chmod(0o600);path.write_bytes(raw)
        self.assertEqual(subprocess.check_output([str(self.binary),'load',str(path)],text=True).strip(),'-1')


class AdmissionAndFailure(unittest.TestCase):
    def test_fixed_functional_and_performance_order(self):
        self.assertEqual([(s['policy'],s['audit']) for s in runner.slots('FUNCTIONAL')],[('C',True),('C',False),('E',True),('E',False)])
        self.assertEqual([s['policy'] for s in runner.slots('PERFORMANCE')],['C','E','E','C'])
    def test_no_functional_evidence_blocks_before_auth(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(contracts,'load_campaign',return_value={'id':'SYNTHETIC'}),patch.object(subprocess,'run') as call:
            with self.assertRaisesRegex(ValueError,'four fresh'):controller.gate(temp,'PERFORMANCE')
            call.assert_not_called()
    def test_non_auth_failure_never_resume(self):self.assertFalse(controller.safe_auth_resume(Path('/NONEXISTENT'),{},dict(kind='MASK_FAILURE'),dict(status='PASS',errors=[])))
    def test_cleanup_failure_never_resume(self):self.assertFalse(controller.safe_auth_resume(Path('/NONEXISTENT'),{},dict(kind='SUDO_AUTH_REQUIRED_OR_EXPIRED'),dict(status='FAIL',errors=['x'])))
    def test_sudo_cached_check_no_refresh_and_exact_stderr(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='sudo: a password is required')) as call:
            with self.assertRaises(runner.AuthRequired):runner.root_command(['true'],Path(temp)/'auth.json')
            self.assertEqual(call.call_args[0][0],['/usr/bin/sudo','-N','-n','true'])
            self.assertTrue((Path(temp)/'auth.json').exists())
    def test_non_auth_command_failure_not_expiration(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(subprocess,'run',return_value=Mock(returncode=1,stdout='',stderr='taskset: invalid argument')):
            with self.assertRaises(ValueError) as raised:runner.root_command(['true'],Path(temp)/'command.json')
            self.assertNotIsInstance(raised.exception,runner.AuthRequired)
    def test_uncertain_cleanup_cannot_be_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);core.save(p/'completion.json',dict(status='COMPLETE',campaign_id='x',stage='FUNCTIONAL',slot={},files={},contract_binding=core.digest(dict(id='x',host={},domain={},baseline={}))))
            core.save(p/'cleanup.json',dict(status='FAIL',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True))
            with self.assertRaises(ValueError):evidence.verify_session(p,dict(id='x',host={},domain={},baseline={}),'FUNCTIONAL',{})
    def test_bad_power_does_not_create_profile(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(contracts,'power',side_effect=ValueError('TURBO_ON')):
            root=Path(temp)/'campaign'
            with self.assertRaisesRegex(ValueError,'TURBO_ON'):contracts.prepare(root,'unused','unused','unused','unused')
            self.assertFalse(root.exists())
    def test_changed_baseline_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'file';p.write_text('original');row=contracts.record(p);p.write_text('changed');self.assertNotEqual(row,contracts.record(p))
    def test_prepare_argv_p6e8_without_fixed4(self):
        d,c=selection(6,8);setup=core.read(ROOT/'examples/server-setup.example.json')
        argv,env=contracts.argv_contract(setup,c);self.assertEqual(argv[argv.index('--threads')+1],'6');self.assertEqual(argv[argv.index('--threads-batch')+1],'10');self.assertEqual(env['OMP_NUM_THREADS'],'6')
    def test_unsafe_env_and_affinity_argv_rejected(self):
        d,c=selection();setup=core.read(ROOT/'examples/server-setup.example.json');setup['server_env']['GOMP_CPU_AFFINITY']='0'
        with self.assertRaises(ValueError):contracts.argv_contract(setup,c)


class SourceInvariants(unittest.TestCase):
    def test_policy_manifest_unchanged(self):
        value=core.read(HERE.parent/'new_a_final/policy-manifest.json')
        for name,expected in value['files'].items():self.assertEqual(core.sha(ROOT/name),expected)
    def test_generated_guard_has_no_fixed_16_or_i5_mask(self):
        with tempfile.TemporaryDirectory() as temp:
            derive.generate(Path(temp));text=(Path(temp)/'monitor.h').read_text();loader=(Path(temp)/'loader.c').read_text()
            self.assertNotIn('analysis_count != 16',text);self.assertNotIn('cpu <= 14',text)
            self.assertIn('analysis_possible_ids[cpu]',text);self.assertIn('ug.selected[cpu]',text);self.assertIn('ug_current()',text)
            for token in ['s->mask_violations','s->tracking_failures','s->events_dropped','execution outside prepared CPU set','analysis_kernel_check()']:self.assertIn(token,text)
            self.assertIn('--guard-config',loader);self.assertIn('UNIVERSAL',loader+ (Path(temp)/'identity.h').read_text())
    def test_no_runtime_lite_or_pid_lite_policy_mixed(self):
        text=(HERE/'build.py').read_text();self.assertIn("ROOT/'new_a/llama_new_a.bpf.c'",text);self.assertNotIn('new_a_runtime_lite',text);self.assertNotIn('new_a_pid',text)



class NativeObjectHash(unittest.TestCase):
    def test_verified_bytes_not_path_reopened_and_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);obj=root/'object';obj.write_bytes(b'SYNTHETIC_OBJECT_NOT_BPF')
            digest=hashlib.sha256(obj.read_bytes()).hexdigest();source=root/'identity.c';binary=root/'identity'
            source.write_text(r"""
#define _GNU_SOURCE
#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <stddef.h>
static int opens;
static struct bpf_object *fake_open(const void *p,size_t n,const struct bpf_object_open_opts *opts){(void)p;(void)n;(void)opts;opens++;return (struct bpf_object *)1;}
#define bpf_object__open_mem fake_open
#include "identity.h"
int main(int argc,char **argv){if(argc!=2)return 2;struct bpf_object *obj=universal_open(argv[1]);free(universal_object_bytes);universal_object_bytes=NULL;printf("%d %d\n",obj!=NULL,opens);return 0;}
""")
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-misleading-indentation','-I'+str(HERE),'-DUNIVERSAL_OBJECT_SHA256="'+digest+'"',str(source),'-o',str(binary)],check=True)
            self.assertEqual(subprocess.check_output([str(binary),str(obj)],text=True).strip(),'1 1')
            obj.write_bytes(b'TAMPERED')
            result=subprocess.run([str(binary),str(obj)],capture_output=True,text=True)
            self.assertEqual(result.stdout.strip(),'0 0');self.assertIn('INTEGRITY_FAILED',result.stderr)


class HostBaselineRoundTrip(unittest.TestCase):
    def test_temporary_synthetic_campaign_no_admission_reuse_on_boot_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);detection,config=selection();core.save(root/'config.json',config)
            setup=core.read(ROOT/'examples/server-setup.example.json')
            paths={name:root/name for name in ['server','model','marker','ggml','loader','child','probe','stats-off','kernel-watch','interpreter','object']}
            for name,path in paths.items():path.write_text('SYNTHETIC_'+name)
            setup['server_argv'][0]=str(paths['server']);setup['server_argv'][setup['server_argv'].index('-m')+1]=str(paths['model'])
            setup.update(marker_elf=str(paths['marker']),elf_roots=[str(paths['ggml'])],expected_server_sha256=core.sha(paths['server']),expected_model_sha256=core.sha(paths['model']))
            core.save(root/'setup.json',setup);core.save(root/'request.json',dict(prompt='SYNTHETIC_NOT_INFERRED',n_predict=128,cache_prompt=False,stream=False))
            static=dict(cpu_probe=dict(path=str(paths['probe']),sha256=core.sha(paths['probe'])),full_object=dict(path=str(paths['object']),sha256=core.sha(paths['object'])),source=dict(path=str(paths['object']),sha256=core.sha(paths['object'])),loader=dict(path=str(paths['loader']),sha256=core.sha(paths['loader'])),child=dict(path=str(paths['child']),sha256=core.sha(paths['child'])),helpers={name:dict(path=str(paths[name]),sha256=core.sha(paths[name])) for name in ['stats-off','kernel-watch']})
            core.save(root/'static.json',static)
            host=dict(kernel='SYNTHETIC_KERNEL',boot_id='SYNTHETIC_BOOT_NOT_A_REAL_ID',btf_sha256='a'*64)
            power=dict(ac=True,no_turbo='1',boost=None,governors={'policy0':'performance'},power_profile='performance',temperatures_c=[40],throttle_counts={})
            with patch.object(contracts,'power',return_value=power),patch.object(contracts,'host_identity',return_value=host),patch.object(contracts,'build_check',return_value=static),patch.object(contracts.collect,'inspect',return_value=detection),patch.object(contracts,'interpreter',return_value=str(paths['interpreter'])),patch.object(contracts,'resolve',side_effect=lambda p,loader,env:sorted({str(p),loader})),patch.object(contracts.subprocess,'check_output',return_value='0000 T llama_scx_phase_v1\n'),patch.object(contracts,'scheduler_state',return_value='disabled'):
                campaign=contracts.prepare(root/'campaign',root/'static.json',root/'config.json',root/'setup.json',root/'request.json')
                self.assertEqual(campaign['functional'],'RUNTIME_FUNCTIONAL_NOT_RUN');self.assertTrue((root/'campaign/host-profile.private.json').exists())
                self.assertEqual(contracts.load_campaign(root/'campaign',current=True)['id'],campaign['id'])
                host['boot_id']='CHANGED_SYNTHETIC_BOOT'
                with self.assertRaisesRegex(ValueError,'Kernel/Boot/BTF'):contracts.load_campaign(root/'campaign',current=True)
                self.assertEqual(contracts.load_campaign(root/'campaign',current=False)['id'],campaign['id'])
                paths['ggml'].write_text('CHANGED_LIBRARY')
                with self.assertRaisesRegex(ValueError,'dependency changed'):contracts.load_campaign(root/'campaign',current=False)


class RequestTiming(unittest.TestCase):
    def test_http_clock_excludes_monitor_poll_delay(self):
        import time
        data=dict(content='SYNTHETIC_NOT_INFERRED',tokens_evaluated=920,tokens_predicted=128,timings=dict(prompt_n=920,predicted_n=128,prompt_ms=1,predicted_ms=1,prompt_per_second=920000,predicted_per_second=128000))
        class Response:
            status=200
            def read(self,limit):return json.dumps(data).encode()
        class Connection:
            def __init__(self,*a,**kw):pass
            def request(self,*a,**kw):pass
            def getresponse(self):time.sleep(.01);return Response()
            def close(self):pass
        request=dict(prompt='SYNTHETIC',n_predict=128,cache_prompt=False,stream=False)
        campaign=dict(request=request,request_sha256=core.digest(request),baseline=dict(server_argv=['server','--port','8096']))
        with tempfile.TemporaryDirectory() as temp,patch.object(runner.http.client,'HTTPConnection',Connection):
            path=Path(temp);runner.request(campaign,path,'measured',lambda:time.sleep(.1));client=core.read(path/'measured-client.json')
            self.assertLess(client['request_wall_ms'],80)

if __name__=='__main__':unittest.main(verbosity=2)
