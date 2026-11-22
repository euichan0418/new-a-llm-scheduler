# SPDX-License-Identifier: GPL-2.0
"""Full synthetic C/E raw records; no acquisition, socket or BPF operations."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import runtime_core as core
import evidence,controller
import test_universal as fixture_source


def fixture(path,policy='E',audit=True):
    path.mkdir();domain=fixture_source.domain(6,8);uid=os.getuid();pid=4242
    slot=dict(id=policy+('-audit' if audit else '-clean'),policy=policy,audit=audit,stage='FUNCTIONAL',block=0,position=1)
    env=dict(PATH='/usr/bin:/bin',LC_ALL='C',OMP_DISPLAY_AFFINITY='FALSE')
    files={'/SYNTHETIC_FROZEN':dict(sha256='a'*64,inode=2,device=3,size=4)}
    campaign=dict(id='SYNTHETIC_NOT_RUNTIME_ADMISSION',uid=uid,host=dict(kernel='SYNTHETIC'),domain=domain,request_sha256='a'*64,configuration=dict(compute_threads=6,batch_threads=10),baseline=dict(server_env=env,server_argv=['/SYNTHETIC_FROZEN','--threads','6','--threads-batch','10','--threads-http','2'],files=files,server_files=list(files),required_server_files=list(files)),build=dict(full_object=dict(sha256='a'*64)))
    def save(name,value):core.save(path/name,value)
    save('cleanup.json',dict(status='PASS',errors=[],sched_ext='disabled',server_exited=True,loader_exited=True))
    save('kernel-watch-result.json',dict(status='PASS',kernel_opened=True,fd_closed=True,bpf_loaded=False))
    server=dict(pid=pid,starttime=5,uid=uid,policy=7 if policy=='E' else 0,affinity=domain['selected'])
    rows=[dict(monotonic_ns=i,power_binding={'fixed':'SYNTHETIC'},max_temperature_c=40,throttle_unchanged=True,online=domain['online'],allowed=domain['allowed'],stats_sysctl=0,tasks=[server],uid=uid) for i in [1,2]]
    campaign['host']['power_binding']={'fixed':'SYNTHETIC'}
    (path/'monitor.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    save('server-identity.json',server);save('thread-contract.json',dict(argv=campaign['baseline']['server_argv'],environment=dict(env,OMP_DISPLAY_AFFINITY='TRUE' if audit else 'FALSE')))
    text='SYNTHETIC_NOT_EXECUTED\n'
    if audit:
        for n in [6,10]:text+='\n'.join(f'NEWA_TEAM pid={pid} team={n} index={i} native=0x{i+1:x}' for i in range(n))+'\n'
    (path/'server.stderr.log').write_text(text)
    save('mapping-proof.json',dict(required_files_present=True,files=files,raw_maps='1000-2000 r-xp 00000000 00:03 2 /SYNTHETIC_FROZEN'))
    attestation=None
    if policy=='E':
        programs=[dict(symbol=symbol,id=1000+i,btf_id=8,type=2 if symbol=='llama_phase' else 27,tag='0123456789abcdef') for i,symbol in enumerate(sorted(evidence.SYMBOLS))]
        attestation=dict(object_sha256='a'*64,programs=programs)
        (path/'loader.log').write_text('UNIVERSAL_OBJECT_IDENTITY '+json.dumps(attestation)+f'\nllama_scx: registered tgid={pid} uid={uid}\n')
        dump=[dict(name='llama_new_a',flags=8,**{symbol.replace('llama_',''):symbol+'/prog_id:'+str(p['id']) for p in programs if (symbol:=p['symbol'])!='llama_phase'}),dict(bpf_map_info=dict(name='llama_ops',type=26,btf_id=8))]
        save('struct-ops-dump.json',dump);save('scheduler-stats.json',fixture_source.diagnostic(domain))
    off=dict(read_only=True,enable_stats_called=False,sysctl=0,stats_fd_references=0,fds_scanned=2,programs=[])
    if attestation:
        by_name={p['symbol']:p for p in attestation['programs']}
        off['programs']=[dict(id=by_name[s]['id'],btf_id=8,tag='0123456789abcdef',name=s[:15],struct_ops_type=True,run_cnt=0,run_time_ns=0) for s in evidence.CALLBACKS.values()]
    for stage in ['attached','before-measured','after-measured']:
        save('stats-off-'+stage+'-command.json',dict(returncode=0,stdout=json.dumps(off),stderr=''))
        save('program-inventory-'+stage+'-command.json',dict(returncode=0,stdout='[]',stderr=''))
    response=dict(content='SYNTHETIC_NOT_INFERRED',tokens_evaluated=920,tokens_predicted=128,timings=dict(prompt_n=920,predicted_n=128,prompt_ms=1,predicted_ms=1,prompt_per_second=920000,predicted_per_second=128000))
    for role in ['warmup-1','warmup-2','measured']:
        save(role+'-response.json',response)
        save(role+'-client.json',dict(role=role,http_status=200,request_sha256='a'*64,response_sha256=core.sha(path/(role+'-response.json')),request_wall_ms=2,begin_ns=2000000,end_ns=4000000))
    save('process-cpu.json',dict(pid=pid,starttime=5,before_ns=100,after_ns=200,before_mono_ns=1000000,after_mono_ns=5000000))
    for after in [False,True]:
        save('metrics-'+('after' if after else 'before')+'.json',dict(tids={'4242':dict(starttime=5,context_switches=2 if after else 1,migrations=1 if after else 0,runtime_ns=200 if after else 100)},cpus={'cpu'+str(c):dict(total=20 if after else 10,idle=4 if after else 2) for c in domain['selected']}))
    finish(path,campaign,slot)
    return campaign,slot


def finish(path,campaign,slot):
    proof=dict(status='COMPLETE',campaign_id=campaign['id'],stage=slot['stage'],slot=slot,contract_binding=core.digest({k:campaign[k] for k in ['id','host','domain','baseline']}),files={str(p.relative_to(path)):core.sha(p) for p in path.rglob('*') if p.is_file() and p.name!='completion.json'})
    (path/'completion.json').write_text(json.dumps(proof))


class Raw(unittest.TestCase):
    def test_all_four_audit_clean_c_e(self):
        for policy in ['C','E']:
            for audit in [True,False]:
                with self.subTest(policy=policy,audit=audit),tempfile.TemporaryDirectory() as temp:
                    path=Path(temp)/'raw';campaign,slot=fixture(path,policy,audit)
                    self.assertEqual(evidence.verify_session(path,campaign,'FUNCTIONAL',slot)['status'],'FUNCTIONAL_PASS')
    def test_full_mandatory_mutations_rejected_after_refreshing_mock_hashes(self):
        mutations=[('scheduler-stats.json',lambda x:x['cpus'][0].update(mask_violations=1)),('scheduler-stats.json',lambda x:x['cpus'][0].update(tracking_failures=1)),('scheduler-stats.json',lambda x:x['cpus'][0].update(events_dropped=1)),('scheduler-stats.json',lambda x:x.update(mask_cpu_limit=16)),('cleanup.json',lambda x:x.update(status='FAIL')),('kernel-watch-result.json',lambda x:x.update(fd_closed=False)),('thread-contract.json',lambda x:x['environment'].update(OMP_DISPLAY_AFFINITY='FALSE')),('mapping-proof.json',lambda x:x.update(files={})),('server-identity.json',lambda x:x.update(policy=0)),('process-cpu.json',lambda x:x.update(after_ns=100)),('measured-client.json',lambda x:x.update(http_status=500)),('measured-client.json',lambda x:x.update(request_wall_ms=9)),('measured-response.json',lambda x:x.update(tokens_predicted=127)),('struct-ops-dump.json',lambda x:x[0].update(flags=0)),('program-inventory-attached-command.json',lambda x:x.update(stdout=json.dumps([dict(id=99999,type='tracing')]))),('mapping-proof.json',lambda x:x.update(raw_maps='1000-2000 r-xp 00000000 00:03 9999 /SYNTHETIC_FROZEN'))]
        for name,mutate in mutations:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'raw';campaign,slot=fixture(path);self.assertEqual(evidence.verify_session(path,campaign,'FUNCTIONAL',slot)['status'],'FUNCTIONAL_PASS')
                value=core.read(path/name);mutate(value);(path/name).write_text(json.dumps(value));finish(path,campaign,slot)
                with self.assertRaises((ValueError,KeyError)):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_missing_or_extra_file_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'raw';campaign,slot=fixture(path);(path/'unapproved').write_text('NOT_IN_PROOF')
            with self.assertRaises(ValueError):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_stale_host_binding_and_modified_raw_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'raw';campaign,slot=fixture(path);campaign['host']['kernel']='OTHER'
            with self.assertRaises(ValueError):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_program_id_and_registration_mismatch_block(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'raw';campaign,slot=fixture(path);log=(path/'loader.log').read_text().replace('registered tgid=4242','registered tgid=9999');(path/'loader.log').write_text(log);finish(path,campaign,slot)
            with self.assertRaises(ValueError):evidence.verify_session(path,campaign,'FUNCTIONAL',slot)
    def test_failed_attempt_cannot_be_reclassified(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'attempts/FUNCTIONAL/E-audit/attempt';path.parent.mkdir(parents=True);campaign,slot=fixture(path);core.save(path/'failure.json',dict(kind='SYNTHETIC_FAILURE'))
            with self.assertRaises(ValueError):controller.completed(root,campaign,'FUNCTIONAL',True)


if __name__=='__main__':unittest.main(verbosity=2)
