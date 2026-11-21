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

## Historical 4/4 measurement contract

- i5-1240P; Compute/Batch/HTTP 4/4/2; P-only `0,2,4,6`; housekeeping15 for research controls.
- AC, no_turbo=1, performance power profile; server GPU offload0.
- Same Frozen server, GGUF, 920 input and128 generated tokens; context2048, batch512, ubatch256, same private request/seed/sampling.
- New server per session, two warmups, one measured request, shutdown/detach/cleanup.
- E FULL diagnostics retained; external Wakeup/fexit/path probes, Runtime Statistics and RAPL OFF for the main performance comparisons.
- HTTP latency is blocking POST through complete response-body read. Internal prompt+decode times come from the response. Request CPU time is the sum of stable-TID schedstat deltas in a slightly wider proc/power snapshot window; not phase CPU time.
- System CPU busy includes background applications; exact phase worker residence/barrier time is not available.

Final Linux/E comparison: six balanced blocks,12 independent server sessions,24 warmups and12 measurements. Portable initial comparison: two blocks,4 sessions. Additional portable comparison: four blocks,8 sessions. The portable subsets remain separate and have no predeclared numerical equivalence margin. `NOT ESTABLISHED` is preserved rather than assigning a post-hoc PASS.

The public aggregate JSON includes per-campaign means, ranges, sample SD and paired changes. It deliberately excludes private raw requests, full responses, Host Profiles, process identities and large logs. This limits third-party re-audit of the original raw corpus; no full raw-data availability is claimed.

## New 4/8 confirmation and Runtime Lite review

The performance-oriented i5-1240P configuration is Compute4/Batch8/HTTP2 with P4+E4 mask0,2,4,6,8,9,10,11. This experimental configuration does not alter the portable physical-P-core-count default. Every compared condition within each new campaign uses the same affinity. Compute8/Batch8 references differ in Compute only.

The six-block 24-sample confirmation used kernel42; Runtime Lite's independent two-block campaign used kernel43 and a fresh Boot/Host Profile/V3, including a changed governor and sensor path. Read docs/research for the separate results. Both measure **whole-process CPU clock** around the HTTP request, which differs from the historical stable-TID CPU metric above. Common CS/Migration remains stable-boundary-TID only; Busy is system-wide. BPF elapsed is a separate whole-lifetime diagnostic, NOT_AVAILABLE for Lite. No RAPL energy result is claimed.

From a public clone, without sudo:

```bash
make test
python3 -B scripts/summarize_research.py --check
# Use a new output directory outside the repository:
make build-lite NEW_BUILD="$NEW_EXTERNAL_BUILD"
make check
```

The Lite builder reuses published static support, checks matching-kernel SCHED_EXT=7 UAPI and BTF/kfuncs, compares executable sections/relocations, preserves mandatory diagnostics and builds guarded E/L loaders. The diagnostic objects are the research policies; plain objects are comparison references. The validated BPF source and candidate.diff are exact copies. Import paths, source pins, ELF inventory extraction and external build output are packaging adaptations, not scheduling changes. The source-release static loader is not represented as the exact object-identity-attested loader used by the private acquisition campaign.

The fixed study plan and diagnostic JSON verifier are included for code review; the full local C/E/L controller, Frozen bundle, historical acceptance inputs, private Host Profiles and per-Boot Baselines are not packaged as a turnkey runner. Do not delete admission checks or fake missing evidence to run them. A current upstream llama-server or new model is not automatically equivalent to the Frozen experiment. Prepare those separately and check their licenses/hashes, kernel/Boot/BTF/topology/power/real argv/env/TIDs, then obtain safety review before any privileged load.

Required functional sequence is fresh C48/E48/L48 Audit/Clean, raw Identity/Mask/Tracking/Migration/events/response920-128/hash/thermal/kernel/cleanup/final-disabled validation, then separately performance admission bound to the same environment. A reboot or runtime change invalidates that admission. Existing safety failures remain even when scoped functionality passes. Do not automatically execute privileged experiments from these docs.

The local authentication repair preserves the controller/worker POSIX terminal session, performs one explicit user sudo-v and non-refreshing sudo-N-n checks, stops on expiry and requires reviewed explicit resume with a new attempt ID. It has no keepalive, password storage or sudoers changes. This is a documented method, not an installed public privilege helper. Do not generalize its safety to another environment without review.

Public CSVs allow independent arithmetic/statistical checks but omit raw bodies, PID/TID, Boot ID, private paths and logs. They are not full third-party raw-corpus revalidation. Source provenance and checksums are provided. Preserved old acquisition modules remain historical reference code and require private sealed inputs; they are not the latest C/E/L acquisition path.
