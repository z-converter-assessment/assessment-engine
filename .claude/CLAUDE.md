# CLAUDE.md

> 본 파일은 본 프로젝트의 규약 단일 진실 (single source of truth).
> 실제 동작은 코드, 흐름은 `docs/architecture/` · `docs/operations/`, 트레이드오프는 `docs/adr/tradeoffs.md`. 본 파일은 그 위에 얹는 결정 사항·원칙·금지 사항만 담는다.
>
> 섹션 번호 규약: A 시스템 → B 데이터 계약 → C 데이터 계층 → D Consumer → E Web → F 운영 규약.
> 각 섹션은 자기 계층 책임만 다룬다. 계층 충돌 시 #E1 원칙(P1~P5) 우선순위로 해결.

## 문서 인덱스

본 파일을 읽다가 "상세는 X 절"을 만나면 아래 표에서 해당 문서를 찾아 직접 점프.

| 디렉토리 | 용도 | 수명 |
|----------|------|------|
| [docs/README.md](../docs/README.md) | 인덱스 — 어떤 문서를 언제 보는지 길잡이 | 영구·갱신 |
| `docs/architecture/` | 컴포넌트별 deep dive (모듈 설계·기술 구현) | 영구·갱신 |
| `docs/operations/` | 운영·환경·배포·검증 (Docker·Vagrant·dev-prod·env·testing·pipeline) | 영구·갱신 |
| [docs/adr/README.md](../docs/adr/README.md) | Architecture Decision Records — "왜 이렇게 결정했나" + 트레이드오프 | 영구·불변 (정정만, 덮어쓰기 금지) |
| `docs/meetings/` | 미팅 합의·일회성 메모 (`YYYY-MM-DD-주제.md` 형식) | 임시 (영구 정책은 다른 영구 문서로 승격) |

`temp` 키워드 들어간 파일(`docs/temp.md` 등)은 작업 중 임시 메모로 항상 무시.

| 파일 | 내용 |
|------|------|
| [docs/operations/pipeline.md](../docs/operations/pipeline.md) | 파이프라인 검증 (Vagrant VM) |
| [docs/operations/env.md](../docs/operations/env.md) | 환경변수 전체 키 목록 (카탈로그) |
| [docs/operations/dev-prod.md](../docs/operations/dev-prod.md) | dev/prod 환경 전략 + secret 정책 + 운영 체크리스트 |
| [docs/operations/testing.md](../docs/operations/testing.md) | 단위·통합 테스트 실행·설정·Fixture·작성 패턴 |
| [docs/adr/tradeoffs.md](../docs/adr/tradeoffs.md) | 의식적 설계 선택과 그 한계 (T1~T11) |
| [docs/architecture/agent.md](../docs/architecture/agent.md) | 에이전트 메시지 스키마 / 포트 수집 / 디스크 필터링 |
| [docs/architecture/consumer.md](../docs/architecture/consumer.md) | schemas / handler / main / 멱등성 / 재시도 |
| [docs/architecture/db.md](../docs/architecture/db.md) | ORM 모델 / DTO / Repository / TimescaleDB |
| [docs/architecture/redis.md](../docs/architecture/redis.md) | 키 설계 / TTL / PUB/SUB / 멱등성 / 캐시 무효화 / mget |
| [docs/architecture/rabbitmq.md](../docs/architecture/rabbitmq.md) | vhost·권한 모델 / 토폴로지 / dev/prod 분기 / prod 전환 체크리스트 |
| [docs/architecture/web.md](../docs/architecture/web.md) | 레이어 원칙 / 서비스 모듈 / ViewModel / Jinja2 / 차트 UI / chart-utils.js |
| [docs/operations/docker.md](../docs/operations/docker.md) | Dockerfile / docker-compose (볼륨·헬스체크·기동 순서·env) |
| [docs/operations/vagrant.md](../docs/operations/vagrant.md) | Vagrant 사용 맥락 / VM 구성 / 프로비저닝 흐름 |
| [docs/adr/0001-redis-decoupling.md](../docs/adr/0001-redis-decoupling.md) | Redis fail-open 전환 의사결정 + 옵션 비교 + 구현 결과 |

---

# A. 시스템

## A1. 프로젝트 개요
ZConverter Cloud Assessment Portal — 고객사 내부 네트워크 호스트 인벤토리 수집·저장 B2B 내부 포털.
고객사 네트워크 내에 엔진(web + consumer + MQ + DB)이 설치되고, 각 서버의 C 기반 에이전트가 메트릭을 MQ에 직접 발행하여 Consumer가 DB에 저장한다.

## A2. 컨테이너 구성

5개 서비스(postgres / rabbitmq / redis / web / consumer)로 구성. 이미지·역할·command 분기·빌드 캐시 전략은 `docs/operations/docker.md`.

운영 결정:
- `consumer depends_on web: condition: service_healthy` — dev/staging 한정. web lifespan이 `CREATE EXTENSION + create_all + create_hypertable`을 수행하므로 consumer는 web 헬스체크 후 시작. prod에서는 lifespan이 schema bootstrap skip(Alembic 위임)이므로 의존성 제거 가능 — 단계적 전환 (dev-prod.md #10 운영 체크리스트).
- `src/assessment_engine/db/session.py` · `src/assessment_engine/db/redis.py`는 `web_settings`만 사용. `ConsumerSettings`는 `WebSettings` 상속 + RabbitMQ 설정 추가. docker-compose의 `POSTGRES_HOST`/`REDIS_HOST`/`RABBITMQ_HOST` env 오버라이드로 컨테이너 내부 host 결정.
- `src/assessment_engine/scheduler/` 코드는 있으나 docker-compose 미등록 + `run_diagnostics()` NotImplementedError. 미사용.

Compose 파일 분리 (dev-prod.md #6):
- `docker-compose.yml` — prod-safe baseline (password·외부 포트 노출 없음)
- `docker-compose.override.yml` — dev 자동 적용 (.env 평문, 포트 노출, 코드 마운트, APP_ENV=dev)
- `docker-compose.prod.yml` — prod 명시 호출 (Docker secrets, APP_ENV=prod)

## A3. 환경변수
정책·dev/prod 분리·secret 단계는 `docs/operations/dev-prod.md` (단일 진실). 키 카탈로그는 `docs/operations/env.md`.

핵심 규칙:
- `APP_ENV` (`dev`/`staging`/`prod`) — 코드 분기는 단 두 곳: `src/assessment_engine/config.py` model_validator (prod 약한 default 거부), `src/assessment_engine/web/main.py` lifespan (prod schema bootstrap skip).
- HOST 변수(`POSTGRES_HOST`/`RABBITMQ_HOST`/`REDIS_HOST`)의 기본값은 docker-compose 서비스명. 호스트 직접 실행 시(IDE 디버깅) `localhost`로 변경.
- docker-compose `environment:` 블록이 컨테이너 내부에서 HOST를 강제 오버라이드 — `.env` 값 변경해도 컨테이너 안에서는 무시.
- prod secret은 `.env` 안 쓰고 `secrets/*` 파일 + `docker-compose.prod.yml` `secrets:` 마운트. pydantic `secrets_dir="/run/secrets"`가 자동 read.
- 에이전트는 엔진의 `.env`를 사용하지 않음 — Vagrantfile이 `infra/agent.env`에서 RabbitMQ 인증·routing 키만 fetch해 VM 안 `/etc/assessment-agent.env`로 옮기고, `RABBITMQ_HOST`는 `10.0.2.2`(NAT)로 별도 주입. (secret 채널 분리 — dev-prod.md #9)
- `EXCHANGE`/`ROUTING_KEY_*` 변경 시 에이전트·컨슈머 양쪽 동기화 필수.

Compose 호출:
- dev: `docker compose up` (override.yml 자동 적용)
- prod: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`

## A4. Vagrant 에이전트 배포 규약

원칙: 에이전트 바이너리는 vboxsf shared folder에서 직접 실행 불가 (SELinux + systemd 제약) → `/usr/local/bin/`로 cp + `EnvironmentFile`은 `/etc/`로 분리.

상세(파일 배치·VM 매트릭스·provisioning 단계·합성 부하 프로파일·트러블슈팅): `docs/operations/vagrant.md`.

---

# B. 데이터 계약 (Agent → Engine)

정식 정의는 `assessment-agent/docs/payload-schema.md` (외부 레포). 엔진 측 핸들링 관점 요약·엔진이 무시하는 필드 카탈로그·활용 중인 필드는 `docs/architecture/agent.md`. 본 절은 엔진의 책임 / 미저장 결정 / 계약 진화 정책만.

## B1. 메시지 타입 — 엔진 측 결정

3가지 routing key (`server.inventory` / `server.metrics` / `server.error`). 메시지 타입별 필드 카탈로그·공통 메타데이터·서브모델 구조는 `docs/architecture/agent.md` "메시지 타입" 절.

공통 메타데이터에 `agent_started_at` (에이전트 프로세스 기동 시각) + `boot_time` (시스템 부팅 시각) 포함 — 두 값 비교로 시스템 재부팅 vs 에이전트 재시작 구분 가능. counter reset 식별의 정확도가 이 두 필드에 의존.

inventory는 기동 시 1회 + 정적 정보 변경 시 + 1시간 주기 자동 재발행. 엔진 측 데이터 손실(DB 장애·메시지 유실) 발생 시 자동 회복 트리거 역할.

엔진 핸들링 결정:
- `InventoryMountInfo.free_bytes`/`avail_bytes`는 핸들러에서 무시. 인벤토리는 정적(total_bytes)만 저장, 동적 사용량은 `server_mount_usage` 시계열로 분리.
- `inventory.disks[]/mounts[]`의 `major`/`minor`는 활용 — `src/assessment_engine/web/services/device_filters.find_parent_disk()`가 mount↔disk 조인 키로 사용 (storage.html "Device" 컬럼).
- `metrics.disk_io[]/mounts[]`의 `major`/`minor`는 시계열 테이블에 컬럼 없어 미저장. 시계열에서도 활용하려면 `ServerDiskIo`/`ServerMountUsage` 컬럼 추가 + `down -v` 필요.
- `server.metrics`는 raw 누적값으로 발행 — 엔진이 두 시점 차로 delta·% 계산 (P1: repository는 raw만 저장, 변환은 service).
- `server.error`는 파싱 + idempotent + 로깅만. DB 저장 없음.

## B2. 단위·옵션 규약
- 단위: 메모리=`kb`, 디스크/네트워크=`bytes` (`/proc` 출력 관례).
- 옵셔널 필드: 수집 실패 시 `null` 발행. 수집 실패와 데이터 없음을 구분하지 않음.
- counter reset: 재부팅·에이전트 재시작 시 카운터 0 리셋 → 엔진은 `delta < 0` 시 `None` 처리. 두 시점의 `boot_time` 차이 시 시스템 재부팅 → delta 계산 건너뛰기. 두 시점의 `agent_started_at`만 다르면 에이전트 재시작 → 카운터는 그대로라 delta 정상 계산 가능.

## B3. MQ 토폴로지

핵심 파라미터(에이전트·컨슈머 동기화 필수):

| routing key | 큐 | DLQ | 큐 TTL | x-max-length |
|-------------|-----|-----|--------|--------------|
| `server.inventory` | `server.inventory` | `server.inventory.dead` | 없음 (one-shot) | 없음 |
| `server.metrics` | `server.metrics` | `server.metrics.dead` | 72h | 1,000,000 |
| `server.error` | `server.error` | `server.error.dead` | 300s | 없음 |

Vhost `/assessment` / Exchange `assessment` (direct, durable) / DLX `assessment.dlx` / prefetch_count 10. dev/prod 공통.

큐 인자 변경 시 broker가 기존 큐 재선언을 PRECONDITION_FAILED로 reject — 큐 삭제 또는 rabbitmq 재생성 후 consumer 재기동 필요. 정책 근거(72h/1M 산정 / DLQ 라우팅 트리거 / 변경 절차) / vhost 권한 모델 / AMQPS·TLS / prod 전환 체크리스트의 단일 진실은 `docs/architecture/rabbitmq.md`.

## B4. 계약 진화 정책 (Forward Compatibility)

에이전트 ↔ 엔진은 독립 배포되므로 한쪽이 먼저 새 필드를 도입할 수 있다. 비대칭 배포에서 엔진이 죽지 않도록:

- Pydantic Input 모델은 `extra=ignore` 유지 — 에이전트가 새 필드를 추가해도 엔진은 통과시키고 무시.
- 엔진이 활용하지 않는 필드는 #B1 "엔진 핸들링 결정"에 명시 (현재 `disks/mounts/disk_io.major/minor`, `inventory.mounts.free_bytes/avail_bytes`). 카탈로그는 `docs/architecture/agent.md` "엔진이 받지만 사용하지 않는 필드".
- 활용 필요해지면 mapper에서 명시적으로 read하고 inbound DTO 필드 추가. 활용 시점 = 명시적 결정 시점.
- 금지: `extra=forbid` — 비대칭 배포에서 새 필드만으로 전체 메시지 reject 위험.
- 금지: 의미 모르는 필드를 추측으로 미리 매퍼에 추가 — 잘못 저장하면 후속 정정 비용 큼.

`agent_version` 의미: 새 필드 추가는 minor bump, 기존 필드 의미 변경/제거는 major bump (운영자 알림). 본 엔진은 minor만 silent 호환, major는 코드 수정 트리거.

---

# C. 데이터 계층

## C1. 키·제약 — 멱등성 의존

ORM 모델 카탈로그(6개 테이블) / Inbound·Outbound DTO 카탈로그 / TimescaleDB 운영 / asyncpg 파라미터 함정: `docs/architecture/db.md` "ORM 모델" · "Inbound DTO" · "Outbound DTO" · "TimescaleDB" · "차트 dimension 필터" 절.

본 절의 결정/금지(D2 멱등성·E5 URL 식별자에 직접 의존):

- 대리키 패턴 — 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약.
- `server_inventory.machine_id` UNIQUE — upsert 키.
- `server_inventory.public_id` — `UUID DEFAULT gen_random_uuid()`. URL 식별자. 정수 PK는 노출 금지.
- 시계열 5개 테이블 복합 PK `(id BIGINT, collected_at TIMESTAMPTZ)` — TimescaleDB 파티션 키 포함.
- 시계열 5개 테이블 자연키 UNIQUE (#D2 멱등성 2단 방어 의존 — 누락 시 멱등성 보장 자체 깨짐):
  - `server_metrics`: `UNIQUE(server_id, collected_at)`
  - `server_disk_io`: `UNIQUE(server_id, device, collected_at)`
  - `server_net_io`: `UNIQUE(server_id, interface, collected_at)`
  - `server_mount_usage`: `UNIQUE(server_id, mount, collected_at)`
  - `server_inventory_history`: `UNIQUE(server_id, collected_at)` — append-only 변경 이력. upsert_server에서 직전 행 비교 후 변경 시에만 INSERT.

스키마 변경 운영 결정: DEV에서 `create_all`은 기존 테이블에 컬럼/제약 추가하지 않음 — 모델 변경 후 최초 기동은 `docker compose down -v` 필요. PROD는 Alembic + `create_hypertable` 수동.

## C2. Repository 계층 — 인터페이스 우선 (F4)

Consumer와 Web 양쪽 별도 인터페이스·구현체. 라우터/핸들러는 추상(`BaseCollectRepository`/`BaseQueryRepository`)에만 의존, 구체 구현체 import는 composition root(`deps.py` / `consumer/main.py`)에서만.

DTO 흐름:
- Inbound (`inbound.py`): Pydantic 스키마 → mapper → Inbound DTO → Repository. 시계열 행 매핑은 nested dataclass(`DiskIoEntry`·`MountUsageEntry`·`NetIoEntry`)로 컴파일 타임 키 보장.
- Outbound (`outbound.py`): Repository → Service. raw 단위 그대로 (P1) — KB·bytes·jiffies·sectors. 변환 금지.

INSERT 통일: 시계열은 `pg_insert(...).on_conflict_do_nothing(index_elements=...)` — 멱등성 2단 방어(#D2).

`list_servers`는 `select(ServerInventory)` 풀로우 대신 11개 컬럼 명시 SELECT (큰 JSONB·텍스트 제외). 트레이드오프 근거 `docs/adr/tradeoffs.md` T8.

repo 메서드 카탈로그·asyncpg 함정·`_chart_*` 패턴: `docs/architecture/db.md` "Collect 계층" · "Query 계층" · "차트 SQL 패턴" · "차트 dimension 필터" 절.

## C3. Redis 전략 — fail-open 의무

모든 Redis 호출은 `src/assessment_engine/db/redis.py`의 `safe_*` helper(`safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_publish`) 경유. RedisError 시 silent fallback + warning 로그. 직접 redis client 호출 금지.

fail-open 핵심 결과(다른 계층이 의존):
- 멱등성 1단 fail-open → DB UNIQUE(#D2 2단)이 중복 흡수 — 시계열 4테이블 UNIQUE(#C1) 누락 시 보장 깨짐.
- list mget 실패 → `last_seen_at > now() - redis_ttl_online` fallback (`ServerSummary.last_seen_at` 필드 의존).
- consumer 부수 작업(online SET / cache DEL / publish) 실패 → 메시지 처리는 정상 진행.

효율 패턴: 목록 N개 서버 온라인 조회는 N번 직렬 `EXISTS` 대신 `redis.mget([online:{id}, ...])` 1회. `query_service.list_servers` 참조.

키 설계 표 / TTL 근거 / PUB/SUB 채널 / 캐시-aside race 한계 / 장애 매트릭스 전체: `docs/architecture/redis.md`. 의사결정 ADR: `docs/adr/0001-redis-decoupling.md`.

---

# D. Consumer

## D1. 구조
- aio-pika 비동기 컨슈머 (FastAPI 독립 프로세스).
- routing key별 핸들러 팩토리: `make_inventory_handler` / `make_metrics_handler` / `make_error_handler` (`src/assessment_engine/consumer/handler.py`).
- 파싱: routing key별 구체 타입(`InventoryInput` / `MetricsInput` / `ErrorInput`) 직접 파싱. `src/assessment_engine/consumer/mappers.py`에서 Pydantic → Inbound DTO 변환.
- 서버 식별 기준: `machine_id` (inventory upsert + metrics 서버 조회).
- metrics 핸들러 흐름: `repo.ensure_server_id(machine_id, placeholder)` → `repo.record_metrics(server_id, dto)`. find→upsert 분기는 repository에 캡슐화. `ensure_server_id`가 `(server_id, auto_registered)` 반환 → handler가 auto-register 시점만 운영 로그.
- auto-register: placeholder inventory(machine_id/hostname/agent_version만 실값, 정적 정보 None) → 다음 진짜 inventory 도착 시 ON CONFLICT DO UPDATE로 풀 정보 자동 덮어씀. (`src/assessment_engine/consumer/mappers.placeholder_inventory_from_metrics`)
- record_metrics 반환: `MetricInsertResult(metrics, disk_io, net_io, mount_usage)` — 각 테이블별 INSERT 행 수. handler가 로그에 노출하여 누락·중복 관측 가능.

### 실패 처리
- 파싱 실패 → raise → nack(requeue=False) → DLX → DLQ.
- DB 일시 장애 (`OperationalError` / `DBAPIError`): `_db_retry`로 지수 백오프(`5  (attempt + 1)`s = 5s/25s/125s, 합 155s) 3회 후 raise → nack → DLX → DLQ.
- DB 영구 장애 (`IntegrityError` 등): `_db_retry`가 즉시 raise (retry 무의미) → nack → DLX → DLQ. 단 시계열 INSERT는 `ON CONFLICT DO NOTHING`이라 IntegrityError 도달 거의 없음.
- 큐 TTL 만료 → 브로커 자동 DLX → DLQ. metrics 72h, error 300s, inventory 없음.
- `error` 메시지: 파싱 + idempotent + 로깅만 (재시도 컨텍스트 포함). DB 저장 없음.

## D2. 멱등성: 2단 방어 (at-most-once, fail-open 1단)

1단 — Redis 키 (fail-open): 메시지 수신 직후 `safe_set_nx(redis, idempotent:{message_id}, "1", 86400)`. 24h 동안 동일 message_id 재전송을 차단. 가장 빠른 RTT 1회. Redis 장애 시 True 반환(처리 진행) — 2단이 흡수.

2단 — DB UNIQUE 제약: 시계열 4개 테이블 자연키 UNIQUE (#C1) + `pg_insert(...).on_conflict_do_nothing(index_elements=...)`. Redis 키 만료·evict·재시작·수동 flush·Redis 장애 등으로 1단이 깨져도 DB 레벨에서 silent no-op 흡수.

at-most-once 트레이드오프: SET NX는 DB 커밋 이전 실행. 커밋 전 프로세스 크래시 시 broker 재전송 메시지가 idempotent 충돌로 silent 드롭 → 데이터 유실 가능. DB UNIQUE도 같은 시나리오는 못 막음. 한계와 outbox 대안은 `docs/adr/tradeoffs.md` T1.

fail-open 의존성: 1단 fail-open은 2단 UNIQUE의 흡수력에 명시적으로 의존. 시계열 4개 테이블 UNIQUE 제약(#C1) 누락 시 멱등성 보장 자체가 깨짐. 모델 변경 시 검증 필수.

## D3. 저장 후 Redis 처리 — fail-open

inventory·metrics 저장 성공 시 routing key별 Redis 후처리는 모두 `safe_*` helper 경유 (#C3) — 부수 작업 실패가 메시지 처리 ack를 막지 않는다. 캐시-aside race(web SET이 stale 데이터를 캐싱) 한계는 `docs/adr/tradeoffs.md` T2.

후처리 시퀀스(inventory: online SET + cache DELETE / metrics: online SET + cache DELETE + PUBLISH metrics.events): `docs/architecture/consumer.md` "handler.py" 절.

## D4. 실패 처리 매트릭스 (silent drop · ack · nack)

원칙: 메시지 자체 결함 → DLQ. 일시 외부 장애 → 재시도 후 DLQ. 의미상 처리 불가 → silent ack. DB는 fail-close, Redis는 fail-open.

상세(시나리오별 매트릭스·재시도 백오프·DLQ 운영): `docs/architecture/consumer.md` "handler.py — 메시지 처리 흐름" · "DB 재시도 정책" · "DLQ 메시지 검사" 절.

---

# E. Web

## E1. 렌더링 레이어 원칙 (표시 계층 단일 진실)

> 표시 코드를 어디에 둘지 결정할 때 P1~P5 우선순위로 적용.
> 충돌 시 P1 > P2 > P3 > P5 > P4 (P4는 P3의 명시 예외).

### P1. Repository는 raw 데이터만 (절대)
- raw 단위 그대로 outbound DTO에 담음 (KB·bytes·jiffies·sectors).
- delta·percent·단위 변환·임계값 분류·dedup·정렬·요약 — 금지.
- 이유: repo가 표현을 알면 동일 raw 데이터를 다른 화면에서 재가공할 때 우회 변환이 필요해진다. 표현 결정을 한 단계 위로 미뤄야 재사용이 깨지지 않는다.

### P2. 서비스 계층이 표현 변환 단일 소스 (절대)
- Service → mapper → ViewModel 흐름에서 모든 파생 데이터를 계산.
- 단위 변환(KB→GB)·델타(jiffies→%)·임계값 분류(`badge_class`/`bar_color`)·dedup·정렬·합계·풀네임 — 전부 mapper.
- 동일 ViewModel 인스턴스가 SSR(`templates.TemplateResponse`)·JSON(`/api/...` 응답)·Redis 캐시(역직렬화 후) 어느 경로로도 동일하게 일관.
- 캐시 역직렬화 직후에도 `enrich_*()` 같은 동일 파생 함수를 호출 (`server_detail_from_json` → `enrich_server_detail`).

### P3. Jinja2 템플릿은 순수 렌더링만 (절대)
- 허용: 표시에 필요한 분기(`{% if %}`)·반복(`{% for %}`)·Jinja2 필터(포맷팅 전용).
- 금지: 계산(`+`, `*`, `length`, `sort`, `selectattr`로 데이터 가공)·dedup·임계값 비교(`{% if pct >= 90 %}`)·단위 변환.
- 포맷팅(`1234.5` → `"1.2 GB"`)은 ViewModel에 raw 값 + Jinja2 필터(`disksize`/`kbps`/`kst`)가 변환.
- 임계값 기반 분기조차 금지 — `badge_class`/`bar_color`/`is_well_known` 같은 boolean·CSS 클래스를 ViewModel에 미리 계산.
- 정렬은 mapper에서 한 번만 (`sort(attribute='unit')` 같은 템플릿 내 sort 금지) — `sorted_*` 필드를 ViewModel에 둠.

### P4. 클라이언트 차트 JS는 P3 명시 예외
브라우저 인터랙션(range 토글·anchor 변경·legend 체크박스)에 즉시 반응해야 하므로 서버 라운드트립 없이 처리해야 하는 동적 시각화에 한해 JS에 연산 허용.

허용 연산:
- 버킷 그리드 생성·서버 응답을 그리드 인덱스로 join·라벨 포매팅(KST 변환·MM/DD HH:mm)
- Chart.js 옵션·데이터셋 객체 조립·legend 체크박스 토글
- 표시 전용 단위 결정(`fmtKbChart`: B/s vs kB/s vs MB/s)

여전히 금지:
- 비즈니스 임계값 분류 — 색상/danger 분류는 서버 ViewModel 또는 차트 옵션 명명 상수에서.
- API 응답을 가공해 다시 통계 계산(평균·합계). 서버 `agg=avg|max|p95` 파라미터로 요청해 raw 시계열을 받음.

의무 규약 (모두 적용):

| 규약 | 내용 |
|------|------|
| (a) sequence counter | 모든 비동기 차트 로더에 `let xxxSeq=0; const seq=++xxxSeq; ... if (seq !== xxxSeq) return;`. range 토글 / anchor 변경 시 in-flight 응답 stale 처리 |
| (b) capture-before-await | 전역 state(range·anchor)는 `await` 직전 로컬 변수로 캡처. 렌더 함수도 전역 참조 금지·파라미터로 받음 |
| (c) 응답 형식 방어 | `Array.isArray(rows)` 검사 후 `.map()`. 서버 5xx가 JSON 오브젝트를 반환할 수 있음 (`safeArray()` 사용 권장) |
| (d) 404 분기 | `/metrics/latest` 등 데이터 부재 응답(404)은 try/catch 이전에 `res.status === 404`로 분기. 그렇지 않으면 `r.json()` 파싱 실패가 "불러오기 실패"로 오인됨. fetchChart 같은 헬퍼는 404를 빈 배열로 정규화해 호출자가 status 분기 안 해도 되게 |
| (e) suggestedMax 명명 상수 | Y축 기본 기준선은 스크립트 상단 `const PERF_IOPS_SUGGESTED_MAX = 200;` 형식으로 분리. 임계값 색상도 `USAGE_DANGER_PCT`/`COLOR_DANGER` 등 명명 상수 |

적용 현황 (Phase 5 검증):
- cpu/memory/storage/network/performance.html 5개 모두 (a)~(e) 적용 완료.
- 특히 performance.html은 11개 차트 로더가 `(seq, capturedRange, capturedAnchor)` 시그니처 + `loadAllCharts`가 최상위에서 캡처해 모든 로더에 전달 — race condition 방지.
- `fetchChart`가 404·!ok를 빈 배열로 정규화해 각 로더가 status 분기 안 해도 됨.

### P5. 동일 표현 데이터는 서버에서 한 번만 (P2의 따름)
- ViewModel과 JSON API 응답에 같은 파생 필드를 중복 계산하지 않음.
- 클라이언트 JS는 임계값 분류·dedup·합계·정렬을 다시 수행하지 않고, 서버가 내려준 결과(또는 raw 시계열 + agg 파라미터)를 그대로 표시.
- 예: `MemSnapshot.cached_pct` / `buffers_pct`는 서버 `compute_mem`이 stacked-bar 누적 비율로 미리 계산. 클라이언트는 `style.width = m.cached_pct + '%'`만.
- 예: `ListenPortItem.is_well_known`은 mapper가 계산. 템플릿은 `{% if p.is_well_known %}`만.

## E2. 데이터 흐름 결정

- DTO(dataclass) ↔ ORM 모델 분리 — 변환은 repository 책임.
- inventory upsert·metrics 저장·server_id 조회 모두 `machine_id` 기준. 미등록 metrics는 drop.
- `last_seen_at`은 `ServerDetail`(단일 조회)에만 포함. `ServerSummary`(목록)는 Redis `online:{id}` TTL로 표시.
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드.

다이어그램 / 라우터 모듈 표 / SSR 페이지 표 / JSON API 표 / MetricDashboard 구조 / 차트 쿼리 파라미터: `docs/architecture/web.md` "데이터 흐름" · "라우터" 절.

## E3. 서비스 계층 모듈

`src/assessment_engine/web/services/` 하위 7개 모듈 (`query_service` / `mappers` / `metrics_calculator` / `cache_serializer` / `units` / `device_filters` / `service_classifier`)의 책임 분리 — 모듈별 상세 책임은 `docs/architecture/web.md` "서비스 계층 모듈" 절.

본 절의 결정/원칙: P2(서비스 계층 단일 변환)에 따라 모든 표시 파생은 `mappers.py`로 집중. `enrich_server_detail()`은 idempotent — cache 역직렬화 후 재호출 안전. 임계값 상수(`_USAGE_DANGER_PCT=90`/`_USAGE_WARN_PCT=75`)는 mapper에서만 정의 — 템플릿/JS 중복 정의 금지.

## E4. ViewModel·Mapper 파생 필드 (P2)

mapper에서 모든 파생 필드 계산 — 단위 변환·정렬·dedup·임계값 분류·`is_well_known`·`cached_pct`·`badge_class`·`bar_color` 등. 템플릿은 ViewModel 그대로 표시.

새 display 파생 필드 추가 시 `cache_serializer._DETAIL_DISPLAY_FIELDS` 셋 동기화 필수 — 누락 시 캐시 역직렬화가 옛 값 사용.

상세 필드 카탈로그(`ServiceItem` / `ListenPortItem` / `ServerListItem` / `ServerDetailResponse` / `MountUsageItem` / `MemSnapshot` 파생 명세, 캐시 호환성 폴백): `docs/architecture/web.md` "ViewModel 설계" 절.

## E5. URL 식별자 — 정수 PK 노출 금지

라우터 `{server_id}` 경로 파라미터는 `public_id` (UUID). 정수 PK 노출 금지.
- path param `UUID` 타입 선언 → invalid 형식 422 자동.
- 형식 OK + DB 미존재 → 404 (`resolve_internal_id` Depends).
- `QueryService.resolve_server_id(public_id) -> int | None` — UUID → 정수 PK 브릿지.

검증의 단일 경로(F3): 라우터 Pydantic이 검증 책임. Service에서 재검증 금지.

## E6. Jinja2 인프라

`Jinja2Templates` 단일 인스턴스 + 필터 등록은 `src/assessment_engine/web/template_setup.py`에 격리. 라우터는 import만 하고 표시 셋업 책임을 갖지 않음. Redis 캐시에서 datetime은 `datetime.fromisoformat()`로 파싱 필수 (`json.loads` str 그대로 두면 `kst` 필터 오작동).

필터 카탈로그(`kst` / `disksize` / `kbps` / `service_badge_class` / `or_dash`): `docs/architecture/web.md` "Jinja2 인프라" 절.

## E7. 정적 자원 — JS 외부화 의무

신규 차트 로직은 외부 `.js` 파일에 작성. inline `<script>`에 차트 로직 신규 추가 금지 (F9 자동화 변환 검증의 "Frontend JS" 항목과 연결 — 페이지 간 회귀 격리 / 정적 분석 / 자동화 변환 안전성). 페이지 `.html`은 Jinja2 변수 정의 + 외부 `.js` `defer` 로드만 허용.

`base.html` `<head>`에서 `chart-utils.js` 단일 로드 → 전역 `ChartUtils`. 각 페이지 .js가 destructure하여 사용. 인라인 중복 정의 금지.

디렉토리 구조 / `ChartUtils` API 카탈로그 / 페이지별 .js 파일 구성: `docs/architecture/web.md` "정적 자원" 절.

## E8. 도메인 분류 책임 (P2)

서비스 카테고리 분류(`classify`)·포트 매핑(`matched_ports`)은 서비스 계층(`service_classifier.py`)에서 수행. 매퍼가 호출해 `ServiceItem`에 채움. 템플릿은 `service_badge_class` 필터로 category → CSS 클래스 변환만 (P3).

키워드 매칭 표 / `_SERVICE_PORTS` 폴백 / 서비스 3단계 표시 계층(목록·상세·services 페이지): `docs/architecture/web.md` "service_classifier.py" 절.

## E9. 차트 UI 디테일 (P4 적용)

원칙: Y축은 분해력(추이 차트) vs 절대 기준(진단 리포트) 두 정책 중 어디 속하는지 먼저 결정. magic number 금지(명명 상수). 비동기 로더는 P4 (a)~(e) 의무 규약(sequence counter / capture-before-await / Array.isArray / 404 분기 / suggestedMax 상수).

상세(차트별 Y축 매트릭스·suggestedMax 상수 정의·avg+max ghost 패턴·로더 표준 템플릿): `docs/architecture/web.md` "템플릿 차트 UI 설계" 절.

---

# F. 운영 규약

## F1. 타입 어노테이션
- `from __future__ import annotations` 절대 금지 — 전 파일.
- `TYPE_CHECKING` 블록은 순환 임포트가 실제로 발생하는 경우에만. Python 3.12 어노테이션은 즉시 평가되어 `NameError` 유발.
- 런타임 데드코드 금지 — `assert x is not None` 같이 type checker 만족용 런타임 검사 금지. 비용·예외 위험만 있고 가치 없음.

### IDE 경고 대처

| Severity | 정석 |
|----------|------|
| Error | 무조건 fix |
| Warning | 원인 분류 후 처리 (아래 우선순위) |
| Info / Hint | 그대로 둠 (시각적 노이즈만 — 코드 더럽히지 않음) |

Warning 처리 우선순위:
1. 타입 어노테이션·변수 추출로 의도 명확화 — type checker가 자연스럽게 narrow 가능하면 그 방향이 가장 정석.
2. 외부 라이브러리 type stub의 false positive → `# type: ignore[specific_code]` (specific code 명시 + 이유 한 줄 주석). 무분별한 generic `# type: ignore` 금지.
3. `cast(T, x)`는 런타임 NO-OP이라 `assert`보다는 안전하지만 narrowing 의도라 stub 한계엔 `# type: ignore`가 더 솔직. cast는 진짜 "타입 변환" 의도일 때만 (예: `Any` → 구체 타입).

### Hook 강제 채널

`.claude/hooks/` PostToolUse hook이 강제하는 위반(exit 2 → system-reminder 피드백)은 본 표의 Warning 우선순위와 별개의 강제 채널 — 즉시 수정. IDE Info-Hint와 달리 Claude 컨텍스트로 직접 피드백되므로 묻힐 위험 없음.

| 위반 | Hook |
|------|------|
| `from __future__ import annotations` 추가 | `conventions-check.sh` |

ruff 위반(E501 line-too-long · F841 unused · I001 import 정렬 등)은 hook 자동 차단 채널 없음 — PyCharm IDE 경고 또는 수동 `.venv/bin/ruff check <file>` 실행으로 검증. 위 표의 Warning 우선순위로 처리.

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

원칙:
- DB·내부 계층 어디에서도 KST 변환 금지. Service에서 KST로 비교/필터링하면 다른 화면 재사용 시 깨진다.
- 변환 경계는 정확히 두 곳: Jinja2 `kst` 필터, JS `fmtLabel`. 새 표시 추가 시 이 둘 중 하나 사용.
- naive datetime 금지 — `cache_serializer`처럼 외부에서 문자열 받을 때 `datetime.fromisoformat()`으로 tzinfo 보존.

## F3. 검증의 단일 경로

같은 입력을 여러 계층에서 반복 검증하지 않는다. 검증은 요청 진입 시 한 곳에만.

| 입력 | 검증 위치 | 다른 계층은 |
|------|-----------|-------------|
| HTTP query string (FastAPI) | 라우터 `Query(MetricType/TimeRange/BucketSize/AggFunc/DeviceCategory)` Literal Pydantic | Service에서 재검증 금지 — `_VALID_*` 같은 frozenset 비교 안 만든다 |
| HTTP path UUID (`{server_id}`) | `resolve_internal_id` Depends — `UUID` 타입 강제로 422, 미존재 시 404 | Service는 정수 PK만 받음 |
| RabbitMQ 메시지 payload | Consumer `model_validate_json()` Pydantic | Mapper/Repository에서 재검증 금지 — 이미 타입 보장 |
| URL path param `{public_id}` | 라우터 `_resolve()` 헬퍼가 404 처리 | Service는 정수 PK만 받음 |
| 환경변수 | `BaseSettings` 필드 타입 (자동 형변환) | 사용처에서 재검증 금지 |

이유: 중복 검증은 (a) 양쪽이 어긋나면 어느 쪽이 진실인지 모호, (b) 새 enum 값 추가 시 누락 위험, (c) 라우터의 자동 422 응답을 우회. 단일 경로가 더 안전하고 단순.

## F4. 인터페이스 우선 — Composition Root 패턴

새 비즈니스 컴포넌트(Service/Handler) 추가 시 다음 패턴.

| 항목 | 규칙 |
|------|------|
| Service/Handler 의존성 | Base 추상 인터페이스만 받음 (생성자 또는 팩토리 인자) |
| 구체 구현체 import | Composition Root 1곳에서만 |
| Composition Root | web=`src/assessment_engine/web/deps.py`, consumer=`src/assessment_engine/consumer/main.py` |
| 새 Repository 추가 | `src/assessment_engine/db/repositories/base_*.py` 추상 우선 → `src/assessment_engine/db/repositories/*.py` 구현 → composition root에서 주입 |

현재 적용:
- `QueryService(repo: BaseQueryRepository, redis: Redis)` — `src/assessment_engine/web/deps.get_service`가 `QueryRepository(db)` 주입.
- `make_*_handler(session_factory, repo_factory: Callable[[AsyncSession], BaseCollectRepository], redis)` — `src/assessment_engine/consumer/main`이 `CollectRepository`를 팩토리로 주입.

금지:
- Service/Handler 안에서 `from db.repositories.collect_repository import CollectRepository` 같은 구체 import.
- Composition Root 외부에서 `Settings()` 같은 전역 인스턴스 새로 만들기 — 단일 모듈 변수 (`web_settings`/`consumer_settings`) 재사용.

이유: 테스트·배포 환경 변경 시 구현체 교체 가능 (예: in-memory repo로 테스트, 새 DB 백엔드 시범 도입). 컴포넌트 경계가 코드로 강제된다.

## F9. 자동화 변환 — 책임 분담

자동화 변환(sed / Edit `replace_all` / 디렉토리 mv / Python 일괄 갱신) 직후 사용자 발견 전에 검증 책임을 다음 3채널로 분담한다.

### 채널별 책임

| 채널 | 자동성 | 담당 영역 |
|------|--------|-----------|
| Hook (`.claude/hooks/`) | 강제·무인 | `from __future__ import annotations` 차단 (.py F1 위반) |
| 메인 세션 | 자가 검증 | (1) 옛 패턴 잔존 grep (2) 새 패턴 스코프 검증 (3) Frontend JS 외부 .js 강제 (4) DTO·매퍼·cache_serializer·템플릿·JS 체인 동기화. 변환 직후 매 회 보고 의무 |
| 에이전트 (사용자 호출) | 사용자 트리거 | code-reviewer / schema-contract-auditor — 사용자 명시 요청 시에만 발동 (`리뷰해줘`·`스키마 일관성 확인` 등). 메인 자동 위임 제안 없음 |

Hook 강제 영역은 메인이 또 grep으로 확인하지 않는다 (중복). ruff·syntax 검증은 자동 hook 채널 없음 — PyCharm IDE 경고로 노출, F1 IDE 경고 처리 정책에 따라 메인/사용자가 판단.

에이전트 결과 수신 시 메인 행동:
- Error → 즉시 수정. 변경은 사용자 가시 보고.
- Warning → 사용자 결정 위임.
- Info → 보고만, 무시 가능.

### 메인 자가 검증 — Must (변환 직후 매 회)

1. 옛 패턴 잔존 0건 확인 — grep 명시 출력. 잔존 시 즉시 추가 변환.
2. 새 패턴이 의도된 스코프에만 — 함수 외부 / 의도 외 위치에 들어갔는지 grep + awk(함수 경계)로 검증.
3. Frontend JS 외부 .js 강제 — .html 변경 시 신규 inline `<script>`에 코드 줄 들어갔는지 grep (단순 `src=`만 있는 빈 script는 허용).
4. DTO·매퍼·cache_serializer·템플릿·JS 체인 동기화 — 의미적 검증 (hook 자동화 불가).

테스트 실행은 본 검증의 의무 단계가 아니며 사용자가 명시 요청할 때만 수행 (tests/는 `.claudeignore` + 리팩토링 중 실패 상태).

### Must Not

- 변환 후 검증 생략하고 다음 단계로 진행.
- 사용자 IDE 경고나 브라우저 콘솔 발견에 의존.
- 명시 요청 없이 pytest 실행하거나 "테스트 통과 확인했음"을 검증 결과로 보고.
- Hook 강제 영역(F1 future annotations) 메인이 또 확인 — 중복.

### 변환 유형별 추가 체크

| 유형 | 추가 검증 |
|------|---------|
| sed / Edit `replace_all` | 들여쓰기 무관 패턴 (`^[[:space:]]*` 사용 여부), 줄 시작·끝 스코프, 문자열 리터럴 안까지 영향 위치 grep |
| 디렉토리 mv | `from X` import (들여쓰기 포함), `import X` (단순), 문자열 형태 모듈 경로 (`"web.main:app"`, target=`"X.Y"` 등), 동적 import (`importlib.import_module`) 모두 grep |
| DTO·모델 타입 변경 | mapper / cache serializer / 템플릿 / inline JS / view_models 체인 — 한 곳 누락 시 cache 역직렬화 또는 attribute access 깨짐 |
| 동시성 코드 (consumer / 핸들러) | placeholder는 `ON CONFLICT DO NOTHING` 의무 (`DO UPDATE`는 진짜 데이터에만). race 시나리오 명시 검증 |
| Frontend JS | 외부 `.js` 파일에서 작업 (inline 신규 금지). 변환 후 `node --check` + 사용자 IDE에서 경고 0건 |

### 누적 사고 패턴 (반면교사)

- sed `^from` 패턴이 함수 안 들여쓰기 import 놓침 → `^[[:space:]]*from` 또는 별도 grep 라운드.
- sed가 함수-local 변수(예: `globalRange→capturedRange`)를 함수 외부까지 변환 → awk로 함수 경계 마킹 후 사용 위치 검증.
- 문자열 형태 모듈 경로 (`uvicorn.run("web.main:app")`) 잔존 → import 변환 후 `grep '"[a-z_.]*:'` 별도 라운드.
- placeholder upsert(`ON CONFLICT DO UPDATE`)가 진짜 inventory 덮어쓰는 race → placeholder 전용 메서드는 `ON CONFLICT DO NOTHING` + 충돌 시 다시 find.
- inline JS 변경은 도구 적용 어려움 → 외부 `.js`로 옮긴 후 변경.

누락 시 사용자 회귀 사고 발견의 책임은 검증 누락에 있음. 같은 패턴 재발 시 본 절 "누적 사고 패턴"에 추가하고 검증 절차에 누락된 단계 보강.