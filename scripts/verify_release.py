#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""공개 파일 무결성·금지 파일·알려진 비밀정보 형식과 최종 정책 해시를 확인한다."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
PATTERNS={
    'private_home_path':r'/(?:home|Users)/[A-Za-z0-9_.-]+/|Desk[t]op/',
    'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'github_token':r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}',
    'aws_key':r'AKIA[A-Z0-9]{16}',
    'api_key':r'sk-(?:proj-)?[A-Za-z0-9_-]{32,}',
}
FORBIDDEN={'.gguf','.o','.so','.a','.bin','.gz','.zip','.tar','.log','.jsonl','.pem','.key','.p12','.pfx','.pyc'}
SECRET_NAMES={'.env','id_rsa','id_ed25519','.git-credentials','hosts.yml'}


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def check():
    errors=[];paths=[]
    for p in sorted(ROOT.rglob('*')):
        if '.git'in p.relative_to(ROOT).parts:continue
        if not p.is_file()and not p.is_symlink():continue
        name=p.relative_to(ROOT).as_posix();paths.append(name)
        if p.is_symlink():errors.append((name,'symlink'));continue
        if p.suffix in FORBIDDEN or p.name in SECRET_NAMES:errors.append((name,'forbidden_file_type'))
        if p.stat().st_size>524288:errors.append((name,'file_over_512KiB'))
        raw=p.read_bytes()
        if b'\0'in raw:errors.append((name,'binary_content'));continue
        try:text=raw.decode('utf-8')
        except UnicodeDecodeError:errors.append((name,'not_utf8'));continue
        for label,pattern in PATTERNS.items():
            if re.search(pattern,text):errors.append((name,label))
    policy=json.loads((ROOT/'tools/sched_ext/new_a_final/policy-manifest.json').read_text())
    if policy['policy_change']!='NONE' or policy['strict_acceptance']!='FAILED' or policy['safety_verdict']!='FAIL':errors.append(('policy','verdict_changed'))
    for name,digest in policy['files'].items():
        if not(ROOT/name).is_file()or sha(ROOT/name)!=digest:errors.append((name,'original_policy_hash_mismatch'))
    manifest=json.loads((ROOT/'PUBLICATION_MANIFEST.json').read_text())
    expected={x['path']for x in manifest['files']}
    if set(paths)!=expected|{'PUBLICATION_MANIFEST.json'}:errors.append(('manifest','file_set_mismatch'))
    for row in manifest['files']:
        p=ROOT/row['path']
        if not p.is_file()or sha(p)!=row['sha256']or p.stat().st_size!=row['size']:errors.append((row['path'],'manifest_mismatch'))
    return dict(status='PASS'if not errors else'BLOCKED',files=len(paths),findings=errors,
                note='Pattern scan is not a proof that every possible secret is absent.',actual_bpf_load='NOT_RUN')


if __name__=='__main__':
    result=check();print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(result['status']!='PASS')
