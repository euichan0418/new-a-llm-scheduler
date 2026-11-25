# Auto-Tuning 안정성 개선 및 팀 인계

게시 브랜치: [`feature/autotune-auth-power-isolation`](https://github.com/euichan0418/new-a-llm-scheduler/tree/feature/autotune-auth-power-isolation). 기존 main 및 연구 브랜치를 변경하지 않는다. 이 문서는 코드 전달과 단계별 검증 안내이며 실제 장시간 실험 완료 보고가 아니다.

## 수정 배경과 확인 범위

팀원 i7-12700H에서 sudo 인증 만료로 실험이 중단되고 자동 전원 복구도 실패했다. 기존 세션 경계의 캐시 검사는 실제 sudo 유효기간을 보장하지 못했고, 기본 복구 호출은 비대화식 권한 명령에 의존했다. 정확한 팀원 실패 명령을 특정하려면 해당 원시 증거가 필요하다.

`bootstrap-20261125T151205Z-7233cc74`의 원시 자료는 개발 검증 환경에서 확보하지 못했다. 이 캠페인의 판정은 **`RESUME_BLOCKED`**다. 실제 완료 표본 수·Cleanup·복구 증거를 추측하거나 실패를 PASS로 바꾸지 않았다. 사용자 수동 복구 완료 보고와 별개로 원시 증거 감사는 미완료다.

## 기존 동작과 변경

| 항목 | 기존 경로 | 신규 경로 |
|---|---|---|
| 전원 | 승인 후 성능 설정 적용·종료 시 복구 | Governor/Turbo/EPP/Profile/주파수/드라이버 변경 없음 |
| 과거 Power Journal | 복구 증거에 따라 신규 실행 차단 | 신규 진입 조건에서 분리; 과거 수동 복구 도구·기록 보존 |
| sudo | 경계 캐시 검사 이후 명령 실행 | 경계 및 실제 명령 직전 검사; 자원이 없을 때만 전경 재인증 |
| 세션 중 인증 실패 | 중단 및 복구 실패 가능 | 비밀번호를 기다리지 않고 자체 Cleanup 후 중단; 실패 raw 보존 |
| 완료 세션 | 명시적 native resume에 의존 | bootstrap audit/resume로 원시 재검증 후 건너뛰기 |
| 개발 소스 | 실행 source identity 변경 시 중단 가능 | 검증한 고정 소스 스냅샷에서 실행; 개발 checkout 편집과 분리 |
| Codex | 코드/부하 간섭의 구분이 불분명 | Codex 존재 자체는 허용; 개발 프로세스 종료·affinity 변경 없음 |
| CPU 간섭 | 별도 관문 없음 | 사전 등록 관문으로 시작 보류·오염 시도 제외·명시적 재개 |

CPU topology, Online/Allowed/cgroup, sched_ext 충돌, FULL Guard, Mask/Tracking, BPF Identity, Thermal, Statistics OFF, 프로세스 Cleanup은 유지한다. E FULL BPF·기본 추천 공식·후보 순서·워밍업2/측정1·HTTP 및 Process CPU Time의 의미를 변경하지 않는다. NEW_A BPF 자동 attach는 하지 않는다.

## 검증 결과

이전 구현 증거는 **451개/17 suites PASS**다. 게시 검증에서도 같은 회귀 명령을 다시 실행하고 이전 결과와 구분한다. 추가 manifest 도구 검증은 별도의 5개 테스트다. 최종 집계와 범위는 [공개 검증 요약](results/autotune-auth-power-isolation-validation.json), [기술 보고서](docs/autotune-auth-power-isolation-report.ko.md)에 기록한다.

검증은 2초 TTL 및 명령 직전 race 모의, 활성 세션 만료·취소·비인증 오류, 여러 세션의 성공 건너뛰기와 명시적 재개, 전원 미변경·과거 저널 보존, 소스 편집 격리·파일 변조 거절, 가상 CPU 부하·보류 및 개발 프로세스 생존을 포함한다. 실제 외부 프로세스 CPU clock의 짧은 비권한 테스트도 기존 suite에 포함된다. 이는 LLM 벤치마크가 아니다.

```text
Real Sudo Validation: NOT_RUN
Real Long-Running Inference: NOT_RUN
Real BPF Execution: NOT_RUN
Team Campaign Raw Audit: NOT_VERIFIED
```

## 별도 디렉터리에 받기와 첫 점검

기존 checkout을 덮어쓰지 않는다. 아래 새 디렉터리가 이미 있으면 clone이 실패하고 뒤 명령도 실행되지 않는다. 폴더를 삭제하거나 reset하지 말고 다른 새 디렉터리를 선택한다.

```bash
git clone --branch feature/autotune-auth-power-isolation --single-branch https://github.com/euichan0418/new-a-llm-scheduler.git new-a-autotune-auth-isolation && cd new-a-autotune-auth-isolation
```

Python 3.11 이상과 C 빌드 의존성이 있는 환경에서, 추론이나 sudo 없이 먼저 실행한다. 필요한 의존성이 없으면 임의로 다른 도구를 쓰지 말고 누락 항목을 확인한다.

```bash
make test && make test-publication && make check && make check-manifest && bash scripts/autotune-bootstrap.sh --verify-only
```

준비 상태만 추가로 보려면 다음 명령을 쓸 수 있다. 다운로드·설치·엔진 빌드·추론을 하지 않으며 점검 보고서만 만든다. `CHECK_ONLY_NOT_RUNTIME_ADMISSION`과 `disk_sufficient`를 확인한다. 이것은 실험 승인 PASS가 아니다.

```bash
bash scripts/autotune-bootstrap.sh --check-bootstrap
```

## 짧은 실제 인증 검증: 별도 승인 후에만

아래 절차는 이번 게시 작업에서 **실행하지 않았다**. 일반 사용자 전경 터미널에서 자체 서버/로더/관측기가 없고 sched_ext가 disabled인 상태여야 한다. 다른 작업을 종료하거나 root shell로 실행하지 않는다.

다음 명령은 모델·BPF를 사용하지 않고 `sudo -v`, 비갱신 캐시 검사, 명시적 캐시 무효화와 `true` 명령만 검사한다. 비밀번호는 sudo가 터미널에서 직접 받는다. 현재 터미널의 캐시를 무효화하는 `sudo -k`가 포함된다. Python의 stdin이 터미널로 유지되도록 `-c`를 사용하며 pipe나 heredoc으로 바꾸지 않는다. 로그는 고유한 private 폴더에 남긴다.

```bash
python3 -B -c '
import json, pathlib, subprocess, sys, uuid
sys.path.insert(0, "tools/sched_ext/new_a_universal_runtime")
import boundary_auth, controller, runner
root = pathlib.Path(".autotune-work") / ("auth-smoke-" + uuid.uuid4().hex)
root.mkdir(parents=True, mode=0o700)
auth = boundary_auth.BoundaryAuth(root, controller.quiet_boundary)
auth.initial()
with runner.auth_context(auth):
    runner.root_command(["true"], root / "initial-command.json")
subprocess.run(["/usr/bin/sudo", "-k"], check=True)
auth.before_session("auth-smoke-after-explicit-invalidation")
with runner.auth_context(auth):
    runner.root_command(["true"], root / "reauthenticated-command.json")
controller.quiet_boundary()
(root / "result.json").write_text(json.dumps({"status": "AUTH_INVALIDATION_SMOKE_PASS", "natural_expiry": "NOT_TESTED", "inference": "NOT_RUN", "bpf": "NOT_RUN"}) + "\n")
print(root)
'
```

**명시적인 캐시 무효화 검사는 자연적인 15분 만료 검증이 아니다.** 실제 만료 정책·TTY/PPID namespace 검증은 별도 짧은 승인 창에서 자원이 없는 상태로 자연 만료를 기다려 수행해야 한다. sudoers나 TTL은 변경하지 않는다. 실패·취소 시 위 명령은 중단되고 raw 로그를 보존하며 추론은 시작하지 않는다.

## 실제 세션·Checkpoint 검증 계획

긴 `--auto`를 첫 실기기 검사로 시작하지 않는다. 위 모의·인증 점검 뒤 별도 실험 승인 시 다음 순서를 따른다. 이 게시본은 기본 `--auto`를 한 세션으로 제한하는 새 옵션을 제공하지 않는다.

1. 새 PC/Boot의 Host/BTF/Topology/Allowed와 고정 engine/model/build를 확인한다. 다른 PC의 기능 증거를 재사용하지 않는다.
2. 기존 짧은 Audit/Clean 및 Cleanup 검증부터 수행한다. BPF 기능 검증은 별도 관리자 승인 단계이며 이 bootstrap의 자동 실행 대상이 아니다.
3. Linux 단일 세션의 완료 raw·전체 process CPU clock·Thread/Affinity·Thermal·Cleanup을 검토한다. 기본 전체 탐색은 이 검증 전 시작하지 않는다.
4. 자원 없는 경계에서 중단한 뒤, 같은 source snapshot/Host/build/model/rules의 감사가 `RESUME_AVAILABLE`인 경우에만 명시적으로 재개하고 이전 정상 세션이 반복되지 않는지 확인한다.
5. Codex로 별도 개발 checkout의 문서/소스를 편집하면서 고정 snapshot hash가 유지되는지 확인한다. CPU 부하가 큰 개발 작업은 별도의 승인된 간섭 검사로 검증하고 오염 시도를 정상 성능 표본으로 쓰지 않는다.
6. 실행 전후 전원 설정과 명령 저널에 쓰기가 없음을 검토한다. 주파수의 자연 변동이나 외부 전원 데몬의 변경을 프로그램의 제어로 단정하지 않는다. 종료 후 자체 서버/로더/관측기만 정리되고 Codex는 계속 살아 있어야 한다.

현재 코드의 실제 짧은 추론/기능·성능 검증은 미수행이므로 위 계획을 통과했다고 주장하지 않는다.

## 새 Auto-Tuning과 캐시 재사용

별도 실험 승인과 짧은 검증이 끝난 뒤의 실제 전체 탐색 진입점은 다음이다. 기본값은 Linux이고, 준비·설치·실험 승인 및 sudo 입력이 필요할 수 있다. 신규 provisioning은 최소6GiB 여유, 실행 단계는 최소512MiB reserve를 요구한다.

```bash
bash scripts/autotune-bootstrap.sh --auto
```

기존 엔진·모델 캐시가 있는 작업 공간은 복사하지 않고 지정할 수 있다. 아래 경로는 실제 팀원 소유 workspace로 바꿔야 하는 선택 옵션이며 기본 실행의 필수 인자는 아니다.

```bash
bash scripts/autotune-bootstrap.sh --auto --workspace /path/to/existing-autotune-workspace
```

캐시는 source revision/tree·옵션·CPU/컴파일러·서버/라이브러리·모델 size/SHA256/GGUF metadata가 모두 일치할 때만 재사용한다. 불완전 빌드·부분 다운로드·해시 불일치는 보존·차단한다. 결과는 `runs/bootstrap-고유ID/tuning/` 아래에 추가되며 기존 폴더를 덮어쓰지 않는다. 이전 데이터 파일을 임의로 새 workspace에 복사하여 재개하거나 다른 캠페인의 통계에 합산하지 않는다. 새 머신의 바이너리가 역사적 Frozen과 byte identity를 갖는다고 가정하지 않는다.

## 감사와 명시적 Resume

다음은 구현된 옵션이다. 새 실험 요청이 아니라 이후 승인 절차를 위한 안내다.

```bash
bash scripts/autotune-bootstrap.sh --audit-campaign /path/to/bootstrap-CAMPAIGN
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN --resume-auth
bash scripts/autotune-bootstrap.sh --resume-campaign /path/to/bootstrap-CAMPAIGN --resume-interference
```

감사는 읽기 전용이다. 재개는 같은 frozen snapshot과 원시 Identity/Cleanup을 확인하고 별도 사용자 승인을 요구한다. 인증 실패와 간섭 제외는 서로 다른 명시적 옵션을 요구하며 다른 오류·불완전 Cleanup은 차단한다. 과거 managed-power 또는 다른 source 계약이면 `NEW_CAMPAIGN_REQUIRED`이며 원본을 편집해 이식하지 않는다. 대상 기존 캠페인 `bootstrap-20261125T151205Z-7233cc74`는 증거 감사 전 계속 **`RESUME_BLOCKED`**다.

## 동시 개발의 한계와 안전성

사용하지 않는 E-core가 있으면 helper housekeeping과 선택적 개발 CPU를 제안한다. 기존 Codex affinity를 자동 변경하지 않는다. 모든 물리 코어를 쓰는 후보에는 여유 코어가 없음을 기록한다. 공유 캐시·메모리·전력·발열, 다른 UID·짧게 종료된 프로세스 등은 완전히 분리/계측하지 못한다. 간섭 관문은 대상 CPU pre-busy15%, 겹치는 동일 UID 외부 CPU0.20초/초, 관측1초, 안전한 경계 대기30초로 사전 등록한다. 감시 자체의 실제 HTTP 비용도 NOT_RUN이다.

사용 중인 전원 환경을 바꾸지 않으므로 캠페인 간 동일 전원 조건을 보장하지 않는다. 안정성 개선은 스케줄러 성능 우위나 무간섭 성능 측정, 전체 커널 안전성 승인이 아니다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

## 게시 무결성 갱신 절차

검토한 공개 파일만 명시적으로 Git index에 추가한 뒤 `make manifest`를 사용한다. 이 공식 갱신 명령은 Git index의 파일 목록만 사용하고 이전 provenance를 보존하며 원래 `verify_release.py`의 금지 파일·비밀 패턴·정책 해시 검사를 그대로 실행한다. 실패 시 packaging metadata를 되돌리고 게시를 차단한다. `make check-manifest`는 변경 없이 정합성만 확인한다. 검사 비활성화나 원시 자료를 추가해 해결하지 않는다.
