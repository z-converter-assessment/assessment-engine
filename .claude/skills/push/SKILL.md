---
name: push
description: TRIGGER when user requests push ("푸시", "/push", "push it"). Branch tracking 확인 후 안전하게 push. main/master 직접 push 차단.
---

# push — 안전한 원격 푸시

## 브랜치 전략

| 브랜치 | 용도 |
|--------|------|
| main | 배포용. 직접 push 금지 |
| develop | 개발 통합. PR로만 머지 |
| feature/xxx | 기능 |
| fix/xxx | 버그 |
| chore/xxx | 설정 |

## 사전 검증

1. 현재 브랜치 확인:
   ```bash
   git branch --show-current
   ```

2. main/master 직접 push 차단:
   - 현재 브랜치가 `main` 또는 `master`면 → 사용자에게 강력 경고. 명시적 재요청 없으면 중단.
   - 정상 워크플로우는 feature/fix/chore 브랜치 → PR.

3. 원격 추적 상태 확인:
   ```bash
   git status -sb
   git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null
   ```

## 푸시 실행

- 첫 push (원격 추적 없음) → `git push -u origin <current-branch>`
- 이후 push → `git push`

## force push 정책

- `--force` 또는 `--force-with-lease`는 사용자 명시 요청 시에만
- main/master에 force push 절대 금지 (사용자가 요청해도 한 번 더 확인)

## 결과 보고

- push 성공 → 원격 URL 형태로 보고 (예: `origin/feature/skills-refactor`)
- 실패 → stderr 그대로 노출 + 흔한 원인(non-fast-forward / hook 실패) 안내

## 후속

push 후 자동으로 PR 생성하지 않음. 별도 워크플로우 (`/pr-create`).