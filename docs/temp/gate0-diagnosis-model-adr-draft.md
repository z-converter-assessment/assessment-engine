# [초안] v2 진단모델 Gate0 — USE 5자원 x 3축 신호·임계·근거

성격: 검토용 초안 (docs/temp). 8개 결정 사용자 확정 완료(하단 "확정된 결정" 절) — /ship 시 정식 ADR 0053 으로
승격 예정 (docs/decisions/adr/, README 인덱스 동반). 본 모델을 T3 cagg / T4 recommendation / T5 표시가 단일 진실로 따름.

핵심 방향 (사용자 확정): 판정(classification)은 지금 근거 있는 고전 신호로만. PSI 는 저장만 하고 판정에는
안 물린다 -> 운영 데이터가 쌓인 뒤 근거 갖고 별도 ADR 로 도입 (collect now, classify later).

---

# ADR 0053 — v2 진단모델 Gate0: recommendation.py v2 신호 확정 (USE 5자원 x 3축)

상태: Proposed (2026-07-09) — Builds on ADR 0052.

ADR 0052 가 원칙(전제 기반 유도 + USE 5자원 + 임계 근거 계층)을 세웠고, 본 ADR 은 그 원칙을 v2 저장
스키마의 실제 신호에 배선하는 Gate0 확정 문서다. 여기서 축·신호·임계를 확정하면 T3(cagg 집계 컬럼)·
T4(`recommendation.py` 판정)·T5(표시 계층)가 이걸 단일 진실로 따른다. right-sizing 은 화면 간 분류 단일
진실(CLAUDE.md #E3)이라 본 ADR 이 recommendation.py + right-sizing.md 갱신의 근거가 된다.

## Context

v2 스키마가 확정돼 "어느 컬럼을 읽어 판정하나"를 배선할 시점이다. 신호 후보는 두 부류다:

- 근거 있는 고전 신호 — run queue, paging, disk await, filesystem used%/inode, net drops/conntrack/retransmit.
  전부 벤더/업계 convention 임계가 문서화돼 있고, 오래된 커널(/proc/stat·vmstat·diskstats)과 Windows 등가에도
  존재한다. 즉 fleet 전체(레거시·Windows 포함) 커버 + 임계를 지어낼 필요 없음.
- PSI (server_pressure) — stall 벽시계 시간을 직접 재는 USE-정본 saturation 신호. 다만 (1) 어떤 벤더도
  "stall 몇 %부터 포화"라는 임계를 문서화하지 않았고, (2) 좋은 임계는 실제 fleet 데이터로 캘리브레이션해야
  하는데 아직 운영 데이터가 없다. (3) Linux 4.20+(2018) 에만 있고 부팅옵션 의존·Windows 부재라 마이그레이션
  평가 fleet(레거시 고객 서버·Windows 다수)에서 커버리지가 낮다.

결정: 판정은 고전 신호로만 확정한다. PSI 는 이미 스키마(server_pressure)에 저장·ingest 되므로 계속 수집하되,
recommendation.py 판정에는 물리지 않는다. 운영 데이터가 몇 주/몇 달 쌓이면 PSI 분포를 고전 신호 분류와
대조해 판별력·임계를 근거 갖고 정하고, 별도 ADR 로 도입한다. 이는 CLAUDE.md #B("활용 안 하는 필드는
저장/드롭, 필요해진 시점에 명시적 결정으로 read 추가")와 정합한다 — 에이전트가 PSI 를 canonical 로 넣은 건
"수집하라"는 wire 계약이고, 엔진이 언제 판정에 쓸지는 엔진의 명시적 결정이다.

이 방향의 효과: (a) 본 ADR 의 모든 판정 임계가 vendor/convention 출처를 갖는다(지어낸 값 0). (b) 레거시·Windows
커버리지 구멍 없음. (c) PSI 캘리브레이션 부담이 지금 사라진다.

v2 로 실제 개선되는 것 (PSI 없이, 문서 근거 있는 순수 이득):
- CPU steal 인과 분리 — cpu_steal_s(모든 Linux /proc/stat)로 "진짜 vCPU 부족 vs 하이퍼바이저 경합" 구분.
  오버서브스크립션 fleet 에서 잘못된 증설 권고 방지.
- 디스크 용량 확장여력 — server_inventory.lvm_vgs[].free_bytes 로 "찼지만 in-place 확장 가능 vs 여력 없음" 구분.
- 네트워크 재전송을 Errors 축으로 정리 — retransmit 은 원격 혼잡 성격이라 saturation 아닌 error/quality(USE 정합).
- inode(server_filesystem) — 바이트 여유해도 inode 고갈 ENOSPC 포착.
- server_disk_error / cpu_mce / mem_hardware_corrupted — 하드웨어·FS 무결성을 health(Errors) 축으로 노출.

확정 삭제·폐기:
- load average — v2 소스 부재로 폐기. cpu_run_queue(procs_running gauge)로 대체(D-state IO 오염 배제).
- swap 런타임 추이 축 — 폐기. swap 은 용량(block_devices type=swap)만, 메모리 포화는 paging 으로.
- sat_* 하드코드 임계 — ADR 0052 근거 계층 규율로 해소.

각 신호에 basis(vendor-verified / vendor-stated / convention / judgment)를 정직히 붙이고, 임계 수치만큼
robustness(집계·지속·hysteresis·스파이크 가드)를 함께 확정한다.

## Decision

### USE 매트릭스 (행=5자원, 열=Utilization / Saturation / Errors) — 판정 신호는 고전 신호만

```
RESOURCE       | UTILIZATION                          | SATURATION (classify)                        | ERRORS (health)
---------------+--------------------------------------+----------------------------------------------+---------------------------
CPU            | util p95 >= 70% under / target 70%   | run_queue os-aware:                          | cpu_mce delta > 0
               | idle <= 3% / per-core >= 85% hold    |  Linux run_queue_p95/cores >= 1.0            |  = health warn
               | [vendor-stated] Gregg,AWS-CO,Azure   |  Win queue_p95/cores >= 2.0 [vendor-verified] |  [convention] Gregg,EDAC
               |                                      | steal p95 >= 5% -> biased(경합, 증설 억제)   |
---------------+--------------------------------------+----------------------------------------------+---------------------------
MEMORY         | Linux 1-avail/limit p95 >= 90% under | Linux: paging_major refault rate sustained   | oom_kill delta >= 1 = under
               | Win commit >= 80% aux / target 80%   |  AND util 높음 (dual-gate, swappiness FP 방지) |  (강한 신호)
               | [vendor-stated] Azure,AWS-CO,MS      | Win Pages Input/sec p95 >= 20 [convention]   | hw_corrupted > 0 = health
               |                                      | oom_kill = 확정 escalation                    |  [convention] Gregg,kernel
---------------+--------------------------------------+----------------------------------------------+---------------------------
DISK-CAPACITY  | used% >= 85 static guard             | runway < 30d = filling (used 추세 회귀)       | disk_error fs class
               | inode used% >= 85 (ext 게이트)        |  inode runway 병렬 / LVM free 시 확장처방      |  delta > 0 = health
               | [convention] monitoring 표준          |  [judgment 30d / vendor-stated ENOSPC]       |  [vendor-stated] Gregg
---------------+--------------------------------------+----------------------------------------------+---------------------------
DISK-IO        | %util p95 >= 70% busy = 정보만        | await p95 > 20ms = io_bound (biased=True)     | disk_error(mdraid/btrfs/
               |  (분류 트리거 아님, SSD 오탐)         |  Win IOCTL 동일 20ms / 폴백 queue_p95 >= 2    |  ext4/eventlog) delta > 0
               |  [vendor-stated] Gregg               |  [vendor-verified] VMware,SQL,iostat         |  = health [vendor-verified]
---------------+--------------------------------------+----------------------------------------------+---------------------------
NETWORK        | 사이징 임계 없음(link_speed null 지배) | drops rate > 0.5% + conntrack ratio >= 80%    | rx/tx_errors sustained
               | idle net <= 2Mbps(활동 축만)          |  (retransmit 은 Errors 로 이동)               |  + retransmit > 1%
               | [judgment] Gregg,AWS-CO idle          |  [convention] Gregg,Prometheus               |  = health [vendor-stated]
```

PSI 열은 매트릭스에서 뺐다 — 저장은 하되 판정에 미투입(Deferred 절). 위 셀은 전부 고전 신호 + 문서 근거.

핵심 구조 (ADR 0052 계승):
- 사이징(under/over) 판정 = CPU · memory · disk_capacity 3자원만.
- disk_io = saturation-only (증분 불가 -> biased=True 티어 상향 표시, 사이징 숫자 미투입).
- network = orthogonal `network_congested` 플래그 (host under/over 미편입).
- Errors 축 = 5자원 모두 health 로 분리 노출 — 사이징·saturation 에 흘리지 않음.

---

## 자원별 상세

### CPU

Utilization
- 신호: util% = 1 - delta(cpu_idle_s)/delta(cpu_total_s) (seconds counter, counter_agg reset 흡수) p95 over 14d.
  per-core = server_cpu_core 코어별 util% p95 최대. cpu_logical_count 는 사이징 target 분모.
- 임계: under util_p95 >= 70%; 사이징 목표 70% 착지(대칭); idle util_p95 <= 3%; per-core 어느 코어든 p95 >= 85%
  면 다운사이즈/유휴 보류(단일스레드 병목 보호). basis: vendor-stated — Gregg USE, AWS Compute Optimizer(<70%
  P95, 14d lookback), Azure Advisor(<=3% idle).
- robustness: p95 + 14d 창(순간 100% burst 절단). idle 도 p95(스파이크로 유휴 탈락 방지). per-core 85% hold.
  버스티(p95/median>2)는 분류 대신 confidence 하향. hysteresis 미적용(창 통계).

Saturation (classify)
- 신호: os-aware run queue 단일 진실 cpu_saturated(). Linux cpu_run_queue(procs_running gauge) p95 / cores;
  Windows Processor Queue Length p95 / cores. 집계 p95.
- 임계: Linux run_queue_p95/cores >= 1.0, Windows >= 2.0. basis: vendor-verified — Gregg(vmstat r > CPU count),
  MS(Processor Queue Length sustained > 2 per CPU). Windows 2.0 은 ready-only 모집단이라 값만 다른 것.
- steal 인과 분리 (v2 개선): cpu_steal_s -> steal p95 >= 5%(vendor-verified, AWS/Datadog <5% 정상 >10% 조사).
  run_queue 포화 + steal 높음 -> 하이퍼바이저 경합 = biased 마커(증설로 안 풀림, 처방 억제).
  run_queue 포화 + steal 낮음 -> 진짜 vCPU 부족(증설 처방). saturation 을 steal 로 오분류하지 않고 인과만 가른다.
- 근본원인 게이트: cpu_blocked(procs_blocked D-state) p95 >= 1.0 이면 disk_io 귀속(CPU 는 증상). rollup_host 사슬.
- v1 변경: run queue 임계·집계 유지. load average 폐기 -> run_queue 대체. steal 인과 분리 명시 배선.

Errors (health)
- 신호: cpu_mce(Machine Check Exception counter) 창 내 delta.
- 임계: delta > 0 = 오류 발화(USE: errors nonzero 자체가 문제). 사이징 아님, health 경고. basis: convention
  — Gregg USE, Linux MCE/EDAC.
- robustness: 단일 버킷 1회 증가 = 정정 가능·일시(경고 억제 가능), 다수 버킷 monotonic 증가 = 하드웨어 열화
  (강한 경고). rate 로 일시/지속 구분. Windows WHEA 소스 상이 -> 미발행 시 미노출(coverage 축 아님).

### Memory

Utilization
- 신호: Linux 1 - mem_available_bytes/mem_limit_bytes p95 (available 가 reclaimable 캐시 제외 -> page cache
  오탐 제거). Windows 물리 used% + commit(mem_commit_usage/limit) 병용.
- 임계: under p95 >= 90%; 사이징 착지 80%(20% headroom, 확정); Windows 커밋 >= 80% 가드. basis: vendor-stated
  — Azure Advisor(메모리 90% resize 관례), AWS Compute Optimizer(headroom 기본 20% = 목표 80%, 확정 정합),
  MS Perfmon(% Committed Bytes In Use > 80% 또는 Available < 5%).
- robustness: 14일 p95. available 기반으로 캐시 오탐 제거. Windows 물리 used% 는 캐시로 상시 높으니 커밋률 병행.

Saturation (classify)
- 신호: PSI 를 뺀 고전 신호. Linux = paging_major(하드폴트/refault rate) + paging_out(swap-out rate) + oom_kill.
  Windows(PSI 원래 없음) = Pages Input/sec 하드폴트 rate. 신호원은 mem_saturated() os-aware helper 단일 진실.
- 판정: dual-gate 로 swappiness 오탐 방지 — "util 높음(available p95 낮음) AND paging_major rate sustained"
  일 때만 saturation. paging_major(재접근이 강제한 하드폴트)를 주 신호로 씀 — 단순 paging_out 은 swappiness
  (기본 60)로 여유 RAM 에도 유휴 페이지를 내보내 압박 아닌데 발화하므로. oom_kill delta >= 1 은 dual-gate
  없이 즉시 under 확정(사후 증거).
- 임계: paging_major sustained rate(창 다수 버킷 nonzero) + util p95 >= 90%; Windows Pages Input/sec p95 >= 20.
  basis: convention — MS Perfmon(하드폴트/Pages Input 지속 주의), Red Hat(vmstat si/so 지속 = thrashing).
- robustness: paging 은 counter delta rate(창 전반, 스파이크 견고). dual-gate(util AND paging)로 idle
  swap-out FP 차단. oom 은 이산 이벤트라 count>=1 flag + 창 내 발생 횟수 노출(일회 runaway vs 반복 구분).

Errors (health)
- 신호: oom_kill(mem_oom_kill counter) delta + hardware_corrupted(mem_hardware_corrupted_bytes gauge).
- 임계: oom_kill delta >= 1 = under 확정(강한 신호, Saturation 과 공유); hw_corrupted > 0 = health 경고(DIMM
  불량, 사이징 아님). basis: convention — Gregg USE Linux checklist, kernel meminfo.
- robustness: hw_corrupted 는 단조 gauge(리부트 리셋) -> 0 초과 지속·재발 = DIMM 교체 신호. disk_error 와 동일
  health 축 노출.

### Disk-capacity

용량은 under/over 가 아닌 남은 시간(누적 자원). 상태 = filling / capacity_ok / unmeasured. host_saturation_unmeasured
대상 제외(_SATURATION_KINDS=cpu/memory/disk_io 한정).

Utilization
- 신호: used% = used_bytes/(used_bytes+free_bytes) worst mount. inode% = inodes_used/(inodes_used+inodes_free).
  확장여력 = server_inventory.lvm_vgs[].free_bytes.
- 임계: used% >= 85(정적 major 가드); inode% >= 85(ext 계열 fstype 게이트 — XFS/btrfs 동적 inode 오탐 방어).
  basis: convention — 업계 80 warn / 90 crit 관례, 분류축은 85 단일 major(엔진 judgment). UI badge 2단(90/75)은
  별 도메인 — 혼용 금지. 출처: Gregg(df -h), monitoring-plugins check_disk, site24x7 inode.
- robustness: 용량은 누적/단조라 순간 스파이크 여지 적음. worst-mount = 마운트별 max. 작은/가상 파티션
  (/boot·/var/log·tmpfs·overlay) 제외 필터가 오탐 방어 핵심(open — 현행 필터 확인 후).

Saturation (classify)
- 신호: runway = used 추세 회귀 ENOSPC 도달 일수 + inode 소진 일수. _min_runway 로 먼저 차는 축 채택.
  가용 이력 전체 span 사용(누적 신호라 길수록 정확).
- 임계: runway < 30일(RS_DISK_RUNWAY_DAYS) = filling; inode runway 병렬 30일. 추세 불가 시 정적 85% fallback.
  basis: judgment(30일 = 프로비저닝 lead time) / vendor-stated framing — Gregg(capacity saturation = ENOSPC 임박).
- 확장여력 처방 (v2 개선): filling 이거나 used% 높을 때 lvm_vgs free 확인 — VG 여력 있으면 "LV in-place 확장
  (lvextend+resize)" 처방(긴급도 하향, 새 디스크 불요), 여력 없으면 "스토리지 증설". 같은 85% 라도 확장 가능
  여부로 처방·긴급도가 갈린다.
- robustness: 짧은 span 과외삽 방지(span<14d 면 1년 목표 억제). 2점 fill_rate -> 창 내 다점 Theil-Sen 강건 회귀로
  스파이크·가속 완화(권고, CPU util_trend 기법 재사용). 순간 삭제/생성 튐 caveat.

Errors (health)
- 신호: server_disk_error fs 계열 — ext4 errors_count, btrfs corruption, mdraid member_errors/degraded.
- 임계: 창 내 delta(count) > 0 = health(누적 counter 라 절대 nonzero 는 과거 1회도 영구 발화). mdraid degraded
  는 상태라 관측 즉시 경고. basis: vendor-stated — Gregg USE, btrfs device stats, mdadm.
- robustness: 창 delta(신규 에러)로 stale 방지. 단일 증가 = 정보, 반복/지속 = 강한 health. counter_agg reset-safe.

### Disk-IO

saturation 단일 축(증분 불가라 사이징 없음, biased=True — virtio 게스트 지연은 하이퍼바이저·이웃 간섭 편향).

Utilization (정보 축)
- 신호: %util = delta(io_time_s)/delta(wallclock) per device p95.
- 임계: p95 %util >= 70% = busy(Gregg). 단 %util 단독 io_bound 분류 금지 — SSD/NVMe 병렬성으로 오표시.
  basis: vendor-stated — Gregg USE + SSD %util caveat.
- robustness: p95. SSD false-positive 가드 = %util 단독 분류 금지, await corroboration 필수.

Saturation (classify)
- 신호: await p95 = delta(op_read_time_s+op_write_time_s)/delta(ops_read+ops_write) per device (ms). 보조
  pending_ops(큐 gauge). Windows await 미배선 시 disk_queue_p95 폴백. disk_io_saturated() os-aware helper 단일 진실.
- 임계: await p95 > 20ms = io_bound(평면, HDD/SSD 차등 없음 — v2 에 rotational 신호 부재). Windows IOCTL 동일
  20ms, 구세대 viostor 폴백 disk_queue_p95 >= 2.0 -> 불가 시 None=coverage_gap. basis: vendor-verified —
  VMware(GAVG/DAVG >20-30ms concern), SQL Server(sec/Read·Write >20ms poor), iostat(await SSD<10 HDD<20ms).
- 근본원인: rollup_host 에서 disk await + cpu_blocked D-state p95 >= 1 이면 root_cause=disk_io, CPU 증상 억제.
- robustness: await p95(14d, 순간 지연 max 오탐 방지). io_bound<->io_ok 플래핑 방지 hysteresis 권고. pending_ops
  큐 보조는 NVMe 깊은 큐 정상 감안.
- v1 변경: await 임계·집계 유지(baseline). HDD/SSD 차등은 rotational 신호 추가 전까지 평면 20ms(open).

Errors (health)
- 신호: server_disk_error(device_id, error_kind=mdraid|btrfs|ext4|eventlog|ioerr, count counter).
- 임계: 창 내 delta(count) > 0 = health(saturation 과 분리). mdraid degraded 즉시 경고. basis: vendor-verified —
  btrfs device stats, Software-RAID HOWTO(/proc/mdstat).
- robustness: btrfs stats 는 lifetime 누적 -> 창 delta(신규)로 판정. 단발 io_error = 정보, degraded 지속 = 실패.

### Network

사이징 축 아님(vNIC link_speed 부재로 utilization 기반 사이징 불가). trigger OR -> congested/quality_ok/unmeasured,
HostAssessment.network_congested 플래그로만 orthogonal 노출(ADR 0052).

Utilization
- 신호: rx/tx_bytes counter delta -> bps / link_speed_bps. virtio/vNIC link_speed=null 이라 대부분 산출 불가 ->
  net_avg_kbytes_per_s(절대 throughput)로 유휴만 판정.
- 임계: 사이징 fault 임계 없음(의식적 부재). idle net <= 2Mbps = 활동 축, 강한유휴 <=1kB/s. basis: judgment —
  Gregg(net util 高가 곧 문제 아님), AWS Compute Optimizer idle.
- robustness: throughput counter delta 창 avg. idle 은 활동 없음 확인(스파이크 avg 희석).

Saturation (classify)
- 신호(로컬 saturation 2신호 OR): (A) conntrack = net_conntrack_usage/net_conntrack_limit 비율(gauge, Linux
  nf_conntrack 로드 시만). (B) drop rate = rx_dropped/tx_dropped delta / packets delta. retransmit 은 여기서 제외
  -> Errors 축으로 이동(원격 혼잡 성격, USE 상 error/quality).
- 임계: conntrack >= 80%(crit, 신규연결 드롭 임박; 70% warn 2단 권고). drop rate > 0.5%. basis: convention —
  Gregg(network saturation = dropped/buffer overruns), conntrack 70-80% 관례(Prometheus node_exporter), rx_dropped
  (Red Hat tuning).
- robustness (현행 순간 임계의 핵심 보강): (1) counter 신호(drop)는 창 전체 delta rate(단일 버킷 스파이크 무시).
  (2) conntrack(gauge)은 p95. (3) sustained 요건(다수 버킷 초과 시만 trigger — 순간 마이크로버스트 배제).
  (4) hysteresis(conntrack 80 진입 / 70 해제). (5) burst 허용(창 avg rate, 순간 max 금지).
- v1 변경: retransmit 을 saturation 에서 빼 Errors 로 이동. drop+conntrack 만 로컬 saturation. 순간 비교 ->
  창 rate/p95 + sustained + hysteresis 보강.

Errors (health)
- 신호: rx/tx_errors(counter) delta = 프레임/CRC/collision 에러율 + net_tcp_retransmits(재전송률, error 성격).
- 임계: rx/tx_errors sustained nonzero = health(절대 rate 임계 벤더 부재). retransmit > 1%(성능영향, >5% 심각).
  basis: vendor-stated — Gregg(network errors), motadata/oneuptime(TCP retransmit <1% healthy). retransmit 을
  saturation 아닌 여기에 두는 게 USE 정합(원격 손실 포함).
- robustness: counter delta 창 rate. 단발 에러 = 정상(케이블 노이즈), sustained/누적 rate 초과 = 발화.
- v1 변경: 현행 "virtio 0이라 미사용" 폐기를 뒤집어 disk_error 동형 health 노출(물리 NIC CRC/프레임 조기신호).
  virtio 게스트는 0 무발화(무해).

---

## 엣지케이스 견고성 공통 원칙

임계 수치만큼 순간 스파이크 오분류 방어가 중요하다. 자원 공통 규율:

1. 창(window) 3분리 (F10 정합): 분류·신뢰도 = 14일(WINDOW_DAYS) / 용량 runway = 가용 이력 전체 span /
   모니터링 게이지 = 24h(DASHBOARD_TIME_RANGE, 분류 아닌 스냅샷). 서버 상세 실시간 차트는 별도(15m).
2. 집계 = p95 기본: gauge(run_queue·conntrack·%util·await)는 p95 후 임계 비교 — 순간 max 금지. idle 만 avg.
3. counter 는 delta rate: counter(cpu_mce·oom_kill·disk_error·retransmit·drop·paging)는 창 delta — 절대 nonzero
   는 과거 1회 이벤트가 영구 발화하는 stale 오탐. counter_agg reset-safe delta 로 재부팅/재시작 흡수.
4. 지속시간(sustained) 요건: retransmit/drop/paging 은 다수 버킷 초과일 때만 trigger — 단일 마이크로버스트
   (정상 TCP 혼잡제어·writeback·swappiness) 배제.
5. hysteresis: 플래핑 위험 경계에 진입/해제 분리(conntrack 80/70, io_bound<->io_ok). 창 통계(p95)는 진동
   낮아 CPU/mem util 미적용.
6. 표본 충분성: p95 자체가 스파이크 가드. 절대 바닥 = RS_CONFIDENCE_MIN_HOURS=30h(신뢰도 하향). 다운사이즈
   게이트 = 관측 비율 RS_DOWNSIZE_MIN_SUFFICIENCY=0.7. 미측정 축 None -> coverage_gap(cpu/memory/disk_io 한정).
7. 강건 회귀(용량 runway): 2점 fill_rate -> 창 내 다점 Theil-Sen 으로 스파이크/가속 완화(권고).

---

## 확정 결정 반영

- swap 런타임 추이 축 폐기: swap 은 용량(block_devices type=swap)만. 메모리 포화는 paging(major/out)+oom 으로.
- server_disk_error 를 health 축으로 노출: 5자원 Errors(cpu_mce·mem_hardware_corrupted·disk fs errors·net
  rx/tx_errors)를 사이징·saturation 과 분리한 orthogonal health 축으로 통일. delta>0 기준, 사이징 미투입.
- load average 폐기: v2 소스 부재. cpu_run_queue 로 대체(D-state IO 오염 배제).
- PSI 판정 미투입: server_pressure 저장은 유지, recommendation.py 미배선(Deferred 절).

---

## OS 분기 (판정은 고전 신호 -> 커버리지 구멍 없음)

판정을 고전 신호로만 하므로 PSI 부재 환경(Windows·구커널)도 동일 축으로 판정된다.

```
AXIS            | LINUX (all)                    | WINDOWS / LEGACY
----------------+--------------------------------+-------------------------------------------
CPU sat         | run_queue procs_running/cores  | run_queue Processor Queue Length/cores
                |  >= 1.0                         |  >= 2.0 (ready-only 모집단)
MEM util        | 1 - available/limit            | 물리 used% + commit/commit_limit >= 80%
MEM sat         | paging_major refault + util    | Pages Input/sec p95 >= 20
                |  (dual-gate) + oom_kill         |  + oom 등가 부재 -> commit 압박
DISK-IO sat     | await p95 > 20ms               | await(IOCTL) 주, disk_queue >= 2 폴백
CPU err         | MCE/EDAC (cpu_mce)             | WHEA 소스 상이 -> 미노출
DISK err        | ext4/btrfs/mdraid class        | eventlog (error_kind=eventlog)
INODE/LVM       | ext 계열 / lvm_vgs             | NTFS MFT (inode 개념 없음, null)
NET conntrack   | nf_conntrack (모듈 로드 시)     | 항상 null (WFP 별개, 미노출)
PSI (all res)   | 저장만, 판정 미투입 (Deferred) | 원래 부재
```

- os-aware helper(cpu_saturated·mem_saturated·disk_io_saturated)가 분기 단일 진실 — 반환 계약 bool|None(None=
  coverage_gap) 불변. Windows 임계가 다른 건 모집단 차이지 임계 불일치 아님. steal 은 양 OS 가상화 게스트만 유의.

---

## Deferred — 운영 데이터 확보 후 별도 ADR (지금 저장만, 판정 미투입)

수집은 하되 판정에 안 물리는 항목. 운영 데이터로 캘리브레이션 후 근거 갖고 도입한다. 지금 저장하므로 도입 시
재수집 갭 없음.

- PSI 전면 (server_pressure cpu/memory/io) — stall 임계가 벤더 미문서화 + 데이터 없음. 도입 시 결정: cpu some
  발화 임계, memory some avg10 임계, io some/full 임계, 주신호 승격 vs corroboration, 근본원인(rollup_host)
  인과 사슬 편입(PSI io/memory 교차로 CPU stall 귀속).
- HDD/SSD await 차등(SSD>10ms/HDD>20ms, vendor-verified) — v2 block_devices 에 rotational/media-type 신호 부재.
  agent 발행에 rotational flag 추가 여부 결정 후. 그 전까지 평면 20ms.
- paging_major/swap-in 폴백 절대 rate 세부값(15~20/s) — 디스크 속도 의존. 초기엔 sustained rate>0 dual-gate 로
  운영하고 fleet 분포로 절대값 캘리브레이션.
- cpu_mce CE vs UCE 심각도 분리 — 현 스키마 총 count 단일. UCE 강경보는 agent 스키마 kind 분리 필요.

---

## 확정된 결정 (사용자 선택 2026-07-09)

1. 메모리 사이징 목표 = 80% (20% headroom). AWS Compute Optimizer 기본 정합 — 비용 최적 방향(70% 보수안 대신
   80% 선택). 다운사이즈 판정이 그만큼 적극.
2. 메모리 포화 = dual-gate AND: "util p95 >= 90% AND paging_major(refault) rate sustained" 둘 다 참일 때만.
   paging 단독은 mmap/시작 하드폴트 오탐(예: RAM 여유 많은 mmap DB), util 단독은 페이지캐시 오탐 -> AND 로 차단.
   paging_major(재접근 강제 폴트)를 주 신호. oom_kill delta >= 1 은 gate 없이 즉시 under 확정.
3. 네트워크 포화 = drop rate > 0.5% + conntrack ratio >= 80%(70 warn 2단, hysteresis 80 진입/70 해제) 로컬
   saturation. retransmit 은 saturation 에서 제외 -> Errors 축으로 이동(원격 혼잡 오귀속 방지, Gregg USE 정본
   분류: dropped=saturation / retransmit=errors).
4. Errors/health 노출 = 전부 >0 즉시. disk_error + net rx/tx_errors + cpu_mce + mem_hardware_corrupted 를 사이징·
   saturation 과 분리한 health 축으로 통일 노출(물리/베어메탈 fleet 조기신호, virtio 는 0 무발화).
5. 용량 = runway 30일 유지 + inode ext 계열 fstype 게이트(XFS/btrfs 동적 inode 오탐 방어) + 2점 fill_rate ->
   Theil-Sen 강건 회귀 전환.
6. worst-mount = 작은/가상 파티션(/boot·/var/log·tmpfs·overlay 등) 제외 필터 적용(현행 필터 확인 후 정합).
7. 재전송률 분모 = tx_packets 근사 유지(비-TCP 혼입으로 과소 -> 보수적, 오탐 감소). agent OutSegs 계약 변경 안 함.

전부 vendor / convention 근거 또는 의식적 judgment — 지어낸 값 없음. PSI 전면·HDD/SSD await 차등·CE/UCE 분리·
paging 절대 rate 세부값은 Deferred 절(운영 데이터·rotational 신호 확보 후 별도 ADR).

## 관련 문서·코드

- ADR 0052 — 배선 원칙(전제 유도 + USE 5자원 + tier 근거). 0052=원칙, 0053=v2 고전 신호 확정.
- ADR 0043 — counter_agg 사전집계(신호 원천, reset 처리). ADR 0049 — agent_id 매칭 키.
- src/assessment_engine/recommendation.py — 구현 대상(assess_* + rollup_host + os-aware helper).
- docs/reference/right-sizing.md — 구현 상태 명세(Gate0 확정 후 v2 신호로 갱신 + 인간가독 임계 문서 동반).
- CLAUDE.md #E3·#F10·#B — 분류 단일 진실 + 평가 윈도우 + 미사용 필드 저장/명시 도입.
