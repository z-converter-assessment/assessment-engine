# Diagnostic (Worker)

결정·옵션 비교·트레이드오프: ADR 0004 + ADR 0010 (진단 규칙 기반 한정) + ADR 0023 (scheduler 폐기). 본 문서는 모듈 구조·흐름·운영 노트 deep dive.

ADR 0023: scheduler cron 자동 발화 폐기. trigger 채널 = 사용자 명시 (web POST `/api/diagnostics`) 만. submitter (`diagnostic/submitter.py`) 는 본 ADR 0014 결정으로 별도 모듈 — web service 가 submitter 단독 사용처.

진단 의사결정 source: USE Method 임계값(AWS Compute Optimizer·Azure Advisor 기반)으로 `recommendation.py`가 결정론적으로 산출 — LLM 의사결정 0 (환각 회피). narrative (자연어 출력) 만 `OllamaLlmClient` 가 LLM 호출 (ADR 0025: 단일 provider, 과금 발생 외부 유료 API 금지).

```
src/assessment_engine/diagnostic/
├── __main__.py     - python -m assessment_engine.diagnostic entry
├── main.py         - aio-pika 큐 소비 + composition root
├── submitter.py    - DiagnosticSubmitter — job INSERT + publish (web service 단독 사용처, ADR 0014)
├── handler.py      - 메시지 1건 = job 1건 처리 (4 stage)
├── aggregator.py   - query_repo 집계 호출 + classify 매핑
└── llm/
    ├── base.py     - BaseLlmClient 추상 (F4)
    └── ollama.py   - OllamaLlmClient (ADR 0025 — 단일 provider, HTTP POST /api/chat)
```

웹 측 연동: `web/services/diagnostic_service.py` (job 발행·캐시·publish — submitter composition), `web/services/mappers/diagnostic.py` (표시 파생 단일 진실 — P2), `web/routers/diagnostics.py` (POST/GET API), `web/routers/diagnostic_results.py` (결과/이력 SSR), `web/routers/pages/` (server detail·report SSR 카드 — `to_panel_payload`).

## main.py — 워커 entry

`python -m assessment_engine.diagnostic`로 기동. consumer/main.py와 동일 패턴:

```
DiagnosticSettings 로드 (ConsumerSettings 상속 — broker_url + 진단 고유 필드)
  -> Redis pool 획득
  -> LLM 클라이언트 구축 (_build_llm_client — 단일 OllamaLlmClient, ADR 0025)
  -> handler factory 호출 (F4 — repo factory + LLM client + redis 주입)
  -> aio-pika robust connect (timeout 10s)
  -> exchange `assessment` + DLX `assessment.dlx` declare (B3 일치 의무)
  -> queue `diagnostic.request` declare (TTL 24h, max-len 100000, DLX 바인딩)
  -> set_qos(prefetch_count=1) — LLM rate limit 자연 throttle
  -> queue.consume(handler)
```

broker declare 인자는 `web/main.py` + `consumer/main.py`와 정확 일치 의무(#B) — 다르면 `PRECONDITION_FAILED`.

`_build_llm_client`는 composition root (F4) — 단일 `OllamaLlmClient` 반환 (ADR 0025). 과금 발생 외부 유료 API (Anthropic·OpenAI) 도입 금지 — 운영자 정책.

## submitter.py — job INSERT + publish

ADR 0014 단일 진실. `DiagnosticSubmitter.submit(scope, server_public_ids, time_range, anchor_at, requested_by)` — N건 batch enqueue:

```
input_params = {server_public_id?, time_range, anchor_at}  # anchor_at None 시 분 단위 truncate now()
input_hash = sha256(scope + canonical(input_params))
DiagnosticJobCreate INSERT (active partial UNIQUE 충돌 흡수)
  -> 신규 INSERT 성공: publish diagnostic.request + 신규 job_id 반환
  -> active 충돌 (같은 input pending/running 존재): 기존 job_id 회수
```

trigger 채널 = web POST `/api/diagnostics` 단독 (ADR 0023). 사용자 명시 의도 표현 만.

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

stage 라벨 단일 진실은 `web/services/mappers/diagnostic._PROGRESS_LABEL_KR` + `db/repositories/base_diagnostic_repository.CLASSIFICATION_LABEL_KR` (분류 라벨 — mapper + mock LLM narrative 양쪽 공용). router·SSR·JSON API·결과 페이지·이력 페이지가 모두 본 mapper view를 사용한다 (P2·P3).

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
| `OllamaLlmClient` (단일) | HTTP POST `/api/chat` (system + user prompt). default 모델 `llama3.1:8b` | 로컬 무료 LLM, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. 한국어 정합 우위 모델 (qwen2.5:14b 등) 으로 운영자 교체 가능. ADR 0025 — 단일 provider 통합 (mock 폐기) |

수치 hallucination 방지 규약 (ADR 0003 3G절):
- 응답 안 모든 숫자 토큰은 payload 안에 존재해야 함
- 호출자가 정규식으로 추출·검증, 실패 시 재생성 1회 후 `status='failed'`
- mock = template 합성이라 자동 통과. ollama = handler 안 검증 단계 적용 의무

실제 LLM latency 본질 (ollama llama3.1:8b CPU 10~30s · GPU 수초) — UI progress_stage 단계 표시 (`extracting_stats` → `applying_rules` → `retrieving_context` → `generating_narrative`) 가 사용자 인내심 제공.

### ollama 운영 (dev = compose 서비스 / prod = 운영자 활성)

dev — `docker-compose.yml (루트)` 안 `ollama` 서비스로 자동 구동. diagnostic-worker 가 compose 네트워크에서 `OLLAMA_BASE_URL=http://ollama:11434` (서비스명) 로 연결. 모델은 빈 상태 기동이라 compose up 후 1회 pull 의무 — `ollama_data` 볼륨에 영속 (재기동 시 재pull 불필요).

```bash
# 1. 스택 기동 (ollama 서비스 포함)
docker compose up -d

# 2. 모델 1회 pull (ollama_data 볼륨 영속)
docker compose exec ollama ollama pull qwen2.5:1.5b      # dev default (CPU 추론 경량)
docker compose exec ollama ollama pull mxbai-embed-large # ADR 0024 RAG embedding (RAG_ENABLED 시)

# 3. diagnostic-worker env (compose 가 default 주입 — override 시만 명시. ADR 0025: 단일 provider, LLM_PROVIDER env 없음)
#    OLLAMA_BASE_URL=http://ollama:11434   (compose 서비스명 — default)
#    OLLAMA_MODEL=qwen2.5:1.5b             (dev default. config.py 코드 default 는 llama3.1:8b)
```

prod — ollama 를 host·별도 노드 어디서 구동하든 운영자가 `OLLAMA_BASE_URL` 로 주입 (env.md). GPU 가속 활용 시 host/전용 노드 직접 구동 권장 (Docker GPU pass-through 회피).

본 catalog 본질 = 운영자 명시 활성 catalog. ollama 가 미가동·연결거부면 `mark_failed('llm_error: <예외타입>')`, 연결됐으나 `LLM_TIMEOUT_SECONDS`(60s) 내 미응답(hang)이면 `mark_failed('llm_timeout')` — 둘 다 DLQ 재시도 없이 job status='failed' 로 흡수, 운영자 polling 인지 후 재발행.

## RAG infra (ADR 0024)

본 phase = infra 구축 단계. handler retrieve_context 단계 본문은 phase 2 (별도 ADR 0024 결정 catalog 7).

### 모듈 구조

```
src/assessment_engine/rag/
├── embedding/
│   ├── base.py        - BaseEmbeddingClient 추상 (F4)
│   ├── mock.py        - MockEmbeddingClient — SHA-256 seed deterministic random vector (비용 0)
│   └── ollama.py      - OllamaEmbeddingClient — HTTP /api/embed (mxbai-embed-large)
└── retriever/
    ├── base.py        - BaseRetriever 추상 + RetrievedDoc dataclass
    └── pgvector.py    - PgVectorRetriever — embedding -> ORDER BY <=> + LIMIT
```

top-level `src/assessment_engine/rag/` package — diagnostic 안 phase 1 활용, 향후 운영 노트 phase 시점 web 측 활용 가능성 (모듈 분리 정공).

### rag_documents 테이블

ORM: `src/assessment_engine/db/models/rag_document.py`. alembic revision `f8b2c4d6e1a3_rag_documents_pgvector` 가 pgvector extension + 테이블 + HNSW 인덱스 생성.

| 컬럼 | 타입 | 비고 |
|------|------|------|
| id | BIGSERIAL PK | 내부 식별자 |
| source_type | VARCHAR(32) NOT NULL | `'domain_knowledge'` (본 phase) / `'operation_note'` (보류) / `'peer_snapshot'` (보류) |
| source_id | VARCHAR(512) NOT NULL UNIQUE | file_path + chunk_index 합성 (UPSERT 키). 백서 갱신 시 같은 source_id 재 insert |
| content | TEXT NOT NULL | chunk 원문 (LLM prompt 인용 대상) |
| metadata | JSONB | source 출처·tag·날짜 등 (예: {'source': 'use-method.md', 'chunk_index': 3, 'title': 'CPU saturation'}) |
| embedding | vector(1024) NOT NULL | mxbai-embed-large default. raw SQL 단독 read/write (`CAST(... AS vector)`) |
| created_at / updated_at | TIMESTAMPTZ NOT NULL | |

인덱스:
- `uq_rag_documents_source_id` UNIQUE (source_id) — UPSERT 키
- `ix_rag_documents_source_type` (source_type) — 카탈로그 필터
- `rag_documents_embedding_hnsw_idx` HNSW (embedding vector_cosine_ops) — recall 95%+ 안정, ORDER BY <=> + LIMIT 패턴 가속

alembic env.py `_include_object` filter — autogenerate 가 vector 타입·HNSW 인덱스 인식 못 함 → rag_documents 테이블·embedding 컬럼·HNSW 인덱스 비교 제외.

### 추상 인터페이스 (F4)

```python
class BaseEmbeddingClient(ABC):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

@dataclass
class RetrievedDoc:
    content: str
    score: float  # cosine similarity (1.0 = 정확 일치)
    metadata: dict[str, Any]
    source_id: str

class BaseRetriever(ABC):
    async def retrieve(self, query: str, top_k: int, source_type: str) -> list[RetrievedDoc]: ...
```

composition root (worker main · ingest CLI) 에서 `EMBEDDING_PROVIDER` 분기 + 구체 주입.

### RAG_ENABLED feature flag

`DiagnosticSettings.rag_enabled` (default False) — phase 2 handler 안 분기 시점 활용. False 시 retrieve_context 단계 skip + payload['rag_context']=[].

### ingest CLI (ADR 0024 결정 8)

```bash
# 도메인 지식 ingest — 파일 1건
python -m assessment_engine.rag.ingest docs/rag-seed/use-method.md

# source_type 명시 (default = domain_knowledge)
python -m assessment_engine.rag.ingest --source-type domain_knowledge file.md

# 재실행 = UPSERT (source_id = file_path + chunk_index 키), 백서 갱신 자연 반영

# 본 repo 자체 sample 일괄 ingest (RAG 활성 검증)
for f in docs/rag-seed/*.md; do
  [ "$f" = "docs/rag-seed/README.md" ] && continue
  python -m assessment_engine.rag.ingest "$f"
done
```

본 repo 안 `docs/rag-seed/` 디렉토리 = 자체 작성 sample 도메인 지식 (USE Method · right-sizing 임계 · classification 본질) — license 의무 0. 외부 백서 (Brendan Gregg · AWS Compute Optimizer 등) 는 운영자가 직접 다운로드 후 같은 형식 (MD/Text) 으로 추가 ingest.

흐름: 파일 read -> `recursive_split` (chunk 500 token + overlap 50, 단락 우선) -> embedding batch -> rag_documents UPSERT -> HNSW 인덱스 자동 갱신.

본 CLI 는 worker 와 동일 settings (`EMBEDDING_PROVIDER` 등) 활용. mock provider = deterministic random vector (비용 0, 동작 검증). ollama provider = 로컬 mxbai-embed-large 호출.

PDF/DOCX 자료는 외부 도구 사전 변환 의무:
```bash
pdftotext -layout original.pdf converted.txt
pandoc original.docx -o converted.md
python -m assessment_engine.rag.ingest converted.md
```

### 운영 / 디버깅 (RAG 안)

```bash
# pgvector 활성 확인
docker compose exec postgres psql -U assessment -d assessment -c "SELECT * FROM pg_extension WHERE extname='vector'"

# rag_documents 카운트 + 카탈로그 분포
docker compose exec postgres psql -U assessment -d assessment -c "SELECT source_type, count(*) FROM rag_documents GROUP BY source_type"

# 검색 본문 (raw SQL 직접 검증)
docker compose exec postgres psql -U assessment -d assessment -c "
SELECT source_id, 1 - (embedding <=> '[0.1,0.2,...]'::vector) AS score
FROM rag_documents WHERE source_type='domain_knowledge'
ORDER BY embedding <=> '[0.1,0.2,...]'::vector LIMIT 5"
```

## diagnostic_jobs 테이블

ORM: `src/assessment_engine/db/models/diagnostic_job.py`. TimescaleDB hypertable 아님 — 일반 테이블. ADR 0023: scheduler retention DELETE 폐기 — 사용자 trigger 만 누적이라 부담 작음. 자료 폭주 시점 별도 retention 결정 의무.

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

submitter: web POST 안 호출 — uvicorn `timeout_graceful_shutdown=3s` 가 진행 중 HTTP 요청 완료 후 exit 보장. publish 중 SIGTERM 은 broker 측 transaction 보장 (`connect_robust`). (ADR 0023: scheduler 폐기로 cron 발화 측 SIGTERM 본문 본 ADR 범위 밖.)

진행 중 job 운영 정책 (#F11 본문 + ADR 0004 후속 결정 영역):
- SIGTERM 시 `status='running'`인 job은 DB에 그대로 남는다. 다음 워커 기동 시 본 job은 retrieve 안 됨 (consumer는 `pending` 만 fetch).
- `worker_job_timeout_seconds` (기본 300s) 초과한 stale `'running'`은 운영자가 수동 `'failed'` UPDATE 또는 timeout 기반 자동 정리. 현재 미구현 — prod 도입 전 별도 ADR 의무.
- 임시 대응: SQL `UPDATE diagnostic_jobs SET status='failed', error_message='stale running cleanup' WHERE status='running' AND started_at < now() - interval '5 minutes'` 운영자 manual 실행.

## 운영 / 디버깅

```bash
docker compose logs -f diagnostic-worker
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready | grep diagnostic
docker compose exec postgres psql -U assessment -d assessment -c "SELECT status, count(*) FROM diagnostic_jobs GROUP BY status"
```

| 증상 | 원인 |
|------|------|
| `diagnostic job not found id=...` | submitter publish 후 DB INSERT 누락 — 트랜잭션 순서·commit 의심 |
| `mark_failed('llm_error: ...')` | ollama 미가동·연결거부·HTTP 오류 — ollama(dev: compose `ollama` 서비스 / prod: host) 가동 + `OLLAMA_BASE_URL` 도달성 확인 |
| `mark_failed('llm_timeout')` | ollama 연결됐으나 60s 내 미응답 — 모델 미 pull(`ollama list`) 또는 모델 로딩 지연 |
| `diagnostic.request.dead` 큐 누적 | 영구 오류 누적 — DLQ peek로 message_body 확인 |

## 관련 문서

- ADR 0004 — 채택 결정·옵션 비교 (워커 + LLM 토글 본질 유지)
- ADR 0010 — 진단 규칙 기반 한정 (LLM 분기 보류)
- ADR 0014 — Diagnostic submitter 분리 (web service 단독 사용처)
- ADR 0023 — scheduler 폐기 (cron 자동 발화 -> 사용자 trigger 만)
- ADR 0024 — AI 진단 RAG 도입 (도메인 지식 phase)
- ADR 0003 — AI/LLM 활용 로드맵 (Phase 2~3 — 임계값·방법론·LLM 모델)
- `docs/architecture/consumer.md` — 워커 구현 시 참조 패턴 (F4·D2·C3 공유)
- `docs/architecture/web/services.md` — `diagnostic_service.py`·`mappers/diagnostic.py` 책임 분리
- `docs/operations/alembic.md` — `diagnostic_jobs` 마이그레이션 절차
- `.claude/CLAUDE.md` #B·#C1·#D2·#F4·#F6 — 결정·금지 사항 단일 진실
