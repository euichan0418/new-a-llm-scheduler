# SPDX-License-Identifier: GPL-2.0
"""Pinned source/build/model acquisition; no inference in provisioning."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import auto_assets as assets
import auto_system as system
require=assets.require
sha=assets.digest


def command(argv,log,*,cwd=None,env=None,timeout=1800):
    with Path(log).open('a')as f:
        f.write(json.dumps(list(map(str,argv)))+'\n');f.flush()
        done=subprocess.run(list(map(str,argv)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    require(done.returncode==0,'PREPARATION_COMMAND_FAILED: '+str(log))


def cpu_binding():
    text=Path('/proc/cpuinfo').read_text()
    vendors=set(re.findall(r'^vendor_id\s*:\s*(\S+)',text,re.M));require(vendors=={'GenuineIntel'},'Intel CPU required for this Auto-Tuning profile')
    return hashlib.sha256('\n'.join(sorted(line for line in text.splitlines()if line.startswith(('model name','flags','cpu family','model\t','stepping')))).encode()).hexdigest()


def source(root,recipe):
    root=Path(root);config=recipe['engine'];commit=config['commit']
    if root.exists():
        require((root/'.git').is_dir(),'Source directory exists without expected Git provenance; no overwrite')
        require(subprocess.check_output(['git','remote','get-url','origin'],cwd=root,text=True).strip()==config['repository'],'Source origin differs')
        require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()==commit,'Source revision differs; no reset')
        require(not subprocess.check_output(['git','status','--porcelain','--untracked-files=all'],cwd=root,text=True).strip(),'Source worktree modified')
    else:
        root.mkdir(parents=True)
        command(['git','init','-q'],root.parent/'source-acquire.log',cwd=root)
        command(['git','remote','add','origin',config['repository']],root.parent/'source-acquire.log',cwd=root)
        command(['git','fetch','--depth','1','--filter=blob:none','origin',commit],root.parent/'source-acquire.log',cwd=root,timeout=600)
        command(['git','sparse-checkout','init','--no-cone'],root.parent/'source-acquire.log',cwd=root)
        (root/'.git/info/sparse-checkout').write_text('/*\n'+''.join('!/'+name+'/\n'for name in config['source_sparse_excludes']))
        command(['git','-c','advice.detachedHead=false','checkout','--detach',commit],root.parent/'source-acquire.log',cwd=root,timeout=600)
    require(subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=root,text=True).strip()==config['tree'],'Pinned source tree differs')
    require(not config['patches'],'Unimplemented source patch contract; do not silently skip patches')
    return root


def build_identity(source,recipe):
    return dict(commit=recipe['engine']['commit'],tree=recipe['engine']['tree'],recipe_sha256=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest(),cpu_binding=cpu_binding(),options=recipe['engine']['cmake_options'],
                compiler_sha256={name:sha(Path(shutil.which(name)).resolve())for name in ['cc','c++','cmake']},cc=subprocess.check_output(['/usr/bin/cc','--version'],text=True).splitlines()[0],cxx=subprocess.check_output(['/usr/bin/c++','--version'],text=True).splitlines()[0],cmake=subprocess.check_output(['cmake','--version'],text=True).splitlines()[0],historical_byte_identity=False)


def build(source_dir,root,recipe,contracts):
    root=Path(root);expected=build_identity(source_dir,recipe);proof=root/'reproduction.private.json'
    if root.exists():
        require(proof.is_file(),'Incomplete engine build preserved; no automatic retry/overwrite')
        old=json.loads(proof.read_text());require(old['input']==expected,'Build input/CPU/compiler changed; use a new workspace')
        for path,digest in old['outputs'].items():require(sha(root/path)==digest,'Rebuilt engine artifact changed')
        validate(root/'bin',contracts,recipe,run_cli=False)
        return root/'bin'
    root.mkdir(parents=True)
    env=dict(PATH='/usr/bin:/bin',LC_ALL='C',HOME=str(Path.home()),CCACHE_DISABLE='1')
    command(['cmake','-S',source_dir,'-B',root,*expected['options']],root/'configure.log',env=env)
    require(shutil.disk_usage(root).free>=512*1024*1024,'DISK_SPACE_BLOCKED before serial engine build')
    command(['cmake','--build',root,'--target','llama-server','--parallel','1'],root/'compile.log',env=env,timeout=3600)
    identity=validate(root/'bin',contracts,recipe,run_cli=True)
    outputs={str(p.relative_to(root)):sha(p)for p in (root/'bin').iterdir()if p.is_file()}
    outputs.update({name:sha(root/name)for name in ['CMakeCache.txt','compile_commands.json']})
    system.save(proof,dict(input=expected,outputs=outputs,validation=identity,functional='NOT_RUN',model_loading='DEFERRED_TO_LINUX_FUNCTIONAL_AUDIT',historical_byte_identity=False))
    return root/'bin'


def validate(directory,contracts,recipe,*,run_cli=True):
    directory=Path(directory);server=directory/'llama-server';marker=directory/'libllama-server-impl.so'
    require(server.is_file()and os.access(server,os.X_OK)and marker.is_file(),'Rebuilt server/phase library missing')
    loader=contracts.interpreter(server);env=dict(PATH='/usr/bin:/bin',LC_ALL='C',LD_LIBRARY_PATH=str(directory))
    resolutions=contracts.resolve(server,loader,env)
    names={Path(p).name for p in resolutions}
    for prefix in ['libggml-base','libggml-cpu','libggml.','libllama-common','libllama-server-impl','libllama.','libmtmd','libgomp']:
        require(any(n.startswith(prefix)for n in names),'Missing runtime dependency '+prefix)
    for path in resolutions:
        if Path(path).name.startswith(('libggml','libllama','libmtmd')):require(Path(path).resolve().is_relative_to(directory.resolve()),'Cross-build engine library selected')
    symbols=subprocess.check_output(['nm','-D','--defined-only',marker],text=True,timeout=15)
    require(re.search(r'\bllama_scx_phase_v1$',symbols,re.M),'Rebuilt phase marker missing')
    if run_cli:
        version=subprocess.check_output([server,'--version'],env=env,text=True,stderr=subprocess.STDOUT,timeout=30)
        require(recipe['engine']['commit'][:7]in version,'Server revision marker differs')
        helptext=subprocess.check_output([server,'--help'],env=env,text=True,stderr=subprocess.STDOUT,timeout=30)
        for option in ['--threads','--threads-batch','--threads-http','--server-compute-profile-mode']:require(option in helptext,'Required CLI option absent: '+option)
    return dict(status='ELF_PHASE_CLI_VALIDATED'if run_cli else'ELF_PHASE_REVALIDATED',project_libraries='ALL_PRESENT',openmp='LIBGOMP_LINKED;_TEAM_COUNTS_REQUIRE_FUNCTIONAL_AUDIT',model_loading='NOT_RUN')


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        u=urllib.parse.urlparse(newurl)
        require(u.scheme=='https'and any(u.hostname==d or (u.hostname or'').endswith('.'+d)for d in ['huggingface.co','hf.co','xethub.hf.co']),'Unapproved model download redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def model(root,recipe,*,opener=None):
    root=Path(root);config=recipe['model'];file=root/'model.gguf'
    if file.exists():
        require(file.stat().st_size==config['size']and sha(file)==config['sha256'],'Cached model differs; no overwrite/substitute')
        require(assets.gguf_identity(file)=={'general.architecture':config['architecture'],'general.name':config['name']},'Cached GGUF identity differs');return file
    root.mkdir(parents=True,exist_ok=True)
    require(shutil.disk_usage(root).free>=config['size']+512*1024*1024,'MODEL_DOWNLOAD_DISK_BLOCKED: model size plus 512MiB reserve required')
    require(config['url'].startswith('https://huggingface.co/')and config['revision']in config['url'],'Pinned HTTPS model URL required')
    require(not list(root.glob('*.partial-*')),'Partial model download preserved; review before a new transfer')
    part=root/('model.gguf.partial-'+uuid.uuid4().hex[:8]);digest=hashlib.sha256();count=0
    opener=opener or urllib.request.build_opener(Redirect())
    with opener.open(config['url'],timeout=60)as response,part.open('xb')as output:
        length=response.headers.get('Content-Length')
        require(length is None or int(length)==config['size'],'Model content length mismatch')
        while True:
            data=response.read(1024*1024)
            if not data:break
            count+=len(data);require(count<=config['size'],'Model transfer larger than pinned file');output.write(data);digest.update(data)
            require(shutil.disk_usage(root).free>=512*1024*1024,'DISK_SPACE_BLOCKED during model transfer')
    require(count==config['size']and digest.hexdigest()==config['sha256'],'MODEL_DOWNLOAD_INTEGRITY_FAILED: partial retained')
    require(assets.gguf_identity(part)=={'general.architecture':config['architecture'],'general.name':config['name']},'Downloaded GGUF identity differs')
    os.rename(part,file);system.save(root/'download.private.json',dict(url=config['url'],sha256=config['sha256'],bytes=count,license=config['license'],verified=True))
    return file
