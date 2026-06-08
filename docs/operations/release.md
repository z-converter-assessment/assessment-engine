# Release Artifact

본 repo CI가 외부 인프라에 제공하는 release artifact 단일 진실. install·실행 단계는 `docs/operations/deployment.md`.

## 1. artifact 카탈로그

semver tag `v*` push 시 두 채널 동시 발행 — 운영자 선택권 (#A0 자율 선택, ADR 0017).

### 1.1. wheel + sdist → GitHub Release

| 파일 | 형식 | 용도 |
|------|------|------|
| `assessment_engine-X.Y.Z-py3-none-any.whl` | Python wheel (PEP 517) | `pip install`로 venv·system Python에 설치 |
| `assessment_engine-X.Y.Z.tar.gz` | sdist | source 재현 가능성 보존 (wheel 빌드 불가 환경 fallback) |
| `SHA256SUMS` | 텍스트 (sha256sum 형식) | wheel·sdist 무결성 검증 |
| `sbom.cdx.json` | CycloneDX JSON | 의존성 트리 명세 (CVE 추적) |
| `*.sigstore.json` | Sigstore signature | `cosign verify-blob` 무결성·발행자 검증 |
| `docker-compose.yml` (prod-safe base) + `env.example` | compose base + env 카탈로그 | 빌드 없는 pull-and-run prod compose (ADR 0035). `build:` 키 없음 — 받은 base 에 `ENGINE_IMAGE`(또는 base 기본 핀)·`PGDATA_HOST`·`MQ_DATA_HOST`·`OLLAMA_*` 주입 후 `docker compose up -d` 로 1.2 GHCR 이미지 pull. base 의 `__ENGINE_VERSION__` 은 release CI 가 태그 semver(예 `0.1.0`)로 치환. dev 편의(빌드·bind mount)는 repo `docker-compose.override.yml`(릴리즈 미첨부) |

wheel 안 force-include (`pyproject.toml` `[tool.hatch.build.targets.wheel].force-include`):
- `assessment_engine/migrations/` — Alembic versions (ADR 0005)
- `assessment_engine/_alembic.ini` — Alembic config

즉 wheel 1 artifact만 install하면 `alembic upgrade head` 즉시 실행 가능.

### 1.2. Docker image → GHCR

| 태그 | 의미 | 용도 |
|------|------|------|
| `ghcr.io/z-converter-assessment/assessment-engine:0.1.0` | immutable 정확 버전 (semver, git tag `v0.1.0` -> 태그는 `v` 없는 `0.1.0`) | prod pin 권장 |
| `:0.1` | minor 최신 | minor patch auto-track |
| `:0` | major 최신 | major lock |
| `:latest` | stable release 최신 | dev 시연 / 모니터링 — prod 비추천 (변경 무경고) |

이미지 attestation:
- BuildKit 자동 SBOM (SPDX) — `docker buildx imagetools inspect --format '{{ json .SBOM.SPDX }}'`
- Cosign keyless signature — `cosign verify ghcr.io/.../assessment-engine:0.1.0 --certificate-identity-regexp=... --certificate-oidc-issuer=https://token.actions.githubusercontent.com`

multi-arch: `linux/amd64` + `linux/arm64` (운영자 ARM 서버 직접 호환).

3 컴포넌트 단일 이미지 + ENTRYPOINT 가 `python -m` + CMD 가 `assessment_engine.web` (default).
운영자가 module override:
- web (default): `docker run image` → `python -m assessment_engine.web`
- consumer: `docker run image assessment_engine.consumer`
- diagnostic-worker: `docker run image assessment_engine.diagnostic`

ADR 0023: scheduler cron 폐기로 4 컴포넌트 → 3 컴포넌트.

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
   - `resolve-version` job (앞단, 버전 derive 단일 진실): tag 형식 가드(A) → hatch-vcs(`uvx --with hatch-vcs hatch version`) 실측 PEP 440 버전 1회 산출 → job output `version`. 두 release job이 이 output을 받아쓴다 (ADR 0030 정정 C — tag derive 4경로 분산을 single-source로 수렴).
   - `release-wheel` job (`needs: resolve-version`): checkout `fetch-depth: 0`(hatch-vcs가 tag 읽음) → `uv build` (wheel + sdist) → wheel 파일명 버전 == single source assert(B) → compose `__ENGINE_VERSION__` 핀 = job output 치환 → SHA256SUMS → SBOM (cyclonedx-py) → Sigstore signing → GitHub Release 첨부
   - `release-image` job (`needs: resolve-version`): `metadata-action` `{{version}}` == single source assert(B) → 버전(job output)을 `--build-arg APP_VERSION`로 전달(Dockerfile이 `SETUPTOOLS_SCM_PRETEND_VERSION`로 hatch-vcs 주입 — 빌드 컨텍스트에 `.git` 없음) → docker buildx multi-arch (`linux/amd64,arm64`) → GHCR push → cosign keyless signing → BuildKit SBOM (SPDX). `metadata-action`은 `:X.Y`·`:X`·`:latest` alias 매핑 전용.

   release notes는 GitHub가 자동 생성 (`generate_release_notes: true`) — 누적 CHANGELOG 파일 미유지.

semver 규칙 (사람이 tag 결정 시 가이드):

| 변경 성격 | bump | 0.x 동안 |
|-----------|------|----------|
| 새 기능 (`feat`) | MINOR | 0.1 → 0.2 |
| 버그 수정 (`fix`/`perf`) | PATCH | 0.1.2 → 0.1.3 |
| 호환성 깨짐 (`feat!`/`BREAKING`) | MAJOR | 0.x 동안은 MINOR로 (1.0 전 자유도) |
| 문서·잡무만 (`docs`/`chore`/`ci` 등) | 없음 | tag 안 함 |

수동 빌드 (로컬 dev 검증용 한정 — release 발사 아님):
```bash
uv build
# dist/assessment_engine-X.Y.Z-py3-none-any.whl + .tar.gz 생성
```

## 3. 무결성 검증 (외부 인프라 의무)

```bash
gh release download v1.2.3 --repo z-converter-assessment/assessment-engine \
  --pattern '*.whl' --pattern '*.tar.gz' --pattern 'SHA256SUMS' --dir /tmp/release

cd /tmp/release && sha256sum -c SHA256SUMS
# assessment_engine-1.2.3-py3-none-any.whl: OK
# assessment_engine-1.2.3.tar.gz: OK
```

## 4. 다운로드 채널

| 채널 | 명령 |
|------|------|
| GitHub Release page | https://github.com/z-converter-assessment/assessment-engine/releases/tag/v<X.Y.Z> 직접 접근 |
| `gh` CLI | `gh release download v<X.Y.Z> --repo z-converter-assessment/assessment-engine` |
| 사내 mirror | 인프라 측이 GitHub outbound 차단 시 mirror 별도 구성 (devpi·Nexus·MinIO 등) |

사내 폐쇄망 GitHub outbound 제한은 본 repo 범위 밖 — 인프라 측이 mirror 결정 (ADR 0012 한계 절).

## 5. install·실행 다음 단계

본 문서는 artifact 정의·생성·검증까지. install·systemd unit·환경변수 주입·alembic 실행 절차는 별도:

- `docs/operations/deployment.md` — 일반 install·실행 단계 가이드
- `docs/operations/env.md` — secret·환경변수 contract + APP_ENV=prod fail-fast 검증
- `docs/operations/env.md` — 환경변수 카탈로그
- `docs/operations/alembic.md` — schema 마이그레이션 (wheel 안 `_alembic.ini` 활용)

## 6. 의사결정 history

- ADR 0005 — Alembic schema 관리 단일 진실 (migrations 동봉 사유)
- ADR 0012 — wheel + GitHub Release 채택, Docker image·devpi·S3 등 옵션 비교
- ADR 0013 — release-please 자동화 (Superseded by 0028)
- ADR 0028 — Commitizen 전환 (Superseded by 0030)
- ADR 0030 — tag-derived 버전 (hatch-vcs, 버전을 repo에 저장 안 함) — 현행. 정정(2026-06-08): tag derive single-source(`resolve-version` job) + stable semver 가드 + 등가성 검증
- ADR 0035 — prod-safe compose base 첨부 (build 키 없는 pull-and-run) + override(dev) 분리. base `__ENGINE_VERSION__` CI 치환, SHA256SUMS 에 compose·env 포함

## 7. 한계

- semver tag 정책 — stable semver `vX.Y.Z`만 지원 (prerelease/RC 미지원, `resolve-version` job 형식 가드가 거부, ADR 0030 정정). prerelease 도입 시 PEP 440/SemVer 규약 통일 + 양쪽 도구 설정 정합 별도 ADR 의무. "다음 버전" 결정은 사람이 (semver 규칙 표 참조)
- wheel arch 무관 (`py3-none-any`) — Python pure code라 arch·OS 의존성 0. 단, install 환경의 Python 3.12+ 필수 (`pyproject.toml` `requires-python`)
- prod 운영 방식 자체 (systemd·k8s·docker 등) 강제 안 함 — 외부 인프라 자유 (#A0)
