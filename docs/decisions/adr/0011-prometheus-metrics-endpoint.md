# ADR 0011 — Prometheus metrics endpoint 도입 (web 한정)

상태: Withdrawn (2026-06-08, 원 Accepted 2026-05-16)

정정 (2026-06-08): 본 ADR 철회 — Prometheus metrics endpoint(`/metrics`·prometheus-fastapi-instrumentator)를 본 프로젝트에서 미적용으로 결정. 코드(`web/main.py` instrument)·의존성(pyproject·uv.lock)·관측 문서에서 제거. 관측은 `LOG_FORMAT=json` 구조화 로그 단독. 아래 본문은 당시 결정 기록.

## Context

본 repo는 CD가 아닌 CI까지 + 배포 contract만 인프라에 제공하는 범위(CLAUDE.md #A0). 관측 영역에서 본 repo가 제공해야 할 contract는:

- 데이터 출력 (format·endpoint) — 본 repo 책임
- 데이터 수집·저장·시각화 (Prometheus 서버·Grafana·alerting) — 인프라 책임

로그(`docs/reference/observability.md`)는 stdout 평문으로 이미 출력 — JSON 토글은 별도 ADR 또는 결정. 다만 metrics 출력은 본 repo가 노출하지 않으면 인프라가 어떤 stack을 쓰든 application-level metrics 수집 불가.

운영 단계 가정:
- 본 repo는 단일 worker 인스턴스(web·consumer·diagnostic-worker 각 1). HPA 같은 자동 확장 대상 아님. ADR 0023: scheduler cron 폐기로 4 → 3.
- prod 인프라 결정(Prometheus stack 채택 여부 포함)은 본 repo 범위 밖. 다만 본 repo는 "Prometheus 호환 endpoint를 제공한다"는 contract만 충족하면 인프라 측이 자유롭게 결정 가능.

## Decision

`prometheus-fastapi-instrumentator>=7.0` 의존성 추가 후 web 컨테이너에 `GET /metrics` endpoint 노출.

구현:
- `src/assessment_engine/web/main.py`에서 `Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)` — middleware 등록 시점에 모든 라우터 자동 계측.
- `include_in_schema=False` — OpenAPI(swagger) 문서에서 제외. metrics는 운영 endpoint라 API 카탈로그에 노출하지 않음.

노출 metrics:
- `http_request_duration_seconds` (histogram) — endpoint·method·status_code 라벨
- `http_requests_total` (counter) — endpoint·method·status_code 라벨
- `process_*` (Python 런타임 자동 — CPU·memory·GC·thread count)

worker(consumer/diagnostic-worker)는 HTTP server 없음 → 본 ADR 범위 밖. 대안 관측 source:
- broker 측 큐 길이·소비 ack rate — RabbitMQ management API
- DB·Redis 측 connection pool 상태 — 각 서비스 native metrics

## Options Considered

1. prometheus-fastapi-instrumentator (채택)
   - 장점: FastAPI 표준·widely-used·낮은 학습 비용. middleware 자동 계측 — 라우터·서비스 코드 변경 0.
   - 단점: 라이브러리 추가 의존성 1개.

2. prometheus_client 직접 사용
   - 장점: 의존성 최소·full control.
   - 단점: HTTP middleware 계측 코드 직접 작성 — 보일러플레이트 증가. instrumentator가 wrap하는 패턴 그대로 작성하게 됨.

3. 도입 보류 (현 상태 유지)
   - 장점: 의존성 0개.
   - 단점: 인프라가 본 repo의 application-level metrics 수집 불가. 큐 길이만으로 운영 가시성 부족.

옵션 1 채택 — Python·FastAPI 생태계 표준 + 운영 가시성 contract 즉시 제공.

## Consequences

장점:
- 인프라가 본 repo의 application metrics(HTTP traffic·latency·error rate·Python 런타임)를 즉시 수집 가능.
- middleware 자동 — 신규 라우터 추가 시 추가 계측 코드 0.
- 외부 stack 변경(Datadog·New Relic 등)에도 그대로 동작(OpenMetrics format은 Datadog Agent 등도 수집 지원).

단점·한계:
- prod에서 `/metrics`는 외부 노출 금지. 인증·인가 없는 endpoint → 외부 공개 시 환경 메타데이터 leak. reverse proxy(nginx·istio 등)에서 internal-only 라우트로 차단 의무 — 인프라 책임.
- consumer/worker metrics 미적용. 향후 워커 수평 확장 시 필요할 수 있음 — 별도 ADR.
- 의존성 1개 추가 — 단순 lib(prometheus-fastapi-instrumentator·prometheus-client)이지만 의존성 카탈로그에 박힘.

## 관련 문서·코드

- `docs/reference/observability.md` "Prometheus metrics endpoint" 절 — 운영 contract·구현 위치
- `src/assessment_engine/web/main.py` — Instrumentator 등록 위치
- `pyproject.toml` dependencies — `prometheus-fastapi-instrumentator>=7.0`
- CLAUDE.md #A0 — CI vs CD 책임 분리 원칙
- CLAUDE.md #F7 — 로깅 정책 (관측 보완 source)
