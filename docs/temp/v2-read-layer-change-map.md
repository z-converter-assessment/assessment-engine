# v2 read/query 계층 변경 지도 (자율 스캔 산출물)

성격: 내부 구현 계획 입력(삭제 자유). v2 스키마 전환 후 read 계층 185 결합점을 tier별로 스캔한 결과.
작업 방향 = 진단모델 먼저 -> 신호 확장 (Gate0 ADR -> T3 cagg -> T4 recommendation -> T5a 구조포팅 -> T5b enrichment).
정본 계약 = docs/reference/contracts/agent-data.md. 본 지도는 Gate0 진단모델 결정의 입력.

---

# v2 스키마 read 계층 재배선 지도 (tier별)

## 1. 요약 — 표현계층/API 설계할 때 쿼리가 왜·얼마나 바뀌나

write(수집·저장)는 이미 v2인데 read(query repository)만 v1 컬럼·자연키·cagg를 참조해서, 지금 `db/repositories/query/{report,metric,server,types}.py` 전체가 실행 시(또는 import 시) 깨진 상태다. 그래서 표현계층·API를 새로 설계하려면 "화면 배선"만 손대는 게 아니라 그 아래 3층을 순서대로 다시 깔아야 한다: (T3) report.py가 소비하는 continuous aggregate 5종이 squash로 전부 삭제됐으니 v2 컬럼(jiffies->seconds, kB->bytes, sectors->bytes, device/interface/mount->안정키)으로 재생성해야 하고, (T4) 그 cagg가 채우는 ResourceStats가 right-sizing 분류의 유일한 입력이라 삭제된 신호(sat_*/load/swap)를 새 신호(PSI stall_time·paging_in/out·cpu_run_queue)로 대체·재배선해야 하며, (T5) outbound DTO 필드명과 metrics_calculator·mapper·template·JS 체인이 v1 단위/이름에 하드코딩돼 있어 한 컬럼 rename이 서버상세·환경개요·보고서·목록 전 화면으로 fanout된다. 핵심 위험은 두 종류의 단위 함정(sectors*512 이중환산, mem kB->bytes 1024배)과 "삭제된 축을 다른 신호로 대체"(load->run_queue, sat_disk_queue->await/PSI, mem_pages_input->paging)라 단순 sed rename으로는 못 끝난다.

---

## 2. T3 — continuous aggregate 재생성 (read의 기반, 최우선 blocker)

5개 cagg 모두 `v1_revisions_backup/`에 원본 있음. v2 컬럼 기준 `CREATE MATERIALIZED VIEW` 재작성 필요. 소비처는 전부 `report.py` 한 파일.

server_metrics_5m — `report_aggregate` / `report_cpu_breakdown_batch` / `environment_utilization`
- counter_agg 입력 `cpu_user..cpu_steal`(jiffies) -> `cpu_*_s`(seconds). `cpu_total_ca` COALESCE 8성분 합·`cpu_idle_ca`/`user`/`system`/`iowait`/`steal_ca` 전부 `_s` 기반으로. 소비측 delta 비율(1-idle/total 등)은 분자·분모 동시 스케일이라 값 불변 -> 컬럼명만 교체.
- `mem_pct_avg/max`: (1-mem_available_kb/mem_total_kb) -> `mem_available_bytes`/`mem_limit_bytes` 분모로.
- `load_15m_max` 컬럼 제거(load_* 삭제). `swap_in_use` 재정의(swap_*_kb 삭제). `disk_queue_avg`(sat_disk_queue)·`sat_disk_time_ca`/`sat_disk_count_ca`(Windows IOCTL) 제거 -> disk await는 server_disk_io_5m로 단일화.
- `cpu_run_queue_avg`=avg(cpu_run_queue), `procs_running_avg`와 v2에서 같은 컬럼으로 수렴(둘 다 cpu_run_queue). `procs_blocked_avg`=avg(cpu_blocked).
- `pswpout_ca`=counter_agg(paging_out), `mem_pages_input_ca` 제거, `oom_kill_ca`=counter_agg(mem_oom_kill), `tcp_retrans_ca`=counter_agg(net_tcp_retransmits), `conntrack_ratio_max`=max(net_conntrack_usage/net_conntrack_limit).

server_mount_usage_5m -> server_filesystem 기반 신 cagg — `report_aggregate`(mount_max·mount_span) / `report_mount_usage_batch`
- FROM `server_filesystem`, 자연키 `mount`->`mountpoint`(필요시 device_id). `used_pct`=used_bytes/(used_bytes+free_bytes)*100, `total_bytes_max`=max(used_bytes+free_bytes), `avail_first/last`=first/last(free_bytes). inode: `inodes_total` 삭제 -> inodes_used/(inodes_used+inodes_free). `kind='data'` 필터 -> fstype/device_id 기반 가상fs 제외로 교체.

server_disk_io_5m — `report_aggregate`(disk_await) / `report_disk_io_baseline`
- 자연키 `device`->`device_id`. `reads_ca/writes_ca`=counter_agg(ops_read/ops_write). `sread_ca/swritten_ca` -> counter_agg(io_read_bytes/io_write_bytes) 이고 소비측 `*512` 제거(이미 bytes). `treading_ca/twriting_ca`=counter_agg(op_read_time_s/op_write_time_s)(ms->s). `WHERE kind='physical'` 삭제 -> device_id 기반 물리판별 또는 무필터+표시경계.

server_net_io_5m — `report_aggregate`(net_quality) / `report_net_io_baseline`
- 자연키 `interface`->`iface_id`. `rxd_ca/txd_ca`=counter_agg(rx_dropped/tx_dropped). `rx_ca/tx_ca/rxp_ca/txp_ca`는 컬럼 불변(rx_bytes/tx_bytes/packets 유지)이나 `kind IN('physical','bond_master')` 필터 삭제 -> iface_id 기반 물리/본드 판정 재설계(안 하면 멤버 이중집계).

server_cpu_core_5m — `report_aggregate`(percore, cpu_percore_p95_max)
- `cpu_user..cpu_steal`(jiffies) -> `cpu_*_s`. core_util 비율식 유지, 자연키 (server_id, core_id) 불변.

추가: envelope 메타 이동 결합점(T3 성격). `report_uptime_stats`(DISTINCT boot_time)·`report_agent_restart_stats`/`agent_restart_counts_recent`(DISTINCT agent_started_at)가 `server_inventory_history`를 읽는데 — 단 실제로는 v2 `server_inventory_history`가 boot_time/agent_started_at를 그대로 보유(metric.py notes에서 확인)하므로 이 세 메서드와 `reboot_events`(metric.py:638-700)는 무손상. "메타는 server_metrics 전용" 델타는 disk_io/net_io/filesystem/cpu_core 자식 시계열에만 적용된다.

---

## 3. T4 — recommendation / right-sizing 분류 입력 재배선

`recommendation.py` 자체는 컬럼을 모르고 `ResourceStats`(dataclass)만 소비한다. 그래서 실제 파손은 (a) `report_aggregate` SQL이 참조하는 삭제/개명 컬럼(T3로 대부분 해소), (b) `mappers/report.py:build_resource_stats`의 단위 환산, (c) 병렬 실시간 경로 `metric.py:latest_saturation`의 직접 raw 참조에 있다.

순수 재설계 포인트 3가지 (단순 rename 아님):
- disk_io: `recommendation.disk_io_saturated`/`disk_io_saturation_index`가 await-first + `disk_queue_p95`(sat_disk_queue) 폴백 구조인데, sat_disk_queue 삭제. `server_pressure`(resource=io, stall_time_s counter + ratio_avg10/60/300 gauge)를 await보다 우선하는 PSI 분기를 신설하고 disk_queue 폴백은 제거. PSI는 저장은 되는데 아직 아무 분류도 소비 안 함 -> net-new 배선.
- memory: `mem_saturated`/`mem_pressure_active`/`_mem_paging_active`가 Linux `mem_swap_paging`(pswpout)·Windows `mem_pages_input_rate_p95`(mem_pages_input) 두 컬럼을 타는데, mem_pages_input 삭제 + pswpout->paging_out. 둘을 `paging_in`/`paging_out`로 os-aware 통합(Windows 하드폴트->paging_in, Linux page-out->paging_out). `mem_pressure_active(pages_input_rate, pageout_delta, os_family)` 시그니처와 실시간 소비자 동반 갱신.
- vestigial 축 제거: `load_15m`(assess_cpu는 이미 procs_running_p95 사용), `sat_disk_queue` 폴백 — v2에 소스 없음. `ReportRowRaw.load_15m_max`/`disk_queue_p95`/`ResourceStats.cpu_load_15m_max`는 rewire 아니라 삭제 대상.

Linux `procs_running` + Windows `sat_cpu_run_queue` -> 단일 `cpu_run_queue` gauge 병합. 현재 두-컬럼 COALESCE os-aware 모델(`cpu_saturated`/`_run_queue_value`)을 단순화할 수 있음.

단위 함정 (T4에서 silent 오분류 최고 위험):
- `mappers/report.py:build_resource_stats`의 `mem_total_mb=raw.mem_total_kb//1024`. server_inventory.mem_total_kb->mem_total_bytes면 `//1048576`로 안 바꾸면 1024배 -> `_mem_target_mb`/headroom이 터무니없는 값 -> 사이징 처방 붕괴.
- disk await: v1 `time_reading_ms`(ms) -> v2 `op_read_time_s`(초). `RS_DISKIO_AWAIT_MS=20ms` 비교·표시에 `*1000` 필요. Windows IOCTL의 `/10000`(100ns) 보정은 폐기.

실시간 경로 `metric.py:latest_saturation`(197-268)은 report_aggregate를 그대로 미러링해 `procs_running`/`sat_cpu_run_queue` COALESCE, `sat_disk_queue`, `pswpout`, `tcp_retrans_segs`, `mem_pages_input`, sat_disk_* IOCTL, `server_disk_io.time_*_ms`+kind, `server_net_io.tx_packets`+kind를 직접 SELECT -> 전부 깨짐 -> `SaturationRaw` 전 필드 None -> `environment.py`의 `cpu_saturation_index`/`disk_io_saturation_index`/`mem_pressure_active` 실시간 카드 공백. cagg가 아니라 raw 참조라 T3와 별개로 손봐야 함.

`environment_utilization`(577-624)은 cagg(cpu)+raw server_metrics(mem kB 가중)+raw server_mount_usage(disk) 혼합 -> 환경 개요 "자원 적정성" 카드(동일 14일 창 분류)까지 파급.

F9 lockstep: SQL(cagg) -> `ReportRowRaw` -> `build_resource_stats` -> `mappers/shared.py:saturation_axis_displays`(신호 라벨: Processor Queue/Pages Input/IOCTL await) -> `mappers/right_sizing_api.py` 표시까지 한 번에. 부분 배선하면 화면 간 분류 불일치(#E3).

---

## 4. T5 — display/API 필드 변경 체인 (화면 단위)

공통 계약: `db/dtos/outbound.py`가 아직 v1 필드명 보유(mem_total_kb·swap_*_kb·load_*·cpu jiffies·reads_completed·sectors·device·interface·kind·mount·total_bytes/avail_bytes). repo SELECT를 v2로 재정의할 때 이 DTO를 (a) rename하면 display가 AttributeError, (b) 이름 유지하면 kB->bytes 등 스케일 붕괴 -> 어느 쪽이든 아래 전부 동시 갱신 대상.

공통 기반 — `device_filters.py`
- `is_physical_disk`/`is_lvm_disk`/`is_partition`/`is_data_volume`/`is_virtual_interface`/`disk_total_bytes`가 전부 `kind` 인자 기반. kind가 자식 시계열·인벤토리에서 전면 삭제 -> device_id·fstype·block_devices.type 기반 재분류 계약 필요. 이게 깨지면 metrics_calculator·query/metric.py·server.py·attention.py 표시 필터가 연쇄 오작동.
- `types.py` dispatch 상수: `_CPU_TOTAL_EXPR`/`_CPU_NUMERATOR`(jiffies->_s), `_RATE_PER_DIM_DEFS`(device/interface->device_id/iface_id, `sectors_read*512/1024`->io_read_bytes/1024, reads_completed->ops_read), `_DATA_VOLUME_SQL_FILTER`/`_PHYS_DISK_SQL_FILTER`/`_PHYS_IFACE_SQL_FILTER`(kind 삭제->fstype/device_id 재설계), `_ENV_SCALAR_WEIGHTED`(mem_*_kb->bytes, swap 소스 소멸). 이 상수들은 chart(T5)와 cagg 정의(T3)가 동일 규약 공유 -> 양쪽 동시.
- import-time 死: `server.py:18`·`metric.py:20`의 `from ...server_mount_usage import ServerMountUsage`. 모듈 파일 삭제됨 -> ModuleNotFoundError로 두 파일 전체 로드 불가. 최우선 교체(->`server_filesystem.ServerFilesystem`).

서버 상세 실시간 대시보드 — `metrics_calculator.py` -> `view_models/metric.py` -> `cache_serializer.py` -> `detail.js`/`memory.js`
- `compute_mem`(143-175): mem_total_kb 삭제(총량 재조달)·나머지 kB->bytes -> `MemSnapshot` 재배선, 안 하면 usage_pct 1024배.
- `compute_swap`(178-190): swap_*_kb 소멸 -> `SwapSnapshot` 산출 불가, swap은 block_devices type=swap 집계로 재조달. `memory.js:loadSnapshot`·`detail.js`의 swap 섹션 게이트(`if swap.total_kb`) 항상 미표시화.
- `compute_cpu`(103-137): cpu_user..cpu_steal->cpu_*_s. 비율식 유지, 필드명만.
- `_disk_io_snapshot`(196-235): reads_completed->ops_read, sectors->io_bytes(이미 bytes라 `sector_to_kbps` 512배 함정), `rows[0].kind` 기반 phys/lvm/part 분류 붕괴 -> device_id/이름 재분류.
- `compute_net_io`(238-269): interface->iface_id/iface_name, `is_virtual_interface(kind)` 붕괴.
- `compute_mounts`(275-290): used=total-avail 산식 붕괴(v2 used_bytes 직접, total=used+free), `is_data_volume(kind)` 소멸.
- `build_dashboard`(86-89): load_1m/5m/15m 삭제(AttributeError), procs_running->cpu_run_queue(이름 이미 v2 정합, 저위험).
- `view_models/metric.py`: `MetricDashboard.load_*` 제거, `MemSnapshot.*_kb`/`SwapSnapshot.total_kb` -> bytes. `cache_serializer.py:dashboard_from_json/to_json`(106-132)이 필드명 하드코딩 -> ViewModel rename 시 캐시 히트 경로만 조용히 None(60s TTL 후 신선 경로와 divergence). #F9 (5)(6)(7) 동시.
- `metrics.js`: `loadLoadChart`(239, `load.15m`) 소스 소멸, `loadDiskQueueChart`(268-304, sat_disk_queue) 정지.

환경 개요(/) — `environment.py` -> `environment-metrics.js`
- `get_environment_realtime`(216-242): compute_mem/compute_mounts/SaturationRaw 붕괴가 그대로 전파 -> capacity-weighted 평균·disk_pool_pct·cpu_sat_index·disk_sat_index·mem_pressure 랭킹 값 손실. 부하상위·평균활용률 카드 공백.
- `get_right_sizing._disc_match`(147-162): `s.interfaces` dict의 `address` 키 접근 -> net_interfaces 소스·키 스키마 확인 필요.
- `environment-metrics.js`: `loadDiskSaturationChart`(255)·`net.retrans`(435)·`cpu.run_queue`(223) 세 차트가 삭제/rename 컬럼 MetricType 요청 -> Promise.all 실패가 같은 페이지 다른 차트까지 막음(#F9 차트 5-chain).

인벤토리/스토리지/네트워크 — `server.py` -> `mappers/server.py` -> `mappers/topology.py`
- `list_servers`(58-81): mem_total_kb->mem_total_bytes, disks->block_devices. `ServerSummary` DTO 동시.
- `_row_to_server_detail`(105-112): mem_total_kb/swap_total_kb/interfaces/disks/mounts 5개 삭제·개명. swap_total_kb·mounts는 v2 대응 컬럼 부재 -> 파생 재설계.
- `get_storage`(140-160)·`get_network`(179-207): `_latest_per_dimension` dim_col `mount`/`interface`->`mountpoint`/`iface_id`, MountUsageRaw total_bytes/avail_bytes->used/free.
- `mappers/server.py:build_inventory_snapshot`(182-193): `kb_to_gb(mem_total_kb)` 1024배 위험, swap 총량 카드·`_to_ip_addrs(interfaces)`·`disk_total_bytes(disks,mounts)` 붕괴.
- `to_storage_detail`/`build_volumes`(205-408): `dto.mounts`(인벤토리 mounts) 자체 삭제 -> storage_layers_gb 산식 원천 상실, block_devices+server_filesystem 조합 재설계.
- `topology.py:build_network_topology`(51-78): `h.interfaces` dict 키 kind/family/address/prefix/gateway 순회 -> net_interfaces 스키마 일치 필요(불일치 시 has_data=False).

보고서 — `report.py` -> `mappers/report.py` -> `single_report.html`
- `build_resource_stats`(382-416): T4 참조(mem_total_mb 1024배·load/swap/conntrack/retrans 재배선).
- `ReportRowRaw->ReportRowItem`(526,534,407): `kb_to_gb(mem_total_kb)` 1024배, `load_15m_max`는 `single_report.html:268`이 직접 표시 -> 원천 삭제로 항상 '—'.
- `single_report.html`: load(268)·mount(345, mountpoint)·cached/buffers(300-301, mem bytes 재계산). 정적 스냅샷이라 `report_serializer` 라운드트립 동반(#F9 스냅샷 nested).

attention / export
- `mappers/attention.py`(218-220,358,509-512): `total_mem_kb=Σ d.mem_total_kb` 1024배, `disk_total_bytes(d.disks,d.mounts)` 값 0, `efficiency_memory_gb` 단위 붕괴.
- `mappers/export.py`(164,187,20-32): `memory_mb=mem_total_kb//1024` 1024배, load export 항상 null, `_disk_from_mounts`의 inventory mounts fallback(Windows 디스크) 경로 상실.

`get_latest_metric`(query/metric.py 68-72): `SaturationRaw`를 채우던 saturation cagg 전량 squash 삭제 -> 서버상세 포화축 카드(detail.js disk-sat/mem-paging/net-retrans)·환경 실시간 포화지수 통째 비표시. server_pressure·counter_agg·신 자연키로 원천 재정의.

---

## 5. 순서 의존성 + 위험 지점

순서 (앞이 뒤의 blocker):
1. `server.py:18`·`metric.py:20` import 교체(server_mount_usage->server_filesystem). 안 하면 두 파일 import-time 死라 다른 결합점 손도 못 댐. 선행.
2. `outbound.py` DTO 필드명 정책 결정(bytes/seconds/신자연키로 rename). 전 체인의 계약이라 여기서 확정해야 아래가 한 방향.
3. T3 cagg 5종 재정의 마이그레이션(정의+정책 트랜잭션 내, `refresh_continuous_aggregate` 밖 1회 #C4). read의 기반.
4. `types.py` dispatch 상수 + `device_filters.py` kind 재설계. chart(T5)와 cagg(T3) 공유라 lockstep.
5. T4 recommendation 신호 재배선(PSI/paging/run_queue) + `build_resource_stats` 단위.
6. T5 표시 lockstep(mapper->ViewModel->cache_serializer->template->JS 동시).

단위 변환으로 SQL/계산식 자체가 바뀌는 곳 (silent 오값, rename 아님):
- sectors*512 제거: `report.py:24,451`(baseline `(delta(sread_ca)+delta(swritten_ca))*512`), `types.py:_RATE_PER_DIM_DEFS[disk.read_kbps]`, `metrics_calculator.py:_disk_io_snapshot`. v2 io_*_bytes는 이미 bytes -> *512 남기면 512배.
- mem kB->bytes: `build_resource_stats`의 `//1024`->`//1048576`, `mappers/server.py`/`attention.py`/`export.py`의 `kb_to_gb(mem_total_kb)`, `memory.js:fmtGb`(kb/1024/1024->bytes/1e9). 안 바꾸면 1024배.
- disk await ms->s: `report.py:disk_await` await_ms가 초 단위화 -> `disk_await_p95_ms` 표시·`RS_DISKIO_AWAIT_MS=20` 비교에 *1000. Windows `/10000`(100ns) 보정은 폐기.
- 반대로 안전(값 불변): percent 계열(cpu jiffies->s, mem kB->bytes 비율식, core_util)은 분자·분모 동시 스케일이라 값 보존 -> 컬럼명만 교체.

삭제 신호를 다른 신호로 대체하는 곳 (축 재설계):
- `load_1m/5m/15m` -> `cpu_run_queue` gauge (또는 차트 폐기). MetricType Literal·JS 토글·`_load_cols` 동시 제거.
- `sat_disk_queue` -> disk await / `server_pressure` PSI io stall. Windows 큐 폴백 은퇴.
- `mem_pages_input`(Windows 하드폴트 rate) -> `paging_in`. Linux `pswpout` -> `paging_out`. os-aware 통합.
- `swap_total_kb/swap_free_kb` -> block_devices type=swap(인벤토리) 또는 paging 신호. server_metrics에 시점별 swap 추이 원자료 없음 -> 환경 swap 차트 소스 재설계 또는 축 폐기.
- `sat_disk_*`(Windows IOCTL await/queue) -> `server_disk_io.op_*_time_s`(OS 통일) / `pending_ops` / PSI. `SaturationRaw`의 disk_queue_win·win_await·pages_input_rate 3필드 대체 소스 확정 필요.
- kind 필터(_DATA_VOLUME/_PHYS_DISK/_PHYS_IFACE) -> fstype/device_id/iface_id 조인 판정. 물리/데이터/가상 선별을 chart·cagg 양쪽 동일 규약으로.

net-new(현 결합점 아님, 별도 배선): `server_pressure`(PSI stall_time_s + ratio_avg)·`server_disk_error`는 read 계층 미참조. T4 재설계에서 disk_io/mem 포화를 PSI로 옮기려면 상류(agent wire -> inbound DTO -> 시계열 컬럼/Alembic -> cagg -> report_aggregate) 순으로 신규 배선.
