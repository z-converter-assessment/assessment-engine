# Repository 계층

Consumer / Web 양쪽 별도 인터페이스 — `BaseCollectRepository` / `BaseQueryRepository`. 라우터·핸들러는 추상에만 의존, 구체 구현체 import는 composition root(`web/deps.py` / `consumer/main.py`)만.

## Collect 계층 — `BaseCollectRepository` (Consumer)

| 메서드 | 설명 |
|--------|------|
| `find_server_id(machine_id) -> int \| None` | machine_id로 server_id 조회 |
| `upsert_server(data) -> int` | machine_id 기준 ON CONFLICT DO UPDATE. 변경 감지 시 history append |
| `ensure_server_id(machine_id, fallback) -> tuple[int, bool]` | find → 없으면 placeholder INSERT. metrics 핸들러 auto-register |
| `record_metrics(server_id, data) -> MetricInsertResult` | 4 시계열 테이블 INSERT. 각 테이블 행 수 반환 |
| `create_task(data) -> str` | tasks INSERT. public_id(UUID) 반환 |
| `complete_task(data) -> bool` | task_result handler — status/completed_at/result_message UPDATE |

### 구현 디테일

- `upsert_server`: `pg_insert ... on_conflict_do_update`. values·set_ dict는 한 번 만들어 재사용 (컬럼 추가 시 한 곳만 수정). machine_id는 set_ 제외
- `ensure_server_id`: `_insert_placeholder_server`는 `ON CONFLICT DO NOTHING` (placeholder가 진짜 inventory 덮어쓰는 race 방지)
- `record_metrics`: 4 테이블 모두 `pg_insert.on_conflict_do_nothing(index_elements=...)` — 멱등성 2단 방어 (D2)
- `create_task`: `IntegrityError` 가능 (부분 UNIQUE `uq_tasks_pending_per_server_type`) — service가 catch

## Query 계층 — `BaseQueryRepository` (Web)

| 메서드 | 설명 |
|--------|------|
| `resolve_server_id(public_id)` | UUID → 정수 PK |
| `list_servers(page, limit, search, is_online)` | 목록 — 11개 컬럼 명시 SELECT (큰 JSONB 제외) |
| `get_server(server_id)` | full row |
| `get_storage(server_id)` | inventory + mount_usage |
| `get_network(server_id)` | inventory IP + net_io |
| `get_collection_status(server_id)` | last_metric_at + last_inventory_at |
| `latest_dashboard(server_id)` | 4 raw DTO (CPU/Mem/Disk/Net delta 계산용) |
| `metric_snapshots(server_id, cursor, limit)` | 시계열 페이지네이션 |
| `metric_chart(server_id, type, dim, range, bucket, agg, end)` | 차트 dispatcher (17 metric_type) |
| `reboot_events(server_id, start, end)` | server_inventory_history boot_time/agent_started_at 변경 시점 |
| `report_aggregate(server_ids, period_days, end)` | USE Method 통계 (CPU p95/peak + MEM p95/peak + load_15m max + swap_used) |

### 타입 별칭 (`base_query_repository.py`)
- `MetricType` Literal — 17개 chart metric
- `TimeRange` Literal — 15m/1h/6h/24h/7d/14d/30d. 14d는 right-sizing 윈도우(`recommendation.WINDOW_DAYS`)와 동일 — F15 단일 진실
- `BucketSize` Literal — 1m/5m/15m/30m/1h/3h/6h/12h/1d. 6h는 14d 토글 자동 매핑용
- `AggFunc` Literal — avg/max/p95
- `TIME_RANGE_TD` — TimeRange -> timedelta 매핑 (repo·service 공유)
- 신규 range·bucket 추가 시 backend Literal·`_BUCKET_INFO`·`chart-utils.js` `RANGE_LABEL`/`AUTO_BUCKET`/`BUCKET_LABEL`/`RANGE_MS`/`BUCKET_MS`·UI 토글 4곳 동시 갱신 의무 (F15)

### `list_servers` 부분 SELECT 정책
`select(ServerInventory)` 풀 row 대신 11컬럼 명시. `mounts`/`listen_ports` JSONB는 페이지당 N행에서 직렬화 비용 큼 + 목록 미사용. 트레이드오프: `docs/tradeoffs.md` T8.

## INSERT 통일 — `pg_insert` + `on_conflict_do_nothing`

시계열 4테이블 모두 동일 패턴 적용 (CLAUDE.md #D2 2단 방어). 자연키 카탈로그는 `docs/architecture/redis.md` "멱등성 — 시계열 자연키 카탈로그" 절.

## `.returning()` + `scalar_one`

INSERT 결과 PK 받기:
- `.returning(ServerInventory.id)` — 단일 컬럼 반환
- `result.scalar_one()` — 1행 보장 시 (upsert)
- `result.scalar_one_or_none()` — 0/1행 (placeholder INSERT — `ON CONFLICT DO NOTHING`이라 0행 가능)

## asyncpg 파라미터 주의사항

- named param `:dim` 뒤 `::text`는 asyncpg 파싱 버그 — `CAST(:dim AS text)`로 우회
- `ANY(:sids)` — 배열 파라미터. asyncpg가 list/tuple 자동 변환
- TIMESTAMPTZ 비교는 tz-aware datetime만 — naive datetime 전달 시 timezone 불일치 오류
