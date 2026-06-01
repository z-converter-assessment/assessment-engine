# RAG seed catalog — 도메인 지식 sample

본 디렉토리는 RAG (Retrieval-Augmented Generation, ADR 0024) 안 자체 작성 sample 도메인 지식 catalog. 외부 license 의무 0 — 자체 정리한 right-sizing · USE Method 본질 요약.

운영자가 본 sample 만으로도 RAG 활성 catalog 즉시 검증 가능. 본격 운영 시 외부 백서 (Brendan Gregg blog · AWS Compute Optimizer · Azure Advisor 등) 를 직접 다운로드 후 같은 형식 (MD/Text) 으로 추가 ingest 정공.

## ingest 방법

```bash
# 1. RAG 활성 (dev/.env 안)
RAG_ENABLED=true

# 2. embedding 모델 pull (host 안 ollama)
ollama pull mxbai-embed-large

# 3. diagnostic-worker 재기동
docker compose up -d --force-recreate diagnostic-worker

# 4. sample 본 디렉토리 ingest (docker container 안)
docker compose exec diagnostic-worker bash -c '
  for f in /app/docs/rag-seed/*.md; do
    [ "$f" = "/app/docs/rag-seed/README.md" ] && continue
    python -m assessment_engine.rag.ingest "$f"
  done
'

# 5. ingest 검증
docker compose exec postgres psql -U assessment -d assessment \
  -c "SELECT source_type, count(*) FROM rag_documents GROUP BY source_type"
```

## sample catalog

| 파일 | 본문 |
|------|------|
| `use-method.md` | USE Method (Brendan Gregg) 본질 요약 — Utilization · Saturation · Errors 3 축 |
| `right-sizing-thresholds.md` | AWS Compute Optimizer + Azure Advisor 임계 catalog 요약 (CPU · Memory · iowait) |
| `classification-rules.md` | 본 엔진 안 classification 분류 catalog (over_provisioned · under_provisioned · idle · shutdown · optimal · insufficient_data) |

## 추가 외부 자료 가이드

본격 운영 시 추가 ingest 후보 (운영자 직접 다운로드 + MD 변환):

| 자료 | URL | 형식 |
|------|-----|------|
| USE Method (Brendan Gregg blog) | https://www.brendangregg.com/usemethod.html | HTML → pandoc 변환 |
| AWS Compute Optimizer docs | https://docs.aws.amazon.com/compute-optimizer/ | PDF → pdftotext 변환 |
| Azure Advisor docs | https://learn.microsoft.com/en-us/azure/advisor/ | HTML → pandoc 변환 |
| Brendan Gregg "Systems Performance" book chapters | (book) | 운영자 license 의무 |

## 추가 source_type catalog (미구현, 후 phase)

| source_type | 본문 | 상태 |
|------------|------|------|
| `domain_knowledge` | 본 sample + 외부 백서 | 본 phase 활성 |
| `operation_note` | 운영자 수동 입력 메모 (서버별 인시던트 · 특수성) | 보류 — 별도 phase 결정 |
| `peer_snapshot` | 본 환경 N대 통계 snapshot vector | 보류 — 별도 phase 결정 (vector 산출 책임 catalog 본질 검토 의무) |
