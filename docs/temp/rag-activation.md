# RAG 적용 가이드 (임시 정리)

> docs/temp 임시 문서 — self-contained (외부 공유 가능, 다른 문서·코드 참조 없이 단독 완결).
> 작성 2026-05-25. RAG 활성화 시점에 영구 문서로 승격 검토.

## 1. 현재 상태 (2026-05-25 기준)

- RAG 비활성: `RAG_ENABLED=false` (기본값).
- 진단 narrative = 규칙 기반 통계 + ollama LLM 자연어 합성. 도메인 지식 RAG 컨텍스트는 주입 안 됨.
- 비활성 시 워커 동작: RAG retrieve 단계에서 retriever 가 None 이라 skip, `payload['rag_context']=[]` 로 두고 LLM 이 통계만으로 narrative 생성.
- 진단 분류·권고 자체는 규칙 기반(USE Method 임계)이라 RAG 유무와 무관하게 정상 동작. RAG 는 narrative 의 "근거 풍부함"에만 영향.

## 2. RAG 가 하는 일

진단 시 도메인 지식(USE Method, AWS Compute Optimizer / Azure Advisor 등 right-sizing 백서)을 의미검색(pgvector 코사인 유사도)으로 retrieve 해서 LLM 프롬프트에 주입한다. 결과적으로 narrative 가 산업 표준 근거를 반영한다.

- scope 별 검색 쿼리 분리: server(단일 서버 지표 기반) vs environment(fleet 분포 기반).
- 검색 결과 상위 K개 chunk 를 LLM 프롬프트의 "관련 도메인 지식" 절에 삽입.
- 의사결정(분류/권고)은 규칙 기반이 결정 — RAG·LLM 은 설명 품질만 담당.

## 3. 활성화 전제 (필요 조건)

1. 임베딩 모델
   - 실제 의미검색: ollama 에 `mxbai-embed-large` (1024차원) pull. `EMBEDDING_PROVIDER=ollama`.
   - 또는 mock: `EMBEDDING_PROVIDER=mock` — deterministic 해시 임베딩. 의미검색 품질은 낮지만 비용 0, 파이프라인 검증용.
2. pgvector + rag_documents 테이블
   - pgvector extension + `rag_documents` 테이블 + HNSW 인덱스 필요.
   - DB 마이그레이션(`alembic upgrade head`)에 이미 포함 — migrate 실행 시 자동 생성. (TimescaleDB-ha 이미지에 pgvector 포함.)
3. RAG 자료 적재
   - ingest CLI 로 도메인 지식 문서를 chunk -> 임베딩 -> `rag_documents` UPSERT.
   - 프로젝트에 샘플 자료 디렉토리 존재(use-method / right-sizing-thresholds / classification-rules 등). 실제 운영 품질은 실 백서를 별도 확보·적재해야 함.
4. 플래그: `RAG_ENABLED=true`.

## 4. 활성화 단계

```
# 1) env 설정 (worker environment 또는 dev/.env)
RAG_ENABLED=true
EMBEDDING_PROVIDER=ollama          # 실제 의미검색 (mock = deterministic, 검증용)
EMBEDDING_MODEL=mxbai-embed-large
EMBEDDING_DIMENSION=1024

# 2) 임베딩 모델 pull (EMBEDDING_PROVIDER=ollama 시)
ollama pull mxbai-embed-large

# 3) DB 마이그레이션 (pgvector extension + rag_documents 생성)
alembic upgrade head               # dev compose 면 migrate 컨테이너가 자동 수행

# 4) 도메인 지식 자료 적재 (수동 CLI — 자동 ingest 없음)
python -m assessment_engine.rag.ingest <도메인 지식 문서.md>
#   샘플 자료(use-method / right-sizing-thresholds / classification-rules)를 각각 ingest

# 5) diagnostic-worker 재기동 (RAG_ENABLED 반영)

# 6) 진단 trigger 후 narrative 에 RAG 컨텍스트 반영 확인
```

## 5. 관련 설정 카탈로그

| env | 기본값 | 의미 |
|-----|--------|------|
| `RAG_ENABLED` | `false` | RAG 활성화 토글 |
| `EMBEDDING_PROVIDER` | `mock` | `mock`(해시, 비용 0) / `ollama`(실제 의미검색) |
| `EMBEDDING_MODEL` | `mxbai-embed-large` | 임베딩 모델 (ollama pull 의무) |
| `EMBEDDING_DIMENSION` | `1024` | 임베딩 차원 |
| `EMBEDDING_TIMEOUT_SECONDS` | `30.0` | 임베딩 호출 timeout |
| `RAG_TOP_K` | `5` | 검색 top-k chunk 수 |
| `RAG_MAX_CONTEXT_CHARS` | `4000` | LLM 프롬프트 내 RAG 컨텍스트 절 최대 길이 |

(LLM 자체는 `OLLAMA_BASE_URL` / `OLLAMA_MODEL=llama3.1:8b` / `LLM_TIMEOUT_SECONDS=60` 별도 — RAG 비활성이어도 LLM 은 필요.)

## 6. 검증

```
# pgvector extension 활성
SELECT * FROM pg_extension WHERE extname='vector';

# 적재 카운트
SELECT source_type, count(*) FROM rag_documents GROUP BY source_type;

# 진단 후 narrative 에 도메인 지식 근거 반영 여부 육안 확인
```

## 7. 활성화 전 인지할 주의·미완 사항

- 실제 도메인 백서 자료는 repo 미포함 — 샘플 디렉토리는 형식 예시. 운영 품질 원하면 실 백서 확보·적재 필요.
- RAG retrieve 실패(임베딩/pgvector 오류) 시 silent fallback — narrative 는 RAG 없이 생성되고 진단 자체는 실패하지 않음.
- 임베딩 모델·차원 변경 시 동시 갱신 의무: `EMBEDDING_MODEL` + `EMBEDDING_DIMENSION` env + alembic revision(`embedding vector(N)` 타입) + 전체 `rag_documents` 재적재(ingest 재실행) + HNSW 인덱스 재build.
- 운영 활성화 권장 순서: mock embedding 으로 파이프라인 검증 -> ollama embedding 으로 전환 후 품질 검증.

## 8. 이번 주 진행 전제 — RAG 비활성 유지

- `RAG_ENABLED=false` 유지.
- 필요 환경: ollama serve + `llama3.1:8b` (LLM) 만. `mxbai-embed-large` pull · pgvector 적재 · ingest 모두 불필요.
- 진단 narrative = 규칙 기반 통계만 반영 (도메인 지식 RAG 컨텍스트 없음). 분류·권고는 규칙 기반이라 정상.
- pgvector extension · `rag_documents` 테이블은 마이그레이션으로 DB 에 생성되지만 사용 안 함(빈 테이블로 방치 — 무해).
- 즉 이번 주는 "LLM narrative 진단"만 동작, "RAG 근거 보강"은 off.
