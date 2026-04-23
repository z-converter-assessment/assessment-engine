## 디렉토리 구조
```
(git root = PyCharm 프로젝트 루트 = .venv 위치)
config.py              # WebSettings, ConsumerSettings(WebSettings)
db/                    # 데이터 접근 계층 (web/consumer 공유)
  session.py           # SQLAlchemy 엔진, 세션
  models/
    base.py
    server_entity.py
    metric_snapshot.py
  repositories/
    base_collect_repository.py
    base_query_repository.py
    collect_repository.py
    query_repository.py
web/                   # FastAPI 서비스 (python -m web)
  __main__.py          # 진입점
  main.py              # FastAPI 앱, lifespan (테이블 auto-create)
  deps.py              # DI 조립 (get_service)
  api/
    router.py          # Jinja2 SSR 라우터
    view_models.py           # 뷰 전용 dataclass (ServerItem, MetricHistoryItem 등)
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
  main.py              # MQ 연결 + main() (ConsumerSettings() 직접 생성)
  deps.py              # 의존성 조립 (세션 + 레포 → handler)
  handler.py           # 메시지 파싱 + DB 저장 로직
  schemas.py           # 에이전트 MQ 메시지 스키마 (MessageBase, InventoryInput, MetricsInput, ErrorInput)
tests/                 # pytest (unit + integration)
_archive/              # 현재 미사용 코드 (언더스코어 = 비활성 관례)
  agent/               # C99/C++03 에이전트 프로토타입 (통합 전)
docs/                  # 참고 문서 (코드와 무관한 설계·계약 자료)
  payload-schema.md          # 에이전트 → MQ 메시지 페이로드 규격
  schema-change-proposal.md  # 페이로드 변경 제안 (검토 중)
  derived-metrics-reference.md # raw 수집값 → 분석 지표 연산 정의
Dockerfile
docker-compose.yml
pyproject.toml
```

## 모듈 의존 관계
```
# 공유 계층 (db/)
db/repositories/base_collect  ← 아무것도 모름 (TYPE_CHECKING으로 consumer/schemas 참조)
db/repositories/base_query    ← 아무것도 모름
db/repositories/collect    ← db/models + base_collect
db/repositories/query      ← db/models + base_query

# web 모듈 (python -m web)
web/api/items              ← 아무것도 모름
web/services               ← base_query + web/api/model_views
web/deps                   ← db/repositories/query + web/services (조립만)
web/api/router             ← web/deps + web/services만 앎
web/main                   ← web/api/router
web/__main__               ← config(WebSettings) + web/main (진입점)

# consumer 모듈 (python -m consumer)
consumer/schemas           ← pydantic
consumer/handler           ← consumer/schemas + db/repositories/base_collect
consumer/deps              ← db/repositories/collect + db/session + consumer/handler
consumer/main              ← config(ConsumerSettings) + consumer/deps
consumer/__main__          ← consumer/main (진입점)
```