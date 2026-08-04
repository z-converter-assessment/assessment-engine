# Right-sizing 분류 — 명세·판정 순서·OS 분기·한계

> 분류 단일 진실 = `recommendation.rollup_host(stats) -> HostAssessment`(자원 5개 per-resource 판정 + 근본원인 종합).
> 표시 배지 = `classify_host(stats)` = `host_status_to_recommendation(rollup_host(stats).host_status)`.
> 본 문서는 그 명세(정의·판정 순서·게이트 구조·OS 분기·신뢰도·한계)의 단일 진실이다. 임계 수치·벤더 출처는
> `right-sizing-thresholds.md` 정본.
> 코드 상수: `src/assessment_engine/recommendation.py`(`RS_*`). 사용자 노출 요약: `reports/_thresholds_reference.html`.
> 변경 시 본 문서·`right-sizing-thresholds.md`·코드 상수·`_thresholds_reference.html`·결정 기록(`docs/decisions/adr/`) 동시 갱신(#F9).

## 1. 목적·범위

right-sizing = 관측 부하(`WINDOW_DAYS` = 14일 통계) 대비 할당 자원의 적정성 평가. 규칙 기반 결정적 분류.
자원 5개(CPU · 메모리 · 디스크 용량 · 디스크 I/O · 네트워크)를 각각 USE(이용률·포화·오류)로 판정한 뒤, 인과
근본원인으로 호스트 하나로 종합한다. 가진 데이터로 항상 결론을 내며, 포화 축은 OS별 실측 신호로 정규화하되
해당 카운터를 못 읽어 값이 없는 축만 분류를 막지 않고 confidence 단서(coverage_gap)로 노출한다.

UI badge 임계(#E3)와는 별 도메인이다 — 그쪽은 시점 사용량 시각 신호, 본 모듈은 윈도우 통계 기반 사이징
결정. 혼용 금지.

## 2. 자원별 판정 (per-resource USE)

각 자원은 자기 어휘로 판정한다 (`assess_cpu`/`assess_memory`/`assess_disk_capacity`/`assess_disk_io`/`assess_network`).

- CPU: under(이용률 p95 >= 70% OR 실행 큐 포화 OR 사이징 목표 > 현재 코어) / over(AWS Balanced 사이징 목표 < 현재 코어, 단일스레드 보호 시 보류) / optimal. 실행 큐 포화는 dual-gate(실행 큐 >= 임계 AND 이용률 >= 70% — 저활동 실행큐 노이즈 차단, 5절). 목표 = 이용률 70% + 포화 headroom 중 큰 쪽.
- 메모리: under(이용률 p95 >= 90% OR OOM) / over / optimal. 페이징(하드 폴트)은 독립 under 트리거가 아니라 dual-gate 포화 신호 — 이용률 >= 90% AND 페이징이라야 mem_saturation(5절), util < 90 이면 페이징이 있어도 under 아님(mmap 정상 폴트 오탐 차단). 단 이용률 미측정 + OOM 은 그대로 under. 사이징 목표 = near-peak(관측 피크) 80% 착지 — 비탄력·OOM 회피라 평균 아닌 피크 통계. swapless 다수는 이용률이 주신호.
- 디스크 용량: filling(소진 runway < 30일 OR 정적 가드 used% >= 85%, 바이트·inode 각 축) / capacity_ok. under/over 아닌 "남은 시간" 예측(누적 자원). runway 는 마운트별 이력 전체의 2점(first/last) fill rate 외삽이고, 확장 목표 GB 를 동반한다.
- 디스크 I/O: io_bound(응답 지연 await p95 > 20ms) / io_ok. await 는 device 사용률 게이트를 통과한 버킷에서만 산출돼, 저활동 device 는 await 미산출이라 io_ok(병목 아님)로 남는다. 증분 불가라 사이징 없음 — 티어 상향 검토 표시.
- 네트워크: congested(품질) / quality_ok. 사이징 축 아님(vNIC 링크 속도 부재). 재전송·드롭·conntrack 은 품질 신호이며, 재전송·드롭은 저트래픽 호스트에서 판정 보류(비율 분모 붕괴 방지)·conntrack 은 트래픽 무관 절대 신호다.

## 3. 호스트 종합 + 판정 순서

`rollup_host` 가 자원별 판정을 인과 근본원인(root_cause)으로 종합한다. 인과 사슬(상류 -> 하류): 메모리 -> 디스크
I/O -> CPU. 판별 신호 = 메모리 포화(dual-gate 페이징, 메모리발) / procs_blocked D-state + await(디스크발) / run queue(CPU발).

처방(`prescribed_under_kinds`/`under_prescription`)은 자원별 독립이다 — 근본원인만 처방하고 하류(증상)를
억제하지 않는다. 근본원인 추정이 틀리면(원인 자원만 고쳐도 하류가 실제로 해소된다는 보장은 없다) 실제 부족을
놓치는 위험이 더 크다는 안전 우선 판단 — assessment API(`/api/assessment` sizing.axes)의 마이그레이션 사이징
정책과 통일했다. root_cause 는 "왜 부족한가"를 알려주는 진단 근거로만 쓰고, 처방 자체("무엇을 늘릴지")는
관측된 under 자원 전부에 적용한다. 5자원을 각각 독립 판정한 뒤 근본원인을 종합하며, 종합 결과가
처방 범위를 좁히지 않는다.

호스트 요약 상태(`host_status`, 정렬·배지용) 판정 순서 (`_host_status`, 위에서 먼저 매칭한 순위로 확정):

| 순위 | host_status -> 배지 | 조건 |
|------|---------------------|------|
| 1 | under -> under_provisioned | under_kinds 비어있지 않음 — 자원 status 가 under 또는 filling(CPU under · 메모리 under · 디스크 용량 filling). io_bound·congested 제외(아래 orthogonal) |
| 2 | insufficient -> insufficient_data | CPU AND 메모리 둘 다 unmeasured/insufficient — 사이징 2축 부재라 disk/network 부분 데이터 있어도 optimal 위장 금지 |
| 3 | idle -> idle | 활동 3축 quiescent — cpu_p95 <= 3% AND net <= 2 Mbps AND 디스크 I/O baseline <= 5 IOPS(측정된 활동만, 미측정 불배제) |
| 4 | over -> over_provisioned | cpu 또는 memory 가 over |
| 5 | optimal -> optimal | 그 외 |

핵심 원칙 — under 가 idle 보다 우선. 사이징 압박(고이용·용량 소진·메모리 페이징+고이용)이 하나라도 있으면 CPU 가
낮아도 "미사용"이 아니라 자원 부족이다. insufficient 는 idle/over 보다 앞 — cpu·mem 없이 idle/over 판정 불가.

디스크 I/O(io_bound)·네트워크(congested)는 host under/over 축이 아니다 — 사이징 축이 아니라 지연/품질 신호라,
호스트를 "자원 부족"으로 분류하지 않고 orthogonal 플래그로 노출: `HostAssessment.network_congested`(네트워크 혼잡)
+ disk_io 상태(`disk_io_status_label` "I/O 병목"). disk_io 만 병목인 호스트는 배지 optimal + 진단/상태
컬럼에만 병목 표기(사이징 아닌 티어 상향 검토 신호).

## 4. 트리거 카탈로그 (신호 -> 판정 경로)

임계 수치·벤더 출처 정본은 `right-sizing-thresholds.md` — 본 절은 trigger key 가 어느 USE 축에서 어떤 게이트로
발화하는지의 대응만 담는다. 상수는 `recommendation.py` 단일 진실(`RS_*`).

| 신호(trigger key) | USE 축 | 판정 경로·게이트 |
|-------------------|--------|------------------|
| cpu_util | Utilization | `assess_cpu` — 이용률 p95 단일 임계 |
| cpu_saturation | Saturation | `cpu_saturated` — 실행 큐/코어 AND 이용률 dual-gate (OS별 신호원 5절) |
| mem_util | Utilization | `assess_memory` — 이용률 p95 단일 임계 |
| mem_saturation | Saturation | `mem_saturated` — 이용률 AND 페이징(하드 폴트) dual-gate (OS별 신호원 5절) |
| mem_oom | Errors | `assess_memory` — 창 안 OOM 발생이면 게이트 없이 즉시 under |
| disk_capacity | Utilization/추세 | `assess_disk_capacity` — runway 임박 OR 정적 가드, 바이트·inode 각 축 독립 |
| disk_io | Saturation | `disk_io_saturated` — await p95, device 사용률 게이트를 통과한 버킷만 산출 |
| net_retrans | Errors(품질) | `assess_network` — 창 전체 delta 합계 비율, 저트래픽이면 판정 보류 |
| net_drop | Errors(품질) | `assess_network` — 창 전체 delta 합계 비율, 저트래픽이면 판정 보류 |
| net_conntrack | 포화(품질) | `assess_network` — 창 최대 비율, 트래픽 무관 절대 신호 |
| (idle) | Utilization | `_host_status` — CPU p95 + 네트워크 + 디스크 baseline IOPS 활동 3축이 전부 quiescent (CPU·네트워크는 측정 필수, 디스크 미측정은 불배제), 강도는 `is_idle_strong`(CPU peak + 네트워크)이 즉시 종료 / 통합·재배치 권고로 가름 |
| (over) | Utilization | `assess_cpu`/`assess_memory` — 사이징 목표 < 현재, CPU 는 per-core 보류 조건이면 유예 |

사이징 목표는 자원 특성에 맞춘 비대칭(용도적합): CPU 는 p95 이용률 착지(탄력적이라 순간 피크 흡수), 메모리는 near-peak(관측 피크) 착지(비탄력·OOM 위험이라 평균 아닌 피크 대표 통계). 증설·다운사이즈 동일 통계.

## 5. OS 분기 (Windows)

포화 3축 모두 OS별 실측 신호로 정규화 — 동일 분류 체계·임계 도메인, 신호원만 상이. 전용 helper 단일 진실 경유
(임계 재계산·직접 해석 금지), 값 None 이면 helper 가 None -> 해당 자원 coverage_gap.

- cpu_saturation: `cpu_saturated` — dual-gate: 실행 큐 포화 AND 이용률 p95 >= 70%. 실행 큐 = Linux procs_running/cores >= 1.0, Windows System\Processor Queue Length p95/cores >= 2. 저활동 실행큐 노이즈(수집기 R-state 자기포함, 특히 1코어) 차단 — util 미측정이면 측정된 실행큐 신뢰.
- mem_saturation: `mem_saturated` — dual-gate: 이용률 p95 >= 90% AND 페이징. 페이징 = Linux paging_major(디스크에서 페이지를 다시 읽는 하드 폴트, `mem_swap_paging` 필드가 싣는 값 — swap 점유량도 swap in/out 도 신호 아님. swappiness 로 여유 RAM 에도 유휴 페이지를 스왑아웃하므로 점유는 압박을 뜻하지 않는다), Windows Memory Pages Input/sec rate p95 >= 20(하드 read 폴트, 총 Pages/sec 과 달리 mmap 미혼입). util < 90 이면 페이징이 있어도 비포화. pagefile 사용량 직접 해석 금지.
- disk_io: `disk_io_saturated` — 양 OS await p95 > 20ms 통일. Windows 도 IOCTL_DISK_PERFORMANCE ReadTime/WriteTime(device 합산 counter_agg)로 await 산출(Linux time_reading/writing 등가, 같은 IOCTL 라 큐와 커버리지 동일). await 미배선/구세대 viostor(IOCTL 미부착)면 Windows 는 큐 깊이(disk_queue_p95 >= 2) 폴백.

각 포화 축은 Windows 에서 해당 perflib 미부착·미발행이면 그 축만 coverage_gap -> `host_saturation_unmeasured`(cpu·mem·disk_io 한정) -> "포화 수치 미관측" confidence 단서. 분류 자체는 utilization·측정된 나머지 축으로 완결.

## 6. 신뢰도 (4종) + 다운사이즈 처방 규칙

신뢰도는 분류와 별개 출력 — 측정 불확실성을 종류별로 가른다(`ConfidenceNote`):

- 통계 정밀도(low_precision): 이력 < 30h(계층3 AWS insufficient-data 14일 창 누적 30h floor) OR 버스티(p95/median > 2).
- 커버리지(coverage_gap): 필요 포화 축 미측정(is_partial). 측정된 축만으로 분류 + "포화 수치 미관측" 마커.
- 충실도(biased): virtio 오염(steal p95 >= 5%, 게스트 await 하이퍼바이저 간섭) — 표본 늘려도 안 줄어드는 편향.
- 정상성(nonstationary): 이용률 상승 추세 — forward-looking 결정(다운사이즈·용량 runway)에만.

다운사이즈 "처방"은 over 분류가 저사용이면 늘 뜨나, 구체 처방은 신뢰도 높음(정밀·커버리지·충실도 온전) AND 상승
추세 아님 AND 창 관측 충분(sample_sufficiency >= 0.7)일 때만. 미충족이면 과다 표시하되 권고는 "관찰만". 잘못된
다운사이즈가 최악이라 위험 방향은 넉넉한 이력을 요구.

## 7. 근거(triggers) 재사용

per-resource 판정의 trigger 집합(`cpu_util`·`mem_util`·`disk_capacity`·`cpu_saturation`·`disk_io`·`mem_saturation`·
`mem_oom`·`net_retrans`·`net_drop`·`net_conntrack`)은 도메인 식별자(머신용). 보고서 진단(`report._build_diagnosis`,
host.resources 상태·trigger 파생)·권고(`under_prescription(host)`, root 정합)·attention 자원 부족 카드
(`to_capacity_warning_item`)가 재사용해 한국어 표시로 변환(P2 — 임계 재계산 금지). stats 생성은 `build_resource_stats`
공용(report·attention·서버목록·환경 단일 진실 — 화면 간 분류 정합).

서버 세부 스토리지 상세 탭 '최근 N일' 카드는 `disk_capacity`/`disk_io` 두 trigger 를 배지 1개로 합치지 않고
`PeriodResource.verdict_label`(용량)/`verdict_label2`(성능·IO) 2개로 분리 노출 — 우선순위 승자만 보이면 나머지
축 상태가 안 보이는 문제(예: "I/O 병목"만 뜨면 용량 상태 불명) 회피. 다른 자원(CPU·메모리·네트워크)은 trigger가
1개뿐이라 배지도 1개(`verdict_label2`는 빈 문자열). ViewModel 카탈로그: `docs/reference/web/view-models.md` "자원
상세 탭 '최근 N일' 평가 카드" 절.

## 8. 한계

- Windows 포화 축 임계 근거: run queue(>= 2/core)는 MS 표준, 메모리는 Pages Input/sec rate p95 >= 20(하드 read 폴트) — 총 Pages/sec(mmap 혼입) 미사용. disk await 는 구세대 viostor(fleet 실측 11대 중 5대) IOCTL 미부착이면 미측정 -> coverage_gap(별도 ETW 트랙). `docs/explanation/tradeoffs.md` T14.
- 디스크 용량 환경 집계: 개별 호스트 판정(worst mount)은 OS 무관 신뢰 가능하나, 환경 전체 합산은 Windows 물리디스크/디바이스 인식 불완전으로 신뢰 저하 — 환경 disk 합산 지표 도입 시 주의.
- 용량 runway 는 가용 이력 전체 span 기반(분류 14일 창과 별개 — 누적 신호라 길수록 정확). 성장 가속·월간 계절성(약 1개월 데이터)은 놓칠 수 있음.
- net_retrans% 분모는 TCP OutSegs 대신 physical NIC tx_packets 근사(에이전트 OutSegs 미발행) — 비-TCP 프레임 혼입으로 과소평가 방향.
- p95 표본: 윈도우(14일)보다 데이터가 짧으면 신뢰도 저하. CPU·메모리 둘 다 unmeasured 면 insufficient_data(disk/network 부분 데이터 있어도).
