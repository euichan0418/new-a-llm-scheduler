# 외부 llama-server 전체 프로세스 CPU Time 수정

기존 공통 runner가 Python `time.clock_getcpuclockid()`를 호출했다. 이 함수는 Python time API가 아니므로 첫 측정 요청에서 AttributeError가 발생했다. Auto-Tuning Linux C와 NEW_A의 Audit/Clean 및 성능 실행은 같은 runner를 사용하므로 모두 수정 대상이다. BPF 정책·FULL Guard·서버·모델의 문제로 분류하지 않는다.

## 측정 정의

- Linux libc `clock_getcpuclockid(pid, &clockid)`를 ctypes로 호출하고 그 ID를 `time.clock_gettime_ns()`에 전달한다.
- 대상은 llama-server TGID의 **전체 프로세스 CPU clock**이다. 모든 스레드의 user+system 실행시간을 포함한다. pthread clock, 호출한 Python 프로세스의 process_time, leader TID의 CPU 시간으로 대체하지 않는다.
- HTTP 요청 직전/직후에 같은 프로세스 clock을 읽는다. HTTP begin/end, Prefill/Decode 서버 timings, `process-cpu.json`의 기존 pid/starttime/clock_id/before_ns/after_ns/before_mono_ns/after_mono_ns/scope와 `process_cpu_s` 필드 의미는 유지한다.
- 프로세스 CPU초는 HTTP wall초, 에너지, BPF callback 실행시간과 다르다. 여러 스레드가 동시에 실행되면 wall초보다 클 수 있다.

공식 계약: [Python time API](https://docs.python.org/3.11/library/time.html), [Linux clock_getcpuclockid](https://man7.org/linux/man-pages/man3/clock_getcpuclockid.3.html), [proc_pid_stat](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html). libc 함수는 errno와 -1 방식 대신 오류 번호를 직접 반환한다. 이를 검사하고 ESRCH/EPERM/ENOSYS 등에서 중단한다. 임의 clock ID 계산이나 fallback은 없다.

## PID 종료·재사용과 독립 검증

`process_cpu.py`는 대상 PID의 pidfd를 열고 starttime/UID를 원래 server identity와 대조한다. clock ID 획득 전후, 각 clock read 전후에 pidfd 생존 및 `/proc/<pid>/stat`의 PID/starttime/UID를 확인한다. 좀비·종료·PID 재사용·읽기 실패를 명시적으로 거부하고 FD를 정리한다. 같은 PID 번호라는 이유로 새로운 프로세스를 측정하지 않는다.

각 clock read를 `/proc/<pid>/stat`의 utime+stime 읽기로 감싼다. USER_HZ tick 양자화 때문에 개별 user/system 항목에 각각 최대1tick의 여유를 두고 clock 값이 proc 범위와 일치하는지 검사한다. proc 값을 고해상도 clock 대체값이나 0으로 쓰지 않는다. 요청 전후 CPU가 증가하지 않거나 증거가 불완전해도 표본을 거부한다.

새 `clock_api`, `clock_verification`은 추가 증거다. 원시 검증기도 동일한 identity/clock/proc/request bracket을 재검증한다. HTTP 전후 경계에서만 proc를 읽으며 요청 중 CPU polling을 추가하지 않는다. 경계 검증에 드는 작은 추가 간격은 존재하며 다른 캠페인 결과와 무비판적으로 합산하지 않는다.

clock 실패 시 `measured-process-cpu-failure.json`에 오류·부분 증거를 남기고 성공 process-cpu/completion을 만들지 않는다. 세션은 `PROCESS_CPU_CLOCK_FAILURE`로 중단하며 Cleanup을 수행한다. Cleanup 오류가 있으면 일반 실패로 남긴다. 인증 실패와 구분하므로 `--resume-auth`로 이 측정을 재시도하지 않는다.

## 테스트 범위

Python 3.11 문법/표준 API를 사용하며 현재 설치된 Python에서 실기 검증한다. Python 3.11 실행 파일이 없으면 해당 버전에서 실행했다고 주장하지 않는다. 추가 패키지를 설치하지 않는다.

가벼운 실제 외부 프로세스가 작업자2개에서 각각 약60ms의 thread CPU 연산을 수행한다. 외부 process clock을 작업자 합계, 자식의 process_time_ns, 독립 `/proc/<pid>/stat`와 비교하고 leader CPU 시간보다 충분히 크다는 것을 확인한다. worker가 끝난 CPU 시간도 전체 프로세스에 포함한다. 실제 외부 프로세스를 쓰되 HTTP는 mock이며 llama-server/LLM/BPF는 실행하지 않는다.

이외 clock API 오류, missing symbol, PID 종료, stale/reused starttime, proc 불일치, FD 정리, CPU 실패 원시 증거 보존, C/E 공통 raw 거부, Cleanup, 전원 복원 성공/실패 및 새 실행 차단을 검증한다. 실제 PID 번호 재사용 강제 실험은 하지 않고 starttime 변경 모의로 검증한다.

```bash
make test
make check
```

## 실패 캠페인과 전원 복원 확인

실패한 캠페인·미완료 세션·전원 저널·이전 보고서는 삭제하거나 성공으로 바꾸지 않는다. 수정 후에는 새 고유 캠페인을 생성한다. 과거 캠페인의 소스 봉인을 편집하거나 이전 기능 증거를 새 측정 계약의 증거로 대체하지 않는다.

팀원 PC에서 현재 코드 저장소의 다음 읽기 전용 명령으로 해당 실행을 확인할 수 있다. 실제 run ID로 바꾼다.

```bash
python3 scripts/check-autotune-recovery.py --run bootstrap-YYYYMMDDTHHMMSSZ-RUNID --current
```

별도 workspace였으면 같은 `--workspace`를 지정한다. v2 복원 저널의 RESTORED 표시와 전체 복원 범위, 마지막 restore 시도의 PASS/오류 없음, 원래 Profile/EPP/Governor/Turbo 및 두 번의 readback을 대조한다. `--current`는 지금 상태도 원래 상태와 비교한다. 상태/Boot가 달라지거나 증거가 없으면 명시적으로 NOT_VERIFIED로 끝나며 실험 승인이 아니다. 과거 실험 실패와 전원 복원 성공은 별개로 표시한다. 전원 변경이 없던 실행은 완전한 원래 snapshot이 있을 때만 NO_POWER_CHANGE_RECORDED로 구분한다.

새 `--auto` 실행도 이전 v2 RESTORED 저널의 실제 증거를 검사하며, 누락/불일치 시 인증/자산 설치/추론 전에 차단한다. 미복원 저널은 기존 수동 복구 확인·사용자 승인 절차를 유지한다. 구형 원래 EPP가 없는 자료는 v2 복원 성공으로 간주하지 않는다. 사용자 확인 없이 실패 기록을 삭제하거나 전원 설정을 바꾸지 않는다.

기존 `feature/new-a-universal-runtime` checkout에서 새 캠페인 시작:

```bash
git pull --ff-only && bash scripts/autotune-bootstrap.sh --auto
```

이 명령은 정상 터미널 승인/sudo를 요청하며 Linux 기본 스케줄러로 시작한다. NEW_A BPF를 자동 attach하지 않는다. 전원 복원 미확인이면 먼저 차단 원인을 검토한다. 이 CPU Clock 수정만으로 실험이나 전원 복원이 성공했다고 주장하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```
