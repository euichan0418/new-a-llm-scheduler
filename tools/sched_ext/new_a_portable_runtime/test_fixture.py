# SPDX-License-Identifier: GPL-2.0
"""공개 테스트용 완전 합성 자료. 실제 커널/서버 결과를 복사하지 않는다."""
import hashlib
import json
import os
from pathlib import Path
import stats_off
import study
import validation
import workers
from fg_config import LABELS,REQUEST_SHA


def fixture(root,visit):
    root=Path(root)/visit['relative_dir'];root.mkdir(parents=True)
    def write(name,value):(root/name).write_text(json.dumps(value,allow_nan=False))
    role='functional'if visit['kind']=='FUNCTIONAL'else'measured'
    thermal=dict(status='NO_INCREMENT_OBSERVED')
    guard=dict(status='COMPLETED',errors=[],samples=2)
    identity=dict(pid=4242,uid=os.getuid(),policy=7,affinity=[0,2,4,6],starttime=100)
    write('processes.json',dict(server=identity))
    for name in ['policies-before.json','policies-pre-measured.json','policies-after.json']:
        write(name,[dict(identity,tid=4242+i)for i in range(6)])
    write('cleanup.json',dict(status='PASS',errors=[],sched_ext='disabled'))
    write('guard-result.json',guard)
    monitor=[dict(sample=i,settings=dict(no_turbo='1',no_turbo_at_start='1',power_profile='performance'),
                  throttle_delta=thermal,capabilities=dict(power_supply=dict(entries=dict(AC=dict(type='Mains',online='1')))))for i in range(2)]
    (root/'power-thermal-monitor.jsonl').write_text('\n'.join(json.dumps(x)for x in monitor)+'\n')
    write('kernel-watch-result.json',dict(status='PASS',errors=[],record=dict(status='PASS',kernel_opened=True,fd_closed=True,bpf_loaded=False)))
    write('kernel-load-attach.json',dict(verifier='LOAD_SUCCEEDED',attach='OBSERVED',ops='llama_new_a',flags=8,live_monitor='READY'))
    ops=dict(name='llama_new_a',flags=8,**{name:symbol+'/prog_id:'+str(i+1)for i,(name,symbol)in enumerate(stats_off.CALLBACKS.items())})
    dump=[dict(bpf_map_info=dict(name='llama_ops',type=26,btf_id=42),value=ops)]
    write('struct-ops-dump.json',dump)
    cpus=[]
    for cpu in range(16):
        active=cpu in [0,2,4,6]
        cpus.append(dict(cpu=cpu,running=int(active),stopping=int(active),mask_violations=0,tracking_failures=0,
                         events_attempted=0,events_dropped=0,phase_seen=6 if active else 0,selected=[int(active),0,0,0],
                         runtime_ns=1000 if active else 0,migrations=0))
    write('scheduler-stats.json',dict(instrumentation_mode='FULL',optional_metrics=dict(runtime_ns='COLLECTED',migrations='COLLECTED'),cpus=cpus,
         event_schema=2,mask_cpu_limit=16,events_export_error=0,events=[],live_monitor=dict(status='PASS',failures=0,kernel_opened=True,samples=2)))
    off=dict(read_only=True,enable_stats_called=False,sysctl=0,stats_fd_references=0,fds_scanned=10,
        programs=[dict(id=i+1,name=symbol[:15],struct_ops_type=True,btf_id=42,run_cnt=0,run_time_ns=0,tag='0123456789abcdef')for i,symbol in enumerate(stats_off.CALLBACKS.values())])
    for stage in ['attached','before-measured','after-measured']:
        write('stats-off-'+stage+'.json',off)
        write('stats-off-'+stage+'-command.json',dict(returncode=0,enable_stats_called=False,stdout=json.dumps(off)))
    write('instrumentation.json',validation.INSTRUMENTATION)
    write('record-type.json',dict(purpose='FUNCTIONAL_ONLY'if role=='functional'else'PERFORMANCE_DIAGNOSTIC_COMPARISON'))
    write('measurement.json',dict(scope='FUNCTIONAL_ONLY_EXCLUDED_FROM_PERFORMANCE'if role=='functional'else'ONE_MEASURED_REQUEST'))
    argv=['SYNTHETIC_SERVER','--threads','4','--threads-batch','4','--threads-http','2']
    write('thread-contract.json',dict(server_identity=identity,argv=argv,openmp=dict(OMP_NUM_THREADS='4',OMP_DYNAMIC='FALSE',**study.omp_options()),
                                    compute_threads=4,batch_threads=4,http_threads=2,entire_server_thread_limit=False))
    text='OFFLINE_SYNTHETIC_FIXTURE\n'+'\n'.join(f'NEWA_TEAM pid=4242 team=4 index={i} native=0x{i+100:x}'for i in range(4))+'\n'
    (root/'server.stderr.log').write_text(text)
    team=workers.team(text,4,4242)
    for name in ['openmp-team.json','openmp-before-measured.json','openmp-after-measured.json']:write(name,team)
    snapshots=[]
    for after in [False,True]:
        snapshots.append(dict(monotonic_ns=23000000000 if after else 1000000000,
            threads={str(4242+i):dict(starttime=100+i,runtime_ns=16000000000 if after else 1000000000,
                 context_switches=20 if after else 10,nr_switches=20 if after else 10,**{'se.nr_migrations':2 if after else 1})for i in range(4)},
            cpus={'cpu'+str(c):dict(total=3000 if after else 1000,idle=900 if after else 500)for c in range(16)}))
    before,after=snapshots
    write('counters-before.json',before);write('counters-after.json',after)
    write('visit-timing.json',dict(start_monotonic_ns=0,end_monotonic_ns=24000000000,start_utc='SYNTHETIC',end_utc='SYNTHETIC'))
    data=dict(content='합성 단위 테스트 응답',tokens_evaluated=920,tokens_predicted=128,
              timings=dict(prompt_n=920,predicted_n=128,prompt_ms=15000,predicted_ms=6000,prompt_per_second=920000/15000,predicted_per_second=128000/6000))
    for name in ['warmup-1','warmup-2',role]:
        write(name+'-response.json',data)
        client=dict(request_sha256=REQUEST_SHA,response_sha256=hashlib.sha256((root/(name+'-response.json')).read_bytes()).hexdigest(),http_status=200,role=name,request_wall_ms=21500)
        write(name+'-client.json',client)
    sample=validation.request_metrics(before,after,data,client,10,thermal)
    write('result.json',dict(**LABELS,**visit,visit_status='COMPLETE',valid_for_comparison=role=='measured',errors=[],sched_ext_after='disabled',
         configuration_guard=guard,thermal_throttle_whole_visit=thermal,requests_completed=['warmup-1','warmup-2',role],sample=sample))
    return root
