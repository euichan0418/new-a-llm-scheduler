# SPDX-License-Identifier: GPL-2.0
"""실제 최종 E 기능 증거가 같은 입력에 묶여 있을 때만 성능 비교를 허용한다."""
import hashlib,json
from pathlib import Path
from records import read,save,sha,require,seal_check
import study
import integration

def binding(setup,host):
    value={k:setup[k]for k in ['head','base_run','server_argv','server_env','request_sha256','workload_constraints','instrumentation','policy_binaries','build_static','manual_configuration','manual_configuration_sha256','portable_configuration','cpu_probe_binary','portable_configuration_sha256']}
    value['host']=host
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def record(root,profile):
    root=Path(root);setup=read(root/'setup.json')
    require(setup['mode']=='FUNCTIONAL','기능 단계에서만 승인 증거를 저장합니다')
    summary=study.summarize(root)
    require(summary['complete'] and summary['functional_samples']==2 and summary['performance_samples']==0,'실제 기능 검증이 완료되지 않았습니다')
    observed=integration.equivalence(summary)
    files={str(p):sha(p)for p in (root/'visits').rglob('*')if p.is_file()}
    for name in ['completion.json','summary.json']:files[str(root/name)]=sha(root/name)
    save(root/'functional-evidence.json',dict(status='PORTABLE_RUNTIME_FUNCTIONAL_COMPLETE_NOT_SAFETY_APPROVED',binding=binding(setup,profile['host']),files=files,runtime_equivalence=observed,is_safety_approval=False,automatic_next_stage=False))

def verify(setup,profile):
    require(setup['mode']=='PERFORMANCE','성능 비교 단계가 아닙니다')
    root=Path(setup['required_functional_dir'])
    require((root/'functional-evidence.json').is_file(),'최종 E 실제 기능 검증이 아직 완료되지 않았습니다. 성능 비교를 실행하지 않습니다')
    require(not(root/'STOPPED.json').exists(),'실패한 기능 검증은 승인 근거가 아닙니다')
    seal_check(root)
    proof=read(root/'functional-evidence.json')
    require(proof['status']=='PORTABLE_RUNTIME_FUNCTIONAL_COMPLETE_NOT_SAFETY_APPROVED' and proof['is_safety_approval']is False,'잘못된 기능 검증 판정입니다')
    require(proof['binding']==binding(setup,profile['host']),'객체/서버/요청/Host Profile 부팅 환경이 기능 검증과 다릅니다')
    for name,digest in proof['files'].items():require(sha(name)==digest,'기능 검증 원시 증거가 변경됐습니다: '+name)
    completion=read(root/'completion.json')
    require(completion['status']=='PORTABLE_RUNTIME_FUNCTIONAL_COMPLETE_NOT_SAFETY_APPROVED' and completion['strict_acceptance']=='FAILED' and completion['safety_verdict']=='FAIL','기능 검증 완료 또는 기존 안전성 판정이 다릅니다')
    fresh=study.summarize(root)
    require(fresh['mode']=='FUNCTIONAL' and fresh['complete'] and fresh['functional_samples']==2 and fresh['performance_samples']==0,'기능 원시 증거의 재검증에 실패했습니다')
    require(integration.equivalence(fresh)==proof['runtime_equivalence'],'실제 런타임 구성 비교 증거가 변경됐습니다')
    return dict(status='FUNCTIONAL_EVIDENCE_REVALIDATED',source=str(root),binding=proof['binding'],is_safety_approval=False)
