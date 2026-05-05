# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
고객사 내부 네트워크의 각 호스트 인벤토리를 수집·저장하는 B2B 내부 포털.

### 배포 시나리오
고객사 네트워크 내에 서버 엔진(web + consumer + MQ + DB)이 설치된다.
각 서버의 **C99/C++03 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행하고, Consumer가 소비해 DB에 저장한다.

## docker-compose 구성

| 서비스 | 이미지 | 역할 |
|--------|--------|------|
| postgres | timescale/timescaledb:latest-pg16 | 메인 DB |
| rabbitmq | rabbitmq:3.13-management-alpine | 메시지 브로커 |
| redis | redis:7-alpine | 캐시·온라인TTL·PUB/SUB |
| web | 로컬 빌드 | FastAPI SSR + API |
| consumer | 로컬 빌드 | aio-pika 컨슈머 |

- **scheduler**는 코드(`scheduler/`)는 있으나 docker-compose 서비스로 미등록. `run_diagnostics()` NotImplementedError 상태.
- `consumer depends_on web: condition: service_healthy` — **DEV 전용**: web 기동 시 lifespan이 `CREATE EXTENSION + create_all + create_hypertable` 를 수행하므로, consumer가 web health 확인 후 시작해야 스키마가 존재함. 프로덕션에서는 Alembic으로 대체하고 이 의존성 제거.
- `db/session.py`와 `db/redis.py`는 `web_settings`를 사용. `ConsumerSettings`는 `WebSettings`를 상속하여 RabbitMQ 설정을 추가하며, docker-compose의 `POSTGRES_HOST: postgres` / `REDIS_HOST: redis` environment 오버라이드로 동작함.

## ORM 모델

| 모델 | 테이블 | PK 타입 | 설명 |
|------|--------|---------|------|
| ServerInventory | server_inventory | Integer | 기본 인벤토리. machine_id 기준 upsert |
| ServerMetrics | server_metrics | BigInteger | 스칼라 메트릭 시계열. TimescaleDB hypertable |
| ServerDiskIo | server_disk_io | BigInteger | 디스크 I/O 시계열. TimescaleDB hypertable |
| ServerNetIo | server_net_io | BigInteger | 네트워크 I/O 시계열. TimescaleDB hypertable |
| ServerMountUsage | server_mount_usage | BigInteger | 마운트 사용량 시계열. TimescaleDB hypertable |

- 대리키(surrogate key) 패턴 — 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- 시계열 4개 테이블 모두 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB hypertable 파티션 키 포함, 무한 누적 대비
- `server_disk_io`·`server_net_io`·`server_mount_usage`는 per-device/per-interface/per-mount 행 분리 — 차트 API `dimension` 파라미터에 대응

## 데이터 흐름 설계

- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- Inbound DTO: `ServerInventoryCreate`, `ServerMetricCreate` (`db/repositories/inbound.py`) — Consumer → Repository
- Outbound DTO: `XxxResponse`, `DashboardRaw`, `StorageWithUsageResponse`, `NetworkWithIoResponse` (`db/repositories/outbound.py`) — Repository → Service
- ViewModel: `web/view_models.py` — Service → Router (Jinja2 컨텍스트 또는 JSON)
- Consumer 파싱: Pydantic `AgentMessage` discriminated union (정의됨) → 실제 핸들러는 routing key별로 구체 타입(`InventoryInput`, `MetricsInput`, `ErrorInput`)을 직접 파싱
- inventory upsert 및 metrics 서버 조회 기준: `machine_id` (미등록 서버면 drop)
- `last_seen_at`: `list_servers()`에서 `COALESCE(MAX(server_metrics.collected_at), server_inventory.last_seen_at)` subquery JOIN으로 산출 — "마지막 메트릭 수신 시각" 의미. 인벤토리 등록 시각이 아님.
- `CollectionStatusItem`은 `last_metric_at`(마지막 메트릭 수신)과 `last_inventory_at`(마지막 인벤토리 수신)을 별도 필드로 분리 반환 — `/collection-status` API에서 두 시각을 모두 노출.

## 에이전트 메시지 스키마 (`agent_version` = 계약 버전)

3가지 `message_type`을 routing key로 분기. 모든 메시지에 공통 메타데이터 포함.

### 공통 메타데이터
| 필드 | 설명 |
|------|------|
| `message_type` | "inventory" / "metrics" / "error" |
| `machine_id` | `/etc/machine-id` 기준. 표준 Linux는 32 hex, 가상화 환경은 UUID(하이픈 포함 36자) 가능. DB/스키마 max 64자 |
| `agent_version` | 빌드 시 정의. 계약 버전 역할 |
| `collected_at` | ISO 8601 UTC |
| `hostname` | 보조 식별자 (가변) |
| `message_id` | UUID v4 (멱등성 키) |

### `server.inventory` (기동 시 1회)
정적 인프라 정보. OS·kernel·CPU·메모리/스왑 총량, `disks[]` (name·size·type), `mounts[]` (fstype·total_bytes 포함), `ip_internal[]`/`ip_external[]`, `boot_time`.

- `InventoryMountInfo`에 `free_bytes`/`avail_bytes` 필드가 있으나 핸들러에서 무시됨 — 인벤토리엔 static 정보(total_bytes)만 저장하고 동적 사용량은 `server_mount_usage`에 분리 저장

### `server.metrics` (1분 주기)
**모두 raw 누적값**. 분석 엔진이 두 시점의 차로 delta·% 계산.
- `cpu_stat` (user/system/idle/iowait/… jiffies)
- 메모리·스왑 (총량·free·available·buffers·cached)
- `load_1m` / `load_5m` / `load_15m`
- `disk_io[]` per device (reads/writes_completed, sectors)
- `mounts[]` per mount (현재 사용량 — inventory의 정적 정보와 별도)
- `net_io[]` per interface (rx/tx bytes·packets·errors)

### `server.error`
에이전트 측 수집/발행 실패. `error_code`·`error_message`·`failed_component` (collect/publish).

### 규약
- **단위**: 메모리=`kb`, 디스크/네트워크=`bytes`. `/proc` 출력 관례를 따름
- **옵셔널 필드**: 수집 실패 시 `null` 전송. 수집 실패와 데이터 없음을 구분하지 않음
- **counter reset**: 재부팅·에이전트 재시작 시 카운터 0 리셋. 현재 구현은 delta < 0 이면 `None` 처리(UI에서 "—" 표시). boot_time 기반 delta 건너뛰기는 미구현.
- **2차 상세 (파티션·LVM·파일시스템·라우팅·DNS)**: 현 스키마 미포함. 추후 별도 `message_type`으로 확장 여지

## Consumer 설계

- aio-pika 기반 순수 비동기 컨슈머 (FastAPI와 독립 프로세스)
- 파싱 실패: raise → nack(requeue=False) → DLX → DLQ
- DB 실패: 지수 백오프(`2^attempt` s) 3회 재시도 후 raise → nack(requeue=False) → DLX → DLQ
- TTL 만료: 브로커가 자동으로 DLX → DLQ. 큐별 TTL 상이 — inventory: 없음(one-shot), metrics/error: 60s
- `error` 메시지: 파싱 후 로깅만. DB 저장 없음

### 멱등성 처리
메시지 수신 시 `SET idempotent:{message_id} 1 EX 86400 NX` — 원자적 체크. 이미 처리된 message_id면 ack 후 조기 리턴.
- **at-most-once 주의**: SET NX는 DB 커밋 이전에 실행. 커밋 전 프로세스 크래시 시 RabbitMQ 재전송 메시지가 중복으로 판정되어 조용히 드롭됨 (데이터 유실 가능). 현재 설계상 허용된 트레이드오프.

### metrics 저장 후 Redis 처리 순서
1. `SET online:{server_id} 1 EX 90`
2. `DELETE cache:metrics:{server_id}` — 캐시 즉시 무효화
3. `PUBLISH metrics.events {"server_id": ..., "machine_id": ...}` — 브라우저 SSE 트리거

### MQ 토폴로지
Exchange: `assessment` (direct, durable) / DLX: `assessment.dlx` (direct, durable) / prefetch_count: 10

| routing key | 큐 | DLQ |
|-------------|-----|-----|
| `server.inventory` | `server.inventory` | `server.inventory.dead` |
| `server.metrics` | `server.metrics` | `server.metrics.dead` |
| `server.error` | `server.error` | `server.error.dead` |

## Query API 설계

### 라우터 구조
| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `web/routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2) |
| `web/routers/api.py` | `api_router` | `/api/v1/servers` | JSON |

### SSR 페이지 엔드포인트
| 경로 | 템플릿 | 데이터 |
|------|--------|--------|
| GET /servers/ | servers/list.html | 인벤토리 목록 + Redis online 상태 |
| GET /servers/{server_id} | servers/detail.html | 인벤토리 정적 정보 (메트릭은 AJAX) |
| GET /servers/{server_id}/storage | servers/storage.html | 인벤토리 disks + 최신 mount_usage |
| GET /servers/{server_id}/network | servers/network.html | IP + 최신 net_io delta |
| GET /servers/{server_id}/chart | servers/chart.html | Chart.js 시계열 차트 (API AJAX) |

### API 엔드포인트 (AJAX / SSE)
| 경로 | 응답 | 설명 |
|------|------|------|
| GET /api/v1/servers/{id}/collection-status | CollectionStatusItem | 수집 상태 + Redis online 뱃지 |
| GET /api/v1/servers/{id}/metrics/latest | MetricDashboard (JSON) | 계산된 CPU%·mem%·IOPS·kBps·FS% |
| GET /api/v1/servers/{id}/metrics/snapshots | list[MetricSeriesItem] | 히스토리 (커서 페이지네이션) |
| GET /api/v1/servers/{id}/metrics/chart | list[MetricSeriesItem] | 차트 시계열 (time_bucket + LAG delta) |
| GET /api/v1/servers/{id}/metrics/stream | SSE (text/event-stream) | metrics.events 구독, 브라우저 실시간 갱신 |

### MetricDashboard 구조
`/metrics/latest` 응답. raw jiffies가 아닌 **계산된 값** 반환.
- `cpu`: usage_pct / user_pct / system_pct / iowait_pct (연속 2회 readings delta)
- `memory`: total_kb / used_kb / available_kb / cached_kb / buffers_kb / usage_pct (단일 시점)
- `swap`: total_kb / used_kb / usage_pct
- `load_1m` / `load_5m` / `load_15m`
- `disk_io[]`: device / read_iops / write_iops / read_kbps / write_kbps (2회 delta)
- `net_io[]`: interface / rx_kbps / tx_kbps / rx_pps / tx_pps (2회 delta)
- `mounts[]`: mount / total_gb / used_gb / avail_gb / usage_pct (단일 시점)

### 메트릭 차트 쿼리 파라미터
- `metric_type`: cpu.usage_percent / disk.read_iops / disk.write_iops / fs.usage_percent / net.rx_bytes_per_sec / net.tx_bytes_per_sec
- `dimension`: 복수 인스턴스 메트릭의 특정 대상 (장치명·mountpoint·NIC명)
- `time_range`: 15m / 1h / 6h / 24h / 7d (기본 1h)
- `bucket`: 5m / 1h / 1d
- `agg`: avg / max / p95

### Jinja2 KST 필터
`web/template_filters.py`에 `kst`, `disksize`, `kbps` 정의 → `web/routers/pages.py`에서 `templates.env.filters`에 등록.
datetime(UTC) → KST `"YYYY-MM-DD HH:MM"` 변환. Redis 캐시에서 복원 시 datetime 필드는 `datetime.fromisoformat()`으로 파싱해야 필터가 동작함 (`json.loads`는 str 반환).

### asyncpg 파라미터 주의사항
두 가지 패턴이 런타임 오류를 유발한다.

1. **interval 산술**: `collected_at >= :start - interval '5 minutes'` — asyncpg가 `:start` 타입을 추론하지 못함.  
   → Python에서 `window_start = start - timedelta(minutes=5)` 계산 후 `:window_start` 파라미터로 전달.

2. **named parameter + PostgreSQL cast**: `:dim::text` 형태는 SQLAlchemy가 named parameter 뒤 `::`를 파싱하지 못해 SQL에 `:dim` 그대로 남음.  
   → `CAST(:dim AS text)` 로 대체.

## Redis 전략

### 키 설계
| 용도 | 키 | TTL |
|------|----|-----|
| 인벤토리 캐시 | `cache:inventory:{server_id}` | 300s (read-through) |
| 메트릭 캐시 | `cache:metrics:{server_id}` | 60s + consumer가 새 metrics 저장 시 즉시 DELETE |
| 멱등성 | `idempotent:{message_id}` | 24h |
| 온라인 TTL | `online:{server_id}` | 90s |
| 인증 토큰 | `token:{token}` | 1h |

- PUB/SUB 채널: `metrics.events` (consumer 발행 → web SSE 구독 → 브라우저 AJAX 재요청)
- eviction 정책: `volatile-lru`, maxmemory 256mb
- 의존성 주입: web은 `web/deps.py`의 `get_redis_client()`, consumer는 `db/redis.py`의 `get_redis()` 직접 호출 — 둘 다 내부적으로 `web_settings.redis_url` 사용

## TimescaleDB
- `server_metrics`·`server_disk_io`·`server_net_io`·`server_mount_usage` 4개 모두 hypertable (`collected_at` 기준 파티셔닝)
- **개발**: 기동 시 `CREATE EXTENSION IF NOT EXISTS timescaledb` → `Base.metadata.create_all` (없는 테이블만 생성) → `create_hypertable(..., if_not_exists => true)`. web 재시작 시 기존 데이터 보존.
- 완전 초기화가 필요하면 `docker compose down -v` 후 재기동.
- **프로덕션**: Alembic 마이그레이션. `create_hypertable`은 최초 생성 마이그레이션에 수동 작성

## 환경변수
루트 `.env`에서 주입. 키 목록은 `.env.example` 참조.
docker-compose `environment`에는 서비스명 오버라이드(`POSTGRES_HOST`, `RABBITMQ_HOST`, `REDIS_HOST`)와 이미지 요구 변수명 매핑만 명시.
- `REDIS_HOST`는 `.env.example` 미기재 (기본값 "redis"가 docker-compose 환경과 일치하므로 생략)

## Vagrant 에이전트 배포 규약

에이전트 바이너리는 VirtualBox shared folder(`/home/vagrant/assessment-agent/`)에서 직접 실행 불가 — SELinux(Rocky Linux 9) 및 vboxsf 마운트 제약.

| 항목 | 경로 | 이유 |
|------|------|------|
| 바이너리 | `/usr/local/bin/assessment-agent` | systemd가 vboxsf 경유 바이너리를 실행 못함 |
| 환경변수 파일 | `/etc/assessment-agent.env` | SELinux가 `/home/vagrant/` 내 파일 systemd 읽기 차단 |

Vagrantfile step 3에서 `cp` + `chmod 755`, step 2에서 `/etc/assessment-agent.env` 작성. `ExecStart=/usr/local/bin/assessment-agent`, `EnvironmentFile=/etc/assessment-agent.env`.

## 서비스 계층 구조

`web/services/` 하위 모듈 분리:

| 모듈 | 역할 |
|------|------|
| `query_service.py` | QueryService 클래스 (Redis + repo 오케스트레이션) |
| `mappers.py` | outbound DTO → ViewModel 변환 |
| `metrics_calculator.py` | CPU/Mem/Disk/Net delta 계산 |
| `cache_serializer.py` | Redis serde (ServerDetailResponse, MetricDashboard) |
| `units.py` | `_kb_to_gb`, `_bytes_to_gb`, `_usage_pct`, `_sector_to_kbps` |

## ViewModel 설계 원칙

- 단위 변환(KB→GB, bytes→GB/TB, kBps→MBps)은 **서비스 계층**에서 수행. 템플릿은 이미 변환된 값을 사용.
- `_kb_to_gb(kb)`, `_bytes_to_gb(b)` — `web/services/units.py` 유틸 함수.
- 표시 포맷(숫자 → "1.2 TB", "3.4 kBps") 은 Jinja2 필터 `disksize` / `kbps`로 위임 (`web/template_filters.py` 정의, `web/routers/pages.py`에서 등록).
- `NetInterfaceItem`은 제거되었음. `NetworkDetailResponse.interfaces`는 `list[NetIoSnapshot]` 재사용 — 필드 구조가 동일하므로 별도 타입 불필요.

## 템플릿 차트 UI 설계 원칙

Chart.js 4.4.3 사용. 아래 패턴을 전 차트 템플릿에 동일하게 적용한다.

### avg+max 음영 패턴
avg 데이터셋과 max ghost 데이터셋을 쌍(짝수·홀수 인덱스)으로 구성한다.

- avg: `fill: '+1'`, `pointRadius: 1`, `pointHoverRadius: 3`, 실선
- max ghost: `borderColor:'transparent'`, `backgroundColor:'transparent'`, `pointRadius: 0`, `pointHoverRadius: 0`
- ghost 데이터: `bufferedMaxData` — avg가 null인 버킷은 max도 null (빈 구간 음영 방지)
- 실제 max 값은 `realData` 커스텀 속성에 보관 → 툴팁 콜백에서 `ds.realData[idx]`로 참조
- 툴팁: `filter: item => item.datasetIndex % 2 === 0` — avg 데이터셋만 표시

### 포인트 크기 규칙 (전 차트 통일)
- avg/실데이터: `pointRadius: 1`, `pointHoverRadius: 3`
- ghost(max): `pointRadius: 0`, `pointHoverRadius: 0`

### suggestedMax 상수화
Y축 기본 기준선은 스크립트 상단에 명명 상수로 분리한다.
```javascript
const NET_Y_SUGGESTED_MAX = 2048; // B/s 기본 기준선 (≈2 kB/s). 조정 가능.
```
`suggestedMax`는 soft ceiling — 실데이터가 초과하면 자동 확장된다.

### 네트워크 I/O Y축
- 데이터: 서버에서 넘어온 B/s 원값 그대로 (나누기 없음)
- 포매터 `fmtKbChart(v)`: `v >= 1024*1024` → MB/s, `v >= 1024` → kB/s, else B/s
- Y축 title: `'처리량'` (단위가 값에 따라 동적이므로 고정 단위명 부적합)
- `NET_Y_SUGGESTED_MAX = 2048`

### 스왑 Y축
`beginAtZero: true, suggestedMax: 25` — 스왑 사용률이 낮아도 추이 가시성을 위해 최소 0–25% 스케일 유지.

### IOPS 차트 Y축
`ticks: { precision: 0 }` — 정수값만 표시. `stepSize: 1` + 정수 callback 조합 불필요.

### SSE 상태 + 수집기준시간 레이아웃
SSE dot/label과 수집기준시간 span은 반드시 단일 flex 컨테이너 안에 두어 줄바꿈을 방지한다.
```html
<div id="sse-status" style="display:flex; align-items:center; gap:5px; font-size:11px; color:#94a3b8; white-space:nowrap;">
  <span id="sse-dot" class="dot dot-off"></span>
  <span id="sse-label">연결 중...</span>
  <span id="xxx-snapshot-ts" style="margin-left:4px;"></span>
</div>
```
타임스탬프 span을 SSE div 밖에 두면 flex-wrap으로 분리될 수 있다.

### 헤더 브랜드 네비게이션
`base.html`의 `ZConverter Assessment` 브랜드는 `/servers/`로 이동하는 `<a>` 태그.
별도 "서버 목록" nav 링크는 제거 (브랜드 클릭으로 대체).

## 타입 어노테이션 규칙

- **`from __future__ import annotations` 절대 금지** — 전 파일.
- `TYPE_CHECKING` 블록은 순환 임포트가 실제로 발생하는 경우에만 사용. 순환 임포트 없는 경우 직접 임포트. Python 3.12에서 어노테이션은 즉시 평가되므로 `TYPE_CHECKING` 블록의 import는 런타임 `NameError`를 유발함.
- 타입 좁히기용 데드코드(`assert x is not None` 등) 작성 금지.

## 테스트 정책

현재 코드 변경 및 리팩토링 진행 중으로 테스트는 실패 상태다.
- 테스트 실행 금지 — 명시적 요청이 있을 때만 수행
- 테스트 파일 확인·분석·제안 금지 — 명시적 요청이 있을 때만 수행
- 작업 완료 확인 수단으로 pytest를 사용하지 않음

## 테스트 구조

테스트 코드는 `tests/` 디렉토리에 위치. 전략 문서: `docs/TEST_STRATEGY.md`.

```
tests/
├── unit/
│   ├── consumer/          # test_schemas.py, test_handler.py
│   └── web/
│       ├── services/      # test_metrics_calculator.py, test_mappers.py, test_query_service.py
│       └── routers/       # test_pages.py, test_api.py
└── integration/
    └── db/                # test_collect_repository.py, test_query_repository.py
```

테스트 의존성은 optional group으로 관리 (`pyproject.toml [project.optional-dependencies] test`):

```bash
uv pip install -e ".[test]"
pytest tests/unit/ -v          # 단위 테스트 (Docker 불필요)
pytest tests/integration/ -v   # 통합 테스트 (Docker daemon 필요)
```

## 브랜치 전략
| 브랜치 | 용도 |
|--------|------|
| main | 배포용. 직접 push 금지 |
| develop | 개발 통합. PR로만 머지 |
| feature/xxx | 기능 개발 |
| fix/xxx | 버그 수정 |
| chore/xxx | 설정 변경 |

## 커밋 컨벤션
설명은 한글로 작성

| 타입 | 설명 |
|------|------|
| feat | 새로운 기능 추가 |
| fix | 버그 수정 |
| chore | 설정, 패키지 변경 |
| refactor | 리팩토링 |
| test | 테스트 코드 |