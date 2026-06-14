# ADR 0025 — LLM 단일 provider 통합 (ollama), mock 폐기

상태: Superseded — AI 진단(LLM narrative) 기능 폐기 (2026-06-14)

이전 상태: Proposed (2026-05-24)

## Context

ADR 0004 (Refined by 0010 + 0023) LLM 토글 결정 = `LLM_PROVIDER=mock|ollama` 분기 + `MockLlmClient` deterministic template + `OllamaLlmClient` stub (`NotImplementedError`). ADR 0010 = "LLM 분기 보류 — mock default 유지, ollama 미구현". 본 결정 본 시점 본질 catalog 검토:

1. mock = deterministic template 합성 — 실제 LLM 호출 0. 본 시점 mock 활용 = "사실상 규칙 기반 진단 — narrative 도 결정론".
2. ollama 활용 catalog 본 시점 본질 = 운영자 정책 (과금 발생 외부 유료 API 금지) + 로컬 무료 LLM 정합 — 본 phase 안 ollama 구현 의무.
3. mock vs ollama 분기 본질 catalog 검토 — 본 시점:
   - 개발 환경 안 ollama 호출 catalog 본 시점 catalog 본 시점 정공 (host 안 `ollama serve` 의무) — 본질 부담 적음
   - 운영 환경 안 ollama 단독 catalog 본 시점 정공
   - mock 본 시점 본질 = test 용 deterministic. 다만 본 시점 test 안 unittest.mock.AsyncMock 활용 catalog 가능 — 별도 클래스 보존 의무 약
4. 본질적 의문 = "분기 유지 vs 단일 통합" — 본 시점 단일 통합 정공:
   - 코드 단순화 (composition root 분기 제거, env 1개 제거)
   - 운영 일관성 (dev/prod 동일 LLM 호출 흐름 — 본질 catalog 정합)
   - 환각 검증 catalog 본 시점 본질 (ollama narrative 안 검증 단계 본질 catalog)

## Options

### A. 현 상태 유지 — mock default + ollama 분기

- 장점: 외부 의존 0 (mock default), 환각 위험 0 (deterministic template)
- 단점: 사실상 규칙 기반 진단 — "AI 진단" 본질 부재. ollama 활용 catalog 본 시점 운영자 명시 활성 의무 (UX 부담)

### B. 단일 ollama 통합, mock 폐기

- 장점: 코드 단순 (분기·env 제거), dev/prod 일관 (실제 LLM 호출 통일), "AI 진단" 본질 회복
- 단점: dev 안 host ollama 활성 의무 (운영자 부담), 환각 검증 catalog 본 시점 본질 의무, LLM latency (CPU 10~30s · GPU 수초)

### C. 외부 유료 API 추가 (Anthropic / OpenAI 등)

- 장점: 본격 LLM 품질, 응답 빠름 (TTFT 1~3s)
- 단점: 비용 발생 (정책 위반), secret 채널 의무, multi-tenant 환경 안 PII masking 의무

## Decision

옵션 B 채택.

근거:

1. dev 환경 안 host ollama 활성 = 운영자 1회 setup 만 (`brew install ollama` + `ollama pull llama3.1:8b` + `ollama serve`). docker compose 안 ollama service 추가 없이 host 활용 정공 (macOS Metal 가속 + Linux native GPU).
2. 단일 provider catalog = dev/prod 일관 — LLM 호출 흐름·환각 검증·latency 본질 모두 동일 catalog 검증 가능.
3. mock 본 시점 본질 = test 용 deterministic. test 안 `unittest.mock.AsyncMock(return_value="...")` 활용 catalog 정합 — 별도 mock 클래스 보존 의무 약.
4. "AI 진단" 명칭 본질 회복 — ADR 0010 안 "사실상 규칙 기반" historical 본문 supersede.
5. 외부 유료 API (옵션 C) 본 시점 정책 catalog 위반 — 별도 ADR 정정 catalog 본질 의무.

## Architecture

### 코드 변경

- `src/assessment_engine/diagnostic/llm/mock.py` 삭제 (`MockLlmClient` + `_server_narrative` + `_environment_narrative` + `_fmt_*`)
- `src/assessment_engine/diagnostic/main.py:_build_llm_client` 단순화 — `LLM_PROVIDER` 분기 제거, 단일 `OllamaLlmClient` 반환
- `src/assessment_engine/config.py` 안 `llm_provider`·`llm_mock_latency_seconds` 필드 제거
- `src/assessment_engine/diagnostic/llm/base.py` docstring 정정 — "LLM_PROVIDER 분기" → "단일 provider"

### env 변경

- `LLM_PROVIDER` env 제거
- `LLM_MOCK_LATENCY_SECONDS` env 제거
- `OLLAMA_BASE_URL` + `OLLAMA_MODEL` 유지 (운영자 명시 catalog)
- dev/docker-compose.yml `diagnostic-worker.environment` 안 default = `OLLAMA_BASE_URL=http://host.docker.internal:11434` + `OLLAMA_MODEL=llama3.1:8b`

### test 변경

- `tests/unit/test_diagnostic_mock_llm.py` 삭제 (mock 자체 test catalog)
- `tests/unit/test_diagnostic_handler.py` 안 LLM mock = `AsyncMock(return_value="narrative")` 활용 catalog 유지

### docs 변경

- `docs/architecture/diagnostic.md` 안 "LLM 토글" 본문 정정 → 단일 provider
- `docs/operations/env.md` 안 `LLM_PROVIDER` + `LLM_MOCK_LATENCY_SECONDS` 카탈로그 제거
- `docs/operations/deployment.md` 안 multi-node 분리 catalog 정정
- `docs/products/server-report.md` + `environment-report.md` 안 mock.py 참조 → ollama.py 정정

### 환각 검증 catalog (별도 task)

본 ADR 결정 직후 별도 task — handler 안 generating_narrative 단계 안 수치 환각 검증 (정규식 추출 + payload 안 숫자 존재 확인 + 재생성 1회 + 실패 시 `mark_failed('llm_hallucination')`). ADR 0003 3G절 원칙 정공.

## Consequences

### 긍정

- 코드 단순 — 분기 제거, env 2개 제거, mock 자체 제거
- dev/prod 일관 — 실제 LLM 호출 통일
- "AI 진단" 본질 회복 (ADR 0010 안 "사실상 규칙 기반" historical 본문 supersede)
- 외부 유료 API 도입 catalog 본 시점 추후 결정 catalog — 본 시점 본질 catalog 영향 0 (단순 추가)

### 부정·한계

- dev 안 host ollama 활성 catalog 운영자 부담 (1회 setup) — 다만 본 시점 본질 catalog 정공
- LLM latency (CPU 10~30s · GPU 수초) — UI progress_stage 4 단계 표시 catalog 사용자 인내심 제공
- LLM 미가동 시 진단 발행 timeout (`LLM_TIMEOUT_SECONDS=60` default) → `mark_failed('llm_timeout')` 흡수 — 본 시점 본질 정공
- 환각 위험 (LLM 안 본질 catalog) — 별도 task 안 검증 catalog 의무

### 전환 경로 (외부 유료 API 도입 시점)

- ADR 정정 + `LlmProvider` enum 본질 재도입 + secret 채널 (`SecretStr` API key) 의무
- 본 시점 본 catalog 본 시점 정공 catalog 본 시점 catalog 본 시점 정공 — 본 결정 본 시점 catalog 본 시점 정공

## 관련 문서

- ADR 0004: 진단 워커 아키텍처 (LLM 토글 본문 supersede)
- ADR 0010: 진단 규칙 기반 한정 (LLM 분기 보류 본문 supersede)
- ADR 0024: AI 진단 RAG 도입 (Superseded by 0039 — RAG 제거, ollama prompt RAG context 절 폐기)
- `docs/architecture/diagnostic.md` "LLM 토글" 절 — 단일 provider 본문
- `docs/operations/env.md` `OLLAMA_*` 카탈로그
