# SPDX-License-Identifier: GPL-2.0
"""Append-only stages, fresh raw verification, and no automatic failed retry."""
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from planning import candidate,paired,default,prefill,decode,possible,balanced,means,screening,report,fingerprint,require
import backend as b


def seal(root,value):
    b.save(root/'tuning.private.json',value);b.save(root/'seal.json',dict(sha256=b.sha(root/'tuning.private.json')))


def load(root):
    root=Path(root);require(b.sha(root/'tuning.private.json')==b.read(root/'seal.json')['sha256'],'Tuning contract changed')
    value=b.read(root/'tuning.private.json')
    require(b.sha(root/'plan.private.json')==value['plan_sha256'] and b.read(root/'plan.private.json')['rules']==value['rules'],'Approved plan/selection rules changed')
    return value


def variant(root,tune,config,scheduler):
    path=(Path(tune['functional_root'])/'variants'/config['id'])if scheduler=='new-a' else root/'variants'/config['id']
    if scheduler=='new-a':return path,b.admit_newa(path,tune,config)
    if not path.exists():return path,b.variant(tune,config,path)
    value=b.contracts.load_campaign(path,current=True)
    require(value['configuration']['id']==config['id'] and value['configuration']['affinity_cpus']==config['affinity_cpus'] and value['id']==tune['id']+'-'+config['id'],'Foreign variant')
    return path,value


def records(root):return [b.read(p)for p in sorted((root/'records').glob('*.json'))if not p.name.endswith('.outcome.json')]


def validate_record(record,campaign):
    c=record['candidate'];expected=candidate(campaign['host']['detection'],c['e_count'],c['compute_threads'])
    require(c==expected,'Journal candidate altered')
    require(all(campaign['configuration'][k]==c[k]for k in ['compute_threads','batch_threads','http_threads']) and campaign['domain']['selected']==c['affinity_cpus'],'Journal configuration differs from raw campaign')
    s=record['slot'];require(s['candidate_id']==c['id'] and s['tuning_stage']==record['stage'] and s['block']==record['block'] and s['position']==record['position'],'Journal/raw slot differs')


def recovered(record,tune,resume_auth):
    path=Path(record['raw']);campaign=b.contracts.load_campaign(record['variant'],current=True)
    if (path/'completion.json').exists():
        require(not(path/'failure.json').exists(),'Failed raw cannot become success')
        validate_record(record,campaign)
        return b.verify(path,campaign,record['slot'])
    require(path.exists(),'Incomplete attempt without raw cleanup; review required')
    failure=b.read(path/'failure.json');cleanup=b.read(path/'cleanup.json')
    require(resume_auth and b.controller.safe_auth_resume(path,campaign,failure,cleanup),'Failed attempt retained; automatic retry prohibited: '+str(path))
    return None


def stage_plan(root,name,visits):
    folder=root/'plans';folder.mkdir(exist_ok=True);path=folder/(name+'.json');value=dict(stage=name,visits=visits)
    if path.exists():require(b.read(path)==value,'Stage selection/order changed')
    else:b.save(path,value)


def export(root,tune,rows,complete):
    result=report(tune['host']['detection'],rows,tune['rules'],tune['scheduler'],complete)
    result.update(excluded_attempts=[r for r in records(root)if not(Path(r['raw'])/'completion.json').exists()],execution_conditions=dict(kernel=tune['host']['kernel'],server_sha256=tune['base']['server_sha'],model_sha256=tune['base']['model_sha'],request_sha256=tune['base']['request_sha256'],power=tune['host']['power_binding'],thermal_limit_c=85,whole_process_cpu_clock=True),raw_records=rows,limits=tune['limits'])
    folder=root/'analysis';folder.mkdir(exist_ok=True)
    path=folder/(b.token()+'.json');b.save(path,result)
    columns=['stage','block','position','compute','batch','affinity','prefill_tps','decode_tps','http_s','process_cpu_s','peak_temperature_c','observed_process_tid_count','raw']
    with path.with_suffix('.csv').open('x',newline='')as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for r in rows:
            c,m=r['candidate'],r['metrics'];writer.writerow(dict(stage=r['stage'],block=r['block'],position=r['position'],compute=c['compute_threads'],batch=c['batch_threads'],affinity=','.join(map(str,c['affinity_cpus'])),**{k:m[k]for k in ['prefill_tps','decode_tps','process_cpu_s','peak_temperature_c','observed_process_tid_count']},http_s=m['http_ms']/1000,raw=r['raw']))
    lines=['# Intel Hybrid Auto-Tuning 결과','',f"완료: {complete}; scheduler={tune['scheduler']}",'', '기본 정책을 유지하는 것과 통계적 동등성 입증은 다르다. 단계 간 표본을 합산하지 않는다.','', '| 단계 | Decode | Prefill | Affinity | Prefill tok/s | Decode tok/s | HTTP s | CPU Time s | n |','|---|---:|---:|---|---:|---:|---:|---:|---:|']
    for stage,g in result['statistics_by_stage'].items():
        for x in g.values():
            c=x['candidate'];lines.append(f"| {stage} | {c['compute_threads']} | {c['batch_threads']} | {','.join(map(str,c['affinity_cpus']))} | {x['prefill_tps']['mean']:.4f} | {x['decode_tps']['mean']:.4f} | {x['http_ms']['mean']/1000:.6f} | {x['process_cpu_s']['mean']:.6f} | {x['n']} |")
    lines+=['', 'CPU: '+json.dumps(result['cpu'],ensure_ascii=False), '', '기존 공식: '+json.dumps(result['default_policy'],ensure_ascii=False), '', '단계별 관측상 최고 설정: '+json.dumps(result['observed_by_stage'],ensure_ascii=False), '', '가장 빠른 단일 표본: '+json.dumps(result['fastest_single_observation'],ensure_ascii=False), '', '확증된 기본 공식 대비 개선율(측정 scheduler 범위): '+str(result['default_formula_gain_pct']), '', 'Linux 실행 추천(NEW_A 정책 추천과 별개): '+json.dumps(result['linux_execution_recommendation'],ensure_ascii=False), '', '최종 NEW_A 추천: '+json.dumps(result['final_new_a_recommendation'],ensure_ascii=False),'',result['reason'],'', 'STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED']
    path.with_suffix('.md').write_text('\n'.join(lines)+'\n');print('RESULT '+str(path),flush=True)
    return result


class Experiment:
    def __init__(self,root,tune,resume_auth=False,auth=None):
        self.root,self.tune,self.resume_auth=Path(root),tune,resume_auth;self.rows=[];self.deadline=time.monotonic()+tune['limits']['seconds']
        self.auth=auth
        (self.root/'records').mkdir(exist_ok=True);(self.root/'raw').mkdir(exist_ok=True);(self.root/'variants').mkdir(exist_ok=True)
    def bound(self):
        require(time.monotonic()<self.deadline,'Controller boundary deadline; completed raw preserved')
        count=len(records(self.root))+sum(1 for p in (self.root/'variants').glob('*/attempts/FUNCTIONAL/*/*')if p.is_dir())
        require(count<self.tune['limits']['sessions'],'Approved campaign session limit reached')
    def functionals(self,path,campaign):
        done=b.controller.completed(path,campaign,'FUNCTIONAL',self.resume_auth)
        needed=b.runner.slots('FUNCTIONAL') if self.tune['scheduler']=='new-a' else [v for v in b.runner.slots('FUNCTIONAL')if v['policy']=='C']
        if self.tune['scheduler']=='new-a':require(len(done)==4,'Missing fresh NEW_A functionality; no automatic attach here');return
        for v in needed:
            if v['id']in done:continue
            self.bound();b.current(self.tune);slot=dict(v,stage='FUNCTIONAL')
            require(self.auth is not None,'BOUNDARY_USER_AUTH_REQUIRED')
            self.auth.before_session(v['id'],ready=self.bound,after_auth=lambda:b.current(self.tune))
            folder=Path(path)/'attempts/FUNCTIONAL'/v['id'];folder.mkdir(parents=True,exist_ok=True);raw=folder/b.token()
            b.acquire(raw,campaign,path,slot);self.auth.completed(raw,campaign,slot)
    def measure(self,stage,visit):
        config=visit['candidate'];path,campaign=variant(self.root,self.tune,config,self.tune['scheduler']);self.functionals(path,campaign)
        slot=dict(id=stage+'-'+str(visit['block'])+'-'+config['id'],policy='E'if self.tune['scheduler']=='new-a'else'C',audit=False,stage='PERFORMANCE',block=visit['block'],position=visit['position'],tuning_stage=stage,candidate_id=config['id'],decision_rules_sha256=fingerprint(self.tune['rules']))
        old=[r for r in records(self.root)if r['slot']==slot]
        valid=[]
        for r in old:
            result=recovered(r,self.tune,self.resume_auth)
            if result is not None:valid.append((r,result))
        require(len(valid)<=1,'Duplicate successful samples')
        if valid:
            require(valid[0][0]==old[-1],'A later failed/unknown attempt follows success');record,result=valid[0]
        else:
            self.bound();b.current(self.tune);require(self.auth is not None,'BOUNDARY_USER_AUTH_REQUIRED')
            self.auth.before_session(slot['id'],ready=self.bound,after_auth=lambda:b.current(self.tune))
            folder=self.root/'raw'/stage/slot['id'];folder.mkdir(parents=True,exist_ok=True);raw=folder/b.token()
            record=dict(raw=str(raw),variant=str(path),slot=slot,stage=stage,candidate=config,block=visit['block'],position=visit['position'])
            record_path=self.root/'records'/(b.token()+'.json');b.save(record_path,record)
            try:result=b.acquire(raw,campaign,path,slot)
            except BaseException as error:
                b.save(record_path.with_name(record_path.stem+'.outcome.json'),dict(status='FAILED_EXCLUDED',error=type(error).__name__+': '+str(error),automatic_retry=False));raise
            b.save(record_path.with_name(record_path.stem+'.outcome.json'),dict(status='RAW_COMPLETE',cleanup='PASS'))
        if self.auth is not None:self.auth.completed(record['raw'],campaign,slot)
        row=dict(stage=stage,candidate=config,block=visit['block'],position=visit['position'],metrics=result,raw=record['raw']);self.rows.append(row)
        export(self.root,self.tune,self.rows,False)
    def stage(self,name,candidates,blocks):
        visits=balanced(candidates,blocks);stage_plan(self.root,name,visits)
        for visit in visits:self.measure(name,visit)
    def run(self):
        d=self.tune['host']['detection'];base=default(d);r=self.tune['rules'];complete=False
        try:
            self.stage('PREFILL',prefill(d),2)
            selected=screening(self.rows,base,r);batch=selected[0]if selected else base
            grid=decode(d,batch);grid={v['id']:v for v in [*grid,base]}
            self.stage('DECODE',list(grid.values()),2)
            finalists={}
            for stage in ['PREFILL','DECODE']:
                rows=[x for x in self.rows if x['stage']==stage]
                for c in screening(rows,base,r):
                    score=statistics.mean(v['http_ms']for v in paired(rows,base['id'],c['id']));previous=finalists.get(c['id'])
                    if previous is None or score<previous[0]:finalists[c['id']]=(score,c)
            chosen=[c for score,c in sorted(finalists.values(),key=lambda v:v[0])][:r['finalists']-1]
            if chosen:self.stage('CONFIRMATION',[base,*chosen],r['confirmation_blocks'])
            complete=True
        finally:result=export(self.root,self.tune,self.rows,complete)
        return result
