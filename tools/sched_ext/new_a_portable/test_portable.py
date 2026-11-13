# SPDX-License-Identifier: GPL-2.0
"""모의 Intel/AMD topology와 설정 생성 테스트다. 실제 BPF/LLM 실행이 아니다."""
import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import config
import topology as t


def fixture(cores=4, ecores=0, vendor='GenuineIntel', smt=True, hybrid=False, groups=None, allowed=None, offline=(), packages=None):
    if groups is None:
        groups = [[2*i,2*i+1] if smt else [i] for i in range(cores)]
        groups += [[2*cores+i] for i in range(ecores)]
    all_cpus = sorted(c for group in groups for c in group)
    online = sorted(set(all_cpus)-set(offline))
    allowed = online if allowed is None else sorted(allowed)
    cpus = {}
    for index, group in enumerate(groups):
        package = packages[index] if packages else 0
        core = index if packages is None else sum(packages[j]==package for j in range(index))
        for c in group:
            if c in online:
                cpus[str(c)] = dict(package=package,die=0,core=core,siblings_all=sorted(group),core_type_raw='',capacity=None,
                    vendor=vendor,model_name='모의 CPU',family=6 if vendor=='GenuineIntel' else 25,model=100,stepping=1)
    value = dict(possible_cpus=all_cpus,present_cpus=all_cpus,online_cpus=online,process_allowed_cpus=allowed,allowed_cpus=allowed,
                 usable_cpus=sorted(set(online)&set(allowed)),cpus=cpus)
    evidence = dict(schema=1,max_cpus=t.MAX_CPUS,affinity_restored=True,cpus=[])
    for c in value['usable_cpus']:
        row=cpus[str(c)]
        evidence['cpus'].append(dict(cpu=c,vendor=vendor,family=row['family'],model=row['model'],stepping=row['stepping'],
                                   hybrid=hybrid,core_type=(64 if row['core']<cores else 32) if hybrid else 0,
                                   native_model=0,amd_leaf26=False,amd_heterogeneous=False))
    return value,evidence


class TopologyTests(unittest.TestCase):
    def test_intel_p4_e8(self):
        p=t.plan(*fixture(4,8,hybrid=True))
        self.assertEqual((p['compute_threads'],p['batch_threads'],p['http_threads']),(4,4,2))
        self.assertEqual(p['selected_cpus'],[0,2,4,6])
        self.assertEqual(p['excluded_smt_cpus'],[1,3,5,7])
        self.assertEqual(p['unused_e_cpus'],list(range(8,16)))
        self.assertEqual((p['physical_p_cores'],p['physical_e_cores']),(4,8))

    def test_intel_p6_e8(self):
        self.assertEqual(t.plan(*fixture(6,8,hybrid=True))['compute_threads'],6)

    def test_intel_p8_e16(self):
        self.assertEqual(t.plan(*fixture(8,16,hybrid=True))['compute_threads'],8)

    def test_intel_homogeneous_4c8t(self):
        self.assertEqual(t.plan(*fixture(4))['selected_cpus'],[0,2,4,6])

    def test_amd_6c12t(self):
        self.assertEqual(t.plan(*fixture(6,vendor='AuthenticAMD'))['compute_threads'],6)

    def test_amd_8c16t(self):
        self.assertEqual(t.plan(*fixture(8,vendor='AuthenticAMD'))['compute_threads'],8)

    def test_amd_12c24t(self):
        self.assertEqual(t.plan(*fixture(12,vendor='AuthenticAMD'))['compute_threads'],12)

    def test_amd_16c32t(self):
        self.assertEqual(t.plan(*fixture(16,vendor='AuthenticAMD'))['compute_threads'],16)

    def test_smt_off_8c8t(self):
        for vendor in ['GenuineIntel','AuthenticAMD']:
            p=t.plan(*fixture(8,smt=False,vendor=vendor))
            self.assertEqual(p['selected_cpus'],list(range(8)))
            self.assertEqual(p['excluded_smt_cpus'],[])

    def test_intel_hybrid_smt_off(self):
        self.assertEqual(t.plan(*fixture(4,8,smt=False,hybrid=True))['compute_threads'],4)

    def test_online_partial_core_and_entire_core_offline(self):
        p=t.plan(*fixture(4,offline=[0,4,5]))
        self.assertEqual(p['selected_cpus'],[1,2,6])
        self.assertEqual(p['compute_threads'],3)

    def test_allowed_only_smt_siblings(self):
        p=t.plan(*fixture(4,allowed=[1,3,5,7]))
        self.assertEqual(p['selected_cpus'],[1,3,5,7])
        self.assertEqual(p['excluded_smt_cpus'],[])

    def test_affinity_limits_usable_core_count(self):
        p=t.plan(*fixture(8,allowed=[0,1,10,11]))
        self.assertEqual((p['compute_threads'],p['selected_cpus']),(2,[0,10]))

    def test_sparse_ids_and_multiple_packages_repeated_core_ids(self):
        p=t.plan(*fixture(groups=[[3,41],[7,87],[12,99],[24,127]],packages=[0,0,1,1]))
        self.assertEqual(p['selected_cpus'],[3,7,12,24])
        self.assertEqual(p['compute_threads'],4)

    def test_optional_die_missing(self):
        v,e=fixture()
        for row in v['cpus'].values():row['die']=None
        self.assertEqual(t.plan(v,e)['compute_threads'],4)

    def test_cpu_1023_is_not_truncated(self):
        self.assertEqual(t.plan(*fixture(groups=[[1023]]))['selected_cpus'],[1023])

    def test_cpu_1024_rejected_even_if_not_allowed(self):
        v,e=fixture();v['possible_cpus'].append(1024)
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_e_only_does_not_become_zero_or_e_thread_plan(self):
        with self.assertRaises(t.Unsupported):t.plan(*fixture(4,8,hybrid=True,allowed=list(range(8,16))))

    def test_unknown_intel_type_rejected(self):
        v,e=fixture(4,8,hybrid=True);e['cpus'][0]['core_type']=99
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_amd_heterogeneous_or_unknown_core_type_rejected(self):
        for key,value in [('amd_heterogeneous',True),('core_type',1),('core_type',99)]:
            v,e=fixture(vendor='AuthenticAMD');e['cpus'][0][key]=value
            with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_amd_extended_leaf_uniform_supported(self):
        v,e=fixture(vendor='AuthenticAMD')
        for x in e['cpus']:x['amd_leaf26']=True
        self.assertEqual(t.plan(v,e)['compute_threads'],4)

    def test_amd_native_model_mismatch_rejected(self):
        v,e=fixture(vendor='AuthenticAMD');e['cpus'][0]['native_model']=2
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_capacity_mismatch_rejected(self):
        v,e=fixture(vendor='AuthenticAMD');v['cpus']['0']['capacity']='1024'
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_sysfs_named_type_conflict_rejected(self):
        v,e=fixture(4,8,hybrid=True);v['cpus']['0']['core_type_raw']='efficiency'
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_unknown_homogeneous_sysfs_type_rejected(self):
        v,e=fixture();v['cpus']['0']['core_type_raw']='99'
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_missing_core_id_and_nonreciprocal_siblings_rejected(self):
        for mutation in [lambda v:v['cpus']['0'].pop('core'),lambda v:v['cpus']['0'].update(siblings_all=[0])]:
            v,e=fixture();mutation(v)
            with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_cpu_identity_mismatch_rejected(self):
        v,e=fixture();e['cpus'][0]['model']=99
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_duplicate_cpuid_or_missing_cpuid_rejected(self):
        for remove in [True,False]:
            v,e=fixture()
            if remove:e['cpus'].pop()
            else:e['cpus'].append(e['cpus'][0])
            with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_boolean_cpu_and_schema_rejected(self):
        for mutation in [lambda e:e.update(schema=True),lambda e:e['cpus'][1].update(cpu=True)]:
            v,e=fixture();mutation(e)
            with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_overlapping_offline_sibling_rejected(self):
        v,e=fixture(groups=[[0,9],[2,10]],offline=[9,10]);v['cpus']['2']['siblings_all']=[2,9]
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_no_usable_cpu_rejected(self):
        v,e=fixture();v['usable_cpus']=[]
        with self.assertRaises(t.Unsupported):t.plan(v,e)

    def test_hotplug_or_affinity_drift_blocks_without_retry(self):
        v,e=fixture();changed=copy.deepcopy(v);changed['online_cpus'].pop()
        reader=unittest.mock.Mock(side_effect=[v,changed]);prober=unittest.mock.Mock(return_value=e)
        with self.assertRaises(t.Unsupported):t.discover('unused',reader=reader,prober=prober)
        self.assertEqual(prober.call_count,1)

    def test_old_e_tier_abi_and_binary(self):
        v,e=fixture(4,8,hybrid=True);p=t.plan(v,e)
        old=t.original.classify(dict(usable_cpus=v['usable_cpus'],online_cpus=v['online_cpus'],allowed_cpus=v['allowed_cpus'],cpus=v['cpus'],heterogeneous=dict(multiple_core_types=False,frequency_hint=False)),{x['cpu']:x['core_type']for x in e['cpus']})
        self.assertEqual(p['policy'],old)
        blob=t.original.encode(p['policy'])
        self.assertEqual(len(blob),20+3*1024*4)
        self.assertEqual(struct.unpack_from('<5I',blob),(t.original.MAGIC,1,4,8,4))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.selection=t.plan(*fixture(4,8,hybrid=True))
        self.argv=['/Frozen/llama-server','-m','/model.gguf','--threads','8','--threads-batch','8','--threads-http','2','--ctx-size','2048','--n-gpu-layers','0']

    def test_only_thread_arguments_changed(self):
        argv,env=config.server_config(self.selection,self.argv,{'LC_ALL':'C'})
        expected=self.argv.copy();expected[4]=expected[6]='4'
        self.assertEqual(argv,expected)
        self.assertEqual(env,dict(LC_ALL='C',OMP_NUM_THREADS='4',OMP_DYNAMIC='FALSE'))
        self.assertEqual(self.argv[4],'8')

    def test_amd_server_12_threads(self):
        selection=t.plan(*fixture(12,vendor='AuthenticAMD'))
        argv,_=config.server_config(selection,['/server'],{})
        self.assertEqual(argv,['/server','--threads','12','--threads-batch','12','--threads-http','2'])

    def test_server_thread_limit_does_not_silently_cap_core_count(self):
        selection=t.plan(*fixture(513,smt=False))
        self.assertEqual(selection['compute_threads'],513)
        with self.assertRaises(t.Unsupported):config.server_config(selection,['/server'],{})

    def test_aliases_and_equals(self):
        argv,_=config.server_config(self.selection,['/server','-t=8','-tb','8','--threads-http=7'],{})
        self.assertEqual(argv,['/server','--threads','4','--threads-batch','4','--threads-http','2'])

    def test_positional_terminator_is_not_silently_accepted(self):
        with self.assertRaises(t.Unsupported):config.server_config(self.selection,['/server','--'],{})

    def test_duplicate_or_missing_thread_value_rejected(self):
        for args in [['/s','-t','8','--threads','4'],['/s','--threads'],['/s','--threads='],['/s','-t4']]:
            with self.assertRaises(t.Unsupported):config.server_config(self.selection,args,{})

    def test_affinity_and_numa_arguments_rejected(self):
        for option in ['--cpu-mask','-C','--cpu-range-batch','--numa']:
            with self.assertRaises(t.Unsupported):config.server_config(self.selection,['/s',option,'0'],{})

    def test_conflicting_openmp_environment_rejected(self):
        with self.assertRaises(t.Unsupported):config.server_config(self.selection,['/s'],{'GOMP_CPU_AFFINITY':'0-7'})

    def test_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory()as temp:
            with self.assertRaises(t.Unsupported):config.write_output(Path(temp),{}, {})

    def test_saved_plan_revalidation_and_boot_drift(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);helper=root/'probe';helper.write_bytes(b'OFFLINE_MOCK')
            host=dict(kernel='mock',boot_id='mock',btf_sha256='mock',architecture='x86_64')
            with patch.object(config,'host_identity',return_value=host):
                current=config.make(self.selection,self.argv,{},helper)
                dest=root/'plan';config.write_output(dest,current,dict(server_argv=self.argv,server_env={},requested_cpus=None))
                with patch.dict(os.environ,{},clear=True):
                    self.assertFalse(config.check_current(dest,helper,discovery=lambda *a,**k:self.selection)['execution_admitted'])
                with patch.object(config,'host_identity',return_value=dict(host,boot_id='other')):
                    with self.assertRaises(t.Unsupported):config.check_current(dest,helper,discovery=lambda *a,**k:self.selection)

    def test_selected_cpu_tampering_rejected(self):
        selection=copy.deepcopy(self.selection);selection['selected_cpus'].append(8)
        with self.assertRaises(t.Unsupported):config.make(selection,self.argv,{},'/unused')

    def test_saved_topology_tampering_blocks_before_probe(self):
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);helper=root/'probe';helper.write_bytes(b'OFFLINE_MOCK')
            current=config.make(self.selection,self.argv,{},helper,host={'fixture':True})
            dest=root/'plan';config.write_output(dest,current,dict(server_argv=self.argv,server_env={},requested_cpus=None))
            (dest/'topology.bin').write_bytes(b'CORRUPT')
            discovery=unittest.mock.Mock()
            with self.assertRaises(t.Unsupported):config.check_current(dest,helper,discovery=discovery)
            self.assertEqual(discovery.call_count,0)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.value,self.evidence=fixture(2)
        self.cpu=self.root/'cpu';self.cpu.mkdir();self.proc=self.root/'cpuinfo'
        for name in ['possible','present','online']:
            (self.cpu/name).write_text(','.join(map(str,self.value[name+'_cpus'])))
        lines=[]
        for c in self.value['online_cpus']:
            row=self.value['cpus'][str(c)];p=self.cpu/('cpu'+str(c))/'topology';p.mkdir(parents=True)
            for name,key in [('physical_package_id','package'),('die_id','die'),('core_id','core')]:
                (p/name).write_text(str(row[key]))
            for name in ['thread_siblings_list','core_cpus_list']:(p/name).write_text(','.join(map(str,row['siblings_all'])))
            lines.append(f"processor : {c}\nvendor_id : {row['vendor']}\nmodel name : {row['model_name']}\ncpu family : {row['family']}\nmodel : {row['model']}\nstepping : {row['stepping']}")
        self.proc.write_text('\n\n'.join(lines))

    def tearDown(self):self.temp.cleanup()

    def snapshot(self,**kwargs):
        return t.snapshot(cpu_root=self.cpu,proc=self.proc,affinity=kwargs.pop('affinity',lambda _:set(self.value['online_cpus'])),**kwargs)

    def test_required_identity_is_not_guessed(self):
        (self.cpu/'cpu0/topology/core_id').unlink()
        with self.assertRaises(t.Unsupported):self.snapshot()

    def test_sibling_information_is_not_synthesized(self):
        for name in ['thread_siblings_list','core_cpus_list']:(self.cpu/'cpu0/topology'/name).unlink()
        with self.assertRaises(t.Unsupported):self.snapshot()

    def test_requested_mask_cannot_expand_actual_allowed(self):
        with self.assertRaises(t.Unsupported):self.snapshot(requested=[0,8])

    def test_optional_die_not_available(self):
        for c in self.value['online_cpus']:(self.cpu/('cpu'+str(c))/'topology/die_id').unlink()
        s=self.snapshot();self.assertTrue(all(x['die'] is None for x in s['cpus'].values()))
        self.assertEqual(t.plan(s,self.evidence)['compute_threads'],2)

    def test_change_during_snapshot_blocks(self):
        affinity=unittest.mock.Mock(side_effect=[{0,1,2,3},{0,1}])
        with self.assertRaises(t.Unsupported):self.snapshot(affinity=affinity)


class ProbeBranchTests(unittest.TestCase):
    def test_actual_c_probe_vendor_branch_with_mock_cpuid(self):
        source=(Path(__file__).parent/'cpu_probe.c').read_text()
        body=source[source.index('struct cpu_record'):source.index('\nint main')]
        prelude=r'''
#include <stdbool.h>
#include <string.h>
#include <assert.h>
static int intel, extended, hetero, calls_intel;
static unsigned int mock_max(unsigned int leaf, void *p){(void)p;return leaf ? (extended ? 0x80000026u : 0x80000008u) : 0x1au;}
static void mock_cpuid(unsigned int leaf,unsigned int sub,unsigned int *a,unsigned int *b,unsigned int *c,unsigned int *d){
 *a=*b=*c=*d=0;
 if(leaf==0){const char *v=intel?"GenuineIntel":"AuthenticAMD";memcpy(b,v,4);memcpy(d,v+4,4);memcpy(c,v+8,4);}
 if(leaf==1)*a=intel?0x600:0xa00f00;
 if(leaf==7){calls_intel++;*d=1u<<15;}
 if(leaf==0x1a){calls_intel++;*a=64u<<24;}
 if(leaf==0x80000026 && sub==0){*c=1u<<8;*a=hetero?1u<<30:0;}
}
#define __get_cpuid_max mock_max
#define __cpuid_count(l,s,a,b,c,d) mock_cpuid(l,s,&a,&b,&c,&d)
'''
        post=r'''
int main(void){
 struct cpu_record r={};
 assert(inspect_cpu(&r)==0);assert(calls_intel==0);assert(!r.amd_leaf26);
 extended=1;r=(struct cpu_record){};assert(inspect_cpu(&r)==0);assert(r.amd_leaf26);assert(calls_intel==0);
 hetero=1;r=(struct cpu_record){};assert(inspect_cpu(&r)==0);assert(r.amd_heterogeneous);assert(calls_intel==0);
 intel=1;r=(struct cpu_record){};assert(inspect_cpu(&r)==0);assert(r.hybrid && r.core_type==64);assert(calls_intel==2);
 return 0;
}
'''
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);(root/'probe-test.c').write_text(prelude+body+post)
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(root/'probe-test.c'),'-o',str(root/'probe-test')],check=True,capture_output=True)
            subprocess.run([str(root/'probe-test')],check=True,capture_output=True)


if __name__=='__main__':
    unittest.main()
