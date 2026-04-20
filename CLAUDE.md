# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

## 디렉토리 구조
```
backend/app/
  celery_app.py          # Celery 인스턴스 (공유 인프라)
  config.py
  main.py
  contracts/
    task_names.py        # 태스크명 상수 (ingestion ↔ worker 계약)
  domain/
    server.py            # 순수 도메인 모델 (ORM 무관)
  models/
    base.py              # SQLAlchemy Base
    database.py          # 엔진, 세션 (FastAPI용)
    server.py            # ORM 모델
  repositories/
    interface.py         # IServerRepository (ABC) — 도메인 모델 반환
    server.py            # 구현체 — ORM → 도메인 변환 책임
  api/
    deps.py              # DI 조립 (get_repo, get_service)
    ingestion.py         # 수신/검증 후 celery_app.send_task
    query.py             # Jinja2 SSR 렌더링, ServerService 참조
    schemas.py           # View 전용 dataclass (ServerListItem, ServerDetail 등)
  services/
    server.py            # ServerService — repo 호출 + 뷰 모델 변환
  workers/
    database.py          # NullPool 엔진 (Celery 전용)
    tasks.py             # process_metric 태스크
  templates/
    base.html
    servers/
      list.html          # GET /servers/
      detail.html        # GET /servers/{id}
      history.html       # GET /servers/{id}/history

tools/agent/             # 별도 컨테이너, push.sh 기반
```

## 모듈 의존 관계
```
domain/          ← 아무것도 모름
contracts/       ← 아무것도 모름
api/schemas      ← 아무것도 모름

repositories/interface  ← domain만 앎
repositories/server     ← ORM + domain + interface

services/server  ← interface + api/schemas
api/deps         ← interface + repositories/server + services/server (조립만)
api/query        ← deps + services/server만 앎
api/ingestion    ← celery_app + contracts만 앎

workers/tasks    ← celery_app + contracts + repositories/server + workers/database
```

## 테이블 구조

### servers
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | UUID | PK |
| hostname | String(255) | unique, not null |
| created_at | timestamp | not null |
| updated_at | timestamp | not null |

### server_metrics
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | UUID | PK |
| server_id | UUID | FK → servers.id |
| recorded_at | timestamp | not null |
| nproc | integer | |
| mem_total_mb | bigint | |
| disks | JSONB | |
| ip_internal | JSONB | |
| ip_external | JSONB | |
| created_at | timestamp | not null |

## ORM 설계 원칙
- 타임스탬프 컬럼은 각 모델에 직접 선언 (믹스인 사용 금지)
- 타임스탬프는 `server_default=func.now()` 사용. flush 후 반드시 `await session.refresh(orm)` 호출
- disks, ip_internal, ip_external은 별도 테이블 없이 JSONB로 관리
- 도메인 모델(dataclass)과 ORM 모델은 분리, 변환은 repository 구현체 책임

## Worker DB 설계 원칙
- Celery는 `asyncio.run()`을 태스크마다 호출 → 이벤트 루프가 매번 재생성됨
- 커넥션 풀은 루프 간 커넥션을 재사용하려다 충돌 → `workers/database.py`에서 `NullPool` 사용
- FastAPI용 엔진(`models/database.py`)과 Worker용 엔진(`workers/database.py`)은 분리

## Query API
```
GET /servers/                    # 서버 목록 (HTML)
GET /servers/{server_id}         # 서버 최신 데이터 (HTML)
GET /servers/{server_id}/history # 서버 시계열 전체 (HTML)
```
- 라우터는 ServerService만 참조 (IServerRepository 직접 참조 금지)
- 쓰기(INSERT/UPDATE) 쿼리 금지
- disks 표시 시 `size == "0B"` 항목 필터링 (services/server.py의 `_real_disks()`)

## Ingestion API
```
POST /ingest/   # 에이전트 데이터 수신 → RabbitMQ 발행
```

## 수신 페이로드 (agent → ingestion)
```json
{
  "hostname": "server01",
  "nproc": "4",
  "free": { "mem_total_mb": 16384 },
  "lsblk_raw": [{ "name": "vda", "size": "30G" }],
  "ip_raw": { "internal": ["10.0.0.1"], "external": ["1.2.3.4"] }
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