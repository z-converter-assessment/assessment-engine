# 관측 (Observability)

본 문서는 본 repo 가 제공하는 관측 contract 단일 진실. 두 절로 구분:

- 1 부 — 현재 활성 (로그 레벨·실패 매트릭스·log format·Prometheus metrics)
- 2 부 — 향후 확장 (Request/Correlation ID 분산 trace — 미구현, 도입 트리거·정석 패턴·ADR 의무)

정책 출처: CLAUDE.md #F7 (로깅) · #F6 (외부 의존 실패 모드).

---

# 1 부. 현재 활성

본 repo 가 즉시 제공하는 관측 channel. 운영자가 본 절 contract 만 충족하면 인프라 측 log aggregator·Prometheus stack 으로 indexing·alerting 가능.

## 로그 레벨 (CLAUDE.md #F7 단일 진실)

| 레벨 | 용도 |
|------|------|
| ERROR | 처리 실패 + 사용자/메시지 영향 (DB raise·DLQ·5xx) |
| WARNING | 정상 흐름이지만 운영 시그널 (시계 invariant 위반·재시작 burst·Redis fail-open·counter reset) |
| INFO | 상태 전이 (auto-register·schema bootstrap·consumer ready·DLQ enqueue) |
| DEBUG | 루프 내부·메시지별 흐름 — 운영 기본 비활성 |

원칙·금지·loguru 규약은 CLAUDE.md #F7.

## 외부 의존 실패 모드 매트릭스 (CLAUDE.md #F6 단일 진실)

| 외부 의존 | 실패 모드 | 처리 | 시그널 |
|-----------|-----------|------|--------|
| PostgreSQL | fail-close | `_db_retry` 백오프 후 raise → DLQ / 5xx | ERROR |
| RabbitMQ broker | fail-close | aio-pika 자동 재연결, persistent 메시지 | ERROR |
| Redis | fail-open | `safe_*` 흡수 (#C3) → 다음 계층 fallback | WARNING |
| HTTP 외부 호출 | fail-open | timeout → "unreachable" 결과 | INFO |

원칙·금지·예외 타입 catch 규약은 CLAUDE.md #F6.

## 로그 format toggle

stdout 로그 출력 format 을 `LOG_FORMAT` 환경변수로 토글.

- `text` (default) — loguru colorized 콘솔. dev grep·시연 가독성.
- `json` — loguru `serialize=True` 로 record 를 JSON 으로 변환. 외부 log aggregator (Loki·ELK·CloudWatch·Datadog 등) 가 `level`·`time`·`message`·`extra` 필드 자동 indexing → 검색·필터·alerting 가능.

```
              stdout 로그 (각 컨테이너)
                    v
              인프라 측 collector (Fluentbit·Promtail 등)
                    v
              log aggregator (Loki·ELK·CloudWatch·Datadog)
                    v
              indexed search·filter·alerting
```

구현: `src/assessment_engine/log_config.py` 의 `setup_logging(log_format)`. 각 entry (web/consumer/diagnostic-worker/diagnostic-scheduler) 가 Composition Root 에서 호출 (F4 단일 진실). `web_settings.log_format` · `consumer_settings.log_format` · `diagnostic_settings.log_format` 모두 동일 env 읽음.

운영 권장:
- dev: `LOG_FORMAT=text` — 사람이 직접 stream 을 보거나 grep 할 때 가독성 우선.
- prod: `LOG_FORMAT=json` — 외부 log aggregator 로 indexing·alerting. 평문 stdout grep 으로 충분한 시기에만 `text` 유지.

본 repo 책임 한계:
- 로그 format 출력만. log aggregator stack 선택·운영 (Loki·ELK 등) + collector (Fluentbit·Promtail) 배포는 인프라 책임 (CLAUDE.md #A0).

## Prometheus metrics endpoint

web 컨테이너가 `GET /metrics` 로 Prometheus 호환 metrics 노출. 외부 Prometheus (인프라 책임) 가 polling 수집 → Grafana 시각화·alerting.

```
Prometheus (인프라)              Web 컨테이너
   ├─ scrape_interval=15s  →  GET /metrics
   ├─ scrape_target=...           v
   └─ TSDB 저장                prometheus_fastapi_instrumentator
                                  v
                              HTTP request count·latency·error rate
                              (built-in Python process metrics — CPU·mem·GC)
```

구현: `web/main.py` 에서 `Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)`. middleware 등록 시점에 모든 라우터 자동 계측.

기본 노출 metrics:
- `http_request_duration_seconds` (histogram) — endpoint·method·status_code 라벨
- `http_requests_total` (counter) — endpoint·method·status_code 라벨
- `process_*` (process_cpu_seconds·process_resident_memory_bytes·process_start_time 등 — Python 런타임 자동)

본 repo 책임 한계:
- `/metrics` endpoint 노출만. Prometheus 서버·Grafana·alerting rule 은 인프라 책임 (CLAUDE.md #A0).
- prod 에서 `/metrics` 는 외부 노출 금지 — reverse proxy 에서 internal-only 라우트로 차단 권장. 인증·인가 없는 endpoint 라 외부 노출 시 환경 메타데이터 leak.
- consumer/diagnostic worker 는 HTTP server 없음 — 별도 `/metrics` 미노출. broker 측 큐 길이 (RabbitMQ management API) 로 대신 관측.

---

# 2 부. 향후 확장

현재 미구현. 운영 신호가 발견되면 ADR 의무 + 본 절 가이드 따라 도입.

## Request / Correlation ID 분산 trace

본 프로젝트 현재 미적용 — HTTP 측 `X-Request-ID` 없음, MQ `message_id` 는 멱등성 키로만 활용. 로그는 식별자 (machine_id·server_id) 별 grep 으로 trace.

### 정석 패턴 (도입 시)

HTTP 요청 진입 시 `X-Request-ID` 헤더 read 또는 신규 UUID 생성 → `contextvars` 로 보관 → 모든 로그에 자동 박힘 (loguru `logger.contextualize(request_id=...)`). MQ 메시지는 `message.message_id` 를 같은 contextvars 로 보관해 같은 흐름.

```
HTTP 요청 → middleware
              ├─ X-Request-ID 헤더 read (없으면 uuid4 생성)
              └─ contextvars.set(request_id)
                    v
         라우터 → service → repo
                    v
              loguru logger
                    v
         로그에 request_id=<uuid> 자동 박힘
                    v
         응답 헤더 X-Request-ID 에코
```

MQ 메시지:

```
aio-pika message 수신
    v
contextvars.set(request_id=message.message_id)
    v
handler → service → repo → loguru
    v
로그에 request_id=<message_id> 자동 박힘
```

### 구현 위치 (도입 시)

| 컴포넌트 | 위치 | 책임 |
|----------|------|------|
| HTTP middleware | `src/assessment_engine/web/main.py` lifespan 뒤 `app.middleware("http")` | 요청 진입 시 헤더 read + contextvars set + 응답 헤더 echo |
| MQ handler 진입 | `src/assessment_engine/consumer/handlers/` 각 핸들러 첫 줄, `src/assessment_engine/diagnostic/handler.py` 동일 | `message.message_id` 를 contextvars set |
| logger 설정 | `src/assessment_engine/log_config.py` (신규 또는 기존 setup) | loguru `logger.configure(extra={"request_id": "-"})` + format 에 `{extra[request_id]}` 포함 |

### 도입 트리거

다음 중 하나 발생 시 ADR 추가 후 도입:

1. prod 운영에서 요청 trace 어려움 발생 — 동일 사용자의 연쇄 요청을 grep 으로 묶기 힘들거나, MQ 메시지 발행 → 처리 흐름이 식별자만으로 부족.
2. 분산 trace (OpenTelemetry) 도입 — request_id 가 trace_id 로 자연 매핑.

### 도입 시 의무

- ADR 추가 (의사결정·옵션 비교 — 본 정석 패턴 채택 사유 + 명시적 트리거).
- middleware 위치는 `web/main.py` lifespan 뒤 (lifespan 안에서는 contextvars 시점이 어긋남).
- loguru `contextualize` 패턴 일관 — `logger.bind` 일회성 사용 금지.
- 로그 format 에 `{extra[request_id]}` 추가 — default `-` 로 미설정 흐름도 깨지지 않음.

---

## 관련 문서

- CLAUDE.md #F7 — 로깅 일상 룰 (단일 진실)
- CLAUDE.md #F6 — 외부 의존 fail-open/close 결정 매트릭스
- CLAUDE.md #F4 — Composition Root 패턴 (middleware 등록 위치)
- `docs/operations/env.md` "전체 키 카탈로그" — `LOG_FORMAT` env
- ADR 0011 — Prometheus metrics endpoint 채택
- ADR (도입 시 신규) — 분산 trace 채택 사유
