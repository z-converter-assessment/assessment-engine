# Web 레이어 원칙·DI·식별자

원칙은 CLAUDE.md E1 (P1~P5) + F4 (인터페이스 우선) 단일 진실. 본 문서는 web 컴포넌트 구현 디테일.

## 데이터 흐름

```
Browser → Router → deps.get_service → QueryService
                                          v
                          ┌───────────────┼───────────────┐
                          ▼               ▼               ▼
                       Redis cache    Repository       PUB/SUB
                                          v
                                   OutboundDTO (raw)
                                          v
                                   mapper → ViewModel
                                          v
                                   Template / JSON
```

- DTO - ORM 분리 — 변환은 repository 책임
- inventory upsert·metrics 저장·server_id 조회 모두 `machine_id` 기준
- `last_seen_at`은 `ServerDetail`에만 포함. 목록은 Redis `online:{id}` TTL
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드

## 의존성 주입 (deps.py — composition root)

```python
def get_service(db, redis) -> QueryService:
    return QueryService(QueryRepository(db), redis)

def get_task_service(db, redis) -> TaskService:
    return TaskService(QueryRepository(db), AsyncSessionLocal, CollectRepository, redis)
```

서비스는 추상(`BaseQueryRepository`/`BaseCollectRepository`)만 받음. 구체 import는 `deps.py` 1곳. 라우터는 `Depends(get_*_service)` 주입만.

`TaskService`는 task INSERT용 별도 트랜잭션 필요(서버별 독립 commit) → `session_factory` + `repo_factory` 주입 패턴.

## URL 식별자 — public_id (UUID)

정책 단일 진실: CLAUDE.md #E5 (정수 PK 노출 금지). 본 절은 구현 메커니즘만.

- 라우터 path `{server_id}` 타입을 `UUID`로 선언 -> invalid 형식 422 자동
- 형식 OK + DB 미존재 -> 404 (`resolve_internal_id` Depends)
- `QueryService.resolve_server_id(public_id) -> int | None` — read-through 캐시 (`cache:resolve:{public_id}`, TTL 없음 — 불변)
- 라우터에서 `internal_id: int = Depends(resolve_internal_id)` 주입 -> 422/404 자동

## 검증의 단일 경로

| 입력 | 검증 위치 |
|------|-----------|
| HTTP query string | 라우터 `Query(MetricType/TimeRange/...)` Literal Pydantic |
| HTTP path UUID | `resolve_internal_id` Depends — 422/404 자동 |
| HTTP body JSON | 라우터 Pydantic `BaseModel` (`InstallRequest`, `ProbeRequest`, `InventoryExportRequest`) |

Service에서 재검증 금지 (`_VALID_*` frozenset 비교 같은 패턴 안 만든다).

## SSR + AJAX 하이브리드 (설계 결정)

페이지 자체는 SSR로 즉시 first paint(`서버 목록`, `상세`, `보고서`). 차트·실시간 메트릭은 페이지 로드 후 AJAX(`/api/v1/servers/{id}/metrics/chart`) 또는 SSE(`/metrics/stream`)로 갱신.

근거: SPA 도구 미도입 → 빠른 시연·운영. 동적 영역만 JS로 격리 — `static/js/pages/{page}.js` 외부 파일 (CLAUDE.md F9 "Frontend JS 외부화 의무").
