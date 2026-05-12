# ADR 0004 — AI 진단 워커 아키텍처

상태: Proposed (2026-05-12)

## Context

ADR 0003 (AI/LLM 활용 로드맵)에서 Phase 2~3의 임계값·방법론·LLM 모델 선택을 정의했지만 실행 인프라(어디서 호출하나, 결과 어떻게 저장·전달하나)는 미정. 본 ADR은 실행 인프라 결정을 박제한다.

요구사항:
1. 주기적 진단 — 스케줄러가 활성 서버 N대 각각 + 전체 환경 1건을 일정 주기로 자동 진단
2. 사용자 트리거 진단 — 웹 포털에서 단일·다중 서버·전체 환경 진단 요청
3. LLM API 호출 latency 수십초~수분 — HTTP 응답에 직접 포함 불가
4. LLM 비용 절감 — 동일 input 반복 진단 캐싱 필요
5. 진행 가시성 — 단계 표시·부분 결과 polling

기존 자산:
- aio-pika 비동기 consumer 프로세스 패턴 (`docs/architecture/consumer.md`)
- RabbitMQ 토폴로지·DLQ (`docs/architecture/rabbitmq.md`)
- F4 인터페이스 우선 / D2 멱등성 2단 / C3 Redis fail-open

## Options

### A. 웹 프로세스 내부 비동기 (`asyncio.create_task` / BackgroundTasks)
FastAPI 안에서 LLM 호출. 202 즉시 응답 + 백그라운드 task가 후속 처리.
- 장점: 신규 서비스·큐 추가 없음
- 단점: web 재시작 시 in-flight job 손실. LLM 호출이 web 워커 점유 (요청 격리 안 됨). 스케줄러 코드는 별도 구현 — 양쪽에 LLM 호출 코드 복제 위험

### B. 진단 워커 별도 프로세스 + RabbitMQ 큐
스케줄러·웹 양쪽이 큐에 진단 작업 enqueue. 워커 프로세스가 큐 소비 → LLM 호출 → DB 저장.
- 장점: consumer 패턴 그대로 재활용 (F4·D2). web 격리. 스케줄러·웹이 동일 워커 공유 → LLM 호출 로직 단일 진실
- 단점: docker-compose 서비스 +2 (worker, scheduler). 큐·DLQ 추가

### C. 스케줄러·웹 각자 LLM 직접 호출 (워커 없음)
스케줄러는 독립 프로세스 cron, 웹은 BackgroundTasks. LLM 호출 코드를 양쪽에 복제.
- 장점: 큐 추가 없음
- 단점: LLM 호출·재시도·캐시 정책이 양쪽 동기화 의무. 옵션 A의 web 점유 단점 그대로

## Decision

옵션 B 채택.

근거:
1. 스케줄러 자동 진단 + 웹 사용자 진단이 동일 워크로드 — 워커 단일화로 LLM 호출 로직 한 곳
2. 기존 consumer 패턴 그대로 재활용 — aio-pika·F4 composition root·D2 멱등성·C3 fail-open 모두 적용 가능
3. web 격리 — LLM 호출이 web 프로세스 점유 안 함, 요청·진단 부하 분리
4. RabbitMQ 큐 1개·서비스 +2 추가 비용은 작음 (기존 토폴로지 위에 routing key 1개)

## Architecture

### 신규 컴포넌트
| 서비스 | 역할 | 비고 |
|---|---|---|
| `diagnostic-worker` | aio-pika 소비 → 통계 추출 → 룰 → LLM 호출 → DB 저장 | consumer와 동일 패턴, 별도 코드 베이스 |
| `diagnostic-scheduler` | cron 발화 → 활성 서버·환경 job enqueue + retention DELETE | 큐 publish만 담당, 무상태 |

기존 `web` 라우터에 `/api/v1/diagnostics` 추가.

### Routing key 및 큐 (B3 확장)
- `diagnostic.request` — engine 내부 routing key, 워커 소비
- DLQ는 기존 `assessment.dlx` 재활용 (#B3)
- adhoc vs scheduled 큐 분리하지 않음 — 트래픽 증가 시 분리 (트레이드오프 별도 기록)

### `diagnostic_jobs` 테이블

| 컬럼 | 타입 | 비고 |
|---|---|---|
| id | UUID PK | 라우터 path param (E5 정수 PK 노출 금지) |
| scope | TEXT NOT NULL | `'server'` 또는 `'environment'` |
| input_params | JSONB NOT NULL | scope별 입력 (`server_id`, `period_days`, ...) |
| input_hash | TEXT NOT NULL | sha256(scope + canonical(input_params)) — 캐시·멱등 키 |
| status | TEXT NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| progress_stage | TEXT | `queued` / `extracting_stats` / `applying_rules` / `generating_narrative` |
| result | JSONB | succeeded 시 채움 |
| error_message | TEXT | failed 시 채움 |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| started_at | TIMESTAMPTZ | running 진입 시 |
| finished_at | TIMESTAMPTZ | 종료 시 |
| requested_by | TEXT | 인증 도입 후 채움 (현재 NULL 허용) |

제약:
- partial UNIQUE `(scope, input_hash) WHERE status IN ('pending','running')` — 더블클릭·중복 enqueue 방어 (`tasks` 패턴 재활용 #C1)
- 인덱스: `(input_hash, finished_at DESC) WHERE status='succeeded'` — 1시간 캐시 조회용
- TimescaleDB hypertable 대상 아님 (단위가 job, 시간 partition 가치 작음) — 일반 테이블 + 스케줄러가 retention DELETE 같이 수행

### API

| Endpoint | 동작 |
|---|---|
| `POST /api/v1/diagnostics` | body:`{scope, server_ids?, time_range, anchor_at?}` → `{job_ids: [...]}`. N개 batch enqueue 가능 (server_ids 길이만큼). `time_range` = Literal `"24h"`/`"7d"`/`"14d"`/`"30d"`(기본 `"14d"`). `anchor_at` = ISO 8601 UTC datetime 또는 생략 시 서버에서 분 단위 truncate한 now() — 같은 분 호출이면 캐시 적중. 차트 토글(`base_query_repository.TimeRange`)과 동일 컴포넌트·동일 UX |
| `GET /api/v1/diagnostics?ids=j1,j2,j3` | 배열 응답 `[{job_id, server_id?, status, progress_stage, result?, error_message?}, ...]` |
| `GET /api/v1/diagnostics/{job_id}` | 단일 응답 (path param 편의) |

캐시 (1시간):
- 라우터가 input_hash로 `diagnostic_jobs` 조회 → 동일 input_hash + `status='succeeded'` + `finished_at > now() - 1h` 발견 시 기존 job_id 반환 (LLM 비용 절감)
- 캐시 히트 클라이언트는 polling 1회로 succeeded 받음

폴링 정책:
- 클라이언트 3초 interval, 5분 timeout
- 워커가 단계별 UPDATE 시 Redis `SET diagnostic:job:{job_id}` (TTL 1h) — polling이 Redis 우선, miss 시 DB fallback (#C3 `safe_*` helper 의무)

### 워커 흐름
1. 메시지 수신 (routing_key=`diagnostic.request`, body=`{job_id}`)
2. 멱등성 1단 (`safe_set_nx idempotent:{message_id}` 24h) — 기존 consumer 패턴 그대로 (#D2)
3. UPDATE `status='running', started_at=now(), progress_stage='extracting_stats'`
4. SQL USE Method 통계 추출 (ADR 0003 3B절)
5. UPDATE `progress_stage='applying_rules'`
6. 룰 엔진 실행 (ADR 0003 3C절)
7. UPDATE `progress_stage='generating_narrative'`
8. LLM API 호출 (`LLM_PROVIDER` 분기)
9. LLM 응답 수치 검증 (ADR 0003 3G절 — 정규식 추출 → 입력 JSON 존재 확인, 미존재 시 재생성 1회)
10. UPDATE `status='succeeded', result, finished_at=now()`
11. Redis SET (polling 캐시) — fail-open (#C3)
12. 실패 분기:
    - 일시 장애 (LLM API timeout·5xx·DB OperationalError) → `_db_retry` 백오프 후 재시도
    - 영구 오류 (입력 검증 실패·LLM 응답 검증 2회 실패) → `status='failed', error_message`
    - 메시지 자체 결함 (job_id 미존재 등) → DLQ (#F10)

### LLM 토글 (mock·ollama 전용 시작)
- 환경변수 `LLM_PROVIDER=mock|ollama` (기본값 `mock`)
- 운영자 정책: 과금 발생 외부 API(Anthropic·OpenAI 등) 호출 금지 — 개발·테스트·운영 모든 단계에서 mock 또는 로컬 ollama만 사용. 외부 유료 API 도입은 운영자 명시 동의 시점에 본 ADR 정정 또는 신규 ADR로 추가.
- `mock`: deterministic 텍스트 합성 (룰 결과 JSON 값 인용 → ADR 0003 3G절 수치 검증 자동 통과) + `asyncio.sleep` latency 시뮬레이션 (progress_stage 단계 표시 확인용)
- `ollama`: 로컬 무료 LLM (`OLLAMA_BASE_URL=http://localhost:11434`), 모델 예 `llama3.1:8b`. 응답 latency 큼 (CPU 10~30초, GPU 수초)
- F4 인터페이스: `BaseLlmClient` 추상 + `MockLlmClient` / `OllamaLlmClient` 구현. composition root는 워커 main
- 외부 유료 API 도입 시점에 식별자(machine_id·hostname·IP) 마스킹 정책(ADR 0003 3L절) 적용 — 현 단계는 mock·ollama 모두 로컬이라 마스킹 의무 없음

### 스케줄러
- 환경변수 `DIAGNOSTIC_SCHEDULE_CRON` (기본 `0 3 * * *` — 매일 03시 KST)
- 발화 시:
  1. 활성 서버 조회 (`last_seen_at > now() - 24h` 기준)
  2. 각 서버에 대해 `scope='server'` job enqueue (캐시 히트는 skip — 1시간 정책)
  3. `scope='environment'` job 1건 enqueue
  4. retention DELETE: `DELETE FROM diagnostic_jobs WHERE finished_at < now() - INTERVAL '90 days'`
- prefetch_count(워커 1~2)로 LLM rate limit 자연 throttle — 큐 분리 불요

## Consequences

### 긍정
- 스케줄러·웹 진단 워크로드 통합 — LLM 호출 로직 1곳 (F4 composition root 일관성)
- 기존 consumer 패턴(F4·D2·C3) 그대로 재활용 — 학습 비용 0
- LLM 비용 절감 — input_hash 1시간 캐시
- 진행 단계 가시화 — UX 개선 (progress_stage 5단계)
- web 격리 — LLM 호출 부하가 사용자 요청 응답에 영향 없음

### 부정·한계
- docker-compose 서비스 +2 (worker, scheduler) — 운영 노드 증가
- progress_stage UPDATE 5회/진단 — DB 쓰기 증가 (트래픽 작아 영향 무시)
- 단일 큐 — adhoc 요청이 스케줄러 백로그 뒤로 밀릴 가능성 (트래픽 보고 분리 결정)
- LLM API 외부 의존 — timeout·재시도 정책 의무 (#F10) — ADR 결정 단계 D로 분리
- 캐시 1시간 윈도우 — 동일 input이라도 1시간 내 데이터 변경(예: 부하 급증)은 stale 결과 반환 가능 — 사용자 수동 refresh로 대응 (force_refresh flag 추후)

### 즉시성 요구 발생 시 전환 경로
ADR 0002와 유사 — 진단 단위가 작아지고 빈도가 폭증할 경우:
1. 큐 분리 (`diagnostic.adhoc` priority high, `diagnostic.scheduled` priority low)
2. 워커 prefetch_count 상향 또는 워커 프로세스 N개
3. progress_stage Redis-only (DB UPDATE 5회 → SUCCEEDED 1회만)

### 미해결 (다음 단계 결정)
- LLM input/output JSON 스키마 정확한 형태 (수치 검증)
- environment 진단 집계 필드 카탈로그 (over_provisioned_count·idle_count·potential_savings 등)
- LLM API timeout·재시도 백오프 횟수·최대 대기
- 인증·multi-tenant (`requested_by`·고객사 격리)

## 관련 문서
- ADR 0003: 임계값·방법론·LLM 모델 선택 (본 ADR은 실행 인프라)
- ADR 0002: RPC piggyback 패턴 (Task 명령 — 진단과 무관하지만 워커 패턴 참조)
- CLAUDE.md #A2 컨테이너 구성·#B1 routing key 표·#C1 키 제약 갱신 의무
- `docs/architecture/consumer.md` 워커 구현 시 참조 패턴
- `docs/operations/alembic.md` `diagnostic_jobs` 마이그레이션 절차

## 정정 이력

- 2026-05-12: LLM 토글에서 `anthropic` 제거, `mock`+`ollama` 전용으로 정정. 사유: 운영자 정책 "과금 발생 외부 API 호출 절대 금지". 외부 유료 API 도입은 운영자 명시 동의 후 별도 결정.
- 2026-05-12: API input을 `period_days` int에서 `time_range` Literal + `anchor_at` datetime으로 정정. 사유: 차트 토글 컴포넌트(`base_query_repository.TimeRange`)와 통합 UX. `input_params` 카탈로그도 `{server_public_id?, period_days}` → `{server_public_id?, time_range, anchor_at}`로 변경 — 모든 input_hash는 분 단위 anchor 정규화 후 sha256. 기본값 `14d` (ADR 0003 WINDOW_DAYS와 동일 진실).
- 2026-05-12: `time_range` 4개→7개로 확장 (`15m`/`1h`/`6h` 추가) — 차트 TimeRange와 동일 옵션. 짧은 윈도우는 USE Method 표본 부족하나 차트 토글과 UX 일관성 우선. `DIAGNOSTIC_RANGE_DAYS` fraction day 매핑 + `DIAGNOSTIC_RANGE_LABEL_KR` 한국어 라벨 단일 진실.
- 2026-05-12: 1시간 input_hash 캐시(`get_cached_succeeded`) 제거. 사유: `anchor_at` 분 단위 정규화로 매 분 hash가 달라져 cache hit 거의 안 일어남(실효성 없음). 더블클릭 방어는 active partial UNIQUE `WHERE status IN ('pending','running')`가 이미 흡수. partial cache index도 같이 drop (마이그레이션 d2f4a6b8c0e1).
- 2026-05-12: SSR latest 진단 표시 — 신규 `get_latest_by_context(scope, time_range, server_public_id)` 메서드. `anchor_at` 무관 (JSONB 검색)으로 latest 매칭 — 사용자가 자유 anchor로 발행해도 SSR 카드는 가장 최근 결과 표시.
- 2026-05-12: 진단 발행 UI 모달 패턴 통일 — 단일 서버 카드·환경 카드·list batch 모두 "AI 진단" 버튼 → 모달(time_range·anchor) → POST → 결과 페이지(`/diagnostics?ids=...`) 이동. 결과 페이지에서 polling으로 succeeded 추적. SSR latest는 카드에 자동 표시 (페이지 로드 즉시).
- 2026-05-12: 진단 이력 페이지(`GET /diagnostics/history`) 신설. 운영자 회고용 — 최근 N일 발행 이력 + scope 필터.
- 2026-05-12: 표시 파생 mapper 신설 (`web/services/diagnostic_mapper.DiagnosticPanelView`) — short_id·status_badge_class·window_label_kr·classification_label_kr·classification_badge_class·recommendation_action 등 단일 진실(P2·P5). `to_panel_payload`·API 라우터(`/api/v1/diagnostics`)가 mapper 위임. JS 두 파일(`diagnostic.js`/`diagnostic-results.js`)과 템플릿(`results.html`/`history.html`/`report.html`)의 매핑 dict 제거·view 필드 직접 사용은 별도 follow-up PR.
- 2026-05-12: 스케줄러 cron timezone KST 명시(`zoneinfo.ZoneInfo("Asia/Seoul")`). 이전 UTC 기반 계산으로 03시 KST 의도와 9시간 차이 발생하던 문제 fix.
