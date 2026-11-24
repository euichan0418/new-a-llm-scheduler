GitHub에서 기존 자산 없이 시작하려면 [bootstrap 안내](../../../docs/autotune-bootstrap.ko.md)와 `bash scripts/autotune-bootstrap.sh --auto`를 사용한다. 아래는 자산을 직접 지정하는 기존 저수준 실행기 설명이다.

# Intel Hybrid Prefill/Decode Auto-Tuning

실제 Frozen llama-server 추론을 반복 측정하는 연구용 실행기다. `run --execute`는 탐지에서 끝나지 않고, 서버 독립 실행·워밍업2·측정1·검증·정리를 후보마다 수행한다. CPU 전원 설정, 모델 다운로드, 기존 BPF 정책 및 Portable 기본값을 변경하지 않는다.

이번 개발에서는 실제 sudo 인증·BPF attach·추론·성능 측정을 하지 않았다. 실기기 검증은 NOT_RUN이다. 모의 테스트 결과를 CPU 성능 결과로 해석하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

## 기본 정책과 선정 원칙

사용 가능한 물리 P-core 수를 P, 사용 가능한 E-core 수를 E라 할 때 기본값은 Compute=P, Batch=P+floor(E/2), HTTP2다. Affinity는 P 대표 전체+E 절반, SMT는 제외하고 E는 검증된 L2 compact 배치를 사용한다. 논리 CPU 수를 단순히2로 나누지 않는다. P SMT2-way/E1-way가 모두 사용 가능하고 E가 짝수인 경우에만 Batch가 논리 CPU 수의 절반과 같다.

1. Prefill 탐색: Compute=P 고정. Batch=P,P+2,...,P+E 및 홀수 E의 마지막 후보를 생성한다. 기존 공식 대조군이 step2 격자 사이에 있으면 별도로 추가한다(E6의 절반3 등). Batch와 affinity가 함께 바뀌므로 **실행 구성 전체** 비교다.
2. Decode 탐색: 유망 Prefill 설정의 Batch/affinity를 고정하고 Compute=max(1,P-2)..min(P+2,선택 CPU 수)를 탐색한다. P6/선택 CPU10이면4,5,6,7,8이다. 기본 공식은 항상 대조군으로 포함한다. 다른 mask를 가진 공식 대조군은 Compute 단독 효과로 해석하지 않는다.
3. 기본 탐색은 후보별2회, 정순/역순이다. 이 결과는 선별용이다. HTTP 일관 개선 및 사전 기준에 맞는 상위 후보 최대2개+기본값을 **새 독립6블록**으로 추가 비교한다. 3조건이면6개 순열을 사용하며2조건이면 순서를 교차한다. 확인할 후보가 없으면 무리하게 반복하지 않는다.

관측상 Prefill/Decode/HTTP/CPU 최고 설정과 최종 추천은 분리한다. 최저 단일 표본으로 정책을 선정하지 않고, 단계별 평균/SD·블록 차이·원시 경로를 보고하며 탐색과 확증 표본을 합산하지 않는다.

사전에 고정하는 보수적 기본 기준:

| 기준 | 기본값 |
|---|---:|
| 최소 실질 HTTP 개선 |2%|
| Prefill/Decode 시간 회귀 허용 상한 |1%|
| Process CPU Time 증가 허용 상한 |0%|
| 독립 확증 블록 |6|

이는 성능 예측이나 과거 실험에서 도출한 수치가 아니라 이 도구의 연구 설계 기본값이다. CLI로 **실행 전** 명시할 수 있으며 이후 resume에서 변경하면 거부한다. 최초 계획·규칙은 봉인하고 측정 raw slot에도 규칙 hash를 결합한다. 봉인은 우발적 변경 검출이며 동일 UID의 의도적인 전체 기록 조작을 막는 보안 경계가 아니다.

예외 추천에는6블록 모두 HTTP 개선, 대응 변화율 Student-t95% CI가 최소 개선보다 유리한 범위, Prefill/Decode/CPU의 대응 CI가 위 허용 범위를 만족해야 한다. n=6의 분포/독립성 가정과 다중 후보의 pointwise CI 한계를 보고하며 전역 최적값을 주장하지 않는다. 차이가 작거나 방향이 섞이거나 불확실하면 기본값을 유지한다. **기본값 유지가 통계적 동등성 입증은 아니다.** 추천 JSON만 생성하고 기본 정책/설정 파일을 자동 변경하지 않는다.

Linux만 측정했다면 NEW_A 최종 추천은 기본 공식으로 유지한다. Linux의 확인된 예외는 `linux_execution_recommendation`으로 별도 보고한다. NEW_A 예외는 NEW_A의 새 검증·측정에서만 허용한다. 스케줄러 자체의 Linux 대비 우위를 주장하지 않는다.

## 의존성과 준비

기존 `new_a_hybrid`, `new_a_portable`, `new_a_universal_runtime`, `support`, `bench`와 검증한 GGML 헤더를 재사용한다. Linux x86_64/Python3.11 이상, 기존 [범용 빌드 의존성](../../../docs/universal-runtime.md)이 필요하다. 일반 llama.cpp 서버를 임의로 대신 사용하지 않는다. 선택한 Frozen에는 Compute/Batch/HTTP 옵션과 `--server-compute-profile-mode legacy`, OpenMP 팀 출력 지원이 필요하다.

**Linux 모드도 기존 연구 실행기의 kernel-watch(/dev/kmsg)·읽기 전용 Runtime Statistics OFF 확인을 재사용하므로 sudo와 sched_ext/BTF/kfunc 정적 빌드 환경이 필요하다.** FULL BPF는 필요하면 컴파일하지만 Linux 측정에서는 attach하지 않는다. 이 버전은 해당 환경이 없는 일반 커널용 rootless 벤치마커가 아니다.

서버·모델은 사용자가 명시한다. `--library-dir`의 ELF 및 `--elf-root` 추가 라이브러리, 시스템 glibc/OpenMP/로더 의존성, symlink 해석과 해시를 기록한다. 기존 build report가 없으면 명시적 실제 실행 시 새 output 안에서 기존 정적 builder를 실행한다. 기존 builder가 helper loop 뒤 source 필드를 잘못 기록한 보고서는 원본을 편집하지 않고, FULL compile 명령/출력/원본 정책 hash를 확인한 별도 메타데이터 보고서로 정규화한다. 바이너리나 Guard를 바꾸는 과정이 아니다. CPUID probe는 자동 빌드하거나 `--probe`로 검증된 것을 제공할 수 있다. `plan`의 단순 probe와 실제 FULL build probe의 hash가 다르면 후보 ID는 달라질 수 있지만 실제 실행 계획은 최종 build의 탐지로 봉인된다.

AC, Turbo OFF, 모든 governor performance, Power Profile performance, 온도85°C 미만, online/allowed/cpuset/quota/Boot/kernel/BTF 불변을 요구한다. 조건이 맞지 않으면 수정하지 않고 차단한다. 디스크512MiB reserve를 요구하며 Frozen·모델을 복사하지 않는다. 읽을 수 없는 전원/온도 인터페이스는 PASS로 추정하지 않는다.

Housekeeping은 모든 후보 affinity의 합집합 밖에 있는 공통 논리 CPU 하나로 고정한다. SMT sibling이면 물리 P-core를 공유할 수 있다. 별도 CPU가 없으면 기본 차단하고, 사용자가 `--ack-shared-housekeeping`을 명시한 경우 P 대표 하나를 공통 공유한다. 이 간섭은 결과에 포함되므로 별도 검토한다. CPU 수/스레드 수가 실제 CPU 체류시간이나 P-core 전용 Decode를 뜻하지 않는다.

## Linux 단일 실행 명령

저장소 루트에서 실제 경로를 채워 일반 터미널에서 실행한다. 아래 실행 파일/옵션은 구현되어 있다. `/absolute/path/...`는 사용자가 준비한 파일로 바꿔야 하며 자동 다운로드하지 않는다.

```bash
python3 -B tools/sched_ext/new_a_hybrid_autotune/autotune.py run \
  --server /absolute/path/to/frozen/llama-server \
  --model /absolute/path/to/model.gguf \
  --library-dir /absolute/path/to/frozen/libraries \
  --output /absolute/path/to/new-tuning-output \
  --execute --acknowledge-existing-safety-fail
```

사용자가 직접 최초 `sudo -v` 인증을 한다. 이후 동일 controller POSIX session에서 `sudo -N -n`을 사용하며 세션별 Enter 동의를 요구하지 않는다. 자동 인증 갱신·비밀번호 저장·sudoers 변경·root Python controller는 없다. 장시간 인증 만료 없이 완료된다는 보장은 없다.

`--server-sha256`, `--model-sha256`으로 사전 hash를 pin할 수 있다. 없으면 명시한 파일의 최초 hash를 고정하며 그 바이너리가 역사적 Frozen과 같다고 자동 주장하지 않는다. 추가 ELF는 `--elf-root FILE`, 기존 build는 `--build-report FILE`이다. 워크로드는 기존 uncached/nonstream **920/128** 요청이며 `--request JSON`으로 같은 토큰 계약의 요청을 지정할 수 있다. 다른 tokenizer/model이면 실제920토큰에 맞는 요청을 별도 준비해야 한다. cache_prompt=false와 실제 prompt_n920/전체 응답128을 매 요청 검증한다.

Linux에서도 후보별 C Audit/Clean 두 기능 세션을 **별도로** 실행한 후에 성능 표본을 측정한다. 이 기능 요청은 평균에 합산하지 않는다. 기본 탐색2회는 기능 세션 외의 측정 횟수다. Audit의 OpenMP 팀 수/각 index가 완전해야 하고 성능에서는 팀 출력이 OFF여야 한다. `/proc` 실제 TID 수는 별도로 보고하며 Compute/Batch의 값과 동일시하지 않는다.

기본 상한은 총160서버 세션, 인증 후 실행 구간7200초이며 `--max-sessions`, `--max-seconds`로 사전에 변경할 수 있다. 세션은 최대300초, 각 helper는 TERM360초/kill-after10초, HTTP120초 제한을 상속한다. 모델/CPU가 너무 느리면 상한에 걸려 중단될 수 있다. 실패 세션을 표본으로 사용하지 않고 자동 확대/재시도하지 않는다. readiness GET은 추론 표본이 아니다.

## 계획, 결과 및 재개

계획만 읽기 전용으로 확인하려면:

```bash
python3 -B tools/sched_ext/new_a_hybrid_autotune/autotune.py plan --output /absolute/path/to/new-plan-output
```

계획은 성능 검증이 아니다. 실제 `run`에 `--execute`가 없으면 추론을 시작하지 않는다. 완료/중단 결과를 새 추론 없이 재검증·분석하려면:

```bash
python3 -B tools/sched_ext/new_a_hybrid_autotune/autotune.py analyze --output /absolute/path/to/tuning-output
```

`analysis/`에 새 JSON/CSV/한국어 Markdown 파일을 저장한다. 표에는 Decode/Prefill/affinity/두 처리량/HTTP/CPU/n을 표시한다. JSON에는 온도·실제 TID 수·모델/서버 hash·기본 공식·단계별 관측 최고·최저 단일 표본·최종 추천·기본 대비 확증 개선율·제외 시도와 raw 경로가 있다. 최고값은 단계별이며 단계 간 반복을 섞은 순위로 정책을 정하지 않는다.

실행을 중단한 뒤 같은 명령/인자로 `--resume`을 추가하면 완료 표본을 재검증하여 건너뛴다. auth 실패로 native raw의 안전한 미시작/정리가 확인된 경우에만 `--resume-auth`로 새 시도를 명시적으로 승인할 수 있다. 다른 실패는 보존·차단한다. SIGINT/SIGTERM에서도 기존 cleanup을 수행하며, 확인되지 않은 정리·강제 종료·Mask/Tracking/열/Identity 문제 뒤에는 다음 서버를 시작하지 않는다. 커널/Boot/BTF·소스·의존성·기준 변경이면 새 캠페인이 필요하다. 파일/실패 기록을 지우지 않는다.

## NEW_A 재측정

기존 NEW_A FULL만 사용한다. Runtime Lite/PID Lite/Topology Direct/관측기는 포함하지 않는다. 동일 후보라도 **현재 Boot/Thread/affinity/모델/요청/원본 객체에 결합된 새 C/E Audit/Clean4개**가 없으면 Auto-Tuning 인증 전에 차단한다. 전역 strict safety 실패는 별도로 유지한다.

새 기능 입력 준비(attach/추론 없음):

```bash
python3 -B tools/sched_ext/new_a_hybrid_autotune/autotune.py prepare-functional \
  --server /absolute/path/to/frozen/llama-server \
  --model /absolute/path/to/model.gguf \
  --library-dir /absolute/path/to/frozen/libraries \
  --marker-elf /absolute/path/to/frozen/phase-marker-library.so \
  --output /absolute/path/to/new-functional-root
```

`variants/<후보ID>/`에 가능한 적응형 탐색 범위의 독립 Host/Baseline/기능 입력을 만든다. 현재 연구의 Frozen phase marker `llama_scx_phase_v1`이 필요하다. **기능 완료는 NOT_RUN**이다. 후보 ID 목록은 `prepared.private.json`을 확인한다.

사용자가 각 설정의 검증을 별도로 승인한 뒤 기존 명령을 사용한다.

```bash
python3 -B tools/sched_ext/new_a_universal_runtime/runtime.py functional \
  --campaign /absolute/path/to/functional-root/variants/ACTUAL_CANDIDATE_ID \
  --execute --acknowledge-existing-safety-fail
```

모든 가능한 후보의 원시 기능4개가 완료된 뒤, 위 Linux 명령과 같은 파일/환경에 `--scheduler new-a --marker-elf ... --functional-root /absolute/path/to/functional-root`를 추가해 `run --execute`를 실행한다. build report는 기능 준비에서 만든 것을 자동 참조하거나 동일한 `--build-report`를 명시한다. 이 검증량은 적지 않으며 입력 생성만으로 지원/안전성 PASS라고 하지 않는다. 기능 증거 없는 새 CPU/후보를 Auto-Tuner가 자동 attach하지 않는다.

## 검증 범위 및 개인정보

실제 기능/성능, 팀원 CPU, 새 실행기 자체의 계측 영향은 NOT_RUN이다. 기존 native loader와 raw verifier·FULL 진단·Mask/Tracking·객체 hash/실제 program ID/BTF/tag·Partial8·Cleanup 검사를 수정하지 않고 재사용한다. Linux에는 같은 BPF 내부 진단이 없으므로 NOT_APPLICABLE로 구분한다. CPU Time은 요청 구간 **전체 서버 프로세스 CPU clock**이며 BPF runtime_ns나 에너지/RAPL이 아니다. Runtime Statistics와 외부 관측기/RAPL은 OFF다.

모델과 서버는 복사하지 않지만 SHA 검증으로 읽는다. 전체 의존성 해시는 측정 밖에서 확인하므로 준비 시간과 페이지 캐시에 영향을 줄 수 있다. 워밍업/검증 경로는 후보에 공통이지만 과거 실행기와의 성능 동등성을 주장하지 않는다. 통제되지 않은 background/열/순서의 영향이 완전히 제거됐다는 주장도 하지 않는다.

output에는 사용자 경로·Boot ID·CPU/TID·응답 등이 있으므로 자동 GitHub 업로드하지 않는다. 저장소에는 소스/테스트/문서만 공유하고 원시 출력은 검토 후 선별한다.

```bash
python3 -B tools/sched_ext/new_a_hybrid_autotune/test_autotune.py
```
