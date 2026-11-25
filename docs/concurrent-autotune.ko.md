# Codex 개발과 Auto-Tuning의 분리

이 문서는 전원 비관리 신규 캠페인의 계약이다. 기존 E FULL BPF, FULL Guard, 워크로드 및 추천 기준은 변경하지 않는다. Linux 설정 탐색은 NEW_A의 Linux 대비 정책 우위를 증명하지 않는다. 실제 팀원 장시간 실행은 아직 검증하지 않았다.

## 고정 실행 공간

`scripts/autotune-bootstrap.sh`는 먼저 공개 manifest의 모든 파일을 검증하고 `.autotune-work/source-snapshots/<manifest SHA256>/`에 소스만 복사한다. Git 객체, 모델, 바이너리, 원시 실험은 복사하지 않는다. 이후 Python controller는 이 스냅샷에서 실행한다. 개발 checkout을 편집하거나 Git 작업을 해도 다음 세션에 변경을 섞지 않는다. 부분 스냅샷과 무결성 실패는 보존하고 차단한다.

고정 엔진 소스와 빌드는 전용 workspace, 모델은 전용 cache, 결과는 고유 `runs/bootstrap-.../tuning`에 둔다. 서버·공유 라이브러리·모델은 소유자 읽기 전용으로 바꾸고 기존 파일·실행 mapping·의존성 해시 검증을 유지한다. 이는 실수로 덮어쓰는 것을 막는 장치다. 같은 UID의 의도적인 chmod/변조를 격리하는 보안 경계는 아니다. 변조가 검출되면 중단하며 실행 바이너리를 자동 대체하지 않는다.

## CPU 자원과 개발 공존

Codex, 편집기, Git 프로세스의 존재 자체를 차단하지 않는다. 실제 서버·스케줄러·관측기 충돌 검사는 유지한다. 개발 프로세스를 종료/정지하거나 affinity를 자동 변경하지 않는다.

후보별 실제 topology에서 사용하지 않는 E-core를 구한다. 남는 E-core 중 결정적으로 마지막 CPU를 helper housekeeping에 배정하고 다른 남는 E-core는 선택적인 개발 CPU로 제안한다. i7 P6+E8의 탐지 결과가 기존 사례와 같다면 6/10은 E16~19가 남고 helper는19, 개발용 제안은16~18이다. 다른 CPU에서는 실제 탐지 목록을 사용한다. 전체 E-core 후보에는 여유 물리 E-core가 없으므로 기존 허용 housekeeping을 쓰고 `physical_isolation=NOT_AVAILABLE`로 기록한다. Controller와 기존 Codex의 affinity는 변경하지 않는다.

독립 CPU를 제안해도 개발 도구를 자동 이동시키지 않는다. 사용자가 직접 별도 개발 셸을 해당 CPU에 제한할 수 있지만 이 도구는 실행하지 않는다. 공유 L2/L3, 메모리 대역폭, CPU 전력 예산, 발열에 따른 간섭은 남는다. 일반적인 편집과 가벼운 대화는 공존할 수 있지만 빌드·테스트·다른 추론의 무간섭을 보장하지 않는다.

## 사전 등록된 간섭 관문

`interference.DEFAULT`를 신규 tuning 계약에 봉인한다. 이는 새로운 연구 설계 기본값이며 경험적으로 무간섭을 보장하는 수치가 아니다. 결과를 본 뒤 바꿀 수 없다.

| 항목 | 사전 기준 |
|---|---:|
| 시작 전 대상 CPU별 busy 상한 | 15% |
| 대상 affinity와 겹치는 동일 UID 외부 프로세스 CPU 합계 | 0.20 CPU초/초 |
| 집계 간격 | 1초 |
| 자원 없는 경계의 대기 상한 | 30초 |

경계에서는 실제 대상 CPU busy와 외부 PID/starttime/affinity/프로세스 CPU clock을 `/proc`에서 읽는다. 부하가 높으면 서버/BPF를 시작하지 않고 기다린다. 30초까지 해소되지 않으면 `CPU_INTERFERENCE_BOUNDARY_PAUSED`로 종료하며 raw와 경계 로그를 유지한다. 자원이 없는 상태의 보류에서 부하가 해소되면 승인 범위와 시간 상한 내에서 진행할 수 있다.

세션 중에는 외부 프로세스 CPU 사용률을 집계한다. 기준 초과는 `CPU_INTERFERENCE_DETECTED`로 기록하고 Cleanup 후 해당 시도를 제외한다. 자동 재측정하지 않는다. 정상 completion으로 승격하거나 실패 파일을 덮어쓰지 않는다. 측정 중 발견한 간섭은 이미 계산한 HTTP 결과까지 정상 표본으로 인정하는 근거가 아니다.

같은 UID이며 두 표본에 모두 존재하는 프로세스의 집계다. 새로 생기거나 짧게 종료된 프로세스, 다른 UID의 실행량, 실제 CPU별 체류시간과 캐시·대역폭 간섭은 완전하게 식별하지 못한다. 겹치는 affinity로 계산한 외부 사용량은 실제 대상 CPU 실행의 정확한 귀속이 아니므로 보수적인 오탐도 가능하다. 주파수/Governor/EPP/Turbo는 가능한 인터페이스에서 선택적으로 읽어 기록하며 결측·특정 값이 승인 실패를 만들지 않는다. 전원 데몬과 power profile 조회는 필수 의존성이 아니다.

이 가벼운 감시도 CPU 비용이 있다. 실제 HTTP·CPU Time 영향은 `NOT_RUN`이며 기존 Prefill 관측기의 오버헤드 실패를 해결했다고 주장하지 않는다. 결과는 이 관측 계약을 포함한 새 캠페인의 범위로 해석한다. 최종 성능 확증 전에 감시 자체의 영향도 별도로 검토해야 한다.

## 전원 비관리

신규 bootstrap에서 Governor/Turbo/EPP/Profile/주파수/드라이버를 변경하지 않는다. 적용·복원·전원 저널과 과거 저널에 따른 신규 차단이 없다. OS의 과열 보호를 변경하지 않으며 온도 85도 미만·throttle 증가 없음 검사와 CPU Online/Allowed/cgroup/Mask/Tracking/Identity/Cleanup 검사는 유지한다.

전원 상태는 캠페인 간 동일성을 보장하지 않는다. 서로 다른 캠페인을 동일 전원 환경으로 간주하여 합산하거나 정책 효과를 해석하지 않는다. 과거 `environment.private.json` 및 복구 실패는 변경하지 않는다. 과거 수동 복구는 `--restore-environment <기존 저널>`이라는 별도 명시적 도구로 남으며 새 실험이 자동 호출하지 않는다.

## 인증과 재개

시작과 안전한 경계에서 사용자 전경 `sudo -v`를 사용한다. 실제 권한 명령 직전에도 동일 Python PID/PPID/session/TTY에서 `sudo -N -n -v`를 확인한다. 경계 검사 직후 만료되면 자원이 없을 때만 제한된 재인증을 허용한다. 이미 서버/스케줄러/monitor를 시작했다면 인증 요청 없이 Cleanup 후 중단한다. Sudo TTL 자체를 알아내거나 세션 중 만료를 완전히 방지할 수 있다고 주장하지 않는다.

비밀번호 저장, sudoers 변경, background keepalive는 없다. 권한 명령 자체의 실패와 인증 실패를 구분하고 원래 returncode/stderr 및 실행 단계를 기록한다. 실패한 측정을 자동 재실행하지 않는다.

아래는 인터페이스 문서이며 이번 개발 작업에서 팀원에게 실제 실행을 요청하는 것은 아니다.

```bash
# 새 캠페인: 준비·추론을 실행할 수 있는 명령이므로 별도 승인 필요
bash scripts/autotune-bootstrap.sh --auto

# 기존 캠페인 읽기 전용 감사
bash scripts/autotune-bootstrap.sh --audit-campaign /path/to/bootstrap-CAMPAIGN

# 안전한 경계 보류 후, 실패 시도 없는 동일 계약 캠페인의 명시적 재개
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN

# Cleanup이 확인된 인증 실패 슬롯을 새 시도로 재개하는 명시적 승인
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN --resume-auth

# Cleanup이 확인된 간섭 제외 슬롯의 별도 승인
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN --resume-interference
```

재개는 최초 manifest에 해당하는 기존 고정 스냅샷으로 돌아간다. Kernel/Boot/BTF/Topology/Allowed/빌드/서버/모델/의존성/추천 규칙이 같고 모든 성공 raw가 재검증되어야 한다. 정상 세션은 건너뛰며 실패 시도는 그대로 남긴다. 인증 실패·간섭 실패 각각의 명시적 옵션을 요구한다. 다른 실패, Cleanup 불확실, 증거 변조는 `RESUME_BLOCKED`다.

과거 전원 관리 계약 또는 다른 소스 계약은 `NEW_CAMPAIGN_REQUIRED`이며 자동 변환하지 않는다. 과거 성공 표본은 과거 계약의 기록으로 보존하고 새 평균에 합산하지 않는다. 팀원 캠페인 원시 자료가 없으면 완료 개수나 Cleanup을 추정하여 재개 승인하지 않는다.

## 비권한 검증

`make test-bootstrap test-universal test-autotune test-power-state`는 실제 sudo/BPF/LLM 없이 실행한다. 2초 TTL, 최종 검사 중 만료, 실제 권한 명령 직전 race, 세션 중 만료, 거부/취소, 여러 세션의 명시적 재개와 중복 실행 방지, 전원 경로 부재, 과거 저널 보존, 개발 스냅샷/바이너리 보호, 모의 CPU 간섭/보류, 독립 개발 프로세스 생존을 검사한다.

모의 성공은 팀원 실제 sudo 정책·TTY namespace·실제 장시간 추론·실제 성능 영향 검증을 대체하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
RUNTIME_FUNCTIONAL_NOT_RUN
TEAM_CPU_RUNTIME_NOT_TESTED
```
