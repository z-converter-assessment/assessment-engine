# Web 레이어 원칙·DI·식별자

정책: CLAUDE.md #E1 (P1~P4) · #E4 (URL 식별자) · #F3 (검증 단일 경로) · #F4 (인터페이스 우선). 본 문서는 web 컴포넌트 구현 메커니즘 단일 진실.

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
- inventory upsert·metrics 저장·server_id 조회 모두 `agent_id` 단일 키 기준 (#C1)
- `last_seen_at`은 `ServerDetail`에만 포함. 목록은 Redis `online:{id}` TTL
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드

## 의존성 주입 (deps.py — composition root)

3 service 모두 `deps.py`에서 추상 인터페이스만 받도록 주입 — 구체 구현체 import는 본 모듈 1곳 (#F4).

| 헬퍼 | 반환 | 주입 인자 |
|------|------|-----------|
| `get_service(db, redis)` | `QueryService` | `BaseQueryRepository` (QueryRepository) + redis |
| `get_task_service(db, redis)` | `TaskService` | query_repo + session_factory + `BaseCollectRepository` factory + redis |
| `get_diagnostic_service(db)` | `DiagnosticService` (web 이 진단 발행·조회를 단일 진입점으로 쓰는 facade — 보고서 발행·이력) | query_repo + session_factory + `BaseDiagnosticRepository` factory |
| `resolve_internal_id(server_id, service)` | `int` | path UUID → 정수 PK + 422/404 자동 |

설계 결정:
- `query_repo`는 request-scoped(`get_db`) — 한 요청 안 다중 read에 동일 트랜잭션
- `collect_repo`·`diagnostic_repo`는 별도 트랜잭션 필요라 `session_factory` + factory 패턴 — service가 트랜잭션 경계 자체 관리. 서버별 독립 commit(task INSERT 실패 1건이 다른 서버 commit에 영향 X)
- `broker_channel`은 lifespan에서 `app.state.broker_channel`에 저장한 영속 channel 재사용 — `TaskService`(install task 발행)가 받아 매 발행마다 connection open/close 안 함 (오버헤드 0). `DiagnosticService`는 보고서를 DB(`diagnostic_jobs`)로 발행·생성(web 내부 job-claim 워커)이라 broker 미사용
- 라우터는 `Depends(get_*_service)` 주입만. 구체 import 금지.

## URL 식별자 — public_id (UUID)

정책: CLAUDE.md #E4 (정수 PK 노출 금지). 본 절은 구현 메커니즘만.

- 라우터 path `{server_id}` 타입을 `UUID`로 선언 -> invalid 형식 422 자동
- 형식 OK + DB 미존재 -> 404 (`resolve_internal_id` Depends)
- `QueryService.resolve_server_id(public_id) -> int | None` — read-through 캐시 (`cache:resolve:{public_id}`, TTL 없음 — 불변)
- 라우터에서 `internal_id: int = Depends(resolve_internal_id)` 주입 -> 422/404 자동

## SSR + AJAX 하이브리드 (설계 결정)

페이지 자체는 SSR로 즉시 first paint(`서버 목록`, `상세`, `보고서`). 차트는 페이지 로드 후 AJAX(`/api/servers/{id}/metrics/chart`), 실시간 메트릭은 30초 polling(`/metrics/latest`)으로 갱신.

근거: SPA 도구 미도입 → 빠른 시연·운영. 동적 영역만 JS로 격리 — `static/js/pages/{page}.js` 외부 파일 (CLAUDE.md F5 "Frontend JS 외부화 의무").
