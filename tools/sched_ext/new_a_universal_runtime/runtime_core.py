# SPDX-License-Identifier: GPL-2.0
"""Shared bounds, immutable files and exact Hybrid detector imports."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'new_a_hybrid'))
import collect
import profiles
MAX_CPUS = collect.portable.MAX_CPUS
LABELS = dict(strict_acceptance='FAILED', safety_verdict='FAIL', overhead_review='OVERHEAD_REVIEW_REQUIRED')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    Path(path).chmod(0o600)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return collect.portable.fingerprint(value)


def space(path):
    require(shutil.disk_usage(path).free >= 512 * 1024 * 1024, 'DISK_SPACE_BLOCKED: 512MiB reserve required; no evidence deleted')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def domain(detection, config, allow_shared=False):
    profiles.validate(config, detection)
    snap = detection['portable_plan']['snapshot']
    possible, online, allowed, selected = (snap['possible_cpus'], snap['online_cpus'], snap['process_allowed_cpus'], config['affinity_cpus'])
    require(possible == sorted(set(possible)) and possible and max(possible) < MAX_CPUS, 'Possible CPU IDs must be below 1024')
    require(set(selected) <= set(online) & set(allowed) <= set(possible), 'Selected/online/allowed domain inconsistent')
    housekeeping = sorted((set(online) & set(allowed)) - set(selected))
    shared=not housekeeping
    require(housekeeping or allow_shared,'No separate housekeeping CPU; explicit --ack-shared-housekeeping required, no silent affinity changes')
    if shared:housekeeping=[selected[-1]]
    return dict(schema=1, possible=possible, online=online, allowed=allowed, selected=selected,
                mask_bits=max(possible)+1, per_cpu_slots=len(possible), housekeeping=[housekeeping[-1]],housekeeping_shared=shared,
                tiers=detection['portable_plan']['policy']['tiers'],
                tier_meaning=['P_REPRESENTATIVES','E_CORES','P_SMT'] if config['architecture']=='INTEL_HYBRID' else ['PHYSICAL_REPRESENTATIVES','EMPTY','SMT'],
                topology_fingerprint=detection['fingerprint'])


def bitmap(cpus):
    require(all(type(c) is int and 0 <= c < MAX_CPUS for c in cpus) and len(cpus)==len(set(cpus)), 'Invalid CPU bitmap')
    return bytes(int(c in cpus) for c in range(MAX_CPUS))


def ticks(pid):
    raw = Path(f'/proc/{pid}/stat').read_text()
    return int(raw[raw.rfind(')')+2:].split()[19])


def guard_bytes(value, pid=None, uid=None, start=None):
    pid = os.getpid() if pid is None else pid
    uid = os.getuid() if uid is None else uid
    start = ticks(pid) if start is None else start
    require(pid > 1 and uid > 0 and start > 0, 'Normal controller identity required')
    return struct.pack('<6IQ', 0x4e415547, 1, value['mask_bits'], value['per_cpu_slots'], pid, uid, start) + b''.join(bitmap(value[k]) for k in ['possible','online','allowed','selected'])
