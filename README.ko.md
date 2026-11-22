# NEW_A - CPU 토폴로지 기반 LLM 추론 연구

최종 정책은 기존 NEW_A(E) FULL이다. Runtime Lite는 E를 대체하지 않는 별도 실험 후보다. 연구용 프로토타입이며 배포용 제품이 아니다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
Portable runtime performance equivalence: NOT ESTABLISHED
```

## 최신 연구 성과와 실행 구성

최신 공유 내용은 [feature/new-a-48-research-update](https://github.com/euichan0418/new-a-llm-scheduler/tree/feature/new-a-48-research-update)에서 검토한다. main은 팀원 검토·Merge 전까지 기존 상태다.

```bash
git clone --branch feature/new-a-universal-runtime https://github.com/euichan0418/new-a-llm-scheduler.git
cd new-a-llm-scheduler
```

현재 i5-1240P에서 테스트한 성능 우선 구성은 **Compute4/Batch8/HTTP2, P4+E4 Affinity0,2,4,6,8,9,10,11**이다. 아래 Portable 기본4/4 P-only 정책은 그대로 유지한다.

| 구성 | Prefill tok/s | Decode tok/s | HTTP s | Process CPU Time s |
|---|---:|---:|---:|---:|
| C88 | 73.1430 | 19.4326 | 19.172912 | 122.393803 |
| E88 | 72.5486 | 17.1294 | 20.160085 | 126.271304 |
| C48 | 73.0414 | 21.5022 | 18.560891 | 106.998383 |
| E48 | 72.4070 | 21.7677 | 18.593546 | 109.503467 |

6블록 확증에서 NEW_A4/8은 NEW_A8/8 대비 HTTP7.77%/전체 CPU Time13.28% 감소, Decode27.08% 증가, Prefill99.80% 유지를 관측했다. HTTP/CPU 감소는6/6블록이다. Linux8/8 대비 HTTP3.02%/CPU10.53% 감소도6/6블록에서 나타났다. 같은 Affinity지만 Thread 설정이 달라 스케줄러만의 성과가 아니다.

같은4/8 Linux 대비 NEW_A는 평균32.66ms 느렸고 대응95%구간은-176.62~+241.94ms다. **SCHEDULER_SUPERIORITY_NOT_PROVEN**, 동등성도 미입증이다.

Runtime Lite는 선택적 elapsed 누적만 제거하고 필수 진단을 유지한 후보다. kernel43에서 기능 Audit/Clean을 통과했지만2블록 탐색에서는 E 대비 HTTP+26.514ms/CPU+0.0808%, Decode 감소와 HTTP 블록 방향 반전으로 **RUNTIME_LITE_EXPLORATION_INCONCLUSIVE**다. E FULL 유지, L 채택/즉시 확증은 보류한다.

[비대칭 확증](docs/research/asymmetric-threads.md), [Linux 비교](docs/research/linux-comparison.md), [Lite 결과](docs/research/runtime-lite.md), [인수인계·향후 연구](docs/research/research-status.md)를 먼저 읽는다. 새 연구는 PID/TGID 중복 읽기의 실제 BPF 잔존 여부, Prefill 배치 병목, E-core L2 그룹 Affinity 순이며 아직 구현/개선 검증되지 않았다.

## 구성

Intel Hybrid는 사용 가능한 물리적 P-core마다 대표 논리 CPU 하나를, 일반 Intel·동종 AMD는 사용 가능한 물리적 코어마다 하나를 선택한다. Compute/Batch는 선택 코어 수, HTTP는 기본2다. 시스템 SMT를 끄지 않으며 현재 online·허용 Affinity·패키지·코어·SMT 관계를 검증한다. 불명확하거나 지원하지 않는 토폴로지는 명시적으로 거부한다.

현재 i5-1240P에서는 4/4/2와 0,2,4,6을 생성한다. BPF가 서버 내부 스레드 수를 바꾸는 것이 아니라 별도 설정 계층이 기존 Frozen 서버 인자를 만든다. 기존 P 대표 -> E -> SMT -> fallback, Local/Shared DSQ, Partial mode, FULL 진단과 Mask·Tracking을 보존했다. V2/V3/V4 정책은 기본 실행 경로에 포함하지 않는다.

## 기존4/4 및 자원·에너지·Portable 결과

모든 실제 성능 결과는 Intel Core i5-1240P에서 얻었다. 동일4T P-only 캠페인에서 정책별6회 평균:

| 지표 | Linux | NEW_A |
|---|---:|---:|
| Prefill tok/s | 60.6814 | 60.0433 |
| Decode tok/s | 22.7254 | 21.5542 |
| HTTP 초 | 20.8019 | 21.2682 |
| 요청 구간 CPU 초 | 75.9722 | 77.3200 |

Linux보다 높은 성능을 입증하지 못했다. 별도 E8-MIX/E4-P 비교에서는 계산 스레드 설정50% 감소, Prefill82.19% 유지, Decode125.77% 유지, CPU 시간38.50% 감소, HTTP6.06% 증가를 관측했다. 스레드 수와 Affinity가 함께 바뀌었으므로 스케줄러만의 효과가 아니다. 다른 캠페인과 합산하지 않는다.

RAPL ON 평균 Package Energy는 E8-MIX251.465J, E4-MIX265.199J, E4-P266.014J다. 4T 에너지 절감을 확인하지 못했다. 사전 오버헤드 기준 위반으로 OVERHEAD_REVIEW_REQUIRED를 유지한다. CPU 시간 감소와 에너지 절약은 다르며 RAPL은 서버만의 에너지가 아니다.

수동/자동 최초2블록에서 AUTO의 Prefill/Decode는 약0.79%/3.26% 감소, HTTP는1.51% 증가했다. 추가4블록에서는 약0.29%/0.67% 감소, HTTP0.39% 증가했다. 두 자료는 구분한다. 명확한 설정 구현 오류는 발견하지 못했지만 성능 동등성을 입증하지 못했고 사후 허용 기준을 만들지 않았다.

## 빌드와 사용 범위

영어 [README](README.md)의 설치·사용·재현 절차를 따른다. make test는 모의 토폴로지51개, 실행 연결 합성 테스트17개 및 Runtime Lite14개 비특권 검사를 실행한다. 공개 숫자는 scripts/summarize_research.py --check로 재계산한다. BPF 빌드는 정적 검사이며 실제 verifier·attach 성공을 뜻하지 않는다. 설정 생성은 서버나 BPF를 실행하지 않는다.

실제 연구 실행기는 기존 Frozen·모델·원시 요청·Host Profile·Git 참조·strict 실패 증거를 요구한다. 이 저장소에 모델·바이너리·개인 Host 정보·대형 로그는 포함하지 않았다. 새 clone만으로 완전한 추론 실험을 실행할 수 있다고 주장하지 않는다. 다른 Intel·AMD의 실제 BPF·추론·성능은 NOT_TESTED다.

기존 live-affinity Mask 오류로 strict/safety 실패가 남아 있다. 고정 Affinity 기능 검증의 성공이 이를 해결하지 않는다. [안전성 상세](docs/SAFETY.md), [재현 조건](docs/REPRODUCIBILITY.md), [라이선스](docs/LICENSES.md)를 함께 읽어야 한다.

## 실험적 Intel Hybrid 자동 구성

명시적 성능 후보 생성기와 [사용 방법](docs/hybrid-auto-configuration.md), [팀원 P6+E8 계획](docs/team-p6e8-experiment.md)을 추가했다. 가용 물리 P 수로 Compute를 정하고 E 절반을 Batch/Affinity에 추가한다. 기존 Portable 기본값과 E FULL은 변경하지 않았다. P6+E8의 6/10은 성능 미검증이며 팀원 PC의 CPU 번호/SMT/캐시 구조를 직접 탐지해야 한다. `make test-hybrid`는 비특권 검사다. 기존 고정-host Guard의 별도 adapter와 새 Host/Baseline/Audit/Clean 없이는 실제 실행할 수 없다. `TEAM_CPU_RUNTIME_NOT_TESTED`.

## Experimental universal runtime / 범용 실행 연결

[범용 Runtime](docs/universal-runtime.md) · [Intel/i7-12700H](docs/intel-hybrid-runtime.md) · [AMD](docs/amd-runtime.md) · [Guard와 안전성](docs/runtime-safety-and-validation.md)

`tools/sched_ext/new_a_universal_runtime/` connects verified CPU topology proposals to an independent dynamic FULL Guard/loader. Original NEW_A E FULL BPF, Portable defaults, Runtime Lite/PID Lite and observer research are unchanged. Intel Hybrid uses dynamic P/E/SMT tiers; homogeneous Intel/AMD uses physical representatives before SMT. Unknown types are blocked. CPU IDs 0..1023, including sparse IDs above63, have explicit bounds.

정적 빌드·모의 테스트만 완료했다. 새 범용 객체의 실제 verifier/attach/기능/성능은 **NOT_RUN**이다. `RUNTIME_FUNCTIONAL_NOT_RUN`, `TEAM_CPU_RUNTIME_NOT_TESTED`, `AMD_RUNTIME_NOT_TESTED`이며 기존 `STRICT_ACCEPTANCE=FAILED`, `safety_verdict=FAIL`, `OVERHEAD_REVIEW_REQUIRED`를 유지한다. 설정 성공이 실행 승인이나 다른 CPU에서의 성능 증거는 아니다. 팀원 PC마다 새 Host/Baseline과 Audit/Clean 증거를 요구한다. 일반 사용자 준비는 `make test`와 문서의 read-only CLI를 사용한다. 실제 권한 실행은 별도 사용자 승인 후에만 가능하다.
