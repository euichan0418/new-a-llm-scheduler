#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Relocatable source package admission; no sudo, attach, or inference in checks."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from types import SimpleNamespace
sys.dont_write_bytecode = True
if sys.version_info < (3,11):
    raise SystemExit('BLOCKED: Python 3.11 or newer required')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNTIME = ROOT
RESERVE = 512 * 1024 * 1024
TOOLS = ['cc', 'clang', 'bpftool', 'readelf', 'nm', 'powerprofilesctl']
FIXED = ['/usr/bin/sudo', '/usr/bin/taskset', '/usr/bin/timeout']


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def contained(path, root=ROOT):
    return Path(path).resolve().is_relative_to(root.resolve()) and not Path(path).resolve().is_relative_to(root.resolve()/'.autotune-work')


def verify(root=ROOT):
    root = Path(root).resolve()
    if (root/'PUBLICATION_MANIFEST.json').is_file():
        rows=json.loads((root/'PUBLICATION_MANIFEST.json').read_text())['files']
        for row in rows:
            f=root/row['path']
            require(not f.is_symlink() and f.is_file() and f.stat().st_size==row['size'] and sha(f)==row['sha256'],'Public source integrity mismatch: '+row['path'])
        return dict(status='PACKAGE_INTEGRITY_PASS',files=len(rows),manifest_sha256=sha(root/'PUBLICATION_MANIFEST.json'),signature=False)
    rows = json.loads((root / 'PACKAGE_MANIFEST.json').read_text())['files']
    expected = {'PACKAGE_MANIFEST.json', 'SHA256SUMS'}
    require(rows, 'Empty package manifest')
    for row in rows:
        name = row['path']; rel = PurePosixPath(name)
        require(name == rel.as_posix() and not rel.is_absolute() and '..' not in rel.parts and name not in expected, 'Unsafe/duplicate manifest path')
        expected.add(name)
        f = root / name
        require(not any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)), 'Package symlink rejected: ' + name)
        require(f.is_file() and f.stat().st_size == row['size'] and sha(f) == row['sha256'], 'Package source integrity mismatch: ' + name)
    actual = {f.relative_to(root).as_posix() for f in root.rglob('*') if f.is_file() or f.is_symlink()}
    require(actual == expected, 'Unexpected/missing package files; keep outputs outside this directory')
    sums = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        require(re.fullmatch('[0-9a-f]{64}', digest) and name not in sums, 'Invalid SHA256SUMS')
        sums[name] = digest
    require(set(sums) == expected - {'SHA256SUMS'}, 'SHA256SUMS file set mismatch')
    require(all(sha(root / name) == digest for name, digest in sums.items()), 'SHA256SUMS mismatch')
    return dict(status='PACKAGE_INTEGRITY_PASS',files=len(rows),manifest_sha256=sha(root/'PACKAGE_MANIFEST.json'),signature=False)


def engine():
    folder = RUNTIME / 'tools/sched_ext/new_a_hybrid_autotune'
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location('delivered_autotune', folder / 'autotune.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def nearest(path):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    return path


def check_inputs(args):
    if getattr(args, 'server', None):
        server = Path(args.server)
        require(server.is_file() and os.access(server, os.X_OK), 'Explicit executable Frozen server is missing')
        with server.open('rb') as f:
            require(f.read(6) == b'\x7fELF\x02\x01', 'Frozen server must be ELF64 little-endian; no replacement is provided')
    if getattr(args, 'model', None):
        model = Path(args.model)
        require(model.is_file(), 'Explicit GGUF model is missing')
        with model.open('rb') as f:
            require(f.read(4) == b'GGUF', 'Model must have GGUF magic; no download or replacement')
    for folder in getattr(args, 'library_dir', []):
        require(Path(folder).is_dir(), 'Explicit library directory is missing: ' + folder)
    for file in [*getattr(args, 'elf_root', []), *([args.marker_elf] if getattr(args, 'marker_elf', None) else [])]:
        require(Path(file).is_file(), 'Explicit ELF/marker input is missing: ' + file)


def dependencies(args, full=True, module=None):
    require(sys.version_info >= (3,11), 'Python 3.11 or newer required')
    require(sys.platform == 'linux' and platform.machine() in ['x86_64','amd64'], 'Linux x86_64 required')
    require(os.getuid() != 0 and os.sched_getscheduler(0) == 0, 'Run as a normal SCHED_OTHER user; do not sudo this launcher')
    free = shutil.disk_usage(nearest(args.output if hasattr(args,'output') else ROOT.parent)).free
    require(free >= RESERVE, 'DISK_SPACE_BLOCKED: at least 512MiB free required; no data will be deleted')
    names = TOOLS if full else ['cc']
    missing = [name for name in names if not shutil.which(name)]
    if full:
        missing += [name for name in FIXED if not (Path(name).is_file() and os.access(name,os.X_OK))]
    require(not missing, 'Missing dependencies (install manually): ' + ', '.join(missing))
    check_inputs(args)
    result = dict(status='DEPENDENCIES_PASS',free_bytes=free,reserve_bytes=RESERVE,package_static_only=True,sudo='NOT_RUN',attach='NOT_RUN',inference='NOT_RUN')
    if not full:
        return result
    btf = Path('/sys/kernel/btf/vmlinux')
    require(btf.is_file() and os.access(btf,os.R_OK), 'Readable current kernel BTF required')
    header = Path('/lib/modules')/platform.release()/'build/include/uapi/linux/sched.h'
    require(header.is_file() and re.search(r'^#define\s+SCHED_EXT\s+7\s*$',header.read_text(),re.M), 'Matching kernel headers with SCHED_EXT=7 required')
    require(Path('/sys/kernel/sched_ext/state').read_text().strip() == 'disabled', 'sched_ext must be disabled; will not detach another scheduler')
    require(Path('/proc/sys/kernel/bpf_stats_enabled').read_text().strip() == '0', 'Runtime Statistics must be OFF; not changing it')
    # Temporary compile/link probes only; no generated binary is executed.
    with tempfile.TemporaryDirectory(prefix='new-a-autotune-deps-') as tmp:
        probes = [(['cc','-x','c','-','-o',str(Path(tmp)/'link-test'),'-lbpf','-lelf','-lz'], '#include <bpf/libbpf.h>\n#include <bpf/bpf.h>\n#include <libelf.h>\n#include <zlib.h>\nint main(void){return 0;}\n'),
                  (['clang','-target','bpf','-O2','-x','c','-c','-','-o',str(Path(tmp)/'test.o')], 'int test(void){return 0;}\n')]
        for command, source in probes:
            done = subprocess.run(command,input=source,text=True,capture_output=True,timeout=30)
            require(done.returncode == 0, 'Compile dependency probe failed: '+command[0]+'\n'+done.stderr[-3000:])
    module = module or engine()
    conditions = module.b.contracts.power()  # AC/Turbo/governors/profile/thermal, read-only.
    result.update(kernel=platform.release(),power_conditions_verified=True,temperature_c=max(conditions['temperatures_c']),btf_readable=True,
                  actual_kfunc_signatures='DEFERRED_TO_FULL_STATIC_BUILD',actual_verifier='NOT_RUN')
    return result


def action(mode, argv, module=None):
    module = module or engine()
    if mode == 'functional':
        parser = argparse.ArgumentParser(description='Explicit existing FULL functional validation')
        parser.add_argument('--campaign',type=Path,required=True)
        parser.add_argument('--execute',action='store_true')
        parser.add_argument('--acknowledge-existing-safety-fail',action='store_true')
        parser.add_argument('--resume-auth',action='store_true')
        args = parser.parse_args(argv)
        require(not contained(args.campaign), 'Keep private campaigns outside the delivered source package')
        args.output = args.campaign
        return args, [sys.executable,'-B',str(RUNTIME/'tools/sched_ext/new_a_universal_runtime/runtime.py'),'functional',*argv]
    cmd = {'run':'run','check':'run','plan':'plan','analyze':'analyze','prepare-new-a':'prepare-functional'}[mode]
    if mode == 'analyze':
        require('--output' in argv or any(v.startswith('--output=')for v in argv), 'Analysis requires the existing --output directory')
    elif '--output' not in argv and not any(v.startswith('--output=')for v in argv):
        output = ROOT/'.autotune-work'/('run-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
        argv = [*argv,'--output',str(output)]
    args = module.parser().parse_args([cmd,*argv])
    require(not contained(args.output), 'Keep all results/builds/private profiles outside the package; use --output ../results/NEW_RUN')
    if mode == 'check':
        require(not args.execute and not args.resume and not args.resume_auth, '--check never executes or resumes sessions')
    return args, [sys.executable,'-B',str(RUNTIME/'tools/sched_ext/new_a_hybrid_autotune/autotune.py'),cmd,*argv]


def admission_record(args, command, integrity, preflight):
    if not (getattr(args,'execute',False) or getattr(args,'command',None)=='prepare-functional'):
        return
    root = Path(args.output).resolve();root.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    name = root.name+'.package-admission-'+uuid.uuid4().hex[:10]+'.private.json'
    record = dict(package=integrity,preflight=preflight,command=command,raw_output=str(root),automatic_retry=False,keepalive=False,
                  strict_acceptance='FAILED',safety_verdict='FAIL',overhead_review='OVERHEAD_REVIEW_REQUIRED')
    fd = os.open(root.parent/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(record,f,ensure_ascii=False,indent=2);f.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,allow_abbrev=False,epilog='Default: run, --execute and --acknowledge-existing-safety-fail explicitly required for inference. Frozen/model/library paths are forwarded. Run --check before experiments. See README.ko.md.')
    group = parser.add_mutually_exclusive_group()
    for name in ['check','plan','analyze','prepare-new-a','functional','self-test','verify-only','auto','auto-check']:
        group.add_argument('--'+name,action='store_true')
    group.add_argument('--restore-environment',type=Path)
    group.add_argument('--prepare-only',action='store_true')
    group.add_argument('--check-bootstrap',action='store_true')
    parser.add_argument('--workspace',type=Path)
    parsed,rest = parser.parse_known_args(argv)
    mode = next((name for name in ['check','plan','analyze','prepare-new-a','functional','self-test','verify-only','auto','auto-check']if getattr(parsed,name.replace('-','_'))), 'run')
    integrity = verify();print(json.dumps(integrity),flush=True)
    if parsed.restore_environment is not None or mode in ['auto','auto-check']or parsed.prepare_only or parsed.check_bootstrap:
        require(not rest, '--auto/--auto-check use automatic paths and fixed Linux policy; extra engine arguments are not accepted')
        sys.path.insert(0,str(HERE))
        import auto_run
        driver=sys.modules.get(__name__)or SimpleNamespace(**globals())
        if parsed.restore_environment is not None:return auto_run.restore_only(driver,parsed.restore_environment)
        import bootstrap
        return bootstrap.entry(driver,workspace=parsed.workspace,check=(mode=='auto-check'or parsed.check_bootstrap),prepare_only=parsed.prepare_only)
    if mode == 'verify-only':
        require(not rest, 'Unexpected arguments for --verify-only');return 0
    if mode == 'self-test':
        require(not rest, 'Unexpected arguments for --self-test')
        require(shutil.which('cc') and shutil.which('make'), 'C compiler and make needed for nonprivileged regression tests')
        env = dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        commands = [['make','test'],['make','check']]
        for cmd in commands:
            code = subprocess.run(cmd,cwd=RUNTIME,env=env).returncode
            require(code == 0, 'Nonprivileged self-test failed: '+str(cmd))
        return 0
    module = engine();args,command = action(mode,rest,module)
    print('RESULT_DIRECTORY='+str(Path(args.output).resolve()),flush=True)
    if mode == 'analyze':
        require(Path(args.output).is_dir(), 'Existing results directory required for analysis')
        os.execv(command[0],command)
    full = mode in ['check','prepare-new-a','functional'] or (mode=='run' and args.execute)
    checks = dependencies(args,full,module);print(json.dumps(checks),flush=True)
    if mode == 'check':
        print('CHECK_ONLY; no sudo authentication, BPF attach, server start, or Host Profile created',flush=True);return 0
    admission_record(args,command,integrity,checks)
    os.execv(command[0],command)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        print('BLOCKED: '+type(error).__name__+': '+str(error)+'; no automatic install/download/retry',file=sys.stderr)
        raise SystemExit(2)
