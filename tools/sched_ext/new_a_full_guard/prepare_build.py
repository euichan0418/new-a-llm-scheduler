# SPDX-License-Identifier: GPL-2.0
"""기존 연구 도구에서 필요한 지원 함수만 보존한 공개 사본이다."""
from pathlib import Path
import struct
import shutil
import build_static as old_build
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]


def elf_relocations(path):
    data=Path(path).read_bytes();header=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
    offset,size,count,strings_index=header[6],header[11],header[12],header[13]
    sections=[struct.unpack_from('<IIQQQQIIQQ',data,offset+i*size) for i in range(count)]
    strings=sections[strings_index];names=data[strings[4]:strings[4]+strings[5]]
    name=lambda s:names[s[0]:].split(b'\0',1)[0].decode()
    result={}
    for s in sections:
        if s[1] not in [4,9]:continue
        symbols=sections[s[6]];symstrings=sections[symbols[6]];table=data[symstrings[4]:symstrings[4]+symstrings[5]]
        rows=[]
        for pos in range(s[4],s[4]+s[5],s[9]):
            place,info=struct.unpack_from('<QQ',data,pos);index=info>>32
            sym=struct.unpack_from('<IBBHQQ',data,symbols[4]+index*symbols[9])
            label=table[sym[0]:].split(b'\0',1)[0].decode()
            if not label and sym[3]<len(sections):label=name(sections[sym[3]])
            rows.append([place,info&0xffffffff,label])
        result[name(sections[s[7]])]=rows
    return result


def generate_loader(output):
    neutral=(REPO/'tools/sched_ext/llama_scx.c').read_text()
    loop='\t\t/* Coarse terminal lifecycle check; never read phase or collect metrics. */'
    ready='\tfprintf(stderr, "llama_scx: partial weighted-vtime scheduler active; waiting for selected child\\n");'
    assert neutral.count(loop)==neutral.count(ready)==1
    neutral=neutral.replace(loop,'\t\tif (analysis_monitor()) { error = -EUCLEAN; break; }\n'+loop)
    neutral=neutral.replace(ready,'\tif (analysis_monitor()) { error = -EUCLEAN; goto out; }\n\tfprintf(stderr, "FULL_GUARD_MONITOR_READY\\n");\n'+ready)
    (output/'neutral_lifecycle.c').write_text(neutral)
    text=(REPO/'tools/sched_ext/new_a/loader.c').read_text()
    text=text.replace('#define main neutral_loader_main','static int analysis_monitor(void);\n#define main neutral_loader_main')
    text=text.replace('#include "../llama_scx.c"','#include "neutral_lifecycle.c"')
    text=text.replace('#ifdef NEW_A_DIAGNOSTICS\n#include "mask_dump.h"','#include "monitor.h"\n\n#ifdef NEW_A_DIAGNOSTICS\n#include "mask_dump.h"')
    anchor='\terror = bpf_object__load(object);';assert text.count(anchor)==1
    text=text.replace(anchor,'\tif (analysis_kernel_open()) return -EUCLEAN;\n'+anchor)
    anchor='\tif (diagnostics_fd >= 0) {';assert text.count(anchor)==1
    text=text.replace(anchor,'\tif (diagnostics_fd >= 0 && analysis_monitor()) result = EXIT_FAILURE;\n'+anchor)
    start=text.index('\t\t\tfprintf(output, "{\\\"cpus\\\":[");')
    end=text.index("\t\t\tfputc(']', output);",start)
    export='''#ifdef FG_GUARD
            fprintf(output, "{\\"instrumentation_mode\\":\\"GUARD_NO_DETAIL\\",\\"optional_metrics\\":{\\"runtime_ns\\":\\"NOT_AVAILABLE\\",\\"migrations\\":\\"NOT_AVAILABLE\\"},\\"cpus\\":[");
#else
            fprintf(output, "{\\"instrumentation_mode\\":\\"FULL\\",\\"optional_metrics\\":{\\"runtime_ns\\":\\"COLLECTED\\",\\"migrations\\":\\"COLLECTED\\"},\\"cpus\\":[");
#endif
            for (int cpu = 0; cpu < count; cpu++) {
                struct new_a_stats *s = &stats[cpu];
                fprintf(output, "%s{\\"cpu\\":%d,\\"selected\\":[%llu,%llu,%llu,%llu],\\"running\\":%llu,\\"stopping\\":%llu",
                    cpu ? "," : "", cpu, (unsigned long long)s->selected[0], (unsigned long long)s->selected[1],
                    (unsigned long long)s->selected[2], (unsigned long long)s->selected[3],
                    (unsigned long long)s->running, (unsigned long long)s->stopping);
#ifdef FG_GUARD
                fprintf(output, ",\\"runtime_ns\\":\\"NOT_AVAILABLE\\",\\"migrations\\":\\"NOT_AVAILABLE\\"");
#else
                fprintf(output, ",\\"runtime_ns\\":%llu,\\"migrations\\":%llu", (unsigned long long)s->runtime_ns, (unsigned long long)s->migrations);
#endif
                fprintf(output, ",\\"mask_violations\\":%llu,\\"tracking_failures\\":%llu,\\"phase_seen\\":%llu,\\"events_attempted\\":%llu,\\"events_dropped\\":%llu}",
                    (unsigned long long)s->mask_violations, (unsigned long long)s->tracking_failures,
                    (unsigned long long)s->phase_seen, (unsigned long long)s->events_attempted, (unsigned long long)s->events_dropped);
            }
'''
    text=text[:start]+export+text[end:]
    text=text.replace("\t\t\tfputc(']', output);", "\t\t\tfputc(']', output);\n\t\t\tanalysis_monitor_export(output);")
    before, after=text.rsplit('\treturn result;\n}',1)
    text=before+'\tanalysis_monitor_close();\n\treturn result;\n}'+after
    assert text.count('\tanalysis_monitor_close();')==1
    (output/'loader.c').write_text(text)
    shutil.copyfile(HERE/'monitor.h',output/'monitor.h')
    return dict(neutral_original=old_build.sha(REPO/'tools/sched_ext/llama_scx.c'),loader_original=old_build.sha(REPO/'tools/sched_ext/new_a/loader.c'),
                differences=['Read-only live diagnostic map and /dev/kmsg checks at loader lifecycle boundaries/poll loop',
                    'Explicit FULL/GUARD provenance; guard optional fields exported NOT_AVAILABLE','Final monitor export and owned fd cleanup'],policy_change=False)
