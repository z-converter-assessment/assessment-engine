# ADR 0012 — CI 산출물 정책: Python wheel + GitHub Release

상태: Superseded by ADR 0048 (2026-07-01) — 배포 산출물이 wheel + GitHub Release 에서 GHCR 이미지 단일로 바뀌었다. wheel 빌드는 CI 패키징 검증으로 존속(ADR 0057).

## Context

본 repo는 CI까지 + 배포 contract를 인프라에 제공하는 범위(CLAUDE.md #A0). 운영 환경 가정은 인프라 측이 결정 — 다만 차용자(인프라 담당)가 본 엔진을 VM + Linux + systemd 환경에 배포할 가능성이 가장 유력 (사내 폐쇄망 OpenStack + DB 등 stateful은 host package 권장 + 작은 규모 단일 호스트 운영).

이 운영 모델에서 Docker image는 산출물로서 가치가 작음 — 운영 환경이 컨테이너를 안 쓰므로. 대신 Python 표준 build artifact(wheel)가 인프라 측에 자연스러운 입력.

본 repo가 CI에서 어떤 산출물을 어떤 위치에 publish할지 결정 필요. 본 ADR 이전 상태(ADR 이전):
- ci.yml의 `build` job이 `docker build` 통과 verify만 수행 (push 0)
- 별도 산출물 publish 정책 없음 — 인프라 측이 git clone + 자체 build

## Decision

본 repo CI 산출물 = Python wheel. 두 채널로 publish.

### 1. CI workflow (`ci.yml`) — 모든 PR + main push

- `uv build`로 wheel + sdist 생성
- wheel을 fresh venv에 install → import + 정적 자원 포함 검증 (smoke test)
- wheel을 GitHub Actions artifact로 upload (retention 7일 — 디버깅·중간 산출물 활용)
- registry push 0 — main push만으로는 stable release 아님

### 2. Release workflow (`release.yml`) — semver tag(`v*`) push

- `uv build` + sha256 checksums
- GitHub Release 자동 생성 + wheel·sdist·SHA256SUMS 첨부
- release body는 git tag annotation 또는 GitHub auto-generated changelog
- 인프라 측이 release page 또는 API로 wheel·sha256 다운로드

### 3. wheel 내용물 (force-include)

`pyproject.toml`의 `[tool.hatch.build.targets.wheel].force-include`로 다음 자원을 wheel에 동봉:
- `migrations/` → `assessment_engine/_migrations/`
- `alembic.ini` → `assessment_engine/_alembic.ini`

이유: 차용자가 wheel 1 artifact만 install하면 alembic migration 즉시 실행 가능 — `python -m alembic -c $(python -c 'from importlib.resources import files; print(files("assessment_engine") / "_alembic.ini")') upgrade head`. 별도 git clone 또는 추가 artifact 불필요.

### 4. systemd unit example

본 repo는 systemd unit example을 별도 파일로 두지 않음 — `docs/guides/deploy.md` 4절 inline 예시(`multi-node 분리 inject 예시`)가 단일 진실. 외부 인프라가 자체 Ansible template으로 생성 (#A0 — prod 운영 contract를 systemd unit 형식으로 강제하지 않음).

### 5. Dockerfile·docker-compose 위치 재정의

`Dockerfile` + `docker-compose.yml`은 dev·기능 개발 환경 한정. prod CI 산출물 lifecycle에서 제외 (`docker build` 통과 검증은 ci.yml에서 제거됨).

`docker-compose.prod.yml`은 본 ADR 채택 시점에 제거됨 (2026-05-16) — 본 repo는 prod 운영 contract를 docker compose 형식으로 강제하지 않음. prod secret 채널 contract는 코드(`config.py` `_validate_prod_*`)와 docs(`docs/operations/prod-contract.md`)에만. 외부 인프라가 systemd EnvironmentFile·Vault·k8s Secret 등 어떤 채널을 쓰든 결과만 검증(weak default 거부).

인프라 측이 컨테이너 기반 운영을 결정하면 자체 image build·compose 작성 가능 — 본 repo 책임 밖 (CLAUDE.md #A0).

## Options Considered

1. Wheel + GitHub Release (채택)
   - 장점: Python 표준 PEP 517 artifact. GitHub Actions와 자연 통합. 무료 hosting. semver tag = immutable artifact 원칙 정합.
   - 단점: 사내 폐쇄망에서 GitHub outbound 차단 시 mirror 필요 — 인프라 측 책임.

2. Wheel + 사내 devpi (사내 PyPI 서버)
   - 장점: `pip install` 인덱스 표준. 사내 폐쇄망 자연.
   - 단점: devpi 서버 운영 부담. 본 repo가 push credential 보유. 본 ADR 시점은 devpi 인프라 부재 — 도입 후보로 보류.

3. Wheel + S3·MinIO·OpenStack Swift
   - 장점: object storage 활용. 단순 file hosting.
   - 단점: pip install 인덱스 아님. wget 직접. 정석 PyPI 흐름과 멀어짐.

4. Docker image 유지
   - 장점: 컨테이너 운영 환경에 즉시 사용.
   - 단점: VM + systemd 운영 가정에 부적합. 산출물 무용.

5. Registry 없음 (인프라 측 자체 빌드)
   - 장점: 본 repo 인프라 0.
   - 단점: 인프라 측이 매번 빌드. 환경별 build 차이 가능성. immutable artifact 원칙 약화.

옵션 1 채택 — semver release 정책의 표준 흐름. 사내 mirror 필요성은 인프라 측이 GitHub 도달성 보고 결정.

## Consequences

장점
- 차용자(인프라)가 wheel 1 artifact만 받으면 install·alembic·systemd 흐름 즉시 시작 가능.
- semver tag = immutable artifact 원칙 정합. 환경별 build 차이 0.
- sha256 checksums로 artifact 무결성 검증 가능.
- GitHub Actions·Release와 자연 통합 — 추가 인프라 0.

단점·한계
- 사내 폐쇄망 GitHub outbound 제한 가능성 — 그 경우 인프라 측이 mirror·devpi 별도 구성. 본 repo 책임 밖.
- semver tag 정책 운영 의무 — 본 ADR 시점은 tag 정책 규정 없음. 추후 별도 결정.
- wheel에 migrations 동봉했지만 alembic 호출 시 `_alembic.ini` 경로 동적 해석 필요 — install 시점 path를 차용자 ansible role에서 처리.

## Migration (본 ADR 채택 시점)

| 작업 | 위치 | 변경 |
|------|------|------|
| pyproject.toml | `[tool.hatch.build.targets.wheel]` | `force-include` migrations·alembic.ini |
| ci.yml | `build` job | `docker build` → `uv build` + wheel smoke test + artifact upload |
| release.yml | 신규 | tag `v*` 트리거. wheel·sdist·SHA256SUMS GitHub Release 첨부 |
| docs | docker.md·deployment.md·CLAUDE.md | wheel 산출물·systemd 패턴 명시 (unit 예시는 deployment.md inline) |

## 관련 문서·코드

- `pyproject.toml` `[tool.hatch.build.targets.wheel]` — wheel build 설정
- `.github/workflows/ci.yml` — wheel build + smoke test
- `.github/workflows/release.yml` — semver tag → GitHub Release
- `docs/guides/release.md` — release artifact 단일 진실 (카탈로그·trigger·검증·다운로드)
- `docs/guides/deploy.md` 4절 — systemd unit·multi-node 분리 inject 예시 inline
- CLAUDE.md #A0 — CI vs CD 책임 분리 + 본 repo 범위
- ADR 0005 — Alembic schema 관리 (migrations 동봉 사유)
## 정정 (2026-06-01, ADR 0033)

- 5절("docker-compose.prod 미제공 / prod 운영을 docker compose 형식으로 강제 안 함")은 ADR 0033이 supersede. 루트 `docker-compose.yml`을 prod 퀵스타트(단일 호스트 all-in-one) 용도로 제공한다. 본 ADR 1-4절(wheel + GitHub Release artifact 정책)은 유지 — compose는 추가 경로이지 대체가 아니다.
- 3절 force-include 경로 정정: migrations 동봉 위치가 `assessment_engine/_migrations`에서 `assessment_engine/migrations`로 변경. 번들 `_alembic.ini`의 `script_location`(`%(here)s/migrations`)과 정합해야 wheel install 후 migrate가 실제 동작(이전 경로는 불일치로 실패). `_alembic.ini` 파일명은 그대로. 상세: ADR 0033.
