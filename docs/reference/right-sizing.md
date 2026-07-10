# Right-sizing 분류 — 명세·판정 순서·임계 근거·OS 분기·한계

> 분류 단일 진실 = `recommendation.rollup_host(stats) -> HostAssessment`(자원 5개 per-resource 판정 + 근본원인 종합).
> 표시 배지 = `classify_host(stats)` = `host_status_to_recommendation(rollup_host(stats).host_status)`.
> 본 문서는 그 명세(정의·판정 순서·임계 출처·OS 분기·신뢰도·한계)의 단일 진실이다.
> 코드 상수: `src/assessment_engine/recommendation.py`(`RS_*`). 사용자 노출 요약: `reports/_thresholds_reference.html`.
> 변경 시 본 문서·코드 상수·`_thresholds_reference.html`·결정 기록(`docs/decisions/adr/`) 동시 갱신(#F9).

## 1. 목적·범위

right-sizing = 관측 부하(`WINDOW_DAYS` = 14일 통계) 대비 할당 자원의 적정성 평가. 규칙 기반 결정적 분류.
자원 5개(CPU · 메모리 · 디스크 용량 · 디스크 I/O · 네트워크)를 각각 USE(이용률·포화·오류)로 판정한 뒤, 인과
근본원인으로 호스트 하나로 종합한다. 가진 데이터로 항상 결론을 내며, 포화 축은 OS별 실측 신호로 정규화하되
해당 카운터를 못 읽어 값이 없는 축만 분류를 막지 않고 confidence 단서(coverage_gap)로 노출한다.

UI badge 임계(`mappers._USAGE_DANGER_PCT`/`_USAGE_WARN_PCT`, 90/75)와는 별 도메인이다 — 그쪽은 시점 사용량 시각
신호, 본 모듈은 윈도우 통계 기반 사이징 결정. 혼용 금지.

## 2. 자원별 판정 (per-resource USE)

각 자원은 자기 어휘로 판정한다 (`assess_cpu`/`assess_memory`/`assess_disk_capacity`/`assess_disk_io`/`assess_network`).

- CPU: under(이용률 p95 >= 70% OR 실행 큐 포화) / over(AWS Balanced 사이징 목표 < 현재 코어, 단일스레드 보호 시 보류) / optimal. 목표 = 이용률 70% + 포화 headroom 중 큰 쪽.
- 메모리: under(이용률 p95 >= 90% OR active page-out OR OOM) / over / optimal. 사이징 목표 = near-peak(관측 피크) 80% 착지 — 비탄력·OOM 회피라 평균 아닌 피크 통계. swapless 다수는 이용률이 주신호.
- 디스크 용량: filling(소진 runway < 30일 OR 정적 가드 used% >= 85%, 바이트·inode 각 축) / capacity_ok. under/over 아닌 "남은 시간" 예측(누적 자원). 확장 목표 GB 동반.
- 디스크 I/O: io_bound(응답 지연 await p95 > 20ms) / io_ok. 증분 불가라 사이징 없음 — 티어 상향 검토 표시.
- 네트워크: congested(품질) / quality_ok. 사이징 축 아님(vNIC 링크 속도 부재). 재전송·드롭·conntrack 은 품질 신호.

## 3. 호스트 종합 + 판정 순서

`rollup_host` 가 자원별 판정을 인과 근본원인으로 종합한다. 인과 사슬(상류 -> 하류): 메모리 -> 디스크 I/O -> CPU.
판별 신호 = swap page-out(메모리발) / procs_blocked D-state + await(디스크발) / run queue(CPU발). root 에만 처방,
하류(증상)는 "root 해결 후 재평가". "가장 나쁜 자원 승" 폐기 — 삼중 처방(RAM 하나 문제에 RAM+SSD+코어) 방지.

호스트 요약 상태(`host_status`, 정렬·배지용) 판정 순서:

| 순위 | host_status -> 배지 | 조건 |
|------|---------------------|------|
| 1 | under -> under_provisioned | 자원 하나라도 under/io_bound/filling (네트워크 congested 는 제외 — 아래) |
| 2 | idle -> idle | cpu_p95 <= 3% AND net_avg <= 2 Mbps (위 under 0) |
| 3 | over -> over_provisioned | cpu 또는 memory 가 over (위 미해당) |
| 4 | insufficient -> insufficient_data | 전 자원 unmeasured |
| 5 | optimal -> optimal | 그 외 |

핵심 원칙 — under 가 idle 보다 우선. 어떤 자원 압박(page-out·await·용량 소진·고이용)이 하나라도 있으면 CPU 가
낮아도 "미사용"이 아니라 자원 부족이다.

네트워크는 host under/over 축이 아니다 — 원칙상 사이징 축이 아니라 품질 신호라, 혼잡은 호스트를 "자원 부족"으로
분류하지 않고 `HostAssessment.network_congested` 플래그로 orthogonal 노출(별도 "네트워크 혼잡" 경고).

## 4. 임계 카탈로그 (출처)

모든 임계는 (계층, 출처) 추적 — 뿌리 없는 값 0. 상수는 `recommendation.py` 단일 진실(`RS_*`).

| 신호(trigger key) | 임계 | USE 축 | 출처 |
|-------------------|------|--------|------|
| cpu_util | cpu_p95 >= 70% | Utilization | 큐잉 무릎(Kleinrock) + AWS Compute Optimizer Balanced(<70% P95) |
| mem_util | mem_p95 >= 90% | Utilization | Azure Advisor(CPU·메모리 >= SKU 90% resize) |
| cpu_saturation | Linux procs_running/cores >= 1.0 / Windows run queue/cores >= 2 | Saturation | USE Method run queue · MS "sustained > 2 per CPU" (os-aware) |
| mem_saturation | Linux active page-out(pswpin/pswpout rate>0) / Windows Pages Input/sec p95 >= 20 | Saturation | USE Method(si/so) · MS 하드폴트 관례(체감 저하 20) |
| disk_capacity | runway < 30일 OR 정적 가드 used%/inode% >= 85% | Utilization/추세 | 용량 계획(Theil-Sen 추세) + monitoring 표준 85%(major) |
| disk_io | await p95 > 20ms (양 OS 통일) | Saturation | VMware(read >20ms critical) / SQL Server(~10-15ms) |
| net_retrans | 재전송률 > 1% | Errors(품질) | monitoring 관행(재전송 >1% 성능 영향) |
| net_drop | 드롭률 > 0.5% | Errors(품질) | monitoring 관행(드롭 <0.5% 비즈니스 앱) |
| net_conntrack | conntrack count/max >= 80% | 포화(품질) | 연결테이블 고갈 임박(신규 연결 드롭) |
| (idle) | cpu_p95 <= 3% AND net <= 2 Mbps | Utilization | Azure/AWS 저사용 |
| (over) | AWS Balanced 사이징 목표 < 현재 | Utilization | AWS Compute Optimizer Balanced(70% 목표) |

사이징 목표는 자원 특성에 맞춘 비대칭(용도적합): CPU 는 p95 이용률 착지(탄력적이라 순간 피크 흡수), 메모리는 near-peak(관측 피크) 착지(비탄력·OOM 위험이라 평균 아닌 피크 대표 통계). 증설·다운사이즈 동일 통계. 목표%·포화 배수·per-core 보류 임계 수치·근거는 `right-sizing-thresholds.md`.

## 5. OS 분기 (Windows)

포화 3축 모두 OS별 실측 신호로 정규화 — 동일 분류 체계·임계 도메인, 신호원만 상이. 전용 helper 단일 진실 경유
(임계 재계산·직접 해석 금지), 값 None 이면 helper 가 None -> 해당 자원 coverage_gap.

- cpu_saturation: `cpu_saturated` — Linux procs_running/cores >= 1.0, Windows System\Processor Queue Length p95/cores >= 2.
- mem_saturation: `mem_saturated` — Linux active page-out(mem_swap_paging = pswpin/pswpout rate>0, 정적 swap 점유 아님. swappiness 로 여유 RAM 에도 유휴 페이지 스왑아웃하므로 점유는 신호 아님), Windows Memory Pages Input/sec rate p95 >= 20(하드 read 폴트, 총 Pages/sec 과 달리 mmap 미혼입). pagefile 사용량 직접 해석 금지.
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

## 8. 한계

- Windows 포화 축 임계 근거: run queue(>= 2/core)는 MS 표준, 메모리는 Pages Input/sec rate p95 >= 20(하드 read 폴트) — 총 Pages/sec(mmap 혼입) 미사용. disk await 는 구세대 viostor(fleet 실측 11대 중 5대) IOCTL 미부착이면 미측정 -> coverage_gap(별도 ETW 트랙). `docs/explanation/tradeoffs.md` T14.
- 디스크 용량 환경 집계: 개별 호스트 판정(worst mount)은 OS 무관 신뢰 가능하나, 환경 전체 합산은 Windows 물리디스크/디바이스 인식 불완전으로 신뢰 저하 — 환경 disk 합산 지표 도입 시 주의.
- 용량 runway 는 가용 이력 전체 span 기반(분류 14일 창과 별개 — 누적 신호라 길수록 정확). 성장 가속·월간 계절성(약 1개월 데이터)은 놓칠 수 있음.
- net_retrans% 분모는 TCP OutSegs 대신 physical NIC tx_packets 근사(에이전트 OutSegs 미발행) — 비-TCP 프레임 혼입으로 과소평가 방향.
- p95 표본: 윈도우(14일)보다 데이터가 짧으면 신뢰도 저하. 전 자원 unmeasured 면 insufficient_data.
