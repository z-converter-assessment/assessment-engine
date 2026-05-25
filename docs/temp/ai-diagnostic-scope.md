# 이번 주 AI 진단 진행 범위 (RAG 제외)

> docs/temp 임시 문서 — self-contained. 작성 2026-05-25.
> 이번 주 작업 범위 확정. RAG(도메인 지식 검색)는 범위 밖으로 명시 제한.

## 0. 한 줄 정의

AI 진단을 "규칙 기반 분류·권고 + ollama LLM narrative"까지만 동작시킨다. RAG(도메인 지식 의미검색)는 비활성 고정, 이번 주 손대지 않는다.

## 1. 포함 (이번 주 구현·검증 대상)

| # | 항목 | 내용 |
|---|------|------|
| 1 | 진단 trigger | web POST `/api/diagnostics` (환경/서버 scope). 환경·서버 보고서 inline 자동 발행 포함 |
| 2 | 통계 집계 | 규칙 기반 USE Method — 분류(idle/over/under/optimal 등) + 권고. scope별 분리 (server=단일 서버, environment=fleet 분포) |
| 3 | LLM narrative | ollama `llama3.1:8b` 호출. scope별 프롬프트. 환각 검증(narrative 숫자 vs payload 대조, 재생성 1회) |
| 4 | 저장·표시 | `diagnostic_jobs` DB 저장, Redis polling 캐시, web 결과 표시(진행 단계 + narrative) |
| 5 | LLM 서버 연결 | 외부 ollama(OpenStack VM 등). diagnostic-worker 에 `OLLAMA_BASE_URL` 주입 |

## 2. 제외 (이번 주 안 함 — RAG 경계선)

- RAG 도메인 지식 검색 (pgvector 의미검색 retrieve).
- 임베딩 (`mxbai-embed-large` pull, `EMBEDDING_PROVIDER=ollama`).
- ingest CLI 도메인 자료 적재.
- narrative 에 도메인 백서 근거 주입 — 이번 주 narrative 는 규칙 기반 통계만 반영.

RAG 활성화 전체 절차는 별도 정리(`docs/temp/rag-activation.md`)에 있으며, 이번 주는 적용하지 않는다.

## 3. 범위 고정 설정 (RAG 차단)

- `RAG_ENABLED=false` 고정 (기본값이지만 명시 권장).
- `EMBEDDING_PROVIDER` 는 RAG off 라 미사용 — 값 무관.
- diagnostic-worker 주입: `OLLAMA_BASE_URL`, `OLLAMA_MODEL=llama3.1:8b`, `LLM_TIMEOUT_SECONDS`(기본 60).

RAG off 시 워커는 retrieve 단계에서 retriever 가 None 이라 skip 하고 `rag_context=[]` 로 둔다. LLM 은 통계만으로 narrative 를 생성한다 (도메인 지식 없이).

## 4. 전제 환경

- 인프라: postgres + redis + rabbitmq.
- 스키마: `alembic upgrade head`.
  - 주의: RAG off 여도 마이그레이션이 `CREATE EXTENSION IF NOT EXISTS vector` + `rag_documents` 테이블을 생성한다. 따라서 postgres 에 pgvector extension 이 설치돼 있어야 `upgrade head` 가 성공한다 (dev 의 timescaledb-ha 이미지엔 포함). 일반 postgres 면 pgvector 설치 필요. `rag_documents` 테이블은 빈 채로 방치(무해).
- diagnostic-worker 기동.
- LLM: 외부 ollama serve + `llama3.1:8b` pull. 외부 호스트면 ollama 측 `OLLAMA_HOST=0.0.0.0:11434` 로 외부 listen + 방화벽/보안그룹에서 worker 호스트만 11434 허용.
- web 기동 (trigger + polling).

## 5. 컴포넌트별 LLM env 주입

| 컴포넌트 | OLLAMA_BASE_URL | 이유 |
|----------|-----------------|------|
| diagnostic-worker | 주입 | LLM narrative 실제 호출 |
| web | 불필요 | 진단 MQ publish + polling 만 |
| consumer | 불필요 | 메트릭 수집만 |

## 6. 정상 동작 경계 (장애·미구현 인지)

- ollama 미연결/지연: 60초 timeout -> job `status=failed`. 진단 결과 없음. LLM 서버 가동 필수.
- worker 진행 중 강제 종료: `status=running` job 영구 stale (자동 정리 미구현 — 수동 SQL 필요). 정상 진단 동작은 막지 않으나 prod 운영 전 해결 대상.
- 분류·권고는 규칙 기반이라 RAG·LLM 유무와 무관하게 결정론적. LLM 은 narrative 설명 품질만 담당.

## 7. 다음 단계 — 로직이 본 범위와 일치하는지 검증 (문서 정리 후 별도)

본 문서로 범위 확정 후, 코드가 범위대로 동작하는지 점검할 항목:

1. `RAG_ENABLED=false` 시 worker 가 RAG 단계를 정상 skip 하는가 (retriever=None, `rag_context=[]`, 진단 실패 유발 없음).
2. 환경/서버 진단 둘 다 RAG 없이 narrative 가 생성되는가.
3. RAG 관련 코드·설정이 "비활성 분기"로만 격리됐는가 — 실수로 RAG 를 강제 활성화하거나, RAG 미적재 상태가 진단을 깨뜨리는 경로가 없는가.
4. pgvector 마이그레이션이 RAG off 환경에서도 안전히 통과하는가 (extension 부재 시 실패 가시화).
5. ollama 미연결 시 timeout -> failed 흐름이 깔끔히 처리되는가 (job 상태·에러 메시지).
