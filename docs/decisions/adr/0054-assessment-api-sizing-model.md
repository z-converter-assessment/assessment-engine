# ADR 0054 — 프로비저닝 어세스먼트 API 사이징 모델 (near-peak 메모리·per-mount 디스크·물리 집계)

상태: Accepted (2026-07-10) — Builds on ADR 0052·0053.

ADR 0053(Gate0)이 분류 신호와 임계(메모리 사이징 목표 80% 포함)를 확정했다. 본 ADR 은 그 위에서 재해복구/
마이그레이션 소비자용 `/api/assessment` 계약의 사이징 산출을 확정한다 — Gate0 가 목표%만 정하고 남겨둔 "어떤
통계로 역산하나"와 디스크 사이징 구조를 채운다. 계약 표면 정본은 `docs/reference/contracts/assessment-api.md`,
임계·근거는 `right-sizing-thresholds.md`, 본 ADR 은 결정과 근거.

## Context

Gate0 는 메모리 사이징 목표를 80% 로 확정했으나 그 80% 를 어떤 관측 통계에 적용하는지는 명시하지 않았다.
코드는 분류용 p95(5분 버킷 avg 의 p95)를 사이징에도 재사용하고 있었다 — 재계산 0 편의였고 근거 문서가 없었다.
두 가지로 위험하다:

- 메모리는 비탄력 자원이다. CPU 는 순간 100% 를 실행 큐로 흡수하지만(탄력), RAM 은 한계를 넘으면 OOM 으로
  프로세스가 죽는다. 평균 기반 p95 는 짧은 피크를 평탄화해 실제 최대 상주 메모리를 과소평가 -> 안전한 크기보다
  작게 역산한다.
- 어세스먼트 산출물은 재해복구/하이퍼바이저 이전용이라 "대상이 부하를 못 받는 상황을 절대 안 만듦"이 최우선.
  과소 사이징은 계약 원칙(안전 > 정확 > 절감) 위반이다.

디스크도 코드는 호스트 worst-mount 하나로 사이징했다 — 멀티볼륨 호스트에서 /data 만 찬 경우 / 까지 같이 키우거나
특정 마운트를 누락한다. 볼륨별 실제 사용에 맞는 per-mount 사이징이 정석이다.

또 시계열 device 집계가 물리/논리 구분 없이 SUM 이라, 물리 디스크 위 LVM/RAID/swap 이 각각 카운터를 발행하면
이중집계된다(실 fleet 확인: lvm 40·raid 2·bridge 2·swap 17 device 를 시계열로 발행). 이 오염이 환경 차트뿐
아니라 사이징 입력(disk_iops_baseline 유휴 게이트)까지 번진다.

## Decision

1. 메모리 사이징 통계 = near-peak. 5분 버킷 max 의 p99.9(관측 피크 대표)로 목표를 역산한다:
   ceil(총량 x near_peak / 80%). 분류(under = 이용률 p95 >= 90% OR page-out OR OOM)는 p95 유지 — eval 통계와
   sizing 통계를 분리한다. CPU 사이징은 p95(70%) 유지. 이 CPU(p95)/메모리(near-peak) 비대칭이 용도적합
   (fit-for-purpose)의 핵심이다: 탄력 자원은 대표부하로, 비탄력 자원은 피크로 역산한다.

2. 디스크 사이징 = 마운트별(per-mount). server_filesystem 마운트별 사용률·증가율로 각 볼륨의 목표 GiB 를 산출하고
   절대 축소하지 않는다(never-shrink). host-worst 단일 사이징 폐기. device_ref(block_device id) 로 재현
   레이아웃과 조인한다.

3. sizing 계약 = 자기서술 axes[] 배열. cpu/memory(호스트 1축) + disk(마운트별 N축). 소비자는 전 원소를 순회해
   max(current, recommended) 로 프로비저닝한다 — 모르는 축도 같은 규약 적용, 한 축 누락 = 그 자원 통째 누락
   (under)이라 배열로 자기서술한다. base 수량(코어/총 RAM/디스크 총량) 미상 자원은 축을 생략한다(null 원소를
   안 만들어 max 불변식 유지). recommended 는 축이 존재하면 항상 non-null(최소 current). 포화 주도 under 로 정확
   목표가 없으면 안전 한 단계 상향 floor.

4. 사이징에 인과 억제(causal suppression) 미적용. 근본원인은 diagnostics 메타로만 노출하고 각 축은 자기 신호로
   독립 사이징한다 — 어세스먼트는 1회성 마이그레이션 산출이라 재평가 루프가 없어, 억제가 오히려 과소 사이징으로
   흐른다.

5. 시계열 device 집계 물리 필터. 인벤토리에서 물리 device(block_device type=='disk')·물리 iface(net_interface
   kind in physical/bond_master)만 집계에 포함하고, 논리볼륨(lvm/raid/crypt/swap)·가상 iface(bridge/virtual)는
   배제한다. 미매칭 device_id/iface_id 는 fail-open 유지(물리 데이터 누락 방지). 환경 차트·report baseline·
   disk_iops_baseline 을 동시 교정한다. 판정은 시계열 device_id = inventory (id_type):(id) 재구성 조인.

## Consequences

- 메모리 목표가 안전 방향으로 상향된다(실측 fleet 에서 near-peak 목표가 p95 대비 유의미하게 큼) — OOM 유발
  다운사이즈 차단. report_aggregate 에 mem_near_peak(p99.9 of bucket max) 컬럼 추가, ResourceStats.
  mem_near_peak_pct 배선.
- 디스크 사이징이 server_filesystem per-mount + device_ref 조인으로 axes[] disk 원소 N개를 낸다. 마운트 미관측
  볼륨은 축 생략(never-null 유지).
- 물리 필터로 이중집계 제거(redhat8bios disk_iops_baseline 6.8 -> 4.4 등). correlated NOT EXISTS(fail-open)
  구현, 70 서버 14일 report_aggregate 약 450ms 로 비용 수용. cagg retention 확대 시 materialized CTE 로 격상 여지.
- 계약 표면이 얼어붙는다(contracts/assessment-api.md). 필드/축/enum 추가는 CONTRACT_VERSION minor, 구조 파괴는
  major bump(현 1.0) — 엔진+에이전트+DR 동시 flag-day.
