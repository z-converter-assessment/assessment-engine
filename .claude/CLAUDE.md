# CLAUDE.md

> 본 파일은 본 프로젝트의 규약 단일 진실 (single source of truth).
> 실제 동작은 코드, 흐름은 `docs/reference/` · `docs/guides/`, 트레이드오프는 `docs/explanation/tradeoffs.md`. 본 파일은 그 위에 얹는 결정 사항·원칙·금지 사항만 담는다.
>
> 섹션 번호 규약: A 시스템 → B 에이전트 데이터 계약 → C 데이터 계층 → D Consumer → E Web → F 운영 규약.
> 각 섹션은 자기 계층 책임만 다룬다. 계층 충돌 시 #E1 원칙(P1~P4) 우선순위로 해결.

## 문서 인덱스

본 파일을 읽다가 "상세는 X 절"을 만나면 아래 카테고리에서 위치 파악 후 `docs/README.md`로 점프 — 카테고리별 파일 목록·역할은 `docs/README.md` 단일 진실.

문서는 Diátaxis 4목적으로 가른다 — 찾는 이유(지금 어떻게/어떻게 하나/왜 설계/왜 바꿨나)별로.

| 디렉토리 | 목적 (Diátaxis) | 성격 |
|----------|----------------|------|
| `docs/README.md` | 문서 관리 계약 (4원칙) + 지도 단일 진실 | 영구·갱신 |
| `docs/reference/` | 지금 어떻게 도나 — subsystem 동작·docker 구성 + `contracts/` 얼어붙은 계약 | 현재 상태 선언 |
| `docs/guides/` | 어떻게 하나 — 작업 절차 | 현재 상태 선언 |
| `docs/explanation/` | 왜 이렇게 설계했나 — 한계(`tradeoffs.md`)·산출물 의의(`products/`) | 현재 상태 선언 |
| `docs/decisions/` | 왜 바꿨나 — `adr/`(결정)·`rfc/`(제안) append-only 이력 | 불변 아카이브 |

그 외 명시되지 않은 경로의 문서는 코드·영구 문서에서 인용 금지.

`docs/temp/` 디렉토리 — 임시 자료 모음. 용도 둘: 외부 공유 자료(협의 input 등)와 학습 자료 초안. 디렉토리 위치 자체는 영구이나 안의 파일은 임시 (자유 작성·삭제). 본 repo 영구 문서·코드가 temp 인용 금지. 외부 공유 자료는 본 repo 영구 문서·코드 의존도 금지 (self-contained 필수) — 학습 자료 초안은 이 제약에서 예외이며 `docs/learning/` 격상 대상이다. 인덱스 표에 추가 안 함.

`docs/learning/` 디렉토리 — 학습 자료. 도구·플랫폼 원리가 본문이고 이 저장소는 그것이 실물로 어떻게 나타나는지 보여주는 예제로 쓰인다. 시점 스냅샷이라 코드 변경 추종 의무 없음 (기준 시점 표기 의무). 인용 단방향 — 학습 자료가 코드·영구 문서를 가리키는 것은 허용, 영구 문서·코드가 학습 자료 인용 금지. 계층 3층(L1 플랫폼·프로토콜 / L2 도구·생태계 / L3 이 저장소의 조합)과 작성·격상 규약은 `docs/learning/README.md` 단일 진실. 인덱스 표에 추가 안 함.

## 빠른 참조 (진입점)

> 오리엔테이션 포인터. 커맨드 절차 상세는 `docs/guides/` 단일 진실 — 본 파일은 복제하지 않는다 (#F12).

프로세스 (단일 이미지 — 어느 컴포넌트를 띄울지는 compose `command` 가 정한다):
- `assessment_engine.web` — FastAPI SSR+JSON 표현 계층 (E)
- `assessment_engine.consumer` — aio-pika MQ 소비·수집 (D)
- `assessment_engine.worker` — 보고서 생성 + install reaper (F11)
- compose `migrate` 서비스 — alembic init-container (C4)

작업 절차 진입 (상세는 각 guide):
- 이미지·compose 구성 = `docs/reference/docker.md` / dev 기동·코드 반영 = `docs/guides/local-dev.md`
- 테스트 = `docs/guides/testing.md` / 마이그레이션 = `docs/guides/migrate.md`
- 배포(VM rollout)·부트스트랩 = `docs/guides/deploy.md` / 릴리즈 = `docs/guides/release.md`
- 표현계층 타입 계약(codegen·tsc) = `docs/reference/web/type-contract.md`
- 커밋·PR 마무리 = `docs/guides/wrap-up.md`

---

# A. 시스템

ZConverter Cloud Assessment Portal — 고객사 내부 네트워크 호스트 인벤토리·메트릭 수집·저장·분석(right-sizing·규칙 기반 진단) B2B 내부 포털. 시스템 소개·아키텍처 그림은 루트 `README.md` 단일 진실.

## A0. 범위

본 repo는 엔진 애플리케이션 + docker compose 배포 + 엔진 rollout(`deploy.sh`, 배포 대상 VM 에서 실행)까지 다룬다. VM provisioning(IaC — VM 생성·OS 설정)은 별도 준비 VM 전제. docker·cosign 설치와 운영 스크립트(`deploy.sh`·`rotate-secret.sh`) 배치는 1회성 `bootstrap.sh`.

본 절 결정:
- compose = 공통 base + dev override(소스 빌드·bind mount, 파일명으로 자동 머지) + prod overlay(file-secret). dev = base+override, prod = base+prod.yml — 어느 쪽이 붙는지는 `.env` 의 `COMPOSE_FILE` 이 정한다. base 는 환경 색을 담지 않는다. Dockerfile 은 dev/prod 분리 안 함 (parity — dev 편의는 override bind mount 로만). prod 비번 = file-secret 채널 단일 (`SecretStr`). 파일 구조·서비스 카탈로그 상세 = `docs/reference/docker.md`.
- prod 외부 인프라가 활용할 수 있는 정석 contract만 본 repo에서 유지:
  - 환경변수 contract — `docs/reference/contracts/env.md` 키 카탈로그
  - secret 채널 추상화 — `SecretStr` 강제 + pydantic `secrets_dir` (`SECRETS_DIR` env로 override 가능) + env var 둘 다 지원. 외부 인프라가 systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등 어떤 채널을 써도 본 엔진 동작
  - 설정 검증 — 비밀번호 미설정·빈값·뻔한 값·채널 충돌을 기동 시점에 거부 (`docs/reference/contracts/env.md` 6절). 환경으로 강도를 가르지 않고, secret 주입 방식과 무관하게 결과만 검증
  - CI 산출물 — 서명(cosign)·SBOM(SPDX)·provenance 된 OCI 이미지 단일 (GHCR). 배포는 VM 에서 `deploy.sh` 실행 (시퀀스는 `docs/guides/deploy.md` 3절). GitHub Actions runner 미사용(public repo 에 self-hosted runner 안티패턴 회피) — 내부망 VM 이 outbound 로 이미지 pull
- VM provisioning 코드(`*.tf`·Ansible playbook·VM 생성·OS 설정)는 본 repo에 두지 않는다 — 배포 대상 VM 은 provisioning 완료 상태를 전제. 엔진 rollout(`deploy.sh`)·비밀번호 교체(`rotate-secret.sh`)·VM 부트스트랩(`bootstrap.sh`)은 범위 안. 단일 호스트 compose 수동 기동도 지원.

---

# B. 메시지 데이터 계약 (양방향)

메시지 데이터 형식·필드 카탈로그·task.install / task.result 흐름·수신/발행 routing key 카탈로그: `docs/reference/contracts/agent-data.md`. MQ 토폴로지·큐 정책: `docs/reference/rabbitmq.md`. Task 는 별도 큐 모델.

본 절 결정:
- Pydantic Input 모델 `extra=ignore` 유지 — 메시지에 새 필드가 도착해도 엔진은 통과시키고 무시. 비대칭 배포에서 reject 로 엔진이 죽지 않게 함.
- 활용하지 않는 필드는 mapper drop. 필요해진 시점에 mapper read + inbound DTO 필드 추가를 명시적 결정으로 처리.
- wire 계약 버전 = envelope `schema_version` (현 "1.0", 상수 `AGENT_CONTRACT_VERSION` = `contract.py`). 게이트는 major 일치 — minor 는 additive 라 수용한다. 구조 전환은 major 판별(flag-day cutover). `agent_version` major bump 수신 시 엔진 코드 수정 트리거, minor bump silent 호환.
- `task.result` 메시지는 발행 측 worker 컨텍스트가 수집 캐시와 분리되어 `boot_time` / `agent_started_at` 가 항상 null — 본 메시지에 한해 nullable override. 다른 메시지 타입은 required 유지.
- `task.result` 종료 신호: `exit_code` / `signal_no` (int\|null) 상호배타 — 정상종료=exit_code / 시그널종료=signal_no / 미포착=둘 다 null (POSIX wait status). `task_policy`(bool\|null)는 exit_code 보다 우선 판정. `signal_no` 는 `tasks.signal_no` 저장 + task 상세 표시(`mappers/task._signal_label` SIG 이름 라벨). Windows signal_no 항상 null. task_id 로 매칭(composite_id 불요).
- 인바운드 DTO 는 wire 계약과 정합: `boot_time` nullable (판독 불가 시 null, `_log_time_invariants` None 가드) / `composite_id` "" -> None 정규화 (digest 실패 흡수) / error `failed_component` 자유 문자열 수용 (wire permissive, `Literal` 로 좁히면 유효 메시지 DLQ).
- 스토리지·디바이스 = `block_devices[]` 정규화 평면 그래프(parent-by-id 조인, major/minor 폐기) + `system.filesystem` usage(state used/free) + `lvm_vgs`(확장여력 free_bytes). 시계열·조인 device 축은 안정 id (디스크 폴백 dm/partuuid/serial/by-path / 네트워크 MAC) — 이름 아님. 상세 = `docs/reference/contracts/agent-data.md` E·F·G절.

---

# C. 데이터 계층

## C1. 키·제약 — 멱등성 의존 (#D2·#E4 직접 의존)

ORM 모델 / 식별자 규약(대리키·public_id) / 시계열 8테이블 자연키 UNIQUE 표 / `boot_time`·`agent_started_at` 은 `server_metrics` 만 보유(자식 시계열 미보유) / `tasks` 부분 UNIQUE: `docs/reference/db/models.md` · `docs/reference/db/timescaledb.md` 단일 진실.

본 절 결정:
- 시계열 metric 7테이블 자연키 UNIQUE 보존 의무 — 누락 시 #D2 멱등성 2단 방어 깨짐. 모델 변경 시 검증 필수.
- `server_metrics` 만 `boot_time` + `agent_started_at` 컬럼 보존 — 자식 시계열은 미보유(rate 차트 reset 은 `GREATEST(delta,0)`, 보고서 cagg 는 `counter_agg` 가 값-감소 기준 흡수). counter reset 정밀 식별 (#B 동일 진실).
- `server_inventory.public_id` (UUID) URL 식별자 — 정수 PK 노출 금지 (#E4).
- `server_inventory` 식별 분리: `id` PK (FK 대상) / `agent_id` (agent 매칭·식별·라우팅 단일 키 — 첫 실행 시 생성·영구저장한 불변 UUID) / `composite_id`·`machine_id` (감사·표시 전용 nullable — 식별·라우팅 미사용) / `public_id` (URL 노출) / `hostname` display (UNIQUE X). 시계열 8테이블이 `server_id` FK 로 붙는다. 컬럼 타입·제약 표는 `docs/reference/db/models.md`. MQ queue `agent.tasks.{agent_id}` / routing key `task.install.{agent_id}`.
- 식별키 agent_id 불변: agent_id 는 첫 실행 시 1회 생성·영구저장하는 불변 UUID — 부팅마다 NIC MAC 이 재발급되는 환경(OpenStack Windows VM)에서도 동일 agent_id 가 자연히 같은 행을 upsert 한다. 별도 호스트 재연결 로직 없음. composite_id/machine_id 는 clone collision 진단용 감사 컬럼.
- `diagnostic_jobs.job_type` (`customer_report`/`engineer_report` 둘만) + active partial UNIQUE = `(scope, input_hash, job_type)`. 발행 시점 정적 스냅샷을 `result` JSONB 에 보존. customer/engineer 모두 비동기 생성 (pending -> 워커 claim·running -> succeeded/failed). status·progress_stage·started_at·error_message + active partial UNIQUE 가 비동기 상태머신.
- 보고서 = 발행 시점 정적 스냅샷 (재계산 0, 이력 동적변화 0). 비동기 생성 — emit 이 parent job pending enqueue 후 즉시 `?job={id}` 반환, 전용 워커 프로세스(`worker/report_worker.py`)가 job 을 claim 해 스냅샷 ViewModel 생성·`result` JSONB 저장 (생성 불가 시 failed). 더블클릭은 active UNIQUE 로 기존 job 합류. GET `?job={id}` = succeeded 면 정적 렌더, pending/running 이면 `report-poll.js` 폴링.
- server scope N대(ids 2개+) 발행 = 워커가 개별 단일 보고서 N건 + selection 본문을 parent 1건 처리 단위로 생성 — child 전부 성공해야 parent succeeded (부분 누락 차단). ids 1개 = 단일. 양식 통일 — 단일/N대/환경 모두 환경 보고서 양식(`EnvironmentReportSummary`) 공유, selection·단일 전용 필드는 환경에서 None/빈 list.
- 보고서 본문 구조·요약 섹션·운영신호 정책(표시는 os_eol 만)·이력 표시 상세 = `docs/explanation/products/environment-report.md`·`server-report.md`.

## C2. Repository 계층 — 인터페이스 우선 (#F4)

추상 인터페이스(`BaseCollectRepository`/`BaseQueryRepository`/`BaseDiagnosticRepository`) · DTO 흐름(Inbound Pydantic·Outbound raw dataclass) · INSERT 통일(`pg_insert` + `on_conflict_do_*`) · `list_servers` 부분 SELECT 정책 · repo 메서드 카탈로그 · asyncpg 함정 · `_chart_*` 패턴: `docs/reference/db/repositories.md` · `docs/reference/db/dtos.md` · `docs/reference/db/timescaledb.md` 단일 진실. `Settings()` 인스턴스 사용 절차는 #F4 단일 진실.

## C3. Redis 전략 — fail-open 의무

키 설계 표 / TTL 근거 / 캐시-aside race 한계 / 평시·장애 동작 매트릭스 / mget 효율 패턴: `docs/reference/redis.md`.

본 절 결정:
- 모든 Redis 호출은 `src/assessment_engine/cache/redis.py`의 `safe_*` helper(`safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_incr_with_ttl`) 경유. RedisError 시 silent fallback + warning 로그. 직접 redis client 호출 금지.
- fail-open 보장 의존성: 멱등성 1단 fail-open(#D2) → DB UNIQUE(#C1)가 중복 흡수. UNIQUE 누락 시 보장 자체가 깨짐.

## C4. 스키마 변경 — Alembic 단일 진실

모든 환경(dev·staging·prod·테스트) Alembic 단일 진실 / `migrate` init-container 패턴 / 모델 변경 시 동시 갱신 워크플로 / 라운드트립 검증 / autogenerate 미지원 카탈로그 / `_include_object` filter / CI `alembic check` / testcontainers + alembic / Backward compatibility 단계: `docs/guides/migrate.md` 단일 진실.

본 절 결정:
- 시계열 신규 테이블 추가 시 마이그레이션에 `op.execute("SELECT create_hypertable(...)")` 보강 + 자연키 UNIQUE(#C1) 동시 검토 (`boot_time`/`agent_started_at` 은 `server_metrics` 만 보유, 자식 시계열은 미보유 — #B).
- continuous aggregate: 정의 + policy 는 마이그레이션 트랜잭션 내, 초기 materialize(`refresh_continuous_aggregate`)는 트랜잭션 밖 1회. cagg 정의에 박힌 필터(물리 device 등) 규약 변경 시 cagg 재생성 마이그레이션 동반. 상세 = `docs/guides/migrate.md`.

## C5. 쿼리 안전성

본 절 결정:
- hypertable 조회는 `WHERE collected_at >= ?` 술어 의무 — partition pruning. 누락 시 모든 chunk full scan. `_chart_*` 헬퍼·repo 메서드 모두 적용. continuous aggregate 조회는 `WHERE bucket >= ?`(동일 pruning).
- 카운터 메트릭(CPU jiffies·disk/net bytes) 집계는 continuous aggregate + timescaledb_toolkit `counter_agg` 사전집계 단일 진실. counter reset(재부팅·agent재시작·wraparound)은 `counter_agg` 가 값-감소 기준 일률 처리 — 보고서 집계(`report_aggregate`·`report_*_baseline`·`report_cpu_breakdown`)에서 hand-rolled LAG + boot_time gate 부활 금지. 차트(`metric_trend`, 동적 버킷)는 목적상 raw 유지.
- raw SQL의 사용자 입력은 `text()` + bound parameter만. f-string으로 사용자 입력 직접 삽입 금지 — SQL injection + asyncpg statement cache 키 폭증. dispatch table whitelist 상수(Pydantic Literal → enum 매핑 정적 상수)는 f-string 허용.
- 트랜잭션 경계: consumer는 1 메시지 = 1 트랜잭션 (`session_factory()` 컨텍스트), web은 1 request = 1 세션 (`Depends(get_session)`). autocommit 금지·세션 공유·중첩 금지.

---

# D. Consumer

## D1. 구조·후처리·실패 처리

aio-pika 비동기 컨슈머(FastAPI 독립 프로세스) · 4 routing key 핸들러 팩토리 · `agent_id` 단일 키 식별 (#C1) · `ensure_server_id` placeholder 분기 · auto-register · `_db_retry` 백오프 · routing key별 후처리 시퀀스 · 부가 시그널(`_log_time_invariants`·`_track_agent_restart`) · 실패 분기·DLQ 운영: `docs/reference/consumer.md` 단일 진실.

본 절 결정:
- 모든 후처리는 `safe_*` helper 경유(#C3) — 부수 작업 실패가 메시지 처리 ack를 막지 않는다.
- 캐시-aside race(web SET이 stale 데이터를 캐싱) 한계: `docs/explanation/tradeoffs.md` T2.
- 부가 시그널 로그 빈도 제어 의무: #F7.
- 메시지 자체 결함 → DLQ. 일시 외부 장애 → retry 후 DLQ. 의미상 처리 불가 → silent ack.
- DB는 fail-close, Redis는 fail-open(#C3·#F6 일관).

## D2. 멱등성: 2단 방어 (at-most-once, fail-open 1단)

본 절 결정:
- 1단 Redis fail-open: `safe_set_nx(idempotent:{message_id}, 24h)` — 동일 message_id 재전송 차단. Redis 장애 시 True 반환 → 2단이 흡수.
- 2단 DB UNIQUE: 시계열 metric 7테이블 자연키 UNIQUE(#C1) + `pg_insert(...).on_conflict_do_nothing(index_elements=...)` — 1단 깨져도 silent no-op.
- fail-open 의존성: 시계열 metric 7테이블 UNIQUE 제약(#C1) 누락 시 멱등성 보장 자체가 깨짐. 모델 변경 시 검증 필수.
- at-most-once 트레이드오프: SET NX는 DB 커밋 이전 실행 → 커밋 전 크래시 시 broker 재전송이 idempotent 충돌로 silent 드롭 가능. 한계·outbox 대안: `docs/explanation/tradeoffs.md` T1.

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
- 표시 표준 단일 진실 — `docs/reference/web/static-assets.md` 다음 절: 표준 컴포넌트 카탈로그 / 폰트 위계 / 폰트 체 / 시간 표기 / 네비게이션 규약 / 링크 포맷 / P3 정공 예외 (1회 fetch vs polling 흐름).

### P4. 클라이언트 차트 JS는 P3 명시 예외

브라우저 인터랙션(range 토글·anchor 변경·legend) 즉시 반응 필요라 동적 시각화에 한해 JS 연산 허용.

- 허용: 버킷 그리드 생성·라벨 포매팅·Chart.js 옵션 조립·표시 단위 결정(B/s·kB/s·MB/s).
- 금지: 비즈니스 임계값 분류·API 응답 통계 재계산 (서버 `agg=avg|max|p95` 파라미터로 요청).
- 비동기 차트 로더 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/reference/web/static-assets.md` "P4 차트 JS 5 의무 규약" 절 단일 진실.

## E2. 데이터 흐름 결정

- DTO(dataclass)와 ORM 모델 분리 — 변환은 repository 책임.
- inventory upsert·metrics 저장·server_id 조회 모두 `agent_id` 단일 키 기준 (#C1). 미등록 metrics는 drop.
- `last_seen_at`은 `ServerDetail`(단일 조회)에만 포함. `ServerSummary`(목록)는 Redis `online:{id}` TTL로 표시.
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드.
- 내부 인터페이스 = `interfaces` JSONB raw 저장(#B, P1). 인바운드는 `ip_interface` 형식 검증만. 표시 파생은 mapper(P2) — 서버 IP·네트워크 토폴로지(`build_network_topology`, L3 subnet 공동소속 추론 그래프 — 실측 reachability 아니라 caveat 노출 #E9). 상세 = `docs/reference/web/services.md`.

Pagination 정책:
- 목록 endpoint(`list_servers` 등 정적 row): page 기반 — `page=1`, `limit=20` (max 100). 라우터 Query Pydantic 검증.
- 시계열·시간 흐름 endpoint(`metric_snapshots` / `GET /api/tasks` 등): cursor 기반 — `cursor: datetime | None` + `limit`. 시간 역순 스크롤. page 번호 의미 없음 (계속 새 데이터 들어옴).
- 응답 envelope에 `total_count` / `has_more` 미포함 — `SELECT COUNT(*)` 별도 쿼리 비용 + UX는 빈 결과로 자연 종료 신호.
- 신규 목록 endpoint 추가 시 위 두 패턴 중 하나 선택 — 정적 row면 page, 시간 흐름이면 cursor.

다이어그램 / 라우터 모듈 표 / SSR 페이지 표 / JSON API 표: `docs/reference/web/layering.md` + `docs/reference/web/routers.md`.

## E3. 서비스 계층·ViewModel·Mapper (P2)

서비스 모듈 카탈로그·`mappers/` sub-package 표시 파생 집중 (`server`(+`infer_role`)/`metric`/`attention`/`report`/`task`/`shared`/`environment_report`/`report_history`/`topology` + JSON API 매퍼 `api_reference`/`assessment_api`/`right_sizing_api` = 12 sub-module)·`enrich_*` idempotent·UI badge 임계값(`_USAGE_DANGER_PCT`·`_USAGE_WARN_PCT` — `mappers/shared.py`)·USE Method right-sizing 임계값(`assessment_engine/recommendation.py` 도메인 모듈 — web 공용 import)·ViewModel 카탈로그·mapper 파생 필드(`is_well_known`·`badge_class`·`bar_color` 등)·`cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화: `docs/reference/web/services.md` · `docs/reference/web/view-models.md` 단일 진실.

본 절 결정:
- 두 임계 도메인(UI badge / USE Method) 혼용 금지.
- 신규 ViewModel 파생 필드 추가 시 #F9 영향도 체크리스트 적용.
- right-sizing 분류 단일 진실 = `recommendation.rollup_host(stats) -> HostAssessment` (자원 5개 per-resource USE + 인과 근본원인 종합). 배지 = `classify_host` = `host_status_to_recommendation(rollup_host().host_status)`. 네트워크 혼잡은 host under 아닌 별도 `network_congested` 플래그.
- saturation 3축은 os-aware helper(`cpu_saturated`·`mem_saturated`·`disk_io_saturated`) 단일 진실 경유 의무 — 임계 재계산·직접 해석 금지. `if raw.swap_used` 등 raw 직접 해석 금지. 윈도우 분류·환경·보고서(dual-gate)가 이 3축 verdict helper를 쓰고, 실시간 순간 스냅샷은 목적상 sibling single-gate helper(`cpu_saturation_index`·`mem_pressure_active`·`disk_io_saturation_index`·`net_signal_active`, 동일 `RS_*` 상수 재사용)를 경유한다 — 두 경로의 미세 원자료·경계 불일치는 의식적 유예(tradeoffs T20), 실시간 sibling helper 사용은 본 규범 위반 아님.
- triggers·stats 재사용 의무 — report 진단(`_build_diagnosis`, host.resources 상태·trigger 파생)·권고(`under_prescription(host)`)·attention 자원 부족 카드·서버목록·도넛이 `rollup_host` + `build_resource_stats` 공용 입력을 쓴다 (화면 간 분류 정합, 임계 재계산 0).
- 분류 명세·판정 순서·합성 규칙·임계값·출처·OS 분기·미관측(unmeasured) 처리·한계 단일 진실 = `docs/reference/right-sizing.md`.

## E4. URL 식별자 — 정수 PK 노출 금지

라우터 path 파라미터는 `public_id` (UUID). 구현 메커니즘(UUID 타입 선언·422/404 분기·`resolve_internal_id` Depends 브릿지): `docs/reference/web/layering.md`.

## E5. Jinja2 인프라

`Jinja2Templates` 단일 인스턴스 + 필터 등록은 `web/templating/setup.py`에 격리. 라우터는 `from assessment_engine.web.templating import templates` 만. Redis 캐시 datetime은 `datetime.fromisoformat()`로 파싱(`json.loads` str 그대로 두면 `kst` 필터 오작동).

Jinja2 필터 카탈로그(`kst`/`disksize`/`kbps`/`service_badge_class`/`or_dash`): `docs/reference/web/services.md`.

## E6. 정적 자원 — JS 외부화 의무 + 타입 계약

디렉토리 구조 / `chart-utils.js` base.html 단일 로드 / `ChartUtils` API / 페이지별 .js: `docs/reference/web/static-assets.md`. 외부화 강제 채널: #F5.

본 절 결정:
- 클라 JS 는 서버 ViewModel 과 타입 계약을 컴파일 강제한다 — FastAPI OpenAPI -> 생성 TS 타입(`static/js/generated/api.ts`) -> `// @ts-check` 클라 JS 를 `tsc --checkJs`. 파일별 점진 채택(`// @ts-check` opt-in), 핵심 강제 지점은 `fetch('/api/...')` 응답을 생성 타입으로 annotate 하는 fetch 경계. 메커니즘·확장·CI 게이트 단일 진실 = `docs/reference/web/type-contract.md`.
- 서버 JSON 엔드포인트는 응답 타입을 선언한다(`response_model=` 또는 return 어노테이션) — 생성 타입의 원천. 엔드포인트/ViewModel 변경 시 `pnpm run codegen` 으로 `api.ts` 재생성·커밋(CI drift 게이트).
- 클라는 서버 파생을 재계산하지 않는다(P2 보존) — 통계·분류·단위 변환은 서버 props 로만. 인터랙션 파생(차트 range 토글 등)만 예외(P4).

## E7. 도메인 분류 책임 (P2)

서비스 카테고리 분류(`classify`)·포트 매핑(`matched_ports`)·카테고리 집합 사전계산(`compute_service_categories`)은 도메인 모듈 `assessment_engine/service_classifier.py`(web·consumer 공용 — `recommendation.py` 동급 도메인, web 역의존 0). `MatchedPort` 도 본 모듈 정의(web view_model 이 re-export). ingest(consumer)가 inventory upsert 시 `compute_service_categories` 로 카테고리 집합을 산출해 `server_inventory.service_categories`(text[]) 에 저장하고, 모든 read 경로(목록·상세·리포트·필터)가 저장값 소비. 매퍼가 호출해 `ServiceItem`에 채움. 템플릿은 `service_badge_class` 필터로 category → CSS 클래스 변환만(P3).

본 절 결정:
- 카테고리 규약 단일 진실 = `SERVICE_CATALOG`(`CategoryDef`). 분류 키워드·포트·드롭다운·뱃지 CSS·템플릿 범례가 모두 본 카탈로그 파생 — 서비스 추가는 카탈로그 1곳만 수정. 분산 정의 부활 금지.
- 서비스 분류는 이름·comm·포트 다중 신호를 정밀도 순으로 쓰고, 포트 신호는 해당 unit 에 귀속된 포트에만 적용 — 호스트 전체 포트로 unit 을 분류하지 않는다(services 탭 multi-service 오분류 방지).
- 호스트 카테고리 집합 = ingest 사전계산 `service_categories` — 모든 read 경로(목록·상세·리포트·필터)가 이 저장값 소비 (화면 간 재계산·불일치 0). 특징 워크로드만(baseline OS 기본 서비스 제외), 상세는 live classify 로 전부. 카운트 경로는 `workload_category_counter` (동일 분류). `single_instance`(container) = 호스트당 1.
- 본 `classify`(서비스 카테고리)와 `recommendation.classify`(USE Method right-sizing) 혼용 금지 — 다른 함수.

키워드 매칭·카탈로그 파생 / 다중 신호 우선순위 / opaque 이름 한계(T15) / 서비스 3단계 표시 계층: `docs/reference/web/services.md` "서비스 분류" 절.

## E8. 차트·도넛 UI 디테일 (P3·P4 적용)

차트 Y축·suggestedMax·avg+max ghost·P4 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/reference/web/static-assets.md`. ViewModel 필드(`dash_length`/`dash_offset`/`bar_color`)·SVG 원주 상수(`_UTIL_DONUT_CIRC`)·색 상수 카탈로그(`_UTIL_COLOR_GAUGE/NONE`(활용률 게이지 단색)·`_DONUT_SEGMENT_DEFS`): `docs/reference/web/view-models.md` "신호 임계값 단일 정의" 절.

본 절 결정:
- 차트 Y축은 분해력(추이) vs 절대 기준(진단 리포트) 두 정책 중 선택. magic number 금지(명명 상수).
- SVG `stroke-dasharray`·`stroke-dashoffset` 비례 산술은 mapper precompute — 템플릿은 raw 값만 삽입.
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·프로비저닝 분포·capacity trigger 일관).
- 모든 카테고리 항상 노출(count 0 포함). 비활성은 동일 슬롯 옅은 회색. (도넛 카테고리는 #E9 일반 원칙의 한 사례 — 발화 없는 카테고리도 범례에 노출.)
- 도넛 중앙 라벨은 도넛 유형별 의미로 통일: 구성 분포(워크로드·OS 등)=합계 / 포화=발화 호스트 수(count, 표본은 하단) / 이용률=대표 % 값. 분류(risk) 분포는 중앙 라벨 없는 막대(provisioning_dist_bar)로 렌더. 각 유형은 자기 맥락의 단일 중앙 표기만.

## E9. 발화 가능 정보 노출 (discoverability, P3 적용)

> 조건부로만 채워지는 정보(운영 신호·right-sizing 분류·언더 프로비저닝·capacity 임박·표본 부족 등)는 데이터가 없을 때도 "그 정보가 존재할 수 있다"는 사실을 사용자가 인지할 수 있어야 한다. 처음 보는 사용자가 화면만으로 기능 범위를 판단 가능해야 함.

본 절 결정:
- 발화/조건부 섹션은 데이터가 없어도 제목·카테고리를 노출. 섹션을 통째로 `{% if %}`로 숨기지 않음 (E8 도넛 카테고리 항상 노출이 이 원칙의 한 사례).
- 빈 상태 표시는 단일 컴포넌트 경유: `_shared.html`의 `empty_state(message)` 매크로 + base.html `.empty-state` 클래스 (박스 없는 회색 텍스트 placeholder). 매크로는 dumb — 분기·계산 0, 정적 message만 렌더 (P3). 정상/미수집 의미 구분 안 함 (placeholder 통일 — 구분 로직 복잡도·버그 회피).
- "화면 컨텍스트 가드"와 "데이터 발화 가드" 분리:
  - 컨텍스트 가드(유지) — 그 정보 자체가 무의미한 맥락. 예: 환경 요약·운영신호 집계 위젯은 환경 개요(`/`) 전용이고, 서버 목록(`/servers`)은 행만 — 화면 분리 자체가 컨텍스트 가드.
  - 데이터 발화 가드(placeholder 전환) — `{% if items %}` 류. 비면 사라지지 말고 `empty_state`.
- 적용 범위: 대시보드·환경 보고서·서버 상세 등 전 표시 계층. 이미 placeholder("—"·"수집 불가"·"없음")가 있는 셀·항목은 그대로 유지 (중복 전환 금지).
- 신규 조건부(발화) 섹션 추가 시 #F9 체크리스트 적용.

---

# F. 운영 규약

## F1. 타입 어노테이션
- `from __future__ import annotations` 허용 — 순환 import 회피·불필요한 forward-ref 따옴표 제거 목적. 새 파일에 강제하지는 않되(전면 도입 아님), 순환·noise 있는 모듈에 선택 도입.
- `if TYPE_CHECKING:` 블록 허용 — 런타임 미사용 타입 import 격리.
- 단 Pydantic 모델(`config.py`·`consumer/schemas.py`·consumer handler inbound DTO·라우터 body 모델)은 필드 타입을 `TYPE_CHECKING` 블록에만 두지 않는다 — Pydantic v2 는 model build 시 `get_type_hints()` 로 어노테이션을 resolve 하므로 런타임 네임스페이스에 타입이 없으면 `NameError`/`PydanticUndefinedAnnotation`. Pydantic 필드 타입은 런타임 import 유지.
- 시그니처는 정직하게 — 실제로 `None` 을 반환하면 `-> T | None` 으로 선언한다. type checker 억제(`# type: ignore[return-value]`)로 거짓 시그니처를 덮지 않는다.

정적 검사 도구(ruff·pyright) · 편집기 설정 · 경고 대처 · 강제 채널 카탈로그: `docs/guides/conventions.md` 단일 진실.

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

`Settings()` 인스턴스 단일 진실 위치 — import 시점이 아니라 사용 시점에 만든다(alembic 진입점 `migrations/env.py` 만 module-level). import 만으로 설정을 읽으면 비밀번호를 필수 필드로 둘 수 없다:
- `src/assessment_engine/web/settings.py` — `get_web_settings()` (WebSettings) + `get_diagnostic_settings()` (DiagnosticSettings, web 이 task.install 발행 위해 broker 사용)
- `src/assessment_engine/consumer/settings.py` — `get_consumer_settings()` (ConsumerSettings)
- `src/assessment_engine/worker/settings.py` — `get_worker_settings()` (WorkerSettings, 전용 백그라운드 워커 — 보고서 생성·install reaper)
- `src/assessment_engine/db/session.py`·`cache/redis.py`·`migrations/env.py` — 자체 `WebSettings()` (모든 컴포넌트 공통 db layer·캐시·schema 진입점, circular import 회피)

`src/assessment_engine/config.py`는 class 정의만 — module-level instance 0 (multi-node 분리 정합, ADR/문서 패턴 정합).

금지:
- Service/Handler 안 구체 구현체 import.
- Composition Root 외 위치에서 `Settings()` 인스턴스 생성 — 위 6 위치 (web/settings·consumer/settings·worker/settings·db/session·cache/redis·migrations/env)만 허용 — 전부 `src/assessment_engine/` 아래다.
- `assessment_engine.config`에서 Settings 인스턴스 import — class만 export.
- `APP_ENV` 환경 분기를 entry lifespan 외 위치에 추가. 비밀번호 검증은 환경을 가르지 않는다 (#F8·`contracts/env.md` 6절).

추상 인터페이스 카탈로그·새 Repository 절차: `docs/reference/web/layering.md` · `docs/reference/db/repositories.md`.

## F5. 자동화 변환 — 책임 분담

원칙: 자동화 변환(sed · `Edit replace_all` · 디렉토리 mv · Python 일괄 갱신) 직후 검증을 2 채널로 분담. 로컬 훅은 두지 않는다 — 우회 가능한 자리라 강제 수단이 못 된다(`docs/guides/conventions.md` 2절).
- 메인 세션 — 자가 검증. 변환 직후 매 회 의무 (아래 4 항목).
- 에이전트 (code-reviewer / schema-contract-auditor) — 본 절 맥락(변환 직후 점검)에서는 사용자 명시 요청(`리뷰해줘`·`스키마 일관성 확인` 등) 시에만 발동. PR 게이트의 코드 리뷰는 별개 채널이며 배치는 `docs/guides/wrap-up.md` 0절이 정한다 (develop PR = `/pr` 이 code-reviewer 발동).

메인 자가 검증 의무:
1. 옛 패턴 잔존 0건 grep.
2. 새 패턴이 의도된 스코프에만 (함수 외부·의도 외 위치 grep 검증).
3. `.html` 변경 시 신규 inline `<script>` 코드 줄 grep (외부 `.js` 강제).
4. DTO · 매퍼 · `cache_serializer` · 템플릿 · JS 체인 의미적 동기화.

금지:
- 검증 생략 후 다음 단계 진행.
- 사용자 IDE 경고·브라우저 콘솔 발견에 의존.
- 명시 요청 없이 pytest 실행 또는 "테스트 통과"를 검증 결과로 보고.
- 변환 직후 점검에서 메인이 에이전트 자동 위임 제안 (게이트가 규정한 발동은 해당 없음).

에이전트 결과: Error → 즉시 수정 / Warning → 사용자 결정 위임 / Info → 보고만.

변환 유형별 체크리스트·누적 사고 패턴: `docs/guides/conventions.md` 3절·4절.

## F6. 에러 처리·실패 모델

원칙: 외부 의존은 fail-close/fail-open을 컴포넌트 단위로 미리 결정. 결정 근거 없으면 새 통합 도입 금지.

외부 의존별 실패 모드 매트릭스: `docs/reference/observability.md` "외부 의존 실패 모드 매트릭스" 절 단일 진실.

금지:
- `except Exception` 광범위 catch — 예외 타입 명시(`OperationalError`/`IntegrityError`/`RedisError`/`asyncio.TimeoutError` 등). 불가피하면 reraise + 컨텍스트 로그.
- 영구 오류(`IntegrityError`·4xx) 재시도. 일시 장애(`OperationalError`·deadlock(40P01)·5xx·timeout)만 백오프.
- timeout 없는 외부 호출 — `asyncio.wait_for` 또는 클라이언트 옵션(`aiohttp.ClientTimeout`·asyncpg `command_timeout`·redis `socket_timeout`) 의무.

소비자 측 상세 매트릭스: `docs/reference/consumer.md` "DB 재시도 정책" + "메시지 자체 결함 → DLQ" 절.

## F7. 로깅·관측

원칙: 로그는 운영 시그널 — 양이 많으면 시그널이 묻힌다. 레벨·내용·빈도 모두 의도 있게.

레벨별 용도 매트릭스: `docs/reference/observability.md` "로그 레벨" 절 단일 진실.

본 절 결정:
- `loguru` 단일 채택. `print`/`sys.stdout.write`/stdlib `logging` 혼용 금지.
- 예외 로깅은 except 블록 안 `logger.exception()`만 — 자동 traceback. 일반 ERROR는 `logger.bind(...).error(...)`.
- 시그널 로그(`_log_time_invariants`·`_track_agent_restart`)는 쿨다운·슬라이딩 윈도우 의무 — 매 메시지 발생 시 진짜 시그널 매몰.
- 새 시그널 도입 시 (a) 레벨 (b) 빈도 제어 (c) 운영자 행동 — 셋 다 명시.

금지: payload·secret raw dump — 식별자(agent_id·composite_id·routing key·message_id·server_id)와 카운트만.

로그 format: `LOG_FORMAT` env 분기 — `text`(dev colorized) 또는 `json`(prod, loguru `serialize=True`). 각 entry(web/consumer)가 기동 직후 `setup_logging(settings.log_format)` 호출. 단일 진실은 `src/assessment_engine/log_config.py`.

Request/Correlation ID 분산 trace 도입 트리거·정석 패턴: `docs/reference/observability.md` (현재 미적용, 도입 시 별도 ADR 의무).

## F8. 시크릿·PII 노출 금지

원칙: 로그·예외·HTTP 응답·ViewModel·캐시 어디에도 비밀번호·토큰·전체 메시지 payload·고객사 식별 가능 정보 노출 금지. 한 번 새면 영구.

금지:
- pydantic Settings 비밀 필드 `SecretStr` 미적용 — 신규 비밀 필드 의무.
- `.env`·`secrets/*`(실 secret 파일) commit. PR diff `password`/`secret`/`token`/`key` 패턴 검토 의무.
- 예외 메시지에 raw payload·접속 문자열 — catch 후 sanitize 후 reraise.
- HTTP 응답·ViewModel·JSON export에 PII. 운영 식별자는 `public_id`(UUID)만(#E4).
- Redis·DB에 raw payload 캐싱 — Outbound DTO·ViewModel 단계에서 sanitize 후.
- 메시지 payload 본문 로깅 (`agent_id`·`composite_id`는 식별자라 OK).

secret 채널·설정 자동 검증: `docs/reference/contracts/env.md`.

## F9. 변경 영향도 체크리스트

원칙: 영향받는 모든 곳 동시 갱신 의무 — 한 곳만 수정 후 PR 금지.

적용 시점: 게이트는 되돌리기 비용에 비례해 배치한다 (`docs/guides/wrap-up.md` 0절 단일 진실). 로컬 커밋은 lint 만, 테스트·코드 리뷰는 develop PR, 문서·ADR·본 체크리스트의 동시 갱신은 main PR 시점이다. 기능 개발 중간 단계에서는 기능 코드만 작성한다 — 동작 검증은 실행 화면으로 확인(사용자 직접 또는 `/run`)하고, 메인 세션이 기능 추가와 함께 테스트·문서를 선제 작성하지 않는다. (테스트 자동 실행·보고 금지는 #F5 와 일관.)

변경 유형별 동시 갱신 위치 표(시계열/inventory 컬럼·routing key·페이로드 schema·분류 임계·환경변수·ViewModel 파생 필드·JSON API·보고서 스냅샷·조건부 UI·외부 의존·차트 MetricType·비동기 보고서·install task lifecycle) = `.claude/skills/change-impact/SKILL.md` 단일 진실. 해당 유형 변경 시 본 스킬 로드 의무.


## F10. 평가 윈도우 · 차트 시계열 옵션 — 단일 진실

원칙: 보고서·대시보드·차트 모두 같은 평가 윈도우·시계열 옵션 카탈로그 참조 — 화면별 의미 분기 방지.

본 절 결정:
- right-sizing 평가 윈도우 단일 진실 = `recommendation.WINDOW_DAYS` (수치·근거는 `docs/reference/right-sizing-thresholds.md`). 보고서 라우터·서버 목록 분류·환경 개요 자원 적정성 카드·환경 자원 평가 페이지·구간 선택 기본값(`DIAGNOSTIC_DEFAULT_TIME_RANGE`·보고서 발행 select) 모두 본 상수/동일 값 참조 — 분류는 화면 간 한 창 단일(#E3, 화면별 의미 분기 0). 변경 시 `_thresholds_reference.html` 표제도 동기화.
- 윈도우 2분리 기준 (목적별, 임의값 0): (1) 분류·신뢰도·이용률·포화 입력 = 평가 윈도우 창 — p95·burst·steal·coverage·history_hours + 환경 개요 이용률·포화 도넛 전부 이 창(#E3 화면 간 정합, 분류와 한 창 통일). (2) 용량 runway = 가용 이력 전체 — 누적 신호라 길수록 정확, `report_aggregate` mount_span 이 하한 없이 `bucket <= :end` 로 실제 span 기반 산출(#C5 하한 술어 의식적 예외, tradeoffs T18). 앵커 = 라이브 now / 보고서 발행 시점. 서버 상세 차트는 실시간 모니터링이라 별도(globalRange 15m, 평가 윈도우 무관). 실시간 현황 페이지(attention)는 순간 스냅샷(창 무관).
- 다운사이즈 처방 이력 게이트 = 창 대비 관측 비율(`RS_DOWNSIZE_MIN_SUFFICIENCY`, `sample_sufficiency`) — 절대 시간 아님(WINDOW_DAYS 바뀌어도 문턱 불변, 미세 갭 흡수). 통계 정밀도 절대 바닥은 `RS_CONFIDENCE_MIN_HOURS`(하드 컷 아닌 신뢰도 하향 트리거).
- 환경 부하 추이(보고서 SSR 정적 차트) bucket 은 `AUTO_BUCKET[range]` 동적 — 발행 time_range 기준(예: 7d -> 3h, 24h -> 30m). 윈도우 변경 시 집계 단위 자동 추종 — 하드코딩 금지.
- TimeRange/BucketSize Literal 단일 진실 = `db/repositories/query/types.TimeRange`/`BucketSize` + `_BUCKET_INFO` + `chart-utils.js`. 새 range·bucket 도입 시 backend Literal·SQL dispatch·JS 매핑·UI 토글 4곳 동시 갱신 의무.
- range -> 자동 bucket 매핑(`AUTO_BUCKET`)은 backend `types.AUTO_BUCKET` 와 frontend `chart-utils.js` 두 곳 — 값 동기화 의무 (range별 적정 분해력 단일 의미). 신규 TimeRange 도입 시 두 곳 동시 신설. SSR 정적 차트(환경 부하 추이)는 backend 매핑, 동적 fetch 차트는 frontend 매핑 적용 — 둘이 어긋나면 같은 range 가 화면별 다른 bucket.
- 보고서 형태 산출물은 윈도우를 envelope·표제 명시 — JSON Export `period_window{days, start, end}` 의무 필드(#B 동일 원칙).
- install task 배달/마감 창 단일 진실 = `install_task_deadline_sec` (기본 3600) — engine `tasks.deadline_at` 과 broker 큐 `x-message-ttl` 이 동일 창 (엔진 timeout 선언 == 미배달 메시지 만료, zombie 지연 실행 0). agent 실행 예산 `install_timeout_sec`(600, payload `install.timeout_sec`)는 별개 개념..

## F11. Disposability — Graceful shutdown (12-factor IX)

원칙: SIGTERM 시 in-flight 작업 손실 0 + 다음 기동 시 stale 상태 없음.

본 절 결정:
- web — uvicorn `timeout_graceful_shutdown=3s`. 진행 중 HTTP 요청 완료 후 exit. 실시간 메트릭 polling 은 다음 주기 자동 재요청이라 별도 처리 불요. task.install publish 중 SIGTERM은 aio-pika `connect_robust` transaction 보장.
- consumer — `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료. 정상 exit → ACK / raise → NACK + DLQ.
- 전용 워커 프로세스(`assessment_engine.worker`) — 보고서 생성 + install reaper 를 web(HTTP 전담)에서 분리한 별도 컨테이너. `worker/main.py` 가 두 루프를 공유 stop_event 로 병행 구동, consumer 와 동일 asyncio-native SIGTERM(`loop.add_signal_handler`) graceful. `signal.signal`·`os._exit` 금지(아래 일관).
- 보고서 생성 루프 — SIGTERM 시 stop_event 로 새 claim 중단 + 진행 중 1건은 `report_worker_shutdown_timeout_sec` 안 drain, 미완은 running 잔류 -> 다음 기동 `recover_stale_running` 가 pending 으로 회수(in-flight 손실 0). job 상태는 DB(`diagnostic_jobs`)라 메모리 손실 없음.
- install task reaper 루프 — SIGTERM 시 stop_event 로 tick 중단(진행 중 UPDATE 1건 짧아 즉시 drain). deadline 경과 pending 을 emit 무관하게 `expire_all_overdue_tasks` 로 failure(timeout) 전역 전이 — task 상태는 DB(`tasks`)라 메모리 손실 0.

금지:
- `signal.signal(SIGTERM, ...)` 직접 핸들러 — uvicorn/asyncio 자체 처리, 중복은 종료 race.
- `os._exit()` — graceful shutdown 우회.
- `message.process()` 컨텍스트 밖 await — ACK/NACK 둘 다 안 됨.

consumer 측 상세: `docs/reference/consumer.md` "Disposability" 절.

---

## F12. 문서·주석 현황 선언성

원칙: 영구 문서(`docs/reference/`·`docs/guides/`·`docs/explanation/`·루트 `README.md`)와 코드 주석은 현재 상태만 선언적으로 기술한다. 변경 시 과거 흔적(폐기된 도구·용어·구조·경위)을 제거하고 현황으로 덮는다 — "이전엔 X 였다"·"Y 에서 전환" 회고형 서술 0.

본 절 결정:
- 도구·구조 전환 시 옛 이름·경위를 코드 주석·영구 문서에서 제거. 전환 직후 폐기 토큰 `rg` 0 검증 의무(주석 포함).
- 예외 — `docs/decisions/adr/` (결정 변경 = 새 ADR + 이전 `Superseded by`, 역사 기록 보존 — ADR 불변 규약) · `docs/explanation/tradeoffs.md` (의식적 한계·확장 트리거).

주석 규약 (일반 컨벤션):
- 주석은 why 만 쓴다. what·how 는 코드가 말한다 — 코드를 옮겨 적은 주석은 코드가 바뀌면 주석만 낡는다.
- docstring 은 PEP 257 — 모듈·클래스·공개 함수에 한 줄 요약. 상세는 자명하지 않은 계약(반환 의미·예외·부수효과)이 있을 때만. private(`_prefix`)은 기본 생략.
- 인라인 주석은 코드만으로 판단이 안 서는 지점에만. 외부 제약(계약·OS·라이브러리 동작)이 대표 사례다.
- 정책·규약 서술은 주석에 넣지 않는다. 문서 위치만 가리킨다 — 같은 정책이 두 곳에 있으면 갈라진다.
- 규약 절 번호(`#F8` 등) 인라인 인용 최소화. 그 절이 왜 여기 적용되는지가 자명하지 않을 때만.
- 주석 처리된 코드 0. VCS 가 기억한다.
- 주석 분량이 코드를 넘으면 문서로 갈 내용이 섞인 신호다.

주석으로 설명해야 이해되는 코드는 주석을 늘리지 말고 코드를 고친다 (명명·함수 분리·상수화).

금지:
- 영구 문서·코드 주석에 회고형 서술("과거엔"·"이전 방식"·"~에서 전환했다")·폐기 도구/용어/경로/기본값 잔존 — ADR·tradeoffs 외.
- 코드로 알 수 있는 사실(시그니처·디렉토리 트리·라인 수) 문서 중복 (#F9 단일 진실 원칙과 동렬).

검사: 도구·구조 전환·기능 폐기 시 옛 토큰 `rg` 0 (코드 주석 포함). 위반 발견 시 현황 선언으로 즉시 정정 (덮어쓰기, 경위 서술 추가 X).

---

## F13. 파이프라인 용어 — 통칭 금지

원칙: 자동화 단계를 통칭하지 않는다. CI·릴리즈·배포는 트리거도 산출물도 다르므로 어느 것을 말하는지 매번 지정한다. 문서와 대화 모두 적용.

| 표현 | 가리키는 것 |
|------|------------|
| 워크플로 · 잡 · 스텝 | GitHub Actions 의 파일 · 병렬 단위 · 명령 |
| CI | 검증 워크플로 — `ci.yml` · `alembic-check.yml` · `codeql.yml` · `pr-title-check.yml` |
| 릴리즈 워크플로 | `release.yml` (이미지 빌드·서명·태그). 분류는 Continuous Delivery |
| 배포 (rollout) | 배포 대상 VM 에서 `deploy.sh` 실행 |
| 이미지 빌드 / wheel 빌드 | 항상 어느 쪽인지 명시 |
| 배포 산출물 | GHCR 이미지 단일. wheel 은 CI 패키징 검증용이라 제외 |

금지:
- "CI" 를 러너에서 도는 자동화 전반의 통칭으로 사용 — 전체를 가리킬 때는 "워크플로".
- 수식 없는 "빌드" — 이미지 빌드와 wheel 빌드가 다른 워크플로다.
- wheel 을 배포 산출물·배포처와 같은 층위로 나열.

워크플로 책임 카탈로그는 루트 `README.md` "CI 파이프라인" 절, 발화 조건·required check 는 `docs/guides/ci-setup.md` 3.4 소유.

---