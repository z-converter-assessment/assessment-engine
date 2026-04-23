# CLAUDE.md

## 프로젝트 개요
ZConverter Cloud Assessment Portal.
온프레미스 서버 인벤토리를 수집·저장하는 B2B 내부 포털.

### 배포 시나리오
고객사 네트워크 내에 서버 엔진(web + consumer + MQ + DB)이 설치된다.
네트워크 내 각 서버에는 **C99/C++03 기반 에이전트**가 탑재되어 메트릭을 수집하고,
HTTP를 거치지 않고 **MQ에 직접 발행**한다. Consumer가 이를 소비해 DB에 저장한다.

## 디렉토리 구조
```
(git root = PyCharm 프로젝트 루트 = .venv 위치)
config.py              # WebSettings, ConsumerSettings(WebSettings)
db/                    # 데이터 접근 계층 (web/consumer 공유)
  session.py           # SQLAlchemy 엔진, 세션 (WebSettings() 직접 생성)
  models/
    base.py
    server_entity.py
    metric_snapshot.py
  repositories/
    dto.py             # dataclass 출력 모델 (ServerDTO, ServerMetricDTO)
    i_collect_repository.py
    i_query_repository.py
    collect_repository.py
    query_repository.py
web/                   # FastAPI 서비스 (python -m web)
  __main__.py          # 진입점
  app.py               # FastAPI 앱, lifespan (테이블 auto-create)
  deps.py              # DI 조립 (get_service)
  api/
    router.py          # Jinja2 SSR 라우터
    items.py           # 뷰 전용 dataclass (ServerItem, MetricHistoryItem 등)
  services/
    query_service.py   # QueryService — 조회 + 뷰 모델 변환
  templates/
    base.html
    servers/
      list.html        # GET /servers/
      detail.html      # GET /servers/{id}
      history.html     # GET /servers/{id}/history
consumer/              # Consumer 서비스 (python -m consumer)
  __main__.py          # 진입점
  app.py               # MQ 연결 + run() (ConsumerSettings() 직접 생성)
  deps.py              # 의존성 조립 (세션 + 레포 → handler)
  handler.py           # 메시지 파싱 + DB 저장 로직
  schemas.py           # 에이전트 MQ 메시지 스키마 (ServerMetricInput 등)
tests/                 # pytest (unit + integration)
Dockerfile
docker-compose.yml
pyproject.toml
```

## 모듈 의존 관계
```
# 공유 계층 (db/)
db/repositories/dto        ← 아무것도 모름
db/repositories/i_collect  ← 아무것도 모름
db/repositories/i_query    ← db/repositories/dto만 앎
db/repositories/collect    ← db/models + i_collect
db/repositories/query      ← db/models + db/repositories/dto + i_query

# web 모듈 (python -m web)
web/api/items              ← 아무것도 모름
web/services               ← i_query + db/repositories/dto + web/api/items
web/deps                   ← db/repositories/query + web/services (조립만)
web/api/router             ← web/deps + web/services만 앎
web/app                    ← web/api/router
web/__main__               ← web/app (진입점)

# consumer 모듈 (python -m consumer)
consumer/schemas           ← pydantic (ipaddress, pydantic만)
consumer/handler           ← consumer/schemas + db/repositories/i_collect
consumer/deps              ← db/repositories/collect + db/session + consumer/handler
consumer/app               ← ConsumerSettings + consumer/deps
consumer/__main__          ← consumer/app (진입점)
```

## 설정 (config.py)

모듈 레벨 인스턴스 없음. 각 진입점에서 직접 생성.

```python
from pydantic_settings import BaseSettings

class WebSettings(BaseSettings):  # postgres 접속 정보
    ...

    @property
    def database_url(self) -> str: ...


class ConsumerSettings(WebSettings):  # postgres + RabbitMQ 접속 정보
    ...

    @property
    def broker_url(self) -> str: ...
```

- `db/session.py`: `WebSettings().database_url` 로 엔진 생성
- `consumer/app.py`: `ConsumerSettings()` 로컬 생성 후 사용
- web 서비스 컨테이너에는 RabbitMQ 환경변수 미주입 → `ConsumerSettings()`를 web에서 import하면 ValidationError

## 테이블 구조

### servers
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | integer | PK, autoincrement |
| hostname | String(255) | unique, not null |
| created_at | timestamp | not null |
| updated_at | timestamp | not null |

### metric_snapshots
| 컬럼 | 타입 | 제약 |
|------|------|------|
| id | integer | PK, autoincrement |
| server_id | integer | FK → servers.id, not null |
| recorded_at | timestamp | not null |
| nproc | integer | not null |
| mem_total_mb | bigint | not null |
| disks | JSONB | not null |
| ip_internal | JSONB | not null |
| ip_external | JSONB | not null |
| created_at | timestamp | not null |

## ORM 설계 원칙
- 타임스탬프는 `server_default=func.now()` 사용
- autoincrement PK는 flush 후 ORM 인스턴스에 자동 반영됨 (refresh 불필요)
- server_default 컬럼(created_at 등)을 읽어야 할 때만 flush 후 refresh 호출
- `updated_at` 갱신은 ORM 필드 직접 수정 (`datetime.now(timezone.utc)`)
- disks, ip_internal, ip_external은 별도 테이블 없이 JSONB로 관리
- DTO(dataclass)와 ORM 모델은 분리, 변환은 repository 구현체 책임
- 읽기/쓰기 레포지토리 분리: CollectRepository(쓰기), QueryRepository(읽기)

## Consumer 설계 원칙
- aio-pika 기반 순수 비동기 컨슈머 (FastAPI와 독립 프로세스)
- 단일 이벤트 루프 유지 → 커넥션 풀 정상 사용 (NullPool 불필요)
- FastAPI와 동일한 세션(`session.py`) 공유
- 파싱 실패: nack without requeue
- DB 실패: 지수 백오프 3회 재시도 후 raise → aio-pika requeue

## Query API
```
GET /servers/                    # 서버 목록 (HTML)
GET /servers/{server_id}         # 서버 최신 데이터 (HTML)
GET /servers/{server_id}/history # 서버 시계열 전체 (HTML)
GET /health                      # 헬스체크 (JSON)
```
- 라우터는 QueryService만 참조
- disks 표시 시 `size == "0B"` 항목 필터링 (`_real_disks()`)

## 메시지 수신 (agent → MQ → consumer)
에이전트는 HTTP API를 거치지 않고 MQ에 직접 발행한다. `api/ingestion.py`는 존재하지 않는다.

### 메시지 페이로드
```json
{
  "hostname": "server01",
  "nproc": 4,
  "mem_total_mb": 16384,
  "disks": [{ "name": "vda", "size": "30G" }],
  "ip": { "internal": ["10.0.0.1"], "external": ["1.2.3.4"] }
}
```

## 실행 환경
- 개발 머신: MacBook Air (Apple Silicon)
- 메인 스택: docker compose (web, consumer, rabbitmq, postgres)
- 실제 에이전트: C99/C++03 바이너리, 고객사 네트워크 내 각 서버에 배포
- 테스트 시뮬레이터: `tools/agent/` (MQ 직접 발행 검증용, 별도 docker-compose)
- 시뮬레이터 → 메인 스택 통신: host.docker.internal 경유

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