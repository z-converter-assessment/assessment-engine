# GitHub UI Setup

CI workflow·릴리즈·브랜치 정책을 작동시키려면 GitHub 측에서 한 번 활성해야 하는 설정 카탈로그. 저장소 코드 영역 밖이라 운영자가 Settings 에서 수동 적용한다. 배포는 GitHub 설정이 불요 — 배포 대상 VM 에서 `deploy.sh` 를 실행한다(`docs/guides/deploy.md`).

## 1. Actions 권한

위치: Settings -> Actions -> General -> Workflow permissions

| 항목 | 값 | 사유 |
|------|----|------|
| Read and write permissions | 필수 | 전역 설정이 각 워크플로 `permissions:` 블록의 상한이다. `release.yml` 이 릴리즈 tag 를 push 하려면 `contents: write` 가 필요하므로 전역 read-only 면 실패한다 |
| Allow GitHub Actions to create and approve pull requests | 불필요 | bot 의 PR 생성 없음 |

## 2. Code scanning

위치: Settings -> Code security -> Code scanning

Default setup 을 켜지 않는다. `.github/workflows/codeql.yml`(Advanced setup)이 이미 SAST 를 수행하며, GitHub 은 두 방식을 동시에 쓸 수 없어 Default setup 을 켜면 이 워크플로가 비활성화된다.

Advanced 를 유지하는 이유는 둘이다. 쿼리 범위를 `security-extended` 로 넓혀 두었고, 발화 시점을 main PR 로 좁혀 두었다. Default setup 은 둘 다 GitHub 이 정한다.

CodeQL 결과는 required status check 가 아니라 Security 탭 alert 으로 운영자가 인지한다.

## 3. Rulesets

위치: Settings -> Rules -> Rulesets -> New ruleset

Branch protection rules 가 아니라 ruleset 을 쓴다 — 여러 패턴을 한 규칙으로 묶고, bypass 대상을 지정할 수 있으며, 적용 전 평가 모드로 돌려볼 수 있다.

### 3.1. main

| 규칙 | 값 | 사유 |
|------|----|------|
| Target | `refs/heads/main` | |
| Restrict deletions | 활성 | |
| Block force pushes | 활성 | git 이력 보호 |
| Require linear history | 비활성 | `develop` -> `main` 승격을 merge commit 으로 해야 두 브랜치가 이력을 공유한다. linear 를 강제하면 squash·rebase 만 남아 승격할 때마다 이미 반영된 커밋이 다시 대상이 된다 |
| Require a pull request before merging | 활성 | |
| Required approvals | 1+ (선택) | 1인 운영이면 0 |
| Dismiss stale pull request approvals | 활성 | 새 커밋이 올라오면 승인 무효화 |
| Require conversation resolution | 활성 | |
| Allowed merge methods | Merge 만 | 승격 방법을 사람 선택에 맡기지 않는다 |
| Require status checks to pass | 활성 (아래 3.4 main 목록) | |
| Require branches to be up to date | 활성 | |
| Bypass list | 비움 | 관리자도 우회 불가 |

### 3.2. develop

| 규칙 | 값 | 사유 |
|------|----|------|
| Target | `refs/heads/develop` | |
| Restrict deletions · Block force pushes | 활성 | |
| Require linear history | 비활성 | `develop` 이 merge commit 을 받을 수 있어야 승격 정합이 맞는다 |
| Require a pull request before merging | 활성 | |
| Allowed merge methods | Squash 만 | feature 의 작업 커밋을 압축한다. PR title 이 그대로 커밋 메시지가 되므로 형식 검사가 의미를 갖는다 |
| Require status checks to pass | 활성 (아래 3.4 develop 목록) | |
| Bypass list | 비움 | |

### 3.3. release tags

| 규칙 | 값 | 사유 |
|------|----|------|
| Target | `refs/tags/v*` | |
| Restrict deletions | 활성 | 발행된 tag 불변 보존 |
| Block force pushes | 활성 | tag 재지정 차단 |
| Restrict creations | 활성 | 사람이 tag 를 붙이지 않는다 — `release.yml` 이 `pyproject.toml` 의 version 에서 파생 생성한다 |
| Bypass list | GitHub Actions | 워크플로가 tag 를 push 해야 한다 |

생성 제한은 bypass 에 Actions 가 등록돼야 성립한다. 등록 없이 켜면 릴리즈가 tag push 단계에서 실패하므로, 첫 릴리즈로 tag 가 실제로 남는지 확인한다.

### 3.4. Required status checks

발화 범위가 base 브랜치마다 다르다. 실행되지 않는 check 를 required 로 등록하면 영원히 대기 상태가 되어 머지가 막힌다.

| Check | 워크플로 | develop | main |
|-------|---------|---------|------|
| `pr title + metadata` | `pr-title-check.yml` | 의무 | 의무 |
| `ruff + hadolint` | `ci.yml` | 의무 | 의무 |
| `pytest (unit)` | `ci.yml` | 의무 | 의무 |
| `frontend typecheck` | `ci.yml` | 의무 | 의무 |
| `alembic-check` | `alembic-check.yml` | 의무 | 의무 |
| `wheel build` | `ci.yml` | 미발화 | 의무 |
| `pytest (integration)` | `ci.yml` | 미발화 | 의무 |

develop 5개, main 7개다. UI 검색 결과에 워크플로 이름이 접두로 붙어 보일 수 있으니(`ci / pytest (unit)`) 검색해서 나오는 항목을 그대로 고른다.

`pr title + metadata` 는 Conventional Commits 형식과 AI 메타데이터 부재를 함께 본다.

`wheel build` 와 `pytest (integration)` 은 job 의 `if` 조건으로 main PR 에서만 실행된다. paths 조건은 어느 워크플로에도 없다 — paths 로 skip 된 required check 가 N/A 로 남아 머지를 막는 함정을 피한다.

## 4. Repository Settings

### 4.1. Pull request

위치: Settings -> General -> Pull Requests

| 옵션 | 값 |
|------|----|
| Allow merge commits | 활성 |
| Allow squash merging | 활성 (default: PR title and description) |
| Allow rebase merging | 비활성 |
| Automatically delete head branches | 활성 |

머지 방법 강제는 ruleset 의 Allowed merge methods 가 한다. 여기서는 저장소 전체에서 허용할 방법만 켠다.

### 4.2. Dependabot

위치: Settings -> Code security -> Dependabot

| 항목 | 값 |
|------|----|
| Dependabot alerts | 활성 |
| Dependabot security updates | 활성 |
| Dependabot version updates | 비활성 |

version updates 를 끄는 이유는 uv.lock 자동 갱신을 지원하지 않기 때문이다 — PR 이 머지되면 lockfile drift 가 누적되어 다음 PR 의 CI 가 실패한다. 버전 bump 는 운영자가 `uv lock --upgrade-package <name>` 으로 처리한다.

## 5. Secrets

`GITHUB_TOKEN` 외 추가 secret 을 쓰지 않는다. 배포는 대상 VM 에서 실행되며 GitHub secret·runner·Environment 를 쓰지 않는다(public 이미지 pull + cosign 공개 검증).

추가가 필요해지는 시점은 PyPI publish(`PYPI_API_TOKEN`), 사내 mirror push(`NEXUS_USER`·`NEXUS_PASSWORD`), Codecov upload(`CODECOV_TOKEN`) 정도다.

## 6. 활성 체크리스트

- [ ] Actions -> Workflow permissions -> Read and write
- [ ] Code scanning -> Default setup 켜지 않음 (Advanced 유지)
- [ ] Dependabot alerts + security updates 활성 (version updates 비활성)
- [ ] Ruleset: main (3.1)
- [ ] Ruleset: develop (3.2)
- [ ] Ruleset: release tags (3.3) + 첫 릴리즈에서 tag 생성 확인
- [ ] Pull Requests: merge + squash 허용, rebase 비활성, head branch 자동 삭제

## 7. 관련 문서

- 워크플로 책임 카탈로그: 루트 `README.md` "CI 파이프라인" 절 (발화 조건·required check 는 본 문서 3.4 소유)
- 릴리즈 artifact·절차: `docs/guides/release.md`
- 배포(rollout): `docs/guides/deploy.md`
