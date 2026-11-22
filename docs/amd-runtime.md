# AMD 범용 Runtime의 범위와 후속 검증

AMD_RUNTIME_NOT_TESTED. AMD 하드웨어에서 verifier/attach/추론/성능 검증을 수행하지 않았다. 범용 Python/C 모의 테스트와 현재 Intel 커널 BTF에 대한 빌드만 통과했다.

동종 코어로 신뢰성 있게 식별되는 AMD는 package/die/core와 SMT sibling을 확인하여 물리 대표 하나씩 선택하고 Compute=Batch=사용 가능한 물리 코어 수, HTTP2를 제안한다. SMT 번호 규칙이나 논리 CPU÷2 공식을 사용하지 않는다. die/NUMA/L3 공유를 보존하며 여러 CCD를 가상의 Intel P/E로 재분류하지 않는다. SMT 후순위, Local/Shared DSQ 및 weighted vtime은 기존 E FULL을 재사용한다. 그 정책이 AMD에서 우수하다는 주장은 없다.

Zen/Zen c 혼합, 코어 유형 신호 누락·불일치, 일부만 보이는 가상화는 자동 실행 차단 대상이다. 확인할 수 없는 유형을 동종으로 강제 지정하지 않는다. offline/cpuset/SMT 한 sibling만 허용된 경우 실제 허용 CPU에서 대표를 다시 선정한다.

[범용 실행 안내](universal-runtime.md)를 따라 해당 AMD PC에서 새 build/CPUID/config/Host/Baseline을 만든다. 새 커널의 sched_ext/BTF/kfunc/Partial flags 지원을 확인한다. CPU ID 0..1023 범위를 초과하면 차단한다. 4/6/8/12/16코어, SMT OFF, 여러 die/NUMA, 비연속 ID, 제한된 allowed mask는 모의 검증 범위이며 실기기 지원 승인이 아니다.

실행 전 추가 확인:

- AC/Turbo OFF/governor/profile/CPU 온도 인터페이스가 실제 존재하며 의미가 맞는지 검토한다. 데스크톱에서 AC 또는 thermal 증거를 못 읽으면 현재 admission은 차단한다. 값이 없다고 안전한 상태로 추정하지 않는다.
- Intel thermal_throttle 카운터가 없는 AMD에서는 해당 증가 검사가 NOT_AVAILABLE이다. 온도 센서는 계속 검사하지만 하드웨어 throttling 부재를 입증하지 못한다. 현 CPU에 맞는 thermal 검증은 별도 검토한다.
- AF_ALG sha256, native 로더 의존성, Frozen marker/OpenMP 팀 출력 지원을 확인한다. 같은 이름의 서버라도 hash/라이브러리가 다르면 새 Baseline으로 기록한다.
- 사용자 승인 후 C audit/clean, E audit/clean으로 verifier/실제 객체ID/Mask/Tracking/응답/팀/정리를 검증한다. 그 후만 C→E/E→C 성능 탐색을 허용한다.

CPU 구성 제안 성공과 실제 BPF 실행 호환성은 별개다. i5의 안전성 실패 상태와 OVERHEAD_REVIEW_REQUIRED는 계속 유지한다.
