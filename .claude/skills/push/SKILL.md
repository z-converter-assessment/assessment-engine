---
name: push
description: TRIGGER when the user wants to push a feature branch to origin ("푸시", "/push", "push it up"). Verifies the branch is not main/develop, then pushes with upstream tracking. Protected branches are blocked server-side by ruleset — use /pr instead.
---

# /push — feature 브랜치 원격 push

로컬 게이트는 없다. 보호 브랜치 차단은 GitHub ruleset 이, 코드 검증은 PR CI 가 담당한다.

## 절차

1. 브랜치 확인 — `git branch --show-current`. `main`·`master`·`develop` 이면 abort + `/pr` 안내.
2. 상태 확인 — `git status --short`. 미커밋 있으면 `/commit` 먼저 권유.
3. push — `git push -u origin <branch>` (upstream 설정).
4. push 성공 후 PR 필요하면 `/pr` 안내.

## 규율

- 보호 브랜치 직접 push X — `/pr` 로 develop base PR.
- push 자체는 사용자 의도 확인된 액션이라 자동 X — 명시 요청 시 실행.
