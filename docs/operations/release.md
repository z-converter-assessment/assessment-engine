# Release Artifact

본 repo CI가 발행하는 release artifact 단일 진실. 배포(rollout) 단계는 `docs/operations/deployment.md`.

## 1. artifact 카탈로그

semver tag `v*` push 시 서명·attestation 된 멀티아치 엔진 이미지 하나를 GHCR 로 발행한다 (ADR 0048).
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

2 컴포넌트 단일 이미지 + ENTRYPOINT 가 `python -m` + CMD 가 `assessment_engine.web` (default).
운영자가 module override:
- web (default): `docker run image` → `python -m assessment_engine.web`
- consumer: `docker run image assessment_engine.consumer`
- migrate: base compose 의 init-container 가 `alembic upgrade head` 실행 (이미지 안 `_alembic.ini`·`migrations/`)

이미지 안 force-include (`pyproject.toml` `[tool.hatch.build.targets.wheel].force-include`):
- `assessment_engine/migrations/` — Alembic versions (ADR 0005)
- `assessment_engine/_alembic.ini` — Alembic config

## 2. 생성 trigger (tag-derived 버전, git-flow)

버전은 git tag(`v*`)가 단일 진실 — repo에 버전을 저장하지 않고 빌드 시 hatch-vcs가 tag에서 derive (ADR 0030). 릴리즈 = `main`에 tag push. 버전 bump 커밋이 없어 보호 브랜치(develop·main) push 마찰 0.

흐름:
1. `feature/*`·`fix/*` → PR(squash) → `develop`. (PR title은 Conventional Commits — `pr-title-check.yml` 강제. 버전 변경 없음)
2. `develop` → `main` PR(merge method) 승격·머지 — 코드만 올라감, main이 develop 이력 공유 (divergence 0). source는 develop만.
3. 릴리즈 = `main`에 tag push:
   ```bash
   git checkout main && git pull
   git tag v0.2.0 && git push origin v0.2.0
   ```
   tag 생성은 tag ruleset이 허용 (deletion·non-fast-forward만 차단). 단 `release.yml`이 stable semver `vX.Y.Z`만 수락 — prerelease/비정규 태그는 `resolve-version` job 형식 가드가 fail (ADR 0030 정정, prerelease 미지원). "다음 버전" semver는 사람이 결정 (직전 tag 이후 `feat`/`fix`/`BREAKING` 비율 보고 — 필요 시 `git log <last-tag>..main`).
4. tag push → `release.yml` 발사:
   - `resolve-version` job (앞단, 버전 derive 단일 진실): tag 형식 가드(A) → hatch-vcs(`uvx --with hatch-vcs hatch version`) 실측 PEP 440 버전 1회 산출 → job output `version`. `release-image` job이 이 output을 받아쓴다 (ADR 0030 정정 C).
   - `release-image` job (`needs: resolve-version`): `metadata-action` `{{version}}` == single source assert(B) → 버전(job output)을 `--build-arg APP_VERSION`로 전달(Dockerfile이 `SETUPTOOLS_SCM_PRETEND_VERSION`로 hatch-vcs 주입 — 빌드 컨텍스트에 `.git` 없음) → docker buildx multi-arch (`linux/amd64,arm64`) → GHCR push → cosign keyless signing + BuildKit SBOM (SPDX) + SLSA provenance. `metadata-action`은 `:X.Y`·`:X`·`:latest` alias 매핑 전용.

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
  --certificate-identity-regexp='^https://github.com/z-converter-assessment/assessment-engine/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com'
```

이미지는 content-addressed(digest `sha256:...`)라 별도 체크섬 파일이 없다 — digest 자체가 무결성 기준.

## 4. pull 채널

| 채널 | 명령 |
|------|------|
| GHCR (public) | `docker pull ghcr.io/z-converter-assessment/assessment-engine:<X.Y.Z>` (토큰 없이 pull, ADR 0035) |
| air-gapped | outbound 가능한 곳에서 `docker save ... -o image.tar` → scp → 운영 VM `docker load -i image.tar` |

사내 폐쇄망 GHCR outbound 제한은 배포 환경 결정 — air-gapped 는 `docker save/load` 로 대응.

## 5. 배포 다음 단계

본 문서는 artifact 정의·생성·검증까지. VM 부트스트랩·rollout·환경변수·alembic 절차는 별도:

- `docs/operations/deployment.md` — bootstrap + rollout(deploy.sh) 가이드
- `docs/operations/env.md` — secret·환경변수 contract + APP_ENV=prod fail-fast 검증
- `docs/operations/alembic.md` — schema 마이그레이션 (이미지 안 `_alembic.ini`, base compose migrate init-container)

## 6. 한계

- semver tag 정책 — stable semver `vX.Y.Z`만 지원 (prerelease/RC 미지원, `resolve-version` job 형식 가드가 거부). prerelease 도입 시 PEP 440/SemVer 규약 통일 별도 ADR 의무. "다음 버전" 결정은 사람이 (semver 규칙 표 참조)
- 배포 매체 = docker compose 단일. wheel+venv·k8s 등 다른 매체는 미지원 — 필요 시 별도 ADR
