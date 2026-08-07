# Release Artifact

릴리즈 워크플로(`release.yml`)가 발행하는 release artifact 의 단일 진실이다. 배포(rollout) 절차는 `docs/guides/deploy.md` 가 갖는다.

## 1. artifact 카탈로그

릴리즈는 서명·attestation 된 멀티아치 엔진 이미지 하나를 GHCR 로 발행한다. 이미지가 유일한 배포 산출물이고, compose 파일은 `deploy.sh` 가 배포 대상 버전 태그의 raw 에서 내려받는다.

### 1.1. Docker image → GHCR

| 태그 | 의미 | 용도 |
|------|------|------|
| `ghcr.io/z-converter-assessment/assessment-engine:X.Y.Z` | immutable 정확 버전 (`pyproject.toml` 의 version) | prod pin (배포 기본) |
| `:X.Y` | minor 최신 | minor patch auto-track |
| `:X` | major 최신 | major lock |
| `:latest` | stable release 최신 | 모니터링 — prod 비추천 (변경 무경고) |

이미지 attestation 은 별도 파일이 아니라 이미지에 귀속된다.

- Cosign keyless signature — 검증 명령은 3절
- BuildKit SBOM (SPDX) — `docker buildx imagetools inspect --format '{{ json .SBOM.SPDX }}'`
- SLSA provenance — `docker buildx imagetools inspect --format '{{ json .Provenance }}'`

multi-arch 는 `linux/amd64` + `linux/arm64` 다 (운영자 ARM 서버 직접 호환).

이미지는 실행할 컴포넌트를 정하지 않는다 — 호출자가 완결된 명령을 넘긴다. 실행 컴포넌트 분기는 `docs/reference/docker.md`, 이미지 안 Alembic 자원은 `docs/guides/migrate.md`.

## 2. 생성 trigger (파일 버전, git-flow)

버전 입력 지점은 `pyproject.toml` 의 `version` 하나다. 릴리즈는 그 값을 올린 커밋이 `main` 에 들어오는 것이며, git tag 는 릴리즈가 성공한 뒤 `release.yml` 이 그 값에서 파생 생성한다. 사람이 tag 를 붙이지 않으므로 파일과 tag 가 어긋날 수 없다.

흐름:

1. `feature/*`·`fix/*` -> PR(squash) -> `develop`. PR title 은 Conventional Commits 를 따르고 `pr-title-check.yml` 이 강제한다.
2. 릴리즈 준비 — 브랜치에서 버전을 올려 `develop` 으로 PR:
   ```bash
   git checkout -b chore/release-X.Y.Z
   uv version --bump minor      # pyproject.toml 의 version 수정
   git commit -am "chore: release X.Y.Z"
   ```
   develop·main 은 직접 push 가 차단되므로 bump 커밋도 PR 을 거친다. 다음 버전 semver 는 사람이 결정한다 — 직전 릴리즈 이후 `feat`/`fix`/`BREAKING` 비율을 보고 판단하며, 필요하면 `git log <last-tag>..develop` 으로 확인한다.
3. `develop` -> `main` PR 을 merge method 로 승격·머지한다. main 이 develop 이력을 공유해 divergence 가 없고, source 는 develop 뿐이다.
4. `main` push 가 `release.yml` 을 발사한다. 워크플로는 `uv version --short` 로 버전을 산출해 stable semver 형식을 가드하고, 해당 tag 가 이미 있으면 버전을 안 올린 일반 머지이므로 릴리즈를 건너뛴다. 릴리즈로 판정되면 이미지 빌드·GHCR push·서명·attestation 을 마친 뒤 마지막에 `v<version>` tag 를 남긴다 — tag 가 끝이라 앞 단계가 실패하면 그대로 재시도할 수 있다.

`workflow_dispatch` 는 이미 릴리즈된 버전의 재발행(이미지 유실·재서명)에 쓴다. tag 중복 판정을 건너뛰고 진행하며, 이 경로에서는 `:latest` alias 를 붙이지 않는다.

semver 규칙 (`uv version --bump` 대상 결정 가이드):

| 변경 성격 | bump | 예 |
|-----------|------|-----|
| 새 기능 (`feat`) | MINOR | 1.2.1 -> 1.3.0 |
| 버그 수정 (`fix`/`perf`) | PATCH | 1.2.0 -> 1.2.1 |
| 호환성 깨짐 (`feat!`/`BREAKING`) | MAJOR | 1.2.1 -> 2.0.0 |
| 문서·잡무만 (`docs`/`chore`/`ci` 등) | 없음 | 릴리즈 안 함 |

## 3. 무결성 검증 (배포 게이트)

`deploy.sh` 가 pull 전에 cosign 서명을 검증하고, 미통과면 배포를 중단한다. 수동 검증:

```bash
cosign verify ghcr.io/z-converter-assessment/assessment-engine:<X.Y.Z> \
  --certificate-identity-regexp='^https://github.com/z-converter-assessment/assessment-engine/.github/workflows/release.yml@refs/(heads/main|tags/v)' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com'
```

이미지는 content-addressed(digest `sha256:...`)라 별도 체크섬 파일이 없다 — digest 자체가 무결성 기준이다.

## 4. pull 채널

| 채널 | 명령 |
|------|------|
| GHCR (public) | `docker pull ghcr.io/z-converter-assessment/assessment-engine:<X.Y.Z>` (토큰 없이 pull) |
| air-gapped | outbound 가능한 곳에서 `docker save ... -o image.tar` -> scp -> 운영 VM `docker load -i image.tar` |

사내 폐쇄망의 GHCR outbound 제한은 배포 환경이 결정하며, air-gapped 는 `docker save`/`docker load` 로 대응한다.

## 5. 배포 다음 단계

- `docs/guides/deploy.md` — bootstrap + rollout(deploy.sh) 가이드
- `docs/reference/contracts/env.md` — secret·환경변수 contract + 기동 시점 fail-fast 검증
- `docs/guides/migrate.md` — schema 마이그레이션

## 6. 한계

- 버전 정책은 stable semver `X.Y.Z` 만 지원한다 (prerelease/RC 미지원). prerelease 도입 시 규약 통일을 별도 ADR 로 결정한다.
- 서명 대상은 이미지뿐이다. `deploy.sh` 가 같은 tag 에서 받는 compose 파일에는 서명이 없고, compose 무결성은 raw HTTPS 와 `v*` tag 불변(ruleset — `docs/guides/ci-setup.md` 3.3)에 기댄다.
