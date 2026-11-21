# NEW_A_RUNTIME_LITE - experimental research candidate

Status: `RUNTIME_LITE_EXPLORATION_INCONCLUSIVE`. E FULL remains the selected scheduler. Scoped C48/E48/L48 Audit/Clean completed on i5-1240P / kernel 7.0.0-43-generic, but this does not clear the historical safety failure. The two-block performance exploration did not demonstrate an improvement.

The BPF source and candidate.diff are exact copies of the validated research candidate. Only optional normal-path elapsed accumulation in observe_runtime is removed: timestamp reads, start_ns record/reset, subtraction and runtime_ns accumulation. Error paths still record timestamps. CPU selection, local/shared DSQ, weighted vtime, Task Storage, Mask/Tracking, Migration and error evidence are retained. See candidate.diff.

```text
instrumentation_mode=FULL_NO_ELAPSED_RESEARCH
runtime_ns=NOT_AVAILABLE
migrations=COLLECTED
```

An unmeasured runtime is not zero. Process CPU Time is separately measured using the whole-process CPU clock around each HTTP request. BPF elapsed accumulation is neither that clock nor BPF Callback kernel execution cost.

From the repository root, without sudo:

```bash
python3 -B tools/sched_ext/new_a_runtime_lite/test_runtime_lite.py
python3 -B tools/sched_ext/new_a_runtime_lite/build.py --output "$NEW_EXTERNAL_BUILD"
```

NEW_EXTERNAL_BUILD must be a new absolute directory outside this repository. Build dependencies and kernel checks are described in ../../../docs/REPRODUCIBILITY.md. This compiles static E/L diagnostic and plain references plus guarded loaders; it never attaches BPF or runs an LLM. Plain objects are static references, not benchmark policies. The diagnostic monitor uses the exact P4+E4 mask in both E and L. Static PASS is not verifier/attach or safety PASS.

Public packaging reuses existing support/static_btf.py and support/prepare_build.py, extracts the original ELF inventory and pure plans() function, checks the running kernel SCHED_EXT UAPI directly, and pins public support copies. See PUBLICATION_PROVENANCE.json and original-research-pins.json. The candidate BPF source is unchanged. The static loader is not represented as the exact object-identity-attested acquisition loader from the private research campaign. Private Frozen inputs, Host Profiles, per-Boot baselines and the complete runtime acquisition chain are not shipped. A clone is not a one-command reproduction of the measured experiment.

Validation.py checks only the diagnostic JSON contract. It does not replace full-session identity, response, thermal, kernel, Runtime Statistics OFF, Cleanup or fresh Audit/Clean admission. Prepare_study.py exposes only the fixed plans for tests, not an executable campaign controller.

Measured results and limitations: [Runtime Lite report](../../../docs/research/runtime-lite.md).

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```
