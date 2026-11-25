# Auto-Tuning 인증·재개·전원 분리 게시 기술 보고서

이 보고서는 `euichan0418/new-a-llm-scheduler`의 개선 worktree를 감사하여 공유 브랜치에 게시하는 범위다. 대상은 `autotune-bootstrap.sh --auto`이며 다른 저장소의 Integration V2/6-Arm은 수정하지 않았다. [팀 인계 문서](../AUTOTUNE_AUTH_POWER_ISOLATION_HANDOFF.md), [최종 검증 JSON](../results/autotune-auth-power-isolation-validation.json)을 함께 확인한다.

## 저장소 및 이전 증거

origin은 지정 저장소의 SSH URL, 기준 HEAD는 `dfa450f608854a4e385003641e90a9d40a020721`이다. 원격 `feature/new-a-universal-runtime`와 같은 기준이다. 이전 manifest·전체 패치·소스 및 451개/17 suites 테스트 증거의 일치를 확인했다. 기존 detached worktree의 변경을 보존하여 `feature/autotune-auth-power-isolation`을 만들었다. 원격 main·기존 연구 브랜치와 다른 worktree는 덮어쓰지 않는다.

공개 대상은 runtime 제어·테스트·문서·manifest/provenance다. 모델·Frozen 바이너리·공유 라이브러리·개인 Host Profile·Boot ID·원시 응답·인증/실험 로그·작업공간 snapshot은 제외한다. 이전 로컬 기술 보고서와 테스트 원문은 보존하고 공개본에는 비식별 집계·해시만 기록한다.

## 인증 원인과 수정

기존 비갱신 캐시 검사는 실제 남은 sudo TTL을 반환하지 않는다. 600초 계획 구간과 다음 세션용360초 예약은 관리자 정책의 측정값이 아니다. 마지막 source/raw 검증 중 짧은 TTL이 만료되거나 검사 후 캐시가 무효화되면 실제 `sudo -N -n`이 거절될 수 있었다. 자동 전원 복구의 기본 호출도 재인증 없이 캐시에 의존했다.

새 경로는 경계 및 실제 권한 명령 직전에 PID/PPID/session/TTY와 캐시를 검사한다. 자원이 없는 상태에서만 전경 `sudo -v`를 요청한다. 경계 검증 중 만료와 자원 미시작 상태의 실제 권한 명령 거절에만 제한된 재인증을 허용한다. 활성 세션 중 만료는 비밀번호를 기다리지 않고 Cleanup 후 중단한다. 실제 명령 returncode/stderr와 실패 phase, 재인증 시도는 보존한다. Python 초기 설치의 sudo 파이프라인도 제거했다.

실제 팀원의 sudo 정책·namespace·자연 만료를 검증하지 않았다. 실패 캠페인 raw 없이 정확한 실패 명령을 특정하지 않는다. 세션 중 인증 만료가 불가능해졌다고 보장하지 않는다. 비밀번호 저장·sudoers 수정·background keepalive는 없다.

## 전원 및 기존 안전성

신규 계약은 `USER_MANAGED_UNCONTROLLED_V1`이다. 전원 Apply/Restore 및 과거 Power Journal에 따른 신규 진입 차단을 제거했다. 특정 Governor/Turbo/EPP/Profile/주파수 값이나 AC 여부를 승인 조건으로 요구하지 않는다. 선택적 read-only telemetry의 결측도 차단하지 않는다. 과거 수동 복구 도구와 실패 기록은 그대로 유지한다.

온도85도 미만·throttle 증가 없음, Online/Allowed/cgroup, sched_ext 충돌, FULL Guard·Mask·Tracking·BPF Identity·Statistics OFF·자체 프로세스 Cleanup은 유지한다. E FULL BPF·기본 추천 공식·후보 순서·워밍업2/측정1·HTTP 및 전체 Process CPU Time의 의미를 변경하지 않는다. OS 보호 장치를 변경하지 않으며 NEW_A BPF를 자동 attach하지 않는다. 서로 다른 캠페인의 전원 조건이 같다고 가정하지 않는다.

## 입력 격리·Checkpoint·Resume

검증한 공개 manifest에 바인딩된 소스 snapshot에서 실행하고 개발 checkout의 변경을 다음 세션에 섞지 않는다. 전용 cache의 서버/라이브러리/모델은 읽기 전용 소유자 mode로 보호하며 source·symlink·hash/inode/device·실제 ELF mapping 검사를 유지한다. 같은 UID의 의도적인 변조를 격리하는 보안 경계는 아니며 검출 시 중단한다.

Bootstrap audit/resume는 seal·원래 계약의 성공 raw·Audit/Clean·실패 Cleanup을 재검증한다. 정상 슬롯은 건너뛰고 실패 슬롯은 별도 명시적 승인과 새 시도만 허용한다. 인증 실패와 간섭 제외의 옵션을 구분한다. power/interference/CPU isolation 계약도 raw completion binding에 포함한다. 다른 계약·불완전 Cleanup·Identity 불일치를 이어 붙이지 않는다.

`bootstrap-20261125T151205Z-7233cc74`는 계속 `RESUME_BLOCKED`다. raw가 없으므로 실제 완료 수·실패 명령·Cleanup·복구 증거는 NOT_VERIFIED다. managed-power 또는 다른 source 계약이 확인되면 `NEW_CAMPAIGN_REQUIRED`이며 과거 성공 표본은 과거 결과로 보존한다. 같은 새 frozen 계약의 재개만 모의 검증했다.

## 동시 개발 및 관측 한계

Codex의 존재 자체를 거절하거나 강제 종료/affinity 변경하지 않는다. 후보별 실제 미사용 E-core를 helper housekeeping과 선택적 개발 CPU로 제안한다. 모든 물리 코어 사용 후보에는 여유 코어가 없음을 기록한다. controller affinity도 자동 변경하지 않는다. 공유 cache·memory·전력·열의 완전 분리는 보장하지 않는다.

간섭 관문은 사전 등록한 대상 CPU pre-busy15%, 겹치는 동일 UID 외부 process CPU0.20초/초, 관측1초, 자원 없는 경계 대기30초다. 경계에서는 시작을 보류하며 세션 중 오염이 검출되면 해당 시도를 제외·정리하고 자동 재측정하지 않는다. 짧게 종료된 프로세스·다른 UID·실제 CPU별 귀속은 불완전하다. monitor 자체의 실제 HTTP 영향도 NOT_RUN이다. 동시 개발 지원을 무간섭 성능 증명으로 표현하지 않는다.

## 게시 검증과 Provenance

이전 결과는 451개/17 suites PASS이며 현재 코드에서 같은 회귀 명령을 다시 수행한다. 추가 manifest 도구 검증은 별도5개 테스트다. 최종 결과는 검증 JSON에 분리 기록한다. 범위는 TTL/race 모의, 안전한 재인증·취소, 성공 슬롯 건너뛰기·명시적 재개, 전원 함수 부재, 개발 소스 변경 격리, 바이너리 변조 거절, 가상 부하·보류 및 독립 개발 프로세스 생존이다.

기존 release 검증기를 수정하거나 비활성화하지 않는다. 공식 `make manifest`는 검토하여 Git index에 추가한 파일만 사용한다. 이전 source/kind/hash provenance를 보존하고 원래 금지 파일·secret 패턴·정책 hash 검사에 실패하면 metadata를 복구하여 게시를 차단한다. `make check-manifest`는 읽기 전용이다. 패턴 검사는 모든 비밀 부재의 증명이 아니다.

실제 sudo·BPF attach·모델 다운로드/엔진 빌드·장시간 LLM 추론·성능 측정은 이 게시 검증에서 수행하지 않는다. 첫 실기기 확인은 별도 승인된 짧은 인증·기능·Cleanup·checkpoint 창에서 수행해야 한다. 기본 전체 `--auto`를 단일 세션으로 제한하는 새 옵션은 제공하지 않으므로 이를 짧은 검증 명령으로 오인하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
Real Sudo Validation=NOT_RUN
Real Long-Running Inference=NOT_RUN
Real BPF Execution=NOT_RUN
```

원격 게시 완료는 Push 후 브랜치 HEAD와 파일의 재조회로만 판단한다. PR이나 main 자동 merge는 수행하지 않는다. 안정성 개선을 NEW_A의 Linux 대비 성능 우위나 전체 커널 안전성 문제 해결로 주장하지 않는다.
