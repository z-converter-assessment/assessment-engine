---
name: push
description: TRIGGER when the user wants to push a feature branch to origin ("푸시", "/push", "push it up"). Verifies branch (not main/master), lets the pre-push hook run local-ci, pushes with upstream tracking. main/master direct push is blocked by .githooks/pre-push — use /pr instead.
---

# /push — feature 브랜치 원격 push

작성 가이드(opt-in). 최종 강제 게이트는 `.githooks/pre-push` — main 직접 push 차단 + push 전 `local-ci(develop)` 통과.

## 절차

1. 브랜치 확인 — `git branch --show-current`. main/master 면 abort + `/pr` 안내 (직접 push 금지, PR 경유).
2. 상태 확인 — `git status --short`. 미커밋 있으면 `/ship`(문서 정합 + commit) 먼저 권유.
3. push — `git push -u origin <branch>` (upstream 설정). pre-push hook 이 local-ci(develop) 자동 실행 — 실패 시 push 거부되니 원인 수정 후 재시도.
4. push 성공 후 PR 필요하면 `/pr` 안내.

## 규율

- main/master 직접 push X — `/pr` 로 develop base PR.
- local-ci 우회(`--no-verify`)는 사용자 명시 시만 — 깨진 코드 원격 유입 위험 고지.
- push 자체는 사용자 의도 확인된 액션이라 자동 X — 명시 요청 시 실행.
