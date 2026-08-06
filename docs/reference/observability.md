# 관측 (Observability)

본 문서는 본 repo 가 제공하는 관측 contract 단일 진실 — 로그 레벨·외부 의존 실패 매트릭스·log format.

정책 출처: CLAUDE.md #F7 (로깅) · #F6 (외부 의존 실패 모드). 운영자는 아래 contract 만 충족하면 인프라 측
log aggregator 로 indexing·alerting 을 붙일 수 있다.

## 로그 레벨 (CLAUDE.md #F7 단일 진실)

| 레벨 | 용도 |
|------|------|
| ERROR | 처리 실패 + 사용자/메시지 영향 (DB raise·DLQ·5xx) |
| WARNING | 정상 흐름이지만 운영 시그널 (시계 invariant 위반·재시작 burst·Redis fail-open·DB 재시도 백오프) |
| INFO | 상태 전이 (auto-register·consumer 기동/구독/종료·중복 메시지 skip·워커 job 전이) |
| DEBUG | 루프 내부·메시지별 흐름 — 기본값이 `INFO` 라 안 찍힌다. `LOG_LEVEL=DEBUG` 로 연다 |

원칙·금지·loguru 규약은 CLAUDE.md #F7.

## 외부 의존 실패 모드 매트릭스 (CLAUDE.md #F6 단일 진실)

| 외부 의존 | 실패 모드 | 처리 | 시그널 |
|-----------|-----------|------|--------|
| PostgreSQL | fail-close | `_db_retry` 백오프 후 raise → DLQ / 5xx | ERROR |
| RabbitMQ broker | fail-close | aio-pika 자동 재연결, persistent 메시지 | ERROR |
| Redis | fail-open | `safe_*` 흡수 (#C3) → 다음 계층 fallback | WARNING |
| ZDM 패키지 메타 fetch (httpx) | fail-close | `ZdmPackageMetaError` → install 발행 취소, 503 | ERROR |

원칙·금지·예외 타입 catch 규약은 CLAUDE.md #F6.

## 로그 format toggle

stdout 로그 출력 format 을 `LOG_FORMAT` 환경변수로 토글하고, 최소 수준은 `LOG_LEVEL` 로 정한다(기본 `INFO`).

uvicorn·SQLAlchemy·aio-pika 는 stdlib `logging` 으로 내보내므로 `setup_logging` 이 그 레코드를 같은 loguru
sink 로 넘긴다 — 브릿지가 없으면 `LOG_FORMAT=json` 을 켜도 그 로그만 형식이 달라 aggregator 가 파싱에 실패한다.

예외 traceback 은 호출 스택만 남기고 프레임 지역변수는 남기지 않는다(`diagnose=False`). 그 프레임에는
비밀번호와 메시지 payload 가 들어온다(#F8).

- `text` (default) — loguru colorized 콘솔. dev grep·시연 가독성.
- `json` — loguru `serialize=True` 로 record 를 JSON 으로 변환. 외부 log aggregator (Loki·ELK·CloudWatch·Datadog 등) 가 `level`·`time`·`message`·`extra` 필드 자동 indexing → 검색·필터·alerting 가능.

구현: `src/assessment_engine/log_config.py` 의 `setup_logging(log_format, log_level)`. 각 entry (web/consumer/worker) 가 Composition Root 에서 호출 (F4 단일 진실). 세 컴포넌트의 Settings 가 모두 같은 env 를 읽는다.

환경별 권장값은 `docs/reference/contracts/env.md` 키 카탈로그.

본 repo 책임 한계:
- 로그 format 출력만. log aggregator stack 선택·운영 (Loki·ELK 등) + collector (Fluentbit·Promtail) 배포는 인프라 책임 (CLAUDE.md #A0).

## 관련 문서

- CLAUDE.md #F7 — 로깅 일상 룰 (단일 진실)
- CLAUDE.md #F6 — 외부 의존 fail-open/close 결정 매트릭스
- `docs/reference/contracts/env.md` "전체 키 카탈로그" — `LOG_FORMAT`·`LOG_LEVEL`
- `docs/explanation/tradeoffs.md` T23 — 분산 trace 미적용 근거와 재검토 트리거
