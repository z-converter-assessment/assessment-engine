# DB 레이어

```
src/assessment_engine/db/
├── session.py                          — 엔진·세션 팩토리
├── redis.py                            — Redis 커넥션 풀
├── models/                             — ORM 모델
└── repositories/
    ├── inbound.py                      — Consumer → Repository DTO
    ├── outbound.py                     — Repository → Service DTO
    ├── base_collect_repository.py      — Consumer용 추상 인터페이스
    ├── collect_repository.py           — Consumer용 구현체
    ├── base_query_repository.py        — Web용 추상 인터페이스
    └── query_repository.py             — Web용 구현체
```

---

## ORM 모델

| 모델 | 테이블 | PK | 설명 |
|------|--------|----|------|
| `ServerInventory` | `server_inventory` | Integer | 기본 인벤토리. machine_id 기준 upsert (현재 상태 단일 행) |
| `ServerInventoryHistory` | `server_inventory_history` | BigInteger | 인벤토리 변경 이력. append-only. TimescaleDB hypertable |
| `ServerMetrics` | `server_metrics` | BigInteger | 스칼라 메트릭 시계열. TimescaleDB hypertable |
| `ServerDiskIo` | `server_disk_io` | BigInteger | 디스크 I/O 시계열. TimescaleDB hypertable |
| `ServerNetIo` | `server_net_io` | BigInteger | 네트워크 I/O 시계열. TimescaleDB hypertable |
| `ServerMountUsage` | `server_mount_usage` | BigInteger | 마운트 사용량 시계열. TimescaleDB hypertable |

- 대리키(surrogate key) 패턴 — 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- `server_inventory.public_id` — `UUID DEFAULT gen_random_uuid()`. URL 식별자. 정수 PK는 내부 참조 전용
- 시계열 5개 테이블 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB 파티션 키 포함
- 시계열 5개 테이블 자연키 UNIQUE 제약 (멱등성 안전망):
  - `server_metrics`: `UNIQUE(server_id, collected_at)`
  - `server_disk_io`: `UNIQUE(server_id, device, collected_at)`
  - `server_net_io`: `UNIQUE(server_id, interface, collected_at)`
  - `server_mount_usage`: `UNIQUE(server_id, mount, collected_at)`
  - `server_inventory_history`: `UNIQUE(server_id, collected_at)`
- per-device/per-interface/per-mount 행 분리 — 차트 API `dimension` 파라미터에 대응
- 시계열 4개 테이블 모두(`server_metrics` · `server_disk_io` · `server_net_io` · `server_mount_usage`) `boot_time` + `agent_started_at` `TIMESTAMPTZ NULL` 컬럼 — 메시지 공통 메타 균일 보존. metrics·disk_io·net_io는 `metrics_calculator._is_counter_reset`이 두 시점 비교로 시스템 재부팅 시 delta 건너뛰기 (CLAUDE.md B1·C1). mount_usage는 시점값이라 calculator 직접 활용 없으나 메타데이터 일관성 + 운영 디버깅을 위해 보존

### server_inventory_history — append-only 변경 이력

`upsert_server`에서 직전 `server_inventory` 행과 비교 후 비교 대상 컬럼 중 하나라도 다르면 한 행 INSERT (앱 레벨 trigger). 비교 제외: `collected_at` · `last_seen_at` (매번 변경되므로 noise). 변경 발생 trigger 빈도가 가장 높은 필드는 `agent_started_at` (에이전트 재시작) · `boot_time` (시스템 재부팅) · `services` · `listen_ports`.

스키마: `server_inventory`의 컬럼을 거의 그대로 미러링(Shadow). `machine_id`/`public_id`는 제외 — `server_id` FK로 충분. 1시간 주기 재발행이라도 정적 정보 동일하면 history 그대로 → noise 없음.

`ON CONFLICT DO NOTHING(server_id, collected_at)` — broker 재전송·동시 워커 race 시 중복 INSERT 흡수.

---

## session.py

### `create_async_engine`

SQLAlchemy 비동기 엔진. asyncpg 드라이버로 PostgreSQL과 비동기 통신.

### `async_sessionmaker`

세션 팩토리. `expire_on_commit=False` 필수 설정.

`expire_on_commit=False`: SQLAlchemy 기본값은 commit 후 ORM 객체 속성을 만료(expire)해 다음 접근 시 DB 재조회를 유도한다. 비동기 환경에서는 만료된 속성에 접근할 때 lazy load가 발생하는데, 이미 세션이 닫혀 있으면 `MissingGreenlet` 오류가 난다. `expire_on_commit=False`로 만료를 막아 commit 후에도 속성을 그대로 읽을 수 있다.

### 세션 획득 방식

| 용도 | 방식 |
|------|------|
| Consumer (`_db_retry`) | `AsyncSessionLocal()` 직접 호출 — `async with`로 세션 생성·close |
| Web (FastAPI 의존성) | `get_db()` 제너레이터 — `Depends(get_db)`로 주입 |

`get_db()` 시그니처: `AsyncGenerator[AsyncSession, None]`. `SendType`은 `None` (generator에 값을 보내지 않음).

---

## Inbound DTO (`inbound.py`)

Consumer가 파싱한 Pydantic 스키마를 Repository에 전달하는 중간 표현. `dataclass`로 정의. Pydantic 모델을 Repository에 직접 넘기지 않아 계층 경계를 명확히 한다.

| DTO | 대응 테이블 |
|-----|------------|
| `ServerInventoryCreate` | `server_inventory` |
| `ServerMetricCreate` | `server_metrics` + 시계열 3개 (disk_io / net_io / mount_usage). 모두 `boot_time` / `agent_started_at` 메타데이터 함께 저장 |

---

## Outbound DTO (`outbound.py`)

Repository → Service 반환 타입. `dataclass`로 정의. 모두 raw 단위 (P1) — KB·bytes·jiffies·sectors 그대로. 변환은 service 계층의 `metrics_calculator`/`mappers`에서.

| DTO | 용도 |
|-----|------|
| `ServerSummary` | 서버 목록 |
| `ServerDetail` | 서버 상세 |
| `StorageWithUsage` | 스토리지 + 마운트 사용량 |
| `NetworkWithIo` | 네트워크 IP + 인터페이스 현황 |
| `DashboardRaw` | 메트릭 대시보드 raw (4개 raw DTO 컨테이너) |
| `MetricPairRaw` | server_metrics 단일 행 raw (CPU jiffies / mem KB / load + `boot_time`/`agent_started_at`) |
| `DiskIoRaw` | server_disk_io 단일 행 raw (per device + `boot_time`/`agent_started_at`) |
| `NetIoRaw` | server_net_io 단일 행 raw (per interface + `boot_time`/`agent_started_at`) |
| `MountUsageRaw` | server_mount_usage 단일 행 raw (per mount + `boot_time`/`agent_started_at`) |
| `CollectionStatus` | 수집 상태 (`last_metric_at`, `last_inventory_at`) |
| `MetricSeries` | 시계열 차트 단일 포인트 (`collected_at`, `value`, `dimension`) |

---

## Collect 계층 (Consumer)

### 인터페이스 — `BaseCollectRepository`

Consumer 핸들러가 의존하는 추상 계약. 트랜잭션 경계는 호출자(`_db_retry`)가 관리.

| 메서드 | 설명 |
|--------|------|
| `find_server_id(machine_id) -> int \| None` | machine_id로 서버 PK 조회. 미등록이면 `None` |
| `upsert_server(data) -> int` | machine_id 기준 INSERT or UPDATE. 서버 PK 반환 |
| `ensure_server_id(machine_id, fallback) -> tuple[int, bool]` | find 시도 → 없으면 fallback으로 upsert. `(server_id, auto_registered)` 반환. metrics 핸들러 auto-register 흐름 캡슐화 |
| `record_metrics(server_id, data) -> MetricInsertResult` | 4개 시계열 테이블에 INSERT. `MetricInsertResult(metrics, disk_io, net_io, mount_usage)`로 각 테이블 행 수 반환 |

### Inbound DTO 타입 정책 — `inbound.py`

| DTO | 컬렉션 필드 | 형태 | 이유 |
|-----|----------|------|------|
| `ServerInventoryCreate` | `disks` / `mounts` / `services` / `listen_ports` | `list[dict]` | JSONB 컬럼 직렬화. dict가 자연스러움 |
| `ServerMetricCreate` | `disk_io` / `mounts` / `net_io` | `list[DiskIoEntry]` / `list[MountUsageEntry]` / `list[NetIoEntry]` | 시계열 4테이블 행 매핑이라 컴파일 타임 타입 보장 |

`DiskIoEntry`/`NetIoEntry`/`MountUsageEntry`는 nested dataclass. dict 키 오타가 mapper 단계에서 차단된다. INSERT 시 `dataclasses.asdict(entry)`로 키 풀어쓰기.

### 구현체 — `CollectRepository`

`AsyncSession`을 생성자에서 주입받는다. `_db_retry`가 세션을 생성하고 `CollectRepository(session)`을 팩토리로 주입한다.

#### `upsert_server`

PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`. `machine_id` unique 제약 충돌 시 전체 필드를 덮어쓰고 PK를 `RETURNING`으로 반환. values·set_ dict는 한 번 만들어 재사용 (`set_`는 `machine_id` 제외) — 컬럼 추가 시 한 곳만 수정.

```python
row = {"machine_id": ..., "hostname": ..., ...}
update_set = {k: v for k, v in row.items() if k != "machine_id"}
stmt = pg_insert(ServerInventory).values(row).on_conflict_do_update(
    index_elements=["machine_id"], set_=update_set,
).returning(ServerInventory.id)
```

#### `ensure_server_id`

```python
async def ensure_server_id(self, machine_id, fallback) -> tuple[int, bool]:
    server_id = await self.find_server_id(machine_id)
    if server_id is not None:
        return server_id, False           # 기존 서버 — fallback 미사용
    return await self.upsert_server(fallback), True  # auto-registered
```

handler가 placeholder 생성 비용 부담을 일부 안지만 흐름이 1줄로 단순화 + auto-register 시점만 운영 로그.

#### `record_metrics`

4개 테이블 INSERT를 facade로 묶는다. 내부 4개 private helper 분리: `_insert_scalar_metrics` / `_insert_disk_io` / `_insert_net_io` / `_insert_mount_usage`. 각각 `result.rowcount`를 반환.

모두 `pg_insert(...).on_conflict_do_nothing(index_elements=...)`로 통일. UNIQUE 위반 시 silent no-op이라 멱등성 키(Redis) 만료·evict·Redis 장애 후 중복 메시지가 들어와도 데이터 정합성이 유지된다.

| 모델 | conflict 키 |
|------|-----|
| `ServerMetrics` | `(server_id, collected_at)` |
| `ServerDiskIo` | `(server_id, device, collected_at)` |
| `ServerNetIo` | `(server_id, interface, collected_at)` |
| `ServerMountUsage` | `(server_id, mount, collected_at)` |

반환 `MetricInsertResult`의 각 카운트는 ON CONFLICT DO NOTHING 충돌 시 0. handler 로그가 이를 노출해 누락·중복을 운영 관측 가능.

#### `.returning()` + `scalar_one` / `scalar_one_or_none`

`RETURNING`: INSERT/UPDATE 결과 행을 즉시 반환하는 PostgreSQL 확장.
- `scalar_one()`: 단일 스칼라 추출. 결과가 없거나 둘 이상이면 예외
- `scalar_one_or_none()`: 결과가 없으면 `None`. `find_server_id`에서 사용

---

## Query 계층 (Web)

### 인터페이스 — `BaseQueryRepository`

Web 서비스가 의존하는 추상 계약. `QueryService`는 이 인터페이스만 알고 구체 구현을 모른다. `deps.py`(composition root)에서 `QueryRepository(db)`를 생성해 주입.

주요 메서드: `resolve_server_id`, `list_servers`, `get_server`, `get_storage`, `get_network`, `get_collection_status`, `latest_dashboard`, `metric_snapshots`, `metric_chart`

### 타입 별칭

`base_query_repository.py`에 `MetricType`, `TimeRange`, `BucketSize`, `AggFunc` Literal 타입 정의. `api.py` 라우터가 이 타입을 Query 파라미터 타입으로 사용. FastAPI Pydantic이 라우터 단계에서 검증하므로 service 계층에서 중복 검증 금지.

### `list_servers` SELECT 정책

목록 화면이 사용하는 11개 컬럼만 명시 SELECT (`id`, `public_id`, `machine_id`, `hostname`, `os_id`, `os_version`, `cpu_cores`, `mem_total_kb`, `ip_external`, `disks`, `services`).

`select(ServerInventory)` 풀로우 SELECT 대신 부분 SELECT를 쓰는 이유: `mounts` / `listen_ports` JSONB는 페이지당 N행에서 직렬화 비용이 크고 list 화면 미사용. `kernel_version` / `boot_time` / `swap_total_kb` / `agent_version` / `last_seen_at` / `ip_internal` / `os_codename` / `cpu_model`도 list 화면 미표시. 트레이드오프 정리는 `docs/adr/tradeoffs.md` T8.

### 차트 SQL 패턴 (`_chart_*` 헬퍼)

7개 메트릭 타입별 헬퍼 (`_chart_cpu`, `_chart_cpu_component`, `_chart_load`, `_chart_mem`, `_chart_disk_iops`, `_chart_net`, `_chart_fs`). 공통 패턴:

1. window_start 확장: `LAG`로 첫 버킷 delta를 계산할 수 있도록 요청 `start`보다 한 bucket 만큼 더 과거부터 raw를 읽음 (`window_start = start - _BUCKET_TD[bi]`).
2. delta CTE: `LAG(...) OVER (PARTITION BY device ORDER BY collected_at)`로 누적 카운터 차 + `LAG(boot_time)`으로 직전 boot_time 동시 추출.
3. reset 식별 CASE — calculator의 `_is_counter_reset`과 동일 정책 (CLAUDE.md B1):
   ① `dt IS NULL OR dt <= 0` → NULL (페어 미충족 / 동일 시점 / 역행)
   ② `boot_time != prev_boot` → NULL (시스템 재부팅 확정)
   ③ `d_val < 0` → NULL (옛 데이터 boot_time NULL일 때 휴리스틱 fallback / wrap-around)
   ④ 정상 → `d_val / dt` 또는 `d_num * 100 / d_total`
   `dt`는 검증이 아니라 분모 — 1분이든 3분이든 실제 시간으로 자연 정규화. CPU percent는 jiffies 비율이라 `dt` 무관.
4. time_bucket 집계: TimescaleDB `time_bucket(interval '5m', collected_at)` + `agg`(avg/max/p95) 적용. WHERE `v IS NOT NULL`로 reset 시점 차트 제외 (missing point로 자연 표시).
5. dimension 필터: `(CAST(:dim AS text) IS NULL OR device = :dim)` — None이면 전체, 지정 시 그 dimension만.

---

## TimescaleDB

- 시계열 4개 테이블 모두 hypertable (`collected_at` 기준 파티셔닝)
- 개발: web lifespan에서 `CREATE EXTENSION IF NOT EXISTS timescaledb` → `create_all` → `create_hypertable(if_not_exists => true)`
- 주의: `create_all`은 기존 테이블에 컬럼/제약(UniqueConstraint 등)을 추가하지 않음. 스키마 변경 시 `docker compose down -v` 후 재기동
- 프로덕션: Alembic 마이그레이션. `create_hypertable`은 최초 생성 마이그레이션에 수동 작성

---

## 설계 결정

### DEV 환경 스키마 관리: web lifespan → consumer depends_on

web lifespan에서 스키마를 생성하고, consumer가 `depends_on web: service_healthy`로 web 헬스체크 통과 후 기동한다. web과 DB 마이그레이션 책임이 뒤섞이는 구조이나, 초기 개발 단계에서 별도 마이그레이션 도구 없이 빠르게 스키마를 유지할 수 있다. `create_all`은 이미 존재하는 테이블을 건드리지 않으므로 데이터 유실 위험 없음.

개선 방향(P1): Alembic 초기화 → 현재 스키마를 초기 마이그레이션으로 작성 → `consumer depends_on web` 제거, consumer가 DB에 직접 의존.

### 차트 dimension 필터: `CAST(:dim AS text) IS NULL` 패턴

dimension 유무를 단일 쿼리로 처리:
```sql
AND (CAST(:dim AS text) IS NULL OR device = :dim)
```

분기 쿼리가 더 단순하고 인덱스 최적화 여지가 있으나, 코드 중복이 발생한다. 현재 데이터 규모에서는 성능 차이 없음.

`:dim::text IS NULL` 형태는 SQLAlchemy + asyncpg에서 named parameter 뒤 `::` 파싱 버그가 있으므로 `CAST(:dim AS text)`로 우회.

---

## 운영 / 디버깅

### psql 접속

```bash
docker compose exec postgres psql -U assessment -d assessment
```

### 자주 쓰는 쿼리

```sql
-- 등록된 서버 목록
SELECT id, hostname, machine_id, last_seen_at FROM server_inventory ORDER BY id;

-- 서버별 시계열 행 수
SELECT s.hostname,
       (SELECT count(*) FROM server_metrics WHERE server_id = s.id) AS metrics,
       (SELECT count(*) FROM server_disk_io WHERE server_id = s.id) AS disk_io,
       (SELECT count(*) FROM server_net_io WHERE server_id = s.id) AS net_io,
       (SELECT count(*) FROM server_mount_usage WHERE server_id = s.id) AS mount
FROM server_inventory s;

-- (server_id, collected_at) 중복 검사 — UNIQUE가 동작하면 0행이어야 함
SELECT server_id, collected_at, count(*) FROM server_metrics
GROUP BY 1, 2 HAVING count(*) > 1 LIMIT 5;

-- hypertable 청크 정보 (TimescaleDB)
SELECT show_chunks('server_metrics');
SELECT hypertable_size('server_metrics');

-- 가장 최근 메트릭 1개
SELECT * FROM server_metrics ORDER BY collected_at DESC LIMIT 1;
```

### 인덱스 확인

```sql
\d server_metrics
```

기대 인덱스:
- `server_metrics_pkey` (id, collected_at) — 복합 PK
- `server_metrics_collected_at_idx` (collected_at DESC) — TimescaleDB 자동 생성 (파티션 키)
- `uq_server_metrics_sid_ts` (server_id, collected_at) UNIQUE — 멱등성 안전망 + 자연키 인덱스

`(server_id, collected_at)` 인덱스가 있어 `WHERE server_id = ? ORDER BY collected_at DESC LIMIT N` 쿼리(`latest_dashboard`, `metric_snapshots`)가 인덱스 스캔으로 처리.

### EXPLAIN 예시

```sql
EXPLAIN ANALYZE
SELECT * FROM server_metrics
WHERE server_id = 1 AND collected_at >= now() - interval '1 hour'
ORDER BY collected_at DESC
LIMIT 60;
```

기대 plan: `Index Scan using uq_server_metrics_sid_ts` 또는 hypertable의 chunk별 인덱스 스캔. Seq Scan이 보이면 인덱스 누락 의심.

### 시계열 누적 모니터링

```sql
-- 테이블별 디스크 사용량
SELECT hypertable_name, pg_size_pretty(hypertable_size(format('%I', hypertable_name)::regclass))
FROM timescaledb_information.hypertables;

-- 청크별 행 수 (가장 최근 5개)
SELECT chunk_name, range_start, range_end
FROM timescaledb_information.chunks
WHERE hypertable_name = 'server_metrics'
ORDER BY range_start DESC LIMIT 5;
```

retention policy 도입 시점은 `docs/adr/tradeoffs.md` T3 참조.

### 흔한 에러

| 에러 | 원인 | 해결 |
|------|------|------|
| `relation "server_metrics" does not exist` | consumer가 web 헬스체크 전에 시작 (depends_on 누락) | docker-compose의 `depends_on: web: service_healthy` 확인 |
| `extension "timescaledb" does not exist` | `timescale/timescaledb` 이미지가 아닌 일반 postgres 이미지 사용 | docker-compose `image:` 확인 |
| `non-default argument 'X' follows default argument` | Python dataclass 필드 순서 위반 (default factory 필드 뒤에 non-default) | non-default 필드를 모두 위로 이동 |
| `MissingGreenlet` | `expire_on_commit=True`로 commit 후 lazy-load 시도 | `src/assessment_engine/db/session.py`의 `expire_on_commit=False` 유지 |
| `UniqueViolation` | UNIQUE 제약은 있지만 `pg_insert.on_conflict_do_nothing` 누락 | `collect_repository._insert_*` helper 검토 |