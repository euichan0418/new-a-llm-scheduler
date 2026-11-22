# NEW_A 범용 연구 Runtime

이 구현은 CPU 탐지 결과를 **독립적인 동적 FULL Guard 로더**에 연결한다. 기존 E FULL BPF, 기존 Guard, Portable 기본 정책과 연구 원본을 수정하지 않는다. 설정 생성, 정적 빌드, Host admission, 실제 기능 검증은 서로 다른 단계다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
RUNTIME_FUNCTIONAL_NOT_RUN
TEAM_CPU_RUNTIME_NOT_TESTED
AMD_RUNTIME_NOT_TESTED
```

## 지원 계약과 범위

Linux x86_64에서 CPUID와 sysfs가 일치하는 Intel Hybrid는 P 대표 → E → P SMT → fallback 순서를 사용한다. Intel/AMD 동종 코어는 물리 대표 → 빈 tier → SMT 순서다. 물리 코어 키는 package/die/core이며 실제 online/allowed CPU 중 대표 하나를 선택한다. AMD 혼합형·가상화·숨겨진 코어 유형·불완전한 topology는 자동 실행을 차단한다. AMD CCD/L3/NUMA 정보는 보존하지만 CCD 배치 최적화를 주장하지 않는다.

Hybrid의 명시적 실험 프로파일은 Compute=P, Batch=P+floor(E/2), HTTP=2다. 대표 P 전부와 E 절반을 affinity로 제안한다. 동종 코어는 Compute=Batch=사용 가능한 물리 코어 수, affinity는 각 대표 하나다. SMT sibling을 물리 코어 둘로 세지 않는다. Compute 수는 Decode의 P-core 실행을 보장하지 않는다.

범위는 **가능 CPU ID 0..1023, possible CPU 최대 1024개**다. 단일 u64 mask를 사용하지 않는다. 원래 BPF에 존재하던 16개 u64 오류 mask(1024bit)를 그대로 사용하며, Guard 입력은 CPU별 1024byte bitmap 네 개다. per-CPU 통계의 packed slot과 실제 possible CPU ID를 별도로 매핑한다. 비연속 CPU와 63 초과 ID를 모의 검증했다. 해당 대형/비연속 실기기의 verifier·attach 검증은 하지 않았다. 범위 밖 CPU는 차단한다.

## 일반 사용자 준비

의존성: Python **3.11 이상**, x86_64 Linux, sched_ext/BTF 및 필요한 kfunc를 제공하는 커널과 헤더, clang BPF backend, C compiler, bpftool, libbpf/libelf/zlib 개발 파일, binutils, taskset, timeout, powerprofilesctl. 원본 빌드와 같은 현재 커널 BTF를 사용한다. 시스템 패키지 자동 설치는 하지 않는다. ELF 객체 hash 검증은 Linux AF_ALG sha256을 사용하며 미지원이면 실패한다.

저장 공간은 최소 512MiB 여유를 요구한다. 빌드/개인 프로파일/실험 결과는 저장소 바깥의 새로운 디렉터리에 둔다. Frozen·모델·시스템 라이브러리는 복사하지 않고 원본을 읽어 해시를 검증한다.

아래 명령은 저장소 루트에서 실행한다. `U`는 실제 구현된 CLI 경로다. 예제 setup의 절대 경로와 두 SHA256 값은 **자신이 검토한 Frozen/모델 값으로 직접 채워야 한다**. Frozen 서버에는 기존 phase marker 및 Compute/Batch/HTTP 옵션 지원이 필요하며 임의 llama.cpp 바이너리와의 호환성을 주장하지 않는다.

```bash
make test
TASK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/new-a-universal.XXXXXXXX")
U=tools/sched_ext/new_a_universal_runtime/runtime.py
python3 -B tools/sched_ext/new_a_universal_runtime/build.py --output "$TASK_DIR/build"
python3 -B "$U" detect --probe "$TASK_DIR/build/cpu-probe" > "$TASK_DIR/detection.private.json"
python3 -B "$U" recommend --probe "$TASK_DIR/build/cpu-probe" > "$TASK_DIR/configuration.private.json"
python3 -B "$U" validate-config --probe "$TASK_DIR/build/cpu-probe" --config "$TASK_DIR/configuration.private.json"
python3 -B "$U" request-template --output "$TASK_DIR/request.private.json"
cp examples/universal-setup.example.json "$TASK_DIR/setup.private.json"
```

생성한 setup은 미설정 상태로 실행할 수 없다. 자신의 Frozen/모델 경로와 hash, GGML 라이브러리, marker ELF, 환경을 검토한다. Host에는 Kernel/Boot/BTF/CPU/online/allowed/cgroup/전원을, Baseline에는 ELF 해석 경로·의존성·glibc·모델·환경·인자를 기록한다. **개인 프로파일은 공개하지 않는다.**

```bash
python3 -B "$U" inspect-host > "$TASK_DIR/host-inspection.private.json"
python3 -B "$U" prepare --build-report "$TASK_DIR/build/static-report.json" --config "$TASK_DIR/configuration.private.json" --setup "$TASK_DIR/setup.private.json" --request "$TASK_DIR/request.private.json" --output "$TASK_DIR/campaign"
python3 -B "$U" preflight --campaign "$TASK_DIR/campaign"
```

`prepare`가 새 Host Profile과 Runtime Baseline 및 봉인 파일을 함께 생성한다. AC, Turbo OFF, 모든 governor performance, Power Profile performance, 온도 센서 85°C 미만, sched_ext disabled를 실제 확인한 경우만 생성한다. 데스크톱에 AC/Turbo/thermal 인터페이스가 없으면 추측하여 PASS하지 않고 차단한다. CPU quota는 보이는 cgroup 범위만 기록하며 조상 quota의 완전한 확인은 보장하지 않는다. 어떤 센서가 실제 CPU/package 센서인지도 호스트별 검토해야 한다.

Housekeeping은 selected 외 allowed/online CPU 중 가장 큰 번호를 사용한다. 별도 CPU가 없으면 기본 차단한다. `prepare --ack-shared-housekeeping`은 검토 후 같은 CPU 공유를 명시적으로 기록하는 옵션이며 자동 실행 허가가 아니다. C/E 모두 같은 배치를 쓰고 추가 간섭을 평가해야 한다.

## 실제 실행 준비와 순서

다음은 **관리자 권한 및 커널 실험을 별도로 승인한 사용자가 일반 터미널에서 실행할 명령**이다. 이 개발 작업에서는 실행하지 않았다. 기능 검증 4세션(C audit/clean, E audit/clean), 세션마다 독립 서버·워밍업2·측정1·Cleanup을 수행한다. Audit의 OpenMP 팀 출력은 Clean/성능에서 꺼진다. 두 팀의 모든 index가 관측되지 않으면 기능 PASS가 아니다.

```bash
python3 -B "$U" functional --campaign "$TASK_DIR/campaign"
python3 -B "$U" functional --campaign "$TASK_DIR/campaign" --execute --acknowledge-existing-safety-fail
python3 -B "$U" performance --campaign "$TASK_DIR/campaign"
python3 -B "$U" performance --campaign "$TASK_DIR/campaign" --execute --acknowledge-existing-safety-fail
python3 -B "$U" analyze --campaign "$TASK_DIR/campaign"
```

`--execute` 없는 명령은 인증/attach/추론을 하지 않는 preflight다. 성능은 **같은 캠페인/Boot의 네 개 기능 원시 증거**를 모두 재검증해야 인증 전 관문을 통과한다. 순서는 C→E, E→C 2블록이며 서버4/HTTP12회다. 원시 기록은 독립 시도 디렉터리에 보존한다. 주 지표는 HTTP wall time, 보조 지표는 Prefill/Decode·전체 프로세스 CPU clock·Context Switch·외부 Migration·시스템 CPU Busy·Thermal이다. BPF runtime_ns, 안정된 경계 TID의 schedstat와 전체 프로세스 CPU clock은 별개다. HTTP와 프로세스 CPU clock은 같은 HTTP 작업 스레드에서 구간을 기록해 제어기 polling 대기를 제외한다.

결과 상태는 CONFIGURATION_VALID → RUNTIME_STATIC_READY → HOST_PREFLIGHT_PASS → FUNCTIONAL_PASS이며 마지막도 전체 안전성 승인이 아니다. 실제 기능 실행 전에는 RUNTIME_FUNCTIONAL_NOT_RUN, 성능은 PERFORMANCE_NOT_TESTED다. 과거 i5 평균이나 Runtime Lite 자료를 새 캠페인에 합산하지 않는다.

## 인증·실패·재개

일반 사용자 controller가 직접 `sudo -v` 인증을 요청한다. 그 controller의 같은 POSIX session/부모 문맥에서 `sudo -N -n`으로만 권한 작업을 실행한다. 비밀번호 저장, keepalive, sudoers 변경, root Python controller는 없다. 한 인증으로 모두 완료된다는 보장은 없다. 인증 만료이면 즉시 중단하며 자동 재시도하지 않는다.

인증 오류가 원시 stderr로 입증되고 Cleanup PASS, 오류 없는 진단·Identity 등 중단 시점에 필요한 증거가 남은 경우에만 같은 단계의 `--execute ... --resume-auth`로 사용자가 새 인증/새 시도를 승인할 수 있다. 성공 증거는 다시 검사하고 재실행하지 않는다. 다른 실패·불확실한 Cleanup·진단 누락은 차단한다. Kernel/Boot/BTF·library·CPU·cgroup·입력이 바뀌면 새 캠페인을 준비한다. 봉인을 편집하지 않는다.

권한 helper는 단일 세션당 timeout TERM 360초/kill-after10초, 세션 제어는300초, 묶음은1800초 경계 제한이다. 다음 세션 전에 다시 Host와 원시 증거를 검사한다. 실패 시 서버(pidfd) → 로더의 기존 pidfd detach → /dev/kmsg 감시 STOP → FD 정리 및 sched_ext disabled를 확인한다. 강제 SIGKILL·비정상 종료·미확인 Cleanup은 FAIL이며 다음 세션을 차단한다. 외부 sched_ext나 실험 프로세스를 자동 종료하지 않는다.

[동적 Guard와 안전성](runtime-safety-and-validation.md) · [Intel 팀원 안내](intel-hybrid-runtime.md) · [AMD 안내](amd-runtime.md)
