# 팀원 인수인계 및 연구 상태

## 현재 선택

- 최종 스케줄러: **NEW_A(E) FULL**. 기존 소스/안전성 검사를 보존한다.
- i5-1240P에서 테스트한 성능 우선 구성: **Compute4 / Batch8 / HTTP2, CPU0,2,4,6,8,9,10,11**.
- Portable 기본 정책: 사용 가능한 물리 P-core 수 기반, 이 PC에서는4/4 P-only. 변경하지 않았다.
- 동일4/8 Linux 대비: **SCHEDULER_SUPERIORITY_NOT_PROVEN**. 우위와 동등성 모두 미확정.
- Runtime Lite: 구현·정적 검사·scoped Audit/Clean 완료, **RUNTIME_LITE_EXPLORATION_INCONCLUSIVE**, E 대체/즉시 확증 보류.

## 완료와 미완료

| 연구 | 실제로 확인한 범위 | 한계/판정 |
|---|---|---|
|4/8 vs8/8 확증|4조건*6블록24유효 세션, E48의 HTTP/CPU 감소6/6|실행 구성 성과; 스케줄러만의 우위 아님|
|같은4/8 Linux/E|HTTP 차이+32.66ms, 대응95%구간0포함|우위/동등성 미입증|
|Portable|Intel Hybrid/동종 Intel/동종 AMD topology·설정 및 모의 테스트|기본값 유지; i5 외 실제 BPF/추론/성능 NOT_TESTED|
|MANUAL/AUTO|별도2블록 및4블록|성능 동등성 NOT ESTABLISHED; 합산하지 않음|
|Runtime Lite|실행시간 누적만 제거, 새 Boot Audit/Clean,2블록|개선 미확인; E 유지|
|에너지|과거 탐색 RAPL|절감 미입증, OVERHEAD_REVIEW_REQUIRED; 최신 성능에서는OFF|
|V2/V3/V4|기존 탐색 후보 이력 로컬 보존|최종 기본 정책 아님; 이름만 바꿔 자동 재시도하지 않음|

## 다음 연구 우선순위: 모두 아직 구현/개선 미검증

1. **PID/TGID 중복 읽기 제거 가능성 검토.** llama_select_cpu와 pick_idle_from_list의 중복 조회가 실제 최적화된 BPF 명령에도 남아 있는지 먼저 확인한다. 컴파일러가 이미 제거했다면 수정 근거가 없다. E FULL 기준에서 한 가설만 독립 후보로 검토하며 개선을 확정하지 않는다.
2. **Prefill 작업자 배치 병목 관측.** P/E 실제 실행 분포, 동기화, Migration, Shared DSQ 대기, 재개 지연을 구분한다. 현재 단계별 TID/CPU 체류시간·barrier/runnable 대기 분해는 충분하지 않다. fexit 등 과거 관측 왜곡을 고려해 계측 ON/OFF 비용부터 검증한다.
3. **E-core L2 그룹 배치.** E8-11과E12-15의 공유 L2 그룹 관측을 바탕으로 집중/분산 배치를 별도 비교한다. 캐시 미스/대역폭 미측정 상태에서 분산이 빠르다고 가정하지 않는다. Affinity가 바뀌므로 scheduler-only 비교와 구분한다.

이번 공유에서는 새 정책을 개발하지 않았다. Runtime Lite 실패를 이유로 자동으로 여러 버전을 만들지 않는다. 향후 비교의 Primary는 HTTP이고 Prefill/Decode 및 전체 Process CPU Time을 함께 평가한다. CPU Time은 에너지가 아니다.

## 팀원이 먼저 할 일

1. README와 safety/재현 문서를 읽고 세 비교(E48/E88, E48/C88, E48/C48)를 구분한다.
2. make test, make check, 공개 CSV 재계산으로 소스·숫자를 검토한다.
3. E와 L의 candidate.diff, 필수 진단 검증기, publication provenance를 검토한다.
4. 실제 커널 실험이 필요하면 별도 Frozen 입력·모델 라이선스·kernel/BTF/Boot·Host Profile/Runtime Baseline·기능/열/Cleanup 관문을 준비하고 연구 책임자 검토를 먼저 받는다. 배포용 준비나 무인 운용 승인이 아니다.

## 공유 요약

NEW_A4/8은 검증한 i5-1240P의 기존 NEW_A8/8 대비 HTTP7.77% 개선, CPU Time13.28% 감소를6블록에서 확인했다. 같은 Affinity의 Linux8/8 대비 HTTP3.02% 단축도 관측했다. Thread 구성이 달라 스케줄러만의 우위는 아니며 동일4/8 Linux 대비 우위는 아직 미입증이다. Runtime Lite는 구현·기능 검증을 완료했지만 성능 개선이 확인되지 않아 연구 후보로 보존한다. 다음 연구는 CPU 선택 경량화 가능성 확인과 Prefill 병목 관측 중심이다.

STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED.

이번 공개 업데이트의 비특권 검사 결과는 results/research-update-validation.json이고 최초 공개68개 검사 결과는 results/release-validation.json로 구분한다.
