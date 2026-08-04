# Web 레이어 원칙·DI·식별자

정책: CLAUDE.md #E1 (P1~P4) · #E4 (URL 식별자) · #F3 (검증 단일 경로) · #F4 (인터페이스 우선). 본 문서는 web 컴포넌트 구현 메커니즘 단일 진실.

## 데이터 흐름

```
Browser -> Router -> deps.get_service -> QueryService
                                              |
                                     +--------+--------+
                                     v                 v
                                Redis cache        Repository
                                                       |
                                                       v
                                               OutboundDTO (raw)
                                                       |
                                                       v
                                               mapper -> ViewModel
                                                       |
                                                       v
                                               Template / JSON

Browser -> Router -> deps.get_task_service -> TaskService -> broker_channel (task.install)
```


## 의존성 주입 (deps.py — composition root)

3 service 모두 `deps.py` 에서 protocol 만 받도록 주입 — web 쪽 구현 import 는 본 모듈 1곳이다 (consumer·worker 는 각자 진입점, #F4).

| 헬퍼 | 반환 | 주입 인자 |
|------|------|-----------|
| `get_service(db, redis)` | `QueryService` | `QueryRepository` (SqlQueryRepository) + redis |
| `get_task_service(request, db, redis)` | `TaskService` | query_repo + session_factory + `CollectRepository` factory + broker_channel + zdm_resolver + redis |
| `get_diagnostic_service()` | `DiagnosticService` (web 이 진단 발행·조회를 단일 진입점으로 쓰는 facade — 보고서 발행 enqueue·이력·워커 lifecycle) | session_factory + `DiagnosticRepository` factory |
| `resolve_internal_id(server_id, service)` | `int` | path UUID → 정수 PK + 422/404 자동 |

설계 결정:
- `query_repo`는 request-scoped(`get_db`) — 한 요청 안 다중 read에 동일 트랜잭션
- `collect_repo`·`diagnostic_repo`는 별도 트랜잭션 필요라 `session_factory` + factory 패턴 — service가 트랜잭션 경계 자체 관리. 서버별 독립 commit(task INSERT 실패 1건이 다른 서버 commit에 영향 X). `DiagnosticService`는 request-scoped 세션 미의존이라 워커가 DI 없이 동일 인스턴스 구성 가능
- `broker_channel`은 lifespan에서 `app.state.broker_channel`에 저장한 영속 channel 재사용 — `TaskService`(install task 발행)가 받아 매 발행마다 connection open/close 안 함 (오버헤드 0). `DiagnosticService`는 보고서를 DB(`diagnostic_jobs`)로 발행·생성(전용 워커 프로세스)이라 broker 미사용
- 라우터는 `deps.py` 의 `*Dep` 별칭만 받는다 (형식은 `docs/guides/conventions.md` 3절). 구현 import 금지.

## URL 식별자 — public_id (UUID)

정책: CLAUDE.md #E4 (정수 PK 노출 금지). 본 절은 구현 메커니즘만.

- 라우터 path `{server_id}` 타입을 `UUID`로 선언 -> invalid 형식 422 자동
- 형식 OK + DB 미존재 -> 404 (`resolve_internal_id`)
- `QueryService.resolve_server_id(public_id) -> int | None` — read-through 캐시 (`cache:resolve:{public_id}`, TTL 없음 — 불변)
- 라우터는 `internal_id: ServerIdDep` 으로 받는다 -> 422/404 자동

## SSR + AJAX 하이브리드 (설계 결정)

페이지 자체는 SSR로 즉시 first paint(`서버 목록`, `상세`, `보고서`). 차트는 페이지 로드 후 AJAX(`/api/servers/{id}/metrics/chart`), 실시간 메트릭은 polling(`/metrics/latest`)으로 갱신.

근거: SPA 도구 미도입 → 빠른 시연·운영. 동적 영역만 JS로 격리 — `static/js/pages/{page}.js` 외부 파일 (CLAUDE.md F5 "Frontend JS 외부화 의무").
