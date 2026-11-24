# SPDX-License-Identifier: GPL-2.0
"""Read-only, bounded discovery of hash-approved Frozen artifacts."""
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import time


def require(ok, message):
    if not ok:raise ValueError(message)


def digest(path):
    with Path(path).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()


def roots(package, home=None):
    home=Path.home()if home is None else Path(home)
    values=[home/'projects',home/'Downloads',Path(package),Path(package).parent,Path.cwd()]
    values.extend(p for p in sorted(home.iterdir())if p.is_dir()and not p.name.startswith('.')and re.search(r'new[-_]a|llama',p.name,re.I))
    result=[]
    for path in values:
        path=path.resolve()
        if not path.is_dir()or path==home or path==Path('/'):continue
        if any(path.is_relative_to(p)for p in result):continue
        result=[p for p in result if not p.is_relative_to(path)];result.append(path)
    return result


def gguf_identity(path):
    with Path(path).open('rb')as f:
        def read(n):
            b=f.read(n);require(len(b)==n,'Truncated GGUF header');return b
        def number(fmt):return struct.unpack(fmt,read(struct.calcsize(fmt)))[0]
        def string():
            n=number('<Q');require(n<=1048576,'GGUF header string too large');return read(n).decode('utf-8')
        require(read(4)==b'GGUF'and number('<I')in [2,3],'Unsupported GGUF header')
        number('<Q');count=number('<Q');require(count<=100000,'Invalid GGUF metadata count')
        result={}
        sizes={0:1,1:1,2:2,3:2,4:4,5:4,6:4,7:1,10:8,11:8,12:8}
        def skip(kind):
            if kind in sizes:read(sizes[kind])
            elif kind==8:string()
            elif kind==9:
                element=number('<I');n=number('<Q');require(element in sizes or element==8,'Nested/unknown GGUF array')
                require(n<=200000,'GGUF metadata array too large')
                if element in sizes:read(sizes[element]*n)
                else:
                    for _ in range(n):string()
            else:raise ValueError('Unknown GGUF metadata type')
        for _ in range(min(count,256)):
            key=string();kind=number('<I')
            if key in ['general.architecture','general.name']:
                require(kind==8,'GGUF identity must be a string');result[key]=string()
            else:skip(kind)
            if len(result)==2:return result
            require(f.tell()<16*1024*1024,'GGUF identity not found within bounded header')
    raise ValueError('GGUF identity metadata missing')


def discover(pins, search_roots, *, max_entries=400000, max_seconds=90):
    artifacts=pins['artifacts'];names={r['name']for r in artifacts}|{r['soname']for r in artifacts if r['soname']}
    expected={r['sha256']:r for r in artifacts};matches={k:[]for k in expected};models=[];ignored=[];seen={};count=0;begin=time.monotonic()
    skipped={'.git','.cache','.venv','venv','node_modules','__pycache__','CMakeFiles','new-a-autotune-results'}
    errors=[]
    def walk_error(error):errors.append(str(error))
    for root in search_roots:
        for folder,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
            dirs[:]=sorted(d for d in dirs if d not in skipped and not d.startswith('.'))
            for name in files:
                count+=1
                require(count<=max_entries and time.monotonic()-begin<max_seconds,'DISCOVERY_INCOMPLETE: bounded search exhausted; no automatic selection')
                if name not in names and not name.lower().endswith('.gguf')and name!='llama-server':continue
                path=Path(folder)/name
                try:
                    st=path.stat();key=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
                    if not path.is_file():continue
                    model=name.lower().endswith('.gguf')
                    sizes={pins['model']['size']}if model else {r['size']for r in artifacts}
                    if st.st_size not in sizes:ignored.append(dict(path=str(path),reason='SIZE_NOT_APPROVED'));continue
                    if key not in seen:seen[key]=digest(path)
                    sha=seen[key];after=path.stat()
                    require(key==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'Asset changed during hashing')
                    if model and sha==pins['model']['sha256']:
                        ident=gguf_identity(path);require(ident=={'general.architecture':pins['model']['architecture'],'general.name':pins['model']['name']},'MODEL_IDENTITY_MISMATCH');models.append(str(path.resolve()))
                    elif not model and sha in expected:
                        with path.open('rb')as f:require(f.read(6)==b'\x7fELF\x02\x01','Approved artifact ELF header changed')
                        matches[sha].append(str(path.resolve()))
                    else:ignored.append(dict(path=str(path),reason='HASH_NOT_APPROVED'))
                except OSError as error:errors.append(str(error))
    require(not errors,'DISCOVERY_INCOMPLETE: inaccessible search entries: '+str(errors[:5]))
    missing=[r['name']for r in artifacts if not matches[r['sha256']]]
    if missing:
        reason=pins['reproducible_frozen_build']['reason']
        raise ValueError('FROZEN_ASSETS_MISSING: '+', '.join(missing)+'; verified Frozen rebuild unavailable: '+reason+'; no arbitrary server will be built')
    require(models,'MODEL_NOT_FOUND: approved '+pins['model']['name']+' SHA256='+pins['model']['sha256']+'; no approved download URL; no substitute/download')
    chosen=[]
    for row in artifacts:
        copies=sorted(set(matches[row['sha256']]),key=lambda v:(len(v),v))
        chosen.append(dict(row,path=copies[0],equivalent_copies=copies))
    # Every accepted profile is hash-pinned. Future multi-profile additions must refuse ambiguity.
    require(len([r for r in chosen if r['role']=='server'])==1,'AMBIGUOUS_APPROVED_SERVER_PROFILE')
    require(len([r for r in chosen if r['phase_marker']])==1,'AMBIGUOUS_PHASE_MARKER_PROFILE')
    return dict(artifacts=chosen,model=sorted(set(models),key=lambda v:(len(v),v))[0],equivalent_models=sorted(set(models)),ignored=ignored,
                searched_roots=list(map(str,search_roots)),scanned_entries=count,profile=pins['profile'])


def stage_assets(selection, pins, directory):
    directory=Path(directory);directory.mkdir(mode=0o700)
    for row in selection['artifacts']:
        require(digest(row['path'])==row['sha256'],'Frozen changed after discovery')
        if row['role']=='library':
            for name in {row['name'],row['soname']}:
                target=directory/name
                if target.exists():require(target.resolve()==Path(row['path']),'Library alias collision')
                else:target.symlink_to(row['path'])
    require(digest(selection['model'])==pins['model']['sha256'],'Model changed after discovery')
    return directory


def validate_elf(selection, library_dir, contracts):
    server=next(r['path']for r in selection['artifacts']if r['role']=='server')
    marker=next(r['path']for r in selection['artifacts']if r['phase_marker'])
    env=dict(PATH='/usr/bin:/bin',LC_ALL='C',LD_LIBRARY_PATH=str(library_dir))
    approved={r['name']:r['sha256']for r in selection['artifacts']}
    resolutions={};loader=contracts.interpreter(server)
    for row in selection['artifacts']:
        files=contracts.resolve(row['path'],loader,env);resolutions[row['path']]=files
        for file in files:
            name=Path(file).name
            if name.startswith(('libllama','libggml','libmtmd')):require(name in approved and digest(file)==approved[name],'Frozen dependency resolution selected an unapproved library: '+file)
    symbols=subprocess.check_output(['nm','-D','--defined-only',marker],text=True,timeout=15)
    require(re.search(r'\bllama_scx_phase_v1$',symbols,re.M),'Approved phase marker symbol missing')
    blobs=b''.join(Path(r['path']).read_bytes()for r in selection['artifacts'])
    for option in ['server-compute-profile-mode','threads-batch','threads-http']:require(option.encode()in blobs,'Approved Frozen CLI contract missing: '+option)
    return dict(server=server,marker=marker,resolutions=resolutions,phase_symbol_verified=True,cli_options_identity_and_strings_verified=True,
                actual_cli_execution='NOT_RUN',actual_thread_response_validation='DEFERRED_TO_FUNCTIONAL_AUDIT')
