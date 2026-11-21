# Runtime Lite: 선택적 elapsed 진단 제거

판정: **RUNTIME_LITE_EXPLORATION_INCONCLUSIVE**. 기존 NEW_A(E) FULL을 대체하지 않는 experimental research candidate다. 기능 검증의 성공과 성능 개선을 구분한다.

## 단일 변경과 진단 계약

[독립 BPF 소스](../../tools/sched_ext/new_a_runtime_lite/llama_new_a_runtime_lite.bpf.c)와 [정확한 Diff](../../tools/sched_ext/new_a_runtime_lite/candidate.diff)는 검증한 후보의 원본 그대로다. 정상 running/stopping 경로의 bpf_ktime_get_ns 읽기, start_ns 기록/reset, elapsed 차이 계산, runtime_ns 누적만 제거했다. 오류 경로의 시각 읽기는 유지한다. 오류 발견 시각과 Callback 진입 시각의 의미 차이도 숨기지 않는다.

CPU 선택·P/E/SMT 우선순위·Local/Shared DSQ·Dispatch·weighted vtime·Partial mode·fallback은 유지한다. 필수 Task Storage/Mask/Tracking/Migration/오류 이벤트 및 시각/drop/export/live monitor 검사도 유지한다. 필수 진단 제거로 안전성 PASS를 만든 후보가 아니다.

```text
instrumentation_mode=FULL_NO_ELAPSED_RESEARCH
runtime_ns=NOT_AVAILABLE
migrations=COLLECTED
```

L의 elapsed는 미수집이며 숫자0이 아니다. E의 태스크 elapsed 누적은 BPF Callback 실행시간도 전체 Process CPU Time도 아니다. 공통 CPU Time은 같은 전체 프로세스 CPU clock 측정으로 별도 수집했다. 진단 JSON만의 validation.py는 full-session 기능 승인 도구가 아니다.

## 기능 검증 범위

Intel i5-1240P,7.0.0-43-generic의 해당 Boot에서 C/E/L 각각 Audit/Clean이 raw PASS였다. E/L의 Mask violation0/Tracking failure0, 실제 BPF 객체 SHA/active callback ID/BTF ID/tag, 실제4/8/2와 Affinity, 응답920/128토큰·해시, thermal·kernel monitor·Cleanup/final disabled를 검증했다. Linux의 BPF 진단은 N/A다. Audit 팀4/8 표시는 정확한 phase별 TID/CPU 체류시간을 입증하지 않는다.

이전 Boot의 proof는 새 Boot에 재사용하지 않았다. 새 Host Profile/V3를 생성하고 기능6개를 다시 실행했다. 최근 Boot에서는 governor가 과거powersave에서performance로 달랐으며 온도 센서 경로도 바뀌었다. 새 캠페인의 C/E/L은 같은 새 설정을 사용했지만 과거6블록과 합산하지 않았다. 이 scoped PASS는 과거 dynamic-affinity strict 실패를 해결하지 않는다.

## 두 블록 성능 탐색

모두Compute4/Batch8/HTTP2, CPU0,2,4,6,8,9,10,11, GPU0, 같은 Frozen/모델/920·128 요청/Context·Batch·Ubatch다. AC/Turbo OFF/performance, RAPL/Wakeup/fexit/Runtime Statistics OFF를 유지했다. E는 FULL, L은 elapsed 없는 필수 진단이다.

| 구성 | Prefill tok/s | Decode tok/s | HTTP s | Process CPU Time s |
|---|---:|---:|---:|---:|
| C48 | 73.2209 | 22.0492 | 18.381604 | 106.737081 |
| E48 | 72.6686 | 22.0877 | 18.462115 | 109.192390 |
| L48 | 72.6993 | 21.9663 | 18.488629 | 109.280614 |

| 블록 | 순서 | 조건 | Prefill tok/s | Decode tok/s | HTTP ms | Process CPU s |
|---|---:|---|---:|---:|---:|---:|
| 1 | 1 | C48 | 73.2407 | 21.4886 | 18524.292 | 107.057104 |
| 1 | 2 | E48 | 72.7883 | 22.2351 | 18402.988 | 109.217904 |
| 1 | 3 | L48 | 72.5003 | 22.1549 | 18473.262 | 109.312356 |
| 2 | 1 | L48 | 72.8984 | 21.7777 | 18503.996 | 109.248871 |
| 2 | 2 | E48 | 72.5489 | 21.9402 | 18521.242 | 109.166876 |
| 2 | 3 | C48 | 73.2011 | 22.6098 | 18238.915 | 106.417058 |

- L/E 평균 HTTP **+26.514ms(+0.1436%)**, CPU **+0.088224초(+0.0808%)**. 감소를 확인하지 못했다.
- L/E Prefill 평균+0.0423%지만 블록별 방향 반전. Decode는 두 블록 모두 감소, 평균-0.5496%.
- L/E HTTP 차이는 블록1+70.274ms, 블록2-17.247ms로 일관되지 않았다.
- L/C 평균 HTTP+107.025ms(+0.5822%), CPU+2.3830%. 같은4/8 Linux 추월도 미입증이다.
- E/C 평균 HTTP+80.511ms(+0.4380%). 이 비교도 블록별 방향이 달랐다.

**개선 미확인이지, n=2로 확정적 악화나 효과0/동등성을 입증한 것은 아니다.** 선택적 진단 비용 제거가 Process CPU 감소를 자동으로 뜻하지 않는다. 현재는 E 유지/L 채택 보류, 즉시6블록 확증도 우선하지 않는다. 미소 효과를 더 정밀하게 규명할 가치가 별도로 있다면 전향적 추가 비교를 설계하되 본2블록을 새 확증에 사후 합산하지 않는다.

## 실패·재개와 자료 제한

첫 r02-L48은 측정 전 Runtime Statistics OFF 명령에서 sudo 비밀번호 요구/종료1로 중단됐다. 측정 응답이 없었고 워밍업2회만 저장됐다. Cleanup PASS 후 사용자가 명시적으로 resume-auth로 새 독립 시도를 실행했고 그 성공만 포함했다. 실패 기록은 로컬 보존하며 유효 성능 표본에 넣지 않았다. 통계OFF 명령 실행 실패를 통계가 켜졌다는 증거로 해석하지 않는다.

유효6세션은 워밍업12+측정6의18응답을 검증했다. 추가 실패 시도의 워밍업2회는 이18개나 통계에 넣지 않았다. 두 블록 사이에는 실패·재인증을 포함한 약17분27초의 유효 세션 간격이 있었다. 순서는 유지됐으나 연속 실험과 동일한 시간 조건이라고 주장하지 않는다. C48 HTTP가 블록2에서285ms 변하는 등 n=2 변동이 있어 원인 분리는 CAUSE_UNDETERMINED다.

[6개 숫자 CSV](../../results/runtime-lite-6.csv) | [대응 비교/변동 JSON](../../results/runtime-lite-summary.json). 공개 CSV 재계산은 비공개 원시 증거 전체를 대체하지 않는다.

[후보 정적 빌드/테스트](../../tools/sched_ext/new_a_runtime_lite/README.md). 로컬 경로·원시 로그·Host Profile·관리자 인증·전체 캠페인 획득 연결은 공개하지 않았으므로 clone만으로 측정 재현이 완료되지 않는다.

STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED.
