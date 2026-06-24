# TimescaleDB · 차트 SQL 패턴

정책: CLAUDE.md #C1·#C4·#C5. 본 문서는 hypertable 구성·차트 SQL 패턴·report_aggregate 단일 진실.

## hypertable 구성

시계열 5개 테이블 모두 hypertable (`collected_at` 기준 파티셔닝):
- `server_metrics` / `server_disk_io` / `server_net_io` / `server_mount_usage` / `server_inventory_history`

### 4테이블 `boot_time`/`agent_started_at` 컬럼 보존

`server_metrics`/`server_disk_io`/`server_net_io`/`server_mount_usage` 4개 테이블은 행마다 `boot_time`/`agent_started_at` 컬럼을 함께 저장한다(CLAUDE.md #C1·#B). 근거:
- metrics/disk_io/net_io는 `metric_trend` 차트 SQL과 `metrics_calculator._is_counter_reset`이 `LAG(boot_time)`로 시스템 재부팅 식별 -> counter reset 시 delta 건너뛰기.
- mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅 시 단일 테이블 SELECT로 boot_time까지 같이 보고 싶어서 보존.
- 옛 데이터(컬럼 NULL)는 `d_val < 0` 휴리스틱 fallback (CASE 3순위).

## DEV / PROD 스키마 관리

| 환경 | 방식 |
|------|------|
| DEV | web lifespan에서 `CREATE EXTENSION IF NOT EXISTS timescaledb` → `Base.metadata.create_all` → `create_hypertable(if_not_exists => true)` |
| PROD | Alembic 마이그레이션. `create_hypertable`은 최초 생성 마이그레이션에 수동 작성 |

`create_all`은 기존 테이블에 컬럼/제약(UniqueConstraint 등)을 추가하지 않음. 스키마 변경 시 `docker compose down -v` 후 재기동.

## 차트 SQL 패턴 — 단일 함수 `metric_trend`

모든 차트(환경 성능 추이·선택 N대·서버 상세·대시보드 부하 추이·보고서 추이)는 단일 함수 `metric_trend`가 산출한다. `metric_chart`(서버 상세)는 `metric_trend(collapse=False, server_ids=[1대])`에 위임하는 thin wrapper. metric_type별 분기(jiffies delta percent·시점값 load/mem/swap %·누적 카운터 rate disk/net·fs.usage_percent)는 `metric_trend` 내부 SQL 분기로 흡수.

### 통일 산식

각 `collected_at`(시점)마다 그 시점에 데이터를 보낸 서버로 환경값 1개를 산출(`per_ts` CTE):
- 활용률 = `sum(num)/sum(den)` (capacity-weighted)
- 처리량 = 합산 `SUM`
- 로드 = `sum(load_15m)/sum(cpu_cores)` (코어 정규화 — 환경·서버 상세 동일)

이후 그 시점값을 `time_bucket`의 `agg`(avg/max/p95)로 집계. 시점 분리 없이 버킷 전체를 한 번에 합하는 방식은 폐기 — 시점별 환경값을 먼저 만들고 버킷 집계한다.

온라인/오프라인을 별도로 판단하지 않는다 — 그 시점에 데이터가 있으면 포함, 없으면 자동 제외(데이터 유무가 곧 온라인 필터).

`server_ids` 인자: `None`이면 전체 환경, `[1대]`이면 서버 상세와 동치(`per_ts`의 합산 대상이 1서버뿐이라 시점값=그 서버값), `[N대]`이면 선택. `collapse=True`면 dimension(device/iface/mount) 합산 단일선(환경), `False`면 dimension 보존(서버 상세 멀티라인).

### 공통 패턴

1. window_start 확장 — `LAG`로 첫 버킷 delta 계산 위해 요청 `start`보다 한 bucket 더 과거부터 raw 읽음 (`window_start = start - bucket_td`)
2. delta CTE — `LAG(...) OVER (PARTITION BY device ORDER BY collected_at)`로 누적 카운터 차 + `LAG(boot_time)`로 직전 boot_time 동시 추출
3. reset 식별 CASE — calculator의 `_is_counter_reset`과 동일 정책 (CLAUDE.md B1):
   ① `dt IS NULL OR dt <= 0` → NULL
   ② `abs(boot_time - prev_boot) > BOOT_TIME_JITTER_TOLERANCE`(5s) → NULL (재부팅 — NTP 보정 흔들림은 흡수)
   ③ `d_val < 0` → NULL (옛 데이터 휴리스틱)
   ④ 정상 → `d_val / dt` 또는 `d_num * 100 / d_total`
   `dt`는 검증이 아니라 분모 — 실제 시간으로 자연 정규화
4. time_bucket 집계 — TimescaleDB `time_bucket(interval '5m', collected_at)` + `agg`(avg/max/p95)
5. dimension 필터 — `(CAST(:dim AS text) IS NULL OR device = :dim)` — None이면 전체

### Reset 시점 차트 표시

`v IS NOT NULL` WHERE filter — reset 시점 missing point로 자연 표시. 별도 marker는 web/static-assets.md "Reboot/Restart marker" 참조.

## 카운터 메트릭 사전집계 — continuous aggregate (ADR 0043)

CPU jiffies·disk/net bytes 는 카운터다. 매 요청 7일치 raw 를 LAG 로 스캔하지 않고 continuous aggregate +
timescaledb_toolkit `counter_agg` 로 사전집계한다. 5분 버킷(클라우드 right-sizing 표준), `materialized_only=false`
(real-time aggregation — 미materialize 최근 구간 실시간 집계, staleness 0), 5분 refresh policy. 초기 materialize 는
`refresh_continuous_aggregate`(트랜잭션 밖)로 마이그레이션 외 1회.

| cagg | 그룹 | 저장 |
|------|------|------|
| `server_metrics_5m` | server_id, bucket | CPU `counter_agg`(total/idle/user/system/iowait) + mem% avg/max + load max + swap |
| `server_disk_io_5m` | server_id, device, bucket | reads/writes/sectors `counter_agg` (물리 device만) |
| `server_net_io_5m` | server_id, interface, bucket | rx/tx bytes·packets `counter_agg` (물리 interface만) |

counter reset(재부팅·agent재시작·wraparound)은 `counter_agg` 가 값-감소 기준 일률 처리 — boot_time gate 불요.
가상 device/interface 는 cagg 단계 필터(물리만, types 필터 스냅샷).

## 보고서 집계 — `report_aggregate` SQL

USE Method (Brendan Gregg) 기반 N서버 X period_days 통계 — `server_metrics_5m` cagg 조회:

```sql
WITH bkt AS (
  -- 버킷별 delta(counter_agg)로 CPU%/iowait% (reset 일률 처리). per-bucket = 5분 평균.
  SELECT server_id, bucket,
    CASE WHEN delta(cpu_total_ca) > 0
         THEN GREATEST(0, (1 - delta(cpu_idle_ca)/delta(cpu_total_ca)) * 100) END AS cpu_pct,
    mem_pct_avg, mem_pct_max, load_15m_max, swap_in_use
  FROM server_metrics_5m WHERE server_id = ANY(:sids) AND bucket >= :start AND bucket <= :end
),
cpu_stats AS (  -- 버킷에 percentile_cont(정확), avg, max
  SELECT server_id, percentile_cont(0.95) WITHIN GROUP (ORDER BY cpu_pct) AS cpu_p95, MAX(cpu_pct) AS cpu_peak
  FROM bkt GROUP BY server_id
),
mem_stats AS (...), load_stats AS (...),
mount_max AS (-- server_mount_usage(가상 mount 제외), 카운터 아님 raw 집계)
SELECT s.id, ..., cs.cpu_p95, cs.cpu_peak, ms.mem_p95, ms.mem_peak, ms.swap_used, ls.load_15m_max, mm.worst_used_pct
FROM server_inventory s
LEFT JOIN cpu_stats cs ON cs.server_id = s.id
... (mem/load/mount LEFT JOIN)
WHERE s.id = ANY(:sids)
```

- `services` JSONB 동시 SELECT — N+1 회피 (role 추론용)
- LEFT JOIN — metric 없는 서버도 행 반환 (service에서 `insufficient_data` 분류)
- sufficiency = 실측 버킷 / 기대 버킷(period_days*288, 5분). `report_disk_io_baseline`·`report_net_io_baseline`·`report_cpu_breakdown` 도 동일 cagg 조회.
- repository는 raw 컬럼만 (P1) — `os_display`/`internal_ip[0]` 등 표시 가공은 mapper

## 운영 / 디버깅

```bash
# 시계열 hypertable chunk 확인
docker compose exec postgres psql -U assessment -d assessment -c \
  "SELECT show_chunks('server_metrics')"

# 인덱스 사용 확인
docker compose exec postgres psql -U assessment -d assessment -c \
  "EXPLAIN SELECT * FROM server_metrics WHERE server_id = 1 ORDER BY collected_at DESC LIMIT 10"

# tasks pending 조회
docker compose exec postgres psql -U assessment -d assessment -c \
  "SELECT public_id, target_composite_id, task_type, status, created_at FROM tasks WHERE status='pending'"
```
