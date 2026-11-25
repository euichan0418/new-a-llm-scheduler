# SPDX-License-Identifier: GPL-2.0
"""Freeze the reviewed source set before importing the experiment controller."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid


def sha(path):
    with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()


def freeze(root,workspace):
    root=Path(root).resolve();workspace=Path(workspace).resolve()
    raw=(root/'PUBLICATION_MANIFEST.json').read_bytes();manifest=json.loads(raw)
    identity=hashlib.sha256(raw).hexdigest();parent=workspace/'source-snapshots';parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=parent/identity
    rows=[*manifest['files'],dict(path='PUBLICATION_MANIFEST.json',size=len(raw),sha256=identity)]
    def verify(folder):
        for row in rows:
            rel=Path(row['path'])
            if rel.is_absolute()or '..'in rel.parts:raise ValueError('Unsafe snapshot path')
            path=folder/rel
            if any((folder/Path(*rel.parts[:i])).is_symlink()for i in range(1,len(rel.parts)+1))or not path.is_file()or path.stat().st_size!=row['size']or sha(path)!=row['sha256']:
                raise ValueError('FROZEN_SOURCE_IDENTITY_CHANGED: '+row['path'])
    if target.exists():verify(target);return target
    size=sum(r['size']for r in rows)
    if shutil.disk_usage(parent).free<size+512*1024*1024:raise ValueError('DISK_SPACE_BLOCKED snapshot plus 512MiB reserve')
    temporary=parent/('partial-'+uuid.uuid4().hex);temporary.mkdir(mode=0o700)
    # Copy source only: never models, binaries, raw evidence, Git objects or keys.
    verify(root)
    for row in rows:
        source=root/row['path'];dest=temporary/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest);dest.chmod(0o500 if row.get('executable')else 0o400)
    verify(temporary)
    metadata=temporary/'.autotune-work';metadata.mkdir()
    (metadata/'SNAPSHOT_ORIGIN.private.json').write_text(json.dumps(dict(schema=1,manifest_sha256=identity,source_root=str(root),same_uid_isolation_only=True,large_assets_copied=False))+'\n')
    # Atomic publication; partial snapshots remain evidence if copying fails.
    temporary.rename(target)
    return target


def main():
    root=Path(__file__).resolve().parents[3];args=sys.argv[1:]
    workspace=root/'.autotune-work'
    for i,arg in enumerate(args):
        if arg=='--workspace':workspace=Path(args[i+1]).expanduser().resolve()
        elif arg.startswith('--workspace='):workspace=Path(arg.split('=',1)[1]).expanduser().resolve()
    for i,arg in enumerate(args):
        if arg in ['--resume-campaign','--audit-campaign']or arg.startswith(('--resume-campaign=','--audit-campaign=')):
            audit=arg.startswith('--audit-campaign')
            campaign=Path(args[i+1]if '='not in arg else arg.split('=',1)[1]).expanduser().resolve()
            tune_file=campaign/'tuning/tuning.private.json';seal=json.loads((campaign/'tuning/seal.json').read_text())
            if sha(tune_file)!=seal['sha256']:raise ValueError('Resume tuning seal changed')
            tune=json.loads(tune_file.read_text());pinned=tune.get('source_snapshot')
            if pinned is None:
                if audit:continue
                raise ValueError('NEW_CAMPAIGN_REQUIRED: legacy managed-source/power contract preserved')
            original=Path(pinned['root']).resolve()
            if original.stat().st_uid!=os.getuid()or sha(original/'PUBLICATION_MANIFEST.json')!=pinned['manifest_sha256']:
                raise ValueError('RESUME_BLOCKED: original snapshot missing/changed')
            if original.parent.name!='source-snapshots':raise ValueError('RESUME_BLOCKED: not an isolated source snapshot')
            workspace=original.parent.parent;root=original
    snapshot=freeze(root,workspace)
    # Manual recovery and audit remain explicit operations, never auto-run.
    if not any(a=='--workspace'or a.startswith('--workspace=')for a in args):args.extend(['--workspace',str(workspace)])
    entry=snapshot/'tools/sched_ext/new_a_bootstrap/package_driver.py'
    os.execv(sys.executable,[sys.executable,'-I','-B',str(entry),*args])


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,KeyError,IndexError)as error:raise SystemExit('SNAPSHOT_BLOCKED: '+str(error))
