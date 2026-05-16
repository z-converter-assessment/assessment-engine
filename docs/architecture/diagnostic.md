# Diagnostic Worker

결정·옵션 비교·트레이드오프: ADR 0004 + ADR 0010 (진단 규칙 기반 한정). 본 문서는 모듈 구조·흐름·운영 노트 deep dive.

진단 의사결정 source: USE Method 임계값(AWS Compute Optimizer·Azure Advisor 기반)으로 `recommendation.py`가 결정론적으로 산출. `MockLlmClient`는 그 산출 결과를 자연어 템플릿으로 변환만 — 외부 LLM 호출 0. `LLM_PROVIDER=ollama` 분기는 stub(`NotImplementedError`)으로 보존, 외부 LLM 도입 결정 시 활성 가능.

```
src/assessment_engine/diagnostic/
├── __main__.py     - python -m assessment_engine.diagnostic entry
├── main.py         - aio-pika 큐 소비 + composition root
├── scheduler.py    - cron 발화 + 활성 서버/환경 job enqueue + retention DELETE
├── handler.py      - 메시지 1건 = job 1건 처리 (4 stage)
├── aggregator.py   - query_repo 집계 호출 + classify 매핑
└── llm/
    ├── base.py     - BaseLlmClient 추상 (F4)
    └── mock.py     - MockLlmClient (deterministic 텍스트 합성)
```

웹 측 연동: `web/services/diagnostic_service.py` (job 발행·캐시·publish), `web/services/diagnostic_mapper.py` (표시 파생 단일 진실 — P2), `web/routers/diagnostics.py` (POST/GET API), `web/routers/diagnostic_results.py` (결과/이력 SSR), `web/routers/pages.py` (server detail·report SSR 카드 — `to_panel_payload`).

## main.py — 워커 entry

`python -m assessment_engine.diagnostic`로 기동. consumer/main.py와 동일 패턴:

```
DiagnosticSettings 로드 (ConsumerSettings 상속 — broker_url + 진단 고유 필드)
  -> Redis pool 획득
  -> LLM 클라이언트 구축 (_build_llm_client — LLM_PROVIDER 분기)
  -> handler factory 호출 (F4 — repo factory + LLM client + redis 주입)
  -> aio-pika robust connect (timeout 10s)
  -> exchange `assessment` + DLX `assessment.dlx` declare (B3 일치 의무)
  -> queue `diagnostic.request` declare (TTL 24h, max-len 100000, DLX 바인딩)
  -> set_qos(prefetch_count=1) — LLM rate limit 자연 throttle
  -> queue.consume(handler)
```

broker declare 인자는 `web/main.py` + `consumer/main.py`와 정확 일치 의무(#B) — 다르면 `PRECONDITION_FAILED`.

`_build_llm_client`는 composition root (F4). `LLM_PROVIDER=mock`이면 `MockLlmClient`, `ollama`면 별도 PR 활성 분기, 그 외는 `ValueError`. 과금 발생 외부 API(Anthropic·OpenAI) 도입 금지 — 운영자 정책, ADR 0004 정정 시점에 추가.

## scheduler.py — cron 발화

`python -m assessment_engine.diagnostic.scheduler`로 기동. 무상태 publisher:

```
croniter(DIAGNOSTIC_SCHEDULE_CRON, tz=Asia/Seoul) 다음 발화 대기
  -> 활성 서버 조회 (last_seen_at > now() - DIAGNOSTIC_ACTIVE_SERVER_WINDOW_HOURS)
  -> 각 서버 + environment 1건 job INSERT (status='pending') + publish diagnostic.request
  -> retention DELETE (finished_at < now() - DIAGNOSTIC_RETENTION_DAYS)
  -> 다음 발화 대기
```

cron timezone 명시 의무 — `ZoneInfo("Asia/Seoul")`. UTC 기반 계산 시 03시 KST 의도와 9시간 차이 발생.

## handler.py — 메시지 1건 = job 1건

```
message.process(requeue=False) 컨텍스트
  body = json.loads(message.body)            # job_id 추출
  멱등성 1단 — safe_set_nx(idempotent:{message_id}, ttl=24h)  # fail-open (#D2)
    -> 중복: silent ack 후 return
  session_factory()로 트랜잭션 진입
  diag_repo.get_by_id(job_id)
    -> None: warning 후 silent ack
    -> status != 'pending': info 후 silent ack (멱등 1단 fail-open 결과 가능)
  단계 1 — extracting_stats: aggregator.extract_{server,environment} -> commit -> Redis SET
  단계 2 — applying_rules:   recommendation.classify -> stats 갱신 -> commit -> Redis SET
  단계 3 — generating_narrative: llm_client.generate_narrative + 수치 검증 -> commit -> Redis SET
  단계 4 — succeeded:        finalize(result, finished_at=now()) -> commit -> Redis SET
```

stage 라벨 단일 진실은 `web/services/diagnostic_mapper._PROGRESS_LABEL_KR` + `db/repositories/base_diagnostic_repository.CLASSIFICATION_LABEL_KR` (분류 라벨 — mapper + mock LLM narrative 양쪽 공용). router·SSR·JSON API·결과 페이지·이력 페이지가 모두 본 mapper view를 사용한다 (P2·P3·P5).

실패 매트릭스 (handler.py except 분기):

| 분류 | 처리 |
|------|------|
| 메시지 자체 결함 (JSON 파싱·job_id 누락) | silent ack + ERROR 로그 (DLQ 보내봐야 운영자 개입 의미 없음, #F6) |
| job_id 미존재·이미 처리됨 | silent ack + INFO/WARNING |
| DB 일시 장애 (`OperationalError`) | reraise -> aio-pika NACK requeue=False -> DLQ (#F6 fail-close) |
| 영구 오류 (`ValueError` · `KeyError` · `IntegrityError`) | `status='failed', error_message` UPDATE 후 ack — aggregator no metrics·input_params 누락·DB UNIQUE 충돌 등. 운영자 polling으로 인지·재발행 |

## aggregator.py — 통계 추출

`recommendation` 모듈 위치: `assessment_engine/recommendation.py` (top-level 도메인 모듈 — web·diagnostic 양쪽 import).

`extract_server(query_repo, server_public_id, period_days, end, time_range)`:
- `resolve_server_id` -> `report_aggregate([sid], period_days, end)` -> `ResourceStats` -> `recommendation.classify`
- 반환 dict: scope/period_window/evaluated_at/server/use_method/classification/recommendation

`extract_environment(query_repo, period_days, end, time_range)`:
- 활성 서버 N대 `report_aggregate(server_ids, ...)` 1회 SQL (#C5 N+1 회피)
- 분포 카운트 (over/under/optimal/idle/shutdown/insufficient_data)
- top_actions·saturation_alerts 집계

raw 단위 그대로 (P1) — KB·bytes. percent·delta 변환은 SQL 표현식만(`_chart_*` 패턴) 또는 mapper/recommendation.

## LLM 토글 (F4)

`BaseLlmClient.generate_narrative(scope, payload) -> str` 추상. composition root는 워커 main의 `_build_llm_client`.

| Provider | 구현 | 비고 |
|----------|------|------|
| `mock` (기본) | `MockLlmClient` — payload 안 통계 수치만 인용해 deterministic 합성 | 외부 호출 0, 비용 0, 수치 검증 자동 통과 (ADR 0003 3G절) |
| `ollama` | 미구현 (Phase 2 별도 PR) — `NotImplementedError` | 로컬 무료 LLM, `OLLAMA_BASE_URL`, 모델 `llama3.1:8b` |

수치 hallucination 방지 규약 (ADR 0003 3G절):
- 응답 안 모든 숫자 토큰은 payload 안에 존재해야 함
- 호출자가 정규식으로 추출·검증, 실패 시 재생성 1회 후 `status='failed'`

mock latency 시뮬레이션 — `LLM_MOCK_LATENCY_SECONDS` (`asyncio.sleep`)로 UI progress_stage 단계 표시 확인용.

## diagnostic_jobs 테이블

ORM: `src/assessment_engine/db/models/diagnostic_job.py`. TimescaleDB hypertable 아님 — 일반 테이블 + 스케줄러 retention DELETE.

| 컬럼 | 타입 | 비고 |
|------|------|------|
| id | UUID PK | path param (#E4 정수 PK 노출 금지) |
| scope | TEXT NOT NULL | `'server'` 또는 `'environment'` |
| input_params | JSONB NOT NULL | `{server_public_id?, time_range, anchor_at}` — anchor_at은 분 단위 truncate |
| input_hash | TEXT NOT NULL | sha256(scope + canonical(input_params)) — partial UNIQUE 키 |
| status | TEXT NOT NULL | `pending` / `running` / `succeeded` / `failed` |
| progress_stage | TEXT | `queued` / `extracting_stats` / `applying_rules` / `generating_narrative` |
| result | JSONB | succeeded 시 채움 |
| error_message | TEXT | failed 시 채움 |
| created_at / started_at / finished_at | TIMESTAMPTZ | |

제약:
- partial UNIQUE `(scope, input_hash) WHERE status IN ('pending','running')` — 더블클릭 방어, `tasks` 패턴 재활용 (#C1·#C5).
- `input_hash` 기반 결과 캐시 미사용 — `anchor_at` 분 단위 정규화로 매 분 hash가 달라져 실효성 없음. partial cache index도 없음.
- SSR latest 표시는 `get_latest_by_context(scope, time_range, server_public_id)` — `anchor_at` 무관 JSONB 검색.

## Redis 키 (fail-open)

| 키 | TTL | 용도 |
|------|------|------|
| `idempotent:{message_id}` | 24h | 멱등성 1단 (#D2) — consumer와 공유 namespace |
| `diagnostic:job:{job_id}` | 1h | progress polling 캐시 — 워커가 stage UPDATE 후 SET, web polling 우선 read miss 시 DB |

모두 `safe_*` helper 경유 (#C3). Redis 장애 시 silent skip — DB fallback이 흡수.

## Disposability — SIGTERM 흐름 (#F11)

워커: consumer와 동일 패턴 — `async with message.process(requeue=False)` 컨텍스트가 메시지 손실 0 보장.

스케줄러: croniter 발화 사이 SIGTERM은 즉시 안전 종료. publish 중 SIGTERM은 broker 측 transaction 보장 (`connect_robust`).

진행 중 job 운영 정책 (#F11 본문 + ADR 0004 후속 결정 영역):
- SIGTERM 시 `status='running'`인 job은 DB에 그대로 남는다. 다음 워커 기동 시 본 job은 retrieve 안 됨 (consumer는 `pending` 만 fetch).
- `worker_job_timeout_seconds` (기본 300s) 초과한 stale `'running'`은 운영자가 수동 `'failed'` UPDATE 또는 timeout 기반 자동 정리. 현재 미구현 — prod 도입 전 별도 ADR 의무.
- 임시 대응: SQL `UPDATE diagnostic_jobs SET status='failed', error_message='stale running cleanup' WHERE status='running' AND started_at < now() - interval '5 minutes'` 운영자 manual 실행.

## 운영 / 디버깅

```bash
docker compose logs -f diagnostic-worker
docker compose logs -f diagnostic-scheduler
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready | grep diagnostic
docker compose exec postgres psql -U assessment -d assessment -c "SELECT status, count(*) FROM diagnostic_jobs GROUP BY status"
```

| 증상 | 원인 |
|------|------|
| `diagnostic job not found id=...` | 스케줄러 publish 후 DB INSERT 누락 — 트랜잭션 순서·commit 의심 |
| `LLM_PROVIDER=ollama not implemented yet` | 미구현 분기 호출 — `.env`에서 `LLM_PROVIDER=mock` |
| `diagnostic.request.dead` 큐 누적 | 영구 오류 누적 — DLQ peek로 message_body 확인 |

## 관련 문서

- ADR 0004 — 채택 결정·옵션 비교 (단일 진실)
- ADR 0003 — AI/LLM 활용 로드맵 (Phase 2~3 — 임계값·방법론·LLM 모델)
- `docs/architecture/consumer.md` — 워커 구현 시 참조 패턴 (F4·D2·C3 공유)
- `docs/architecture/web/services.md` — `diagnostic_service.py`·`diagnostic_mapper.py` 책임 분리
- `docs/operations/alembic.md` — `diagnostic_jobs` 마이그레이션 절차
- `.claude/CLAUDE.md` #B·#C1·#D2·#F4·#F6 — 결정·금지 사항 단일 진실
