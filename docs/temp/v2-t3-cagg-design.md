# T3 — continuous aggregate v2 재설계

성격: 내부 구현 설계 (docs/temp, 삭제 자유). squash 로 사라진 v1 cagg 5종을 v2 컬럼(s/By·counter_agg·신
자연키)과 확정 진단모델(Gate0) 신호로 재정의한다. 확정 Gate0 = docs/temp/gate0-diagnosis-model-adr-draft.md,
임계·처방 정본 = docs/reference/right-sizing-thresholds.md.

## 핵심 변경 (v1 -> v2)

- 단위: cpu jiffies -> cpu_*_s(seconds counter_agg), mem kB -> bytes(gauge 비율은 값 불변, 컬럼명만), sectors ->
  io_*_bytes(이미 bytes, *512 폐기).
- 자연키: server_disk_io.device -> device_id, server_net_io.interface -> iface_id, server_mount_usage.mount ->
  server_filesystem.mountpoint. 테이블 server_mount_usage_5m -> server_filesystem_5m 개명.
- 물리/가상 필터 이동 (설계 결정): v1 은 device 이름 regex·mount major·net bond_master 필터를 cagg 정의에 박아
  뒀다. v2 device_id/iface_id 는 안정키(이름 아님)라 regex 불가 + kind 컬럼 폐기. -> cagg 는 필터 없이 전체
  집계하고, 물리/가상·bond 선별은 query 시 inventory(block_devices.type / net_interfaces.kind) 조인으로 한다.
  이점: 필터 규약 바뀌어도 cagg 재생성 불요(C4 고통 해소), 진실 소스가 inventory 단일. 작은/가상 fs 제외는
  fstype/mountpoint 기준(확정 결정 6).
- 신호 반영 (Gate0 확정): paging_major/out counter_agg, oom_kill counter_agg, cpu_run_queue·cpu_blocked·cpu_steal,
  cpu_mce, conntrack ratio, net drops, hw_corrupted, commit(Windows). 폐기: load_15m, swap, sat_*, mem_pages_input.
- PSI(server_pressure)는 Deferred -- 판정 미소비. cagg 미생성(저장만). 도입 시 별도 cagg + report 배선.
- disk error(server_disk_error)는 저volume·sparse -> cagg 없이 query 시 창 delta 집계.

## cagg 별 v2 정의

버킷 = 5분(time_bucket). counter_agg = 카운터(reset-safe delta), avg/max = 게이지. p95 는 query 시 버킷들 위에서
percentile_cont (cagg 는 5분 집계까지, 창 p95 는 report.py).

### server_metrics_5m (host 집계)

NK = (server_id, bucket). FROM server_metrics.

```
counter_agg:
  cpu_total_ca   = counter_agg(collected_at, cpu_user_s+nice+system+idle+iowait+irq+softirq+steal)
  cpu_idle_ca    = counter_agg(cpu_idle_s)
  cpu_user_ca    = counter_agg(cpu_user_s)
  cpu_system_ca  = counter_agg(cpu_system_s)
  cpu_iowait_ca  = counter_agg(cpu_iowait_s)
  cpu_steal_ca   = counter_agg(cpu_steal_s)          -- steal 인과 분리
  paging_major_ca= counter_agg(paging_major)          -- 메모리 포화 주신호(refault)
  paging_out_ca  = counter_agg(paging_out)
  paging_in_ca   = counter_agg(paging_in)             -- Windows 하드폴트 등가
  oom_kill_ca    = counter_agg(mem_oom_kill)
  tcp_retrans_ca = counter_agg(net_tcp_retransmits)   -- Errors 축
  cpu_mce_ca     = counter_agg(cpu_mce)               -- health

gauge (avg/max + sample):
  mem_pct_avg/max/sample = (1 - mem_available_bytes::float/mem_limit_bytes)*100   [mem_limit_bytes>0 AND available NOT NULL]
  commit_pct_avg/max     = mem_commit_usage_bytes::float/mem_commit_limit_bytes*100  [Windows]
  run_queue_avg/max      = cpu_run_queue
  blocked_avg/max        = cpu_blocked                -- D-state 근본원인
  conntrack_ratio_avg/max= net_conntrack_usage::float/net_conntrack_limit          [limit>0]
  hw_corrupted_max       = mem_hardware_corrupted_bytes
  sample_count           = count(*)
```

폐기: load_15m_max, swap_in_use, sat_disk_*(Windows IOCTL — disk_io_5m await 로 단일화), mem_pages_input_ca.

### server_filesystem_5m (개명, was server_mount_usage_5m)

NK = (server_id, mountpoint, bucket). FROM server_filesystem. 필터 없음(작은/가상 fs 제외는 query 시).

```
  used_pct_max/avg  = used_bytes::float/(used_bytes+free_bytes)*100      [used+free>0]
  total_bytes_max   = max(used_bytes+free_bytes)
  free_first/last   = first/last(free_bytes, collected_at)               -- runway 회귀용
  inode_pct_max/avg = inodes_used::float/(inodes_used+inodes_free)*100   [inodes_used+free>0]
  inode_free_first/last = first/last(inodes_free, ..)                    -- inode runway
  fstype_any        = 대표 fstype (query 시 ext 게이트·가상 fs 제외 판단)
```

runway 는 report.py 가 free_first/last + bucket span 으로 Theil-Sen(다점) 회귀 -- v1 2점 fill_rate 개선(확정 5).

### server_disk_io_5m

NK = (server_id, device_id, bucket). FROM server_disk_io. 필터 없음(물리 선별 query 시 block_devices.type 조인).

```
  io_read_ca   = counter_agg(io_read_bytes)     -- *512 폐기(이미 By)
  io_write_ca  = counter_agg(io_write_bytes)
  ops_read_ca  = counter_agg(ops_read)
  ops_write_ca = counter_agg(ops_write)
  op_rtime_ca  = counter_agg(op_read_time_s)    -- await = Δ(rtime+wtime)/Δ(ops), s (ms 변환 폐기, *1000 표시경계)
  op_wtime_ca  = counter_agg(op_write_time_s)
  io_time_ca   = counter_agg(io_time_s)         -- %util = Δio_time/Δwall
  pending_avg/max = pending_ops                 -- 큐 게이지
```

await = (delta(op_rtime_ca)+delta(op_wtime_ca)) / NULLIF(delta(ops_read_ca)+delta(ops_write_ca),0). 단위 s ->
표시·임계 비교는 20ms 이므로 초 그대로 비교(0.02) 또는 *1000. Windows IOCTL 100ns/10000 보정 폐기(v2 s 직접).

### server_net_io_5m

NK = (server_id, iface_id, bucket). FROM server_net_io. 필터 없음(물리/bond 선별 query 시 net_interfaces.kind).

```
  rx_ca/tx_ca     = counter_agg(rx_bytes)/(tx_bytes)
  rxp_ca/txp_ca   = counter_agg(rx_packets)/(tx_packets)   -- 재전송·드롭 분모
  rxd_ca/txd_ca   = counter_agg(rx_dropped)/(tx_dropped)   -- 로컬 saturation
  rxe_ca/txe_ca   = counter_agg(rx_errors)/(tx_errors)     -- Errors 축
  link_max        = max(link_speed_bps)                    -- util% 분모(null 다수)
```

### server_cpu_core_5m

NK = (server_id, core_id, bucket). FROM server_cpu_core. per-core 이용률.

```
  cpu_total_ca = counter_agg(cpu_user_s+..+steal)
  cpu_idle_ca  = counter_agg(cpu_idle_s)
```

per-bucket core util% = 1 - delta(idle)/delta(total). report.py 가 코어별 p95 최대(cpu_percore_p95_max).

## refresh 정책 (5종 공통)

- add_continuous_aggregate_policy: start_offset = WINDOW_DAYS + 버퍼(예 16d), end_offset = 10m(미완 버킷 real-time
  agg 위임), schedule_interval = 5m.
- materialized_only = false (실시간 tail 은 real-time aggregation).
- 정의+정책은 마이그레이션 트랜잭션 내, 초기 refresh_continuous_aggregate 는 트랜잭션 밖 1회 (#C4).

## 마이그레이션 방법

- baseline(53df4c2132fd) 위 신 revision. create_table 아님 -> `op.execute` 로 CREATE MATERIALIZED VIEW 5종 +
  policy. cagg 는 ORM 모델 아니라 Base.metadata 부재 -> `alembic check` drift 무관(단 _include_object 가 cagg
  대상 테이블 자동생성물 무시하는지 확인).
- 검증: throwaway v2 DB(timescaledb-ha:pg16)에 baseline + 본 revision upgrade -> cagg 5종 생성 확인 +
  샘플 INSERT 후 delta/await/used_pct 산출값 sanity.

## 다운스트림 핸드오프 (T4/T5)

- report_aggregate(report.py): 위 v2 컬럼으로 SELECT 재작성. 물리 device/bond/작은fs 필터를 inventory 조인으로.
  단위 함정(sectors*512 제거, mem kB->By, await s). ReportRowRaw 필드 v2.
- ResourceStats(build_resource_stats): 확정 신호 배선 — cpu_run_queue/steal/blocked, paging_major(dual-gate),
  conntrack, drop rate, await, used/inode/runway, lvm free. mem 목표 80%.
- recommendation.py: assess_* 확정 임계(Gate0) + prescription 확정(ceil·단일목표·현행 여유값). RS_MEM_SIZING_TARGET_PCT
  70 -> 80.
