# NEW_A - CPU Topology-Aware LLM Inference Scheduler

**Research prototype. Not production-ready.**

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
Portable runtime performance equivalence: NOT ESTABLISHED
```

No performance advantage over Linux and no energy-saving advantage have been demonstrated. Actual hardware measurements are limited to an Intel Core i5-1240P. Passing a scoped functional test is not a safety approval.

[한국어 설명](README.ko.md) | [Reproducibility](docs/REPRODUCIBILITY.md) | [Safety and limitations](docs/SAFETY.md) | [Results](results/benchmarks.json) | [Licenses](docs/LICENSES.md)

## 1. Overview

NEW_A studies how CPU topology, inference thread count, and CPU affinity affect CPU-only LLM inference. The final selected scheduler is the unchanged **NEW_A(E) FULL** policy, implemented with Linux sched_ext. The portable configuration layer generates thread arguments and affinity without changing llama.cpp, GGML, OpenMP, or model weights.

This is a source release, not a bundled LLM distribution. Model weights, the Frozen llama-server, private Host Profiles, raw execution logs, credentials, and compiled programs are not included.

## 2. Motivation

Intel Hybrid CPUs combine physical P-cores, E-cores, and possible SMT siblings. Using more compute threads can improve Prefill while consuming more CPU time and sometimes reducing Decode throughput. This project evaluates one logical processor per selected physical core as a resource/performance tradeoff. It does not assume fewer threads are always faster or more energy-efficient.

## 3. Scheduler design

Intel Hybrid selection priority:

```text
P-core representative -> E-core -> P-core SMT sibling -> fallback
```

The final policy preserves its idle selection, local DSQ insertion, shared weighted-vtime DSQ, dispatch, fallback, mask/tracking diagnostics, and `SCX_OPS_SWITCH_PARTIAL`. Only selected server tasks use SCHED_EXT; system tasks are not globally switched. FULL diagnostics remain enabled in the research comparison.

The default portable affinity excludes SMT siblings. On the measured i5-1240P it also excludes E-cores, so the server uses `0,2,4,6`. HTTP and auxiliary threads can compete on those same CPUs. Four compute threads do not mean four total server threads.

V2/V3/V4 are not selected release policies and are excluded from the default source/build path. Static-analysis helpers have been separated from historical candidate directories. The final eight-file policy identity is retained in `tools/sched_ext/new_a_final/policy-manifest.json`.

## 4. Automatic thread configuration

| CPU topology | Compute/Batch policy |
|---|---|
| Intel Hybrid | Available physical P-cores |
| Intel non-hybrid | Available physical cores |
| AMD homogeneous | Available physical cores |

Select one allowed online logical processor per selected physical core; HTTP threads default to 2. Package/die/core identities and SMT relationships are validated. The machine's SMT setting is not changed. Restricted affinity, offline CPUs, noncontiguous CPU numbers, and unsupported topology are handled explicitly. Unsupported or ambiguous heterogeneous AMD/topology configurations are not assumed safe.

On the measured i5-1240P: P4 + E8, Compute=4, Batch=4, HTTP=2, affinity `0,2,4,6`, excluded SMT `1,3,5,7`.

The BPF scheduler cannot change llama-server's internal thread count. A separate configuration layer generates existing server arguments and the topology input. This automation is experimental; performance equivalence to manual settings is not established.

## 5. Installation and static build

Required: Linux x86-64 with compatible sched_ext/BTF and kfuncs, Python 3, a C compiler, clang with the BPF target, bpftool, make, libbpf/libelf/zlib development files, and headers matching the running kernel. The tested kernel was `7.0.0-42-generic`. Kernel versions and sched_ext APIs must not be assumed interchangeable.

No installation, privileged loading, or power setting changes occur automatically.

```bash
make test
python3 -B tools/sched_ext/new_a_portable/build.py --output "$NEW_BUILD"
```

`NEW_BUILD` must be a new directory. The build creates the CPU probe, E FULL object and guarded loader, a plain reference object, and the unchanged selected-child launcher. The documented scheduler is E FULL. The plain object is a static reference, not the final benchmark configuration.

`static-report.json` records BTF/kfunc checks and optional reference-object comparison. Compilation/static PASS does not mean verifier or attach PASS. Generated BTF headers and binaries must stay out of Git.

## 6. Usage

Create a local server setup from `examples/server-setup.example.json`, replacing generic paths with your own validated Frozen server/library/model paths. Keep that local file and generated Host information private. Do not pass a generated configuration file directly as a loader admission proof.

```bash
python3 -B tools/sched_ext/new_a_portable/config.py \
  --probe "$NEW_BUILD/cpu-probe" \
  --server-config "$LOCAL_SERVER_SETUP" \
  --output "$NEW_CONFIGURATION"

python3 -B tools/sched_ext/new_a_portable/config.py \
  --probe "$NEW_BUILD/cpu-probe" \
  --check-current "$NEW_CONFIGURATION"
```

This produces `configuration.json` and `topology.bin`, including server argv/environment, selected CPUs, thread counts, and input/host identities. It does **not** start a server or attach BPF. `execution_admitted=false` remains intentional.

For a previously validated research checkout, `new_a_portable_runtime` consumes the generated argv/env/mask/topology through the existing Session layer and compares manual/automatic runtime evidence. That acquisition path depends on the original Frozen bundle, historical Git references, request, strict-failure records, and sealed experiment inputs, which are not shipped here. A fresh clone is **not** a complete one-command inference benchmark environment. See [reproducibility](docs/REPRODUCIBILITY.md) before any privileged operation.

## 7. Benchmarks

Same campaign; Intel Core i5-1240P; 4/4/2; P-only `0,2,4,6`; AC; Turbo OFF; performance profile; same Frozen server/model and 920/128-token request; GPU offload 0. Six independent measured server sessions per policy, each after two warmups. External Wakeup/fexit/probes, BPF Runtime Statistics and RAPL were off.

| Metric | Linux | NEW_A E FULL |
|---|---:|---:|
| Prefill tok/s | 60.6814 | 60.0433 |
| Decode tok/s | 22.7254 | 21.5542 |
| HTTP latency | 20.8019 s | 21.2682 s |
| Process CPU time | 75.9722 s | 77.3200 s |

**NEW_A did not demonstrate higher performance than Linux.** The final policy was selected as the research prototype, not as a performance winner. See aggregate ranges, standard deviations and paired results in [benchmarks.json](results/benchmarks.json).

CPU time is the sum of stable server-TID schedstat deltas around the measured request; startup and warmups are excluded, while adjacent snapshot/power/serialization overhead is included. It is not wall-clock latency or a Prefill-only timer.

## 8. Resource efficiency

In a separate exploratory campaign, NEW_A E8-MIX versus E4-P, with instrumentation OFF and two measurements per condition:

- Compute/Batch thread count: 8 -> 4, a 50% reduction in the setting.
- Prefill retention: approximately 82.19%.
- Decode retention: approximately 125.77%.
- Process CPU time: approximately 38.50% lower.
- HTTP time: approximately 6.06% higher.

Both thread count and affinity change. These are configuration tradeoffs, not causal evidence that the NEW_A scheduler alone saves CPU time. They are not pooled with the Linux comparison. Reducing thread count does not establish a 50% CPU-usage or energy saving.

## 9. Energy measurements

Exploratory RAPL ON mean package energy: E8-MIX 251.465 J, E4-MIX 265.199 J, E4-P 266.014 J, two measurements per condition. This measures the system CPU package during the request, including other CPU work, not the server alone or AC input for the whole laptop.

**No energy-saving advantage was established.** Energy instrumentation remains `OVERHEAD_REVIEW_REQUIRED`: a paired E4-P Prefill change exceeded the original overhead limit. The limit was not relaxed and the 18-session energy comparison was not admitted. CPU-time savings do not imply energy savings.

## 10. Portability and runtime regression

Intel Hybrid, homogeneous Intel and homogeneous AMD topology/configuration logic is covered by unprivileged mock tests, including SMT ON/OFF, offline CPUs, restricted masks, noncontiguous numbering and invalid/unsupported inputs. Only the i5-1240P has actual scheduler/inference measurements. Other CPUs' verifier, attach, inference, performance and resource behavior are **NOT_TESTED**.

Manual versus AUTO results are separate campaigns/subsets:

| Campaign | Measurements per setting | AUTO Prefill change | AUTO Decode change | AUTO HTTP change |
|---|---:|---:|---:|---:|
| Initial 2 blocks | 2 | -0.79% | -3.26% | +1.51% |
| Additional 4 blocks | 4 | -0.29% | -0.67% | +0.39% |

Do not automatically pool them into one official experiment. No clear implementation error was found, but these results do not establish performance equivalence. No post-hoc equivalence margin was introduced.

**Portable runtime performance equivalence: NOT ESTABLISHED**

## 11. Limitations

Known strict safety validation failures remain: a historical live-affinity test recorded mask-violation counters. Functional/cleanup success in fixed-affinity runs did not clear the strict failure. See [safety details](docs/SAFETY.md).

Other limits include uncontrolled background load, limited hardware coverage, small resource/portable subsets, no reliable phase-specific energy attribution, and earlier Wakeup/fexit observer distortion. Migration reduction is not a success criterion by itself. No production-readiness, Linux performance advantage, energy advantage or cross-CPU performance guarantee is claimed.

## 12. License

Scheduler/support source retains its GPL-2.0 notices and upstream copyrights. The top-level [LICENSE](LICENSE) is the existing sched_ext GPL v2 text. The included GGML interface header retains the upstream MIT context in [LICENSES/UPSTREAM-MIT.txt](LICENSES/UPSTREAM-MIT.txt). Linux/libbpf/GCC system dependencies are not vendored. See [license inventory](docs/LICENSES.md); this is not an attempt to relicense third-party files.

## 13. Reproducibility

Build/tests/configuration generation are separate from kernel functional admission and performance acquisition. Existing research runners fail closed without their sealed local inputs. Reboot, CPU hotplug, affinity, kernel/BTF or power changes require revalidation. No automatic retry, global SCHED_EXT switch, SMT disable or power change is provided.

[Reproducibility procedure](docs/REPRODUCIBILITY.md) | [Public file manifest](PUBLICATION_MANIFEST.json)
