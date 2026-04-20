# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

## 디렉토리 구조
```
backend/app/
  broker.py              # taskiq AioPikaBroker
  config.py              # pydantic-settings (Settings)
  session.py             # SQLAlchemy 엔진, 세션
  main.py                # FastAPI 앱, lifespan
  dto/
    inbound.py           # pydantic 태스크 입력 모델 (ServerMetricInput 등)
    outbound.py          # dataclass 레포지토리 출력 모델 (ServerDTO, ServerMetricDTO)
  models/
    base.py              # SQLAlchemy Base
    server_entity.py     # ServerEntity ORM (servers 테이블)
    metric_snapshot.py   # MetricSnapshot ORM (metric_snapshots 테이블)
  repositories/
    i_read_repository.py   # IReadRepository (ABC)
    i_write_repository.py  # IWriteRepository (ABC)
    read_repository.py     # 읽기 구현체
    write_repository.py    # 쓰기 구현체
  api/
    deps.py              # DI 조립
    ingestion.py         # 수신/검증 후 process_metric.kiq()
    query.py             # Jinja2 SSR 렌더링
    views.py             # 뷰 전용 dataclass (ServerItem, MetricHistoryItem 등)
  services/
    query_service.py     # QueryService — 조회 + 뷰 모델 변환
  workers/
    tasks.py             # process_metric 태스크
  templates/
    base.html
    servers/
      list.html          # GET /servers/
      detail.html        # GET /servers/{id}
      history.html       # GET /servers/{id}/history

tools/agent/             # 별도 컨테이너, push.sh 기반 (테스트용)
```

## 모듈 의존 관계
```
dto/inbound, dto/outbound  ← 아무것도 모름
api/views                  ← 아무것도 모름

repositories/i_*           ← dto/outbound만 앎
repositories/read          ← ORM + dto/outbound + i_read
repositories/write         ← ORM + dto/outbound + i_write

services/query_service     ← i_read + api/views
api/deps                   ← repositories + services (조립만)
api/query                  ← deps + query_service만 앎
api/ingestion              ← broker + dto/inbound + workers/tasks

workers/tasks              ← broker + dto/inbound + session + write_repository
```

## 테이블 구조

### servers
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | UUID | PK |
| hostname | String(255) | unique, not null |
| created_at | timestamp | not null |
| updated_at | timestamp | not null |

### metric_snapshots
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | UUID | PK |
| server_id | UUID | FK → servers.id, not null |
| recorded_at | timestamp | not null |
| nproc | integer | not null |
| mem_total_mb | bigint | not null |
| disks | JSONB | not null |
| ip_internal | JSONB | not null |
| ip_external | JSONB | not null |
| created_at | timestamp | not null |

## ORM 설계 원칙
- 타임스탬프는 `server_default=func.now()` 사용. flush 후 반드시 `await session.refresh(orm)` 호출
- disks, ip_internal, ip_external은 별도 테이블 없이 JSONB로 관리
- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- 읽기/쓰기 레포지토리 분리: ReadRepository, WriteRepository

## Worker 설계 원칙
- taskiq + aio-pika 기반 순수 비동기 워커
- 단일 이벤트 루프 유지 → 커넥션 풀 정상 사용 (NullPool 불필요)
- FastAPI와 동일한 세션(`session.py`) 공유

## Query API
```
GET /servers/                    # 서버 목록 (HTML)
GET /servers/{server_id}         # 서버 최신 데이터 (HTML)
GET /servers/{server_id}/history # 서버 시계열 전체 (HTML)
```
- 라우터는 QueryService만 참조
- disks 표시 시 `size == "0B"` 항목 필터링 (`_real_disks()`)

## Ingestion API
```
POST /ingest/   # 에이전트 데이터 수신 → RabbitMQ 발행
```

## 수신 페이로드 (agent → ingestion)
```json
{
  "hostname": "server01",
  "nproc": "4",
  "mem_total_mb": 16384,
  "disks": [{ "name": "vda", "size": "30G" }],
  "ip": { "internal": ["10.0.0.1"], "external": ["1.2.3.4"] }
}
```

## 실행 환경
- 개발 머신: MacBook Air (Apple Silicon)
- 메인 스택: docker-compose (backend, worker, rabbitmq, postgres)
- 에이전트: 별도 docker-compose (tools/agent/)
- 에이전트 → 메인 스택 통신: host.docker.internal 경유

## 환경변수
루트 `.env`에서 주입. 키 목록은 `.env.example` 참조.

## 브랜치 전략
| 브랜치 | 용도 |
|--------|------|
| main | 배포용. 직접 push 금지 |
| develop | 개발 통합. PR로만 머지 |
| feature/xxx | 기능 개발 |
| fix/xxx | 버그 수정 |
| chore/xxx | 설정 변경 |

## 커밋 컨벤션
| 타입 | 설명 |
|------|------|
| feat | 새로운 기능 추가 |
| fix | 버그 수정 |
| chore | 설정, 패키지 변경 |
| refactor | 리팩토링 |
| test | 테스트 코드 |