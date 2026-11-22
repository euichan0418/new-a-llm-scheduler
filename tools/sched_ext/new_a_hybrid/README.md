# Experimental Hybrid configuration only

[한국어 사용 방법](../../../docs/hybrid-auto-configuration.md) · [팀원 P6+E8 실험 계획](../../../docs/team-p6e8-experiment.md)

The `new-a-topology-config` Python CLI supports `detect`, `recommend`, `validate`, `environment`, and `experiments`. Build only its unprivileged CPUID probe with `build_probe.py --output NEW_DIRECTORY`. Run tests with `make test-hybrid` at the repository root.

This explicit experimental profile preserves the existing Portable default and E FULL policy. It does not start servers, change power settings, authenticate, load BPF, or grant runtime admission. The existing fixed-host Guard/runtime must not be bypassed on a new CPU.

`TEAM_CPU_RUNTIME_NOT_TESTED`; `performance_validated=false`; `execution_admitted=false`.

`STRICT_ACCEPTANCE=FAILED`; `safety_verdict=FAIL`; `OVERHEAD_REVIEW_REQUIRED`.

GPL-2.0. Reuses the unchanged existing Portable Python detector and CPUID C source; no third-party implementation or binary is vendored.
