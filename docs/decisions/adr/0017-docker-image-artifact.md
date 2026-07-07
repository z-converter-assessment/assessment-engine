# ADR 0017: Docker 이미지 CI 산출물 추가 (wheel 보조)

Status: Accepted (2026-05-21)

Refines: ADR 0012 (CI 산출물 = wheel + GitHub Release)

## Context

ADR 0012 결정: "본 repo 의 CI 산출물 = Python wheel (+ sdist · SHA256SUMS · SBOM · Sigstore). prod 운영 방식 contract 를 docker compose 형식으로 강제하지 않는다."

본 결정은 prod 운영 토폴로지 강제 금지 정합 — 외부 인프라가 OS·도구 자율 선택 (#A0). 그러나 wheel-only 산출물은 운영자 토폴로지에 비대칭 부담:
- systemd / venv 운영자: wheel pip install → 즉시 사용 (정합)
- docker / k8s 운영자: wheel → 자체 Dockerfile 작성 → image build → registry push (외부 부담)

운영자 자율 선택 정책은 양 토폴로지 모두 즉시 사용 가능해야 정합. wheel 만으로는 container 운영자에게 빌드 부담 이전. ADR 0012 본질 ("compose 형식 강제 금지") 와는 다른 issue — image artifact 제공 자체는 운영 토폴로지를 강제하지 않음 (운영자가 image 안 쓰고 wheel 만 써도 가능).

산업 일반: 서비스형 OSS (kafka·grafana·loki·rabbitmq·redis 등) 모두 release 에 wheel/tarball + Docker image 둘 다 제공 — 선택권 정합.

## Decision

CI 산출물에 Docker image 추가. wheel 과 image 양쪽 동시 발행.

### 이미지 정책
- Registry: GHCR (`ghcr.io/{org}/assessment-engine`) — 무료, OCI 표준
- Multi-arch: `linux/amd64` + `linux/arm64` (ARM 서버 호환)
- 3 컴포넌트 단일 이미지 + `ENTRYPOINT ["python", "-m"]` + `CMD ["assessment_engine.web"]` — 운영자가 module 만 override (`assessment_engine.consumer` / `.diagnostic`). ADR 0023: scheduler cron 폐기로 4 컴포넌트 → 3 컴포넌트.
- Non-root user (uid/gid 1000)
- Multi-stage build: builder (wheel build) → runtime (wheel install + slim base)

### Tag policy (semver tag v* push 시)
- `:0.1.0` — immutable 정확한 버전 (운영자 prod pin 권장)
- `:0.1` — minor 최신
- `:0` — major 최신
- `:latest` — stable release 만 (prerelease tag 인 `v*-rc.*` 등 제외)
- 모두 동일 image digest 가리킴 — pull 시 동일 산출물

### 무결성 검증
- Cosign keyless signing (Sigstore OIDC keychain — wheel 의 sigstore 와 동일)
- BuildKit 자동 SBOM (SPDX) — image attestation 첨부
- 운영자 검증: `cosign verify ghcr.io/.../assessment-engine:v0.1.0 --certificate-identity-regexp=... --certificate-oidc-issuer=https://token.actions.githubusercontent.com`

### Dockerfile 분리
- `Dockerfile` (root) = prod — multi-stage builder + wheel install + non-root + ENTRYPOINT/CMD
- `dev/Dockerfile` = dev 한정 — source bind mount + uv editable install (hot reload 친화). `dev/docker-compose.yml` 의 build context 가 본 dev/Dockerfile 사용

## Consequences

### 얻음
- 운영자 선택권: systemd / venv (wheel) / docker / k8s (image) 모두 즉시 사용 가능
- 자율 선택 정책 (#A0) 정합 — 토폴로지 강제 0
- 보안: cosign keyless 서명 + SBOM 으로 supply chain 검증
- Multi-arch ARM 서버 직접 호환 (cross-compile 외부 부담 0)

### 비용
- GitHub Actions 빌드 시간 추가 (multi-arch QEMU emulation 약 5~10분)
- GHCR 저장 (GitHub 무료 한도 안)
- Base image (python:3.12-slim) CVE 추적 의무 — Dependabot Docker eco-system enable
- Dockerfile 분리 유지 (`Dockerfile` prod + `dev/Dockerfile` dev) — 변경 시 양쪽 갱신 의무

### ADR 0012 와의 관계
- ADR 0012 "compose 형식 강제 금지" 결정은 유지 — `docker-compose.prod.yml` 본 repo 두지 않음 (운영 토폴로지 강제 회피)
- 본 ADR 은 image artifact 제공 추가 — image 사용 자체는 운영 토폴로지 강제 아님 (운영자가 image 안 쓰고 wheel 만 써도 됨, 또는 image 받아 자체 compose · k8s manifest 자유 작성)
- ADR 0012 supersede 아닌 보강 (refines)

## 운영자 사용 예시

### Docker 직접 실행
```bash
# default = web
docker run -d --name web -p 8000:8000 \
  --env-file .env ghcr.io/zconverter/assessment-engine:v0.1.0

# consumer (module override)
docker run -d --name consumer \
  --env-file .env ghcr.io/zconverter/assessment-engine:v0.1.0 \
  assessment_engine.consumer

# diagnostic-worker
docker run -d --name worker --env-file .env \
  ghcr.io/zconverter/assessment-engine:v0.1.0 assessment_engine.diagnostic
```

### docker-compose.yml (외부 인프라 작성)
```yaml
services:
  web:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: .env
    ports: ["8000:8000"]
  consumer:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: .env
    command: assessment_engine.consumer
  diagnostic-worker:
    image: ghcr.io/zconverter/assessment-engine:0.1
    env_file: .env
    command: assessment_engine.diagnostic
```

### Kubernetes Deployment (외부 인프라 작성)
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: consumer}
spec:
  replicas: 3   # consumer 수평 확장
  template:
    spec:
      containers:
        - name: consumer
          image: ghcr.io/zconverter/assessment-engine:0.1.0
          args: ["assessment_engine.consumer"]   # CMD override
          envFrom:
            - secretRef: {name: assessment-engine-env}
```

### 무결성 검증 (운영자 측)
```bash
# Cosign image 검증
cosign verify ghcr.io/zconverter/assessment-engine:v0.1.0 \
  --certificate-identity-regexp='https://github\.com/zconverter/assessment-engine/.*' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com'

# SBOM 확인 (BuildKit attestation)
docker buildx imagetools inspect ghcr.io/zconverter/assessment-engine:v0.1.0 \
  --format '{{ json .SBOM.SPDX }}'
```

## 폐쇄망 (air-gapped) 운영

GHCR pull 불가 환경에서는 `docker save` tar export + scp:
```bash
# 외부망 (인터넷 가능 환경)
docker pull ghcr.io/zconverter/assessment-engine:v0.1.0
docker save ghcr.io/zconverter/assessment-engine:v0.1.0 \
  -o assessment-engine-v0.1.0.tar
# (수백 MiB, 운영자가 scp 또는 USB 로 air-gapped 환경 이동)

# air-gapped 환경
docker load -i assessment-engine-v0.1.0.tar
```

wheel artifact 도 동일 패턴 (`pip wheel` → scp). 운영자 선택권 (wheel 또는 image 어느 채널이든) 그대로.
