---
name: wrap-up
description: TRIGGER when user requests feature wrap-up before commit/PR ("마무리", "/wrap-up", "기능 정리"). Runs 5-stage workflow per docs/development/wrap-up.md — doc consistency / code review / tests / README / CLAUDE.md and related docs. Confirm only at stage boundaries; within-stage changes are automatic. Never auto-trigger commit/PR.
---

# wrap-up — 기능 개발 마무리 5단계 오케스트레이션

본 skill 은 `docs/development/wrap-up.md` 명세를 실행한다. 단계별 체크리스트·통과 기준·도구 매핑은 명세 단일 진실 — 본 skill 은 절차·진행 정책·도구 dispatch 만.

## 진입 조건

- feature branch 위 (`main` · `master` 직접 X — 위반 시 abort + 사용자 보고).
- 기능 동작 완성 — 사용자 명시 또는 `/run` · `/verify` 통과 (사용자 컨펌).
- 사용자 commit · PR 명시 요청 전 (메모리 `feedback_no_commit_pr_mention.md`).

진입 시 `git branch --show-current` · `git status` · `git log --oneline main..HEAD` 동시 확인 후 사용자에게 1줄 요약 + 진행 OK 받기.

## 진행 정책

- 자율 진행. 결정 필요 사항(스코프 확장 / 알려진 갭 / Stage 5 큐 항목)만 사용자 컨펌. 그 외 정석 정정은 즉시 적용 + 카탈로그 기록.
- 단계 경계는 알림 + 결과 보고만 — 다음 Stage 즉시 진입. 사용자가 명시적으로 정지·추가 컨펌 요청 시에만 대기.
- 매 Stage 진입 전 1줄 알림: `Stage N — <목적 1줄> (도구: X)`.
- 매 Stage 종료 후 보고: 변경 파일 / 자율 처리 항목 / 결정 필요 사항 / 다음 Stage 큐.
- 1분 진행 신호 무 시 stuck 판단 → abort + 진단 (메모리 `feedback_one_minute_timeout.md`).
- 사용자 명시 없이 pytest 자동 실행 0건 (메모리 `feedback_no_test_runs.md`).
- 본 명세(wrap-up.md) 자체의 self-audit 시 메타 인용 제외 — `--glob '!docs/development/wrap-up.md' --glob '!.claude/CLAUDE.md'` (명세 1절 "Self-audit 메타 인용 제외" 절).

## 5 Stage 실행

### Stage 1 — 문서 정합성 정리 (17 항목)
- 도구: `/docs-sync` skill 호출. 매핑 표는 `.claude/skills/docs-sync/SKILL.md` 단일 진실.
- 체크리스트: `docs/development/wrap-up.md` 2절 [1.1]~[1.17] — 정합 5 / 중복 4 / 간결 3 / 엄밀 3 / 원칙 2.
- CLAUDE.md · ADR · tradeoffs 후보는 Stage 5 큐로만 적재 (본 단계 변경 X).
- 산출물: `docs/architecture/*` · `docs/operations/*` · `docs/products/*` · `docs/tradeoffs.md`.

### Stage 2 — 코드 리뷰 (정석 + 명문 원칙, 17 항목)
- 도구: code-reviewer 에이전트 1회 발동 (`Agent(subagent_type='code-reviewer', ...)`) + `/simplify` skill.
- 체크리스트: `docs/development/wrap-up.md` 3절 [2.1]~[2.17] — 정석 6 / 명문 규약 매핑 9 / 중복 2.
- 정석 idiom 과 본 repo 명문 규약(F1~F11 · #B · #C5 · #E1 P1~P4 · ADR) 양자 모두 충족 의무.
- 에이전트 Error 즉시 수정 / Warning 사용자 결정 위임 / Info 보고만.
- 코드 수정 시 Stage 1 재실행 큐 적재.
- 산출물: `src/` 코드.

### Stage 3 — 단위·모듈 테스트 수정 (13 항목)
- 도구: `/test-write` skill.
- 체크리스트: `docs/development/wrap-up.md` 4절 [3.1]~[3.13] — 정합 5 / 정석 5 / 원칙 3.
- pytest 자동 실행 0건 — 단계 종료 시 사용자에게 "테스트 실행하시겠습니까?" 1회 옵션만.
- 코드 부족 발견 시 Stage 2 재실행 큐 적재.
- 산출물: `tests/unit/` · `tests/integration/`.

### Stage 4 — README.md 갱신 (11 항목)
- 도구: 수동 Read + Edit. skill 없음 (README 는 entry voice — 사용자 결정).
- 체크리스트: `docs/development/wrap-up.md` 5절 [4.1]~[4.11] — 정합 5 / 간결 3 / 원칙 3.
- 산출물: 루트 `README.md`.

### Stage 5 — CLAUDE.md · 관련 영구 문서 최종 수정 (12 항목)
- 도구: 수동 Read + Edit. Stage 1·2·3 큐 일괄 처리.
- 체크리스트: `docs/development/wrap-up.md` 6절 [5.1]~[5.12] — 정합 5 / 정석 4 / 원칙 3.
- ADR 신규는 마지막 번호 + 1 단조 증가. 결정 변경은 새 ADR + 이전 ADR `Status: Superseded`.
- 산출물: `CLAUDE.md` · `docs/adr/*` · `docs/architecture/*` · `docs/tradeoffs.md` · `docs/README.md` · `docs/operations/observability.md`.

## 루프 처리

Stage 2~5 변경이 이전 Stage 영향 시 해당 Stage 재실행. 최대 3 cycle, 4 cycle 진입 시 사용자에게 정지·재설계 제안 (`docs/development/wrap-up.md` 1절).

## 종료

5 Stage 통과 + 사용자 컨펌 → 종료 보고:
- 누적 변경 파일 카탈로그 (코드 / 문서 / 테스트 분류).
- 사용자 결정 위임된 Warning · 알려진 갭 카탈로그.
- 다음 액션 후보는 사용자가 먼저 언급할 때만 제안.

commit · PR 자동 트리거 X. 사용자 명시 시 `/commit` · `/push` · `/pr-create` 별도 발동.

## 금지

- commit · PR 자동 실행 또는 사용자 명시 전 commit · PR 옵션 제안 (메모리 `feedback_no_commit_pr_mention.md`).
- 단계 안 사용자 컨펌 요청 (단계 경계에서만).
- 사용자 명시 없이 pytest 자동 실행 (메모리 `feedback_no_test_runs.md`).
- 단계 건너뛰기 — 사용자가 명시적으로 "Stage N 건너뛰기"라 하지 않는 한 모두 진행.
- 본 skill 안에 체크리스트 본문 중복 — 명세(`docs/development/wrap-up.md`) 단일 진실, 본 skill 은 절차만.
