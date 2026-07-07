# ADR 0043 — 보고서 메트릭 집계 continuous aggregate + counter_agg reset 통일

상태: Accepted (2026-06-24)

## Context

right-sizing 분류·보고서가 매 요청 7일치 raw 시계열을 LAG window 로 스캔했다 — `report_aggregate`(CPU/mem/load)
693ms, `report_disk_io_baseline`·`report_net_io_baseline` per-device/interface LAG, `report_cpu_breakdown`.
서버목록 1.6s, 환경자원평가 3.4s.

CPU/disk/net 의 jiffies·bytes 는 카운터(단조 증가, 재부팅·wraparound 시 감소)다. 카운터 reset 처리가 코드마다
달랐다: CPU% 는 boot_time LAG gate(±5s)로 reset 행 제외, 그러나 disk/net baseline·cpu_breakdown 은 gate 없이
`delta>=0` 만 — 재부팅 점프 delta 가 baseline·peak·평균에 섞일 수 있는 불일치(weirdness). boot_time gate 자체도
agent 재시작·counter wraparound(boot_time 불변) reset 은 못 잡는 한계.

## Decision

카운터 메트릭 집계를 TimescaleDB continuous aggregate + timescaledb_toolkit `counter_agg` 로 사전집계·정석 통일.

- cagg: `server_metrics_5m`(CPU counter_agg total/idle/user/system/iowait + mem%/load/swap avg/max), `server_disk_io_5m`(per-device reads/writes/sectors counter_agg), `server_net_io_5m`(per-interface rx/tx bytes·packets counter_agg). 5분 버킷 = 클라우드 right-sizing 표준 granularity. real-time aggregation(materialized_only=false)으로 미materialize 최근 구간 실시간 집계 -> staleness 0. 5분 refresh policy. 가상 device/interface 는 cagg 단계 필터(물리만).
- reset: `counter_agg` 가 값-감소를 reset 으로 일률 감지·세그먼트 합산 — boot_time gate 불요, 재부팅·agent재시작·wraparound 전부 흡수. disk/net/cpu_breakdown 의 gate 누락 weirdness 근본 제거.
- percentile: cagg 버킷(7일=2016행/서버)에 쿼리 시 `percentile_cont`(정확) — 스캔량 감소로 정확 백분위 유지(tdigest 근사 불요).
- 재작성: `report_aggregate`·`report_cpu_breakdown`(+batch)·`report_disk_io_baseline`·`report_net_io_baseline` 가 cagg 조회.

## Options

### A. boot_time gate 를 disk/net/cpu_breakdown 에 보강 (raw 유지)
불일치는 맞추나 raw 스캔 비용 그대로 + boot_time gate 의 비-재부팅 reset 미감지 한계 잔존.

### B. counter_agg cagg (채택)
정석 reset 처리(값-감소 기준, 모든 reset 흡수) + raw 재스캔 제거(사전집계) + 정확 percentile. toolkit 가용(timescaledb-ha 이미지).

### "값이 바뀐다"에 대한 판단
5분 버킷 평균으로 per-bucket CPU% 가 1분 raw 대비 평활된다. 전 fleet 검증: cpu_p95 cross-70=0·mem_p95 cross-80=0, 최종 분류 변동 0/66. 1분 p95 의 transient blip 과민함 교정 측면도 있어 right-sizing 표준(5분)이 더 정합적(ADR 0029 evidence 기반 분류와 정합).

## Consequences

- 서버목록 1.57s -> 0.30s, 환경자원평가 3.43s -> 1.42s. report_aggregate 693ms -> 239ms.
- 카운터 reset 처리 단일화(counter_agg) — disk/net/cpu_breakdown weirdness 제거, CPU 와 동일 정석.
- 분류 안정(0 flips), sufficiency 비율 불변(버킷 분모 288/day 로 신구 동일 비율).
- environment_utilization(대시보드 24h)의 CPU 도 cagg counter_agg 로 통일(mem/disk 는 capacity-weighted KB gauge 라 raw 유지 — cagg 에 KB 합 없음, reset weirdness 무관). report_mount_worst·report_mount_usage·report_aggregate mount_max 는 `server_mount_usage_5m` cagg(used% max/avg + first/last avail fill_rate, 가상 mount 필터 pre-applied). metric_trend(차트)는 동적 버킷이 목적이라 raw 유지.
- cagg 4개 = `server_metrics_5m`·`server_disk_io_5m`·`server_net_io_5m`·`server_mount_usage_5m`. 모든 무거운 보고서/대시보드 집계가 cagg 소비.
- 초기 materialize 는 마이그레이션 밖 1회(real-time aggregation 이라 refresh 전에도 정확, 성능용).
- cagg 필터(물리 device/interface)는 types 필터 스냅샷 — 필터 규약 변경 시 cagg 재생성 동반.
