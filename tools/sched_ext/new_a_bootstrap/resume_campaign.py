# SPDX-License-Identifier: GPL-2.0
"""Read-only audit and explicit resume. Never migrates historical raw contracts."""
import json
import os
from pathlib import Path
import sys
import uuid


def failed_commands(b,path):
    rows=[]
    for file in Path(path).glob('*-command.json'):
        value=b.read(file)
        if isinstance(value,dict)and value.get('returncode',0)!=0:rows.append(dict(path=str(file),**value))
    return rows


def audit(driver,folder):
    module=driver.engine();b,x=module.b,module.x
    folder=Path(folder).resolve();root=folder/'tuning'
    result=dict(campaign=str(folder),decision='RESUME_BLOCKED',raw_valid=[],functional_valid={},failed_attempts=[],errors=[],power_journal='PRESERVED_NOT_READ_OR_RECLASSIFIED')
    try:
        b.require(folder.is_dir()and folder.stat().st_uid==os.getuid(),'Owned campaign directory required')
        tune=x.load(root)
        result['power_contract']=tune.get('power_management','LEGACY_MANAGED_POWER')
        result['identity_changes']={}
        for name,expected in tune['inputs'].items():
            try:actual=b.sha(name)
            except OSError:actual='MISSING'
            if actual!=expected:result['identity_changes'][name]=dict(expected=expected,actual=actual)
        # Revalidate native raw against its own historical sealed contract, not
        # against new live source files. Never turn a failed attempt into PASS.
        seen=set()
        for record in x.records(root):
            path=Path(record['raw']);variant=Path(record['variant']);seal=b.read(variant/'seal.json')
            for name,key in [('campaign.private.json','campaign_sha256'),('host-profile.private.json','host_sha256'),('runtime-baseline.private.json','baseline_sha256'),('topology.bin','topology_sha256')]:
                b.require(b.sha(variant/name)==seal[key],'Historical variant seal changed')
            campaign=b.read(variant/'campaign.private.json');x.validate_record(record,campaign)
            if (path/'completion.json').is_file():
                b.require(not(path/'failure.json').exists(),'Failed attempt cannot become success')
                key=(record['stage'],record['slot']['id']);b.require(key not in seen,'Duplicate successful slots');seen.add(key)
                metrics=b.verify(path,campaign,record['slot']);result['raw_valid'].append(dict(path=str(path),slot=record['slot'],metrics=metrics))
            else:
                failure=b.read(path/'failure.json');cleanup=b.read(path/'cleanup.json')
                safe=b.controller.safe_auth_resume(path,campaign,failure,cleanup)
                noise=b.controller.safe_interference_resume(path,campaign,failure,cleanup)
                result['failed_attempts'].append(dict(path=str(path),failure=failure,cleanup=cleanup,failed_commands=failed_commands(b,path),explicit_auth_retry_eligible=safe,explicit_interference_retry_eligible=noise))
        # Successful Audit/Clean proofs are also revalidated under their original
        # seal; no old functional PASS is inferred for a different build/config.
        for variant in (root/'variants').glob('*'):
            seal=b.read(variant/'seal.json')
            for name,key in [('campaign.private.json','campaign_sha256'),('host-profile.private.json','host_sha256'),('runtime-baseline.private.json','baseline_sha256'),('topology.bin','topology_sha256')]:b.require(b.sha(variant/name)==seal[key],'Historical variant seal changed')
            campaign=b.read(variant/'campaign.private.json')
            for path in (variant/'attempts/FUNCTIONAL').glob('*/*'):
                if (path/'completion.json').exists():continue
                failure=b.read(path/'failure.json');cleanup=b.read(path/'cleanup.json')
                result['failed_attempts'].append(dict(path=str(path),stage='FUNCTIONAL',failure=failure,cleanup=cleanup,failed_commands=failed_commands(b,path),explicit_auth_retry_eligible=b.controller.safe_auth_resume(path,campaign,failure,cleanup),explicit_interference_retry_eligible=b.controller.safe_interference_resume(path,campaign,failure,cleanup)))
            proofs=b.controller.completed(variant,campaign,'FUNCTIONAL',True,True)
            result['functional_valid'][str(variant)]={key:str(value)for key,value in proofs.items()}
        if not b.contracts.unmanaged(tune)or result['identity_changes']:
            result['decision']='NEW_CAMPAIGN_REQUIRED'
            result['reason']='Legacy managed-power contract or pinned source identity changed; no migration, no old/new pooling'
            return result
        b.current(tune)
        for variant in (root/'variants').glob('*'):
            campaign=b.contracts.load_campaign(variant,current=True)
            b.controller.completed(variant,campaign,'FUNCTIONAL',True,True)
        b.require(all(v['explicit_auth_retry_eligible']or v['explicit_interference_retry_eligible']for v in result['failed_attempts']),'Failure/Cleanup not eligible for explicit resume')
        result['decision']='RESUME_AVAILABLE';result['reason']='Same contracts/assets/host, successful raw revalidated, explicit approval still required'
    except (OSError,ValueError,KeyError,TypeError)as error:result['errors'].append(type(error).__name__+': '+str(error))
    return result


def arguments(tune,root,resume_auth,resume_interference=False):
    result=[]
    for key,value in tune['options'].items():
        if key=='command':continue
        flag='--'+key.replace('_','-')
        if value is None or value is False:continue
        if value is True:result.append(flag)
        elif isinstance(value,list):
            for item in value:result.extend([flag,str(item)])
        else:result.extend([flag,str(value)])
    result.extend(['--output',str(root),'--execute','--acknowledge-existing-safety-fail','--resume'])
    if resume_auth:result.append('--resume-auth')
    if resume_interference:result.append('--resume-interference')
    return result


def run(driver,folder,*,resume_auth=False,resume_interference=False):
    # Caller holds prepare/runtime locks until child takes over.
    module=driver.engine();folder=Path(folder).resolve()
    review=audit(driver,folder)
    module.b.require(review['decision']=='RESUME_AVAILABLE',review['decision']+': '+str(review.get('errors')or review.get('reason')))
    module.b.require(all(resume_auth and row['explicit_auth_retry_eligible']or resume_interference and row['explicit_interference_retry_eligible']for row in review['failed_attempts']),'Explicit matching --resume-auth/--resume-interference required for preserved failed attempts')
    import auto_run,auto_system
    module.b.controller.quiet_boundary()
    auto_system.consent('완료된 raw를 재검증하여 건너뛰고 남은 세션만 재개합니다. 실패 기록 보존, 실패 측정 자동 재시도 없음.\n인증 실패 슬롯의 신규 시도는 --resume-auth를 명시한 경우만 허용합니다. 전원 설정은 변경하거나 복구하지 않습니다. 선택적 상태 기록은 읽기 전용입니다.')
    args,command=driver.action('run',arguments(module.x.load(folder/'tuning'),folder/'tuning',resume_auth,resume_interference),module)
    review_path=folder/('resume-approval-'+uuid.uuid4().hex+'.private.json')
    auto_system.save(review_path,dict(review=review,approved=True,command=command,automatic_retry=False))
    checks=driver.dependencies(args,True,module);driver.admission_record(args,command,driver.verify(),checks)
    return command
