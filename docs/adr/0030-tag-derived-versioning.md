# ADR 0030 — tag-derived 버전 (hatch-vcs, 버전을 repo 에 저장 안 함)

상태: Accepted — 정정 (2026-06-08): tag derive 경로를 single-source 로 수렴 + stable semver 가드 + 등가성 검증 (하단 "정정" 절). tag-derived 원칙 불변.

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

## 정정 (2026-06-08) — tag derive 경로 분산 가드 (single-source + 형식 가드 + 등가성 검증)

### 보강 배경

본 ADR 은 git tag(`v*`)를 버전 단일 진실로 세웠으나, 그 tag 를 semver 로 푸는 derive 로직이 4경로로 분산돼 있었다 — 입력은 하나지만 "정규화 규칙"이 도구마다 다르다.

| 경로 | 산출물 | 규칙 |
|------|--------|------|
| hatch-vcs (`.git` 직접) | wheel·sdist 버전 | PEP 440 |
| `--build-arg APP_VERSION` -> `SETUPTOOLS_SCM_PRETEND_VERSION` | 이미지 안 패키지 버전 | PEP 440 (주입값) |
| `docker/metadata-action {{version}}` | 이미지 태그명 (`:X.Y.Z`) | Docker SemVer |
| `${ref_name#v}` (sed) / `notify-infra ${GITHUB_REF_NAME#v}` | compose 핀·infra CD payload | 단순 문자열 strip |

문제는 경로가 물리적으로 2개(`.git` 있는 wheel 빌드 / `.git` 없는 이미지 빌드)라는 점이 아니다 — 이건 빌드 컨텍스트 차이라 구조적으로 강제되며 hatch-vcs 채택 이유상 합칠 수 없다. 실제 문제는 둘이다:

1. 정규화 규칙 차이 (잠재): stable `v0.3.1` 에선 4경로 모두 `0.3.1` 이라 무해. 그러나 prerelease 를 쓰는 순간 PEP 440(`1.0.0rc1`)과 SemVer(`1.0.0-rc.1`)가 갈려 wheel 버전 표기와 이미지 태그가 어긋난다.
2. 등가성 미검증 (실질): 4경로가 같은 값을 냈는지 빌드 타임에 아무도 확인하지 않는다. drift 가 나도 release 가 그대로 발행된다.

### 결정 (tag-derived 원칙 불변, derive 흐름에 가드 3종 추가)

A. 입력 공간 제약 (stable-only) — release 발사 tag 를 `^v[0-9]+\.[0-9]+\.[0-9]+$` 로 한정. `resolve-version` job 이 push tag·`workflow_dispatch inputs.tag` 양쪽에 형식 가드, 비정규·prerelease 태그는 즉시 fail. 정규화 규칙 차이의 발현 원천을 제거한다. prerelease 는 현재 계획 없음 — 도입하려면 PEP 440 / SemVer 중 한 규약으로 통일하고 양쪽 도구 설정을 맞추는 별도 ADR 의무.

B. 등가성 검증 (cross-check) — 두 release job 이 각자 산출물이 단일 진실과 같은지 assert. release-wheel 은 `uv build` 가 낸 wheel 파일명 버전 == single source, release-image 는 `metadata-action {{version}}` == single source. 불일치 시 release fail (partial 발행 방지). 일원화가 불가능한 경로를 cross-check 로 등가 보증한다.

C. single-source fan-out — 버전을 `resolve-version` job 이 hatch-vcs(`uvx --with hatch-vcs hatch version`) 실측 PEP 440 값으로 1회 산출해 job output(`version`)으로 노출. compose 핀 sed 와 image job `--build-arg APP_VERSION` 이 `${ref_name#v}` strip·`metadata-action` 독립 파싱 대신 그 output 을 참조. `docker/metadata-action` 은 `:X.Y` `:X` `:latest` alias 매핑 전용으로 축소(`{{version}}` 은 B 로 등가 검증). strip 경로 2곳이 소멸하고 정규화 규칙이 hatch-vcs PEP 440 단일 소스로 수렴. `workflow_dispatch` ref 보정(`ref: inputs.tag`)으로 dispatch 경로도 정확 버전 derive.

`notify-infra.yml` 은 별도 `release: published` 워크플로라 job output 직참조 불가 — A 제약(stable-only) 하에서 `${GITHUB_REF_NAME#v}` 가 PEP 440 산출값과 항상 동일하므로 현행 유지.

### Consequences

- prerelease 정규화 갈림이 입력 단계(A)에서 차단 + 잔여 drift 가 빌드 타임(B)에서 fail. derive 분산을 물리적으로 합치지 않고도 등가성이 보증된다.
- strip 경로 2곳 제거(C) -> 정규화 규칙 hatch-vcs PEP 440 단일 수렴. 버전 산출 권위 소스 = `resolve-version` job 1곳.
- 한계: prerelease/RC 릴리즈 불가(A). 필요해지면 규약 통일 별도 ADR 의무.

### 정정 동시 갱신 (F9)

| 위치 | 변경 |
|------|------|
| `.github/workflows/release.yml` | `resolve-version` job 신설 (A 형식 가드 + C hatch-vcs 단일 산출 output). 두 release job `needs: resolve-version` + ref 보정 + B assert step. compose sed·build-arg 가 job output 참조 |
| `.github/workflows/notify-infra.yml` | A 제약 명시 주석 (stable-only 전제로 `#v` strip 안전) — 코드 무변경 |
| `docs/operations/release.md` | 2절 흐름에 single-source·가드 반영 + 7절 한계 "stable semver only (prerelease 미지원)" 추가 |
| `docs/adr/README.md` | 0030 행 요약에 derive 가드 정정 한 줄 |

## 관련 문서·코드

- `pyproject.toml` `[tool.hatch.version]` — 버전 source = vcs(tag)
- `.github/workflows/release.yml` — tag push -> resolve-version(single source) -> wheel + image (fetch-depth 0 · APP_VERSION 주입 · 등가성 assert)
- `Dockerfile` — `SETUPTOOLS_SCM_PRETEND_VERSION` 주입
- `docs/operations/release.md` — release ceremony 단일 진실
- ADR 0028 — Commitizen (본 ADR 이 supersede)
- ADR 0012 — wheel + GitHub Release artifact contract
