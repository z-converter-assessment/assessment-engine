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
| `*.sigstore` | Sigstore signature | `cosign verify-blob` 무결성·발행자 검증 |

wheel 안 force-include (`pyproject.toml` `[tool.hatch.build.targets.wheel].force-include`):
- `assessment_engine/_migrations/` — Alembic versions (ADR 0005)
- `assessment_engine/_alembic.ini` — Alembic config

즉 wheel 1 artifact만 install하면 `alembic upgrade head` 즉시 실행 가능.

### 1.2. Docker image → GHCR

| 태그 | 의미 | 용도 |
|------|------|------|
| `ghcr.io/{org}/assessment-engine:v0.1.0` | immutable 정확 버전 | prod pin 권장 (운영자 선택) |
| `:0.1.0` | 동일 (semver 형식) | docker compose / k8s manifest |
| `:0.1` | minor 최신 | minor patch auto-track |
| `:0` | major 최신 | major lock |
| `:latest` | stable release 최신 | dev 시연 / 모니터링 — prod 비추천 (변경 무경고) |

이미지 attestation:
- BuildKit 자동 SBOM (SPDX) — `docker buildx imagetools inspect --format '{{ json .SBOM.SPDX }}'`
- Cosign keyless signature — `cosign verify ghcr.io/.../assessment-engine:v0.1.0 --certificate-identity-regexp=... --certificate-oidc-issuer=https://token.actions.githubusercontent.com`

multi-arch: `linux/amd64` + `linux/arm64` (운영자 ARM 서버 직접 호환).

3 컴포넌트 단일 이미지 + ENTRYPOINT 가 `python -m` + CMD 가 `assessment_engine.web` (default).
운영자가 module override:
- web (default): `docker run image` → `python -m assessment_engine.web`
- consumer: `docker run image assessment_engine.consumer`
- diagnostic-worker: `docker run image assessment_engine.diagnostic`

ADR 0023: scheduler cron 폐기로 4 컴포넌트 → 3 컴포넌트.

## 2. 생성 trigger (자동 ceremony)

release ceremony는 Conventional Commits + release-please 자동화 (ADR 0013).

흐름:
1. PR title을 Conventional Commits 형식으로 작성 (`feat:`·`fix:`·`BREAKING CHANGE:` 등) — `pr-title-check.yml`이 PR 시점 강제
2. PR squash merge 시 PR title이 main commit message가 됨
3. main push → `release-please.yml` 발사 → commit 분석
4. feat/fix/BREAKING 감지 시 자동 "Release PR" 생성·갱신:
   - `pyproject.toml` version bump (semver 정책 자동 결정)
   - `CHANGELOG.md` 갱신 (type별 분류 누적)
5. 운영자가 Release PR 검토·승인·merge
6. merge 시점에 release-please가 tag(`v*`) 자동 생성·push
7. tag push → `release.yml` 발사 (2 job 병렬):
   - `release-wheel` job: `uv build` (wheel + sdist) → SHA256SUMS → SBOM (cyclonedx-py) → Sigstore signing → GitHub Release 첨부
   - `release-image` job: docker buildx multi-arch build (`linux/amd64,arm64`) → GHCR push → cosign keyless image signing → BuildKit SBOM (SPDX) attestation

semver bump 규칙 (Conventional Commits → release-please):

| PR type | bump | 예시 |
|---------|------|------|
| `feat:` | MINOR | `feat: add diagnostic stale job cleanup` |
| `fix:` / `perf:` | PATCH | `fix: handle null hostname in mapper` |
| `feat!:` / `BREAKING CHANGE:` body | MAJOR | `feat!: rename routing key` |
| `docs:` / `chore:` / `refactor:` / `test:` / `build:` / `ci:` / `style:` / `revert:` | bump 없음 (CHANGELOG에만 누적) | `docs: clarify alembic policy` |

0.x 동안엔 `bump-minor-pre-major: true`로 BREAKING이 MINOR로 다운 — 초기 개발 자유도 보존. 1.0.0 도달 시점에 manifest 정책 수동 변경 (ADR 0013).

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

## 7. 한계

- semver tag 정책 운영 의무 — 본 repo는 tag 정책 명시 안 함 (추후 별도 결정)
- wheel arch 무관 (`py3-none-any`) — Python pure code라 arch·OS 의존성 0. 단, install 환경의 Python 3.12+ 필수 (`pyproject.toml` `requires-python`)
- prod 운영 방식 자체 (systemd·k8s·docker 등) 강제 안 함 — 외부 인프라 자유 (#A0)
