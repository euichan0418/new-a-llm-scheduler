# GitHub에서 시작하는 Intel Hybrid Auto-Tuning

`time.clock_getcpuclockid` 오류가 있었다면 [전체 프로세스 CPU Clock 수정 및 복원 확인](process-cpu-clock.ko.md)을 확인한다. 실패 캠페인은 보존하고 최신 코드에서 새 캠페인을 시작한다.

전원 복원에서 intel_pstate EPP busy 오류가 있었다면 [전원 적용·복원 v2 및 수동 복구 안내](power-restore.ko.md)를 먼저 확인한다. 원본 실패는 보존하고, 새 실행에서 수동 복구 상태를 별도로 승인한다.


기존 NEW_A/Frozen/모델/라이브러리가 없는 PC를 위한 소스 기반 준비 경로다. 기본은 **Linux 기본 스케줄러의 실행 구성 탐색**이며 NEW_A BPF를 자동 attach하지 않는다.

```bash
git clone --branch feature/new-a-universal-runtime https://github.com/euichan0418/new-a-llm-scheduler.git && cd new-a-llm-scheduler && bash scripts/autotune-bootstrap.sh --auto
```

동명 폴더가 있으면 git clone이 오류를 내며 `&&` 뒤 작업은 진행하지 않는다. 기존 폴더를 자동 삭제/덮어쓰기/reset하지 않는다. 새 빈 부모 디렉터리에서 실행한다. git/bash와 Ubuntu 기본 명령은 선행 조건이다. 서버·모델·library/output 경로를 찾거나 입력할 필요가 없다.

## 자동 준비 순서

1. 일반 사용자/SCHED_OTHER, 배포 소스 SHA256, 저장 공간을 확인한다.
2. Python3.11 이상이 없으면 Ubuntu 저장소 기본 버전을 확인하고 계획 승인 후 설치한다. 배포판 기본 Python이 너무 오래되면 OS/PPA를 자동 변경하지 않는다.
3. C/C++ compiler, CMake, clang BPF, bpftool, binutils, libbpf/libelf/zlib 개발 파일, 현재 커널 헤더, powerprofilesctl 등을 검사한다. 설치 계획·새 의존성의 디스크 사용·변경 범위를 출력하고 YES 승인 후 설치한다. 기존 패키지 업그레이드/제거·저장소 추가·커널 교체·sudoers 변경은 하지 않는다. 설치 패키지는 종료 후에도 남으며 자동 제거하지 않는다.
4. AC와 sched_ext 지원 커널을 요구한다. 기본 Ubuntu 커널이 필요한 기능을 제공하지 않으면 명확히 차단하며 새 커널을 자동 설치하지 않는다.
5. 검증된 소스/모델 URL·리비전·라이선스를 표시한 뒤 별도 준비 승인을 받는다. 사용자 소유 코드로 root shell을 실행하지 않는다. 빌드와 다운로드는 일반 사용자다.
6. 고정 Frozen C 소스를 GitHub에서 shallow/partial fetch하고 tree identity를 확인한다. 모델/vocab/docs/test/example 디렉터리는 sparse 제외하고 빌드 소스는 유지한다. 임의 main/latest 엔진을 사용하지 않는다.
7. 원래 Release/CPU/native/OpenMP 옵션으로 서버를 **직렬 빌드**한다. phase symbol, CLI, ELF 의존성과 프로젝트 라이브러리 위치를 검증한다. source/options/compiler/CPU/산출물 해시를 기록해 재사용을 검증한다.
8. 고정 모델 URL에서 가중치를 받고 파일 크기·SHA256·GGUF architecture/name을 확인한다. 잘못된 파일을 덮어쓰거나 다른 모델을 선택하지 않는다. 부분 다운로드/불완전 빌드는 보존하고 자동 재시도하지 않는다.
9. 실제 CPU P/E/SMT/Online/Allowed/L2를 탐지한다. 기본 구성과 실험 상한을 출력하고 실험 승인을 받는다.
10. 원래 전원 상태를 기록한 뒤 승인받은 경우만 Turbo OFF, governor/profile performance를 설정한다. AC/열·커널 조건을 우회하지 않는다. 일반 터미널의 정상 sudo 인증만 사용한다.
11. 기존 Linux Audit/Clean 기능 검증을 후보별로 먼저 수행한다. 실제 토큰·OpenMP 팀·Thread/Affinity·응답 hash·Thermal·Runtime Statistics OFF·Cleanup 증거가 없으면 성능을 시작하지 않는다.
12. Prefill/Decode 탐색·조건별 분석·사전 관문의 독립 확증·최종 추천을 수행한다. 정상/실패/인터럽트 뒤 전원 복원을 검증한다.

설치·준비·실험 승인은 구분된다. sudo 비밀번호를 저장하거나 주기적으로 인증을 갱신하지 않는다. 한 번의 인증으로 장시간 완료된다고 보장하지 않는다. 인증/측정/정리 실패 시 다음 세션·자동 재시도를 차단한다. 복원이 실패하면 `restore-environment.sh`를 안내하고 새 자동 실험을 차단한다. 남아 있는 server/loader를 자동으로 죽이거나 정리 PASS를 조작하지 않는다.

## 정확히 무엇을 받는가

[소스/모델 고정 manifest](../config/autotune-provision.json)를 참고한다.

- 엔진: [codinghuman0/llama-scheduler-base, c8b9d61d12b4997d902a03e5dc55a185d18c3bc9](https://github.com/codinghuman0/llama-scheduler-base/commit/c8b9d61d12b4997d902a03e5dc55a185d18c3bc9). 기존 연구 Frozen C의 정확한 리비전이며 compute profile/phase hint 변경이 이미 들어 있다. 상이한 팀원 연구의 최신 A/R/P 엔진으로 대체하지 않는다.
- 모델: [Unsloth DeepSeek-R1-Distill-Qwen-1.5B-GGUF 고정 리비전](https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-1.5B-GGUF/tree/3cb4d15544a2a5e07439592b9a0965b6445fbd34), 파일 `DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf`.
- 크기: **1,117,321,312 bytes**. SHA256: `f3bdf9cf31dee4b57ae4e455a1cb0d01b5c2c1b50d72d3112141c195506c2840`.
- 배포 모델 카드의 라이선스는 Apache-2.0, [상위 DeepSeek 모델](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B)은 MIT 표기다. Qwen의 Apache-2.0 계보도 유지한다. GGUF 내부의 MIT 메타데이터만으로 배포 카드 조건을 지우지 않는다. manifest의 라이선스 원문 링크를 준비 승인 때 확인한다. 모델/바이너리를 이 저장소에 재배포하지 않는다.

## 정책 선정

기본값은 Compute=P, Batch=P+floor(E/2), HTTP2, P대표+E절반 compact L2, SMT제외다. 설정 수가 실제 P-core 전용 Decode 실행을 보장하지 않는다.

Prefill grid는 Compute=P를 고정하고 Batch와 affinity를 함께 바꾸는 구성 전체 비교다. Decode grid는 선택한 Prefill의 Batch/mask를 고정한다. 기본 공식 대조군을 계속 포함한다. 후보별2블록은 선별용이며 최대2후보+기본값을 새 최소6대응블록으로 추가 비교한다. 탐색과 확증 자료를 합산하지 않는다.

사전 설계 기본 기준은 HTTP 최소2% 개선, phase 시간 회귀 상한1%, Process CPU Time 증가 상한0%다. 기존 Auto-Tuning의 대응 CI/반복 일관성 관문을 유지하며 미미하거나 불확실한 결과는 기본값 유지다. 동등성/전역 최적값을 증명하지 않는다. 관측상 Prefill·Decode·HTTP·CPU 최고 설정, 최고 단일 표본, 최종 추천을 별도로 보고한다. Linux 결과를 NEW_A BPF 정책 자체의 우위로 바꾸어 설명하지 않는다.

## 저장 공간·결과·재사용

시작 시 **최소6GiB 여유**를 요구한다. 이는 source/Git objects/build/1.117GB 모델/원시 증거를 위한 보수적 준비 관문이다. 설치 계획은 별도로 새 패키지 크기+512MiB reserve를 요구한다. 실제 peak는 툴체인/CPU/로그 양에 따라 달라지므로 더 여유를 확보한다. 기존 연구 자료를 삭제하거나 Frozen/모델을 중복 복사하지 않는다.

기본 작업 공간은 저장소의 **`.autotune-work/`**이며 Git에서 제외한다. 큰 파티션을 지정하려면 `--workspace /actual/large-volume/work` 한 옵션을 사용할 수 있다. 필수 옵션은 아니다.

```text
.autotune-work/
  engine-source/                 # 고정 sparse Git checkout
  engine-build/                  # 소스/옵션/CPU/컴파일러 proof와 산출물
  model-cache/                   # 검증된 GGUF 및 다운로드 provenance
  runs/bootstrap-UTC-식별자/
    REPORT.ko.md                 # 준비/실험/복원 상태
    bootstrap-result.private.json
    environment.private.json, restore-environment.sh
    tuning/analysis/*.md, *.csv, *.json
    tuning/raw/, tuning/variants/, tuning/records/
```

매 시도는 고유 디렉터리를 생성한다. 캐시는 source/recipe/compiler/CPU/hash를 확인한 경우만 재사용하며 다른 CPU/툴체인의 native 객체를 무심코 재사용하지 않는다. 새 엔진은 같은 소스의 기능 호환 재빌드이며 과거 Frozen의 byte identity/성능 동등성을 주장하지 않는다. 새 Host/Baseline을 현재 PC/Boot/엔진으로 기록하고 기존 실험과 합산하지 않는다. 실제 모델 로딩·요청 호환성은 Linux 기능 세션에서 검증한다.

## 추론 없이 확인

```bash
bash scripts/autotune-bootstrap.sh --check-bootstrap
make test
```

check는 의존성 계획/고정 recipe/디스크 상태를 저장하며 다운로드·빌드·설치·추론하지 않는다. CPU 계획만 보고 싶으면 `python3 -B tools/sched_ext/new_a_hybrid_autotune/autotune.py plan --output /new/external/plan`을 사용할 수 있다. 작은 CPUID helper만 빌드한다.

소스/모델 준비까지만 승인하려면:

```bash
bash scripts/autotune-bootstrap.sh --prepare-only
```

이 명령은 엔진 빌드 및 큰 모델 다운로드를 실제로 수행할 수 있으나 추론하지 않는다. `ASSETS_PREPARED_FUNCTIONAL_NOT_RUN`과 `COMPLETE`를 구분한다. 필수 자산 준비 없이 실험 완료 메시지를 출력하지 않는다.

## 실제 검증 범위

최종 구현 검증 요약은 [bootstrap validation](../results/autotune-bootstrap-validation.json)에 구분한다. 모델 다운로드 URL/metadata 확인, 모의 전송 테스트, 실제1.117GB 전체 전송은 서로 다른 검증이다. 실제 verifier/attach·모델 로딩·성능을 실행하지 않았다면 NOT_RUN이다. 다른 CPU에서의 성능은 보장하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

NEW_A 기능 모드는 기존 [범용 Runtime](universal-runtime.md)의 새 C/E Audit/Clean·Identity·Mask/Tracking·Cleanup 관문을 별도로 만족해야 한다. 이 bootstrap은 자동 BPF 실험 승인이 아니다.
