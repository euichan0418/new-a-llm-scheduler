# 실험적 Intel Hybrid 성능 프로파일

이 도구는 토폴로지와 Thread/Affinity 후보를 **읽기 전용으로 제안**한다. 기존 NEW_A(E) FULL의 CPU 선택 순서, DSQ, Partial Mode, Mask/Tracking 진단 및 Portable 기본값을 변경하지 않는다. BPF/LLM 실행 기능은 없다.

```text
STRICT_ACCEPTANCE=FAILED
safety_verdict=FAIL
OVERHEAD_REVIEW_REQUIRED
TEAM_CPU_RUNTIME_NOT_TESTED
```

## 탐지와 신뢰 범위

기존 `new_a_portable/topology.py`와 `cpu_probe.c`의 검증을 재사용한다. `/proc/cpuinfo` 제조사/모델/CPU signature, sysfs package/die/core 및 상호 SMT sibling, possible/present/online, 실제 `sched_getaffinity`를 대조한다. 물리 코어 키는 `(package, die, core)`다. 코어당 **온라인이고 허용된 논리 CPU 중 최솟값 하나**를 대표로 선정한다. 한 sibling만 허용돼도 그 CPU를 선택하며 SMT 둘을 물리 코어 둘로 세지 않는다.

Intel은 각 사용 가능 CPU에서 CPUID Hybrid flag와 leaf 0x1A의 0x40(Core)/0x20(Atom) 유형을 확인한다. CPU 번호, 논리 CPU 수, 최대 주파수만으로 P/E를 추정하지 않는다. CPUID helper는 일반 사용자 하위 프로세스 자신의 affinity만 일시 변경하고 복원한다. 부모 affinity와 전원 설정을 변경하지 않는다. 필요한 권한은 없다. CPUID/SMT/snapshot 불일치 또는 탐지 중 hotplug/제한 변경은 자동 재시도 없이 차단한다. 하드웨어/가상화가 정보를 숨기면 P/E 인식을 보장할 수 없다.

별도 수집기는 L2/L3의 level/type/shared_cpu_list, NUMA node, CPU capacity/하드웨어 최대·base 주파수, 보이는 cgroup cpuset/CPU quota 정보를 읽는다. 캐시 공유는 상호 일치해야 한다. 제한 적용의 기준은 **실제 affinity와 online의 교집합**이다. cgroup namespace/권한 때문에 메타데이터를 못 읽어도 이를 무제한으로 해석하지 않는다. 조상 CPU quota가 더 엄격할 수 있으며 CPU 수는 CPU 대역폭 보장이 아니다. cgroup 경로·Boot ID·Host ID·전체 환경변수는 출력하지 않는다. 상세 모델/코어 수/CPU 번호는 로컬 정보이므로 결과 JSON을 무조건 공개하지 않는다.

`detect`는 전체 online 물리 코어 수와 **사용 가능한** P/E 수를 구분한다. 허용되지 않은 CPU에 CPUID를 강제 실행하지 않는다. 전체 offline 코어가 있으면 설치된 총 물리 코어 수는 미확인일 수 있다.

## 명시적으로 선택하는 공식

P = 사용 가능한 물리 P-core, E = 사용 가능한 물리 E-core:

- Compute = P
- Batch = P + floor(E/2)
- HTTP = 2
- Affinity = P 대표 전부 + 선택한 E-core floor(E/2)개

| 실제 가용 토폴로지 | Compute/Batch | Affinity 구성 | 근거 |
|---|---|---|---|
| P4+E8 | 4/8 | P4+E4 | i5-1240P의 특정 4/8 구성 성능 연구가 있음 |
| P6+E8 | 6/10 | P6+E4 | 모의 구성 검증만 완료, 타 CPU 성능 미검증 |
| P8+E8 | 8/12 | P8+E4 | 초기 후보, 성능 미검증 |
| P8+E16 | 8/16 | P8+E8 | 초기 후보, 성능 미검증 |

Offline/제한된 코어는 제외하고 남은 **물리** 코어로 다시 계산한다. 홀수 E는 내림한다. E=1이면 추가 E 수는 0이다. 필요한 P/E 유형이 없거나 불완전한 유형 판별이면 Hybrid 공식을 강제하지 않는다. 서버 소스 헤더의 Thread 한계를 넘으면 조용히 줄이지 않고 거부한다. 실제 Frozen 빌드의 인자/팀 동작은 별도 확인 대상이다.

동종 Intel/AMD는 기존 보수적인 물리 코어 대표 전체, Compute=Batch=대표 수를 제안한다. 논리 CPU를 2로 나누지 않는다. AMD 이종 또는 불명확한 CPU는 Intel P/E로 해석하지 않고 자동 설정을 차단한다. 기존 검증 가능한 동종 Portable 경로를 유지하며, 유형/물리 식별이 불명확하면 사용자 수동 토폴로지 검토가 필요하다.

## E-core 캐시 배치

기본 `compact`는 CPU 번호가 작은 검증된 L2 그룹 순서에서 필요한 수를 모두 담는 첫 그룹을 선택한다. 단일 그룹에 안 들어가면 같은 결정적 순서로 여러 그룹을 채우고 단일 L2라고 표시하지 않는다. `spread`는 확인한 L2 그룹별 CPU를 하나씩 round-robin 선택한다. `lowest`는 가용 E CPU 번호 오름차순이다. 어느 배치도 최적이라고 주장하지 않는다.

L2 정보가 없으면 compact/lowest는 낮은 번호를 선택하되 `L2_NOT_VERIFIED`를 표시한다. spread는 검증 불가로 거부한다. 모순된 캐시 정보는 거부한다. NUMA/패키지가 여러 개면 메모리 지역성 검토 경고를 남긴다. `--cpus`는 실제 허용 집합 안에서 범위를 좁히는 옵션이며 유형 강제 지정/권한 우회가 아니다.

현재 i5-1240P의 모든 CPU가 online/allowed인 읽기 전용 탐지에서 P 대표 `0,2,4,6`, E 선택 `8,9,10,11`, Compute4/Batch8을 재현했다. E 8-11은 같은 L2 그룹이었다. 이는 현 장비의 구성 일치 검사이며 새 성능 측정이 아니다. 팀원 장비의 번호를 이 목록으로 가정하지 않는다.

## 설치 없이 사용

Linux x86-64, Python 3.10 이상, C 컴파일러 `cc`와 Linux 개발 헤더가 필요하다. 이 패키지를 사용하기 위해 libbpf/BPF 빌드나 sudo는 필요하지 않다. 패키지 설치도 자동으로 하지 않는다.

```bash
git clone --branch feature/new-a-hybrid-auto-config https://github.com/euichan0418/new-a-llm-scheduler.git
cd new-a-llm-scheduler
python3 -B tools/sched_ext/new_a_hybrid/build_probe.py --output ../new-a-cpu-probe-build
mkdir -p configuration.local
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config detect --probe ../new-a-cpu-probe-build/cpu-probe --format json --output configuration.local/topology.local.json
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config recommend --probe ../new-a-cpu-probe-build/cpu-probe --format json --output configuration.local/performance.local.json
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config validate --probe ../new-a-cpu-probe-build/cpu-probe --config configuration.local/performance.local.json --format json
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config environment --format json
```

빌드/출력 디렉터리는 새 경로를 사용한다. 기존 파일을 덮어쓰지 않는다. 위 JSON 경로는 `.gitignore` 대상이다. 사람이 읽는 출력은 `--format text` 또는 옵션 생략으로 선택한다. `detect` 텍스트는 상세 JSON 형식으로도 읽을 수 있다.

다른 L2 배치와 실험 계획:

```bash
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config recommend --probe ../new-a-cpu-probe-build/cpu-probe --placement spread --format json --output configuration.local/spread.local.json
python3 -B tools/sched_ext/new_a_hybrid/new-a-topology-config experiments --probe ../new-a-cpu-probe-build/cpu-probe --expect-p 6 --expect-e 8 --format json --output configuration.local/p6e8-plan.local.json
make test-hybrid
```

expect 값은 **사용 가능한 물리 P/E 수**와 다르면 실패한다. SMT가 꺼져 14논리 CPU인 P6+E8에서도 정확한 유형/물리 구조가 검증되면 6/10 제안은 가능하다. 이 경우 20논리 CPU/SMT 활성이라고 보고하면 안 된다.

`validate`는 현재 CPU·CPUID·affinity·캐시·cgroup을 다시 읽어 fingerprint와 결정적 제안을 재계산한다. 수정된 수치, stale scope, bool로 위장한 수치, 변경된 affinity는 거부한다. 성공은 `VALID_CONFIGURATION_ONLY`다. 자동 제안에는 항상 `performance_validated=false`, `execution_admitted=false`가 포함된다. 알려진 i5 구성 일치 플래그도 새 성능 승인과 별개다.

## 실행과 성능에 대한 제한

프로세스 affinity P6+E4와 Compute6만으로 Decode 작업자의 P-core 전용 실행을 보장하지 않는다. 실제 CPU 선택/체류시간과 OpenMP 팀 수·역할은 별도 관측 대상이다.

새 JSON은 기존 Portable Runtime 실행 계약과 호환된다고 주장하지 않는다. 특히 현재 `new_a_full_guard/monitor.h`에는 16논리 CPU와 고정 mask 가정이 있고 `new_a_portable_runtime/integration.py`도 i5 P4를 요구한다. **이를 우회해 팀원 PC에서 실행하면 안 된다.** 팀원용 별도 Guard/실험 adapter, Host Profile, Runtime Baseline, BTF/kfunc/Identity 검증, 실제 Audit/Clean을 준비해야 한다. 이번 작업은 이 고정 Guard를 변경하지 않았다.

기존 i5 연구의 NEW_A 4/8 vs 8/8 HTTP -7.77%, CPU Time -13.28% 및 Linux 8/8 대비 HTTP -3.02%를 P6+E8 성능으로 일반화하지 않는다. 동일한 4/8의 Linux 대비 스케줄러 우위는 미입증이다. 이 패키지는 기존 성능 자료나 안전성 판정을 변경하지 않는다.

## 인터페이스 근거

- [Intel CPUID Hybrid/core type 설명](https://cdrdv2-public.intel.com/737973/737973_best_practices_for_real-time_optimizations_rev1.1.pdf)
- [Linux CPU topology sysfs](https://docs.kernel.org/admin-guide/cputopology.html)
- [Linux CPU/cache sysfs ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-devices-system-cpu)
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)

소스/라이선스: 기존 GPL-2.0 probe와 분류기를 그대로 재사용하며 기존 저작권·라이선스 고지를 유지한다. [팀원 실험 계획](team-p6e8-experiment.md).
