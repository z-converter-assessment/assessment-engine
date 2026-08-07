---
name: commit
description: 작업 중인 변경을 커밋할 때 사용한다. lint만 실행하고 push, PR 생성, 문서 동기화는 수행하지 않는다.
---

# 작업 커밋

feature branch 의 working diff 를 커밋한다. 가볍게 자주 커밋하는 것이 목적이라 무거운 검증을 걸지 않는다.

검증 배치 단일 진실 = `docs/guides/pre-pr-checklist.md` 0절. 본 skill 은 그중 커밋 단계(lint)만 담당한다.

## 진입 조건

- feature branch 위 (`main`/`master`/`develop` 이면 abort + 보고).
- 커밋할 변경 존재. 없으면 보고 후 종료.

## 절차

1. `git branch --show-current`, `git status --short`, `git diff --stat` — 변경 범위 1줄 요약.
2. `uv run ruff check .` — 초 단위 게이트만. NG 면 수정 후 재시도.
   테스트는 돌리지 않는다. 단위 테스트는 develop PR 의 CI 가 담당한다.
3. 무관한 변경이 섞였으면 (다른 작업의 잔여물, 에디터 설정 등) 사용자에게 확인하고 경로 제외 staging.
4. 커밋 — 현황 선언형 메시지. 무엇을 왜 바꿨나.

## 커밋 메시지

- Conventional Commits type prefix (`feat`/`fix`/`docs`/`chore`/`refactor`/`perf`/`test`/`build`/`ci`/`style`/`revert`).
- 제목은 현황 선언. 과거형/경위 서술 금지.
- 본문은 필요할 때만 — 왜 그렇게 했는지가 코드에서 안 보일 때.
- 작성 주체 메타데이터(`Co-Authored-By`/`Generated with`) 절대 금지.

## 금지

- 문서 갱신/ADR 작성 — develop PR 단계 책임이다. 커밋마다 문서를 고치면 같은 PR 안에서 재작업이 쌓인다.
- 리뷰 프롬프트 실행 — PR 생성 절차가 담당.
- pytest 전체 실행 — 사용자 명시 시에만.
- push/PR 생성 — PR 생성 절차가 담당한다.
