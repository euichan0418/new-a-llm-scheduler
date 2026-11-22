# Intel Hybrid 및 동종 코어 실행 준비

기존 i5-1240P 실험과 새 범용 객체를 구분한다. 기존 E FULL BPF의 정책/소스는 그대로지만 로더·동적 Guard가 새 구현이므로 새 기능 검증이 필요하다. 새 실행기의 성능 동등성은 미검증이다.

## 탐지

CPUID Hybrid flag/leaf0x1A의 Core(0x40)/Atom(0x20), sysfs package/die/core 및 reciprocal SMT를 대조한다. CPU 번호·주파수·논리 CPU 개수로 유형을 추정하지 않는다. online/실제 allowed/cpuset 제약 안에서 각 물리 P-core 대표 한 개를 고른다. 캐시/L2/L3/NUMA는 실제 자료를 보존한다.

실험적 프로파일 Compute=P, Batch=P+floor(E/2), HTTP2를 명시적으로 선택한다. E 선택 기본 compact는 같은 L2 그룹을 먼저 사용하는 결정적 규칙이며 spread는 별도 후보다. 최적 성능을 의미하지 않는다. 프로세스 affinity에 E가 포함되므로 Decode가 P 전용이라는 보장은 없다.

## i5 회귀 범위

이 개발 중 로컬 읽기 전용 검사에서 P4+E8, Compute4/Batch8/HTTP2, `0,2,4,6,8,9,10,11`을 재현했다. 기존 6블록의 E48 vs E88 HTTP -7.77%, CPU Time -13.28% 결과는 보존했다. 새 범용 로더에서 해당 성능 또는 안전성이 재현됐다는 뜻이 아니다.

## i7-12700H 팀원

사용자가 보고한 P6+E8/SMT20 환경과 mask `0,2,4,6,8,10,12,13,14,15`은 **해당 팀원 PC의 예시**다. 자동 생성은 실제 탐지를 다시 수행하며 번호를 하드코딩하지 않는다. 같은 형태의 모의 토폴로지에서는6/10/2와 해당 mask를 재현했다. TEAM_CPU_RUNTIME_NOT_TESTED.

[범용 명령](universal-runtime.md)의 build→detect→recommend→validate-config를 자신의 PC에서 수행한다. P6 대표6개·E4개·중복 없음·online/allowed·L2구조를 JSON에서 검토한다. 자신의 Frozen 서버/모델/라이브러리를 준비하고 새 Host/Baseline을 생성한다. i5의 프로파일·BPF 기능 증거를 복사하지 않는다.

사용자 승인 후 Linux C610 audit/clean, NEW_A E610 audit/clean을 먼저 실행한다(실제 CLI slot 이름은 C-audit/C-clean/E-audit/E-clean이고6/10은 봉인된 configuration에 저장된다). 네 원시 검증 통과 후 C610→E610, E610→C610 2블록을 수행할 수 있다. 각 세션 warmup2/측정1이며 n=2로 우위를 확정하지 않는다. Thermal/Mask/Tracking/Identity/Cleanup 미달이면 중단한다.

Intel 동종 코어는 가상의 P/E 등급 없이 물리 대표 우선, SMT 후순위다. Thread/affinity 변경 실험과 동일 설정 Linux/NEW_A 비교는 따로 설계한다. 기존 Portable 기본값은 변경하지 않았다.
