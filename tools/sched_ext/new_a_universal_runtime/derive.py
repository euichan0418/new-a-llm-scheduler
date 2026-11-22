# SPDX-License-Identifier: GPL-2.0
"""Independent lifecycle/monitor binding; unchanged E FULL BPF source."""
import shutil
import sys
from pathlib import Path
from runtime_core import HERE, ROOT, module, require


def replace_once(text, old, new):
    require(text.count(old)==1, 'Reference source drift: '+old[:65])
    return text.replace(old,new)


def generate(out):
    sys.path.insert(0,str(HERE.parent/'support'))
    helper=module('universal_upstream_prepare',HERE.parent/'support/prepare_build.py')
    helper.generate_loader(out)
    path=out/'monitor.h';s=path.read_text()
    a=s.index('    return cpu == 0');b=s.index('\n}',a)
    s=s[:a]+'    return cpu >= 0 && cpu < NEW_A_MAX_CPUS && ug.selected[cpu];'+s[b:]
    s=s.replace('if (analysis_count != 16)', 'if (analysis_count <= 0 || analysis_count > NEW_A_MAX_CPUS || (unsigned int)analysis_count != ug.count)')
    s=s.replace('if (!analysis_cpu_allowed(cpu)', 'if (!analysis_cpu_allowed(analysis_possible_ids[cpu])')
    s=s.replace('    if (analysis_failures) return -EUCLEAN;', '    if (analysis_failures) return -EUCLEAN;\n    if (ug_current()) return analysis_fail("online/allowed/controller identity changed");')
    path.write_text(s)
    path=out/'loader.c';s=path.read_text()
    s=s.replace('#include "new_a.h"','#include "new_a.h"\n#include "identity.h"\n#include "guard_domain.h"')
    s=replace_once(s,'\terror = bpf_object__load(object);','\tif (ug_current()) return -EINVAL;\n\terror = bpf_object__load(object);')
    s=replace_once(s,'\tfprintf(stderr, "NEW_A: enabled=', '\tif (universal_attest(object)) return -EINVAL;\n\tfprintf(stderr, "NEW_A: enabled=')
    s=s.replace('seen[cpu] || !topology.enabled','seen[cpu] || !topology.enabled || !ug.online[cpu] || !ug.allowed[cpu] || (tier == 2 && ug.selected[cpu])')
    s=replace_once(s,'\terror = bpf_object__load(object);','\tif (!topology.enabled || !topology.count[0]) return -EINVAL;\n\tfor (int cpu=0;cpu<NEW_A_MAX_CPUS;cpu++) if (ug.selected[cpu] && !seen[cpu]) return -EINVAL;\n\terror = bpf_object__load(object);')
    s=s.replace('!map || (diagnostics_fd', '!map || bpf_map__value_size(map) != sizeof(struct new_a_stats) || bpf_map__type(map) != BPF_MAP_TYPE_PERCPU_ARRAY || bpf_map__max_entries(map) != 1 || (diagnostics_fd')
    s=s.replace('!map || (events_fd', '!map || bpf_map__value_size(map) != sizeof(struct new_a_event) || bpf_map__type(map) != BPF_MAP_TYPE_PERCPU_ARRAY || bpf_map__max_entries(map) != NEW_A_EVENT_CAPACITY || (events_fd')
    s=replace_once(s,'\ttopology_path = argv[2];','\tif (argc < 8 || strcmp(argv[1], "--guard-config") || ug_load(argv[2])) return EXIT_FAILURE;\n\tchar *program_name = argv[0]; argc -= 2; argv += 2; argv[0] = program_name;\n\ttopology_path = argv[2];')
    s=replace_once(s,'if (argc < 4 || strcmp(argv[1], "--topology"))','if (argc < 8 || strcmp(argv[1], "--guard-config") || strcmp(argv[3], "--topology"))')
    s=replace_once(s,'cpu ? "," : "", cpu, (unsigned long long)s->selected[0]', 'cpu ? "," : "", analysis_possible_ids[cpu], (unsigned long long)s->selected[0]')
    s=s.replace('\tanalysis_monitor_close();\n\treturn result;', '\tanalysis_monitor_close();\n\tfree(universal_object_bytes);\n\treturn result;')
    path.write_text(s)
    path=out/'neutral_lifecycle.c';s=path.read_text().replace('bpf_object__open_file(BPF_OBJECT_PATH, NULL)','universal_open(BPF_OBJECT_PATH)')
    s=replace_once(s,'if (!uid || !socket_name', 'if (uid != ug.uid || !uid || !socket_name')
    path.write_text(s)
    for name in ['guard_domain.h','identity.h']:shutil.copyfile(HERE/name,out/name)
    return dict(policy_change=False,source='UNCHANGED_E_FULL',guard='DYNAMIC_SELECTED_MASK_AND_ONLINE_ALLOWED_IDENTITY',identity='HASH_VERIFIED_OPEN_MEM_AND_LOADED_PROGRAM_IDS',per_cpu_slots='ASCENDING_POSSIBLE_CPU_IDS_NOT_DENSE_ID_ASSUMPTION')
