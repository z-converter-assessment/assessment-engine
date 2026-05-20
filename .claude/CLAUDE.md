# CLAUDE.md

> 본 파일은 본 프로젝트의 규약 단일 진실 (single source of truth).
> 실제 동작은 코드, 흐름은 `docs/architecture/` · `docs/operations/`, 트레이드오프는 `docs/tradeoffs.md`. 본 파일은 그 위에 얹는 결정 사항·원칙·금지 사항만 담는다.
>
> 섹션 번호 규약: A 시스템 → B 에이전트 데이터 계약 → C 데이터 계층 → D Consumer → E Web → F 운영 규약.
> 각 섹션은 자기 계층 책임만 다룬다. 계층 충돌 시 #E1 원칙(P1~P4) 우선순위로 해결.

## 문서 인덱스

본 파일을 읽다가 "상세는 X 절"을 만나면 아래 카테고리에서 위치 파악 후 `docs/README.md`로 점프 — 카테고리별 파일 목록·역할은 `docs/README.md` 단일 진실.

| 디렉토리 | 용도 | 수명 |
|----------|------|------|
| `docs/README.md` | 카테고리·파일 인덱스 — 어떤 문서를 언제 보는지 길잡이 | 영구·갱신 |
| `docs/architecture/` | 컴포넌트별 deep dive (모듈 설계·기술 구현) | 영구·갱신 |
| `docs/development/` | 본 repo 안 dev 작업·코드 규약 (docker·dependencies·pipeline·testing·conventions) | 영구·갱신 |
| `docs/operations/` | 외부 인프라가 활용할 contract (deployment·env·alembic·observability·release) | 영구·갱신 |
| `docs/products/` | 운영 산출물별 존재 의의·근거 (dashboard·환경 보고서·서버 보고서·JSON Export·Install task) | 영구·갱신 |
| `docs/adr/` | Architecture Decision Records — "왜 이렇게 결정했나" + 트레이드오프. ADR은 정정만, 덮어쓰기 금지 | 영구·불변 |
| `docs/tradeoffs.md` | 의식적 설계 선택과 그 한계 (T1~T13) | 영구·갱신 |

그 외 명시되지 않은 경로의 문서는 코드·영구 문서에서 인용 금지.

---

# A. 시스템

ZConverter Cloud Assessment Portal — 고객사 내부 네트워크 호스트 인벤토리·메트릭 수집·저장·분석(right-sizing·규칙 기반 진단) B2B 내부 포털. 시스템 소개·아키텍처 그림은 루트 `README.md` 단일 진실.

## A0. 범위

본 repo는 기능 개발에 필요한 환경 구성만 다룬다. 배포 인프라(IaC — Terraform·Ansible·OpenStack staging 등)는 본 repo 범위 밖. 추후 도입 결정 시 별도 repo로 분리 (ADR 0006 Withdrawn 사유).

본 절 결정:
- `dev/docker-compose.yml` 은 기능 개발용 한정 (dev 환경에서 앱·DB·MQ·Redis 한 번에 띄움). `docker-compose.prod.yml` 은 본 repo 에 두지 않음 — prod 운영 방식 contract 를 docker compose 형식으로 강제하지 않는다 (ADR 0012).
- prod 외부 인프라가 활용할 수 있는 정석 contract만 본 repo에서 유지:
  - 환경변수 contract — `docs/operations/env.md` 키 카탈로그
  - secret 채널 추상화 — `SecretStr` 강제 + pydantic `secrets_dir` (`SECRETS_DIR` env로 override 가능) + env var 둘 다 지원. 외부 인프라가 systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등 어떤 채널을 써도 본 엔진 동작
  - 환경 분기 — `APP_ENV=prod` + `_validate_prod_*` weak default 거부 (`docs/operations/env.md` 8절). secret 주입 방식은 무관, 결과(약한 default 거부)만 검증
  - CI 산출물 — Python wheel + GitHub Release (ADR 0012). 외부 인프라가 wheel 받아 install·systemd 자체 구성
- IaC 코드(`*.tf`·Ansible playbook·OpenStack 시나리오 문서)·prod compose 변형은 본 repo에 두지 않는다. 인프라 시나리오 언급 자체 금지 — 단 어떤 인프라든 위 contract 충족 시 본 엔진 기동 가능.

---

# B. 메시지 데이터 계약 (양방향)

메시지 데이터 형식·필드 카탈로그·task.install / task.result 흐름·수신/발행 routing key 카탈로그: `docs/architecture/agent.md`. MQ 토폴로지·큐 정책: `docs/architecture/rabbitmq.md`. 채택 사유: ADR 0007 (Task 별도 큐 모델 — 0002 supersede) · ADR 0004 (진단 워커 큐).

본 절 결정:
- Pydantic Input 모델 `extra=ignore` 유지 — 메시지에 새 필드가 도착해도 엔진은 통과시키고 무시. 비대칭 배포에서 reject 로 엔진이 죽지 않게 함.
- 활용하지 않는 필드는 mapper drop. 필요해진 시점에 mapper read + inbound DTO 필드 추가를 명시적 결정으로 처리.
- `agent_version` major bump 수신 시 엔진 코드 수정 트리거. minor bump 는 silent 호환.
- `task.result` 메시지는 발행 측 worker 컨텍스트가 수집 캐시와 분리되어 `boot_time` / `agent_started_at` 가 항상 null — 본 메시지에 한해 nullable override. 다른 메시지 타입은 required 유지.

---

# C. 데이터 계층

## C1. 키·제약 — 멱등성 의존 (#D2·#E4 직접 의존)

ORM 모델 / 식별자 규약(대리키·public_id) / 시계열 5테이블 자연키 UNIQUE 표 / 시계열 4테이블 `boot_time`·`agent_started_at` 공통 메타 / `tasks` 부분 UNIQUE: `docs/architecture/db/models.md` · `docs/architecture/db/timescaledb.md` 단일 진실.

본 절 결정:
- 시계열 5개 테이블 자연키 UNIQUE 보존 의무 — 누락 시 #D2 멱등성 2단 방어 깨짐. 모델 변경 시 검증 필수.
- 시계열 4개 테이블 `boot_time` + `agent_started_at` 컬럼 보존 의무 — counter reset 정밀 식별 (#B 동일 진실).
- `server_inventory.public_id` (UUID) URL 식별자 — 정수 PK 노출 금지 (#E4).
- `server_inventory` 호스트 식별 = `(machine_id, hostname)` 복합 UNIQUE — `machine_id` 단독은 VM 템플릿 복제·이미지 clone·container host `/etc/machine-id` 마운트 등 실제 운영 환경에서 중복 가능. `find_server_id`·`ensure_server_id` signature 와 `on_conflict_do_*` index_elements, redis cooldown 키 모두 복합 키 일관. 한계: MQ queue `agent.tasks.{machine_id}` / routing key `task.install.{machine_id}` 는 agent 측 변경 없이 hostname 포함 불가 — 같은 machine_id 다른 hostname 두 호스트가 동일 큐 공유 (rare race). 별도 ADR / 후속 작업.
- `diagnostic_jobs.job_type` (`ai_diagnostic`/`customer_report`/`engineer_report`) + active partial UNIQUE = `(scope, input_hash, job_type)`. 보고서도 본 테이블에 row 보존 — server scope (선택 N대) 는 `/servers/report` 라우터, environment scope (전체) 는 `/reports/environment` 라우터가 합성 직후 `DiagnosticService.record_report_emission` 으로 즉시 succeeded row INSERT (best-effort). 양식 분리: server scope 는 row 단위 상세 (`servers/report.html`), environment scope 는 high-level (KPI·분류 도넛·Top N·OS 분포·view별 요약, `reports/environment.html`). 이력 표시는 분리: AI 진단 이력 `/diagnostics/history` (job_type='ai_diagnostic' 자동 필터) vs 보고서 이력 `/reports/history` (customer + engineer union, view 필터). 환경 scope 진단 결과 페이지 (`/diagnostics?ids=X`) 는 SSR 로 `/reports/environment` iframe 2개 미리 렌더 + JS view toggle (AI/고객/엔지니어 tab).

## C2. Repository 계층 — 인터페이스 우선 (#F4)

추상 인터페이스(`BaseCollectRepository`/`BaseQueryRepository`/`BaseDiagnosticRepository`) · DTO 흐름(Inbound Pydantic·Outbound raw dataclass) · INSERT 통일(`pg_insert` + `on_conflict_do_*`) · `list_servers` 부분 SELECT 정책 · repo 메서드 카탈로그 · asyncpg 함정 · `_chart_*` 패턴: `docs/architecture/db/repositories.md` · `docs/architecture/db/dtos.md` · `docs/architecture/db/timescaledb.md` 단일 진실.

본 절 결정:
- settings 사용 절차: 컴포넌트 코드는 자기 sub-module(`web/settings.py`·`consumer/settings.py`·`diagnostic/settings.py`)에서 import. `db/session.py`·`db/redis.py`는 자체 `WebSettings()` 인스턴스화 (circular 회피). 새 모듈 추가 시 본 절차 위반 금지 (#F4).

## C3. Redis 전략 — fail-open 의무

키 설계 표 / TTL 근거 / PUB/SUB 채널 / 캐시-aside race 한계 / 평시·장애 동작 매트릭스 / mget 효율 패턴: `docs/architecture/redis.md`. 의사결정 ADR: `docs/adr/0001-redis-decoupling.md`.

본 절 결정:
- 모든 Redis 호출은 `src/assessment_engine/db/redis.py`의 `safe_*` helper(`safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_publish`/`safe_incr_with_ttl`) 경유. RedisError 시 silent fallback + warning 로그. 직접 redis client 호출 금지.
- fail-open 보장 의존성: 멱등성 1단 fail-open(#D2) → DB UNIQUE(#C1)가 중복 흡수. UNIQUE 누락 시 보장 자체가 깨짐.

## C4. 스키마 변경 — Alembic 단일 진실

모든 환경(dev·staging·prod·테스트) Alembic 단일 진실 / `migrate` init-container 패턴 / 모델 변경 시 동시 갱신 워크플로 / 라운드트립 검증 / autogenerate 미지원 카탈로그 / `_include_object` filter / CI `alembic check` / testcontainers + alembic / Backward compatibility 단계: `docs/operations/alembic.md` + ADR 0005 단일 진실.

본 절 결정:
- 시계열 신규 테이블 추가 시 마이그레이션에 `op.execute("SELECT create_hypertable(...)")` 보강 + 자연키 UNIQUE(#C1) + `boot_time`/`agent_started_at` 컬럼(#B) 동시 검토.

## C5. 쿼리 안전성

본 절 결정:
- hypertable 조회는 `WHERE collected_at >= ?` 술어 의무 — partition pruning. 누락 시 모든 chunk full scan. `_chart_*` 헬퍼·repo 메서드 모두 적용.
- raw SQL의 사용자 입력은 `text()` + bound parameter만. f-string으로 사용자 입력 직접 삽입 금지 — SQL injection + asyncpg statement cache 키 폭증. dispatch table whitelist 상수(Pydantic Literal → enum 매핑 정적 상수)는 f-string 허용.
- 트랜잭션 경계: consumer는 1 메시지 = 1 트랜잭션 (`session_factory()` 컨텍스트), web은 1 request = 1 세션 (`Depends(get_session)`). autocommit 금지·세션 공유·중첩 금지.

---

# D. Consumer

## D1. 구조·후처리·실패 처리

aio-pika 비동기 컨슈머(FastAPI 독립 프로세스) · 4 routing key 핸들러 팩토리 · `(machine_id, hostname)` 복합 키 식별 (#C1) · `ensure_server_id` placeholder 분기 · auto-register · `_db_retry` 백오프 · routing key별 후처리 시퀀스 · 부가 시그널(`_log_time_invariants`·`_track_agent_restart`) · 실패 분기·DLQ 운영: `docs/architecture/consumer.md` 단일 진실.

본 절 결정:
- 모든 후처리는 `safe_*` helper 경유(#C3) — 부수 작업 실패가 메시지 처리 ack를 막지 않는다.
- 캐시-aside race(web SET이 stale 데이터를 캐싱) 한계: `docs/tradeoffs.md` T2.
- 부가 시그널 로그 빈도 제어 의무: #F7.
- 메시지 자체 결함 → DLQ. 일시 외부 장애 → retry 후 DLQ. 의미상 처리 불가 → silent ack.
- DB는 fail-close, Redis는 fail-open(#C3·#F6 일관).

## D2. 멱등성: 2단 방어 (at-most-once, fail-open 1단)

본 절 결정:
- 1단 Redis fail-open: `safe_set_nx(idempotent:{message_id}, 24h)` — 동일 message_id 재전송 차단. Redis 장애 시 True 반환 → 2단이 흡수.
- 2단 DB UNIQUE: 시계열 4개 테이블 자연키 UNIQUE(#C1) + `pg_insert(...).on_conflict_do_nothing(index_elements=...)` — 1단 깨져도 silent no-op.
- fail-open 의존성: 시계열 4개 테이블 UNIQUE 제약(#C1) 누락 시 멱등성 보장 자체가 깨짐. 모델 변경 시 검증 필수.
- at-most-once 트레이드오프: SET NX는 DB 커밋 이전 실행 → 커밋 전 크래시 시 broker 재전송이 idempotent 충돌로 silent 드롭 가능. 한계·outbox 대안: `docs/tradeoffs.md` T1.

---

# E. Web

## E1. 렌더링 레이어 원칙 (표시 계층 단일 진실)

> 표시 코드를 어디에 둘지 결정할 때 P1~P4 우선순위로 적용.
> 충돌 시 P1 > P2 > P3 > P4 (P4는 P3의 명시 예외).

### P1. Repository는 raw 데이터만 (절대)
- raw 단위 그대로 outbound DTO (KB·bytes·jiffies·sectors). Python 레이어에서 delta·percent·단위 변환·임계값 분류·dedup·정렬 금지.
- SQL 표현식 예외: 차트·보고서 집계 SQL 안에서 percent·delta·집계 가능 (`_chart_*`/`report_aggregate`/`_METRIC_EXPR`). dispatch table whitelist 상수에만 적용, 사용자 입력 f-string 삽입 금지(#C5).

### P2. 서비스 계층이 표현 변환 단일 소스 (절대)
- Service → mapper → ViewModel 흐름에서 모든 파생 계산 (단위 변환·델타·임계값 분류·dedup·정렬·합계·풀네임).
- 동일 ViewModel이 SSR·JSON·캐시 역직렬화 어느 경로로도 일관 — 캐시 역직렬화 직후 `enrich_*()` idempotent 재호출.

### P3. Jinja2 템플릿은 순수 렌더링만 (절대)
- 허용: `{% if %}`·`{% for %}`·Jinja2 필터(포맷팅 전용).
- 금지: 계산(`+`, `*`, `length`, `sort`, `selectattr`)·dedup·임계값 비교·단위 변환. 정렬·`badge_class`/`bar_color`/`is_well_known` 같은 파생은 mapper precompute.
- 표시 컴포넌트 (폰트 위계·박스·badge·label) 와 네비게이션 규약 (새창 금지·뒤로가기 back chain·toast 에러 표시) 단일 진실: `docs/architecture/web/static-assets.md` "표준 컴포넌트 카탈로그" + "네비게이션 규약" 절.

### P4. 클라이언트 차트 JS는 P3 명시 예외

브라우저 인터랙션(range 토글·anchor 변경·legend) 즉시 반응 필요라 동적 시각화에 한해 JS 연산 허용.

- 허용: 버킷 그리드 생성·라벨 포매팅·Chart.js 옵션 조립·표시 단위 결정(B/s·kB/s·MB/s).
- 금지: 비즈니스 임계값 분류·API 응답 통계 재계산 (서버 `agg=avg|max|p95` 파라미터로 요청).
- 비동기 차트 로더 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/architecture/web/static-assets.md` "P4 차트 JS 5 의무 규약" 절 단일 진실.

## E2. 데이터 흐름 결정

- DTO(dataclass)와 ORM 모델 분리 — 변환은 repository 책임.
- inventory upsert·metrics 저장·server_id 조회 모두 `(machine_id, hostname)` 복합 키 기준. 미등록 metrics는 drop.
- `last_seen_at`은 `ServerDetail`(단일 조회)에만 포함. `ServerSummary`(목록)는 Redis `online:{id}` TTL로 표시.
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드.

Pagination 정책:
- 목록 endpoint(`list_servers` 등 정적 row): page 기반 — `page=1`, `limit=20` (max 100). 라우터 Query Pydantic 검증.
- 시계열·시간 흐름 endpoint(`metric_snapshots` / `GET /api/v1/tasks` 등): cursor 기반 — `cursor: datetime | None` + `limit`. 시간 역순 스크롤. page 번호 의미 없음 (계속 새 데이터 들어옴).
- 응답 envelope에 `total_count` / `has_more` 미포함 — `SELECT COUNT(*)` 별도 쿼리 비용 + UX는 빈 결과로 자연 종료 신호.
- 신규 목록 endpoint 추가 시 위 두 패턴 중 하나 선택 — 정적 row면 page, 시간 흐름이면 cursor.

다이어그램 / 라우터 모듈 표 / SSR 페이지 표 / JSON API 표: `docs/architecture/web/layering.md` + `docs/architecture/web/routers.md`.

## E3. 서비스 계층·ViewModel·Mapper (P2)

서비스 모듈 카탈로그·`mappers.py` 표시 파생 집중·`enrich_*` idempotent·UI badge 임계값(`_USAGE_DANGER_PCT=90`·`_USAGE_WARN_PCT=75`)·USE Method right-sizing 임계값(`assessment_engine/recommendation.py` 도메인 모듈 — web·diagnostic 공용 import)·ViewModel 카탈로그·mapper 파생 필드(`is_well_known`·`badge_class`·`bar_color` 등)·`cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화: `docs/architecture/web/services.md` · `docs/architecture/web/view-models.md` 단일 진실.

본 절 결정:
- 두 임계 도메인(UI badge / USE Method) 혼용 금지.
- 신규 ViewModel 파생 필드 추가 시 #F9 영향도 체크리스트 적용.

## E4. URL 식별자 — 정수 PK 노출 금지

라우터 path 파라미터는 `public_id` (UUID). 구현 메커니즘(UUID 타입 선언·422/404 분기·`resolve_internal_id` Depends 브릿지): `docs/architecture/web/layering.md`.

## E5. Jinja2 인프라

`Jinja2Templates` 단일 인스턴스 + 필터 등록은 `web/template_setup.py`에 격리. 라우터는 import만. Redis 캐시 datetime은 `datetime.fromisoformat()`로 파싱(`json.loads` str 그대로 두면 `kst` 필터 오작동).

Jinja2 필터 카탈로그(`kst`/`disksize`/`kbps`/`service_badge_class`/`or_dash`): `docs/architecture/web/services.md`.

## E6. 정적 자원 — JS 외부화 의무

디렉토리 구조 / `chart-utils.js` base.html 단일 로드 / `ChartUtils` API / 페이지별 .js / Reboot marker plugin: `docs/architecture/web/static-assets.md`. 외부화 강제 채널: #F5.

## E7. 도메인 분류 책임 (P2)

서비스 카테고리 분류(`classify`)·포트 매핑(`matched_ports`)은 `service_classifier.py`에서. 매퍼가 호출해 `ServiceItem`에 채움. 템플릿은 `service_badge_class` 필터로 category → CSS 클래스 변환만(P3).

키워드 매칭 표 / `SERVICE_PORTS` 폴백 / 서비스 3단계 표시 계층: `docs/architecture/web/services.md` "서비스 분류" 절.

## E8. 차트·도넛 UI 디테일 (P3·P4 적용)

차트 Y축·suggestedMax·avg+max ghost·P4 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/architecture/web/static-assets.md`. ViewModel 필드(`dash_length`/`dash_offset`/`bar_color`)·SVG 원주 상수(`_UTIL_DONUT_CIRC`)·임계 색 상수 카탈로그(`_UTIL_COLOR_LOW/MID/HIGH/NONE`·`_DONUT_SEGMENT_DEFS`·`_CAPACITY_TRIGGER_COLORS`): `docs/architecture/web/view-models.md` "신호 임계값 단일 정의" 절.

본 절 결정:
- 차트 Y축은 분해력(추이) vs 절대 기준(진단 리포트) 두 정책 중 선택. magic number 금지(명명 상수).
- SVG `stroke-dasharray`·`stroke-dashoffset` 비례 산술은 mapper precompute — 템플릿은 raw 값만 삽입.
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·프로비저닝 분포·capacity trigger 일관).
- 모든 카테고리 항상 노출(count 0 포함). 비활성은 동일 슬롯 옅은 회색.
- 도넛 중앙 강조 라벨은 가장 시급한 카테고리 카운트 1개만. 합계·ratio 노출 금지.

---

# F. 운영 규약

## F1. 타입 어노테이션
- `from __future__ import annotations` 절대 금지 — 전 파일.
- `TYPE_CHECKING` 블록 절대 금지.
- type checker 만족용 런타임 검사 (`assert x is not None` 등) 금지.

IDE 경고 대처 매뉴얼 · Hook 강제 채널 카탈로그: `docs/development/conventions.md` 단일 진실. hook 위반은 즉시 수정 의무.

## F2. 시간대 정책 (UTC 저장 / KST 표시)

원칙:
- 모든 datetime은 UTC로 저장·전송·내부 처리 (tzinfo-aware).
- KST 변환은 표시 경계 4 함수에서만 단일 적용 — SSR `kst` 필터 / client `ChartUtils.fmtLabel`·`fmtKst`·`initAnchor`.

금지:
- DB·Repository·Service·ViewModel·캐시·API 응답 단계에서 KST 변환.
- 인라인 변환 (`new Date(... + 9*60*60*1000)` 등 임의 offset 더하기).
- naive datetime — 외부 문자열 수신 시 `datetime.fromisoformat()`으로 tzinfo 보존.

## F3. 검증의 단일 경로

원칙: 검증은 요청 진입점에서 한 번만 — 라우터 Pydantic(query/path/body) · Consumer `model_validate_json()`(MQ payload) · `BaseSettings`(환경변수). path UUID는 `resolve_internal_id` Depends가 422(형식)·404(미존재) 자동.

금지: Service·Mapper·Repository·사용처에서 재검증 (`_VALID_*` frozenset 비교 등).

## F4. 인터페이스 우선 — Composition Root 패턴

원칙: Service/Handler는 추상 인터페이스(`Base*Repository`)만 의존. 구체 구현체·`Settings()` 인스턴스는 Composition Root에서만.

`Settings()` 인스턴스 단일 진실 위치:
- `src/assessment_engine/web/settings.py` — `web_settings` (WebSettings) + `diagnostic_settings` (DiagnosticSettings, web도 진단 publish 위해 broker 사용)
- `src/assessment_engine/consumer/settings.py` — `consumer_settings` (ConsumerSettings)
- `src/assessment_engine/diagnostic/settings.py` — `diagnostic_settings` (DiagnosticSettings, worker·scheduler 공통)
- `src/assessment_engine/db/session.py`·`db/redis.py`·`migrations/env.py` — 자체 `WebSettings()` (모든 컴포넌트 공통 db layer·schema 진입점, circular import 회피)

`src/assessment_engine/config.py`는 class 정의만 — module-level instance 0 (multi-node 분리 정합, ADR/문서 패턴 정합).

금지:
- Service/Handler 안 구체 구현체 import.
- Composition Root 외 위치에서 `Settings()` 인스턴스 생성 — 위 6 위치 (web/settings·consumer/settings·diagnostic/settings·db/session·db/redis·migrations/env)만 허용.
- `assessment_engine.config`에서 직접 `web_settings`·`consumer_settings`·`diagnostic_settings` import — class만 export.
- `APP_ENV` 환경 분기를 `config.py` model_validator · entry lifespan 외 위치에 추가.

추상 인터페이스 카탈로그·새 Repository 절차: `docs/architecture/web/layering.md` · `docs/architecture/db/repositories.md`.

## F5. 자동화 변환 — 책임 분담

원칙: 자동화 변환(sed · `Edit replace_all` · 디렉토리 mv · Python 일괄 갱신) 직후 검증을 3 채널로 분담.
- Hook (`.claude/hooks/`) — 무인 강제. F1 위반(`from __future__ import annotations` 등) 차단.
- 메인 세션 — 자가 검증. 변환 직후 매 회 의무 (아래 4 항목).
- 에이전트 (code-reviewer / schema-contract-auditor) — 사용자 명시 요청(`리뷰해줘`·`스키마 일관성 확인` 등) 시에만 발동.

메인 자가 검증 의무:
1. 옛 패턴 잔존 0건 grep.
2. 새 패턴이 의도된 스코프에만 (함수 외부·의도 외 위치 grep 검증).
3. `.html` 변경 시 신규 inline `<script>` 코드 줄 grep (외부 `.js` 강제).
4. DTO · 매퍼 · `cache_serializer` · 템플릿 · JS 체인 의미적 동기화.

금지:
- 검증 생략 후 다음 단계 진행.
- 사용자 IDE 경고·브라우저 콘솔 발견에 의존.
- 명시 요청 없이 pytest 실행 또는 "테스트 통과"를 검증 결과로 보고.
- Hook 강제 영역(F1 future annotations 등) 메인 중복 grep.
- 메인이 에이전트 자동 위임 제안.

에이전트 결과: Error → 즉시 수정 / Warning → 사용자 결정 위임 / Info → 보고만.

변환 유형별 체크리스트·누적 사고 패턴: `docs/development/conventions.md` 3절·4절.

## F6. 에러 처리·실패 모델

원칙: 외부 의존은 fail-close/fail-open을 컴포넌트 단위로 미리 결정. 결정 근거 없으면 새 통합 도입 금지.

외부 의존별 실패 모드 매트릭스: `docs/operations/observability.md` "외부 의존 실패 모드 매트릭스" 절 단일 진실.

금지:
- `except Exception` 광범위 catch — 예외 타입 명시(`OperationalError`/`IntegrityError`/`RedisError`/`asyncio.TimeoutError` 등). 불가피하면 reraise + 컨텍스트 로그.
- 영구 오류(`IntegrityError`·4xx) 재시도. 일시 장애(`OperationalError`·5xx·timeout)만 백오프.
- timeout 없는 외부 호출 — `asyncio.wait_for` 또는 클라이언트 옵션(`aiohttp.ClientTimeout`·asyncpg `command_timeout`·redis `socket_timeout`) 의무.

소비자 측 상세 매트릭스: `docs/architecture/consumer.md` "DB 재시도 정책" + "메시지 자체 결함 → DLQ" 절.

## F7. 로깅·관측

원칙: 로그는 운영 시그널 — 양이 많으면 시그널이 묻힌다. 레벨·내용·빈도 모두 의도 있게.

레벨별 용도 매트릭스: `docs/operations/observability.md` "로그 레벨" 절 단일 진실.

본 절 결정:
- `loguru` 단일 채택. `print`/`sys.stdout.write`/stdlib `logging` 혼용 금지.
- 예외 로깅은 except 블록 안 `logger.exception()`만 — 자동 traceback. 일반 ERROR는 `logger.bind(...).error(...)`.
- 시그널 로그(`_log_time_invariants`·`_track_agent_restart`)는 쿨다운·슬라이딩 윈도우 의무 — 매 메시지 발생 시 진짜 시그널 매몰.
- 새 시그널 도입 시 (a) 레벨 (b) 빈도 제어 (c) 운영자 행동 — 셋 다 명시.

금지: payload·secret raw dump — 식별자(machine_id·routing key·message_id·server_id)와 카운트만.

로그 format: `LOG_FORMAT` env 분기 — `text`(dev colorized) 또는 `json`(prod, loguru `serialize=True`). 각 entry(web/consumer/diagnostic-worker/diagnostic-scheduler)가 기동 직후 `setup_logging(settings.log_format)` 호출. 단일 진실은 `src/assessment_engine/log_config.py`.

Request/Correlation ID 분산 trace 도입 트리거·정석 패턴: `docs/operations/observability.md` (현재 미적용, 도입 시 별도 ADR 의무).

## F8. 시크릿·PII 노출 금지

원칙: 로그·예외·HTTP 응답·ViewModel·캐시 어디에도 비밀번호·토큰·전체 메시지 payload·고객사 식별 가능 정보 노출 금지. 한 번 새면 영구.

금지:
- pydantic Settings 비밀 필드 `SecretStr` 미적용 — 신규 비밀 필드 의무.
- `.env`·`dev/agent.env` 파일 commit. PR diff `password`/`secret`/`token`/`key` 패턴 검토 의무.
- 예외 메시지에 raw payload·접속 문자열 — catch 후 sanitize 후 reraise.
- HTTP 응답·ViewModel·JSON export에 PII. 운영 식별자는 `public_id`(UUID)만(#E4).
- Redis·DB에 raw payload 캐싱 — Outbound DTO·ViewModel 단계에서 sanitize 후.
- 메시지 payload 본문 로깅 (`machine_id`는 식별자라 OK).

secret 채널·prod default 자동 검증(`_validate_prod_*`): `docs/operations/env.md`.

## F9. 변경 영향도 체크리스트

원칙: 영향받는 모든 곳 동시 갱신 의무 — 한 곳만 수정 후 PR 금지.

| 변경 유형 | 동시 갱신 위치 |
|-----------|----------------|
| 시계열 컬럼 추가 | (1) ORM 모델 (2) Alembic revision (3) Inbound DTO·mapper (4) Outbound DTO·mapper (5) `cache_serializer._DETAIL_DISPLAY_FIELDS` (6) ViewModel (7) 템플릿·외부 .js |
| inventory 컬럼 추가 | 시계열 (1)~(7) + agent payload 합의 + `docs/architecture/agent.md` "데이터 형식" 절 (엔진 측 inbound DTO·핸들링 단일 진실) |
| 신규 routing key | (1) 발행 측 (agent 또는 engine web) 상수 (2) consumer 핸들러 팩토리 + dispatch (3) `docs/architecture/rabbitmq.md` 토폴로지 표 (4) `docs/architecture/agent.md` 메시지 타입 절 |
| `EXCHANGE`/`ROUTING_KEY_*` 값 변경 | (1) 발행 측 상수 (2) consumer subscriber dispatch (3) `docs/architecture/rabbitmq.md` 토폴로지 표 |
| 메시지 페이로드 schema 변경 (필드 추가·삭제·rename·Literal 값 변경) | (1) `consumer/schemas.py` 또는 발행 측 payload 빌드 (2) Inbound DTO (3) handler 매핑 (4) DB 모델·Alembic revision (필요 시) (5) `docs/architecture/agent.md` 데이터 형식 절 (6) 운영자 가시성 ViewModel·템플릿·API (필요 시) |
| `recommendation.py` 분류 임계 또는 Lima VM 매트릭스 변경 | (1) `recommendation.py` 임계 상수 (2) `docs/development/pipeline.md` "VM 매트릭스"(합성 부하·swap_used 트리거) (3) #F10 평가 윈도우 정합 |
| 환경변수 추가 | (1) `Settings` 필드 (2) `docs/operations/env.md` 카탈로그 (3) `dev/docker-compose.yml` `environment:` (dev 필요 시) (4) prod secret 분류면 `SecretStr` 타입 + `_validate_prod_*` 에 weak default 거부 추가 + `docs/operations/env.md` 2절·7절 |
| ViewModel 파생 필드 추가 | (1) mapper 계산 (2) `cache_serializer._DETAIL_DISPLAY_FIELDS` (3) 템플릿 표시 (4) 동일 데이터 JSON API 응답이면 dataclass(P2) |
| 신규 외부 의존(HTTP·LLM·외부 큐) | (1) fail-open/close 결정(#F6) (2) timeout·재시도 정책 (3) Settings 필드 (4) #F6 매트릭스 갱신 |
| 신규 의존성(`pyproject.toml`) | (1) `uv.lock` 갱신 (2) PR 설명에 도입 사유 (3) 대형 의존성은 ADR 검토. 워크플로 단일 진실: `docs/development/dependencies.md` |


## F10. 평가 윈도우 · 차트 시계열 옵션 — 단일 진실

원칙: 보고서·대시보드·차트 모두 같은 평가 윈도우·시계열 옵션 카탈로그 참조 — 화면별 의미 분기 방지.

본 절 결정:
- 평가 윈도우 단일 진실 = `recommendation.WINDOW_DAYS` (현재 14, AWS Compute Optimizer 표준). 대시보드·보고서 라우터·ADR 0003 모두 본 상수 참조.
- 보고서 라우터만 `?period_days=N` override 허용. 대시보드는 산업 표준 윈도우 고정.
- TimeRange/BucketSize Literal 단일 진실 = `base_query_repository.TimeRange`/`BucketSize` + `_BUCKET_INFO` + `chart-utils.js`. 새 range·bucket 도입 시 backend Literal·SQL dispatch·JS 매핑·UI 토글 4곳 동시 갱신 의무.
- 신규 TimeRange 도입 시 `AUTO_BUCKET` 매핑 신설 의무.
- 보고서 형태 산출물은 윈도우를 envelope·표제 명시 — JSON Export `period_window{days, start, end}` 의무 필드(#B 동일 원칙).

## F11. Disposability — Graceful shutdown (12-factor IX)

원칙: SIGTERM 시 in-flight 작업 손실 0 + 다음 기동 시 stale 상태 없음.

본 절 결정:
- web — uvicorn `timeout_graceful_shutdown=3s`. 진행 중 HTTP 요청 완료 후 exit. SSE는 client reconnect.
- consumer / diagnostic-worker — `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료. 정상 exit → ACK / raise → NACK + DLQ.
- diagnostic-scheduler — cron 발화 사이 SIGTERM 즉시 안전. publish 중 SIGTERM은 aio-pika `connect_robust` transaction 보장.
- diagnostic-worker 진행 중 job(`status='running'`) stale 정리 미구현 — prod 도입 전 ADR 0004 정정 또는 별도 ADR 의무.

금지:
- `signal.signal(SIGTERM, ...)` 직접 핸들러 — uvicorn/asyncio 자체 처리, 중복은 종료 race.
- `os._exit()` — graceful shutdown 우회.
- `message.process()` 컨텍스트 밖 await — ACK/NACK 둘 다 안 됨.

상세: `docs/architecture/consumer.md` · `docs/architecture/diagnostic.md` "Disposability" 절.

---