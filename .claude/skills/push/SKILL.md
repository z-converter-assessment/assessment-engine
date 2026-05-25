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

## Pre-check (push 직전 의무) — CI 실패·이메일 폭탄 회피

push 후 GitHub Actions 가 발화하는 워크플로 (`.github/workflows/`):

| trigger | 워크플로 | 검증 항목 | 로컬 사전 검증 |
|---------|----------|-----------|----------------|
| PR open (자동 PR 만들 가능성 큼) | `ci.yml` | ruff lint + pytest unit/integration + coverage + uv build wheel | `uv run ruff check .` + `uv run pytest tests/` |
| PR open (paths: models·migrations·pyproject) | `alembic-check.yml` | ORM ↔ migrations 라운드트립 정합 | `uv run alembic check` |
| PR open (paths: pyproject·uv.lock) | `security.yml` | pip-audit CVE | 로컬 실행 비용 큼 — skip 허용 (CI 검증) |
| PR open | `codeql.yml` | SAST (SQL injection·XSS 등) | 로컬 불가 — CI 검증 |
| main/develop 직접 push (안전망) | `ci.yml` | 동일 | 동일 |
| main push (자동 발화) | `release-please.yml` | Conventional Commits 분석 → Release PR 생성 | `git log` commit message convention 확인 |

병렬 Bash 로 본 검증 실행 (CodeQL · pip-audit 제외):
```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -q
# paths 영향 시:
uv run alembic check     # src/assessment_engine/db/models/ 또는 migrations/ 변경 시
uv lock --check          # pyproject.toml 또는 uv.lock 변경 시
```

실패 항목 있으면 push 차단 — 수정 + 새 commit 후 재시도. commit skill 의 pre-check 와 의도 동일 (commit 시점에 한 번 검증되지만 push 시점에도 한 번 더 — commit 후 추가 stage·amend·rebase 가능성 회피).

## 사전 검증 (브랜치 정책)

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