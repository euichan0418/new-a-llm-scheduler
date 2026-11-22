# 팀원 P6+E8 검증과 6/10 실험 계획

상태: `TEAM_CPU_RUNTIME_NOT_TESTED`. 6/10은 실험적 초기 후보이며, i5-1240P의 4/8 확증 결과를 옮겨 쓴 성능 주장이 아니다. 이 문서는 실제 BPF/LLM을 실행하는 스크립트가 아니다.

## 먼저 확인할 정보

[설정기 사용 절차](hybrid-auto-configuration.md)의 detect/recommend/validate/environment를 일반 사용자로 실행한다. 정확한 CPU 모델, available P=6/E=8, P SMT sibling, online/present/usable 수, CPU mapping, L2/L3 및 NUMA를 확인한다. 20논리 CPU 예상은 SMT가 실제 활성이고 제한이 없을 때만 성립한다. 일부 코어가 offline 또는 cpuset 밖이면 6/10을 강제하지 않는다.

```bash
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config experiments --probe ../new-a-cpu-probe-build/cpu-probe --expect-p 6 --expect-e 8 --format json --output configuration.local/p6e8-plan.local.json
```

CPU 번호는 장비에서 생성한 JSON을 사용한다. 팀원 PC 번호는 이 문서에 하드코딩하지 않는다. compact와 spread 두 L2 배치를 제안할 수 있지만 캐시 효과가 입증된 것은 아니다. 첫 실험은 결정적 compact 후보 B로 고정하고 다른 배치는 별도 가설/캠페인으로 남긴다.

| 후보 | Compute | Batch | Affinity 구성 | 순서 |
|---|---:|---:|---|---|
| A | 6 | 6 | P6 | 참고 기준 |
| B | 6 | 10 | P6+E4 | 최우선 |
| C | 6 | 12 | P6+E6 | 선택적 |
| D | 6 | 14 | P6+E8 | 선택적 |

생성기의 숫자는 실제 가용 P/E에 근거한다. A/B/C/D의 Thread와 Affinity가 함께 바뀌므로 구성 전체의 효과다. Thread 수만의 효과를 분리하려면 같은 P6+E8 mask를 고정하고 6/6, 6/10, 6/12, 6/14를 비교하는 별도 실험을 사용한다. 이 대조군을 B의 주 스케줄러 비교와 합산하지 않는다.

## 첫 핵심 비교

Linux C-B와 NEW_A E-B 모두 Compute6/Batch10/HTTP2 및 **같은 P6+E4 CPU 목록**을 사용한다. 동일 Frozen 서버·GGML·OpenMP·모델·920/128 요청·Context/Batch/Ubatch·GPU offload=0을 유지한다. 내부 llama/GGML 수정 없이 기존 `--threads`, `--threads-batch`, `--threads-http`만 사용한다. CLI의 `server_thread_arguments`는 인자 조각이며 실행 명령이나 Frozen 설정 승인서가 아니다.

기능 검증은 C-B Audit/Clean, E-B Audit/Clean을 새로 수행해야 한다. 과거 i5 증거를 팀원 PC 증거로 재사용하지 않는다. 실제 verifier/attach된 객체 Identity, SCHED_EXT 대상과 Partial Mode, FULL Mask/Tracking/Migration/오류 이벤트, 실제 argv·OMP 팀·TID affinity, 응답 토큰/해시, Thermal, Cleanup 및 최종 sched_ext disabled를 확인해야 한다.

현재 공개 Guard의 고정 16 CPU/mask 및 Portable Runtime의 i5 전용 admission은 새 PC용 실행 준비가 아직 끝나지 않았다는 뜻이다. 설정기 성공만으로 Guard를 삭제·완화하거나 기존 실행기를 20 CPU용이라고 간주하지 않는다. **별도 adapter를 검토하고 기존 안전성 조건을 유지한 기능 검증이 끝나기 전에는 성능 단계로 진입하지 않는다.** 이번 패키지는 실제 attach/실험 명령을 제공하지 않는다.

## 기능 승인 이후의 탐색 설계

- Block 1: C-B, E-B
- Block 2: E-B, C-B
- 독립 서버 4개, 각각 워밍업 2회/측정 1회, 총 HTTP 12회.
- Primary: HTTP 전체 요청 시간. Secondary: 요청 구간 전체 서버 Process CPU Time, Prefill/Decode 처리량·시간, Context Switch, 공통 정의의 Migration, CPU Busy, Thermal.
- Process CPU clock과 BPF runtime, CPU Busy와 실제 작업자 체류시간, 선택된 CPU와 실제 실행 CPU를 구분한다.
- RAPL, 외부 관측기, BPF Runtime Statistics는 OFF. E FULL의 필수 진단은 유지한다.
- 양방향 paired HTTP 변화, CPU Time/Decode 손익과 순서·온도·중단 간격을 함께 기록한다. 조건별 n=2에서 우위/동등성을 선언하지 않는다. 일관된 방향과 수용 가능한 부작용이 있을 때만 별도로 사전 고정한 6개 이상의 독립 대응 블록을 검토한다.
- C/D 및 L2 spread는 자동으로 추가하지 않는다. 각각 새 질문과 충분한 기능 증거가 있어야 한다.

## 실행 전 환경과 안전성

커널/Boot/BTF·호환 kfunc, 정확한 새 Host Profile, 새 Runtime Baseline, Frozen/모델/모든 ELF 의존성 및 환경변수 whitelist, 실제 cpuset/cgroup quota, CPU online/affinity를 고정하고 세션마다 재검증한다. AC 연결, Turbo OFF 고정, governor/Power Profile performance, Thermal 조건을 맞추되 설정기가 자동 변경하지 않는다. 다른 장비의 Host Profile을 복사해서 통과시키지 않는다.

사용자 명시적 실험 승인과 정상 sudo 인증은 미래 실행 계층에서 별도로 처리해야 한다. 비밀번호 저장/keepalive/sudoers 변경 없이 인증 만료 및 기능·Mask·Tracking·열·전원·Cleanup 실패 시 다음 세션을 중단한다. 실패 기록은 남기고 자동 재시도하지 않는다. 성공 슬롯 재개 시 원시 증거를 재검증하며 새 시도 ID를 사용한다. 완료하지 못한 블록을 성공으로 집계하지 않는다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
```

이 상태는 변경되지 않았다. 기능 PASS는 전역 안전성 승인이나 프로덕션 배포 승인이 아니다.

## 결과 기록과 공유

새 캠페인 ID, 코드/빌드/객체 SHA256, Host/Baseline 참조, 실제 후보의 CPU 유형·mask·thread, 측정 순서, 성공/실패/제외 사유를 로컬 원시 증거로 보존한다. HTTP/CPU/Prefill/Decode 등의 블록별 수치와 검증 범위를 별도로 요약한다. i5 과거 평균을 새 캠페인에 합산하지 않는다.

팀원에게 공개할 CSV의 권장 열:

```text
campaign_label,block,position,policy,compute,batch,http_threads,affinity_label,http_ms,process_cpu_s,prefill_tps,decode_tps,prefill_ms,decode_ms,context_switches,migrations,mask_tracking,thermal,cleanup,valid,excluded_reason
```

Boot/Host ID, 개인 경로·전체 환경변수·응답 원문·비밀정보·바이너리·모델은 공개 CSV에 넣지 않는다. 실제 CPU 목록은 로컬 증거에 남기고 공개 시 장비 구성 설명과 함께 별도 검토한다.
