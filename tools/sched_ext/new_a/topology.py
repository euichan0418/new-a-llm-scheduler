# SPDX-License-Identifier: GPL-2.0
"""Translate the unchanged Base topology into NEW_A tiers once before attach."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'bench'))
import host_profile

MAX_CPUS = 1024
MAGIC = 0x4e455741


def fallback(reason):
    return dict(enabled=False, reason=reason, mode='fallback', tiers=[[], [], []])


def classify(topology, architectural_types=None):
    """Use architectural types 0x40/0x20, or proven homogeneous topology."""
    architectural_types = architectural_types or {}
    try:
        usable = topology['usable_cpus']
        if not usable or len(usable) != len(set(usable)) or any(type(c) is not int or not 0 <= c < MAX_CPUS for c in usable):
            return fallback('CPU set is empty, duplicated, or exceeds the supported bound')
        if not set(usable) <= set(topology['online_cpus']) & set(topology['allowed_cpus']):
            return fallback('usable CPUs disagree with online/allowed masks')
        cpus = {c: topology['cpus'][str(c)] for c in usable}
        groups = {}
        for cpu, info in cpus.items():
            if info['package'] < 0 or info['core'] < 0:
                return fallback('physical core identity unavailable')
            siblings = set(info['siblings_all'])
            if cpu not in siblings:
                return fallback('CPU missing from its sibling set')
            key = (info['package'], info['die'], info['core'])
            groups.setdefault(key, []).append(cpu)
        for group in groups.values():
            for cpu in group:
                if set(cpus[cpu]['siblings_all']) & set(usable) != set(group):
                    return fallback('core identity and sibling sets disagree')
        types = {}
        for cpu, info in cpus.items():
            raw = str(info.get('core_type_raw', '')).strip().lower()
            # Accept named types only; numeric sysfs enums are architecture-specific.
            named = {'core': 0x40, 'performance': 0x40, 'atom': 0x20, 'efficiency': 0x20}.get(raw)
            arch = architectural_types.get(cpu, 0)
            if named is not None and arch not in (0, named):
                return fallback('sysfs and CPUID types disagree')
            types[cpu] = named or arch
        known = set(types.values())
        hybrid = known <= {0x20, 0x40} and bool(known)
        if not hybrid:
            hetero = topology['heterogeneous']
            if known != {0} or hetero['multiple_core_types'] or hetero['frequency_hint']:
                return fallback('unknown or heterogeneous core type without architectural classification')
        tiers = [[], [], []]
        for group in sorted(groups.values(), key=min):
            group = sorted(group)
            if len({types[c] for c in group}) != 1:
                return fallback('siblings have inconsistent core types')
            if hybrid and types[group[0]] == 0x20:
                if len(cpus[group[0]]['siblings_all']) != 1:
                    return fallback('SMT efficiency cores are outside the original policy')
                tiers[1].extend(group)
            else:
                tiers[0].append(group[0])
                tiers[2].extend(group[1:])
        return dict(enabled=True, reason='Base physical topology plus ' + ('architectural core types' if hybrid else 'homogeneous topology'),
                    mode='hybrid' if hybrid else 'homogeneous', tiers=[sorted(t) for t in tiers])
    except (KeyError, TypeError, ValueError):
        return fallback('incomplete or inconsistent detector data')


def discover(allowed=None):
    try:
        if allowed is not None and not allowed:
            raise ValueError('empty allowed CPU set')
        topology = host_profile.discover_topology(allowed)
        cpus = topology['usable_cpus']
        if any(c >= MAX_CPUS for c in cpus):
            return dict(topology=topology, architectural_types={}, policy=fallback('CPU ID exceeds supported bound'))
        result = subprocess.run([str(HERE / 'build/bin/cpu_types'), *map(str, cpus)],
                                capture_output=True, text=True, timeout=30,
                                preexec_fn=(lambda: os.sched_setaffinity(0, allowed)) if allowed else None)
        if result.returncode:
            return dict(topology=topology, architectural_types={}, policy=fallback('CPUID collection failed: ' + result.stderr.strip()))
        types = dict(map(int, line.split()) for line in result.stdout.splitlines())
        if set(types) != set(cpus):
            return dict(topology=topology, architectural_types=types, policy=fallback('incomplete CPUID collection'))
        return dict(topology=topology, architectural_types=types, policy=classify(topology, types))
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        return dict(topology=None, architectural_types={}, policy=fallback(str(error)))


def encode(policy):
    tiers = policy['tiers']
    flat = [c for tier in tiers for c in tier]
    if len(tiers) != 3 or len(flat) != len(set(flat)) or any(not 0 <= c < MAX_CPUS for c in flat):
        raise ValueError('invalid policy CPU lists')
    return struct.pack('<' + 'I' * (5 + 3 * MAX_CPUS), MAGIC, int(policy['enabled']),
                       *map(len, tiers), *(c for tier in tiers for c in tier + [0] * (MAX_CPUS - len(tier))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New binary topology file; existing paths are refused')
    parser.add_argument('--cpus', help='Optional allowed CPU list, intersected by the Base detector')
    args = parser.parse_args()
    allowed = None
    if args.cpus is not None:
        from common import parse_cpus
        if not args.cpus.strip():
            parser.error('--cpus must not be empty')
        allowed = parse_cpus(args.cpus)
        if not allowed <= os.sched_getaffinity(0):
            parser.error('--cpus must be within current allowed CPUs')
    value = discover(allowed)
    if args.output:
        with args.output.open('xb') as output:
            output.write(encode(value['policy']))
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
