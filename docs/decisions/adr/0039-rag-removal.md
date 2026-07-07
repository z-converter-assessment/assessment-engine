# ADR 0039 — RAG 제거 (0024 supersede)

상태: Accepted (2026-06-10)

## Context

ADR 0024 가 RAG (Retrieval-Augmented Generation) infra 를 도입했다 — pgvector + `rag_documents` 테이블 + HNSW 인덱스 + `BaseEmbeddingClient`/`BaseRetriever` 추상 + `PgVectorRetriever` + ingest CLI + `docs/rag-seed/` 도메인 지식 sample + `RAG_ENABLED` feature flag (default False).

본 시점 본질 catalog:

1. RAG infra 는 ADR 0024 결정 catalog 1~6 (phase 1) 만 구축됐고 handler retrieve_context 단계 (결정 7, phase 2) 는 미완성 — `rag_enabled` default False 로 한 번도 활성된 적 없다.
2. 코드/DB 스키마/문서/ADR/테스트/CLAUDE.md 6계층에 RAG 잔재가 퍼져 유지 부담만 누적. 미활성 기능이 진단 흐름 (`handler` retriever 분기·`ollama` prompt RAG context 절·config 7 필드) 을 복잡하게 함.
3. ADR 0025 단일 ollama LLM narrative 로 진단 품질 본질은 충족 — 통계 집계 (`recommendation.py` 결정론적 분류) + LLM narrative 단독으로 운영 요구 정합. 도메인 지식 grounding 없이도 hallucination 은 수치 검증 (`find_hallucinated_numbers`, ADR 0003 3G절) 으로 방어.
4. pgvector extension·embedding 모델 추론·ingest CLI manual 운영은 미활성 상태에서도 인지 부담 (env 카탈로그·문서·테스트 mock).

## Decision

RAG 전면 제거. ADR 0024 supersede.

제거 대상:

- 코드: `src/assessment_engine/rag/` 패키지 전체 (embedding·retriever·ingest·query) + `db/models/rag_document.py`
- DB: `rag_documents` 테이블 + pgvector extension (drop revision `e2f4a6c8b0d3`, head `d5c8b1a3e9f2` 후속)
- config: `DiagnosticSettings` 안 `rag_enabled`·`embedding_*`·`rag_top_k`·`rag_max_context_chars`
- 진단 흐름: `handler` retriever 파라미터·`_retrieve_rag_context`·payload['rag_context'], `ollama` prompt RAG context 절
- 문서: `docs/rag-seed/`, `diagnostic.md` RAG infra 절, env.md RAG 키, alembic.md pgvector 예시 등
- 테스트: `test_rag_*` 3종 + 진단 테스트 retriever fixture

유지:

- LLM narrative (`OllamaLlmClient`, ADR 0025) — `OLLAMA_*`·`LLM_TIMEOUT_SECONDS` env 그대로. RAG 와 독립.
- 진단 흐름 = 통계 집계 (`aggregator`) -> 결정론적 분류 (`recommendation.py`) -> LLM narrative + 수치 환각 검증.

근거:

1. 미활성 확장 (`RAG_ENABLED=False`) 유지 부담 > 미래 효용. 도입 후 phase 2 미진행 = 실효 0.
2. LLM narrative 단독으로 운영 요구 충족 — 도메인 지식 grounding 부재가 현 진단 품질을 저해하지 않음 (분류·권고는 `recommendation.py` 결정론).
3. pgvector·embedding·ingest 제거로 진단 워커·DB·문서 단순화 — dev-prod parity 와 유지보수성 우위.
4. 재도입 비용은 낮음 — 필요 시점에 새 ADR + drop revision 의 downgrade (테이블·extension 재생성) 로 복원 경로 존재.

## Consequences

### 긍정

- 진단 흐름 단일 경로 (`통계 -> 분류 -> LLM narrative`) — retriever 분기·RAG context 절 제거로 가독성·유지보수성 우위
- DB 스키마 축소 (rag_documents·pgvector extension 제거), config 7 필드 제거, env 카탈로그 단순화
- 미활성 기능의 인지 부담 (문서·테스트 mock·운영 ingest CLI) 소멸
- F12 현황 선언성 정합 — RAG 잔재 0

### 부정·한계

- 도메인 지식 (외부 백서) grounding 부재 — LLM 자체 학습 지식에만 의존. 단 분류·권고는 결정론적 `recommendation.py` 라 narrative 표현 한정 영향
- 향후 도메인 지식 RAG 가 필요해지면 재도입은 새 ADR 의무 (본 ADR supersede). drop revision downgrade 로 스키마 복원 가능하나 ingest 파이프라인·추상은 재작성

## 관련 문서

- ADR 0024: AI 진단 RAG 도입 (본 ADR 이 supersede)
- ADR 0025: LLM 단일 provider (ollama) — 진단 narrative 단독 경로 유지
- ADR 0003: AI/LLM 활용 로드맵 (수치 환각 검증 3G절 — RAG 부재에도 hallucination 방어)
- `docs/architecture/diagnostic.md`: 진단 워커 흐름 (RAG infra 절 제거 후 단일 진실)
