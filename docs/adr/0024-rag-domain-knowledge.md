# ADR 0024 — AI 진단 RAG 도입 (도메인 지식 phase)

상태: Proposed (2026-05-23)

## Context

ADR 0004 (Refined by 0010 · 0023) 안 LLM 토글 결정 (mock · ollama) 도입 후 다음 단계 의문 = "LLM narrative 품질을 어떻게 높이는가".

본 시점 본질 catalog:

1. LLM 자체 학습 자료 = 일반 지식. 본 프로젝트 도메인 (right-sizing · USE Method) 지식 약·gap 가능.
2. 본 시점 mock LLM = `_server_narrative` / `_environment_narrative` 안 payload 통계 인용 + classification·action 라벨 합성. 도메인 지식 반영 0.
3. 향후 ollama LLM 도입 시 = 일반 지식 LLM (llama·qwen 등) — 도메인 백서 학습 약. 가짜 지식 (hallucination) 위험.
4. 본 시점 운영자 (한국어) + 도메인 백서 (영어) — 다국어 처리 의무.

RAG (Retrieval-Augmented Generation, 검색 강화 생성) = LLM 호출 시 외부 지식 베이스에서 관련 문서 검색 후 LLM context 로 주입 -> 응답 품질 강화. 본 ADR 본질 = RAG 컴포넌트 구조 결정.

## Options

### A. RAG 미도입 — mock·ollama LLM 직접 호출

본 시점 catalog 그대로. LLM 자체 학습 자료 만 의존.

- 장점: 컴포넌트 추가 0
- 단점: 도메인 지식 부재. 권장 narrative 품질 한계 + hallucination 위험

### B. RAG 도입 — pgvector + 도메인 지식 만 (본 phase)

pgvector + rag_documents 테이블 + 추상 인터페이스 + handler 단계 확장. 본 phase 자료 = 도메인 지식 (영어 백서) 만.

- 장점: 도메인 지식 RAG 즉시 효과 + 추가 phase (운영 노트·peer) 본질 분리 확장 가능
- 단점: 컴포넌트 +5 (embedding · vector DB · retriever · ingest CLI · settings 토글)

### C. RAG 도입 — 도메인 지식 + 운영 노트 + peer 통계 vector 동시

전 자료 카탈로그 동시 도입.

- 장점: 자료 풍부
- 단점: 본 phase 책임 폭주 — peer 자료 정제 책임 catalog 7 점 (vector 산출 대상 + 시점 + 변환 본문 + retention + cold start 등). 도메인 지식 효과 측정 어려움 (자료 혼재)

### D. 외부 검색 API 활용 (예: Tavily · Perplexity · Brave Search)

런타임 외부 검색 API 호출.

- 장점: 자료 누적·인덱싱 불요
- 단점: 과금 발생 외부 API 정책 위반. 검색 결과 영구 저장 불가 (재현성 약). 외부 의존 단일 실패점

## Decision

옵션 B 채택.

근거:

1. 도메인 지식 = 1순위 — 외부 백서 (USE Method · AWS Compute Optimizer · Brendan Gregg 등) 본질 자료원. 효과 측정 명확 (RAG 전/후 narrative 품질 직접 비교).
2. 본 phase 자료 카탈로그 단일화 (도메인 지식 만) -> 책임 명확·범위 작음·효과 측정 가능.
3. 운영 노트 + peer 통계 vector = 본 phase 후 별도 phase 결정. peer 자료 정제 책임 (vector 산출 대상 + 시점 + 변환 + retention + cold start) 별도 본질 검토 의무.
4. 외부 검색 API = 과금 정책 위반 + 영구 저장 불가.
5. pgvector = 본 시점 postgres 운영 -> 동일 DB 활용. 별도 vector DB (Qdrant · Weaviate 등) 도입 회피.

## Architecture

### 결정 catalog (본 phase)

| 번호 | 항목 | 결정 | 근거 |
|------|------|------|------|
| 1 | RAG 자료 카탈로그 | 도메인 지식 만 (영어 백서·가이드) | 효과 측정 명확, 운영 부담 최소 |
| 2 | embedding 모델 | mxbai-embed-large-v1 (1024d, Matryoshka, Apache 2.0) | MTEB 영어 retrieval 상위, vector 차원 동적 절단 가능 (1024 -> 512 -> 256), ollama 지원 |
| 3 | vector DB | pgvector (postgres extension) | 동일 DB 활용, 별도 서비스 회피. Docker 이미지 = `timescale/timescaledb-ha:pg16` (TimescaleDB + pgvector + postgis 등 all-in-one) — 기존 `timescale/timescaledb:latest-pg16` 는 pgvector binary 미포함 |
| 4 | embedding 차원 | 1024 (모델 default) | 모델 결정 후 자동 |
| 5 | pgvector 인덱스 | HNSW + vector_cosine_ops + 기본 파라미터 (m=16, ef_construction=64) | recall 95%+ 안정, 본 phase 자료 규모 정합, 추후 자료 누적 시까지 동일 인덱스 유지 가능 |
| 6 | RAG_ENABLED feature flag default | False | 단계별 검증 후 활성화 |
| 7 | handler retrieve_context 단계 | query = classification + 통계 + 권장 액션 (recommendation 미정 시 통계+분류 fallback), scope별 query 합성 함수 분리, top-k=5, user prompt 안 별도 절 ("Relevant domain knowledge") | RAG context = LLM narrative 안 근거 자료 인용 |
| 8 | ingestion 파이프라인 | manual CLI (`python -m assessment_engine.rag.ingest <file>`), Markdown + Text 만, RecursiveCharacterTextSplitter (chunk 500 + overlap 50), source_id = file_path + chunk_index UPSERT | 1회 등록 후 갱신 드뭄 본질 정합 |

### 본 phase 자료 카탈로그 결정 근거 (1번 상세)

| 자료 카탈로그 | 본질 | 본 phase 포함 | 근거 |
|--------------|------|--------------|------|
| 도메인 지식 | USE Method · AWS Compute Optimizer · Brendan Gregg 등 외부 백서 | 포함 | 효과 측정 명확 (RAG 전/후 narrative 품질 직접 비교). 1회 ingest CLI 만. 운영 부담 최소 |
| 운영 노트 | 운영자 수동 입력 메모·인시던트 이력·서버별 특이사항 | 보류 | 입력 UI + 비동기 embedding worker 의무. 효과 측정 속에서 도메인 지식 효과 분리 어려움. 본 phase 후 별도 결정 |
| peer 통계 vector | 본 환경 N대 서버 통계 snapshot vector | 보류 | 본 엔진이 peer 자료원 자체 -> 정제 책임 catalog 7 점 (vector 산출 대상 + 시점 + 변환 + 자기 제외 검색 + retention + cold start 등). 본 phase 후 별도 본질 검토 |

### 영어 통일 결정 근거 (2번 상세)

- RAG query 본문 = 본 엔진이 합성 (사용자 직접 입력 X) -> 한국어/영어 선택 가능
- 도메인 지식 자료 자체 = 영어 (USE Method · AWS Compute Optimizer · Brendan Gregg) -> 영어 통일 시 검색 언어 일치 -> 검색 품질 우위
- 영어 전용 embedding 모델 catalog 다양 (MTEB 영어 retrieval 활발 연구) -> 모델 선택 폭 넓음
- 다국어 모델 (bge-m3 등) 보다 영어 전용 모델 (mxbai-embed-large-v1 등) 이 영어 task 우위
- LLM 입력 = top-k 영어 문서 + 한국어 narrative 산출 (다국어 LLM 가 본 정공)
- 본 phase 자료 = 영어 만. 한국어 자료 (운영 노트 등) 추가 시 = 후 phase 별도 결정

### 신규 컴포넌트

| 컴포넌트 | 본문 |
|----------|------|
| pgvector extension | postgres 안 `CREATE EXTENSION vector` (alembic revision) |
| rag_documents 테이블 | id PK · source_type enum (`domain_knowledge` / `operation_note` / `peer_snapshot`) · source_id UNIQUE · content text · metadata jsonb · embedding vector(1024) · created_at · updated_at |
| HNSW 인덱스 | `USING hnsw (embedding vector_cosine_ops)` |
| BaseEmbeddingClient 추상 | `async def embed(texts: list[str]) -> list[list[float]]` |
| MockEmbeddingClient | deterministic random vector (테스트용, 비용 0) |
| OllamaEmbeddingClient | ollama HTTP 호출 (mxbai-embed-large) |
| BaseRetriever 추상 | `async def retrieve(query: str, top_k: int, source_type: str) -> list[RetrievedDoc]` |
| PgVectorRetriever | embedding -> pgvector `ORDER BY embedding <=> query_vec LIMIT top_k` 검색 |
| DiagnosticSettings 신규 필드 | `rag_enabled: bool = False`, `embedding_provider: Literal["mock", "ollama"] = "mock"`, `embedding_model: str = "mxbai-embed-large"`, `embedding_dimension: int = 1024`, `rag_top_k: int = 5`, `rag_max_context_chars: int = 4000` |
| handler retrieve_context 단계 | progress_stage 추가 (`retrieving_context`), payload['rag_context'] 필드 추가 |
| ingest CLI | `src/assessment_engine/rag/ingest.py` + RecursiveCharacterTextSplitter 자체 구현 |

### handler 흐름 (RAG_ENABLED=True 시)

```
extract_stats -> applying_rules -> retrieving_context -> generating_narrative -> succeeded
```

`retrieving_context` 단계:

1. payload (classification + 통계 + recommendation) -> query 텍스트 합성 (scope 별 함수 분리)
2. embedding 모델 호출 -> query vector (1024d)
3. pgvector 검색 (`source_type='domain_knowledge'`, top_k=5)
4. payload['rag_context'] = list of {content, score, metadata}

`generating_narrative` 단계 = 기존 LLM 호출 + payload['rag_context'] 인용. mock LLM 도 RAG context 인용 narrative 합성 의무 ("관련 가이드:" 절 추가).

### RAG_ENABLED=False 분기

retrieve_context 단계 skip + payload['rag_context'] = []. mock LLM 가 본 필드 부재 정합 처리. 본 phase default = False (단계별 검증 후 운영자 명시 활성화).

### query 합성 예시 (scope 별)

server scope:

```
"Server diagnostic context:
 - CPU p95: 85%
 - Memory p95: 60%
 - iowait p95: 5%
 - Classification: cpu_high
 - Recommended action: downsize_cpu
 Related domain knowledge for right-sizing decision."
```

environment scope:

```
"Environment diagnostic context:
 - Total servers: 50
 - Over-provisioned: 12
 - Under-provisioned: 3
 - Idle: 5
 - Optimal: 30
 Related domain knowledge for fleet right-sizing strategy."
```

### LLM prompt 안 RAG context 합성

```
[system prompt]
"You are a server right-sizing expert. Use the provided domain knowledge to ground your analysis."

[user prompt]
"Server statistics:
 - CPU p95: 85%
 - Memory p95: 60%
 ...

Relevant domain knowledge:
[1] {top-1 chunk content}
[2] {top-2 chunk content}
...

Generate a Korean narrative with right-sizing recommendation."
```

### ingestion CLI 흐름

```
[CLI: python -m assessment_engine.rag.ingest <file.md>]
  |
  v
1. 파일 읽기 (MD / Text)
  |
  v
2. RecursiveCharacterTextSplitter (chunk 500 token, overlap 50, 단락 우선 분할)
  |
  v
3. chunk N 개 catalog
  |
  v
4. embedding 모델 batch 호출 (mxbai-embed-large, N개 chunk -> N개 1024-dim vector)
  |
  v
5. rag_documents UPSERT (source_id = file_path + chunk_index, ON CONFLICT DO UPDATE)
  |
  v
6. HNSW 인덱스 자동 갱신 (pgvector)
```

PDF 자료 = 외부 도구 (`pdftotext` · `pandoc`) 로 MD 변환 후 ingest. 본 엔진 PDF 의존성 (pypdf 등) 추가 회피.

## Consequences

### 긍정

- 도메인 지식 RAG 즉시 효과 — LLM narrative 안 백서 인용 근거 자료 반영. hallucination 위험 감소
- 컴포넌트 추가 본 phase 한정 (peer · 운영 노트 별도 phase) -> 책임 명확
- pgvector = 본 시점 postgres 활용 -> 별도 vector DB 운영 부담 0
- 추상 인터페이스 (F4) -> embedding/retriever 구체 구현 교체 자유 (mock · ollama · 기타 로컬 모델)
- RAG_ENABLED feature flag -> 단계별 배포 + 운영자 명시 활성화 결정
- Matryoshka embedding -> 자료 폭주 시점 vector 차원 절단 (1024 -> 512 -> 256) 으로 검색 속도·저장 비용 최적화 여지

### 부정·한계

- LLM context 토큰 증가 -> ollama 추론 시간 증가 (`rag_max_context_chars=4000` default + 본 payload + system prompt)
- embedding 모델 추론 비용 추가 (진단마다 1회 query embedding 호출)
- ingest CLI 운영자 manual 실행 의무 — 백서 추가 시 명시 실행
- 도메인 지식 자료 갱신 드뭄 -> 자료 stale 위험 (백서 개정 1년에 1~2회 수준, 운영자 명시 갱신 의무)
- 본 phase 자료 카탈로그 = 영어 만 -> 한국어 자료 (운영 가이드) 추가 시 영어 통일 정공 위배 가능성 (운영 노트 phase 시점 별도 결정)

### 보류 catalog (후 phase 결정)

| 항목 | 본문 | 결정 의무 시점 |
|------|------|---------------|
| 운영 노트 RAG | 운영자 수동 입력 메모·인시던트 이력·서버별 특이사항. 입력 UI + 비동기 embedding worker 의무 | 본 phase 효과 검증 후 |
| peer 통계 vector RAG | 본 환경 N대 서버 통계 snapshot vector. vector 산출 대상 (narrative vs 통계) + 시점 + 변환 본문 + 자기 제외 검색 + retention + cold start 등 책임 catalog 7 점 | 본 phase 효과 검증 + peer 효과 본질 검토 후 |
| trigger 모델 자동 발화 (TTL 조건부) | A 명시 trigger 만 (본 phase) + C TTL 자동 (후 phase 후보) | 사용 패턴 측정 후 |
| embedding 모델 차원 동적 절단 | mxbai Matryoshka -> 1024 -> 512 -> 256 절단 시 검색 속도·저장 비용 trade-off | 자료 폭주 시점 |
| HNSW 파라미터 튜닝 | m / ef_construction / ef_search 조정 | 검색 품질 측정 후 |
| top-k 튜닝 | 5 default. 운영자 narrative 품질 평가 후 조정 | 본 phase 효과 측정 후 |

### 구현 phase 순서

| phase | 본문 | 의존 |
|-------|------|------|
| phase 0 | scheduler cron 폐기 (ADR 0023) | 무 |
| phase 1 | RAG infra 기본 (본 ADR 결정 catalog 1~6) | ADR 0023 + 0024 결정 후 |
| phase 2 | handler retrieve_context 단계 확장 (본 ADR 결정 7) | phase 1 |
| phase 3 | 도메인 지식 ingest CLI (본 ADR 결정 8) | phase 2 |
| phase 4 (선택) | trigger UI 적용 (A 명시 trigger 모델) | RAG 와 직교, 후순위 |

## 관련 문서

- ADR 0003: AI/LLM 활용 로드맵 (LLM 모델 선택 + 임계값)
- ADR 0004: AI 진단 워커 아키텍처 (LLM 토글 본질 유지, cron 부분 ADR 0023 정정)
- ADR 0010: 진단을 규칙 기반으로 한정 (LLM 분기 보류)
- ADR 0023: diagnostic scheduler 폐기 (RAG 와 본질 정합 — cron 무관 RAG 자료 카탈로그)
- `.claude/CLAUDE.md` #F4 인터페이스 우선 (BaseEmbeddingClient · BaseRetriever 추상)
- `.claude/CLAUDE.md` #F9 영향도 체크리스트 (신규 의존성 + 환경변수 + DB schema 동시 갱신 의무)
