# GitHub UI Setup

본 repo CI workflow·release-please 자동화·branch policy를 정상 작동시키기 위해 GitHub 측에서 한 번만 활성해야 하는 설정 카탈로그. 본 repo 코드 영역 밖이라 운영자가 GitHub Settings에서 수동 활성.

## 1. Actions 권한 (의무)

위치: Settings → Actions → General → Workflow permissions

| 항목 | 활성 의무 | 사유 |
|------|----------|------|
| Read and write permissions | 활성 | release-please가 commit·tag push 가능하게 |
| Allow GitHub Actions to create and approve pull requests | 활성 | release-please가 Release PR 생성 |

본 옵션 비활성이면 release-please workflow가 동작해도 PR 생성 권한 부족으로 실패.

## 2. CodeQL Default Setup (권장)

위치: Settings → Code security → Code scanning → CodeQL → "Default setup" → Enable

본 repo의 `.github/workflows/codeql.yml`이 이미 SAST 실행하지만, GitHub UI의 Default setup도 함께 활성하면:
- Security 탭의 alert dashboard 통합 가시화
- main 기준 baseline scan 자동 등록

UI 활성 안 하고 workflow만 두어도 동작 — 단 Security 탭 통합이 부족.

## 3. Branch Protection Rule (정석)

위치: Settings → Branches → Add branch protection rule

### 3.1. `main` branch

| 옵션 | 값 | 사유 |
|------|-----|------|
| Branch name pattern | `main` | |
| Require a pull request before merging | 활성 | 직접 push 차단, PR 강제 |
| Require approvals | 1+ (선택) | 1인 운영이면 0, 팀이면 1+ |
| Dismiss stale pull request approvals when new commits are pushed | 활성 | 강제 push로 우회 차단 |
| Require status checks to pass before merging | 활성 | CI workflow 통과 의무 |
| Required status checks | `pr-title-check`·`ci / ruff + hadolint`·`ci / pytest (unit)`·`ci / wheel build`·`ci / pytest (integration)`·`alembic-check` (관련 PR 시) | release-please bot도 본 check 통과해야 Release PR merge 가능 |
| Require branches to be up to date before merging | 활성 | merge 직전 main rebase 강제 |
| Require conversation resolution before merging | 활성 | PR comment 미해결 차단 |
| Require linear history | 활성 (선택) | merge commit 금지, squash·rebase만 |
| Do not allow bypassing the above settings | 활성 | admin도 우회 불가 — 본 옵션이 정책의 마지막 안전망 |
| Allow force pushes | 비활성 | git history 보호 |
| Allow deletions | 비활성 | branch 삭제 차단 |

### 3.2. `develop` branch

`main`과 동일 패턴 — `develop`도 PR 강제. 단:
- "Required status checks" 동일 적용
- "Require linear history" 선택 (develop는 통합 branch라 자유도 좀 더)
- "Do not allow bypassing" 활성

## 4. Tag Protection Rule (정석)

위치: Settings → Tags → New tag protection rule

| 옵션 | 값 | 사유 |
|------|-----|------|
| Pattern | `v*` | semver release tag만 적용 |
| Allowed actors | release-please bot + owner | 사용자 수동 tag 생성 차단. release-please bot만 자동 tag push 가능 |

release-please bot이 `GITHUB_TOKEN`을 통해 tag push할 때 본 rule 우회 가능. 일반 사용자가 `git push origin v1.2.3` 직접 시도하면 차단.

## 5. Repository Settings (권장)

### 5.1. Pull request

위치: Settings → General → Pull Requests

| 옵션 | 값 |
|------|-----|
| Allow merge commits | 비활성 |
| Allow squash merging | 활성 (default: PR title and description) |
| Allow rebase merging | 비활성 |
| Automatically delete head branches | 활성 |

squash merge 단독 활성 — PR title이 main commit message가 됨. release-please가 PR title 분석으로 semver bump 결정하므로 일관성.

### 5.2. Dependabot

위치: Settings → Code security → Dependabot

| 항목 | 값 |
|------|-----|
| Dependabot alerts | 활성 |
| Dependabot security updates | 활성 |
| Dependabot version updates | 비활성 |

본 repo 는 Dependabot version updates 를 비활성 — 의존성 PR 폭주 회피 + uv.lock 자동 갱신 미지원 한계 (PR 머지 시 lockfile drift 누적 → 다음 PR CI fail). 의존성 버전 bump 는 운영자 수동 (`uv lock --upgrade-package <name>` 또는 주기 검토). 보안 알림은 alerts + security updates 로 별도 신호 수신.

## 6. Secrets (현재 불필요)

본 repo는 `GITHUB_TOKEN` 외 추가 secret 사용 안 함. 외부 secret 추가 시점:
- PyPI publish 시 — `PYPI_API_TOKEN`
- 사내 Nexus·devpi mirror push 시 — `NEXUS_USER`·`NEXUS_PASSWORD`
- Codecov upload 시 — `CODECOV_TOKEN` (현재는 GitHub artifact만 사용)

## 7. 활성 체크리스트 (운영 시작 전)

순서대로 활성:

- [ ] Actions → General → Workflow permissions = Read and write + Allow GHA to create/approve PR
- [ ] Code security → CodeQL → Default setup → Enable
- [ ] Dependabot alerts + security updates → 활성 (version updates 는 비활성 — 운영자 수동 bump)
- [ ] Branches → main branch protection rule (위 3.1 표 적용)
- [ ] Branches → develop branch protection rule (위 3.2 적용)
- [ ] Tags → `v*` tag protection rule (위 4 적용)
- [ ] General → Pull Requests → squash merge 단독 활성 + Auto-delete head branches

본 체크리스트 모두 완료 = 본 repo CI·release 자동화 정합 활성.

## 8. 관련 문서

- CI workflow 카탈로그: README "CI 파이프라인" 절
- release artifact contract: `docs/operations/release.md`
- release-please 자동화 정책: `docs/adr/0013-release-please-automation.md`
- wheel + GitHub Release: `docs/adr/0012-wheel-ci-artifact.md`
