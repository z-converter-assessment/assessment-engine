# Release Artifact

본 repo CI가 발행하는 release artifact 단일 진실. 배포(rollout) 단계는 `docs/guides/deploy.md`.

## 1. artifact 카탈로그

릴리즈는 서명·attestation 된 멀티아치 엔진 이미지 하나를 GHCR 로 발행한다.
배포 매체는 docker compose 단일 — 이미지가 유일한 배포 산출물이고, compose 파일은 repo 안에서 checkout 해
쓴다(별도 release 첨부 없음).

### 1.1. Docker image → GHCR

| 태그 | 의미 | 용도 |
|------|------|------|
| `ghcr.io/z-converter-assessment/assessment-engine:0.1.0` | immutable 정확 버전 (semver, git tag `v0.1.0` -> 태그는 `v` 없는 `0.1.0`) | prod pin (배포 기본) |
| `:0.1` | minor 최신 | minor patch auto-track |
| `:0` | major 최신 | major lock |
| `:latest` | stable release 최신 | 모니터링 — prod 비추천 (변경 무경고) |

이미지 attestation (별도 파일이 아니라 이미지에 귀속):
- Cosign keyless signature — `cosign verify ghcr.io/.../assessment-engine:0.1.0 --certificate-identity-regexp=... --certificate-oidc-issuer=https://token.actions.githubusercontent.com`
- BuildKit SBOM (SPDX) — `docker buildx imagetools inspect --format '{{ json .SBOM.SPDX }}'`
- SLSA provenance — `docker buildx imagetools inspect --format '{{ json .Provenance }}'`

multi-arch: `linux/amd64` + `linux/arm64` (운영자 ARM 서버 직접 호환).

단일 이미지 + ENTRYPOINT 가 `python -m` + CMD 가 `assessment_engine.web` (default).
운영자가 module override:
- web (default): `docker run image` → `python -m assessment_engine.web`
- consumer: `docker run image assessment_engine.consumer`
- worker: `docker run image assessment_engine.worker`
- migrate: base compose 의 init-container 가 `alembic upgrade head` 실행 (이미지 안 `_alembic.ini`·`migrations/`)

이미지 안 Alembic 자원 (패키지 디렉토리 안에 있어 별도 포장 설정 없이 동봉):
- `assessment_engine/migrations/` — Alembic versions
- `assessment_engine/_alembic.ini` — Alembic config

## 2. 생성 trigger (파일 버전, git-flow)

버전 입력 지점은 `pyproject.toml` 의 `version` 하나다. 릴리즈 = 그 값을 올린 커밋이 `main` 에 들어오는 것이며, git tag 는 릴리즈가 성공한 뒤 `release.yml` 이 그 값에서 파생 생성한다. 사람이 tag 를 붙이지 않으므로 파일과 tag 가 어긋날 수 없다.

흐름:
1. `feature/*`·`fix/*` → PR(squash) → `develop`. (PR title은 Conventional Commits — `pr-title-check.yml` 강제)
2. 릴리즈 준비 — 브랜치에서 버전을 올려 `develop` 으로 PR:
   ```bash
   git checkout -b chore/release-0.2.0
   uv version --bump minor      # pyproject.toml 의 version 수정
   git commit -am "chore: release 0.2.0"
   ```
   develop·main 은 직접 push 가 차단되므로 bump 커밋도 PR 을 거친다.
   "다음 버전" semver는 사람이 결정 (직전 릴리즈 이후 `feat`/`fix`/`BREAKING` 비율 보고 — 필요 시 `git log <last-tag>..develop`).
3. `develop` → `main` PR(merge method) 승격·머지 — main이 develop 이력 공유 (divergence 0). source는 develop만.
4. `main` push → `release.yml` 발사:
   - `resolve-version` job: `uv version --short` 로 버전 산출 → stable semver `X.Y.Z` 형식 가드 (prerelease 미지원). 해당 tag 가 이미 있으면 버전을 안 올린 일반 머지이므로 릴리즈를 건너뛴다.
   - `release-image` job (`needs: resolve-version`): `metadata-action` 이 확정 버전에서 `:X.Y`·`:X`·`:latest` alias 파생 → docker buildx multi-arch (`linux/amd64,arm64`) → GHCR push → cosign keyless signing + BuildKit SBOM (SPDX) + SLSA provenance → 마지막에 `v<version>` tag 생성·push. tag 를 끝에 남기므로 앞 단계 실패 시 그대로 재시도할 수 있다.

`workflow_dispatch` 는 이미 릴리즈된 버전의 재발행(이미지 유실·재서명)에 쓴다 — tag 중복 판정을 건너뛰고 진행한다.

semver 규칙 (사람이 tag 결정 시 가이드):

| 변경 성격 | bump | 0.x 동안 |
|-----------|------|----------|
| 새 기능 (`feat`) | MINOR | 0.1 → 0.2 |
| 버그 수정 (`fix`/`perf`) | PATCH | 0.1.2 → 0.1.3 |
| 호환성 깨짐 (`feat!`/`BREAKING`) | MAJOR | 0.x 동안은 MINOR로 (1.0 전 자유도) |
| 문서·잡무만 (`docs`/`chore`/`ci` 등) | 없음 | tag 안 함 |

## 3. 무결성 검증 (배포 게이트)

배포(`deploy.sh`)가 pull 전에 cosign 서명을 검증한다 — 미통과 시 배포 중단. 수동 검증:

```bash
cosign verify ghcr.io/z-converter-assessment/assessment-engine:1.2.3 \
  --certificate-identity-regexp='^https://github.com/z-converter-assessment/assessment-engine/.github/workflows/release.yml@refs/heads/main' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com'
```

이미지는 content-addressed(digest `sha256:...`)라 별도 체크섬 파일이 없다 — digest 자체가 무결성 기준.

## 4. pull 채널

| 채널 | 명령 |
|------|------|
| GHCR (public) | `docker pull ghcr.io/z-converter-assessment/assessment-engine:<X.Y.Z>` (토큰 없이 pull) |
| air-gapped | outbound 가능한 곳에서 `docker save ... -o image.tar` → scp → 운영 VM `docker load -i image.tar` |

사내 폐쇄망 GHCR outbound 제한은 배포 환경 결정 — air-gapped 는 `docker save/load` 로 대응.

## 5. 배포 다음 단계

본 문서는 artifact 정의·생성·검증까지. VM 부트스트랩·rollout·환경변수·alembic 절차는 별도:

- `docs/guides/deploy.md` — bootstrap + rollout(deploy.sh) 가이드
- `docs/reference/contracts/env.md` — secret·환경변수 contract + APP_ENV=prod fail-fast 검증
- `docs/guides/migrate.md` — schema 마이그레이션 (이미지 안 `_alembic.ini`, base compose migrate init-container)

## 6. 한계

- 버전 정책 — stable semver `X.Y.Z`만 지원 (prerelease/RC 미지원). prerelease 도입 시 규약 통일 별도 ADR 의무
- 배포 매체 = docker compose 단일. wheel+venv·k8s 등 다른 매체는 미지원 — 필요 시 별도 ADR
