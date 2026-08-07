# TimescaleDB · 차트 SQL 패턴

정책: CLAUDE.md #C1·#C4·#C5. 본 문서는 hypertable 구성·차트 SQL 패턴·`get_report_aggregate` 단일 진실.

## hypertable 구성

시계열 8테이블이 `collected_at` 기준으로 파티셔닝된 hypertable 이다. 테이블 카탈로그와 `boot_time`/`agent_started_at` 컬럼 소유·counter reset 처리는 `docs/reference/db/models.md` 가 갖는다.

hypertable·continuous aggregate 를 포함한 모든 스키마 변경은 Alembic revision 단일 경로다 — 절차와 `create_hypertable`·`CREATE EXTENSION` 수동 보강 의무는 `docs/guides/migrate.md`. web/consumer/worker lifespan 은 스키마를 만들지 않고 이미 적용된 것으로 가정한다.

## 차트 SQL 패턴 — 단일 함수 `get_metric_trend`

모든 차트(환경 성능 추이·선택 N대·서버 상세·대시보드 부하 추이·보고서 추이)는 단일 함수 `get_metric_trend` 가 산출한다. `get_metric_chart`(서버 상세)는 `server_ids=[1대]` 로 위임하는 thin wrapper 다. metric_type 별 분기(CPU 시간 delta percent·시점값 mem/run_queue/PSI gauge·누적 카운터 rate disk/net·fs.usage_percent)는 `get_metric_trend` 내부 SQL 분기로 흡수한다.

### 통일 산식

각 `collected_at`(시점)마다 그 시점에 데이터를 보낸 서버로 환경값 1개를 산출한다(`per_ts` CTE).
- 활용률 = `sum(num)/sum(den)` (capacity-weighted)
- 처리량 = 합산 `SUM`
- 실행 큐 = `sum(cpu_run_queue)/sum(cpu_cores)` (코어 정규화 — 환경·서버 상세 동일)

이후 그 시점값을 `time_bucket` 의 `agg`(avg/max/p95)로 집계한다. 온라인/오프라인은 별도로 판단하지 않는다 — 그 시점에 데이터가 있으면 포함, 없으면 자동 제외라 데이터 유무가 곧 온라인 필터다.

`server_ids` 는 `None` 이면 전체 환경, `[1대]` 면 서버 상세와 동치(`per_ts` 의 합산 대상이 1서버뿐이라 시점값이 곧 그 서버값), `[N대]` 면 선택이다. `collapse=True` 면 dimension(device/iface/mount)을 합산해 단일선(환경), `False` 면 dimension 을 보존한다(서버 상세 멀티라인).

예외는 시점값을 먼저 내지 않고 버킷을 먼저 묶는 축들이다. disk/net rate 환경 합산(`collapse=True`)은 server+device 별 버킷 평균 rate 를 낸 뒤 전 함대를 `SUM` 하고, `fs.usage_percent` 환경 합산은 버킷 끝 시점까지의 마지막 값(LOCF)으로 `sum(used)/sum(used+free)` 를 내며, 판정 축(포화 이진·crossing 호스트 수)은 버킷 안에서 `bool_or` 로 넘었는지를 판정한다. 수집이 staggered 라 한 `collected_at` 에는 소수 서버만 있어 시점 합산·시점 카운트가 undercount 가 되기 때문이고, 이 축들은 합산·판정 자체가 결과라 `agg` 를 적용하지 않는다.

### 공통 패턴

1. window_start 확장 — `LAG` 로 첫 버킷 delta 를 내려고 요청 `start` 보다 한 bucket 더 과거부터 raw 를 읽음 (`window_start = start - bucket_td`)
2. delta CTE — `LAG(...) OVER (PARTITION BY server_id, device ORDER BY collected_at)` 로 누적 카운터 차
3. reset 흡수 — `dt IS NULL OR dt <= 0` 은 NULL(분모 무효), rate 는 `GREATEST(delta, 0)` 로 음수 delta 0 클램프, CPU 는 `d_total > 0 AND d_num >= 0` 행만 통과시켜 감소 구간 배제
4. time_bucket 집계 — `time_bucket(interval, collected_at)` + `agg`
5. dimension 필터 — bound parameter 가 NULL 이면 전체, 값이 있으면 그 device/mount 만

차트는 `boot_time` 을 읽지 않는다. 게이트 없이 산식 자체가 reset 구간을 떨구고, `dt` 는 검증이 아니라 분모라 실제 시간으로 자연 정규화된다. reset 시점은 `v IS NOT NULL` WHERE filter 로 missing point 가 되어 선이 끊긴다 — 재부팅·에이전트 재시작 vertical marker 는 별도 엔드포인트이고 `docs/reference/web/routers.md` 가 갖는다.

## 카운터 메트릭 사전집계 — continuous aggregate

CPU 시간(s)·disk/net bytes 는 카운터다. 매 요청 7일치 raw 를 LAG 로 스캔하지 않고 continuous aggregate +
timescaledb_toolkit `counter_agg` 로 사전집계한다. 5분 버킷(클라우드 right-sizing 표준), `materialized_only=false`
(real-time aggregation — 미materialize 최근 구간 실시간 집계, staleness 0), 5분 refresh policy. 초기 materialize 가
필요한 쪽은 cagg 재생성뿐이다 — 마이그레이션 안 `autocommit_block()`(트랜잭션 밖)에서 `refresh_continuous_aggregate`
1회로 기존 raw 를 backfill 한다. 신규 생성은 대상 데이터가 없어 policy 가 도착분부터 채운다.

| cagg | 그룹 | 저장 |
|------|------|------|
| `server_metrics_5m` | server_id, bucket | CPU/paging/oom/tcp재전송/mce `counter_agg` + mem%·commit% avg/max + run_queue·blocked·conntrack avg/max + hw_corrupted + mem byte gauge(available/limit avg, cached/buffered%) |
| `server_filesystem_5m` | server_id, mountpoint, bucket | used% avg/max + inode% max + total_bytes_max + free/inode first·last(runway) + fstype_any(query 시 가상 fs 필터) |
| `server_disk_io_5m` | server_id, device_id, bucket | io bytes·ops·op_time·io_time `counter_agg` + pending avg/max |
| `server_net_io_5m` | server_id, iface_id, bucket | rx/tx bytes·packets·drops·errors `counter_agg` + link_max |
| `server_cpu_core_5m` | server_id, core_id, bucket | per-core CPU `counter_agg`(total/idle) — 단일스레드 병목 감지 |

cagg 정의에는 WHERE 절이 없어 전 device/interface 를 담는다. 물리 device/interface 한정은 조회 시점에
`_PHYS_DISK_SQL_FILTER`/`_PHYS_IFACE_SQL_FILTER`(`query/types.py`) 상관 서브쿼리를 붙여 건다 — 필터 규약이
바뀌어도 cagg 재생성이 필요 없다.

## 보고서 집계 — `get_report_aggregate`

USE Method (Brendan Gregg) 기반 N서버 x period_days 통계. CTE 구성과 산식은 `db/repositories/query/report_sql.py` 단일 진실이고, 본 절은 코드만 봐서는 안 서는 판단 근거만 담는다.

- 입력은 raw hypertable 이 아니라 cagg 5종이다. `get_report_disk_io_baseline`·`get_report_net_io_baseline`·`get_report_memory_breakdown`·`get_report_cpu_breakdown` 도 같은 cagg 를 본다.
- 최종 SELECT 가 `server_inventory` 를 좌변에 두고 통계 CTE 를 전부 LEFT JOIN 하는 이유는 metric 이 한 건도 없는 서버까지 행으로 돌려보내기 위해서다 — 그 행은 service 가 `insufficient_data` 로 분류한다.
- sufficiency 분모 `period_days * 288` 은 5분 버킷이 하루 288개라는 데서 온다. 실측 버킷 수를 이 기대치로 나눈 비율이 다운사이즈 처방 이력 게이트 입력이다 (#F10).
- `mount_span` CTE 만 하한 술어 없이 `bucket <= :end` 로 돈다 — partition pruning(#C5) 의 의식적 예외이고 근거는 `docs/explanation/tradeoffs.md` T18.
- `services` JSONB 를 같은 쿼리에서 SELECT 해 role 추론 N+1 을 없앤다.
- repository 는 raw 컬럼만 (P1) — `os_display`·`internal_ip[0]` 등 표시 가공은 mapper.
