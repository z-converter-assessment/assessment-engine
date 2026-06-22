# Repository 계층

정책: CLAUDE.md #C2 · #F4. 3개 추상 인터페이스 `BaseCollectRepository`(Consumer) / `BaseQueryRepository`(Web) / `BaseDiagnosticRepository`(보고서 발행·diagnostic_jobs 스냅샷). 구체 구현체 import는 composition root(`web/deps.py` / `consumer/main.py`)만.

## Collect 계층 — `BaseCollectRepository` (Consumer)

| 메서드 | 설명 |
|--------|------|
| `find_server_id(composite_id) -> int \| None` | `composite_id` 단일 키로 server_id 조회 (#C1, ADR 0027) |
| `upsert_server(data) -> int` | `composite_id` UNIQUE 기준 ON CONFLICT DO UPDATE. 변경 감지 시 history append |
| `ensure_server_id(composite_id, fallback) -> tuple[int, bool]` | find → 없으면 placeholder INSERT. metrics 핸들러 auto-register. 단일 키 (#C1, ADR 0027) |
| `record_metrics(server_id, data) -> MetricInsertResult` | 4 시계열 테이블 INSERT. 각 테이블 행 수 반환 |
| `create_task(data) -> str` | tasks INSERT. public_id(UUID) 반환 |
| `complete_task(data) -> bool` | task.result handler — status / completed_at / failure_reason / exit_code / duration_ms / stdout_tail / stderr_tail UPDATE |

### 구현 디테일

- `upsert_server`: `pg_insert ... on_conflict_do_update`. values·set_ dict는 한 번 만들어 재사용 (컬럼 추가 시 한 곳만 수정). `composite_id` UNIQUE 키는 set_ 제외 (machine_id 는 set_ 포함 — 최신 표시)
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
| `report_mount_usage(server_id, period_days, end)` | 개별 보고서 전체 마운트 윈도우 평균 사용률 (worst 1개 아님, 가상 mount 제외) |
| `report_memory_breakdown(server_id, period_days, end)` | 개별 보고서 메모리 구성 (used/available/cached/buffers 전체 대비 %, 시점값 avg) |
| `report_cpu_breakdown(server_id, period_days, end)` | 개별 보고서 CPU 분류 (user/system/iowait, jiffies LAG delta) |
| `metric_gap_warnings(gap_min, recent_h)` | 메트릭 갭(통신 끊김 운영신호) 후보 |
| `environment_utilization(period_days, end, server_ids?)` | 환경 평균 활용률 도넛 (capacity-weighted, Σused/Σtotal). server_ids 한정 시 선택 N대·단일(selection 보고서), None 이면 전체 환경 |
| `metric_trend(metric_type, start, end, bi, bucket_td, server_ids?, agg, dimension, collapse)` | 통일 차트 시계열 — 환경·선택·서버상세 단일 진실. metric_type 풀세트 18종 — 집계 3그룹(아래). server_ids=None 전체·[1대]=서버상세 동치·[N]=선택. collapse=False 면 device/iface/mount dimension 보존(상세 멀티라인), True 면 합산 단일선(환경). agg=avg/max/p95 |

### 차트 집계 (`metric_trend`) — 시점별 1값 -> 버킷 agg, 3그룹

단일 원칙: 각 collected_at 마다 그 시점 데이터 보낸 서버로 환경값 1개(per_ts)를 산출하고 -> `time_bucket` 의 `{agg}`(avg/max/p95). 온라인/오프라인 별도 판단 없음 — 그 시점 데이터 있으면 포함(데이터 유무가 곧 필터). server_ids=[1대]는 per_ts 의 Σ가 1서버뿐이라 시점값=그 서버값 -> 서버상세 차트와 동일 값. collapse=True(환경)는 dimension 합산 단일선, False(상세)는 device/iface/mount 보존. 대시보드 부하 추이·환경 성능 추이·서버상세 차트·실시간 카드(최신 1점)·보고서 추이가 모두 본 함수(또는 동일 산식). CPU 분류·메모리 구성 등은 JS 가 별도 metric_type fetch 후 클라이언트 dimension 부여.

| 그룹 | metric_type | 집계 방식 (per_ts -> 버킷 {agg}) |
|------|-------------|-----------|
| capacity-weighted util | `cpu.*`, `mem.*`, `swap.usage`, `disk.usage`, `fs.usage_percent` | 시점별 Σnum/Σden x 100 (per_ts) -> 버킷 {agg}. 자원 총량 가중(큰 서버 큰 비중). CPU=jiffies LAG delta(boot reset 제외, `_CPU_NUMERATOR`), mem/swap=시점값 KB(`_ENV_SCALAR_WEIGHTED`), disk/fs=mount bytes(collapse=True 가상 제외 합산 / False mount 보존) |
| 합산 rate | `disk.read/write_iops`, `net.rx/tx_bytes_per_sec`, `net.rx/tx_packets_per_sec` | 시점별 Σ(전 device LAG delta/dt rate)(per_ts) -> 버킷 {agg}. disk=물리 whole-disk 만(`_PHYS_DISK_SQL_FILTER` — 파티션·LVM 이중계산 회피), net=물리 iface 만(`_VIRTUAL_IFACE_SQL_FILTER` — lo·veth·터널 + bond/team master·br/docker/virbr bridge·vlan 제외). collapse=False 면 device/iface 보존. boot reset·dt<=0·음수 delta 제외 |
| 코어 정규화 | `load.1m/5m/15m` | 시점별 Σload / Σcpu_cores (per_ts, server_inventory JOIN) -> 버킷 {agg}. 코어당 로드(1.0=코어당 포화). 절대 load 동등평균은 코어 수 다른 서버 혼재 시 왜곡 — 코어 정규화. 환경·서버상세 모두 코어당 |

집계 필터 단일 진실(`db/repositories/query/types.py`): `_DATA_VOLUME_SQL_FILTER`(mount 데이터볼륨 — `server_mount_usage.major` 주축, `device_filters.is_data_volume` 의 SQL 투영) · `_PHYS_DISK_SQL_FILTER`(물리 disk) · `_VIRTUAL_IFACE_SQL_FILTER`(비가상 iface) — disk·iface 는 `device_filters` 정규식의 PostgreSQL POSIX 번역(변경 시 동기화). 모든 그룹 partition pruning(#C5) `WHERE collected_at >= window_start` + boot jitter 가드 의무.

## Diagnostic 계층 — `BaseDiagnosticRepository` (보고서 발행 스냅샷)

`diagnostic_jobs` 테이블에 발행 시점 정적 스냅샷을 INSERT·조회 (#C1).

| 메서드 | 설명 |
|--------|------|
| `enqueue(job: DiagnosticJobCreate) -> str \| None` | active partial UNIQUE(scope·input_hash·job_type) 충돌 시 None (기존 job 그대로 반환) |
| `get_active_by_hash(scope, input_hash, job_type)` | 더블클릭 방어 lookup — 활성 job 1건 반환 |
| `get_by_id(job_id)` | `?job={id}` 스냅샷 단건 조회 |
| `claim_next_pending()` | pending job 1건 원자적 claim (`FOR UPDATE SKIP LOCKED` + running 마킹) — 워커 분산 (ADR 0040) |
| `mark_succeeded(job_id, result)` | running -> succeeded + result 저장 (워커 생성 완료 시) |
| `mark_failed(job_id, error_message)` | running -> failed + error_message (생성 불가·내부 오류, F8 sanitize 후) |
| `recover_stale_running(stale_seconds)` | started_at 초과 running -> pending 회수 (크래시 in-flight, 워커 기동 시) |
| `list_recent(days, scope?, server_public_ids?, job_type?, limit)` | 보고서 이력 페이지 — created_at DESC |
| `delete_retention(older_than_days)` | retention DELETE |

interval 표현은 `func.now() - timedelta(days=N)` 또는 `func.now() - timedelta(hours=N)` (SQLAlchemy idiomatic — Python timedelta가 PostgreSQL interval로 자동 변환·bind 파라미터 안전, C5 의무). f-string `text("interval '{N} days'")` 금지.

상수 카탈로그 (`base_diagnostic_repository.py`):
- `DiagnosticTimeRange` Literal — 차트 TimeRange와 동일 7개
- `DIAGNOSTIC_RANGE_DAYS` — TimeRange -> float day 매핑 (fraction 지원)
- `DIAGNOSTIC_RANGE_LABEL_KR` — UI 한국어 라벨
- `CLASSIFICATION_LABEL_KR` — USE Method 분류 라벨
- `DIAGNOSTIC_DEFAULT_TIME_RANGE = "14d"` — F10 단일 진실 (service default · UI 기본값)

### 타입 별칭 (`db/repositories/query/types.py`)
- `MetricType` Literal — 17개 chart metric
- `TimeRange` Literal — 15m/1h/6h/24h/7d/14d/30d. 14d는 right-sizing 윈도우(`recommendation.WINDOW_DAYS`)와 동일 — F10 단일 진실
- `BucketSize` Literal — 1m/5m/15m/30m/1h/3h/6h/12h/1d. 6h는 14d 토글 자동 매핑용
- `AggFunc` Literal — avg/max/p95
- `TIME_RANGE_TD` — TimeRange -> timedelta 매핑 (repo·service 공유)
- 신규 range·bucket 추가 시 backend Literal·`_BUCKET_INFO`·`chart-utils.js` `RANGE_LABEL`/`AUTO_BUCKET`/`BUCKET_LABEL`/`RANGE_MS`/`BUCKET_MS`·UI 토글 4곳 동시 갱신 의무 (F10)

### `list_servers` 부분 SELECT 정책
`select(ServerInventory)` 풀 row 대신 11컬럼 명시. `mounts`/`listen_ports` JSONB는 페이지당 N행에서 직렬화 비용 큼 + 목록 미사용. 트레이드오프: `docs/tradeoffs.md` T8. 정렬은 `hostname` ASC.

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
