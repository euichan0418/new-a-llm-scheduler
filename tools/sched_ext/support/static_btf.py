# SPDX-License-Identifier: GPL-2.0
"""Static kfunc signature comparison; does not load any BPF program."""
import re
import subprocess


def table(path):
    raw = subprocess.check_output(['bpftool', 'btf', 'dump', 'file', str(path), 'format', 'raw'], text=True)
    return {int(m[1]): m[2] for m in re.finditer(r'^\[(\d+)\] (.*?)(?=^\[|\Z)', raw, re.M | re.S)}


def signature(types, number):
    if number == 0:
        return 'void'
    entry = types[number]
    kind = entry.split()[0]
    if kind in ('TYPEDEF', 'CONST', 'VOLATILE', 'RESTRICT', 'TYPE_TAG', 'PTR'):
        value = signature(types, int(re.search(r'\btype_id=(\d+)', entry)[1]))
        return ['pointer', value] if kind == 'PTR' else value
    if kind == 'INT':
        return [kind, *re.search(r'size=(\d+).*nr_bits=(\d+) encoding=(\S+)', entry).groups()]
    if kind in ('STRUCT', 'UNION', 'FWD'):
        return [kind, re.search(r"'([^']*)'", entry)[1]]
    raise ValueError('Unsupported BTF type: ' + entry)


def function(types, name):
    entries = [v for v in types.values() if v.startswith("FUNC '" + name + "'")]
    if len(entries) != 1:
        raise ValueError('Missing or ambiguous kfunc: ' + name)
    proto = types[int(re.search(r'\btype_id=(\d+)', entries[0])[1])]
    return dict(result=signature(types, int(re.search(r'ret_type_id=(\d+)', proto)[1])),
                args=[signature(types, int(v)) for v in re.findall(r"^\s+'[^']*' type_id=(\d+)", proto, re.M)])


def inspect(objects, btf):
    kernel = table(btf)
    report = {}
    for policy, path in objects.items():
        types = table(path)
        names = [re.search(r"^FUNC '([^']+)'", v)[1] for v in types.values() if v.startswith('FUNC ') and 'linkage=extern' in v]
        if not names:
            raise ValueError('No external kfuncs: ' + policy)
        report[policy] = {n: dict(object=function(types, n), kernel=function(kernel, n)) for n in sorted(names)}
        if any(v['object'] != v['kernel'] for v in report[policy].values()):
            raise ValueError('kfunc signature mismatch: ' + policy)
    return dict(status='PASS', objects=report, scope='Static signatures only; new verifier/attach validation NOT_RUN')
