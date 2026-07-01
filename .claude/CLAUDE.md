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
| `docs/tradeoffs.md` | 의식적 설계 선택과 그 한계 (T1~T15) | 영구·갱신 |

그 외 명시되지 않은 경로의 문서는 코드·영구 문서에서 인용 금지.

`docs/temp/` 디렉토리 — 임시·외부 공유 자료 모음 (협의 input 등). 디렉토리 위치 자체는 영구이나 안의 파일은 임시 (자유 작성·삭제). 양방향 의존 0 의무 — 본 repo 영구 문서·코드가 temp 인용 금지, temp 안 파일 자체도 본 repo 영구 문서·코드 의존 금지 (외부 공유 시 self-contained 필수). 인덱스 표에 추가 안 함.

---

# A. 시스템

ZConverter Cloud Assessment Portal — 고객사 내부 네트워크 호스트 인벤토리·메트릭 수집·저장·분석(right-sizing·규칙 기반 진단) B2B 내부 포털. 시스템 소개·아키텍처 그림은 루트 `README.md` 단일 진실.

## A0. 범위

본 repo는 엔진 애플리케이션 + docker compose 배포 + 엔진 rollout(`deploy.yml` self-hosted runner)까지 다룬다 (ADR 0048). 배포 대상 VM 자체의 provisioning(IaC — Terraform·Ansible·VM 생성·OS 설정)은 범위 밖 — docker engine 설치는 1회성 `bootstrap.sh` 로 편의 제공. (ADR 0006 Withdrawn 은 IaC 전면 배제였고, 0048 이 rollout 을 범위 안으로 재획정.)

본 절 결정:
- compose 파일 — prod-safe base(`docker-compose.yml`) + dev override(`docker-compose.override.yml`) (ADR 0035) + prod file-secret overlay(`docker-compose.secrets.yml`, ADR 0046). base 는 `build:` 키 없는 이미지 pull(GHCR 핀)·bind mount 없음·볼륨 env 바인딩(`PGDATA_HOST`·`MQ_DATA_HOST`) = 빌드 없는 pull-and-run prod compose. override 는 dev 전용(소스 빌드·`./src` bind mount·hot reload)으로 `docker compose up` 시 base 에 자동 머지(릴리즈는 override 미배포, prod 는 base+secrets). Dockerfile 은 dev/prod 분리 안 함(단일 multi-stage 이미지, dev-prod parity) — dev 편의는 Dockerfile 이 아니라 override compose 의 bind mount 로만 주입. `docker-compose.prod.yml`(prod 환경 전체를 가르는 compose)은 두지 않는다(base 자체가 prod). prod 비번은 file-secret 채널 단일(ADR 0046) — `docker-compose.secrets.yml`(`./secrets/*` -> `/run/secrets/*`)을 `env.example` 의 `COMPOSE_FILE` 로 base 에 자동 머지. 즉 prod = base+secrets, dev = base+override. hardened prod(APP_ENV=prod·강 secret·LOG_FORMAT=json·HTTPS ingress)는 infra env 주입으로 달성.
- prod 외부 인프라가 활용할 수 있는 정석 contract만 본 repo에서 유지:
  - 환경변수 contract — `docs/operations/env.md` 키 카탈로그
  - secret 채널 추상화 — `SecretStr` 강제 + pydantic `secrets_dir` (`SECRETS_DIR` env로 override 가능) + env var 둘 다 지원. 외부 인프라가 systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등 어떤 채널을 써도 본 엔진 동작
  - 환경 분기 — `APP_ENV=prod` + `_validate_prod_*` weak default 거부 (`docs/operations/env.md` 8절). secret 주입 방식은 무관, 결과(약한 default 거부)만 검증
  - CI 산출물 — 서명(cosign)·SBOM(SPDX)·provenance 된 OCI 이미지 단일 (GHCR, ADR 0048). 배포는 `deploy.yml` rollout (cosign verify -> compose pull -> migration -> up -> health -> rollback)
- VM provisioning 코드(`*.tf`·Ansible playbook·VM 생성·OS 설정)는 본 repo에 두지 않는다 — 배포 대상 VM 은 provisioning 완료 상태를 전제. 엔진 rollout(`deploy.yml`)·docker engine 부트스트랩(`bootstrap.sh`)은 범위 안(ADR 0048). 단일 호스트 compose 수동 기동도 지원 (ADR 0035·0036).

---

# B. 메시지 데이터 계약 (양방향)

메시지 데이터 형식·필드 카탈로그·task.install / task.result 흐름·수신/발행 routing key 카탈로그: `docs/architecture/agent.md`. MQ 토폴로지·큐 정책: `docs/architecture/rabbitmq.md`. 채택 사유: ADR 0007 (Task 별도 큐 모델 — 0002 supersede).

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
- `server_inventory` 식별 분리 (ADR 0022 -> 0027 정정, agent v4): `id bigint PK` (FK 대상) / `composite_id varchar(64) UNIQUE` (agent 매칭·식별 단일 키 — SHA-256 composite hash) / `machine_id varchar(64)` (raw machine-id 표시 전용, nullable — 평시 식별·라우팅 미사용) / `public_id UUID UNIQUE` (URL 노출) / `hostname` display (UNIQUE X). 시계열 5 테이블 FK = `server_id bigint`. MQ queue `agent.tasks.{composite_id}` / routing key `task.install.{composite_id}`.
- composite_id 재연결 (ADR 0044): inventory upsert 시 composite_id 미등록이면 `_relink_rebooted_host`(machine_id + hostname 일치, 후보 정확히 1개)가 기존 행 composite_id 를 re-point — 부팅마다 NIC MAC 재발급(OpenStack Windows VM)으로 composite_id 가 바뀌는 같은 VM 의 중복 행 흡수(server_id·시계열·history 보존). machine_id 는 평시 식별 미사용이나 본 fallback 한정 키. 후보 0/2+ (모호한 clone)면 미연결(오병합 방지). MAC 은 부팅마다 변동이라 매칭 미사용.
- `diagnostic_jobs.job_type` (`customer_report`/`engineer_report` 둘만) + active partial UNIQUE = `(scope, input_hash, job_type)`. 발행 시점 정적 스냅샷을 `result` JSONB 에 보존. customer/engineer 모두 비동기 생성 (pending -> 워커 claim·running -> succeeded/failed, ADR 0040). status·progress_stage·started_at·error_message + active partial UNIQUE 가 비동기 상태머신.
- 보고서 발행 = 정적 스냅샷 (요구: 발행 시점 데이터 그대로 보관, 이력 동적변화 0). POST `/reports/environment/emit` · `/reports/servers/emit` 가 `enqueue_report` 로 parent job 을 pending enqueue 후 즉시 `?job={id}` 반환(생성 안 함, 더블클릭은 active UNIQUE 로 기존 job 합류). web lifespan 의 job-claim 워커(`report_worker.py`)가 `claim_pending`(FOR UPDATE SKIP LOCKED) -> `report_generator.build_report_result_for_job`(발행 시점 ViewModel 생성·`report_serializer` 직렬화) -> `result` JSONB(`{kind,snapshot,view,aux}`) 저장 + succeeded (생성 불가 시 failed, ADR 0040). 응답 view_url=`?job={id}` — JS navigate. GET `?job={id}` 는 succeeded 면 저장된 스냅샷 정적 렌더(재계산 0), pending/running 이면 진행 화면 + `report-poll.js` 폴링(`GET /reports/{job}/status`) -> 완료 시 reload, failed 면 안내(`reports/report_pending.html`). 환경 보고서(`/reports/environment`)는 job 없는 GET 이 컨트롤만(양식·윈도우·앵커 select + 발행 버튼) 노출. server scope(`/reports/servers`)는 job 없는 GET 이 read-only live preview (진단 트리거 없음). result 구조 단일 진실 = `diagnostic.report_result`.
- 양식 통일: server scope 단일/N대 모두 환경 보고서 양식 (`EnvironmentReportSummary`, kind=env_report) 공유. N대 selection (`servers/report.html`) = 환경 보고서 본문 공유 partial (`reports/_env_report_body.html`, environment.html 과 단일 진실) + 하단 세부 서버 목록 표 (`show_report_link` selection 한정 — 환경 보고서는 세부 서버 목록 미표시, 500대 인쇄 폭주 회피, 조치 대상은 효율화/리소스 부족 표가 담당). 단일 1대 (`servers/single_report.html`) 는 customer high-level / engineer 심화 (1대 deep-dive — N대 비교 표엔 없는 CPU 분류·메모리 구성·마운트별 스토리지 전개; 단일 전용 필드 `server_inventory`·`volumes`·`memory_breakdown`·`cpu_breakdown` 는 selection·환경에서 None/빈 list, repo `report_cpu_breakdown`·`report_memory_breakdown`·`report_mount_usage` per server_id). 환경 (`reports/environment.html`) 은 high-level. selection ViewModel = `query_service.get_selection_report(server_ids)` (단일은 N=1 동치, 평균 활용률은 `environment_utilization` 을 server_ids 로 N대 한정 호출 — 전체 환경과 동일 capacity-weighted SQL, attention 은 N대 호스트 필터). `report_summary` 단독 표 양식·kind 폐기. `/reports/servers/emit` 은 ids 1개면 단일, 2개+ 면 selection.
- 선택 N대 발행(`/reports/servers/emit`, ids 2개+)은 워커가 parent(selection, env_report) 1건 처리 단위 안에서 개별 단일 보고서 N건 + selection 본문을 생성 — child 전부 성공 후에만 parent succeeded(부분 누락 차단), 중간 실패는 parent failed. 세부 서버 목록 hostname -> 개별 보고서 정적 link `child_jobs`(이력 개별 조회). ids 1개는 단일 보고서 1건.
- 보고서 운영신호 정책 (engineer): 표시는 OS 지원종료(os_eol)만 (`attention.os_eol_warnings`). 재부팅(`report_uptime_stats`)·에이전트 재시작(`report_agent_restart_stats`)은 보고서 anchor+window 안 카운트(boot_time/agent_started_at DISTINCT-1)해 세부 서버 목록 표 "시스템 안정성" 컬럼에 표시 (세부 서버 목록은 selection 한정이라 환경 보고서엔 미표시). `get_attention_signals` 의 전역 gap/agent_unstable 신호는 window-scoped 보고서에 미표시 (의미 불일치 회피).
- 보고서 요약 섹션은 customer/engineer 동일 (`_env_summary_bullets` view 무관 단일): 등록 서버(+vCPU/메모리/디스크) / 온라인·오프라인 / 자원 적정성 분류 분포 / 자원 부족(active trigger 원인별 `_under_cause_summary`) / OS 지원 종료. bullet 끝 마침표 없음.
- 자원 적정성 본문 구조 (engineer): 분포 막대(소제목 "분류 분포 (N대)") + 효율화 검토 대상 표(`efficiency_hosts` — over/idle/shutdown 정리 시급도 순 Top 30, 호스트·분류·진단·신뢰도. 권고 칼럼 폐기 — 분류와 1:1. `_select_efficiency_targets`) + 리소스 부족 표(`under_provisioned_hosts` 6축 메트릭 + 권고(`CapacityWarningItem.recommendation_action` = `report._build_under_provisioned_reason`) + 신뢰도). 조치 호스트 노출은 두 표가 단일 진실(전수 위험도 종합 표 없음). customer "조치 필요 호스트"(high 만)는 유지. 네트워크 토폴로지는 보고서에선 정적 서브넷 요약 표(`topology.subnets` SubnetGroup — net_key·host_count) — 인터랙티브 Cytoscape 그래프는 화면 토폴로지 페이지(`/environment/topology`) 전용. 서비스 구성은 별도 카드 — "서비스 식별 (N대)"·"서비스 미식별 (M대)" 소제목 하위 카테고리별 칩(전 카테고리 노출 #E9·`ServiceCatalogGroup.total_count`). OS 구성(Linux/Windows, 0대 포함 #E9)은 환경 요약(customer)·환경 현황(engineer) 카드 하위 metric-card 소제목(`.env-stat-card` 인벤토리·메트릭과 높이 통일).
- 이력 표시: 보고서 발행 이력 `/reports/history` (customer + engineer union, view 필터). 재조회 link = `?job={id}` 정적 스냅샷 (scope 별 라우터 분기).

## C2. Repository 계층 — 인터페이스 우선 (#F4)

추상 인터페이스(`BaseCollectRepository`/`BaseQueryRepository`/`BaseDiagnosticRepository`) · DTO 흐름(Inbound Pydantic·Outbound raw dataclass) · INSERT 통일(`pg_insert` + `on_conflict_do_*`) · `list_servers` 부분 SELECT 정책 · repo 메서드 카탈로그 · asyncpg 함정 · `_chart_*` 패턴: `docs/architecture/db/repositories.md` · `docs/architecture/db/dtos.md` · `docs/architecture/db/timescaledb.md` 단일 진실. `Settings()` 인스턴스 사용 절차는 #F4 단일 진실.

## C3. Redis 전략 — fail-open 의무

키 설계 표 / TTL 근거 / 캐시-aside race 한계 / 평시·장애 동작 매트릭스 / mget 효율 패턴: `docs/architecture/redis.md`. 의사결정 ADR: `docs/adr/0001-redis-decoupling.md`.

본 절 결정:
- 모든 Redis 호출은 `src/assessment_engine/cache/redis.py`의 `safe_*` helper(`safe_get`/`safe_set`/`safe_set_nx`/`safe_delete`/`safe_mget`/`safe_incr_with_ttl`) 경유. RedisError 시 silent fallback + warning 로그. 직접 redis client 호출 금지.
- fail-open 보장 의존성: 멱등성 1단 fail-open(#D2) → DB UNIQUE(#C1)가 중복 흡수. UNIQUE 누락 시 보장 자체가 깨짐.

## C4. 스키마 변경 — Alembic 단일 진실

모든 환경(dev·staging·prod·테스트) Alembic 단일 진실 / `migrate` init-container 패턴 / 모델 변경 시 동시 갱신 워크플로 / 라운드트립 검증 / autogenerate 미지원 카탈로그 / `_include_object` filter / CI `alembic check` / testcontainers + alembic / Backward compatibility 단계: `docs/operations/alembic.md` + ADR 0005 단일 진실.

본 절 결정:
- 시계열 신규 테이블 추가 시 마이그레이션에 `op.execute("SELECT create_hypertable(...)")` 보강 + 자연키 UNIQUE(#C1) + `boot_time`/`agent_started_at` 컬럼(#B) 동시 검토.
- continuous aggregate(ADR 0043): 마이그레이션은 `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous, timescaledb.materialized_only=false) ... WITH NO DATA` + `add_continuous_aggregate_policy`(둘 다 트랜잭션 내 OK, TimescaleDB 2.27). 초기 materialize(`refresh_continuous_aggregate`)는 트랜잭션 밖 전용이라 마이그레이션 외 1회(real-time aggregation 이라 refresh 전에도 값 정확). cagg 정의에 박힌 필터(물리 device 등)는 types 필터 스냅샷 — 규약 변경 시 cagg 재생성 마이그레이션 동반.

## C5. 쿼리 안전성

본 절 결정:
- hypertable 조회는 `WHERE collected_at >= ?` 술어 의무 — partition pruning. 누락 시 모든 chunk full scan. `_chart_*` 헬퍼·repo 메서드 모두 적용. continuous aggregate 조회는 `WHERE bucket >= ?`(동일 pruning).
- 카운터 메트릭(CPU jiffies·disk/net bytes) 집계는 continuous aggregate + timescaledb_toolkit `counter_agg` 사전집계 단일 진실(ADR 0043). counter reset(재부팅·agent재시작·wraparound)은 `counter_agg` 가 값-감소 기준 일률 처리 — 보고서 집계(`report_aggregate`·`report_*_baseline`·`report_cpu_breakdown`)에서 hand-rolled LAG + boot_time gate 부활 금지. 차트(`metric_trend`, 동적 버킷)는 목적상 raw 유지.
- raw SQL의 사용자 입력은 `text()` + bound parameter만. f-string으로 사용자 입력 직접 삽입 금지 — SQL injection + asyncpg statement cache 키 폭증. dispatch table whitelist 상수(Pydantic Literal → enum 매핑 정적 상수)는 f-string 허용.
- 트랜잭션 경계: consumer는 1 메시지 = 1 트랜잭션 (`session_factory()` 컨텍스트), web은 1 request = 1 세션 (`Depends(get_session)`). autocommit 금지·세션 공유·중첩 금지.

---

# D. Consumer

## D1. 구조·후처리·실패 처리

aio-pika 비동기 컨슈머(FastAPI 독립 프로세스) · 4 routing key 핸들러 팩토리 · `composite_id` 단일 키 식별 (#C1) · `ensure_server_id` placeholder 분기 · auto-register · `_db_retry` 백오프 · routing key별 후처리 시퀀스 · 부가 시그널(`_log_time_invariants`·`_track_agent_restart`) · 실패 분기·DLQ 운영: `docs/architecture/consumer.md` 단일 진실.

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
- 표시 표준 단일 진실 — `docs/architecture/web/static-assets.md` 다음 절: 표준 컴포넌트 카탈로그 / 폰트 위계 / 폰트 체 / 시간 표기 / 네비게이션 규약 / 링크 포맷 / P3 정공 예외 (1회 fetch vs polling 흐름).

### P4. 클라이언트 차트 JS는 P3 명시 예외

브라우저 인터랙션(range 토글·anchor 변경·legend) 즉시 반응 필요라 동적 시각화에 한해 JS 연산 허용.

- 허용: 버킷 그리드 생성·라벨 포매팅·Chart.js 옵션 조립·표시 단위 결정(B/s·kB/s·MB/s).
- 금지: 비즈니스 임계값 분류·API 응답 통계 재계산 (서버 `agg=avg|max|p95` 파라미터로 요청).
- 비동기 차트 로더 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/architecture/web/static-assets.md` "P4 차트 JS 5 의무 규약" 절 단일 진실.

## E2. 데이터 흐름 결정

- DTO(dataclass)와 ORM 모델 분리 — 변환은 repository 책임.
- inventory upsert·metrics 저장·server_id 조회 모두 `composite_id` 단일 키 기준 (#C1). 미등록 metrics는 drop.
- `last_seen_at`은 `ServerDetail`(단일 조회)에만 포함. `ServerSummary`(목록)는 Redis `online:{id}` TTL로 표시.
- `CollectionStatusItem`은 `last_metric_at` + `last_inventory_at` 별도 필드.
- `ip_internal`은 CIDR 표기 문자열 raw 저장(agent v3.4+, #B). 인바운드는 `ip_interface` 형식 검증만(bare·CIDR 호환). 표시 파생은 `mappers/server._to_ip_addrs`(`ip_interface` 파싱)와 대시보드 네트워크 토폴로지(`mappers/topology.build_network_topology`)에서 — L3 subnet 공동소속 추론 그래프(노드=subnet/host, 가상망·IPv6·단독 subnet 제외). 추론이라 실측 reachability 아님 — 한계는 caveat 노출(#E9). Cytoscape.js(vendored)로 렌더, 자동갱신 fragment 안이라 swap 후 재초기화.

Pagination 정책:
- 목록 endpoint(`list_servers` 등 정적 row): page 기반 — `page=1`, `limit=20` (max 100). 라우터 Query Pydantic 검증.
- 시계열·시간 흐름 endpoint(`metric_snapshots` / `GET /api/tasks` 등): cursor 기반 — `cursor: datetime | None` + `limit`. 시간 역순 스크롤. page 번호 의미 없음 (계속 새 데이터 들어옴).
- 응답 envelope에 `total_count` / `has_more` 미포함 — `SELECT COUNT(*)` 별도 쿼리 비용 + UX는 빈 결과로 자연 종료 신호.
- 신규 목록 endpoint 추가 시 위 두 패턴 중 하나 선택 — 정적 row면 page, 시간 흐름이면 cursor.

다이어그램 / 라우터 모듈 표 / SSR 페이지 표 / JSON API 표: `docs/architecture/web/layering.md` + `docs/architecture/web/routers.md`.

## E3. 서비스 계층·ViewModel·Mapper (P2)

서비스 모듈 카탈로그·`mappers/` sub-package 표시 파생 집중 (`server`/`metric`/`attention`/`report`/`export`/`task`/`shared`/`environment_report`/`report_history`/`topology` 10 sub-module)·`enrich_*` idempotent·UI badge 임계값(`_USAGE_DANGER_PCT`·`_USAGE_WARN_PCT` — `mappers/shared.py`)·USE Method right-sizing 임계값(`assessment_engine/recommendation.py` 도메인 모듈 — web 공용 import)·ViewModel 카탈로그·mapper 파생 필드(`is_well_known`·`badge_class`·`bar_color` 등)·`cache_serializer._DETAIL_DISPLAY_FIELDS` 동기화: `docs/architecture/web/services.md` · `docs/architecture/web/view-models.md` 단일 진실.

본 절 결정:
- 두 임계 도메인(UI badge / USE Method) 혼용 금지.
- 신규 ViewModel 파생 필드 추가 시 #F9 영향도 체크리스트 적용.
- right-sizing 분류 단일 진실 = `recommendation.assess(stats) -> Assessment(recommendation, triggers, unmeasured)` (evidence 기반, OS-aware, ADR 0029 정정). `classify` 는 분류 enum 만 돌려주는 호환 wrapper. 판정 순서 = under(위험 신호 OR) -> idle -> shutdown -> insufficient_data -> over -> optimal — under 가 idle/shutdown 보다 우선이다(saturation·압박 신호가 "미사용" 분류를 가로채지 않음, 예: CPU idle 인데 swap 발생 = under). 합성 규칙: under = 위험 신호 OR(하나라도 hit 되면 발화, 누락 0) / over = cpu/mem 둘 다 다운사이즈 임계 이하일 때만(보수적) / insufficient_data = utilization(cpu/mem) 둘 다 부재 + under 신호도 없을 때만(후순위 — swap 등 saturation 신호가 있으면 util 부재여도 under 로 결론). hit 신호(triggers)를 근거로 동반 — report mapper 권고(`_build_under_provisioned_reason`)와 attention capacity 배지(`to_capacity_warning_item`)가 `assess.triggers` 재사용(임계 재계산 금지), stats 생성은 `build_resource_stats` 공용 — report·attention·서버목록(`to_server_list_item`)·도넛(`_assemble_overview`)이 동일 입력(net baseline·worst_mount disk 포함, 화면 간 idle/shutdown 정합 의무). swap 은 `recommendation.swap_saturation(os_family, swap_used)` helper 경유 의무(Windows pagefile 제외, Linux/None 보존) — `if raw.swap_used` 직접 해석 금지. saturation 축 미관측(값 None, 예: Windows load)은 `unmeasured` 기록 -> `is_partial`(=bool(unmeasured)) -> `ReportRowItem.is_partial` 로 "이용률 기준 평가" confidence 노출 (분류는 utilization/capacity 로 완결, "데이터 부족" 아님 — cpu_p95/mem_p95 산출되는 한). Windows agent 가 등가 카운터(Processor Queue Length 등) 발행 시 unmeasured 자동 해제. 분류 명세·임계 근거(USE Method·AWS/Azure/GCP advisor 출처)·한계 단일 진실 = `docs/architecture/right-sizing.md`.

## E4. URL 식별자 — 정수 PK 노출 금지

라우터 path 파라미터는 `public_id` (UUID). 구현 메커니즘(UUID 타입 선언·422/404 분기·`resolve_internal_id` Depends 브릿지): `docs/architecture/web/layering.md`.

## E5. Jinja2 인프라

`Jinja2Templates` 단일 인스턴스 + 필터 등록은 `web/templating/setup.py`에 격리. 라우터는 `from assessment_engine.web.templating import templates` 만. Redis 캐시 datetime은 `datetime.fromisoformat()`로 파싱(`json.loads` str 그대로 두면 `kst` 필터 오작동).

Jinja2 필터 카탈로그(`kst`/`disksize`/`kbps`/`service_badge_class`/`or_dash`): `docs/architecture/web/services.md`.

## E6. 정적 자원 — JS 외부화 의무

디렉토리 구조 / `chart-utils.js` base.html 단일 로드 / `ChartUtils` API / 페이지별 .js: `docs/architecture/web/static-assets.md`. 외부화 강제 채널: #F5.

## E7. 도메인 분류 책임 (P2)

서비스 카테고리 분류(`classify`)·포트 매핑(`matched_ports`)·카테고리 집합 사전계산(`compute_service_categories`)은 도메인 모듈 `assessment_engine/service_classifier.py`(web·consumer 공용 — `recommendation.py` 동급 도메인, web 역의존 0). `MatchedPort` 도 본 모듈 정의(web view_model 이 re-export). ingest(consumer)가 inventory upsert 시 `compute_service_categories` 로 카테고리 집합을 산출해 `server_inventory.service_categories`(text[]) 에 저장하고, 모든 read 경로(목록·상세·리포트·필터)가 저장값 소비. 매퍼가 호출해 `ServiceItem`에 채움. 템플릿은 `service_badge_class` 필터로 category → CSS 클래스 변환만(P3).

본 절 결정 (ADR 0032):
- 카테고리 규약 단일 진실 = `SERVICE_CATALOG`(`CategoryDef`). 분류 키워드·포트·드롭다운(`SERVICE_CATEGORIES`)·뱃지 CSS(`BADGE_CLASS_BY_CATEGORY`)·템플릿 범례가 모두 본 카탈로그 파생 — 서비스 추가는 카탈로그 1곳만 수정. 분산 정의(옛 `_PATTERNS`/`SERVICE_PORTS`/`_BADGE_CLASSES`) 부활 금지.
- `classify(unit, listen_ports=None)` 다중 신호 우선순위 = name -> comm -> port (정밀도 순). comm/port 는 `_attributed_ports`(comm~name 또는 name well-known 포트) 귀속 포트에만 적용 — per-unit(services 탭) multi-service 오분류 방지.
- 호스트 워크로드 카테고리 집합 = ingest 사전계산 `service_categories`(`compute_service_categories`: services 이름 분류 ∪ `detect_listen_categories` listen 소켓 직접 분류, "unknown" 제외). agent 가 services<->listen_ports join key(pid) 미발행이라(T15), opaque 한 Windows SCM 이름을 listen 소켓 comm/port 로 구제하는 단일 보완 경로. 모든 read 경로가 본 저장값을 소비 — 목록·상세·리포트·필터가 이름·comm·포트 어느 신호로 식별되든 동일 카테고리 집합(화면별 재계산·불일치 0, 목록은 services JSONB 재로드·행별 재분류 없이 경량). 카운트가 필요한 경로(환경분포·`infer_role` export·attention)는 `workload_category_counter`(동일 분류 로직, 키셋은 `service_categories` 와 일치 + 인스턴스 카운트).
- 런타임 스택 카테고리(`CategoryDef.single_instance`, 현재 container)는 호스트당 1로 카운트 — docker+containerd 등이 떠도 "container 2" 금지(`SINGLE_INSTANCE_CATEGORIES`, 카운터·목록 뱃지·detail 뱃지 일관). 일반 카테고리는 인스턴스 카운트.
- detail 뱃지 포트는 카테고리 단위 집계 — comm 귀속 실패 워크로드 포트(W3SVC<->System 80 등)도 카테고리 뱃지에 붙임. 뱃지에 귀속된 포트는 "주요 Listen 포트"에서 제외.
- 본 `classify`(서비스 카테고리)와 `recommendation.classify`(USE Method right-sizing) 혼용 금지 — 다른 함수.

키워드 매칭·카탈로그 파생 / 다중 신호 우선순위 / opaque 이름 한계(T15) / 서비스 3단계 표시 계층: `docs/architecture/web/services.md` "서비스 분류" 절.

## E8. 차트·도넛 UI 디테일 (P3·P4 적용)

차트 Y축·suggestedMax·avg+max ghost·P4 5 의무 규약(sequence counter·capture-before-await·`Array.isArray`·404 분기·suggestedMax 상수): `docs/architecture/web/static-assets.md`. ViewModel 필드(`dash_length`/`dash_offset`/`bar_color`)·SVG 원주 상수(`_UTIL_DONUT_CIRC`)·색 상수 카탈로그(`_UTIL_COLOR_GAUGE/NONE`(활용률 게이지 단색)·`_DONUT_SEGMENT_DEFS`·`_CAPACITY_TRIGGER_COLORS`): `docs/architecture/web/view-models.md` "신호 임계값 단일 정의" 절.

본 절 결정:
- 차트 Y축은 분해력(추이) vs 절대 기준(진단 리포트) 두 정책 중 선택. magic number 금지(명명 상수).
- SVG `stroke-dasharray`·`stroke-dashoffset` 비례 산술은 mapper precompute — 템플릿은 raw 값만 삽입.
- 임계 색 단일 진실 — 동일 의미는 동일 hex (활용률·프로비저닝 분포·capacity trigger 일관).
- 모든 카테고리 항상 노출(count 0 포함). 비활성은 동일 슬롯 옅은 회색. (도넛 카테고리는 #E9 일반 원칙의 한 사례 — 발화 없는 카테고리도 범례에 노출.)
- 도넛 중앙 강조 라벨은 가장 시급한 카테고리 카운트 1개만. 합계·ratio 노출 금지.

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
- `src/assessment_engine/web/settings.py` — `web_settings` (WebSettings) + `diagnostic_settings` (DiagnosticSettings, web 이 task.install 발행 위해 broker 사용)
- `src/assessment_engine/consumer/settings.py` — `consumer_settings` (ConsumerSettings)
- `src/assessment_engine/db/session.py`·`cache/redis.py` + repo root `migrations/env.py` — 자체 `WebSettings()` (모든 컴포넌트 공통 db layer·캐시·schema 진입점, circular import 회피)

`src/assessment_engine/config.py`는 class 정의만 — module-level instance 0 (multi-node 분리 정합, ADR/문서 패턴 정합).

금지:
- Service/Handler 안 구체 구현체 import.
- Composition Root 외 위치에서 `Settings()` 인스턴스 생성 — 위 5 위치 (web/settings·consumer/settings·db/session·cache/redis·migrations/env)만 허용.
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

금지: payload·secret raw dump — 식별자(composite_id·routing key·message_id·server_id)와 카운트만.

로그 format: `LOG_FORMAT` env 분기 — `text`(dev colorized) 또는 `json`(prod, loguru `serialize=True`). 각 entry(web/consumer)가 기동 직후 `setup_logging(settings.log_format)` 호출. 단일 진실은 `src/assessment_engine/log_config.py`.

Request/Correlation ID 분산 trace 도입 트리거·정석 패턴: `docs/operations/observability.md` (현재 미적용, 도입 시 별도 ADR 의무).

## F8. 시크릿·PII 노출 금지

원칙: 로그·예외·HTTP 응답·ViewModel·캐시 어디에도 비밀번호·토큰·전체 메시지 payload·고객사 식별 가능 정보 노출 금지. 한 번 새면 영구.

금지:
- pydantic Settings 비밀 필드 `SecretStr` 미적용 — 신규 비밀 필드 의무.
- `.env`·`secrets/*`(실 secret 파일) commit. PR diff `password`/`secret`/`token`/`key` 패턴 검토 의무.
- 예외 메시지에 raw payload·접속 문자열 — catch 후 sanitize 후 reraise.
- HTTP 응답·ViewModel·JSON export에 PII. 운영 식별자는 `public_id`(UUID)만(#E4).
- Redis·DB에 raw payload 캐싱 — Outbound DTO·ViewModel 단계에서 sanitize 후.
- 메시지 payload 본문 로깅 (`composite_id`는 식별자라 OK).

secret 채널·prod default 자동 검증(`_validate_prod_*`): `docs/operations/env.md`.

## F9. 변경 영향도 체크리스트

원칙: 영향받는 모든 곳 동시 갱신 의무 — 한 곳만 수정 후 PR 금지.

적용 시점: 본 동시 갱신·테스트 작성 의무는 commit/PR 시점(wrap-up, `docs/development/wrap-up.md`) 기준이다. 기능 개발 중간 단계에서는 기능 코드만 작성한다 — 테스트·문서·ADR·CLAUDE.md 동기화를 기능마다 즉시 하지 않고 wrap-up 에서 일괄 처리한다. 개발 중 동작 검증은 실행 화면으로 확인(사용자 직접 또는 `/run`·`/verify`) — 메인 세션이 기능 추가와 함께 테스트·문서를 선제 작성하지 않는다. (테스트 자동 실행·보고 금지는 #F5 와 일관.)

| 변경 유형 | 동시 갱신 위치 |
|-----------|----------------|
| 시계열 컬럼 추가 | (1) ORM 모델 (2) Alembic revision (3) Inbound DTO·mapper (4) Outbound DTO·mapper (5) `cache_serializer._DETAIL_DISPLAY_FIELDS` (6) ViewModel (7) 템플릿·외부 .js |
| inventory 컬럼 추가 | 시계열 (1)~(7) + agent payload 합의 + `docs/architecture/agent.md` "데이터 형식" 절 (엔진 측 inbound DTO·핸들링 단일 진실) |
| 신규 routing key | (1) 발행 측 (agent 또는 engine web) 상수 (2) consumer 핸들러 팩토리 + dispatch (3) `docs/architecture/rabbitmq.md` 토폴로지 표 (4) `docs/architecture/agent.md` 메시지 타입 절 |
| `EXCHANGE`/`ROUTING_KEY_*` 값 변경 | (1) 발행 측 상수 (2) consumer subscriber dispatch (3) `docs/architecture/rabbitmq.md` 토폴로지 표 |
| 메시지 페이로드 schema 변경 (필드 추가·삭제·rename·Literal 값 변경) | (1) `consumer/schemas.py` 또는 발행 측 payload 빌드 (2) Inbound DTO (3) handler 매핑 (4) DB 모델·Alembic revision (필요 시) (5) `docs/architecture/agent.md` 데이터 형식 절 (6) 운영자 가시성 ViewModel·템플릿·API (필요 시) |
| `recommendation.py` 분류 임계 변경 | (1) `recommendation.py` 임계 상수 (2) #F10 평가 윈도우 정합 (3) `docs/architecture/right-sizing.md` 임계 근거 |
| 분류 신호·OS 분기 (USE Method 축·임계·trigger, ADR 0029 evidence) | (1) `recommendation.py` `assess`/`Assessment`(triggers·unmeasured)·임계 상수·`swap_saturation` helper·`ResourceStats` 필드 (2) trigger 키 추가 시 report 권고(`_TRIGGER_ACTION_KO`)·attention capacity 배지 active 매핑 동시 갱신 (3) stats 생성은 `build_resource_stats` 공용(report·attention·서버목록·도넛 단일 진실) — 직접 해석·임계 재계산 금지 (4) 표시 N/A·confidence(`is_partial`=bool(unmeasured)) 마커 (ViewModel precompute + 템플릿) (5) `docs/architecture/right-sizing.md`(명세·근거 단일 진실) + `docs/architecture/web/services.md` "OS 분기" + `right_sizing_thresholds.html` + ADR 0029 정정 |
| 환경변수 추가 | (1) `Settings` 필드 (2) `docs/operations/env.md` 카탈로그 (3) 루트 `docker-compose.yml` `environment:` (필요 시) (4) prod secret 분류면 `SecretStr` 타입 + `_validate_prod_*` 에 weak default 거부 추가 + `docs/operations/env.md` 2절·7절 |
| ViewModel 파생 필드 추가 | (1) mapper 계산 (2) `cache_serializer._DETAIL_DISPLAY_FIELDS` (3) 템플릿 표시 (4) 동일 데이터 JSON API 응답이면 dataclass(P2) |
| 보고서 스냅샷 ViewModel nested 필드 추가 (`EnvironmentReportSummary` 등 정적 스냅샷, #C1) | (1) ViewModel dataclass (2) mapper precompute (3) `report_serializer.*_from_dict` nested 복원 (dict -> dataclass, datetime/IpAddr 재구성 — 누락 시 dict 잔류로 template `.attr` 런타임 깨짐) (4) 템플릿 `.attr` 접근 (5) 라운드트립 단위 테스트(`test_report_serializer`) |
| 신규 조건부(발화) UI 섹션 추가 | (1) 제목·카테고리 항상 노출 (2) 빈 상태 `empty_state` placeholder (3) 화면 컨텍스트 가드와 데이터 발화 가드 분리 (#E9) |
| 신규 외부 의존(HTTP·외부 큐) | (1) fail-open/close 결정(#F6) (2) timeout·재시도 정책 (3) Settings 필드 (4) #F6 매트릭스 갱신 |
| 신규 의존성(`pyproject.toml`) | (1) `uv.lock` 갱신 (2) PR 설명에 도입 사유 (3) 대형 의존성은 ADR 검토. 워크플로 단일 진실: `docs/development/dependencies.md` |
| 신규 차트 MetricType (net/disk rate 등) | (1) `db/repositories/query/types.py` `MetricType` Literal (2) rate 메트릭이면 동 파일 `_RATE_PER_DIM_DEFS` (dim_col, value_col) (3) `db/repositories/query/metric.py` `_RATE_PER_DIM` table 매핑 — 누락 시 `unknown metric_type` AssertionError 500 (Promise.all 한 fetch 실패가 같은 페이지 다른 차트까지 막음) (4) 페이지 JS fetch (5) 가상 제외 필터(`device_filters`) 해당 시 표시 경계 적용 |
| 비동기 보고서 발행 (job-claim 워커·생성 디스패치·폴링, ADR 0040) | (1) emit 라우터 `enqueue_report` 분리 (2) `report_generator.build_report_result_for_job` 생성 디스패치 (3) `report_worker.py` 루프 + `main.py` lifespan 기동·graceful (4) repo `claim_next_pending`/`mark_failed`/`recover_stale_running` (+ `BaseDiagnosticRepository` 추상) (5) GET pending/running/failed 분기 + `GET /reports/{job}/status` + `report-poll.js` + `report_pending.html` (6) `WebSettings` 워커 설정 (7) #C1·#F11·ADR 0040 (8) 단위테스트(`test_diagnostic_service`·`test_report_generator`) |


## F10. 평가 윈도우 · 차트 시계열 옵션 — 단일 진실

원칙: 보고서·대시보드·차트 모두 같은 평가 윈도우·시계열 옵션 카탈로그 참조 — 화면별 의미 분기 방지.

본 절 결정:
- right-sizing 평가 윈도우 단일 진실 = `recommendation.WINDOW_DAYS` (현재 7). 보고서 라우터·서버 목록 분류·구간 선택 기본값(`DIAGNOSTIC_DEFAULT_TIME_RANGE`·보고서 발행 select)·ADR 0003 모두 본 상수/동일 값 참조. 변경 시 `_thresholds_reference.html` 표제도 동기화.
- 모니터링 현황 카드 — 환경 개요(평균 활용률)·환경 자원 평가(자원 적정성 평가)는 `DASHBOARD_TIME_RANGE`("24h", query_service) 고정 — 최근 현황 모니터링, right-sizing 표준 평가와 의도 분리. 보고서 라우터·환경 자원 평가 페이지(`/environment/assessment`)만 `?time_range=` override 허용(평가 페이지 기본값도 `DASHBOARD_TIME_RANGE`). 서버 상세 차트는 실시간 모니터링이라 별도(globalRange 기본 15m, 평가 윈도우와 무관).
- 환경 부하 추이(보고서 SSR 정적 차트) bucket 은 `AUTO_BUCKET[range]` 동적 — 발행 time_range 기준(예: 7d -> 3h, 24h -> 30m). 윈도우 변경 시 집계 단위 자동 추종 — 하드코딩 금지.
- TimeRange/BucketSize Literal 단일 진실 = `db/repositories/query/types.TimeRange`/`BucketSize` + `_BUCKET_INFO` + `chart-utils.js`. 새 range·bucket 도입 시 backend Literal·SQL dispatch·JS 매핑·UI 토글 4곳 동시 갱신 의무.
- range -> 자동 bucket 매핑(`AUTO_BUCKET`)은 backend `types.AUTO_BUCKET` 와 frontend `chart-utils.js` 두 곳 — 값 동기화 의무 (range별 적정 분해력 단일 의미). 신규 TimeRange 도입 시 두 곳 동시 신설. SSR 정적 차트(환경 부하 추이)는 backend 매핑, 동적 fetch 차트는 frontend 매핑 적용 — 둘이 어긋나면 같은 range 가 화면별 다른 bucket.
- 보고서 형태 산출물은 윈도우를 envelope·표제 명시 — JSON Export `period_window{days, start, end}` 의무 필드(#B 동일 원칙).

## F11. Disposability — Graceful shutdown (12-factor IX)

원칙: SIGTERM 시 in-flight 작업 손실 0 + 다음 기동 시 stale 상태 없음.

본 절 결정:
- web — uvicorn `timeout_graceful_shutdown=3s`. 진행 중 HTTP 요청 완료 후 exit. 실시간 메트릭 polling 은 다음 주기 자동 재요청이라 별도 처리 불요. task.install publish 중 SIGTERM은 aio-pika `connect_robust` transaction 보장.
- consumer — `async with message.process(requeue=False)` 컨텍스트 안에서 모든 await 완료. 정상 exit → ACK / raise → NACK + DLQ.
- 보고서 생성 워커 — web lifespan `lifespan_worker` 가 SIGTERM 시 stop_event 로 새 claim 중단 + 진행 중 1건은 `report_worker_shutdown_timeout_sec` 안 drain, 미완은 running 잔류 -> 다음 기동 `recover_stale_running` 가 pending 으로 회수(in-flight 손실 0, ADR 0040). job 상태는 DB(`diagnostic_jobs`)라 메모리 손실 없음 — `signal.signal`·`os._exit` 금지(아래 일관).

금지:
- `signal.signal(SIGTERM, ...)` 직접 핸들러 — uvicorn/asyncio 자체 처리, 중복은 종료 race.
- `os._exit()` — graceful shutdown 우회.
- `message.process()` 컨텍스트 밖 await — ACK/NACK 둘 다 안 됨.

상세: `docs/architecture/consumer.md` "Disposability" 절.

---

## F12. 문서·주석 현황 선언성

원칙: 영구 문서(`docs/architecture/`·`operations/`·`products/`·`development/`·루트 `README.md`)와 코드 주석은 현재 상태만 선언적으로 기술한다. 변경 시 과거 흔적(폐기된 도구·용어·구조·경위)을 제거하고 현황으로 덮는다 — "이전엔 X 였다"·"Y 에서 전환" 회고형 서술 0.

본 절 결정:
- 도구·구조 전환 시 옛 이름·경위를 코드 주석·영구 문서에서 제거. 전환 직후 폐기 토큰 `rg` 0 검증 의무(주석 포함) — 예: dev libvirt 파이프라인·ZDM mock 제거(ADR 0045) 후 `libvirt`(도메인 가상망 용례 제외)·`virsh`·`dev-up`·`dev-down`·`win-server-01`·`dev_zdm_mock`·`host.docker.internal` 잔존 0.
- 예외 — `docs/adr/` (결정 변경 = 새 ADR + 이전 `Superseded by`, 역사 기록 보존 — ADR 불변 규약) · `docs/tradeoffs.md` (의식적 한계·확장 트리거).

금지:
- 영구 문서·코드 주석에 회고형 서술("과거엔"·"이전 방식"·"~에서 전환했다")·폐기 도구/용어/경로/기본값 잔존 — ADR·tradeoffs 외.
- 코드로 알 수 있는 사실(시그니처·디렉토리 트리·라인 수) 문서 중복 (#F9 단일 진실 원칙과 동렬).

검사: 도구·구조 전환·기능 폐기 시 옛 토큰 `rg` 0 (코드 주석 포함). 위반 발견 시 현황 선언으로 즉시 정정 (덮어쓰기, 경위 서술 추가 X).

---