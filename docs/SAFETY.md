# Safety and validation status

This is a research prototype, not production-ready software.

- `STRICT_ACCEPTANCE=FAILED`
- `safety_verdict=FAIL`
- Energy instrumentation: `OVERHEAD_REVIEW_REQUIRED`
- Portable runtime performance equivalence: `NOT ESTABLISHED`

The retained full acceptance record failed with `Mask or task tracking failure`. Its historical live-affinity auxiliary diagnosis recorded **2 mask violations**, **0 tracking failures**, and **0 dropped events**. The diagnosis labeled the timeline `normal_transition_supported`, but the strict counter gate and safety verdict still failed. That auxiliary classification does not waive the strict mask criterion.

Later fixed-affinity functional tests and cleanup checks passed in their scoped experiments. They did not repeat or clear every dynamic-affinity safety case. The release does not remove mask/tracking checks, change thresholds, expand the selected-task scope, disable Partial mode, or convert these failures to PASS.

Runtime invariants include correct Host/kernel/boot/BTF, AC/Turbo/profile, allowed masks, selected-server identity, real thread/OMP configuration, HTTP/tokens/hash, thermal counters, kernel warnings, detach and final sched_ext disabled. Failure stops subsequent sessions; no automatic retry is allowed.

Other Intel and AMD CPUs have configuration/mock coverage only. Actual verifier/attach, LLM functionality and performance there are NOT_TESTED. Topology support does not prove scheduler runtime compatibility.

Previous Wakeup/fexit observers significantly perturbed measured performance. Their ON latency is not treated as uninstrumented latency. RAPL instrumentation overhead review remains open; no energy advantage is claimed.
