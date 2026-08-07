---
name: schema-contract-audit
description: Audit schema and payload drift between assessment-engine and assessment-agent. Use when the user asks to audit schema consistency, contract drift, payload compatibility, or cross-repository schema differences.
---

# 스키마 계약 감사

루트 `AGENTS.md`와 `.agents/reviewers/schema-contract-auditor.md`를 순서대로 읽고 감사 절차와 출력 기준을 그대로 적용한다.

엔진과 외부 에이전트 저장소를 읽기 전용으로 비교한다. 수정하지 않고 계약 drift만 보고한다.
