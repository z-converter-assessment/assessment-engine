# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
고객사 내부 네트워크의 각 호스트 인벤토리를 수집·저장하는 B2B 내부 포털.

### 배포 시나리오
고객사 네트워크 내에 서버 엔진(web + consumer + MQ + DB)이 설치된다.
각 서버의 **C 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행하고, Consumer가 소비해 DB에 저장한다.

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
- `server_inventory.public_id` — `UUID DEFAULT gen_random_uuid()`. URL 식별자. 정수 PK는 내부 참조 전용.
- 시계열 4개 테이블 모두 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB hypertable 파티션 키 포함, 무한 누적 대비
- `server_disk_io`·`server_net_io`·`server_mount_usage`는 per-device/per-interface/per-mount 행 분리 — 차트 API `dimension` 파라미터에 대응
- `server_inventory`에 `services` (JSONB), `listen_ports` (JSONB) 컬럼 추가됨. **`create_all`은 기존 테이블에 컬럼을 추가하지 않으므로**, 스키마 변경 후 최초 기동 시 `docker compose down -v` 로 볼륨 초기화 필요.

## 데이터 흐름 설계

- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- Inbound DTO: `ServerInventoryCreate`, `ServerMetricCreate` (`db/repositories/inbound.py`) — Consumer → Repository
- Outbound DTO: `XxxResponse`, `DashboardRaw`, `StorageWithUsageResponse`, `NetworkWithIoResponse` (`db/repositories/outbound.py`) — Repository → Service
- ViewModel: `web/view_models.py` — Service → Router (Jinja2 컨텍스트 또는 JSON)
- Consumer 파싱: routing key별로 구체 타입(`InventoryInput`, `MetricsInput`, `ErrorInput`)을 직접 파싱. `consumer/mappers.py`에서 Pydantic 스키마 → Inbound DTO 변환.
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

추가 필드:
- `services[]`: `{unit, sub}` — systemctl 수집. non-systemd 호스트는 `null`
- `listen_ports[]`: `{proto, addr, port, uid, pid, comm}` — `/proc/net/{tcp,udp}{,6}` 기반. 수집 실패 시 빈 배열

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

### URL 식별자
라우터의 `{server_id}` 경로 파라미터는 `public_id` (UUID 문자열). 정수 PK는 노출하지 않는다.
- `QueryService.resolve_server_id(public_id) -> int | None` — UUID → 정수 PK 변환 브릿지
- `pages.py` / `api.py` 공통 `_resolve()` 헬퍼에서 404 처리

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
| GET /servers/{server_id}/services | servers/services.html | 서비스 전체 목록 + 포트 전체 현황 |

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

### Jinja2 필터
`web/template_filters.py`에 정의 → `web/routers/pages.py`에서 `templates.env.filters`에 등록.

| 필터 | 동작 |
|------|------|
| `kst` | datetime(UTC) → KST `"YYYY-MM-DD HH:MM:SS"`. None → `"-"` |
| `disksize` | float(GB) → `"1.2 TB"` / `"3.4 GB"`. None → `"-"` |
| `kbps` | float(kBps) → `"1.2 MBps"` / `"3.4 kBps"`. None → `"—"` |
| `service_badge_class` | category 문자열 → CSS 클래스명 (`badge-cat-web` 등) |
| `or_dash` | 값 → `str(값)`. None → `"-"` |

Redis 캐시에서 복원 시 datetime 필드는 `datetime.fromisoformat()`으로 파싱해야 필터가 동작함 (`json.loads`는 str 반환).

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

### 개발 시나리오별 Vagrant 구성

| 파일 | 기동 스크립트 | VM 수 | 리소스 |
|------|-------------|-------|--------|
| `Vagrantfile` | `dev-up.sh` / `dev-down.sh` | 3 | 1024MB / 2CPU |
| `Vagrantfile.single` | `dev-up-single.sh` / `dev-down-single.sh` | 1 | 512MB / 1CPU |

- 단일 VM 시나리오는 `VAGRANT_VAGRANTFILE=Vagrantfile.single` 환경변수로 선택. 기존 Vagrantfile 미수정.
- `Vagrantfile.single` VM 박스: `bento/ubuntu-22.04`. 패키지 관리자: `apt`.
- VM명: `dev-node-01`. RABBITMQ_HOST: `10.0.2.2` (NAT → 호스트머신 docker-compose).

## Repository 계층 구조

Consumer와 Web이 각자 별도 인터페이스·구현체를 사용한다.

| 파일 | 용도 |
|------|------|
| `db/repositories/base_collect_repository.py` | Consumer용 추상 인터페이스 (`find_server_id`, `upsert_server`, `insert_metric`) |
| `db/repositories/collect_repository.py` | Consumer용 구현체. `AsyncSession`을 생성자에서 주입받음 |
| `db/repositories/base_query_repository.py` | Web용 추상 인터페이스 (`resolve_server_id` 포함) |
| `db/repositories/query_repository.py` | Web용 구현체 |

## 서비스 계층 구조

`web/services/` 하위 모듈 분리:

| 모듈 | 역할 |
|------|------|
| `query_service.py` | QueryService 클래스 (Redis + repo 오케스트레이션, `resolve_server_id` 위임) |
| `mappers.py` | outbound DTO → ViewModel 변환. `enrich_server_detail()` 포함 |
| `metrics_calculator.py` | CPU/Mem/Disk/Net delta 계산 |
| `cache_serializer.py` | Redis serde (ServerDetailResponse, MetricDashboard). 역직렬화 후 `enrich_server_detail()` 재계산 |
| `units.py` | `_kb_to_gb`, `_bytes_to_gb`, `_usage_pct`, `_sector_to_kbps` |
| `service_classifier.py` | `classify(unit) -> str`, `matched_ports(unit, listen_ports) -> list[dict]` |

## 서비스 카테고리 분류

`service_classifier.py`의 `classify(unit)` 함수. 서비스명에서 `.service` suffix 제거 후 소문자 substring 매칭. 매칭 없으면 `"unknown"` 반환.

| 카테고리 | 키워드 예시 |
|---------|-----------|
| `web` | nginx, httpd, apache, caddy, lighttpd, traefik, haproxy |
| `db` | postgresql, mariadb, mysqld, mongod, cassandra, influxdb |
| `cache` | redis, memcached, varnish |
| `mq` | rabbitmq, kafka, activemq, nats |
| `container` | docker, containerd, kubelet |
| `monitor` | prometheus, grafana, datadog, node_exporter, zabbix |

`matched_ports(unit, listen_ports)` — 서비스 유닛에 매핑되는 포트 목록 반환.
- comm 기반 매칭 우선. comm 없으면 `_SERVICE_PORTS` well-known 포트 테이블로 폴백.
- `(proto, port)` 기준 dedup. 반환 형식: `[{"proto": "tcp", "port": 80}, ...]`

- 분류·포트 매핑 로직은 **서비스 계층**(`service_classifier.py`)에서 수행. 매퍼가 호출해 `ServiceItem`을 채움.
- `service_badge_class` Jinja2 필터는 category → CSS 클래스명 변환만 담당.

### 서비스 3단계 표시 계층
| 화면 | 표시 내용 |
|------|---------|
| 서버 목록 (`list.html`) | known 카테고리 뱃지만. 전부 unknown이면 unknown 단일 배지 |
| 서버 상세 (`detail.html`) | known 카테고리 뱃지 + 매핑 포트 칩 + 주요 Listen 포트(≤1024, 서비스 매핑 포트 제외) |
| 서비스 상세 (`services.html`) | 서비스 전체 테이블 (unit/sub/category) + 포트 전체 테이블 |

## ViewModel 설계 원칙

### 레이어 원칙
- **템플릿(HTML)은 순수 표시만** 담당. 분기·계산·필터링은 서비스 계층에서 사전 처리.
- 단위 변환(KB→GB, bytes→GB/TB)은 **서비스 계층**에서 수행. 포맷팅(숫자 → "1.2 TB")은 Jinja2 필터 위임.
- `enrich_server_detail(detail)` — `ServerDetailResponse` 생성 후 또는 캐시 역직렬화 후 반드시 호출. display 전용 파생 필드를 계산한다.

### 주요 ViewModel 필드

`ServiceItem(unit, sub, category, ports, display_name)`
- `display_name`: `unit.removesuffix(".service")` — mapper에서 계산
- `ports`: `matched_ports()` 결과 — detail mapper에서 계산, list mapper는 `[]`

`ServerListItem` 추가 필드 (mapper에서 계산)
- `known_services`: category != "unknown" 인 서비스만
- `show_unknown_badge`: services 있고 전부 unknown일 때 True
- `os_display`: `[os_id, os_version]` 공백 join

`ServerDetailResponse` 추가 필드 (`enrich_server_detail`에서 계산)
- `known_services`: 글로벌 dedup된 chips 포함
- `show_unknown_badge`
- `key_listen_ports`: port ≤ 1024이고 서비스 매핑 포트 번호 제외, port·proto 정렬
- `os_display`, `cpu_display`, `disk_total_gb`

`MountUsageItem` 추가 필드 (`_build_mount_item`에서 계산)
- `badge_class`: usage_pct 임계값(90/75) 기반 CSS 클래스
- `bar_color`: 동일 임계값 기반 hex color

### Redis 캐시 역직렬화 호환성
`cache_serializer.py`의 `server_detail_from_json`은 display 파생 필드(`known_services`, `key_listen_ports`, `os_display`, `cpu_display`, `disk_total_gb`, `show_unknown_badge`)를 data에서 제거한 후 `ServerDetailResponse`를 생성하고 `enrich_server_detail()`로 재계산한다. 구버전 캐시 엔트리와 호환.

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

- 모듈 단위 테스트(pytest) 없음 — 추후 별도 작업
- E2E 검증은 Vagrant VM → Docker compose 파이프라인으로 수행 (`docs/TESTING.md` 참조)
- 테스트 관련 작업은 명시적 요청이 있을 때만 수행

## 문서 구조

| 디렉토리 | 용도 |
|----------|------|
| `docs/` | README 연계 문서 (실행·환경변수·파이프라인 검증) |
| `_design/` | 설계·개념 문서 (컴포넌트 설계, UI 규약, 트레이드오프 등) |
| `_internals/` | 기술 구현 문서 (Python 문법·라이브러리 적용 상세) |

| 파일 | 내용 |
|------|------|
| `docs/TESTING.md` | 파이프라인 검증 (Vagrant VM) |
| `docs/ENV.md` | 환경변수 전체 키 목록 |
| `_design/CONSUMER.md` | Consumer 스키마·핸들러 흐름·MQ 토폴로지 |
| `_design/DB.md` | Repository 계층·DTO·세션 획득 방식 |
| `_design/UI_CONVENTIONS.md` | 템플릿 설계 규약·Chart.js 패턴 |
| `_design/LISTEN_PORTS.md` | Listen 포트 수집 구조 |
| `_design/DESIGN_DECISIONS.md` | 설계 결정·트레이드오프·개선 방향 |
| `_design/REPORT_DESIGN.md` | 보고서 설계 (미구현) |
| `_internals/consumer.md` | schemas.py·handler.py·main.py 기술 구현 |
| `_internals/db.md` | session.py·collect_repository.py 기술 구현 |

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