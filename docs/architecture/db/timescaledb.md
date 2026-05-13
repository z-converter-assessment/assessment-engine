# TimescaleDB · 차트 SQL 패턴

정책: CLAUDE.md #C1·#C4·#C5. 본 문서는 hypertable 구성·차트 SQL 패턴·report_aggregate 단일 진실.

## hypertable 구성

시계열 5개 테이블 모두 hypertable (`collected_at` 기준 파티셔닝):
- `server_metrics` / `server_disk_io` / `server_net_io` / `server_mount_usage` / `server_inventory_history`

### 4테이블 `boot_time`/`agent_started_at` 컬럼 보존

`server_metrics`/`server_disk_io`/`server_net_io`/`server_mount_usage` 4개 테이블은 행마다 `boot_time`/`agent_started_at` 컬럼을 함께 저장한다(CLAUDE.md #C1·#B). 근거:
- metrics/disk_io/net_io는 `_chart_*` 헬퍼와 `metrics_calculator._is_counter_reset`이 `LAG(boot_time)`로 시스템 재부팅 식별 -> counter reset 시 delta 건너뛰기.
- mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅 시 단일 테이블 SELECT로 boot_time까지 같이 보고 싶어서 보존.
- 옛 데이터(컬럼 NULL)는 `d_val < 0` 휴리스틱 fallback (CASE 3순위).

## DEV / PROD 스키마 관리

| 환경 | 방식 |
|------|------|
| DEV | web lifespan에서 `CREATE EXTENSION IF NOT EXISTS timescaledb` → `Base.metadata.create_all` → `create_hypertable(if_not_exists => true)` |
| PROD | Alembic 마이그레이션. `create_hypertable`은 최초 생성 마이그레이션에 수동 작성 |

`create_all`은 기존 테이블에 컬럼/제약(UniqueConstraint 등)을 추가하지 않음. 스키마 변경 시 `docker compose down -v` 후 재기동.

## 차트 SQL 패턴 (`_chart_*` 헬퍼 — 7개)

`query_repository.metric_chart` dispatcher:
- `_chart_cpu_delta` — jiffies delta → percent
- `_chart_scalar` — 시점값 (load/mem/swap %)
- `_chart_rate_per_dimension` — 누적 카운터 → rate (disk/net)
- `_chart_fs` — fs.usage_percent (시점값)

### 공통 패턴

1. window_start 확장 — `LAG`로 첫 버킷 delta 계산 위해 요청 `start`보다 한 bucket 더 과거부터 raw 읽음 (`window_start = start - bucket_td`)
2. delta CTE — `LAG(...) OVER (PARTITION BY device ORDER BY collected_at)`로 누적 카운터 차 + `LAG(boot_time)`로 직전 boot_time 동시 추출
3. reset 식별 CASE — calculator의 `_is_counter_reset`과 동일 정책 (CLAUDE.md B1):
   ① `dt IS NULL OR dt <= 0` → NULL
   ② `boot_time != prev_boot` → NULL (시스템 재부팅)
   ③ `d_val < 0` → NULL (옛 데이터 휴리스틱)
   ④ 정상 → `d_val / dt` 또는 `d_num * 100 / d_total`
   `dt`는 검증이 아니라 분모 — 실제 시간으로 자연 정규화
4. time_bucket 집계 — TimescaleDB `time_bucket(interval '5m', collected_at)` + `agg`(avg/max/p95)
5. dimension 필터 — `(CAST(:dim AS text) IS NULL OR device = :dim)` — None이면 전체

### Reset 시점 차트 표시

`v IS NOT NULL` WHERE filter — reset 시점 missing point로 자연 표시. 별도 marker는 web/static-assets.md "Reboot/Restart marker" 참조.

## 보고서 집계 — `report_aggregate` SQL

USE Method (Brendan Gregg) 기반 N서버 X period_days 통계:

```sql
WITH cpu_deltas AS (
  -- LAG로 jiffies delta + boot_time reset 제외
),
cpu_pct AS (...),
cpu_stats AS (
  SELECT server_id,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY pct) AS cpu_p95,
    MAX(pct) AS cpu_peak
  FROM cpu_pct GROUP BY server_id
),
mem_pct AS (...), mem_stats AS (...), load_stats AS (...)
SELECT s.id, s.public_id, s.hostname, s.os_id, s.os_version, s.kernel_version,
       s.ip_internal, s.services, s.last_seen_at,
       cs.cpu_p95, cs.cpu_peak, ms.mem_p95, ms.mem_peak, ms.swap_used, ls.load_15m_max
FROM server_inventory s
LEFT JOIN cpu_stats  cs ON cs.server_id = s.id
LEFT JOIN mem_stats  ms ON ms.server_id = s.id
LEFT JOIN load_stats ls ON ls.server_id = s.id
WHERE s.id = ANY(:sids)
```

- `services` JSONB 동시 SELECT — N+1 회피 (role 추론용)
- LEFT JOIN — metric 없는 서버도 행 반환 (service에서 `insufficient_data` 분류)
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
  "SELECT public_id, target_machine_id, task_type, status, created_at FROM tasks WHERE status='pending'"
```
