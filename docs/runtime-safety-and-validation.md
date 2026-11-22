# 범용 Runtime 안전성·원시 증거 계약

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

## 원본 보존과 새 Guard

기존 BPF에는 MAX_CPUS1024와16개 u64 오류 mask가 이미 있었다. 고정16 CPU 검증과 i5 mask는 기존 Full Guard monitor/validator와 Portable Runtime adapter에 있었다. 새 패키지는 원본을 편집하지 않고 빌드 출력에 로더/monitor 복사본을 만들어 엄격히 필요한 부분만 주입한다. 소스 drift 시 생성이 실패한다.

동적 Guard는 possible/online/전체 controller allowed/선택된 affinity, controller UID/PID/starttime을 봉인한다. 전체 possible CPU의 정확한 slot/ID를 내보내고0..1023 경계를 강제한다. 선택된 CPU 밖에서 running/stopping/runtime이 있으면 FAIL이다. Mask/Tracking/drop이 하나라도 있으면 FAIL이며 affinity 전환 예외도 없다. Task Storage·Migration·오류 이벤트와 시각·FULL runtime_ns는 그대로 유지한다. 이벤트의 complete mask와 정확한 requested/allowed/regular set을 검사한다. 각 선택 CPU의 실제 running 기능 coverage도 필요하다.

CPU hotplug, allowed/cgroup/affinity 변경, UID/PID 재사용, Partials flags!=8, 필수 callback 누락을 차단한다. 동종 코어의 SMT tier는 후보에는 남지만 대표만 허용한 실험에서 실제 SMT 선택/실행이 나타나면 FAIL이다.

## 객체 Identity와 권한

컴파일 시 BPF ELF SHA256을 로더에 주입한다. 로더는 O_NOFOLLOW로 연 파일의 bytes를 AF_ALG로 hash 확인하고 **같은 bytes를 open_mem**으로 열어 hash 후 경로 교체를 피한다. 실제 load 후 열 개 프로그램ID/BTF/tag를 기록하고 커널 struct_ops dump의9개 callback 및 mapBTF와 대조한다. 실제 verifier/attach 성공은 아직 NOT_RUN이다.

이 패키지는 NOPASSWD helper나 root daemon을 설치하지 않는다. 사용자 소유 로더/입력/소스는 기존 sudo 승인 실험의 신뢰 경계 안에 있다. SHA·봉인은 우발적 변경 검출이며 악의적인 동일 UID를 차단하는 보안 경계가 아니다. 사용자가 검토한 checkout/build만 승인해야 한다. 임의 제3자 입력을 root로 처리하는 권한 위임 도구로 사용하지 않는다. Python 제어/서버/HTTP/분석은 일반 사용자다. root는 로더의 BPF/phase attach, kernel-watch /dev/kmsg, 읽기 전용 stats-off와 bpftool 조회에만 요청된다. taskset 자체는 root가 필요하지 않으며 권한 helper의 housekeeping 배치에 사용한다.

## Runtime Statistics와 관측 OFF

기존 stats_off 검증 의미를 그대로 재사용한다. /proc sysctl0, stats FD reference 없음, enable_stats 호출 없음, callback counter0과ID/tag 고정을 attach후/측정전/후에 확인한다. 명령 실행 실패는 실제 OFF 검증 성공이 아니다. 외부 tracing/raw_tracepoint/tracepoint/kprobe가 있으면 차단하고 원시 inventory를 다시 확인한다. Runtime Lite/PID Lite/Prefill Observer/RAPL은 사용하지 않는다.

## 기능·측정·Cleanup

Audit에서 Compute/Batch 두 OpenMP 팀의 전체 index가 필요하다. Clean은 추가 팀 출력 없이 동일 argv/env·모든 TID UID/정책/정확한 mask·ELF 실제 mapping·920/128응답 및 SHA를 확인한다. 4개 기능 증거가 불완전하면 performance가 인증 전에 차단된다. 응답 hash는 각 응답 bytes의 무결성이지 모든 세션에서 같은 생성 문장이라는 의미가 아니다.

측정은 HTTP 전체 wall time을 우선한다. 전체 프로세스 CPU clock은 BPF runtime_ns와 다른 지표다. 외부 Migration/Context Switch는 요청 전후 모두 살아 있던 동일 TID 집합의 counter 차이다. TID 집합이 다르면 제외한다. CPU Busy는 시스템 전체 범위이므로 서버의 코어 체류시간으로 해석하지 않는다. 매초 Host/열/프로세스를 감시하는 새 제어기의 측정 교란은 아직 검증하지 않았다. 과거 i5 실행기와의 성능 동등성은 미확인이다. CPU Time은 에너지가 아니며 RAPL은 OFF다.

SIGINT/SIGTERM/예외에서도 정리를 시도한다. server pidfd 종료, 로더 종료·detach, 감시 FD 폐쇄 증거, 최종 sched_ext disabled가 모두 있어야 Cleanup PASS다. BrokenPipe를 잡았다는 사실만으로 PASS하지 않는다. 서버/KERNEL watch 조기 종료·root 권한 오류·SIGKILL 필요·미확인 FD/프로세스는 FAIL이며 후속 세션 차단이다. Kernel watch 원시 결과와 상태, log/proof를 보존한다. 외부 프로세스는 자동 정리하지 않는다.

모든 성공 증거 파일 hash와 Host/Baseline binding을 재검증한다. 실패 시도는 성공으로 바꾸거나 덮어쓰지 않는다. 인증 오류의 명시적 재개도 Cleanup·오류 없는 진단과 동일 Host를 요구한다. 부팅/커널/BTF 변경은 독립 새 캠페인과 기능 검증부터 시작한다.

이 계약의 정적·모의 검증은 실제 커널 verifier·attach·전체 안전성 증명이 아니다. 새로운 CPU의 기능 PASS를 받은 뒤에도 기존 strict safety 실패는 자동 해소되지 않는다. 프로덕션 및 야간 무인 실행 승인은 제공하지 않는다.
