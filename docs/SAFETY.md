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

## Recent Runtime Lite scope

On the tested i5-1240P/kernel43 campaign, C/E/L Audit/Clean and valid performance sessions passed their fixed-affinity functional, identity, response, thermal and Cleanup gates. E/L had zero observed Mask/Tracking failures. Failed authentication attempts remained excluded; a failed OFF command is not proof that statistics were enabled. Lite keeps optional runtime unavailable and mandatory Migration/Mask/Tracking/error evidence. These scoped successes do not clear the historical live-affinity failure or establish production/unattended-use safety.

Same-configuration scheduler superiority remains NOT_PROVEN. Runtime Lite exploration is INCONCLUSIVE and does not replace E FULL. New topology, Boot/BTF, governor, inputs or affinity require fresh admission. Do not run root experiments just because static source tests pass.
