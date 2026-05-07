# CLAUDE.md

> 본 파일은 본 프로젝트의 **규약 단일 진실 (single source of truth)**.
> 실제 동작은 코드, 흐름은 `docs/components/` · `docs/infra/`, 트레이드오프는 `docs/tradeoffs.md`. 본 파일은 그 위에 얹는 **결정 사항·원칙·금지 사항**만 담는다.
>
> 섹션 번호 규약: **A 시스템 → B 데이터 계약 → C 데이터 계층 → D Consumer → E Web → F 운영 규약**.
> 각 섹션은 자기 계층 책임만 다룬다. 계층 충돌 시 §E1 원칙(P1~P5) 우선순위로 해결.

---

# A. 시스템

## A1. 프로젝트 개요
ZConverter Cloud Assessment Portal — 고객사 내부 네트워크 호스트 인벤토리 수집·저장 B2B 내부 포털.
고객사 네트워크 내에 엔진(web + consumer + MQ + DB)이 설치되고, 각 서버의 **C 기반 에이전트**가 메트릭을 MQ에 직접 발행하여 Consumer가 DB에 저장한다.

## A2. 컨테이너 구성

| 서비스 | 이미지 | 역할 |
|--------|--------|------|
| postgres | timescale/timescaledb:latest-pg16 | 메인 DB |
| rabbitmq | rabbitmq:3.13-management-alpine | 메시지 브로커 |
| redis | redis:7-alpine | 캐시·온라인TTL·PUB/SUB |
| web | 로컬 빌드 | FastAPI SSR + API |
| consumer | 로컬 빌드 | aio-pika 컨슈머 |

운영 결정:
- `consumer depends_on web: condition: service_healthy` — **dev/staging 한정**. web lifespan이 `CREATE EXTENSION + create_all + create_hypertable`을 수행하므로 consumer는 web 헬스체크 후 시작. **prod에서는 lifespan이 schema bootstrap skip**(Alembic 위임)이므로 의존성 제거 가능 — 단계적 전환 (dev-prod.md §10 운영 체크리스트).
- `src/assessment_engine/db/session.py` · `src/assessment_engine/db/redis.py`는 `web_settings`만 사용. `ConsumerSettings`는 `WebSettings` 상속 + RabbitMQ 설정 추가. docker-compose의 `POSTGRES_HOST`/`REDIS_HOST`/`RABBITMQ_HOST` env 오버라이드로 컨테이너 내부 host 결정.
- `src/assessment_engine/scheduler/` 코드는 있으나 docker-compose 미등록 + `run_diagnostics()` NotImplementedError. 미사용.

Compose 파일 분리 (dev-prod.md §6):
- `docker-compose.yml` — prod-safe baseline (password·외부 포트 노출 없음)
- `docker-compose.override.yml` — dev 자동 적용 (.env 평문, 포트 노출, 코드 마운트, APP_ENV=dev)
- `docker-compose.prod.yml` — prod 명시 호출 (Docker secrets, APP_ENV=prod)

## A3. 환경변수
정책·dev/prod 분리·secret 단계는 `docs/dev-prod.md` (단일 진실). 키 카탈로그는 `docs/env.md`.

**핵심 규칙**:
- `APP_ENV` (`dev`/`staging`/`prod`) — 코드 분기는 단 두 곳: `src/assessment_engine/config.py` model_validator (prod 약한 default 거부), `src/assessment_engine/web/main.py` lifespan (prod schema bootstrap skip).
- HOST 변수(`POSTGRES_HOST`/`RABBITMQ_HOST`/`REDIS_HOST`)의 기본값은 docker-compose 서비스명. 호스트 직접 실행 시(IDE 디버깅) `localhost`로 변경.
- docker-compose `environment:` 블록이 컨테이너 내부에서 HOST를 강제 오버라이드 — `.env` 값 변경해도 컨테이너 안에서는 무시.
- prod secret은 `.env` 안 쓰고 `secrets/*` 파일 + `docker-compose.prod.yml` `secrets:` 마운트. pydantic `secrets_dir="/run/secrets"`가 자동 read.
- 에이전트는 엔진의 `.env`를 사용하지 않음 — Vagrantfile이 `infra/agent.env`에서 RabbitMQ 인증·routing 키만 fetch해 VM 안 `/etc/assessment-agent.env`로 옮기고, `RABBITMQ_HOST`는 `10.0.2.2`(NAT)로 별도 주입. (secret 채널 분리 — dev-prod.md §9)
- `EXCHANGE`/`ROUTING_KEY_*` 변경 시 에이전트·컨슈머 양쪽 동기화 필수.

**Compose 호출**:
- dev: `docker compose up` (override.yml 자동 적용)
- prod: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`

## A4. Vagrant 에이전트 배포 규약

### 파일 배치
에이전트 바이너리는 VirtualBox shared folder(`/home/vagrant/assessment-agent/`)에서 직접 실행 불가 — SELinux(Rocky 9) 및 vboxsf 마운트 제약.

| 항목 | 경로 | 이유 |
|------|------|------|
| 바이너리 | `/usr/local/bin/assessment-agent` | systemd가 vboxsf 경유 바이너리를 실행 못함 |
| 환경변수 파일 | `/etc/assessment-agent.env` | SELinux가 `/home/vagrant/` 내 파일 systemd 읽기 차단 |

Vagrantfile step 3에서 `cp` + `chmod 755`, step 2에서 `/etc/assessment-agent.env` 작성. `ExecStart=/usr/local/bin/assessment-agent`, `EnvironmentFile=/etc/assessment-agent.env`.

### 시나리오 구성

`Vagrantfile` 1개. VM 3대(`cache-server-01` / `app-server-01` / `web-server-01`), VM당 1024MB / 2CPU.
기동: `dev-up.sh` (docker compose up + web 헬스체크 대기 + vagrant up). 종료: `dev-down.sh` (vagrant destroy + docker compose down -v).
단일 VM 시나리오는 미구현 — 필요하면 `Vagrantfile`의 `VMS` 배열에서 일부 VM을 주석 처리 또는 `vagrant up cache-server-01` 처럼 개별 기동.

### 합성 부하 (synthetic-load) — 메트릭 차트 가시화 용도
`Vagrantfile` provisioning step 5가 1분 주기 systemd timer(`/etc/systemd/system/synthetic-load.timer`) 등록 → `/usr/local/bin/synthetic-load.sh` 실행. CPU/MEM/DISK/NET 짧은 burst 매 분 발생 → 차트 추이 visible. VM별 시작 sleep 0~30초 random으로 시점 분산.

**프로파일**: `synthetic-load-light.sh` (cache/web), `synthetic-load-heavy.sh` (app-server-01만 — CPU 5~12s × 2코어, MEM 100~250MB, DISK 5~15MB, NET 다중 fetch). symlink로 활성화. Vagrantfile `vm[:name] == "app-server-01"` 분기.

**네트워크는 호스트(10.0.2.2 = NAT 게이트웨이)로 보내야 eth0 net_io 메트릭에 잡힘** — loopback(127.0.0.1)은 lo 인터페이스라 차트에 안 보임.

### 운영 노트 — DB 초기화 시 inventory 자동 회복 (auto-register)
**시나리오**: `docker compose down -v` 후 재기동하면 postgres_data 볼륨 삭제 → server_inventory 비워짐. 에이전트는 broker 재연결 정상이라 metrics는 publish하지만 DB에 미등록 서버라 drop될 위험.
**해결 (이미 적용됨)**: 엔진 consumer가 미등록 metrics 수신 시 auto-register (`src/assessment_engine/consumer/handler.py` + `mappers.placeholder_inventory_from_metrics`) — placeholder inventory(machine_id/hostname/agent_version만 실값) 자동 생성 → metrics 정상 저장. 다음 진짜 inventory 도착 시 `upsert_server`의 ON CONFLICT DO UPDATE로 풀 정보 자동 덮어씀.
**참고**: 에이전트의 broker 자동 재연결·publisher confirm·백오프 retry는 이미 구현되어 있음 (`publish.c`). 이전 문서의 "broker 재기동 시 수동 재시작 필요" 진단은 사실 오류였음 — `docs/tradeoffs.md` T7 정정.

---

# B. 데이터 계약 (Agent → Engine)

정식 정의는 `assessment-agent/docs/payload-schema.md`. 본 절은 엔진 측 핸들링 관점 요약 + 엔진의 책임/무시 항목만.

## B1. 공통 메타데이터
모든 메시지(`inventory` / `metrics` / `error`)에 포함.

| 필드 | 설명 |
|------|------|
| `message_type` | `"inventory"` / `"metrics"` / `"error"` |
| `machine_id` | `/etc/machine-id` 기준. 표준 Linux 32 hex, 가상화 환경 UUID(36자) 가능. DB max 64자 |
| `agent_version` | 에이전트 빌드 버전. **계약 버전 역할** |
| `collected_at` | ISO 8601 UTC |
| `hostname` | 보조 식별자 (가변) |
| `message_id` | UUID v4. **멱등성 키** |

## B2. 메시지 타입

### `server.inventory` (기동 시 1회 + 정적 정보 변경 시)
정적 인프라 — OS·kernel·CPU·메모리/스왑 총량, `disks[]`, `mounts[]`(fstype·total_bytes), `ip_internal[]`/`ip_external[]`, `boot_time`, `services[]` (`{unit, sub}`. non-systemd는 `null`), `listen_ports[]` (`{proto, addr, port, uid, pid, comm}`. 수집 실패는 빈 배열).

엔진 핸들링 노트:
- `InventoryMountInfo.free_bytes`/`avail_bytes`는 핸들러에서 무시. 인벤토리는 정적(total_bytes)만 저장, 동적 사용량은 `server_mount_usage` 시계열로.
- `inventory.disks[]/mounts[]`의 `major`/`minor`는 활용 — `src/assessment_engine/web/services/device_filters.find_parent_disk()`가 mount↔disk 조인 키로 사용 (storage.html "Device" 컬럼).
- `metrics.disk_io[]/mounts[]`의 `major`/`minor`는 시계열 테이블에 컬럼 없어 미저장. 시계열에서도 활용하려면 `ServerDiskIo`/`ServerMountUsage` 컬럼 추가 + `down -v` 필요.

### `server.metrics` (1분 주기)
**모두 raw 누적값**. 엔진이 두 시점 차로 delta·% 계산.
- `cpu_stat` (user/nice/system/idle/iowait/irq/softirq/steal jiffies)
- 메모리·스왑 (total·free·available·buffers·cached, kB)
- `load_1m` / `load_5m` / `load_15m`
- `disk_io[]` per device (`reads_completed` / `writes_completed` / `sectors_read` / `sectors_written`)
- `mounts[]` per mount (`total_bytes` / `free_bytes` / `avail_bytes`)
- `net_io[]` per interface (`rx_bytes` / `tx_bytes` / `rx_packets` / `tx_packets` / `rx_errors` / `tx_errors`)

### `server.error`
에이전트 자가 보고. 핵심 필드 `error_code` / `error_message` / `failed_component` (`collect`/`publish`).
재시도 요약 보고 시점 옵셔널: `retry_count` / `first_failed_at` / `recovered_at` (스키마 v3).
컨슈머는 파싱 + idempotent 후 로깅만. DB 저장 없음.

## B3. 단위·옵션 규약
- 단위: 메모리=`kb`, 디스크/네트워크=`bytes` (`/proc` 출력 관례).
- 옵셔널 필드: 수집 실패 시 `null` 발행. 수집 실패와 데이터 없음을 구분하지 않음.
- counter reset: 재부팅·에이전트 재시작 시 카운터 0 리셋 → 엔진은 `delta < 0` 시 `None` 처리. boot_time 비교 기반 delta 건너뛰기는 미구현.

## B4. MQ 토폴로지
Vhost `/assessment` (전용) / Exchange `assessment` (direct, durable) / DLX `assessment.dlx` (direct, durable) / prefetch_count 10.

| routing key | 큐 | DLQ | 큐 TTL | x-max-length |
|-------------|-----|-----|--------|--------------|
| `server.inventory` | `server.inventory` | `server.inventory.dead` | 없음 (one-shot) | 없음 |
| `server.metrics` | `server.metrics` | `server.metrics.dead` | **72h** | **1,000,000** |
| `server.error` | `server.error` | `server.error.dead` | 300s | 없음 |

DLQ 라우팅: 컨슈머 측 NAK / 큐 TTL 만료 / `x-max-length` 초과(oldest 메시지부터) 시 자동.

`server.metrics` 정책 근거: 1분 주기 발행 + consumer 중단 시 단기 장애 회복 여유 확보(72h). 상한은 메시지당 ~3KB × 1M = ~3GB 메모리 + 디스크 — broker 폭주 방어 동시 충족. 큐 인자 변경 시 broker가 기존 큐 재선언을 PRECONDITION_FAILED로 reject하므로 **rabbitmq 컨테이너 재생성(dev: 볼륨 없음, `--force-recreate rabbitmq`) 또는 큐 수동 삭제(prod) 후 consumer 재기동** 필요.

위 값들은 dev/prod 공통. vhost 개념·권한 모델·AMQPS·TLS·dev/prod 분기·prod 전환 체크리스트의 단일 진실은 `docs/components/rabbitmq.md`.

## B5. 계약 진화 정책 (Forward Compatibility)

에이전트 ↔ 엔진은 독립 배포되므로 한쪽이 먼저 새 필드를 도입할 수 있다. 비대칭 배포에서 엔진이 죽지 않도록:

- Pydantic Input 모델은 **`extra=ignore` 유지** — 에이전트가 새 필드를 추가해도 엔진은 통과시키고 무시.
- 엔진이 활용하지 않는 필드는 §B2 "엔진 핸들링 노트"에 명시 (현재 `disks/mounts/disk_io.major/minor`, `inventory.mounts.free_bytes/avail_bytes`).
- 활용 필요해지면 mapper에서 명시적으로 read하고 inbound DTO 필드 추가. **활용 시점 = 명시적 결정 시점**.
- **금지**: `extra=forbid` — 비대칭 배포에서 새 필드만으로 전체 메시지 reject 위험.
- **금지**: 의미 모르는 필드를 추측으로 미리 매퍼에 추가 — 잘못 저장하면 후속 정정 비용 큼.

`agent_version` 의미: 새 필드 추가는 minor bump, 기존 필드 의미 변경/제거는 major bump (운영자 알림). 본 엔진은 minor만 silent 호환, major는 코드 수정 트리거.

---

# C. 데이터 계층

## C1. ORM 모델 / TimescaleDB

| 모델 | 테이블 | PK 타입 | 설명 |
|------|--------|---------|------|
| ServerInventory | server_inventory | Integer | machine_id 기준 upsert |
| ServerMetrics | server_metrics | BigInteger | 스칼라 메트릭. hypertable |
| ServerDiskIo | server_disk_io | BigInteger | per-device 시계열. hypertable |
| ServerNetIo | server_net_io | BigInteger | per-interface 시계열. hypertable |
| ServerMountUsage | server_mount_usage | BigInteger | per-mount 시계열. hypertable |

### 키·제약
- **대리키 패턴** — 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약.
- `server_inventory.machine_id` UNIQUE — upsert 키.
- `server_inventory.public_id` — `UUID DEFAULT gen_random_uuid()`. URL 식별자. 정수 PK는 노출 금지.
- 시계열 4개 테이블 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB 파티션 키 포함, 무한 누적 대비.
- 시계열 4개 테이블 자연키 UNIQUE 제약 (멱등성 안전망 §D2):
  - `server_metrics`: `UNIQUE(server_id, collected_at)`
  - `server_disk_io`: `UNIQUE(server_id, device, collected_at)`
  - `server_net_io`: `UNIQUE(server_id, interface, collected_at)`
  - `server_mount_usage`: `UNIQUE(server_id, mount, collected_at)`

### TimescaleDB 운영
- 4개 시계열 테이블 모두 hypertable (`collected_at` 파티션).
- **DEV**: web lifespan에서 `CREATE EXTENSION IF NOT EXISTS timescaledb` → `Base.metadata.create_all` (없는 테이블만) → `create_hypertable(if_not_exists => true)`. web 재시작 시 데이터 보존.
- **`create_all`은 기존 테이블에 컬럼/제약(UniqueConstraint) 추가하지 않음** — 모델 변경 후 최초 기동은 `docker compose down -v` 필요.
- **PROD**: Alembic 마이그레이션 + `create_hypertable` 수동 작성.

## C2. Repository 계층

Consumer와 Web이 별도 인터페이스·구현체. 라우터/핸들러는 인터페이스에만 의존.

| 파일 | 용도 |
|------|------|
| `src/assessment_engine/db/repositories/base_collect_repository.py` | Consumer용 추상: `find_server_id`, `upsert_server`, `ensure_server_id`, `record_metrics`. `MetricInsertResult` dataclass |
| `src/assessment_engine/db/repositories/collect_repository.py` | Consumer 구현. session 주입. INSERT는 `pg_insert.on_conflict_do_nothing` 통일. `record_metrics` 내부 4개 private helper(`_insert_scalar_metrics`/`_insert_disk_io`/`_insert_net_io`/`_insert_mount_usage`) |
| `src/assessment_engine/db/repositories/base_query_repository.py` | Web용 추상 + `MetricType`/`TimeRange`/`BucketSize`/`AggFunc` Literal |
| `src/assessment_engine/db/repositories/query_repository.py` | Web 구현. asyncpg 직접 SQL + ORM 혼용 |

### Inbound·Outbound DTO
- Inbound (`inbound.py`):
  - `ServerInventoryCreate` — inventory 1건. `disks`/`mounts`/`services`/`listen_ports`는 JSONB 컬럼 직렬화용이라 `list[dict]` 유지.
  - `ServerMetricCreate` — metrics 1건. 시계열 4테이블 행 매핑이라 nested dataclass: `disk_io: list[DiskIoEntry]`, `mounts: list[MountUsageEntry]`, `net_io: list[NetIoEntry]`. dict 키 오타 컴파일 타임 차단.
  - 흐름: Pydantic 스키마 → mapper → Inbound DTO → Repository.
- Outbound (`outbound.py`) `ServerSummary` / `ServerDetail` / `StorageWithUsage` / `NetworkWithIo` / `DashboardRaw` / `CollectionStatus` / `MetricSeries` — Repository → Service. **raw 단위 그대로** (P1).

### `list_servers` SELECT 정책
`select(ServerInventory)` 풀로우 대신 11개 컬럼 명시 SELECT — `mounts`/`listen_ports`/`kernel_version` 등 큰 JSONB·텍스트는 list 화면 미사용이므로 제외. `docs/tradeoffs.md` T8.

### asyncpg 파라미터 함정
1. **interval 산술**: `collected_at >= :start - interval '5 minutes'` — asyncpg가 `:start` 타입 추론 못함.
   → Python에서 `window_start = start - timedelta(minutes=5)` 계산 후 `:window_start` 파라미터 전달.
2. **named parameter + cast**: `:dim::text`는 SQLAlchemy가 `::` 뒤를 파싱 못해 `:dim`이 그대로 SQL에 남음.
   → `CAST(:dim AS text)` 사용.

## C3. Redis 전략

### 키 설계
| 용도 | 키 | TTL · 무효화 |
|------|----|-----|
| public_id 조회 캐시 | `cache:resolve:{public_id}` | TTL 없음 (public_id 불변) |
| 인벤토리 캐시 | `cache:inventory:{server_id}` | 300s + consumer가 새 inventory 저장 시 즉시 DELETE |
| 메트릭 캐시 | `cache:metrics:{server_id}` | 60s + consumer가 새 metrics 저장 시 즉시 DELETE |
| 멱등성 | `idempotent:{message_id}` | 24h |
| 온라인 TTL | `online:{server_id}` | 90s. consumer가 inventory·metrics 양쪽에서 갱신 |
| 인증 토큰 | `token:{token}` | 1h |

### 채널·정책
- PUB/SUB 채널: `metrics.events` (consumer publish → web SSE 구독 → 브라우저 AJAX 재요청).
- eviction `volatile-lru`, maxmemory 256mb.
- 의존성 주입: web `src/assessment_engine/web/deps.py`에서 `Depends(get_redis)`, consumer는 `get_redis()` 직접 호출. 둘 다 내부적으로 `web_settings.redis_url`.
- Redis 키 패턴은 `WebSettings`에 정의 (consumer는 상속). `query_service.py`는 `web_settings` 직접 참조.

### 장애 정책 — fail-open (모든 Redis 호출)
모든 Redis 호출은 `src/assessment_engine/db/redis.py`의 `safe_*` helper(`safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_publish`) 경유. RedisError 시 silent fallback + warning 로그.
- 캐시 GET 실패 → DB 직접 조회 (응답 정상)
- 캐시 SET 실패 → 다음 요청도 MISS (자연 회복)
- 멱등성 1단 실패 → 처리 진행 → DB UNIQUE(2단)이 중복 흡수
- list mget 실패 → `last_seen_at > now() - redis_ttl_online` fallback (`ServerSummary.last_seen_at`)
- consumer 부수 작업(online SET / cache DEL / publish) 실패 → 로그만, 메시지 처리 정상 진행
- SSE pubsub 끊김 → 브라우저 자동 재연결

상세 매트릭스와 의사결정 근거: `docs/components/redis.md` "Redis 장애 시 동작", `docs/decisions/redis-decoupling.md`.

### 효율 패턴
- 목록 페이지의 N개 서버 온라인 상태 조회는 N번 직렬 `EXISTS` 대신 `redis.mget([online:{id}, ...])` 1회로. `query_service.list_servers` 참조.

---

# D. Consumer

## D1. 구조
- aio-pika 비동기 컨슈머 (FastAPI 독립 프로세스).
- routing key별 핸들러 팩토리: `make_inventory_handler` / `make_metrics_handler` / `make_error_handler` (`src/assessment_engine/consumer/handler.py`).
- 파싱: routing key별 구체 타입(`InventoryInput` / `MetricsInput` / `ErrorInput`) 직접 파싱. `src/assessment_engine/consumer/mappers.py`에서 Pydantic → Inbound DTO 변환.
- 서버 식별 기준: **`machine_id`** (inventory upsert + metrics 서버 조회).
- metrics 핸들러 흐름: `repo.ensure_server_id(machine_id, placeholder)` → `repo.record_metrics(server_id, dto)`. find→upsert 분기는 repository에 캡슐화. `ensure_server_id`가 `(server_id, auto_registered)` 반환 → handler가 auto-register 시점만 운영 로그.
- auto-register: placeholder inventory(machine_id/hostname/agent_version만 실값, 정적 정보 None) → 다음 진짜 inventory 도착 시 ON CONFLICT DO UPDATE로 풀 정보 자동 덮어씀. (`src/assessment_engine/consumer/mappers.placeholder_inventory_from_metrics`)
- record_metrics 반환: `MetricInsertResult(metrics, disk_io, net_io, mount_usage)` — 각 테이블별 INSERT 행 수. handler가 로그에 노출하여 누락·중복 관측 가능.

### 실패 처리
- 파싱 실패 → raise → nack(requeue=False) → DLX → DLQ.
- DB 일시 장애 (`OperationalError` / `DBAPIError`): `_db_retry`로 지수 백오프(`5 ** (attempt + 1)`s = 5s/25s/125s, 합 155s) 3회 후 raise → nack → DLX → DLQ.
- DB 영구 장애 (`IntegrityError` 등): `_db_retry`가 즉시 raise (retry 무의미) → nack → DLX → DLQ. 단 시계열 INSERT는 `ON CONFLICT DO NOTHING`이라 IntegrityError 도달 거의 없음.
- 큐 TTL 만료 → 브로커 자동 DLX → DLQ. metrics 72h, error 300s, inventory 없음.
- `error` 메시지: 파싱 + idempotent + 로깅만 (재시도 컨텍스트 포함). DB 저장 없음.

## D2. 멱등성: 2단 방어 (at-most-once, fail-open 1단)

**1단 — Redis 키 (fail-open)**: 메시지 수신 직후 `safe_set_nx(redis, idempotent:{message_id}, "1", 86400)`. 24h 동안 동일 message_id 재전송을 차단. 가장 빠른 RTT 1회. **Redis 장애 시 True 반환(처리 진행) — 2단이 흡수.**

**2단 — DB UNIQUE 제약**: 시계열 4개 테이블 자연키 UNIQUE (§C1) + `pg_insert(...).on_conflict_do_nothing(index_elements=...)`. Redis 키 만료·evict·재시작·수동 flush·Redis 장애 등으로 1단이 깨져도 DB 레벨에서 silent no-op 흡수.

**at-most-once 트레이드오프**: SET NX는 DB 커밋 이전 실행. 커밋 전 프로세스 크래시 시 broker 재전송 메시지가 idempotent 충돌로 silent 드롭 → 데이터 유실 가능. DB UNIQUE도 같은 시나리오는 못 막음. 한계와 outbox 대안은 `docs/tradeoffs.md` T1.

**fail-open 의존성**: 1단 fail-open은 2단 UNIQUE의 흡수력에 명시적으로 의존. 시계열 4개 테이블 UNIQUE 제약(§C1) 누락 시 멱등성 보장 자체가 깨짐. 모델 변경 시 검증 필수.

## D3. 저장 후 Redis 처리

모든 호출은 `safe_*` helper 경유 — Redis 장애 시 warning 로그 후 진행 (메시지 처리 자체는 정상 완료).

### inventory
1. `safe_set(online:{server_id}, "1", 90)` — 등록 즉시 온라인 판정.
2. `safe_delete(cache:inventory:{server_id})` — 인벤토리 변경(서비스/포트/디스크 등) 즉시 반영. 300s TTL 만료 대기 제거.

### metrics
1. `safe_set(online:{server_id}, "1", 90)`.
2. `safe_delete(cache:metrics:{server_id})` — 캐시 즉시 무효화.
3. `safe_publish(metrics.events, {"server_id": ..., "machine_id": ...})` — 브라우저 SSE 트리거.

**캐시-aside race**: web의 `get_latest_metric`이 cache MISS → DB query 직후 `SET cache:metrics`를 수행하기 전에 consumer가 새 metrics 커밋 + cache DELETE를 끝낼 수 있음. 이 경우 web의 SET이 stale 데이터를 60s TTL로 캐싱. SSE가 즉시 다음 fetch를 trigger하므로 실용적 영향은 최대 1회 표시 지연. `docs/tradeoffs.md` T2.

## D4. 실패 처리 매트릭스 (silent drop · ack · nack)

핸들러를 새로 추가할 때 어떤 실패를 어떻게 처리할지 같은 기준으로 결정한다. 분류 원칙: **메시지 자체의 결함 vs 일시적 외부 장애 vs 의미상 처리 불가**.

| 실패 시나리오 | 처리 | 근거 |
|--------------|------|------|
| Pydantic 파싱 실패 (스키마 위반·필드 타입 오류) | raise → **nack(requeue=False) → DLX → DLQ** | 메시지 자체의 결함. requeue해도 같은 결과. 사람이 DLQ에서 원인 분석 |
| 멱등성 키 중복 (`SET NX` 실패) | 즉시 **ack 후 리턴** (silent) | 정상 동작 (이미 처리됨). 로그 INFO만 |
| DB 일시 장애 (`OperationalError` / `DBAPIError`: connection·deadlock 등) | `_db_retry`로 3회 재시도 (5/25/125s, 합 155s) → 최종 실패 시 **nack → DLX** | 일시 장애 자동 복구. 큐 TTL(metrics 72h / inventory 없음) 안에서 처리 |
| DB 영구 장애 (`IntegrityError` 등: UNIQUE/FK 위반·스키마 mismatch) | `_db_retry`가 **즉시 raise** → nack → DLX | retry 의미 없음. 비용·로그 절약 + 즉시 사람 검토 가능 |
| 미등록 server (machine_id 미존재 + metrics) | 로그 WARNING + **silent ack** (drop) | 의미상 처리 불가지만 재시도해도 무의미. 에이전트가 다음 inventory 보내면 자연 복구. DLQ에 쌓는 것은 노이즈 |
| Redis 일시 장애 (멱등성 체크) | **fail-open** — 처리 진행 → DB UNIQUE(2단) 흡수 | `safe_set_nx`이 None 반환 시 True로 간주. 1단 차단 손실은 일시적, 2단이 정확성 보장 |
| Redis 일시 장애 (online SET / cache DEL / publish) | **fail-open** — warning 로그 + 정상 ack | 부수 작업 실패는 메시지 처리 자체에 영향 없음. SSE는 끊기지만 브라우저가 재연결 |
| 큐 TTL 만료 | 브로커 자동 → **DLX → DLQ** | metrics 72h, error 300s. inventory는 TTL 없음 (one-shot 보장 약함) |
| `error` 메시지 (에이전트 자가 보고) | 로그 WARNING + **silent ack** | 알림 수단으로만 활용. DB 저장 없음 |

**원칙 정리**:
- **silent ack는 "재시도해도 결과 동일 + 데이터 무결성 영향 없음"인 경우에만**. 한 번이라도 의문이면 nack → DLQ.
- **DLQ는 사람이 들여다 봐야 할 케이스만**. 노이즈를 쌓으면 진짜 이상이 묻힌다.
- **자동 retry는 일시 장애에만**. 영구 장애(스키마 위반)에 retry는 비용·로그만 부풀림.
- **fail-open / fail-close 정책**:
  - **DB**: fail-close — 메시지 본체 저장이라 정확성이 절대. `_db_retry`로 일시 장애 흡수 후 최종 실패는 DLQ.
  - **Redis**: fail-open — 캐시·멱등성 1단·부수 작업 모두 `safe_*` helper로 silent fallback. 멱등성 정확성은 DB UNIQUE 2단이 보장. 정책 근거: `docs/decisions/redis-decoupling.md`.

---

# E. Web

## E1. 렌더링 레이어 원칙 (표시 계층 단일 진실)

> **표시 코드를 어디에 둘지 결정할 때 P1~P5 우선순위로 적용.**
> 충돌 시 P1 > P2 > P3 > P5 > P4 (P4는 P3의 명시 예외).

### P1. Repository는 raw 데이터만 (절대)
- raw 단위 그대로 outbound DTO에 담음 (KB·bytes·jiffies·sectors).
- delta·percent·단위 변환·임계값 분류·dedup·정렬·요약 — **금지**.
- **이유**: repo가 표현을 알면 동일 raw 데이터를 다른 화면에서 재가공할 때 우회 변환이 필요해진다. 표현 결정을 한 단계 위로 미뤄야 재사용이 깨지지 않는다.

### P2. 서비스 계층이 표현 변환 단일 소스 (절대)
- Service → mapper → ViewModel 흐름에서 모든 파생 데이터를 계산.
- 단위 변환(KB→GB)·델타(jiffies→%)·임계값 분류(`badge_class`/`bar_color`)·dedup·정렬·합계·풀네임 — 전부 mapper.
- 동일 ViewModel 인스턴스가 SSR(`templates.TemplateResponse`)·JSON(`/api/...` 응답)·Redis 캐시(역직렬화 후) 어느 경로로도 동일하게 일관.
- 캐시 역직렬화 직후에도 `enrich_*()` 같은 동일 파생 함수를 호출 (`server_detail_from_json` → `enrich_server_detail`).

### P3. Jinja2 템플릿은 순수 렌더링만 (절대)
- 허용: 표시에 필요한 분기(`{% if %}`)·반복(`{% for %}`)·Jinja2 필터(포맷팅 전용).
- 금지: 계산(`+`, `*`, `length`, `sort`, `selectattr`로 데이터 가공)·dedup·임계값 비교(`{% if pct >= 90 %}`)·단위 변환.
- 포맷팅(`1234.5` → `"1.2 GB"`)은 ViewModel에 raw 값 + Jinja2 필터(`disksize`/`kbps`/`kst`)가 변환.
- **임계값 기반 분기조차 금지** — `badge_class`/`bar_color`/`is_well_known` 같은 boolean·CSS 클래스를 ViewModel에 미리 계산.
- 정렬은 mapper에서 한 번만 (`sort(attribute='unit')` 같은 템플릿 내 sort 금지) — `sorted_*` 필드를 ViewModel에 둠.

### P4. 클라이언트 차트 JS는 P3 명시 예외
브라우저 인터랙션(range 토글·anchor 변경·legend 체크박스)에 즉시 반응해야 하므로 서버 라운드트립 없이 처리해야 하는 동적 시각화에 한해 JS에 연산 허용.

**허용 연산**:
- 버킷 그리드 생성·서버 응답을 그리드 인덱스로 join·라벨 포매팅(KST 변환·MM/DD HH:mm)
- Chart.js 옵션·데이터셋 객체 조립·legend 체크박스 토글
- 표시 전용 단위 결정(`fmtKbChart`: B/s vs kB/s vs MB/s)

**여전히 금지**:
- 비즈니스 임계값 분류 — 색상/danger 분류는 서버 ViewModel 또는 차트 옵션 명명 상수에서.
- API 응답을 가공해 다시 통계 계산(평균·합계). 서버 `agg=avg|max|p95` 파라미터로 요청해 raw 시계열을 받음.

**의무 규약 (모두 적용)**:

| 규약 | 내용 |
|------|------|
| (a) sequence counter | 모든 비동기 차트 로더에 `let xxxSeq=0; const seq=++xxxSeq; ... if (seq !== xxxSeq) return;`. range 토글 / anchor 변경 시 in-flight 응답 stale 처리 |
| (b) capture-before-await | 전역 state(range·anchor)는 `await` 직전 로컬 변수로 캡처. 렌더 함수도 전역 참조 금지·파라미터로 받음 |
| (c) 응답 형식 방어 | `Array.isArray(rows)` 검사 후 `.map()`. 서버 5xx가 JSON 오브젝트를 반환할 수 있음 (`safeArray()` 사용 권장) |
| (d) 404 분기 | `/metrics/latest` 등 데이터 부재 응답(404)은 try/catch 이전에 `res.status === 404`로 분기. 그렇지 않으면 `r.json()` 파싱 실패가 "불러오기 실패"로 오인됨. fetchChart 같은 헬퍼는 404를 빈 배열로 정규화해 호출자가 status 분기 안 해도 되게 |
| (e) suggestedMax 명명 상수 | Y축 기본 기준선은 스크립트 상단 `const PERF_IOPS_SUGGESTED_MAX = 200;` 형식으로 분리. 임계값 색상도 `USAGE_DANGER_PCT`/`COLOR_DANGER` 등 명명 상수 |

**적용 현황 (Phase 5 검증)**:
- cpu/memory/storage/network/performance.html 5개 모두 (a)~(e) 적용 완료.
- 특히 performance.html은 11개 차트 로더가 `(seq, capturedRange, capturedAnchor)` 시그니처 + `loadAllCharts`가 최상위에서 캡처해 모든 로더에 전달 — race condition 방지.
- `fetchChart`가 404·!ok를 빈 배열로 정규화해 각 로더가 status 분기 안 해도 됨.

### P5. 동일 표현 데이터는 서버에서 한 번만 (P2의 따름)
- ViewModel과 JSON API 응답에 같은 파생 필드를 중복 계산하지 않음.
- 클라이언트 JS는 임계값 분류·dedup·합계·정렬을 다시 수행하지 않고, 서버가 내려준 결과(또는 raw 시계열 + agg 파라미터)를 그대로 표시.
- 예: `MemSnapshot.cached_pct` / `buffers_pct`는 서버 `compute_mem`이 stacked-bar 누적 비율로 미리 계산. 클라이언트는 `style.width = m.cached_pct + '%'`만.
- 예: `ListenPortItem.is_well_known`은 mapper가 계산. 템플릿은 `{% if p.is_well_known %}`만.

## E2. 데이터 흐름

```
RabbitMQ → Consumer → Repository.upsert/insert → DB
                          │
                          └─ Redis (online · cache invalidate · pubsub)

Browser → Router → deps.get_service → QueryService
                                          │
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                       Redis cache    Repository     PUB/SUB
                                          │
                                          ▼
                                 OutboundDTO (raw)
                                          │
                                          ▼
                                   mapper → ViewModel
                                          │
                                          ▼
                                  Template / JSON
```

기준:
- DTO(dataclass) ↔ ORM 모델은 분리 — 변환은 repository 책임.
- inventory upsert·metrics 저장·server_id 조회 모두 `machine_id` 기준. 미등록 metrics는 drop.
- `last_seen_at`: `ServerDetail`(단일 조회)에만 포함. `ServerSummary`(목록)는 Redis `online:{id}` TTL로 표시 — 불필요.
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드. `/collection-status` API에서 두 시각 모두 노출.

## E3. 서비스 계층 모듈

`src/assessment_engine/web/services/`:

| 모듈 | 책임 |
|------|------|
| `query_service.py` | Redis + repo 오케스트레이션. `resolve_server_id` 위임. `get_metric_chart`에서 가상 마운트 필터 + device_category 분류 |
| `mappers.py` | Outbound DTO → ViewModel. raw `list[dict]` → typed 변환의 단일 진입점 (`_to_disk_item`/`_to_listen_port_item`/`_to_service_item`). `enrich_server_detail()`은 idempotent (cache_serializer 역직렬화 후 재호출 가능). 임계값 상수(`_USAGE_DANGER_PCT=90`/`_USAGE_WARN_PCT=75`) + `_usage_severity()` → `_BADGE_CLASS_BY_SEVERITY`/`_BAR_COLOR_BY_SEVERITY` 매핑 |
| `metrics_calculator.py` | CPU/Mem/Disk/Net delta 계산. 공통 helper: `_group_by_dim(rows, key)` (dimension 그룹), `_delta_rate(cur, prev, dt)` (counter rate), `_clip_to_remaining(raw_pct, room)` (mem stacked bar용). `compute_disk_io`/`compute_net_io`는 `_disk_io_snapshot`/`_net_io_snapshot` 페어 helper 사용 |
| `cache_serializer.py` | Redis serde. 역직렬화 후 `enrich_server_detail()` 재계산 (idempotent로 안전) |
| `units.py` | `kb_to_gb` / `bytes_to_gb` / `usage_pct` / `sector_to_kbps` |
| `device_filters.py` | `is_physical_disk` / `is_lvm_disk` / `is_partition` / `is_virtual_mount` / `find_parent_disk` (major·minor 조인) — raw 에이전트 데이터에서 가상 항목 제거 + 디스크 분류 |
| `service_classifier.py` | `classify(unit) -> str` / `matched_ports(unit, listen_ports) -> list[MatchedPort]` |

## E4. ViewModel 설계 (P2 적용)

### 주요 파생 필드 (mapper 계산)

`ServiceItem(unit, sub, category, ports: list[MatchedPort], display_name)` — `MatchedPort(proto, port)`는 view_models의 typed dataclass
- `display_name`: `unit.removesuffix(".service")`.
- `ports`: detail mapper에서 `matched_ports()` 결과. list mapper는 `[]`.

`ListenPortItem` 추가 필드
- `is_well_known`: `port <= 1024`. **템플릿이 임계값 비교 못 하도록** mapper가 계산.

`ServerListItem` (mapper에서 계산)
- `known_services`: category != "unknown" 서비스만, category 기준 dedup.
- `show_unknown_badge`: services 있고 known_services 빈 배열일 때 True.
- `os_display`: `[os_id, os_version]` 공백 join.

`ServerDetailResponse` (`enrich_server_detail`에서 계산)
- `known_services`: 글로벌 dedup된 chips 포함.
- `show_unknown_badge`.
- `key_listen_ports`: `is_well_known` AND 서비스 매핑 포트 번호 제외, port·proto 정렬.
- `sorted_services`: unit ASC. **템플릿 `| sort` 금지**.
- `sorted_listen_ports`: (port, proto) ASC.
- `os_display`, `cpu_display`, `disk_total_gb`.

`MountUsageItem` (`_build_mount_item`에서 계산)
- `badge_class`: usage_pct 임계값(90/75) 기반 CSS 클래스.
- `bar_color`: 동일 임계값 기반 hex color.
- `device_name`: parent disk 이름. `device_filters.find_parent_disk(major, minor, disks)` 결과. inventory.disks/mounts의 (major, minor) 조인. 가상 마운트나 매핑 실패 시 None.

`MemSnapshot` (`compute_mem`에서 계산)
- `cached_pct` / `buffers_pct`: stacked-bar 누적 비율. **클라이언트가 다시 계산하지 않도록** 서버에서 미리 잘라냄.

### Redis 캐시 역직렬화 호환성
`cache_serializer.server_detail_from_json`은 display 파생 필드를 `_DETAIL_DISPLAY_FIELDS` 셋으로 제거 후 `ServerDetailResponse` 생성 → `enrich_server_detail()` 재계산. 새 파생 필드 추가 시 이 셋도 갱신.

`ListenPortItem.is_well_known`은 옛 캐시 호환을 위해 `p.get("is_well_known", p.get("port", 0) <= 1024)` 폴백.

## E5. Query API

### URL 식별자
라우터 `{server_id}` 경로 파라미터는 `public_id` (UUID). 정수 PK 노출 금지.
- path param 타입을 `UUID`로 선언 → invalid 형식은 FastAPI가 422 자동 변환.
- 형식 OK + DB 미존재 → 404 (`resolve_internal_id` Depends).
- `QueryService.resolve_server_id(public_id) -> int | None` — UUID → 정수 PK 변환 브릿지.

### 라우터
| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `src/assessment_engine/web/routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2) |
| `src/assessment_engine/web/routers/api.py` | `api_router` | `/api/v1/servers` | JSON |

### 의존성 주입 (composition root — `src/assessment_engine/web/deps.py`)

| Depends | 책임 |
|---------|------|
| `get_service` | `QueryRepository(db) + Redis → QueryService` 조립. 라우터는 구체 구현 모름 (F4) |
| `resolve_internal_id` | `server_id: UUID` path param → `int` 정수 PK. 422(형식)/404(미존재) 처리. 라우터에서 `internal_id: int = Depends(resolve_internal_id)` 형태로 주입 |

**`_render_server_tab` helper** (`pages.py`): detail/cpu/memory/services/performance 5개 탭이 `service.get_server` + `{"server": ...}` context로 동일하게 렌더링되므로 helper 1개로 묶음. 다른 service 메서드를 쓰는 storage/network는 별도.

### SSR 페이지
| 경로 | 템플릿 | 데이터 |
|------|--------|--------|
| GET /servers/ | servers/list.html | 인벤토리 목록 + Redis online (mget) |
| GET /servers/{id} | servers/detail.html | 인벤토리 정적 정보 (메트릭 AJAX) |
| GET /servers/{id}/storage | servers/storage.html | 인벤토리 disks + 최신 mount_usage |
| GET /servers/{id}/network | servers/network.html | IP + 최신 net_io delta |
| GET /servers/{id}/services | servers/services.html | 서비스 전체 + 포트 전체 |
| GET /servers/{id}/cpu | servers/cpu.html | CPU 추이 차트 (API AJAX) |
| GET /servers/{id}/memory | servers/memory.html | 메모리 추이 차트 (API AJAX) |
| GET /servers/{id}/performance | servers/performance.html | 성능 리포트 (1일/7일/30일 집계) |

### API
| 경로 | 응답 | 설명 |
|------|------|------|
| GET /api/v1/servers/{id}/collection-status | CollectionStatusItem | 수집 상태 + Redis online |
| GET /api/v1/servers/{id}/metrics/latest | MetricDashboard | 계산된 CPU%·mem%·IOPS·kBps·FS% |
| GET /api/v1/servers/{id}/metrics/snapshots | list[MetricSeriesItem] | 히스토리 (커서) |
| GET /api/v1/servers/{id}/metrics/chart | list[MetricSeriesItem] | 차트 시계열 (time_bucket + LAG delta) |
| GET /api/v1/servers/{id}/metrics/stream | SSE | metrics.events 구독 |

### MetricDashboard 구조
`/metrics/latest` 응답. raw jiffies가 아닌 **계산된 값** 반환.
- `cpu`: usage_pct / user_pct / system_pct / iowait_pct (연속 2회 readings delta)
- `memory`: total_kb / used_kb / available_kb / cached_kb / buffers_kb / usage_pct / **cached_pct / buffers_pct** (단일 시점, stacked bar용 미리 계산)
- `swap`: total_kb / used_kb / usage_pct
- `load_1m` / `load_5m` / `load_15m`
- `disk_io_phys[]`: 물리 디스크 (sd*/vd*/nvme*/mmcblk*) — device / read_iops / write_iops / read_kbps / write_kbps
- `disk_io_lvm[]`: LVM 논리볼륨 — 같은 구조
- `disk_io_part[]`: 파티션 폴백 (LVM 없을 때) — 같은 구조
- `net_io[]`: interface / rx_kbps / tx_kbps / rx_pps / tx_pps
- `mounts[]`: mount / total_gb / used_gb / avail_gb / usage_pct

### 차트 쿼리 파라미터
- `metric_type`: `cpu.usage_percent` / `cpu.user_percent` / `cpu.system_percent` / `cpu.iowait_percent` / `load.{1,5,15}m` / `mem.{usage,available,cached,buffers}_percent` / `swap.usage_percent` / `disk.{read,write}_iops` / `fs.usage_percent` / `net.{rx,tx}_bytes_per_sec`
- `dimension`: 복수 인스턴스 메트릭의 특정 대상 (장치명·mountpoint·NIC명)
- `time_range`: `15m` / `1h` / `6h` / `24h` / `7d` / `30d`
- `bucket`: `1m` / `5m` / `15m` / `30m` / `1h` / `3h` / `12h` / `1d`
- `agg`: `avg` / `max` / `p95`
- `device_category`: `phys` / `logical` (LVM 우선, 없으면 파티션 폴백) — disk.{read,write}_iops에만 적용. `DeviceCategory` Literal로 라우터에서 검증.
- 검증: 라우터 `Query(MetricType)` Literal Pydantic이 처리 — 서비스 계층 중복 검증 없음.

## E6. Jinja2 인프라

### 템플릿 인스턴스 — `src/assessment_engine/web/template_setup.py`
`Jinja2Templates` 단일 인스턴스 + filter 등록을 한 모듈에 격리. 라우터는 import만. 라우터에 표시 셋업 책임을 두지 않기 위함.

### 필터
`src/assessment_engine/web/template_filters.py` 정의 → `template_setup.py`에서 `templates.env.filters` 등록.

| 필터 | 동작 |
|------|------|
| `kst` | datetime(UTC) → KST `"YYYY-MM-DD HH:MM:SS"`. None → `"-"` |
| `disksize` | float(GB) → `"1.2 TB"` / `"3.4 GB"`. None → `"-"` |
| `kbps` | float(kBps) → `"1.2 MBps"` / `"3.4 kBps"`. None → `"—"` |
| `service_badge_class` | category → CSS 클래스명 (`badge-cat-web` 등) |
| `or_dash` | 값 → `str(값)`. None → `"-"` |

Redis 캐시에서 datetime은 `datetime.fromisoformat()`으로 파싱 필수 (`json.loads`는 str 반환 → 필터 오작동).

## E7. 정적 자원 — JS 외부화 구조 (Phase 6)

```
src/assessment_engine/web/static/js/
├── chart-utils.js                  ← 공통 유틸 (base.html <head>에서 단일 로드 → 전역 ChartUtils)
└── pages/
    ├── cpu.js / memory.js / storage.js / network.js / performance.js
    └── (각 차트 페이지 로직 — inline <script>에서 외부화. 페이지 간 회귀 격리)
```

`src/assessment_engine/web/main.py`에서 `app.mount("/static", StaticFiles(directory=STATIC_DIR))`.

**페이지 로드 패턴**:
- `base.html` `<head>`에서 `chart-utils.js` 로드 → 전역 `ChartUtils` (자식 template inline script가 즉시 destructure할 수 있도록 head에 둠).
- 각 페이지 `.html`은 짧은 inline `<script>`로 Jinja2 변수만 정의(`SERVER_ID`, `CPU_CORES`) + 외부 `.js` 파일 `defer` 로드.
- 페이지 .js 파일이 `ChartUtils` destructure + 차트 로직 담당.

**외부화 효과** (T6 갱신본):
- 페이지 간 회귀 격리 — sed 일괄 변환 같은 부작용이 한 파일 안에 갇힘.
- `node --check` / IDE 정적 분석 적용 가능.
- 신규 차트 페이지 추가 시 별도 .js 파일 + Jinja2 변수만 inline (inline 신규 금지 — F9).

| 항목 | 제공 |
|------|------|
| 상수 | `RANGE_LABEL` / `AUTO_BUCKET` / `BUCKET_LABEL` / `RANGE_MS` / `BUCKET_MS` / `COLORS` |
| 시간 포매팅 | `fmtKst(iso)` / `fmtLabel(ts, range)` |
| 처리량 포매팅 | `fmtKbChart(v)` (B/s ↔ kB/s ↔ MB/s) |
| anchor 입력 | `getAnchorEnd(inputId)` / `initAnchor(inputId)` |
| 버킷 그리드 | `makeBucketGrid(rangeKey, bucketKey, anchorEnd)` / `joinToGrid(grid, rows, bMs)` |
| 토글 그룹 | `bindToggle(groupId, onChange)` |
| SSE | `initSse(serverId, onMessage)` — dot/label 자동 갱신 + 재연결 메시지 |
| 응답 방어 | `safeArray(arr)` |

각 차트 템플릿은 상단에서 `const { ... } = ChartUtils;`로 destructure. 인라인 중복 정의 금지.

## E8. 도메인 룰: 서비스 카테고리 분류

`service_classifier.py` `classify(unit)`. `.service` suffix 제거 후 소문자 substring 매칭. 매칭 없으면 `"unknown"`.

| 카테고리 | 키워드 예시 |
|---------|-----------|
| `web` | nginx, httpd, apache, caddy, lighttpd, traefik, haproxy |
| `db` | postgresql, mariadb, mysqld, mongod, cassandra, influxdb |
| `cache` | redis, memcached, varnish |
| `mq` | rabbitmq, kafka, activemq, nats |
| `container` | docker, containerd, kubelet |
| `monitor` | prometheus, grafana, datadog, node_exporter, zabbix |

`matched_ports(unit, listen_ports) -> list[MatchedPort]`:
- comm 매칭 우선 → comm 없으면 `_SERVICE_PORTS` well-known 포트 폴백.
- `(proto, port)` dedup. 반환은 `MatchedPort(proto, port)` dataclass 리스트 (이전 dict).

분류·포트 매핑 로직은 **서비스 계층** (P2). 매퍼가 호출해 `ServiceItem` 채움. `service_badge_class` 필터는 category → CSS 클래스명 변환만 (P3).

### 서비스 3단계 표시 계층
| 화면 | 표시 |
|------|------|
| 목록 (`list.html`) | known 카테고리 뱃지 (category dedup, display_name 없음). 전부 unknown이면 unknown 단일 |
| 상세 (`detail.html`) | known 카테고리 뱃지 + 매핑 포트 칩 + 주요 Listen 포트 (`is_well_known` AND 서비스 매핑 포트 제외) |
| 서비스 상세 (`services.html`) | 서비스 전체 테이블 + 포트 전체 테이블 (mapper에서 정렬된 `sorted_*` 사용) |

## E9. 차트 UI 디테일 (P4 적용)

Chart.js 4.4.3.

### 뱃지 CSS 통일
`padding: 4px 10px; border-radius: 6px; font-size: 12px`.

### 비동기 차트 로더 표준 템플릿

```javascript
const { RANGE_LABEL, AUTO_BUCKET, BUCKET_LABEL, BUCKET_MS, COLORS,
        fmtLabel, getAnchorEnd, initAnchor, makeBucketGrid, joinToGrid,
        bindToggle, initSse, safeArray } = ChartUtils;

let xxxSeq = 0;
async function loadXxxChart() {
  const seq = ++xxxSeq;                               // (a)
  const capturedRange  = xxxRange;                    // (b)
  const capturedAnchor = getAnchorEnd('xxx-anchor');
  try {
    const rows = await fetch(`/api/...`).then(r => r.json());
    if (seq !== xxxSeq) return;                       // (a)
    const safe = safeArray(rows);                     // (c)
    renderXxxChart(safe, capturedRange, capturedAnchor);  // (b)
  } catch(e) { console.error(e); }
}

async function loadSnapshot() {                       // (d) 404 분기
  const res = await fetch(`/api/v1/servers/${SERVER_ID}/metrics/latest`);
  if (res.status === 404) { showEmpty(); return; }
  if (!res.ok) return;
  renderSnapshot(await res.json());
}

initSse(SERVER_ID, loadSnapshot);                     // SSE 단일 헬퍼
```

### avg+max 음영 패턴
avg 데이터셋(짝수 인덱스)과 max ghost 데이터셋(홀수 인덱스) 쌍.

- avg: `fill: '+1'`, `pointRadius: 1`, `pointHoverRadius: 3`, 실선
- max ghost: `borderColor: 'transparent'`, `backgroundColor: 'transparent'`, `pointRadius: 0`, `pointHoverRadius: 0`
- ghost 데이터: `bufferedMaxData` — avg가 null인 버킷은 max도 null (빈 구간 음영 방지)
- 실제 max는 `realData` 커스텀 속성 → 툴팁 콜백 `ds.realData[idx]` 참조
- 툴팁 filter: `item.datasetIndex % 2 === 0` — avg 데이터셋만

### Y축 정책 — 두 축 (이중 정책)

차트의 목적에 따라 Y축 기준이 달라진다. 새 차트 추가 시 어느 쪽에 속하는지 먼저 결정.

| 정책 | 적용 대상 | 기준 |
|------|---------|------|
| **A. 분해력 (상세 추이)** | `cpu/memory/storage/network.html`의 추이 차트, 특히 다중 라인 | 변화·이상 탐지가 목적. idle 환경의 작은 값도 시각적으로 보이도록 낮은 `suggestedMax` (soft ceiling) |
| **B. 절대 기준 (진단 리포트)** | `performance.html` | "이 값이 전체 그림에서 위험한가" 판단이 목적. 물리적 한계·% 절대값·비즈니스 임계값 고정 |

같은 metric이 두 페이지에서 다른 스케일을 가질 수 있다 — 의도된 차이.

### Y축 명명 상수 (P4 (e))

스크립트 상단에 분리. 변경 시 의도 추적 가능. **magic number 금지**.

```javascript
// ── 정책 A: 분해력 (상세 추이) ─────────────────────────────────
const NET_Y_SUGGESTED_MAX        = 2048;  // B/s ≈ 2 kB/s — network.html 다중 iface×RX/TX
const STORAGE_IOPS_SUGGESTED_MAX = 5;     // storage.html 다중 device×R/W (idle 0.1 IOPS 분해)
// (cpu.html 로드 추이는 CPU_CORES 변수를 suggestedMax로 사용 — A+B 하이브리드)

// ── 정책 B: 절대 기준 (진단 리포트 performance.html) ─────────
const PERF_IOPS_SUGGESTED_MAX = 200;               // HDD 랜덤 I/O 한계
const PERF_NET_SUGGESTED_MAX  = 10 * 1024 * 1024;  // 10 MB/s — 1 Gbps의 8%

// ── 색상 임계값 — 서버 mappers._usage_bar_color 와 동일 기준. 변경 시 양쪽 동기화.
const USAGE_DANGER_PCT = 90;
const USAGE_WARN_PCT   = 75;
const SWAP_DANGER_PCT  = 0.1;
const COLOR_OK = '#3b82f6'; const COLOR_WARN = '#f59e0b';
const COLOR_DANGER = '#ef4444'; const COLOR_NEUTRAL = '#64748b';
```

### 차트별 Y축 매트릭스

| 페이지 | 차트 | Y축 | 정책 |
|--------|------|-----|------|
| performance | CPU·Memory·Mount 사용률 | `min:0, max:100` | B 절대 |
| performance | Swap | `suggestedMax: 25` | B 부분절대 (낮은값 부각) |
| performance | Load 15m (단일) | `suggestedMax: cpu_cores ∥ 4` | B 물리 포화 |
| performance | Disk Read/Write IOPS | `suggestedMax: PERF_IOPS_SUGGESTED_MAX` (200) | B 물리 한계 |
| performance | Net RX/TX | `suggestedMax: PERF_NET_SUGGESTED_MAX` (10 MB/s) | B 비즈니스 기준 |
| cpu | CPU 사용률 추이 (단일+ghost) | `min:0, max:100` | B 절대 |
| cpu | CPU 분류 추이 (3선 user/system/iowait) | `beginAtZero, auto` | A 분해력 |
| cpu | 로드 평균 추이 (3선 1m/5m/15m) | `beginAtZero, suggestedMax: CPU_CORES` | **A+B 하이브리드** (분해력+포화 임계 시각화) |
| memory | Memory 사용률 추이 (단일+ghost) | `min:0, max:100` | B 절대 |
| memory | Memory 구성 추이 (4선 used/avail/cached/buffers) | `beginAtZero, auto` | A 분해력 |
| memory | Swap 사용률 추이 (단일+ghost) | `suggestedMax: 25` | B 부분절대 |
| storage | 물리/논리 I/O 추이 (다중 device×R/W) | `suggestedMax: STORAGE_IOPS_SUGGESTED_MAX` (5), `precision:0` | A 분해력 |
| storage | 파일시스템 사용량 추이 (다중 mount) | `min:0, max:100` | B 절대 (% 단위) |
| network | 네트워크 I/O 추이 (다중 iface×RX/TX) | `suggestedMax: NET_Y_SUGGESTED_MAX` (2 kB/s), 동적 단위 (`fmtKbChart`) | A 분해력 |

### SSE 상태 + 수집기준시간 레이아웃
SSE dot/label과 수집기준시간 span은 **단일 flex 컨테이너** 안에 두어 줄바꿈 방지.

```html
<div id="sse-status" style="display:flex; align-items:center; gap:5px; font-size:11px; color:#94a3b8; white-space:nowrap;">
  <span id="sse-dot" class="dot dot-off"></span>
  <span id="sse-label">연결 중...</span>
  <span id="xxx-snapshot-ts" style="margin-left:4px;"></span>
</div>
```

### 헤더 브랜드
`base.html`의 `ZConverter Assessment` 브랜드는 `/servers/`로 이동하는 `<a>`. 별도 nav 링크 없음.

---

# F. 운영 규약

## F1. 타입 어노테이션
- **`from __future__ import annotations` 절대 금지** — 전 파일.
- `TYPE_CHECKING` 블록은 순환 임포트가 실제로 발생하는 경우에만. Python 3.12 어노테이션은 즉시 평가되어 `NameError` 유발.
- **런타임 데드코드 금지** — `assert x is not None` 같이 type checker 만족용 런타임 검사 금지. 비용·예외 위험만 있고 가치 없음.

### IDE 경고 대처

| Severity | 정석 |
|----------|------|
| Error | 무조건 fix |
| Warning | 원인 분류 후 처리 (아래 우선순위) |
| Info / Hint | 그대로 둠 (시각적 노이즈만 — 코드 더럽히지 않음) |

**Warning 처리 우선순위**:
1. **타입 어노테이션·변수 추출로 의도 명확화** — type checker가 자연스럽게 narrow 가능하면 그 방향이 가장 정석.
2. **외부 라이브러리 type stub의 false positive** → `# type: ignore[specific_code]` (specific code 명시 + 이유 한 줄 주석). 무분별한 generic `# type: ignore` 금지.
3. `cast(T, x)`는 런타임 NO-OP이라 `assert`보다는 안전하지만 **narrowing 의도**라 stub 한계엔 `# type: ignore`가 더 솔직. cast는 진짜 "타입 변환" 의도일 때만 (예: `Any` → 구체 타입).

## F2. 시간대 정책 (UTC 저장 / KST 표시)

`collected_at`/`last_seen_at` 등 모든 datetime은 다음 단일 경계로 처리.

| 계층 | 형식 | 변환 책임 |
|------|------|-----------|
| Agent → Consumer 메시지 | ISO 8601 UTC (`...Z`) | 에이전트가 `gettimeofday() + UTC`로 발행 |
| DB 저장 | `TIMESTAMPTZ` (UTC) | SQLAlchemy가 자동 변환 |
| Repository / Service / ViewModel | `datetime` (tzinfo=UTC) | 변환 없음 — raw 그대로 |
| Redis 캐시 (JSON serde) | ISO 8601 UTC 문자열 | `cache_serializer`가 `datetime.fromisoformat()`으로 복원 (`json.loads`만 쓰면 str 반환 → 필터 오작동) |
| API 응답 (JSON) | ISO 8601 UTC | dataclass `asdict` + `_json_default` |
| Jinja2 SSR 표시 | `"YYYY-MM-DD HH:MM:SS"` (KST) | `kst` 필터 — 표시 직전 단일 변환 |
| 클라이언트 차트 라벨 | KST `MM/DD HH:mm` | `ChartUtils.fmtLabel(iso, range)` — 표시 직전 단일 변환 |

**원칙**:
- **DB·내부 계층 어디에서도 KST 변환 금지**. Service에서 KST로 비교/필터링하면 다른 화면 재사용 시 깨진다.
- 변환 경계는 정확히 두 곳: Jinja2 `kst` 필터, JS `fmtLabel`. 새 표시 추가 시 이 둘 중 하나 사용.
- naive datetime 금지 — `cache_serializer`처럼 외부에서 문자열 받을 때 `datetime.fromisoformat()`으로 tzinfo 보존.

## F3. 검증의 단일 경로

같은 입력을 여러 계층에서 반복 검증하지 않는다. 검증은 **요청 진입 시 한 곳**에만.

| 입력 | 검증 위치 | 다른 계층은 |
|------|-----------|-------------|
| HTTP query string (FastAPI) | 라우터 `Query(MetricType/TimeRange/BucketSize/AggFunc/DeviceCategory)` Literal Pydantic | Service에서 재검증 금지 — `_VALID_*` 같은 frozenset 비교 안 만든다 |
| HTTP path UUID (`{server_id}`) | `resolve_internal_id` Depends — `UUID` 타입 강제로 422, 미존재 시 404 | Service는 정수 PK만 받음 |
| RabbitMQ 메시지 payload | Consumer `model_validate_json()` Pydantic | Mapper/Repository에서 재검증 금지 — 이미 타입 보장 |
| URL path param `{public_id}` | 라우터 `_resolve()` 헬퍼가 404 처리 | Service는 정수 PK만 받음 |
| 환경변수 | `BaseSettings` 필드 타입 (자동 형변환) | 사용처에서 재검증 금지 |

**이유**: 중복 검증은 (a) 양쪽이 어긋나면 어느 쪽이 진실인지 모호, (b) 새 enum 값 추가 시 누락 위험, (c) 라우터의 자동 422 응답을 우회. 단일 경로가 더 안전하고 단순.

## F4. 인터페이스 우선 — Composition Root 패턴

새 비즈니스 컴포넌트(Service/Handler) 추가 시 다음 패턴.

| 항목 | 규칙 |
|------|------|
| Service/Handler 의존성 | **Base 추상 인터페이스**만 받음 (생성자 또는 팩토리 인자) |
| 구체 구현체 import | **Composition Root 1곳에서만** |
| Composition Root | web=`src/assessment_engine/web/deps.py`, consumer=`src/assessment_engine/consumer/main.py` |
| 새 Repository 추가 | `src/assessment_engine/db/repositories/base_*.py` 추상 우선 → `src/assessment_engine/db/repositories/*.py` 구현 → composition root에서 주입 |

**현재 적용**:
- `QueryService(repo: BaseQueryRepository, redis: Redis)` — `src/assessment_engine/web/deps.get_service`가 `QueryRepository(db)` 주입.
- `make_*_handler(session_factory, repo_factory: Callable[[AsyncSession], BaseCollectRepository], redis)` — `src/assessment_engine/consumer/main`이 `CollectRepository`를 팩토리로 주입.

**금지**:
- Service/Handler 안에서 `from db.repositories.collect_repository import CollectRepository` 같은 구체 import.
- Composition Root 외부에서 `Settings()` 같은 전역 인스턴스 새로 만들기 — 단일 모듈 변수 (`web_settings`/`consumer_settings`) 재사용.

**이유**: 테스트·배포 환경 변경 시 구현체 교체 가능 (예: in-memory repo로 테스트, 새 DB 백엔드 시범 도입). 컴포넌트 경계가 코드로 강제된다.

## F5. 테스트 정책

### 계층

| 계층 | 위치 | 도구 | 격리 |
|------|------|------|------|
| **Unit** | `tests/unit/` | pytest + AsyncMock | DB·외부 의존 없음 (Redis는 mock) |
| **Integration** | `tests/integration/` | pytest-asyncio + testcontainers (TimescaleDB) | function-scope `db_session` transaction rollback |
| **E2E** | Vagrant VM → Docker compose 파이프라인 | `docs/pipeline.md` | 실제 VM·broker·DB |

### 인프라
- `pytest-asyncio` v1+: `asyncio_mode=auto` + fixture·test 양쪽 `loop_scope=session` (pyproject `[tool.pytest.ini_options]`).
- `testcontainers[postgres]`: 세션당 1회 TimescaleDB 컨테이너 spawn → `Base.metadata.create_all` + `create_hypertable` → 모든 테스트 공유. session 종료 시 teardown.
- function-scope `db_session`은 commit 안 하고 finally에서 rollback — 테스트 간 격리.

### Fixture 계층 (`tests/conftest.py`, `tests/integration/conftest.py`)
- session: `_postgres_container`, `engine`
- function: `db_session`, `collect_repo`, `query_repo`

### 데이터 빌더 (`tests/factories.py`)
- `make_inventory()`, `make_metrics()` 함수 형태. factory_boy 미도입 (단일 도메인이라 함수가 정석).

### 명령
- 전체: `python -m pytest`
- 단위만 (빠름): `python -m pytest tests/unit/`
- 통합 (컨테이너 필요): `python -m pytest tests/integration/`

### 적용 범위 (현재)
- ✅ Redis `safe_*` helper (단위, 15 tests)
- ✅ `CollectRepository` (통합, 12 tests)
- ✅ `QueryRepository` (통합, 35 tests — 17 metric_type 모두 dispatch + helper 정확성)
- ✅ Service 계층 (Phase 2 — 단위 146 tests):
  - `units.py` (19), `device_filters.py` (50), `service_classifier.py` (19), `metrics_calculator.py` (22), `mappers.py` (33)
  - `enrich_server_detail` idempotent 검증 포함
- ⏳ Web router (Phase 3 리팩토링과 함께)

### 원칙
- 새 코드 추가 시 테스트도 함께 작성 (코드 리뷰 시 cause).
- 리팩토링은 테스트 통과 baseline 위에서만 진행 — 회귀 즉시 식별.
- E2E (Vagrant) 검증은 별도 — pytest는 unit + integration만.

## F6. 문서 구조

| 디렉토리 | 용도 | 수명 |
|----------|------|------|
| `docs/` | README가 직접 링크하는 1급 문서 (정책·카탈로그·핵심 절차) | 영구·갱신 |
| `docs/components/` | 컴포넌트별 통합 문서 (설계·규약·기술 구현) | 영구·갱신 |
| `docs/infra/` | 인프라 문서 (Docker·Vagrant) | 영구·갱신 |
| `docs/decisions/` | ADR 스타일 의사결정 기록 — "왜 이렇게 결정했나"의 근거 보존 | 영구·불변 (정정만, 덮어쓰기 금지) |
| `docs/meetings/` | 미팅 합의·일회성 메모 (`YYYY-MM-DD-주제.md` 형식) | 임시 (영구 정책은 다른 영구 문서로 승격) |

`temp` 키워드 들어간 파일(`docs/temp.md` 등)은 작업 중 임시 메모로 **항상 무시**.

| 파일 | 내용 |
|------|------|
| `docs/pipeline.md` | 파이프라인 검증 (Vagrant VM) |
| `docs/env.md` | 환경변수 전체 키 목록 (카탈로그) |
| `docs/dev-prod.md` | dev/prod 환경 전략 + secret 정책 + 운영 체크리스트 |
| `docs/testing.md` | 단위·통합 테스트 실행·설정·Fixture·작성 패턴 |
| `docs/tradeoffs.md` | 의식적 설계 선택과 그 한계 (T1~T11) |
| `docs/components/agent.md` | 에이전트 메시지 스키마 / 포트 수집 / 디스크 필터링 |
| `docs/components/consumer.md` | schemas / handler / main / 멱등성 / 재시도 |
| `docs/components/db.md` | ORM 모델 / DTO / Repository / TimescaleDB |
| `docs/components/redis.md` | 키 설계 / TTL / PUB/SUB / 멱등성 / 캐시 무효화 / mget |
| `docs/components/rabbitmq.md` | vhost·권한 모델 / 토폴로지 / dev/prod 분기 / prod 전환 체크리스트 |
| `docs/components/web.md` | 레이어 원칙 / 서비스 모듈 / ViewModel / Jinja2 / 차트 UI / chart-utils.js |
| `docs/infra/docker.md` | Dockerfile / docker-compose (볼륨·헬스체크·기동 순서·env) |
| `docs/infra/vagrant.md` | Vagrant 사용 맥락 / VM 구성 / 프로비저닝 흐름 |
| `docs/decisions/redis-decoupling.md` | Redis fail-open 전환 의사결정 + 옵션 비교 + 구현 결과 |
| `docs/meetings/2026-05-08-agent-protocol.md` | 에이전트 프로토콜 협의 미팅 노트 |

## F7. 브랜치 전략
| 브랜치 | 용도 |
|--------|------|
| main | 배포용. 직접 push 금지 |
| develop | 개발 통합. PR로만 머지 |
| feature/xxx | 기능 |
| fix/xxx | 버그 |
| chore/xxx | 설정 |

## F8. 커밋 컨벤션
설명은 한글.

| 타입 | 설명 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| chore | 설정·패키지 변경 |
| refactor | 리팩토링 |
| test | 테스트 코드 |

## F9. 자동화 변환 — Claude 의무 검증

자동화 변환(sed / Edit `replace_all` / 디렉토리 mv / Python 스크립트로 일괄 갱신) 후, **사용자 발견 전에** 다음 4-step을 매 회 자동 수행하고 결과를 사용자에게 보고한다.

### Must (변환 직후 매 회 의무)

1. **옛 패턴 잔존 0건 확인** — grep 명시 출력. 잔존 시 즉시 추가 변환.
2. **새 패턴이 의도된 스코프에만** — 함수 외부 / 의도 외 위치에 들어갔는지 grep + awk(함수 경계)로 검증.
3. **syntax check** — Python: `ast.parse` / JS: `node --check` / Compose: `docker compose config --quiet`.
4. **테스트 실행** — 영향 범위에 따라 pytest unit/integration 또는 endpoint smoke (`curl /health` 등).

### Must Not

- 변환 후 검증 생략하고 다음 단계로 진행.
- "테스트 통과 = OK" 단정 — 테스트 미커버 영역(JS·템플릿·인프라) 多.
- 사용자 IDE 경고나 브라우저 콘솔 발견에 의존.

### 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| **sed / Edit `replace_all`** | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| **디렉토리 mv** | `from X` import (들여쓰기 포함), `import X` (단순), **문자열 형태 모듈 경로** (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| **DTO·모델 타입 변경** | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| **동시성 코드** (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| **Frontend JS** | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `node --check` + 사용자 IDE에서 경고 0건 |

### 누적 사고 패턴 (반면교사 — 같은 실수 금지)

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange→capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

### 사고 발생 시 자기 책임

> 사용자가 회귀 사고를 먼저 발견했다면, **본 §F9의 4-step 검증을 누락한 것**. 외부화·도구 부재가 아니라 절차 누락이 책임.
> 같은 패턴 사고가 재발하면 본 섹션의 "누적 사고 패턴" 표에 추가 + 검증 절차에 누락된 단계 보강.