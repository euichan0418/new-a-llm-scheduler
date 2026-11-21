# 4/8 비대칭 Thread 확증 결과

판정: **ASYMMETRIC_CONFIRMATION_PARTIAL_SUCCESS**. NEW_A(E) FULL은 유지하고, i5-1240P에서 테스트한 E48/E88 중 성능 우선 실행 구성으로 E48을 선정했다. Portable의 기본 P-core 개수 기반4/4 P-only 설정은 변경하지 않는다.

## 조건과 실제 측정

Intel Core i5-1240P(P4/E8/SMT), Linux7.0.0-42-generic, AC, Turbo OFF, performance profile, 같은 Frozen llama-server/GGML/OpenMP/모델과920입력/128생성 토큰, context2048/batch512/ubatch256, GPU offload0이다. 모든 조건의 CPU Affinity는 `0,2,4,6,8,9,10,11`(P 대표4+E4), HTTP2다. C는 Linux 기본, E는 NEW_A FULL이다. C88/E88의 Compute/Batch는8/8, C48/E48은4/8이다.

각 조건6개 독립 서버, 총24개 유효 성능 세션이다. 각 서버는 워밍업2회 후 측정1회이므로 유효 세션에서72개 HTTP 응답(워밍업48+측정24)을 검증했다. 실패·중단 시도는 보존하지만 유효 표본에 넣지 않았다. 이전 탐색의 n=2도 합산하지 않았다.

| 구성 | Prefill tok/s | Decode tok/s | HTTP s | Process CPU Time s |
|---|---:|---:|---:|---:|
| C88 | 73.1430 | 19.4326 | 19.172912 | 122.393803 |
| E88 | 72.5486 | 17.1294 | 20.160085 | 126.271304 |
| C48 | 73.0414 | 21.5022 | 18.560891 | 106.998383 |
| E48 | 72.4070 | 21.7677 | 18.593546 | 109.503467 |

## 핵심 비교

- **E48 vs E88:** HTTP7.77% 감소, Process CPU Time13.28% 감소, Decode27.08% 증가, Prefill99.80% 유지. HTTP와 CPU Time 감소가 모두6/6블록에서 관측됐다. 유지율은 사전 비열등성 검정을 통과했다는 뜻이 아니다.
- **E48 vs C88:** 같은 P4+E4 Affinity에서 HTTP3.02%, CPU Time10.53% 감소, 두 지표6/6블록 감소. Compute/Batch가4/8과8/8로 달라 **실행 구성 사이의 절대 성능 비교**다. 스케줄러만의 우위로 해석하지 않는다.
- **C48 vs C88:** Linux에서도 HTTP3.19%, CPU Time12.58% 감소,6/6블록에서 같은 방향이다. 비대칭 구성의 이익이 NEW_A에만 발생한 것이 아니다.
- **E48 vs C48:** 동일 설정에서는 HTTP 평균+32.66ms. 우위·동등성 모두 미확정이다. [Linux 비교](linux-comparison.md)를 참고한다.

## 블록 순서와 공개 데이터

| 블록 | 순서 |
|---|---|
|1|C88 -> E88 -> C48 -> E48|
|2|E48 -> C48 -> E88 -> C88|
|3|C48 -> E48 -> C88 -> E88|
|4|E88 -> C88 -> E48 -> C48|
|5|C88 -> E88 -> E48 -> C48|
|6|C48 -> E48 -> E88 -> C88|

개별24개 값은 [CSV](../../results/asymmetric-24.csv), 평균·표본 SD·중앙값·범위·대응 차이·95%구간은 [JSON](../../results/asymmetric-summary.json)에 있다.


`python3 -B scripts/summarize_research.py --check`로 공개 숫자에서 통계를 재계산할 수 있다. private 원시 응답, Host Profile, Boot ID, TID, 로그는 공개하지 않았다. 공개 CSV 재계산은 비공개 원시 corpus의 독립 재감사와 다르다.

## 지표와 한계

Primary Metric은 HTTP 전체 요청 시간이다. Process CPU Time은 요청 전후 **전체 서버 프로세스 CPU clock**의 차이로, 안정 TID schedstat 합이나 BPF runtime_ns와 다르다. CS/Migration은 안정 경계 TID 범위, Busy는 시스템 전체다. RAPL/Wakeup/fexit/Runtime Statistics는 OFF다. CPU Time 감소는 에너지 절감 또는 다른 작업의 가용 CPU 확보를 입증하지 않는다.

6개 블록의 대응 t 구간은 블록 독립성 및 차이의 근사 정규성을 전제로 한다. 다중 비교 보정은 하지 않았다.6회로 시간 변화·배경 부하·비정규성·다른 CPU의 불확실성이 해결되지는 않는다.4/4·6/6·다른 요청 크기에 대한 전역 최적 구성도 아니다. 실제 phase별 활성 OpenMP TID, P/E 체류시간, barrier/runnable 대기는 충분히 계측되지 않았다.

같은 커널/BTF라도 reboot 또는 governor/센서/라이브러리 변경 시 새 Host Profile/Runtime Baseline 및 기능 admission이 필요하다. 새로운 커널43 Runtime Lite 캠페인과 합산하지 않는다.

STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED.
