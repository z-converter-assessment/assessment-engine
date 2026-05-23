# ADR 0010 — 진단을 규칙 기반으로 한정, LLM 분기 보류

상태: Accepted (2026-05-16), Refined by 0023 (2026-05-23) — scheduler cron 자동 발화 폐기 (본 ADR 본문 안 "스케줄러 매일 03시 자동" 본문 supersede). ADR 0003 "AI/LLM 활용 로드맵"·ADR 0004 "AI 진단 워커 아키텍처"의 LLM(narrative 합성) 활용 부분을 본 ADR로 정정.

## Context

ADR 0003에서 LLM을 진단 narrative 합성·리포트 생성 등에 활용하는 로드맵을 그렸고, ADR 0004는 그 실행 인프라(워커·스케줄러·`diagnostic_jobs`·LLM 토글 `mock`/`ollama`)를 구현. 본 시점 실제 운영 상태:

- `LLM_PROVIDER=mock`이 default. `MockLlmClient`는 외부 API 호출 없이 payload(통계 수치)를 결정론적 자연어 템플릿으로 변환만 — 사실상 규칙 기반.
- `ollama` 분기는 `NotImplementedError`. 사용자 정책(과금 발생 외부 API 금지) + ollama 배포·튜닝 운영 부담으로 활성 보류.
- 분류·권장(`classification` / `action` / `recommendation`)은 `recommendation.py`가 USE Method(AWS Compute Optimizer·Azure Advisor 임계값)로 결정. LLM이 결정하지 않음.
- 즉 운영 path의 모든 의사결정은 결정론 규칙. "AI 진단"이라는 명칭이 실제 동작과 불일치 → 운영자·고객 혼동.

## Decision

진단은 규칙 기반으로 한정. UI·docs·코드 docstring 모두 "AI 진단" 표현 제거 — scope에 따라 "환경 진단" / "서버 진단" / "진단"으로 정정.

기술 상태 유지:
- `LLM_PROVIDER` env·`MockLlmClient`·`ollama` 분기 stub은 코드에 보존 — 향후 도입 시 재활성 가능한 toggle point.
- ADR 0004 워커·스케줄러·`diagnostic_jobs` 인프라 그대로 — 진단 job lifecycle·polling·이력 관리에 그대로 사용.
- `MockLlmClient`의 결정론 narrative 합성은 운영 산출물 — 외부 API 부재 환경에서 규칙 기반 텍스트 출력 단일 진실.

명칭 매핑:
- scope=environment → "환경 진단" (전체 활성 서버 대상 발행, 사용자 trigger 만 — ADR 0023)
- scope=server → "서버 진단" (단일 또는 N대 batch 발행)
- 공통 진입점·이력 페이지는 단순 "진단"

## Consequences

장점:
- UI·docs 표현이 실제 동작과 일치 — 운영자·고객 혼동 제거.
- 정책 변경 시(과금 가능 외부 LLM 허용 등) 명칭은 그대로 두고 LLM 분기만 활성하면 됨 — 인프라 재설계 불필요.
- 고객 대상 산출물에 "AI"라는 과대 표현 제거 — 규칙 기반·USE Method 기반이라는 정확한 근거 제시.

단점:
- 향후 실 LLM 도입 시 명칭 재변경(또는 더 일반화) 필요. 다만 그 시점에 별도 ADR로 결정.
- `LLM_PROVIDER`·mock·ollama 같은 토글 자체는 보존 — code dead weight로 보일 수 있음. 다만 ADR 0004 인프라가 LLM 활용을 future-ready 상태로 두려는 의도라 toggle은 명확한 의도 표시.

## 정정 대상 ADR (historical record로 본문 보존)

| ADR | 상태 변경 | 정정 내용 |
|-----|----------|----------|
| ADR 0003 | Refined by 0010 | LLM 활용 로드맵 Phase 2(narrative 합성)는 본 ADR로 보류. Phase 3(리포트 생성·RAG 등) 역시 외부 LLM 도입 결정 후 재논의. |
| ADR 0004 | Refined by 0010 | 본 ADR 워커·스케줄러·`diagnostic_jobs` 인프라는 그대로 유효. "AI 진단" 명칭은 본 ADR(0010)로 "진단"으로 정정. `LLM_PROVIDER=mock` default + `ollama` 미구현 상태 그대로 보존. |
