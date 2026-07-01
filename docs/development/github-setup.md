# GitHub UI Setup

본 repo CI workflow·release(tag push) ceremony·branch policy를 정상 작동시키기 위해 GitHub 측에서 한 번만 활성해야 하는 설정 카탈로그. 본 repo 코드 영역 밖이라 운영자가 GitHub Settings(또는 ruleset)에서 수동 활성. (배포는 GitHub 설정이 불요 — 배포 대상 VM 에서 `deploy.sh` 를 실행한다. `docs/operations/deployment.md`.)

## 1. Actions 권한

위치: Settings → Actions → General → Workflow permissions

| 항목 | 값 | 사유 |
|------|----------|------|
| Read and write permissions | 권장(필수 아님) | 각 워크플로가 `permissions:` 블록으로 최소권한 자체 선언 (release.yml `packages: write`·`id-token: write`). 전역 read-only 여도 동작 |
| Allow GitHub Actions to create and approve pull requests | 불필요 | bot 의 PR 생성 없음 (ADR 0030 — 버전은 tag 단일 진실, bump 커밋 없음). 릴리즈 = 운영자가 main 에 tag push |

릴리즈는 GitHub Actions bot 이 아니라 운영자가 `main` 에 `v*` tag 를 push -> `release.yml` 발사 (ADR 0030). bot PR 생성 권한 불필요.

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
| Required status checks | 아래 6 항목 모두 의무 등록 (체크) — 모든 PR 이 본 check 통과해야 merge 가능 |
| Require branches to be up to date before merging | 활성 | merge 직전 main rebase 강제 |
| Require conversation resolution before merging | 활성 | PR comment 미해결 차단 |
| Require linear history | 비활성 | `develop` → `main` 승격을 merge commit 으로 해 이력 공유 (divergence 0, ADR 0030). linear 강제 시 squash 만 되어 main/develop 영구 분기 |
| Do not allow bypassing the above settings | 활성 | admin도 우회 불가 — 본 옵션이 정책의 마지막 안전망 |
| Allow force pushes | 비활성 | git history 보호 |
| Allow deletions | 비활성 | branch 삭제 차단 |

#### Required status checks 카탈로그 (6 항목 — main 머지 의무)

| Check 이름 (GitHub UI 표시) | 발화 workflow | 검증 | 비고 |
|------------------------------|-----------------|------|------|
| `PR title (conventional commits)` | `pr-title-check.yml` | PR title 형식 (`feat:`·`fix:`·`feat!:` 등) | git history 일관 (ADR 0030) |
| `ci / ruff + hadolint` | `ci.yml` | python lint + Dockerfile lint | |
| `ci / pytest (unit)` | `ci.yml` | 단위 테스트 + coverage | |
| `ci / wheel build` | `ci.yml` | `uv build` 성공 | 패키징 빌드 검증 (PEP 517 — 이미지 빌드가 소비하는 패키지 정합) |
| `ci / pytest (integration)` | `ci.yml` | testcontainers postgres/redis + 통합 | wheel build 의존 |
| `alembic-check` | `alembic-check.yml` | ORM·migrations 라운드트립 정합 | paths 무관 매 PR 발화 (paths 조건 제거 — branch protection skip 함정 회피) |

본 6 check 모두 통과 의무 — paths 조건 없는 워크플로라 main PR 매번 발화 (`alembic-check` ~10s). CodeQL SAST 는 `codeql.yml` 이 별도 SARIF 업로드라 본 required 목록 외 — Security 탭 alert 으로 운영자 인지. 의존성 CVE 는 GitHub Dependabot alerts(아래 5.2)로 수신 — CI gate 아님.

main PR 분기 강화는 `.claude/skills/pr-create/SKILL.md` "main PR 추가 강화" 절 — `pytest tests/integration` 의무·BREAKING change 시 ADR 신설 의무·`hotfix/*` branch naming 권장.

### 3.2. `develop` branch

`main`과 동일 패턴 — `develop`도 PR 강제. 단:
- "Required status checks" — 위 6 항목 동일 적용 (`alembic-check` 도 paths 무관 매 PR 발화)
- "Require linear history" 비활성 — feature 는 squash 로 들어오지만 `develop` 자체가 merge commit 을 받을 수 있어야 화해·승격 정합 (ADR 0030)
- "Do not allow bypassing" 활성

## 4. Tag Protection Rule (정석)

위치: Settings → Tags → New tag protection rule

| 옵션 | 값 | 사유 |
|------|-----|------|
| Pattern | `refs/tags/v*` | semver release tag만 적용 |
| Restrict deletions | 활성 | 발행된 release tag 삭제 차단 (불변 보존) |
| Block force pushes (non-fast-forward) | 활성 | tag 재지정 차단 |

tag 생성(creation)은 제한 안 함 — 운영자가 `main` 머지 후 `git tag v1.2.3 && git push origin v1.2.3` 으로 새 `v*` tag 를 push 하는 게 정상 릴리즈 경로 (ADR 0030). 이미 발행된 tag 의 삭제·재지정만 차단.

## 5. Repository Settings (권장)

### 5.1. Pull request

위치: Settings → General → Pull Requests

| 옵션 | 값 |
|------|-----|
| Allow merge commits | 활성 | `develop` → `main` 승격용 — main 이 develop 이력 공유 (ADR 0030) |
| Allow squash merging | 활성 (default: PR title and description) | feature·fix → develop 통합용 |
| Allow rebase merging | 비활성 |
| Automatically delete head branches | 활성 |

merge + squash 병행 — feature·fix 는 squash 로 develop 에 들어가고(PR title이 commit message), `develop` → `main` 은 merge commit 으로 승격해 두 장수 브랜치가 이력을 공유. 버전은 repo 에 없고 `main` 에 push 하는 `v*` tag 가 단일 진실 (hatch-vcs, ADR 0030).

### 5.2. Dependabot

위치: Settings → Code security → Dependabot

| 항목 | 값 |
|------|-----|
| Dependabot alerts | 활성 |
| Dependabot security updates | 활성 |
| Dependabot version updates | 비활성 |

본 repo 는 Dependabot version updates 를 비활성 — 의존성 PR 폭주 회피 + uv.lock 자동 갱신 미지원 한계 (PR 머지 시 lockfile drift 누적 → 다음 PR CI fail). 의존성 버전 bump 는 운영자 수동 (`uv lock --upgrade-package <name>` 또는 주기 검토). 보안 알림은 alerts + security updates 로 별도 신호 수신.

## 6. Secrets (추가 없음)

본 repo는 `GITHUB_TOKEN` 외 추가 secret 사용 안 함. 배포(`deploy.sh`)는 배포 대상 VM 에서 실행되고 GitHub secret·runner·Environment 를 쓰지 않는다 (public 이미지 pull·cosign 공개 검증, ADR 0048). 외부 secret 추가 시점:
- PyPI publish 시 — `PYPI_API_TOKEN`
- 사내 Nexus·devpi mirror push 시 — `NEXUS_USER`·`NEXUS_PASSWORD`
- Codecov upload 시 — `CODECOV_TOKEN` (현재 coverage 는 CI 콘솔 표시만, artifact 미업로드)

## 7. 활성 체크리스트 (운영 시작 전)

순서대로 활성:

- [ ] Code security → CodeQL → Default setup → Enable
- [ ] Dependabot alerts + security updates → 활성 (version updates 는 비활성 — 운영자 수동 bump)
- [ ] Branches → main branch protection rule (위 3.1 표 적용 — linear history 비활성)
- [ ] Branches → develop branch protection rule (위 3.2 적용)
- [ ] Tags → `v*` tag protection rule (위 4 — deletion·force-push 차단, creation 허용)
- [ ] General → Pull Requests → merge + squash 병행 활성 + Auto-delete head branches

본 체크리스트 모두 완료 = 본 repo CI·release 정합 활성. (배포는 GitHub 설정 불요 — VM 에서 `deploy.sh`.)

## 8. 관련 문서

- CI workflow 카탈로그: README "CI 파이프라인" 절
- release artifact contract: `docs/operations/release.md`
- release(tag-derived) ceremony 정책: `docs/adr/0030-tag-derived-versioning.md` (cz/commitizen supersede)
- 배포(rollout·compose 매체): `docs/adr/0048-engine-rollout-in-repo.md` · `docs/operations/deployment.md`
