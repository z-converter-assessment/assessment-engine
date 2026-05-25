# 남은 작업 catalog

본 세션 안 합의·진행 완료 catalog + 보류 catalog + 본 phase 후 별도 결정 catalog 종합. 임시 자료 — `docs/temp/` 안 자유 작성, 본 repo 영구 문서 안 본 file 인용 0 의무.

## 본 세션 완료 catalog (종합)

### ADR 신규 (3)

| ADR | 본문 |
|-----|------|
| 0023 | scheduler cron 폐기 — trigger 채널 = web POST 사용자 명시 만 |
| 0024 | RAG 도입 (도메인 지식 phase) — pgvector + mxbai-embed-large + HNSW + ingest CLI |
| 0025 | LLM 단일 provider (ollama) — mock 폐기 + `LLM_PROVIDER` env 제거 |

### 본 phase 안 구현 완료

| 항목 | 본문 |
|------|------|
| LLM client | `OllamaLlmClient` 단일 (HTTP `/api/chat` + system prompt + temperature 0.2 + retry 3회 + observability) |
| 수치 환각 검증 | `verify.py` (정규식 + payload 안 숫자 set 비교 + 재생성 1회) |
| handler 단계 | extract → applying_rules → retrieving_context → generating_narrative → succeeded |
| submitter | active hash dedup + anchor 분 단위 truncate |
| DB schema | `diagnostic_jobs` + `rag_documents` (pgvector + HNSW) |
| RAG infra | `OllamaEmbeddingClient` (mxbai-embed-large + retry) + `PgVectorRetriever` + `rag/query.py` + `rag/ingest.py` CLI |
| 보고서 통합 | engineer view 안 자동 진단 + inline section + polling JS (`diagnostic-inline.js`) |
| UI catalog | AI badge (`.tag-ai`) 엔지니어 보고서 발행 버튼 안 표시 |
| 도메인 지식 seed | `docs/rag-seed/` 안 자체 작성 sample 3 (USE Method · 임계 · 분류 규칙) |
| `DIAGNOSTIC_ENABLED` env | 제거 — AI 진단 = 본 엔진 본질 기능 (옵션 X) |

### 활성 의무 catalog (운영자 액션)

```bash
brew install ollama
ollama pull llama3.1:8b
ollama pull mxbai-embed-large    # RAG 활성 시
ollama serve

# RAG 활성 (선택)
echo "RAG_ENABLED=true" >> dev/.env
docker compose -f dev/docker-compose.yml up -d --force-recreate diagnostic-worker

# 본 repo seed 일괄 ingest
docker compose -f dev/docker-compose.yml exec diagnostic-worker bash -c '
  for f in /app/docs/rag-seed/*.md; do
    [ "$f" = "/app/docs/rag-seed/README.md" ] && continue
    python -m assessment_engine.rag.ingest "$f"
  done
'
```

---

## 본 phase 후 별도 결정 catalog (ADR 의무)

### A. peer 통계 vector RAG (ADR 0024 보류)

본 환경 N대 통계 snapshot vector 활용 — 비슷한 패턴 서버 catalog 검색 + LLM context 안 인용.

본질 결정 의무 catalog:
- vector 산출 대상 = narrative vs 통계 수치 (벡터화 본문)
- vector 산출 시점 = 진단 완료 시 자동 / cron / on-demand
- 통계 -> vector 변환 본문 (텍스트화 후 embedding vs 직접 numerical normalize)
- self exclusion (검색 시 자기 자신 제외)
- retention (오래된 snapshot stale)
- cold start (초기 vector 0)

본 phase 미진행 사유: 본질 검토 catalog 본 시점 정공 (통계 vector 가 narrative 보다 효과적 catalog 측정 후 결정).

### B. 운영 노트 RAG (ADR 0024 보류)

운영자 수동 입력 메모 (서버별 인시던트 · 특수성) → embedding → RAG context.

본질 결정 의무 catalog:
- 입력 UI 진입점 (server detail 안 메모 폼 vs 별도 admin)
- 비동기 embedding worker (입력 즉시 vs 큐)
- 권한 catalog (운영자 권한 + 편집 이력)
- 한국어/영어 분리 (운영자 한국어 입력 vs 영어 통일)

본 phase 미진행 사유: 운영자 부담 catalog 본 시점 정공 + 효과 측정 catalog 본 phase 후 결정.

### C. 외부 유료 LLM API (Anthropic · OpenAI 등)

정책 catalog 본 시점 정공 — ADR 0025 안 "단일 ollama provider" 결정 + "외부 유료 API 호출 금지" 정책.

본질 결정 의무 catalog (정책 정정 시점):
- ADR 정정 + `LlmProvider` enum 재도입
- secret 채널 (`SecretStr` API key + `secrets_dir` 또는 env)
- multi-tenant PII masking (ADR 0003 3L절 — 본 시점 미적용)
- 비용 추적 (token usage + cost per request)
- rate limit 회피 (외부 API 안 rate limit 본질 catalog 본 시점 정공)

### D. LLM streaming response

ollama `/api/chat` `stream=true` + SSE 안 client 안 token 점진 표시 — UX 강화 (LLM 30초 대기 catalog 부분 표시).

본질 결정 의무 catalog:
- SSE endpoint 신규 (별도 router)
- uvicorn worker 점유 catalog (SSE = long-lived connection)
- polling JS → SSE 전환 catalog 본질 catalog 본 시점 정공

본 phase 미진행 사유: 본 시점 polling (3s) 본 시점 정공 + uvicorn worker 점유 catalog 본 시점 본질 catalog 본 시점 정공 (장점 vs 비용 비율 약).

### E. diagnostic_jobs retention 정책

ADR 0023 안 cron 폐기 catalog — 본 시점 retention 0 → row 무한 누적.

본질 결정 의무 catalog:
- retention 기간 (90일 default catalog 본 시점 정공)
- retention 발화 catalog (별도 cron · manual · cleanup script)
- 본 시점 본 catalog 본 시점 정공 catalog 본 시점 정공 — 별도 ADR 의무 catalog 본 시점 정공

본 phase 미진행 사유: 본 시점 진단 발행 빈도 catalog 본 시점 정공 (사용자 trigger 만) → row 누적 catalog 작음 catalog 본 시점 정공 → 본 phase 후 모니터링 catalog 본 시점 정공.

### F. trigger 모델 자동화 (TTL 조건부 자동)

ADR 0023 안 본 시점 사용자 trigger 만 결정 catalog. 다만 보고서 진입 시 자동 발행 (ensure_latest_or_submit) catalog 본 시점 정공 catalog 본 시점 catalog 본 시점 정공 — 본 시점 본 catalog 본 시점 정공 catalog 본 시점 정공 = "TTL 조건부" 본질 catalog 본 시점 정공.

본 시점 본 catalog 본 시점 정공 catalog 본 시점 정공:
- 본 시점 본 catalog 본 시점 정공 — 보고서 진입 시 자동 발행 catalog 본 시점 정공 (다만 active hash dedup 안 같은 anchor 시 재발행 차단)
- 본 시점 본 catalog 본 시점 정공 catalog 본 시점 정공 — anchor 분 단위 truncate 본 시점 같은 분 안 진입 시 dedup 자연 catalog
- 본 시점 본 catalog 본 시점 정공 catalog 본 시점 정공 — 별도 TTL catalog 본 시점 본질 catalog 본 시점 정공 (현재 catalog 충분)

### G. LLM 모델 동적 swap UI

운영자 UI 안 모델 catalog 본 시점 정공 catalog 본 시점 catalog 본 시점 정공 — env restart 정공 catalog 본 시점 정공 (UI catalog 본 시점 본질 catalog 본 시점 정공).

본 phase 미진행 사유: env restart 본 시점 본 catalog 본 시점 정공 — 본 catalog 본 시점 catalog 본 시점 정공 운영자 빈도 catalog 본 시점 정공 (모델 catalog 본 시점 정공 catalog 본 시점 catalog 본 시점 정공).

### H. 사용자 narrative 정정 UI

LLM 결과 안 잘못된 catalog 사용자 inline edit catalog 본 시점 정공.

본질 결정 의무 catalog:
- 권한 catalog (운영자 권한)
- 정정 이력 (audit log)
- 재발행 vs manual edit 본질 catalog

본 phase 미진행 사유: 사용자 신호 catalog 본 시점 정공 catalog 본 시점 catalog 본 시점 정공.

---

## 본 commit 전 의무 catalog (본 시점 사용자 결정 catalog 본 시점 정공)

1. pipeline-up.sh end-to-end 재검증 (이전 세션 catalog 본 시점 정공) — alembic + pgvector + 4 VM + RAG ingest + 진단 발행 동작 catalog
2. README.md 안 본 시점 catalog 본문 검증 — 본 세션 안 신규 catalog (RAG · ollama · scheduler 폐기) 반영 catalog 본 시점 정공
3. docs/development/testing.md 안 RAG test 가이드 추가 (선택)
4. commit — type prefix `feat` (RAG · ollama 단일 · 보고서 통합 본질)

---

## 본 catalog 본 시점 정공

본 file 본질 = 본 세션 안 catalog · 보류 catalog · 후 phase 결정 catalog 종합 catalog 본 시점 정공. `docs/temp/` 안 임시 자료 — 본 repo 영구 문서 안 본 file 인용 0 의무 (사용자 메모리 — 참고용 디렉토리는 인용도 금지).

본 시점 후 phase 결정 시점에 ADR 또는 별도 docs 안 catalog 본질 catalog 본 시점 정공.
