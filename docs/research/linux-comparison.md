# Linux 비교: 실행 구성 효과와 스케줄러 효과를 구분

상태: **SCHEDULER_SUPERIORITY_NOT_PROVEN**.

## 같은4/8, 같은Affinity

6블록 확증에서 C48은 HTTP18.5609초/CPU106.9984초, E48은 HTTP18.5935초/CPU109.5035초다. E48-C48의 평균 HTTP 차이는 **+32.6557ms(+0.1759%)**, 블록 대응95%구간은 **-176.62 ~ +241.94ms**다. E가 빠른 블록은2/6이며 CPU Time은 평균+2.5051초(+2.3412%),6/6블록에서 더 많았다.

0을 포함하는 HTTP 구간은 우위나 동등성 PASS가 아니다. 사전 동등성 허용 범위를 정의하지 않았고 사후 기준을 만들어 통과시키지 않는다. Prefill은 E가0.87% 낮고 Decode 평균은1.23% 높았지만, Decode가 높은 블록도3/6에 불과하다. 최우선 HTTP 결과와 함께 평가해야 한다.

## 다른 Compute 구성: E48 vs C88

E48은 동일 P4+E4 mask의 Linux8/8보다 HTTP3.02%, CPU Time10.53% 낮았고 두 지표6/6블록에서 감소했다. 이는 검증한 모델·요청·하드웨어에서 의미 있는 실행 구성 성과다. 스케줄러와 Compute Thread가 동시에 바뀌므로 **NEW_A 정책 자체가 Linux보다 빠르다는 증거가 아니다.**

## 별도 Linux4/4 참고값

이전 별도 탐색의 C4(P-only)는 HTTP약21.67초, CPU약75.78초였다. E48의18.59초와 비교하면 HTTP는 낮지만 CPU Time은 C4가 더 적다. 캠페인과 Affinity가 다르므로 참고값일 뿐, 같은 조건의 대응 실험이나 확증 표본으로 합산하지 않는다. 최초 공개의4T Linux/E6회 결과도 [기존 결과](../../results/benchmarks.json)에 그대로 보존한다.

## 최신 Runtime Lite 탐색과의 관계

새 커널43/새 Boot/새 governor binding에서 C48/E48/L48을 n=2로 따로 측정했다. 이번에는 E48-C48 HTTP+0.4380%, L48-C48+0.5822%의 평균이었지만 블록별 방향은 달랐다. [Runtime Lite](runtime-lite.md)를 참고한다. 과거6블록과 합산하거나 변화 원인을 커널·governor 하나로 단정하지 않는다.

[전체 확증 CSV](../../results/asymmetric-24.csv) | [통계 JSON](../../results/asymmetric-summary.json)

통계 구간은 paired t, n6/df5이며 근사 정규성·독립 블록을 가정하고 다중 비교 보정은 하지 않았다. 실제 측정 CPU는 i5-1240P뿐이다. CPU Time은 에너지가 아니고 에너지 우위는 입증하지 못했다.

STRICT_ACCEPTANCE=FAILED; safety_verdict=FAIL; OVERHEAD_REVIEW_REQUIRED.
