# Intel HWP 전원 적용·복원 수정

## 원인과 확인 범위

팀원 i7-12700H에서 다음 오류가 보고됐다:

```text
Failed to activate CPU driver 'intel_pstate': Error writing energy_performance_preference: Device or resource busy (26)
```

기존 적용은 Profile performance 후 Turbo OFF/Governor performance를 썼지만, 복원은 **원래 Profile을 먼저 요청한 뒤 Governor를 되돌렸다**. Intel HWP의 performance Governor는 EPP를 performance로 고정하고 다른 EPP 쓰기를 거부한다. 따라서 Governor가 performance인 상태에서 PPD가 balanced의 EPP를 쓰면 충돌할 수 있다.

근거는 [커널 intel_pstate 문서](https://docs.kernel.org/admin-guide/pm/intel_pstate.html)와 [커널의 EPP -EBUSY 조건](https://github.com/torvalds/linux/blob/master/drivers/cpufreq/intel_pstate.c)이다. 제공된 오류/수동 복구 상태와 코드 순서를 대조했고, 같은 제약을 넣은 모의 PPD에서 오류를 재현했다. 팀원 원시 로그 파일 자체는 이 개발 PC에 없어 모든 실행 시각/시도의 실제 순서는 직접 재검증하지 않았다. 실제 팀원 전원 적용/복원은 NOT_RUN이다.

## 변경 순서

적용/복원 모두 준비 단계에서만 수행하며 HTTP 요청 구간에 관측 프로세스나 polling을 추가하지 않는다.

1. 현재 Boot/online/policy/driver/EPP scope를 원래 snapshot과 대조한다. 드라이버 모드(active/passive)를 변경하지 않는다.
2. 전환 중 Turbo를 제한한다.
3. Intel active/HWP 정책의 Governor를 **powersave**로 전환해 EPP 잠금을 해제한다.
4. PPD로 대상 Profile을 설정한다. daemon을 정지/재시작하지 않는다.
5. 대상 EPP를 정책마다 설정한다.
6. 대상 Governor를 설정한다.
7. Profile/EPP/Governor/driver/policy의 비Turbo 상태를 검증한다. 명령 실패가 하나라도 있으면 실패로 남긴다.
8. 그 관문을 통과한 경우만 대상 Turbo 상태를 복원한다.
9. 전체 원래값과 두 번의 연속 readback이 일치한 경우만 RESTORED로 기록한다.

적용 대상은 기존 실험 조건(Profile performance, Governor performance, EPP performance, Turbo OFF)이며 복원 대상은 **고정 balanced 값이 아니라 실행 전에 실제 캡처한 값**이다. 예를 들어 사용자가 보고한 balanced / active / powersave20 / balance_performance20 / no_turbo0이면 이 값을 저장하고 복원한다. CPU 수/번호/SMT/L2 공식과 BPF 스케줄링 정책은 변경하지 않는다. 비연속 CPU ID도 같은 정책 단위로 처리한다.

## 저널 v2와 실패 처리

새 snapshot/저널은 모든 Governor·EPP·Turbo·Profile과 CPU 정책/driver/intel_pstate 상태·Boot binding을 기록한다. EPP는 0이나 미수집 값으로 위장하지 않는다. 미지원 EPP driver/mode나 불완전 scope는 자동 설정을 차단한다.

PPD는 일부 정책만 바꾼 뒤 실패할 수 있으므로 첫 쓰기 전에 전체 복원 범위를 기록한다. 실패한 접미 정책을 이전 restored 인덱스만으로 건너뛰지 않는다. 권한 명령의 시작/종료/미확인 종료·부분 stdout/stderr를 보존한다. 별도의 `environment.private.json.apply-*.json`, `environment.private.json.restore-*.json`에 각 시도와 readback을 남긴다. 완료된 실패 시도는 다음 복원 성공으로 덮어쓰지 않는다.

인증 만료·readback 불일치·hotplug/driver/Boot 변경·부분 실패·Cleanup 미확인은 RESTORE_REQUIRED 또는 상위 ENVIRONMENT_RESTORE_REQUIRED다. Profile 이름만 balanced라는 이유나 프로세스 종료 코드만으로 PASS하지 않는다. 비Turbo 복원이 실패하면 Turbo 재활성화를 보류한다. 이 상태를 완전 복원이라고 주장하지 않고 검토/복원 명령을 안내한다. 비밀번호 저장·인증 자동 갱신·sudoers 변경·실패 자동 재시도는 없다.

준비 중 SIGINT/SIGTERM/SIGHUP은 기록과 Cleanup 확인 뒤 복원 경로로 전달한다. SIGKILL/전원 차단/권한 소실까지 무조건 복원을 보장하지 않는다. 정리가 확인되지 않으면 전원 변경부터 진행하지 않는다.

## 이미 수동 복구한 구형 실패

구형 schema1 저널에는 원래 EPP가 없다. 이를 추정해 원래 전원이 완전히 복원됐다고 표시하지 않는다. **원본 실패 저널·root stderr·원시 실험 결과는 수정/삭제하지 않는다.**

업데이트 후 --auto는 미복원 저널을 발견하면 다음을 수행한다:

- sched_ext disabled 및 잔여 server/loader/monitor 부재 확인.
- 기존 저널의 알려진 원래 Profile/Governor/Turbo와 현재 상태 대조.
- 현재의 모든 EPP/driver/policy를 읽어 새 baseline으로 제시.
- 구형 원래 EPP가 NOT_RECORDED라는 점을 표시하고 사용자의 **별도 YES 승인**을 받음.
- 원본 저널 SHA256과 현재 전체 snapshot을 별도 `environment.private.json.manual-baseline-*.json`에 저장.

이것은 **수동 복구 확인 및 새 기준선 승인**이며, 과거 실패를 PASS로 재분류하거나 실패 세션을 자동 재시도하는 행위가 아니다. 승인 파일이 있어도 원본 hash/현재 상태를 다시 검사한다. 알려진 값 불일치, 잔여 프로세스, 승인 거부 또는 불완전 원래 기준선이면 차단한다. v2 저널이면 원래 EPP도 정확히 일치해야 한다. 이전 성공/실패 원시 결과는 새 캠페인 평균에 합산하지 않는다.

## 팀원 재실행 명령

이미 `feature/new-a-universal-runtime`을 Clone한 **그 저장소 디렉터리**에서:

```bash
git pull --ff-only && bash scripts/autotune-bootstrap.sh --auto
```

로컬 source 변경이나 분기 충돌이 있으면 Git이 중단하며 덮어쓰지 않는다. 다른 브랜치의 main으로 이동/merge하지 않는다. 기존 수동 복구 상태가 맞으면 위 승인 절차만 추가로 진행하고, 이후 설치/전원/실험 승인을 정상적으로 받는다. 새 시도는 고유한 `.autotune-work/runs/bootstrap-*/`에 저장되며 기존 자료는 보존한다. 기존 준비 자산 캐시는 hash/input이 일치할 때만 재사용한다. 별도 workspace를 사용했다면 동일한 `--workspace` 옵션을 유지한다.

이미 v2 복원 저널이 있고 직접 복원을 재승인하려면 해당 결과 폴더의 `restore-environment.sh`를 사용한다. 구형 스크립트도 업데이트된 코드를 호출하면 원래 EPP를 추측해서 쓰지 않고 수동 복구 확인을 요구한다. 저널/sidecar를 지워 관문을 우회하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

비특권 모의 PASS와 실제 PPD/커널 전원 복원 성공은 다르다. 이번 수정은 기존 Mask/Tracking/Identity/Cleanup 관문을 유지하며 BPF attach나 성능 실험을 실행하지 않았다.
