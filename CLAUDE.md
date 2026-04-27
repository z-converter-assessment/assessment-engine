# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
고객사 내부 네트워크의 각 호스트 인벤토리를 수집·저장하는 B2B 내부 포털.

### 배포 시나리오
고객사 네트워크 내에 서버 엔진(web + consumer + scheduler + MQ + DB)이 설치된다.
각 서버의 **C99/C++03 기반 에이전트**가 메트릭을 수집해 MQ에 직접 발행하고, Consumer가 소비해 DB에 저장한다.

## ORM 모델

| 모델 | 테이블 | PK 타입 | 설명 |
|------|--------|---------|------|
| ServerInventory | server_inventory | Integer | 기본 인벤토리. machine_id 기준 upsert |
| ServerMetrics | server_metrics | BigInteger | 시계열 메트릭. TimescaleDB hypertable |
| ServerStorageDetail | server_storage_detail | Integer | 2차 스토리지 상세 (파티션·LVM·파일시스템) |
| ServerNetworkDetail | server_network_detail | Integer | 2차 네트워크 상세 (인터페이스·라우팅·DNS) |

- 대리키(surrogate key) 패턴 — 내부 참조는 정수 PK, 비즈니스 식별자는 unique 제약
- `server_metrics`만 BigInteger — 시계열 무한 누적 대비

## 데이터 흐름 설계

- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- Inbound DTO: `XxxCreate` (`db/repositories/dto.py`) — Consumer → Repository
- Outbound DTO: `XxxResponse` (`db/repositories/dto.py`) — Repository → Service
- ViewModel: `web/view_models.py` — Service → Router (Jinja2 컨텍스트)
- Consumer 파싱: Pydantic `AgentMessage` discriminated union → routing key별 핸들러에서 구체 타입 사용
- inventory upsert 기준: `machine_id` / metrics 서버 조회 기준: `hostname` (미등록 서버면 drop)

## Consumer 설계

- aio-pika 기반 순수 비동기 컨슈머 (FastAPI와 독립 프로세스)
- 파싱 실패: raise → nack(requeue=False) → DLX → DLQ
- DB 실패: 지수 백오프(`2^attempt` s) 3회 재시도 후 raise → nack → DLQ
- TTL 만료(60초): 브로커가 자동으로 DLX → DLQ
- `error` 메시지: 파싱 후 로깅만. DB 저장 없음

### MQ 토폴로지
Exchange: `assessment` (topic, durable) / DLX: `assessment.dlx` (direct, durable)

| routing key | 큐 | DLQ |
|-------------|-----|-----|
| `server.inventory` | `server.inventory` | `server.inventory.dead` |
| `server.metrics` | `server.metrics` | `server.metrics.dead` |
| `server.error` | `server.error` | `server.error.dead` |

## Scheduler 설계

- `scheduler/` — 독립 프로세스. `scheduler_interval_seconds`(기본 3600s) 간격으로 diagnostics 실행
- 현재 `run_diagnostics()`는 미구현 (`NotImplementedError`)

## Query API 설계

### 라우터 구조
| 모듈 | 변수 | 접두사 | 응답 |
|------|------|--------|------|
| `web/routers/pages.py` | `pages_router` | `/servers` | HTML (Jinja2) |
| `web/routers/api.py` | `api_router` | `/api/v1/servers` | JSON |

### SSR 페이지 엔드포인트
| 경로 | 템플릿 |
|------|--------|
| GET /servers/ | servers/list.html |
| GET /servers/{server_id} | servers/detail.html |
| GET /servers/{server_id}/storage | servers/storage.html |
| GET /servers/{server_id}/network | servers/network.html |

### API 엔드포인트 (AJAX)
| 경로 | 설명 |
|------|------|
| GET /api/v1/servers/{server_id}/collection-status | 수집 상태 뱃지 |
| GET /api/v1/servers/{server_id}/metrics/latest | 최신 메트릭 |
| GET /api/v1/servers/{server_id}/metrics/snapshots | 히스토리 (커서 페이지네이션) |
| GET /api/v1/servers/{server_id}/metrics/chart | 차트 시계열 (time_bucket) |

### 메트릭 차트 쿼리 파라미터
- `metric_type`: cpu.usage_percent / disk.read_iops / fs.usage_percent / net.rx_bytes_per_sec 등
- `dimension`: 복수 인스턴스 메트릭의 특정 대상 (장치명·mountpoint·NIC명)
- `time_range`: 15m / 1h / 6h / 24h / 7d (기본 1h)
- `bucket`: 5m / 1h / 1d
- `agg`: avg / max / p95

## Redis 전략

### 키 설계
| 용도 | 키 | TTL |
|------|----|-----|
| 인벤토리 캐시 | `cache:inventory:{server_id}` | eviction |
| 메트릭 캐시 | `cache:metrics:{server_id}` | eviction |
| 멱등성 | `idempotent:{message_id}` | 24h |
| 온라인 TTL | `online:{server_id}` | 90s |
| 인증 토큰 | `token:{token}` | 1h |

- PUB/SUB 채널: `metrics.events` (consumer 발행 → web 구독)
- eviction 정책: `volatile-lru`, maxmemory 256mb
- 의존성 주입: web은 `web/deps.py`의 `get_redis_client()`, consumer는 `db/redis.py`의 `get_redis()` 직접 호출

## TimescaleDB
- `server_metrics` hypertable (`collected_at` 기준 파티셔닝)
- **개발**: 기동 시 `DROP SCHEMA public CASCADE` → 재생성 → `CREATE EXTENSION` → `create_hypertable`
- **프로덕션**: Alembic 마이그레이션. `create_hypertable`은 최초 생성 마이그레이션에 수동 작성

## 환경변수
루트 `.env`에서 주입. 키 목록은 `.env.example` 참조.
docker-compose `environment`에는 서비스명 오버라이드(`POSTGRES_HOST`, `RABBITMQ_HOST`, `REDIS_HOST`)와 이미지 요구 변수명 매핑만 명시.

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
