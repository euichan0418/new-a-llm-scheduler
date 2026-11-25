#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Refresh reviewed Git-index files, preserving provenance and all release checks."""
import argparse
import copy
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import verify_release

ROOT=Path(__file__).resolve().parent.parent
ORIGIN='AUTOTUNE_AUTH_POWER_ISOLATION_PUBLICATION'
NOTE='Unmanaged-power autotuning, foreground authentication, sealed checkpoint resume and development snapshots; actual sudo/BPF/long-running inference NOT_RUN.'


def encode(value):return (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()


def candidates(root,names):
    root=Path(root);manifest=json.loads((root/'PUBLICATION_MANIFEST.json').read_text());provenance=json.loads((root/'PUBLICATION_PROVENANCE.json').read_text())
    names=sorted(set(names));old={r['path']:r for r in manifest['files']};origins={r['path']:r for r in provenance['files']}
    files={}
    for name in names:
        rel=PurePosixPath(name)
        if rel.is_absolute()or '..'in rel.parts or '.git'in rel.parts or '.autotune-work'in rel.parts:raise ValueError('Unapproved release path: '+name)
        path=root/name
        if any((root/Path(*rel.parts[:i])).is_symlink()for i in range(1,len(rel.parts)+1)):raise ValueError('Release symlink: '+name)
        files[name]=path.read_bytes()
    if 'PUBLICATION_MANIFEST.json'not in files or 'PUBLICATION_PROVENANCE.json'not in files:raise ValueError('Both tracked publication metadata files required')
    rows=[]
    for name,raw in files.items():
        if name in ['PUBLICATION_MANIFEST.json','PUBLICATION_PROVENANCE.json']:continue
        digest=hashlib.sha256(raw).hexdigest();row=copy.deepcopy(origins.get(name,{}))
        if row.get('release_sha256')!=digest:
            history=row.get('previous_releases',[])
            if row:
                previous={k:v for k,v in row.items()if k not in ['path','previous_releases']}
                if previous not in history:history.append(previous)
            row=dict(path=name,source=ORIGIN,kind='REVIEWED_RUNTIME_TOOL_TEST_OR_DOCUMENTATION',release_sha256=digest)
            if history:row['previous_releases']=history
        rows.append(row)
    provenance['files']=rows
    if NOTE not in provenance['notes']:provenance['notes'].append(NOTE)
    files['PUBLICATION_PROVENANCE.json']=encode(provenance)
    manifest['files']=[]
    for name,raw in files.items():
        if name=='PUBLICATION_MANIFEST.json':continue
        row=dict(old.get(name,{}));row.update(path=name,size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),executable=bool((root/name).stat().st_mode&0o111))
        manifest['files'].append(row)
    return {'PUBLICATION_PROVENANCE.json':files['PUBLICATION_PROVENANCE.json'],'PUBLICATION_MANIFEST.json':encode(manifest)}


def refresh(root,names,*,write=False):
    root=Path(root);expected=candidates(root,names)
    changed=[name for name,raw in expected.items()if (root/name).read_bytes()!=raw]
    if not write:
        if changed:raise ValueError('Publication metadata stale: '+', '.join(changed))
        return dict(status='MANIFEST_CURRENT',files=len(set(names)))
    previous={name:(root/name).read_bytes()for name in expected}
    try:
        for name,raw in expected.items():(root/name).write_bytes(raw)
        result=verify_release.check()
        if result['status']!='PASS':raise ValueError('Release validation rejected metadata: '+json.dumps(result['findings']))
    except BaseException:
        for name,raw in previous.items():(root/name).write_bytes(raw)
        raise
    return dict(status='MANIFEST_REFRESHED_AND_VERIFIED',files=len(set(names)),provenance_history_preserved=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--write',action='store_true');args=parser.parse_args()
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().strip('\0').split('\0')
    print(json.dumps(refresh(ROOT,paths,write=args.write),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError)as error:raise SystemExit('MANIFEST_BLOCKED: '+str(error))
