# 관측 (Observability)

정책: CLAUDE.md #F7. 본 문서는 Request/Correlation ID 분산 trace 도입 트리거·정석 패턴 단일 진실 — 본 프로젝트 현재 미적용, 도입 시 별도 ADR 의무.

## Request / Correlation ID 분산 trace

본 프로젝트 현재 미적용 — HTTP 측 `X-Request-ID` 없음, MQ `message_id`는 멱등성 키로만 활용. 로그는 식별자(machine_id·server_id)별 grep으로 trace.

### 정석 패턴

HTTP 요청 진입 시 `X-Request-ID` 헤더 read 또는 신규 UUID 생성 → `contextvars`로 보관 → 모든 로그에 자동 박힘 (loguru `logger.contextualize(request_id=...)`). MQ 메시지는 `message.message_id`를 같은 contextvars로 보관해 같은 흐름.

```
HTTP 요청 → middleware
              ├─ X-Request-ID 헤더 read (없으면 uuid4 생성)
              └─ contextvars.set(request_id)
                    ↓
         라우터 → service → repo
                    ↓
              loguru logger
                    ↓
         로그에 request_id=<uuid> 자동 박힘
                    ↓
         응답 헤더 X-Request-ID 에코
```

MQ 메시지:

```
aio-pika message 수신
    ↓
contextvars.set(request_id=message.message_id)
    ↓
handler → service → repo → loguru
    ↓
로그에 request_id=<message_id> 자동 박힘
```

### 구현 위치 (도입 시)

| 컴포넌트 | 위치 | 책임 |
|----------|------|------|
| HTTP middleware | `src/assessment_engine/web/main.py` lifespan 뒤 `app.middleware("http")` | 요청 진입 시 헤더 read + contextvars set + 응답 헤더 echo |
| MQ handler 진입 | `src/assessment_engine/consumer/handler.py` 각 핸들러 첫 줄, `src/assessment_engine/diagnostic/handler.py` 동일 | `message.message_id`를 contextvars set |
| logger 설정 | `src/assessment_engine/logging.py` (신규 또는 기존 setup) | loguru `logger.configure(extra={"request_id": "-"})` + format에 `{extra[request_id]}` 포함 |

### 도입 트리거

다음 중 하나 발생 시 ADR 추가 후 도입:

1. prod 운영에서 요청 trace 어려움 발생 — 동일 사용자의 연쇄 요청을 grep으로 묶기 힘들거나, MQ 메시지 발행 → 처리 흐름이 식별자만으로 부족
2. 분산 trace (OpenTelemetry) 도입 — request_id가 trace_id로 자연 매핑

### 도입 시 의무

- ADR 추가 (의사결정·옵션 비교 — 본 정석 패턴 채택 사유 + 명시적 트리거)
- middleware 위치는 `web/main.py` lifespan 뒤 (lifespan 안에서는 contextvars 시점이 어긋남)
- loguru `contextualize` 패턴 일관 — `logger.bind` 일회성 사용 금지
- 로그 format에 `{extra[request_id]}` 추가 — default `-`로 미설정 흐름도 깨지지 않음

## 관련 문서

- CLAUDE.md #F7 — 로깅 일상 룰 (단일 진실)
- CLAUDE.md #F4 — Composition Root 패턴 (middleware 등록 위치)
- ADR (도입 시 신규) — 분산 trace 채택 사유
