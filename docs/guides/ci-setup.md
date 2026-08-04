# GitHub UI Setup

CI workflow·릴리즈·브랜치 정책을 작동시키려면 GitHub 측에서 한 번 활성해야 하는 설정 카탈로그. 저장소 코드 영역 밖이라 운영자가 Settings 에서 수동 적용한다. 배포는 GitHub 설정이 불요 — 배포 대상 VM 에서 `deploy.sh` 를 실행한다(`docs/guides/deploy.md`).

## 1. Actions 권한

위치: Settings -> Actions -> General -> Workflow permissions

| 항목 | 값 | 사유 |
|------|----|------|
| Read repository contents and packages permissions | 기본값 유지 | 워크플로가 `permissions:` 로 명시한 권한은 이 기본값과 무관하게 부여된다. `release.yml` 은 job 스코프로 `contents: write` 를 선언하므로 전역을 넓힐 필요가 없다 |
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
| Dismiss stale pull request approvals | 활성 | main 과 동일 |
| Allowed merge methods | Squash 만 | feature 의 작업 커밋을 압축한다. PR title 이 그대로 커밋 메시지가 되므로 형식 검사가 의미를 갖는다 |
| Require status checks to pass | 활성 (아래 3.4 develop 목록) | |
| Require branches to be up to date | 비활성 | develop 은 통합 지점이라 PR 마다 재실행을 강제하면 대기가 길어진다. main 승격은 활성 |
| Bypass list | 비움 | |

### 3.3. release tags

| 규칙 | 값 | 사유 |
|------|----|------|
| Target | `refs/tags/v*` | |
| Restrict deletions | 활성 | 발행된 tag 불변 보존 |
| Block force pushes | 활성 | tag 재지정 차단 |
| Restrict creations | 비활성 | 저장소 ruleset 은 GitHub Actions 를 bypass actor 로 받지 않는다 — 설치된 앱이 아니라 API 가 422 로 거부한다. 켜면 `release.yml` 의 tag push 가 막혀 릴리즈가 완료되지 못한다 |
| Bypass list | 비움 | |

tag 는 `release.yml` 이 `pyproject.toml` 의 version 에서 파생 생성하며, 사람이 붙이지 않는 것은 규약으로 지킨다 (`docs/guides/release.md` 2절).

`v*` tag 는 릴리즈 완료 마커다 — `resolve-version` job 이 tag 존재 여부로 릴리즈 여부를 판정한다. 손으로 만든 tag 는 이 마커를 위조해 해당 버전의 릴리즈를 건너뛰게 만든다. 그 경우 `workflow_dispatch` 로 재발행한다 (dispatch 는 tag 존재 판정을 건너뛰고, tag push 단계는 이미 있으면 그대로 종료한다).

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

`ruff + hadolint` 잡은 이름과 달리 pyright 도 함께 돌린다 — check 이름이 바뀌면 required 재등록이 필요하므로 잡을 나누지 않았다.

develop 5개, main 7개다. UI 검색 결과에 워크플로 이름이 접두로 붙어 보일 수 있으니(`ci / pytest (unit)`) 검색해서 나오는 항목을 그대로 고른다.

등록된 값은 API 로 대조한다 — 표와 어긋나면 머지가 막히거나 그물이 비는데, UI 로는 두 ruleset 을 번갈아 열어야 한다.

```bash
gh api repos/<owner>/<repo>/rulesets --jq '.[] | select(.target=="branch") | .id'
gh api repos/<owner>/<repo>/rulesets/<id> \
  --jq '.name, (.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context)'
```

`pr title + metadata` 는 Conventional Commits 형식과 AI 메타데이터 부재를 함께 본다.

`wheel build` 와 `pytest (integration)` 은 job 의 `if` 조건으로 main PR 에서만 실행된다. paths 조건은 어느 워크플로에도 없다 — paths 로 skip 된 required check 가 N/A 로 남아 머지를 막는 함정을 피한다.

### 3.5. tag ruleset 에 `Restrict creations` 를 켜지 않는 이유

`release.yml` 이 릴리즈 성공 후 `v<version>` tag 를 push 하므로, creation 을 막으려면 그 push 주체가 bypass 되어야 한다. 주체는 `GITHUB_TOKEN` 이 대변하는 `github-actions[bot]` 이고 ruleset 상 actor type 은 `Integration` 이다.

저장소 레벨 ruleset 은 그 actor 를 받지 않는다 — 등록을 시도하면 `Actor GitHub Actions integration must be part of the ruleset source or owner organization` 으로 거부된다. `RepositoryRole`(admin 등)은 등록되지만 사람 역할이라 봇에 적용되지 않는다.

따라서 creation 은 켜지 않는다. 켜면 릴리즈가 tag push 에서 멈춘다. 조직 레벨 ruleset 으로 옮기면 Actions 를 bypass 로 넣을 수 있으나, 그 경우 조직의 다른 저장소도 함께 규율 대상이 된다.

현재 tag ruleset 은 `deletion` 과 `non_fast_forward` 만 건다. tag 가 다른 커밋으로 옮겨가거나 지워지는 것을 막으면 `deploy.sh` 가 태그 ref 에서 받는 compose 의 무결성 전제가 성립하므로, creation 없이도 목적은 달성된다.

설정 조회·변경은 UI 없이 API 로 가능하다.

```bash
gh api repos/<owner>/<repo>/rulesets --jq '.[] | "\(.id) \(.name) \(.target)"'
gh api repos/<owner>/<repo>/rulesets/<id> --jq '[.rules[].type]'
```

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

Dependabot 은 워크플로가 아니라 플랫폼 기능이다. 러너에서 돌지 않고 GitHub 이 저장소의 의존성 선언과 lockfile 을 자기 인프라에서 스캔한다. 분류로는 SCA — 우리가 가져다 쓰는 패키지의 알려진 취약점을 본다. 우리 코드 자체를 보는 CodeQL(SAST, `codeql.yml` 워크플로)과 다른 도구이고 결과만 같은 Security 탭에 모인다.

| 항목 | 값 | 동작 |
|------|----|------|
| Dependabot alerts | 활성 | 취약점 발견 시 Security 탭에 경고 |
| Dependabot security updates | 비활성 | 켜면 취약점 건에 자동 수정 PR |
| Dependabot version updates | 비활성 | 켜면 취약점과 무관한 정기 버전 올림 PR |

자동 PR 을 여는 두 항목을 끈다. 둘 다 lockfile 을 갱신해 PR 을 여는 동작이라 같은 제약을 받는다 — `uv.lock` 이 자동 갱신 대상이 아니라서 PR 이 머지되면 drift 가 누적되고 다음 PR 의 CI 가 실패한다.

따라서 취약점 대응은 alert 를 받아 사람이 수행한다. 대상 패키지를 `uv lock --upgrade-package <name>` 으로 올리고, 하한을 고정해야 하면 `pyproject.toml` 에 직접 선언해 그 줄에 근거를 남긴다 — 전이 의존이라도 직접 선언하면 핀을 걸 자리가 생긴다.

상태 조회는 API 로 한다.

```bash
gh api -i repos/<owner>/<repo>/vulnerability-alerts   # 204 = alerts 활성, 404 = 비활성
gh api repos/<owner>/<repo>/automated-security-fixes  # enabled = security updates
```

## 5. Secrets

`GITHUB_TOKEN` 외 추가 secret 을 쓰지 않는다. 배포는 대상 VM 에서 실행되며 GitHub secret·runner·Environment 를 쓰지 않는다(public 이미지 pull + cosign 공개 검증).

추가가 필요해지는 시점은 PyPI publish(`PYPI_API_TOKEN`), 사내 mirror push(`NEXUS_USER`·`NEXUS_PASSWORD`), Codecov upload(`CODECOV_TOKEN`) 정도다.

## 6. 활성 체크리스트

- [ ] Actions -> Workflow permissions -> Read repository contents and packages (기본값)
- [ ] Code scanning -> Default setup 켜지 않음 (Advanced 유지)
- [ ] Dependabot alerts 활성 (security updates·version updates 비활성)
- [ ] Ruleset: main (3.1)
- [ ] Ruleset: develop (3.2)
- [ ] Ruleset: release tags (3.3) + 첫 릴리즈에서 tag 생성 확인
- [ ] Pull Requests: merge + squash 허용, rebase 비활성, head branch 자동 삭제

## 7. 관련 문서

- 워크플로 책임 카탈로그: 루트 `README.md` "워크플로" 절 (발화 조건·required check 는 본 문서 3.4 소유)
- 릴리즈 artifact·절차: `docs/guides/release.md`
- 배포(rollout): `docs/guides/deploy.md`
