# ADR 0028 — Commitizen 전환 (release-please 폐기, git-flow 정합)

상태: Superseded by ADR 0030

> Commitizen 은 `cz bump` 이 pyproject 버전 + CHANGELOG 를 repo 에 commit 하는 모델인데, 이 bump 커밋이 보호된 develop·main(PR 필수, bypass 불가)을 직접 push 못 하고, `cz` 기본 `bump:` 커밋 메시지가 commit-msg hook 의 type set 에 없어 거부된다. 즉 본 repo ruleset + hook 과 구조 충돌. ADR 0030 이 tag-derived 버전(hatch-vcs — 버전을 repo 에 저장 안 함)으로 대체해 bump 커밋 자체를 없앤다. 아래 본문은 history 보존용.

## Context

ADR 0013 이 release-please 를 release 자동화로 채택했다. 그러나 본 repo 는 다음을 동시에 유지한다:

- 장수 브랜치 2개 (`develop` 통합 + `main` 배포) — git-flow
- ruleset: `main`·`develop` 둘 다 PR 필수 + `required_linear_history` + squash 단독 머지
- release-please 가 `main` 에만 릴리즈 커밋(version bump·CHANGELOG·tag) 발행

이 셋의 조합이 구조적으로 충돌한다:

- squash + `required_linear_history` = 모든 PR 이 target 에 "새 단일 커밋 1개"로 떨어진다. `develop` → `main` 승격이 squash 면 `main` 은 `develop` 의 실제 커밋을 조상으로 갖지 못하고 새 커밋만 받는다.
- release-please 는 `main` 에만 릴리즈 커밋을 추가해 `develop` 이 영원히 뒤처진다.
- 결과: 릴리즈마다 `main` 과 `develop` 이 divergence — cross-PR 이 매번 대규모 충돌 (실측 develop<->main 머지 시 충돌 다수).

git-flow 의 정석 해법인 back-merge(`main` → `develop` 을 merge commit 으로 되돌리기)는 `required_linear_history` 가 금지한다. 즉 release-please(트렁크 기반 전용 도구) + 장수 develop + squash/linear ruleset 은 양립 불가다.

선택지는 둘 중 하나였다:
- (A) 트렁크 기반 전환 — `develop` 폐기, `main` 단독. release-please 유지.
- (B) git-flow 유지 — release-please 폐기 + merge commit 허용 + git-flow 친화 릴리즈 도구.

운영 정책상 `develop` 통합 버퍼를 유지하기로 결정 → (B).

## Decision

release-please 를 폐기하고 Commitizen(`cz`)으로 전환한다. ruleset 을 git-flow 에 맞게 완화한다.

릴리즈 도구 = Commitizen:
- Python 생태계 native (본 repo 는 Python + uv). pyproject `[tool.commitizen]` 단일 설정.
- 특정 브랜치 모델에 묶이지 않음 — `develop` 에서 `cz bump` 실행 가능 (release-please 의 "main 감시" 제약 없음).
- `version_provider = "pep621"` → `[project].version` 단일 진실 read/write. `tag_format = "v$version"` → `release.yml` 의 `push: tags: v*` trigger 와 정합.
- `update_changelog_on_bump = true` + `changelog_incremental = true` → CHANGELOG.md 누적 보존.
- `major_version_zero = true` → 0.x 동안 BREAKING 도 MINOR (release-please `bump-minor-pre-major` 정합).

릴리즈 흐름 (git-flow):
1. feature·fix → PR(squash) → `develop` (PR title = Conventional Commits, `pr-title-check.yml` 강제)
2. 릴리즈 시점에 운영자가 최신 `develop` 에서:
   ```
   uv run cz bump --yes
   git push origin develop --follow-tags
   ```
   `cz bump` 이 마지막 tag 이후 Conventional Commits 분석 → version bump + CHANGELOG + `vX.Y.Z` tag (모두 로컬 생성)
3. `develop` → `main` PR(merge commit) 승격 → `main` 이 `develop` 이력 공유 (divergence 0)
4. tag push → `release.yml` 발사 (wheel + sdist + SBOM + Sigstore + GHCR image)

ruleset 완화 (`main`·`develop`):
- `required_linear_history` 제거 — `develop` → `main` merge commit 허용.
- allowed merge methods 에 `merge` 추가 (squash 와 병행). repo 설정 `allow_merge_commit = true`.
- 유지: PR 필수, `non_fast_forward`(force-push 차단), `deletion`(삭제 차단).
- tag ruleset(`refs/tags/v*`)은 `deletion`·`non_fast_forward` 만 — creation 제한 없음. `cz bump` 의 개발자 자격증명 tag push 가 정상 release 경로.

`cz bump` 이 만드는 tag 는 로컬 개발자 자격증명으로 push 되므로 `release.yml` 이 정상 발사된다 — release-please bot 의 GITHUB_TOKEN tag push 가 다른 워크플로를 트리거 못 하던 제약(App token 우회 필요)이 제거된다.

## Options Considered

1. Commitizen (Python) — 채택
   - 장점: Python native. 브랜치 모델 무관 (develop 에서 실행). pyproject 단일 설정. CHANGELOG 누적.
   - 단점: `cz bump` 이 로컬/CI 수동 실행 (release-please 의 자동 Release PR 단계 없음 — 단 git-flow 에선 develop→main PR 자체가 review 단계).

2. release-please 유지 + 트렁크 전환 (develop 폐기)
   - 장점: 도구 변경 0. divergence 원천 소멸.
   - 단점: develop 통합 버퍼 포기 (운영 정책상 유지 결정).

3. python-semantic-release
   - 장점: Python. 자동화 강함.
   - 단점: main push 즉시 release 경향 (trunk 지향) — git-flow 와 동일 마찰.

4. git-cliff (CHANGELOG) + 수동 version bump
   - 장점: 빠름. 브랜치 무관.
   - 단점: version bump 수동 — Commitizen 이 bump+CHANGELOG+tag 통합이라 우위.

옵션 1 채택 — git-flow 유지 + Python native + bump/CHANGELOG/tag 통합.

## Consequences

장점
- develop git-flow 유지하면서 divergence 구조적 소멸 (develop→main merge 승격으로 이력 공유).
- release 도구가 Python 생태계 정합 (uv dev 의존성 1개).
- release-please App token / bot 권한 / GITHUB_TOKEN trigger 제약 전부 제거.
- CHANGELOG.md 단일 진실 누적 유지 (release-please 와 동일 가치).

단점·한계
- `cz bump` 이 자동 발화가 아니라 운영자 수동 실행 — 릴리즈 시점 사람이 1회 명령.
- release-please 의 Release PR 자동 review 단계 부재 — develop→main PR 이 그 review 경계를 대신.
- 0.x → 1.0.0 전환 시 `major_version_zero` 제거 수동 (release-please 의 manifest 수동 변경과 동일 부담).
- `RELEASE_PLEASE_APP_ID`·`RELEASE_PLEASE_APP_PRIVATE_KEY` secret 은 미사용 — 운영자가 GitHub Settings 에서 정리 가능 (불필수).

## Migration (본 ADR 채택 시점)

| 작업 | 위치 | 변경 |
|------|------|------|
| `.github/workflows/release-please.yml` | 삭제 | release-please runner 제거 |
| `release-please-config.json` | 삭제 | |
| `.release-please-manifest.json` | 삭제 | |
| `pyproject.toml` | `[tool.commitizen]` 추가 + dev 의존 `commitizen` + version marker 주석 교체 | `cz bump` 설정 단일 진실 |
| `.github/workflows/release.yml` | 주석 정정 | App token 우회 설명 제거, cz tag push 명시 (trigger 동일 `push: tags: v*`) |
| `.github/workflows/pr-title-check.yml`·`ci.yml` | 주석 정정 | release-please → cz bump |
| ruleset `protect` | `required_linear_history` 제거 + merge method 추가 | git-flow merge 승격 허용 |
| repo 설정 | `allow_merge_commit = true` | |
| `docs/guides/release.md`·`docs/guides/ci-setup.md`·`docs/guides/dependencies.md`·`docs/README.md`·README | 갱신 | release ceremony·UI setup·version marker |
| `docs/decisions/adr/0013-*.md` | Superseded by 0028 | |

## 관련 문서·코드

- `pyproject.toml` `[tool.commitizen]` — cz bump 설정 단일 진실
- `.github/workflows/release.yml` — tag `v*` push → wheel + sdist + SBOM + GHCR image
- `.github/workflows/pr-title-check.yml` — PR title Conventional Commits 강제 (cz bump 입력)
- `CHANGELOG.md` — cz bump 자동 갱신 단일 진실
- `docs/guides/release.md` — release artifact + ceremony 단일 진실
- `docs/guides/ci-setup.md` — GitHub UI/ruleset 설정 카탈로그
- ADR 0012 — wheel + GitHub Release artifact contract (본 ADR 이 그 발사 ceremony 도구 교체)
- ADR 0013 — release-please 자동화 (본 ADR 이 supersede)
