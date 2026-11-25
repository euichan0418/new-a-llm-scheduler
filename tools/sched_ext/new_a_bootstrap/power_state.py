# SPDX-License-Identifier: GPL-2.0
"""Transactional power state v2; HWP bridge, exact readback, immutable attempts."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

PROFILES={'performance','balanced','power-saver'}
GOV='scaling_governor'
EPP='energy_performance_preference'


def io():
    import auto_system
    return auto_system


def require(ok,message):
    if not ok:raise ValueError(message)


def capture():
    values={};policies={}
    turbo=Path('/sys/devices/system/cpu/intel_pstate/no_turbo')
    if not turbo.is_file():turbo=Path('/sys/devices/system/cpu/cpufreq/boost')
    if turbo.is_file():values[str(turbo.resolve())]=turbo.read_text().strip()
    for governor in sorted({p.resolve()for p in Path('/sys/devices/system/cpu').glob('cpu*/cpufreq/scaling_governor')}):
        folder=governor.parent;values[str(governor)]=governor.read_text().strip()
        row=dict(driver=(folder/'scaling_driver').read_text().strip(),cpus=(folder/'related_cpus').read_text().split(),governors=(folder/'scaling_available_governors').read_text().split())
        epp=folder/EPP
        row['epp_available']=(folder/'energy_performance_available_preferences').read_text().split()if epp.is_file()else[]
        if epp.is_file():values[str(epp)]=epp.read_text().strip()
        policies[str(folder)]=row
    for path,value in values.items():require(io().allowed_setting(path,value),'Unsupported original power value: '+path)
    profile=None
    if Path('/usr/bin/powerprofilesctl').is_file():
        try:profile=subprocess.check_output(['/usr/bin/powerprofilesctl','get'],text=True,stderr=subprocess.DEVNULL,timeout=10).strip()
        except (OSError,subprocess.SubprocessError):pass
    status=Path('/sys/devices/system/cpu/intel_pstate/status')
    binding=dict(policies=policies,controls=sorted(values),intel_pstate_status=status.read_text().strip()if status.is_file()else None,
                 online=Path('/sys/devices/system/cpu/online').read_text().strip(),boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    return dict(schema=2,sysfs=values,profile=profile,binding=binding,package_installation_not_automatically_rolled_back=True)


def validate(snapshot):
    require(snapshot.get('profile')in PROFILES,'Original power profile unavailable/unsupported')
    binding=snapshot.get('binding',{});policies=binding.get('policies',{})
    require(policies and set(snapshot['sysfs'])==set(binding['controls']),'Power policy/control scope incomplete')
    for path,value in snapshot['sysfs'].items():require(str(Path(path).resolve())==path and io().allowed_setting(path,value),'Unapproved power control/value')
    for folder,p in policies.items():
        g=snapshot['sysfs'][folder+'/'+GOV];require(g in p['governors'],'Original governor unavailable')
        if p['epp_available']:
            value=snapshot['sysfs'][folder+'/'+EPP]
            require(value in p['epp_available']or(value.isdigit()and 0<=int(value)<=255),'Original EPP unavailable')
            require(p['driver']=='intel_pstate'and binding['intel_pstate_status']=='active','Unsupported EPP driver/mode; no guessed transition')
            require('powersave'in p['governors'],'HWP EPP-unlock governor unavailable')
            require(g!='performance'or value in ['performance','0'],'Inconsistent HWP performance/EPP baseline')


def domain(actual,expected):
    require(actual['binding']==expected['binding']and set(actual['sysfs'])==set(expected['sysfs']),'POWER_DOMAIN_CHANGED: boot/online/driver/policy/EPP scope changed')


def mismatch(actual,expected,*,non_turbo=False):
    domain(actual,expected);result=[]
    if actual['profile']!=expected['profile']:result.append('profile')
    for path,value in expected['sysfs'].items():
        if non_turbo and Path(path).name in ['no_turbo','boost']:continue
        if actual['sysfs'][path]!=value:result.append(path)
    return result


def plan(original=None):
    current=io().snapshot();original=current if original is None else original
    require(original.get('schema')==2 and 'binding'in original,'ORIGINAL_EPP_BASELINE_NOT_RECORDED: capture a new full snapshot')
    before=dict(original,profile=original.get('profile')or current['profile']);domain(current,before);validate(before)
    after={}
    for path in before['sysfs']:
        name=Path(path).name
        after[path]='1'if name=='no_turbo'else'0'if name=='boost'else'performance'
    require(any(Path(p).name in ['no_turbo','boost']for p in after),'Turbo control unverified')
    rows=[dict(kind='profile',before=before['profile'],after='performance',contract='POWER_STATE_V2',binding=before['binding'])]
    rows.extend(dict(kind='sysfs',path=p,before=before['sysfs'][p],after=after[p])for p in sorted(after))
    if current['profile']=='performance'and current['sysfs']==after and before['profile']=='performance'and before['sysfs']==after:return []
    return rows


def unpack(rows):
    require(rows and rows[0].get('contract')=='POWER_STATE_V2','POWER_PLAN_V2_REQUIRED; no partial governor/EPP baseline')
    require(rows[0]['kind']=='profile'and all(r['kind']=='sysfs'for r in rows[1:]),'Invalid power plan')
    paths=[r['path']for r in rows[1:]];require(len(paths)==len(set(paths)),'Duplicate power controls')
    original=dict(profile=rows[0]['before'],sysfs={r['path']:r['before']for r in rows[1:]},binding=rows[0]['binding'])
    target=dict(profile=rows[0]['after'],sysfs={r['path']:r['after']for r in rows[1:]},binding=rows[0]['binding'])
    validate(original);validate(target);return original,target


def preregister(rows,journal):
    original,target=unpack(rows)if rows else(None,None)
    current=io().snapshot()if rows else None
    if rows:domain(current,original)
    changed=bool(rows and mismatch(current,original))
    io().save(journal,dict(schema=2,changes=rows,original=original,target=target,attempted=list(range(len(rows)))if changed else[],restored=[],
                          status='INSTALL_SIDE_EFFECTS'if changed else'NOT_APPLIED',attempt_history=[],automatic_credentials_refresh=False))


def operation(kind,value,path=None):return dict(kind=kind,value=value,**(dict(path=path)if path else{}))


def sequence(expected,actual):
    domain(actual,expected);steps=[]
    turbo=[p for p in expected['sysfs']if Path(p).name in ['no_turbo','boost']]
    steps.extend(operation('sysfs','1'if Path(p).name=='no_turbo'else'0',p)for p in turbo)
    for folder,p in expected['binding']['policies'].items():
        if p['epp_available']:
            steps.append(operation('sysfs','powersave',folder+'/'+GOV))
    steps.append(operation('profile',expected['profile']))
    steps.extend(operation('sysfs',v,p)for p,v in expected['sysfs'].items()if Path(p).name==EPP)
    steps.extend(operation('sysfs',v,p)for p,v in expected['sysfs'].items()if Path(p).name==GOV)
    return steps,turbo


def readback(expected,non_turbo=False):
    observations=[]
    for i in range(2):
        current=io().snapshot();observations.append(current)
        require(not mismatch(current,expected,non_turbo=non_turbo),'POWER_READBACK_MISMATCH: '+str(mismatch(current,expected,non_turbo=non_turbo)))
        if i==0:time.sleep(.05)
    return observations


def attempt(journal,state,expected,mode):
    journal=Path(journal);record=journal.with_name(journal.name+'.'+mode+'-'+uuid.uuid4().hex[:12]+'.json')
    data=dict(schema=2,phase=mode,status='STARTED',expected=expected,operations=[],errors=[])
    io().save(record,data);state.setdefault('attempt_history',[]).append(str(record));io().save(journal,state)
    try:
        actual=io().snapshot();validate(expected);domain(actual,expected);data['before']=actual
        steps,turbo=sequence(expected,actual)
        for step in steps:
            event=dict(step,status='PLANNED');data['operations'].append(event);io().save(record,data)
            try:
                row=dict(kind=step['kind'],**(dict(path=step['path'])if step['kind']=='sysfs'else{}))
                # Always invoke PPD; an ActiveProfile string alone is not driver verification.
                if step['kind']=='profile'or io().read_setting(row)!=step['value']:
                    io().write_setting(row,step['value'],record.with_suffix('.commands.jsonl'))
                    event['status']='WRITTEN'
                else:event['status']='ALREADY_MATCHED'
            except (ValueError,OSError,subprocess.SubprocessError)as error:
                event['status']='FAILED';event['error']=str(error);data['errors'].append(str(error))
                if mode=='apply'or 'sudo:'in str(error)or isinstance(error,subprocess.TimeoutExpired):raise
            finally:io().save(record,data)
        # Do not enable Turbo after a failed/partial policy or EPP restoration.
        data['non_turbo_observed']=readback(expected,True)
        require(not data['errors'],'POWER_TRANSITION_COMMAND_FAILED; errors preserved')
        for path in turbo:
            value=expected['sysfs'][path];event=operation('sysfs',value,path);event['status']='PLANNED';data['operations'].append(event);io().save(record,data)
            row=dict(kind='sysfs',path=path)
            if io().read_setting(row)!=value:io().write_setting(row,value,record.with_suffix('.commands.jsonl'))
            event['status']='VERIFIED';io().save(record,data)
        data['observed']=readback(expected);data['status']='PASS';return record
    except BaseException as error:
        data['status']='FAIL';data['errors'].append(type(error).__name__+': '+str(error));raise
    finally:
        io().save(record,data)


def apply(rows,journal,approve=None):
    if not Path(journal).exists():preregister(rows,journal)
    state=json.loads(Path(journal).read_text());require(state['schema']==2 and state['changes']==rows,'Power v2 journal/plan changed')
    if not rows:return state
    (approve or io().consent)('전원 적용/원상 복원 v2: Turbo 제한 -> HWP powersave bridge -> Profile -> EPP -> Governor -> 최종 검증.\n'+json.dumps(rows,ensure_ascii=False,indent=2)+'\n드라이버 모드/daemon 서비스는 변경하지 않습니다. 복원은 원래 EPP/Governor/Profile/Turbo를 모두 검사합니다.')
    io().authenticate();state['attempted']=list(range(len(rows)));state['user_approved']=True;state['status']='APPLYING';io().save(journal,state)
    try:attempt(journal,state,state['target'],'apply')
    except BaseException:
        state['status']='APPLY_FAILED';io().save(journal,state);raise
    state['status']='APPLIED';io().save(journal,state);return state


def restore(journal,quiet,reauthenticate=False,approve=None):
    journal=Path(journal);state=json.loads(journal.read_text())
    if not state.get('attempted'):return
    require(quiet(),'CLEANUP_NOT_VERIFIED: no power writes before cleanup')
    require(state.get('schema')==2,'LEGACY_EPP_BASELINE_UNKNOWN: preserve old journal; use manual-baseline review')
    if reauthenticate:(approve or io().consent)('원래 Profile/EPP/Governor/Turbo 복원만 승인합니다. 추론/재시도는 하지 않습니다.');io().authenticate()
    if state['status']=='RESTORED':
        readback(state['original']);return
    try:attempt(journal,state,state['original'],'restore')
    except BaseException:
        state['status']='RESTORE_REQUIRED';io().save(journal,state);raise
    state['status']='RESTORED';state['restored']=list(range(len(state['changes'])));io().save(journal,state)


def audit_restoration(journal):
    """Read a v2 restore proof; never overwrite a journal or infer missing EPP."""
    journal=Path(journal).resolve();state=json.loads(journal.read_text())
    require(state.get('schema')==2,'RESTORE_PROOF_V2_REQUIRED; legacy EPP not recorded')
    if not state.get('attempted'):
        require(state.get('status')=='NOT_APPLIED'and state.get('attempt_history')==[],'Unattempted power state inconsistent')
        if state['changes']:
            original,_=unpack(state['changes']);require(state['original']==original,'Original/plan mismatch')
            source=journal
        else:
            source=journal.parent/'before-install-environment.private.json';original=json.loads(source.read_text())
            require(original.get('schema')==2,'No original EPP snapshot');validate(original)
        return dict(status='NO_POWER_CHANGE_RECORDED',journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest(),
                    proof_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),proof=str(source),original=original)
    require(state.get('status')=='RESTORED'and state.get('attempted'),'RESTORATION_NOT_VERIFIED: unresolved power journal')
    original,_=unpack(state['changes']);require(state['original']==original,'Restore original/plan mismatch')
    require(state['restored']==list(range(len(state['changes']))),'Restore scope incomplete')
    history=state.get('attempt_history',[]);require(history,'Missing restore attempt evidence')
    path=Path(history[-1]).resolve()
    require(path.parent==journal.parent and path.name.startswith(journal.name+'.restore-'),'Foreign/non-restore final attempt')
    proof=json.loads(path.read_text())
    require(proof.get('status')=='PASS'and proof.get('phase')=='restore'and proof.get('errors')==[]and proof.get('expected')==original,'Restore attempt did not PASS')
    for key,partial in [('non_turbo_observed',True),('observed',False)]:
        rows=proof.get(key,[]);require(len(rows)==2,'Restore readback proof incomplete')
        for row in rows:require(not mismatch(row,original,non_turbo=partial),'Restore saved readback mismatch')
    return dict(status='RESTORE_EVIDENCE_VERIFIED',journal_sha256=hashlib.sha256(journal.read_bytes()).hexdigest(),
                proof_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),proof=str(path),original=original)


def legacy_expected(journal,state):
    original=state.get('original')
    if state.get('schema')==2 and original:return original
    before=journal.parent/'before-install-environment.private.json'
    value=json.loads(before.read_text())if before.is_file()else dict(sysfs={},profile=None)
    fields=dict(value.get('sysfs',{}));profile=value.get('profile')
    for row in state.get('changes',[]):
        if row['kind']=='profile':profile=row['before']
        elif row['kind']=='sysfs':fields[row['path']]=row['before']
    require(profile in PROFILES and fields,'LEGACY_BASELINE_INCOMPLETE; do not guess original state')
    return dict(profile=profile,sysfs=fields)


def pending(journal,quiet,*,readonly=False,approve=None):
    journal=Path(journal);raw=journal.read_bytes();state=json.loads(raw)
    if not state.get('attempted')or state['status']=='RESTORED':return None
    require(quiet(),'CLEANUP_NOT_VERIFIED: manual recovery review blocked')
    live=io().snapshot();validate(live);expected=legacy_expected(journal,state)
    require(live['profile']==expected['profile']and all(live['sysfs'].get(p)==v for p,v in expected['sysfs'].items()),'MANUAL_RECOVERY_NOT_VERIFIED: original known power values differ')
    if state.get('schema')==2:domain(live,expected)
    digest=hashlib.sha256(raw).hexdigest()
    for path in sorted(journal.parent.glob(journal.name+'.manual-baseline-*.json')):
        value=json.loads(path.read_text())
        if value.get('journal_sha256')==digest and value.get('status')=='MANUAL_BASELINE_ACCEPTED'and value.get('snapshot')==live and value.get('approved')is True:return path
    require(not readonly,'MANUAL_BASELINE_REVIEW_REQUIRED: check-only mode cannot approve recovery')
    coverage='구형 저널의 원래 EPP는 NOT_RECORDED입니다.'if state.get('schema')!=2 else'V2 원래 EPP를 포함한 전원값을 재검증했습니다.'
    (approve or io().consent)('과거 전원 복원 실패/미완료 기록을 그대로 보존합니다. 현재 수동 복구 상태가 저널의 알려진 원래값과 일치합니다.\n'+json.dumps(live,ensure_ascii=False,indent=2)+'\n'+coverage+' 과거 실패를 성공으로 바꾸지 않습니다. 표시한 현재 상태를 새 실험의 기준선으로 승인합니까?')
    require(io().snapshot()==live,'Manual recovery state changed during approval')
    require(journal.read_bytes()==raw,'Legacy journal changed during approval')
    path=journal.with_name(journal.name+'.manual-baseline-'+uuid.uuid4().hex[:12]+'.json')
    io().save(path,dict(status='MANUAL_BASELINE_ACCEPTED',approved=True,journal_sha256=digest,snapshot=live,
                       original_epp='NOT_RECORDED'if state.get('schema')!=2 else'VERIFIED_AGAINST_V2',historical_failure_reclassified=False))
    return path
