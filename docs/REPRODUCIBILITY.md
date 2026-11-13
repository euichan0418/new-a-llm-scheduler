# Reproducibility and execution boundaries

## Public, unprivileged steps

1. Run `make test` for mock topology and synthetic runtime evidence tests.
2. Build with `python3 -B tools/sched_ext/new_a_portable/build.py --output "$NEW_BUILD"` in a new directory. This compiles, checks current BTF/kfunc signatures, and builds the guarded E FULL loader and selected-child helper. It does not load BPF. Matching kernel headers must define SCHED_EXT=7.
3. Create a private local server setup from the example. Generate configuration with the README command, then use `--check-current`. The generated CPU list and thread arguments must be checked against the real caller's allowed online CPUs.

These steps can be exercised from this source release. Inference also requires a separately supplied Frozen server with the custom legacy compute-profile interface and phase-marker symbols. An arbitrary current upstream llama-server is not asserted to be equivalent. The included GGML header is an interface/source-limit reference, not a bundled inference engine.

## Existing validated research checkout only

The preserved acquisition modules under `tools/sched_ext/new_a_portable_runtime` implement manual/AUTO provenance, actual /proc argv/OMP and TID mask/policy checks, scoped OpenMP team evidence, FULL diagnostics, response validation, and functional-evidence admission. Their original acquisition/build lineage uses historical result directories, Git refs, Frozen artifacts and strict-failure records. Those private local inputs are not published. They must be restored and independently identity-checked before these campaign modules can run; a clean public clone is not a complete campaign environment.

The final policy and public static build are the same E FULL. Build-only support helpers were separated from V2/V3 namespaces; no candidate BPF policy is selected. See the source provenance and policy hash manifests.

For a validated local campaign, execution is deliberately separate:

```bash
bash "$FUNCTIONAL_RUN/run-terminal.sh"
# Only after actual functional evidence is complete and current:
bash "$REGRESSION_RUN/run-terminal.sh"
```

The first step must verify actual load/attach, mask/tracking, real server/TID configuration, HTTP200/920/128/hash, thermal/kernel status and cleanup/disabled. The second revalidates that evidence bound to the same Host, objects, server, model and request. Reboot or environmental changes require a new profile and admission. No script should be modified to bypass missing historical inputs or strict failures.

## Measurement contract

- i5-1240P; Compute/Batch/HTTP 4/4/2; P-only `0,2,4,6`; housekeeping15 for research controls.
- AC, no_turbo=1, performance power profile; server GPU offload0.
- Same Frozen server, GGUF, 920 input and128 generated tokens; context2048, batch512, ubatch256, same private request/seed/sampling.
- New server per session, two warmups, one measured request, shutdown/detach/cleanup.
- E FULL diagnostics retained; external Wakeup/fexit/path probes, Runtime Statistics and RAPL OFF for the main performance comparisons.
- HTTP latency is blocking POST through complete response-body read. Internal prompt+decode times come from the response. Request CPU time is the sum of stable-TID schedstat deltas in a slightly wider proc/power snapshot window; not phase CPU time.
- System CPU busy includes background applications; exact phase worker residence/barrier time is not available.

Final Linux/E comparison: six balanced blocks,12 independent server sessions,24 warmups and12 measurements. Portable initial comparison: two blocks,4 sessions. Additional portable comparison: four blocks,8 sessions. The portable subsets remain separate and have no predeclared numerical equivalence margin. `NOT ESTABLISHED` is preserved rather than assigning a post-hoc PASS.

The public aggregate JSON includes per-campaign means, ranges, sample SD and paired changes. It deliberately excludes private raw requests, full responses, Host Profiles, process identities and large logs. This limits third-party re-audit of the original raw corpus; no full raw-data availability is claimed.
