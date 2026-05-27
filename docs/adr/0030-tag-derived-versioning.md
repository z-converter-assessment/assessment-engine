# ADR 0030 — tag-derived 버전 (hatch-vcs, 버전을 repo 에 저장 안 함)

상태: Accepted

## Context

ADR 0028 이 Commitizen(`cz bump`)을 release 도구로 채택했다 — `cz bump` 이 `pyproject.toml` `[project].version` + `CHANGELOG.md` 를 갱신하는 "버전을 repo 에 commit" 모델. 그러나 본 repo 의 ruleset + commit-msg hook 과 정면 충돌한다:

1. develop·main 둘 다 `pull_request` 규칙 + `can_bypass: never` — 직접 push 차단. `cz bump` 이 만든 버전 bump 커밋을 `git push origin develop` 하면 거부된다 (ADR 0028 release.md 절차가 동작 안 함).
2. main 의 PR source 는 develop 만이어야 한다 (git-flow). bump 커밋을 release/* 브랜치에서 main 으로 직접 PR 하면 이 원칙 위반. develop 에 먼저 넣으려면 또 별도 PR 필요 — 절차 비대.
3. `cz bump` 의 기본 커밋 메시지 `bump: ...` 는 commit-msg hook 의 허용 type set (feat/fix/docs/chore/refactor/perf/test/build/ci/style/revert) 에 `bump` 이 없어 거부된다.

근본 원인은 하나 — "버전 숫자를 repo(pyproject) 에 저장"하니 bump 커밋이 생기고, 그게 보호된 develop·main 을 PR 로 통과해야 해서 전부 꼬인다. release-please(0013)·Commitizen(0028) 모두 "repo 에 버전 commit" 모델이라 같은 마찰을 가진다.

## Decision

버전을 repo 에 저장하지 않는다. git tag(`v*`) 가 버전의 단일 진실이고, 빌드 시점에 tag 에서 derive 한다 (`hatch-vcs`).

- `pyproject.toml`: `[project]` 의 static `version` 제거 -> `dynamic = ["version"]`. `[tool.hatch.version] source = "vcs"`. build-system requires 에 `hatch-vcs` 추가.
- 버전 bump 커밋이 사라진다 -> "보호 브랜치에 bump push" 문제·commit-msg hook `bump:` 문제·release/* vs develo p→main 긴장 전부 소멸.
- release notes 는 GitHub Releases 자동 생성 (`release.yml` `softprops/action-gh-release` `generate_release_notes: true`). `CHANGELOG.md` 자동 갱신 도구 폐기 — 기존 파일은 history 로 동결, 신규 노트는 GitHub Release.
- Commitizen 제거 (dev 의존성 + `[tool.commitizen]`). semver 결정("다음 버전 뭐로?")은 tag 칠 때 사람이 — 필요 시 `cz`/git log 로 제안만 받되 아무것도 commit 하지 않는다.

릴리즈 흐름 (only develop→main 유지):
1. `feature/*`·`fix/*` -> PR(squash) -> `develop`
2. `develop` -> `main` PR(merge) — 코드 승격 (source develop 만, 버전 변경 없음)
3. `main` 에 tag push:
   ```
   git checkout main && git pull
   git tag v0.2.0 && git push origin v0.2.0
   ```
   tag 생성은 tag ruleset 이 허용 (deletion·non_fast_forward 만 차단). tag push 가 `release.yml` 발화 -> hatch-vcs 가 tag 에서 버전 읽어 wheel + GHCR image 빌드.

CI 정합 (hatch-vcs 가 빌드 시 git tag 필요):
- `release.yml` wheel job checkout `fetch-depth: 0` (full history + tags 없으면 derive 불가).
- Docker 빌드 컨텍스트엔 `.git` 미포함 -> `release.yml` image job 이 tag semver 를 `--build-arg APP_VERSION` 으로 전달, `Dockerfile` 이 `SETUPTOOLS_SCM_PRETEND_VERSION` 으로 hatch-vcs 에 주입 (hatch-vcs = setuptools_scm 기반, 본 env 존중).

## Options Considered

1. tag-derived (hatch-vcs) — 채택
   - 장점: bump 커밋 0 -> 보호 브랜치 push 문제·hook 충돌 전부 소멸. release = main tag 하나. 버전 단일 진실 = tag. "only develop→main" 자연 유지.
   - 단점: pyproject 정적 버전 없음 (dynamic). dev 빌드 버전이 `0.1.3.devN+g<sha>` 형태. Docker 빌드에 버전 주입 1단계 필요.

2. Commitizen / release-please (repo 에 버전 commit) — 폐기 (0028·0013)
   - 단점: bump 커밋이 보호된 develop·main 을 통과 못 함. 본 ruleset 과 구조 충돌.

3. 정적 버전 + 사람 수동 bump (도구 0)
   - 장점: 도구·CI·Docker 변경 0. bump 은 일반 PR 의 한 줄 수정.
   - 단점: pyproject 버전과 git tag 를 사람이 매번 수동 일치시켜야 함 (drift 위험). tag 단일 진실 아님.

옵션 1 채택 — release = tag 하나, 버전 단일 진실 = tag, 보호 브랜치 마찰 0.

## Consequences

장점
- 릴리즈가 "main 에 tag push" 하나로 끝. bump 커밋·CHANGELOG 커밋·cz 도구 전부 불필요.
- 버전 단일 진실 = git tag. pyproject·tag drift 불가능 (pyproject 에 버전이 없으니).
- ruleset(보호 브랜치)·commit-msg hook 과 마찰 0. only develop→main 유지.

단점·한계
- dev/CI 비-tag 빌드 버전이 `0.1.3.devN+g<sha>` 형태 (PEP 440 dev 버전) — 운영 release 빌드(tag 위)는 정확 `X.Y.Z`.
- Docker 이미지 빌드에 버전 주입 1단계 (`APP_VERSION` build-arg) 필요 — `.git` 을 빌드 컨텍스트에 안 넣기 위한 trade. `release.yml` 이 자동 처리.
- `CHANGELOG.md` 자동 누적 중단 — 신규 release 노트는 GitHub Releases (auto-generated). 누적 changelog 파일이 꼭 필요해지면 별도 결정 (git-cliff 등, 단 commit 은 안 함).

## Migration (본 ADR 채택 시점)

| 작업 | 위치 | 변경 |
|------|------|------|
| `pyproject.toml` | `[project].version` 제거 -> `dynamic = ["version"]` + `[tool.hatch.version] source="vcs"` + build requires `hatch-vcs` | 버전 source = tag |
| `pyproject.toml` | `[tool.commitizen]` 제거 + dev 의존 `commitizen` 제거 | cz 폐기 |
| `.github/workflows/release.yml` | wheel job `fetch-depth: 0` + image job `build-args: APP_VERSION` | hatch-vcs CI 정합 |
| `Dockerfile` | builder `ARG APP_VERSION` + `ENV SETUPTOOLS_SCM_PRETEND_VERSION` | .git 없는 빌드에 버전 주입 |
| `CHANGELOG.md` | header 동결 — 신규 노트는 GitHub Releases | 자동 갱신 중단 |
| `docs/operations/release.md`·README·github-setup.md·dependencies.md | 갱신 | tag-derived 흐름 |
| `docs/adr/0028-*.md` | Superseded by 0030 | |

## 관련 문서·코드

- `pyproject.toml` `[tool.hatch.version]` — 버전 source = vcs(tag)
- `.github/workflows/release.yml` — tag push -> wheel + image (fetch-depth 0 · APP_VERSION 주입)
- `Dockerfile` — `SETUPTOOLS_SCM_PRETEND_VERSION` 주입
- `docs/operations/release.md` — release ceremony 단일 진실
- ADR 0028 — Commitizen (본 ADR 이 supersede)
- ADR 0012 — wheel + GitHub Release artifact contract
