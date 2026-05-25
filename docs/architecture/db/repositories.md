# Repository 계층

정책: CLAUDE.md #C2 · #F4. 3개 추상 인터페이스 `BaseCollectRepository`(Consumer) / `BaseQueryRepository`(Web) / `BaseDiagnosticRepository`(워커, ADR 0004 + 0023). 구체 구현체 import는 composition root(`web/deps.py` / `consumer/main.py` / `diagnostic/main.py`)만. ADR 0023: scheduler 폐기.

## Collect 계층 — `BaseCollectRepository` (Consumer)

| 메서드 | 설명 |
|--------|------|
| `find_server_id(host_id) -> int \| None` | `host_id` 단일 키로 server_id 조회 (#C1, ADR 0022) |
| `upsert_server(data) -> int` | `host_id` UNIQUE 기준 ON CONFLICT DO UPDATE. 변경 감지 시 history append |
| `ensure_server_id(host_id, fallback) -> tuple[int, bool]` | find → 없으면 placeholder INSERT. metrics 핸들러 auto-register. 단일 키 (#C1, ADR 0022) |
| `record_metrics(server_id, data) -> MetricInsertResult` | 4 시계열 테이블 INSERT. 각 테이블 행 수 반환 |
| `create_task(data) -> str` | tasks INSERT. public_id(UUID) 반환 |
| `complete_task(data) -> bool` | task.result handler — status / completed_at / failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail UPDATE |

### 구현 디테일

- `upsert_server`: `pg_insert ... on_conflict_do_update`. values·set_ dict는 한 번 만들어 재사용 (컬럼 추가 시 한 곳만 수정). `(host_id, hostname)` 복합 키는 set_ 제외
- `ensure_server_id`: `_insert_placeholder_server`는 `ON CONFLICT DO NOTHING` (placeholder가 진짜 inventory 덮어쓰는 race 방지)
- `record_metrics`: 4 테이블 모두 `pg_insert.on_conflict_do_nothing(index_elements=...)` — 멱등성 2단 방어 (D2)
- `create_task`: `IntegrityError` 가능 (부분 UNIQUE `uq_tasks_pending_per_server_type`) — service가 catch

## Query 계층 — `BaseQueryRepository` (Web)

| 메서드 | 설명 |
|--------|------|
| `resolve_server_id(public_id)` | 단건 UUID → 정수 PK |
| `resolve_server_ids(public_ids)` | N건 batch — 단일 SQL (C5 N+1 회피) |
| `list_server_ids(limit=1000)` | 정수 PK만 fetch (페이로드 절감, T8 패턴) |
| `list_servers(page, limit, search)` | 목록 — 11개 컬럼 명시 SELECT (큰 JSONB 제외) |
| `get_server(server_id)` / `get_servers(server_ids)` | 단건 / batch full row |
| `get_storage(server_id)` | inventory + mount_usage |
| `get_network(server_id)` | inventory IP + net_io |
| `get_collection_status(server_id)` | last_metric_at + last_inventory_at |
| `latest_dashboard(server_id)` | 4 raw DTO (CPU/Mem/Disk/Net delta 계산용) |
| `metric_snapshots(server_id, cursor, limit)` | 시계열 cursor pagination |
| `metric_chart(server_id, type, dim, range, bucket, agg, end)` | 차트 dispatcher (17 metric_type) |
| `reboot_events(server_id, start, end)` | server_inventory_history boot_time/agent_started_at 변경 시점 |
| `report_aggregate(server_ids, period_days, end)` | USE Method 통계 (CPU p95/peak + MEM p95/peak + load_15m max + swap_used) |
| `report_mount_worst(server_ids, period_days, end)` | mount별 worst usage + fill_rate (days_until_full 산출) |
| `report_uptime_stats(server_ids, period_days, end)` | 가동률 통계 |
| `report_disk_io_baseline` / `report_net_io_baseline` | I/O baseline (Export `recommended_size_class` 입력) |
| `disk_usage_warnings(threshold_pct)` | 사용률 임계 초과 mount (attention 신호) |
| `metric_gap_warnings(gap_min, recent_h)` | 메트릭 갭(통신 끊김) 후보 |
| `environment_utilization(period_days, end)` | 대시보드 환경 평균 활용률 도넛 |

## Diagnostic 계층 — `BaseDiagnosticRepository` (ADR 0004)

| 메서드 | 설명 |
|--------|------|
| `enqueue(job: DiagnosticJobCreate) -> str \| None` | active partial UNIQUE 충돌 시 None (기존 job 그대로 반환) |
| `get_active_by_hash(scope, input_hash)` | 더블클릭 방어 lookup — pending/running 활성 job 1건 반환 |
| `get_latest_succeeded(scope, input_hash)` | input_hash 정확 매칭 latest |
| `get_latest_by_context(scope, time_range, server_public_id?)` | anchor_at 무관 (JSONB 검색) 최근 결과 — SSR latest 카드 |
| `get_many_latest_by_context_server(time_range, public_ids)` | 보고서 batch fetch (#C5 N+1 회피, DISTINCT ON) |
| `get_by_id(job_id)` / `get_many_by_ids(job_ids)` | polling 응답 단건/batch |
| `mark_running(job_id, stage)` / `update_progress_stage` | 워커 stage UPDATE |
| `mark_succeeded(job_id, result)` / `mark_failed(job_id, error_message)` | 최종 상태 전이 |
| `list_recent(days, scope?, server_public_ids?, limit)` | 진단 이력 페이지 — created_at DESC |
| `delete_retention(older_than_days)` | 스케줄러 retention DELETE |

interval 표현은 `func.now() - timedelta(days=N)` 또는 `func.now() - timedelta(hours=N)` (SQLAlchemy idiomatic — Python timedelta가 PostgreSQL interval로 자동 변환·bind 파라미터 안전, C5 의무). f-string `text("interval '{N} days'")` 금지.

상수 카탈로그 (`base_diagnostic_repository.py`):
- `DiagnosticTimeRange` Literal — 차트 TimeRange와 동일 7개
- `DIAGNOSTIC_RANGE_DAYS` — TimeRange -> float day 매핑 (fraction 지원)
- `DIAGNOSTIC_RANGE_LABEL_KR` — UI/narrative 한국어 라벨
- `CLASSIFICATION_LABEL_KR` — USE Method 분류 라벨 (`mappers.diagnostic` view + `llm/mock` narrative 공용)
- `DIAGNOSTIC_DEFAULT_TIME_RANGE = "14d"` — F10 단일 진실 (service default · UI 기본값. ADR 0023: scheduler 폐기로 cron 발화 catalog 제거)

### 타입 별칭 (`db/repositories/query/types.py`)
- `MetricType` Literal — 17개 chart metric
- `TimeRange` Literal — 15m/1h/6h/24h/7d/14d/30d. 14d는 right-sizing 윈도우(`recommendation.WINDOW_DAYS`)와 동일 — F10 단일 진실
- `BucketSize` Literal — 1m/5m/15m/30m/1h/3h/6h/12h/1d. 6h는 14d 토글 자동 매핑용
- `AggFunc` Literal — avg/max/p95
- `TIME_RANGE_TD` — TimeRange -> timedelta 매핑 (repo·service 공유)
- 신규 range·bucket 추가 시 backend Literal·`_BUCKET_INFO`·`chart-utils.js` `RANGE_LABEL`/`AUTO_BUCKET`/`BUCKET_LABEL`/`RANGE_MS`/`BUCKET_MS`·UI 토글 4곳 동시 갱신 의무 (F10)

### `list_servers` 부분 SELECT 정책
`select(ServerInventory)` 풀 row 대신 11컬럼 명시. `mounts`/`listen_ports` JSONB는 페이지당 N행에서 직렬화 비용 큼 + 목록 미사용. 트레이드오프: `docs/tradeoffs.md` T8.

## INSERT 통일 — `pg_insert` + `on_conflict_do_nothing`

시계열 4테이블 모두 동일 패턴 적용(CLAUDE.md #D2 2단 방어). 자연키 카탈로그: `docs/architecture/db/models.md` "시계열 5개 테이블 자연키 UNIQUE" 표.

## `.returning()` + `scalar_one`

INSERT 결과 PK 받기:
- `.returning(ServerInventory.id)` — 단일 컬럼 반환
- `result.scalar_one()` — 1행 보장 시 (upsert)
- `result.scalar_one_or_none()` — 0/1행 (placeholder INSERT — `ON CONFLICT DO NOTHING`이라 0행 가능)

## asyncpg 파라미터 주의사항

- named param `:dim` 뒤 `::text`는 asyncpg 파싱 버그 — `CAST(:dim AS text)`로 우회
- `ANY(:sids)` — 배열 파라미터. asyncpg가 list/tuple 자동 변환
- TIMESTAMPTZ 비교는 tz-aware datetime만 — naive datetime 전달 시 timezone 불일치 오류
