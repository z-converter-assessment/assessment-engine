# syntax=docker/dockerfile:1.7
#
# Prod 이미지 — 운영자가 GHCR pull 후 즉시 3 컴포넌트 (web/consumer/diagnostic-worker)
# 운영 가능. wheel install 기반 — release artifact 와 동일 패키지 (assessment_engine-{version}-py3-none-any.whl).
# ADR 0023: scheduler cron 폐기로 4 컴포넌트 → 3 컴포넌트.
#
# 책임 분담 (#A0):
#   - 본 이미지는 운영자 선택권 (systemd · k8s · docker-compose 어느 토폴로지든 호환).
#   - 3 컴포넌트 단일 이미지 — ENTRYPOINT 가 `python -m`, CMD 가 default module (web).
#     운영자는 compose `command:` / k8s `args:` 로 module 명만 override (assessment_engine.consumer 등).
#   - 루트 docker-compose.yml(dev + 퀵스타트, ADR 0033)도 본 이미지를 build — bind mount·hot reload 없음.
#     dev 코드 반복은 venv(README "개발 환경 셋업") 또는 `docker compose up --build`.
#
# Multi-stage 구조:
#   (1) builder — uv 로 wheel build (force-include 로 migrations + alembic.ini 동봉)
#   (2) runtime — python:3.12-slim 위에 wheel pip install + non-root + OCI labels
#   builder stage 의 uv·git 등 빌드 도구는 runtime 에 잔존 안 함 → 이미지 크기·CVE 표면 최소.

# ─── (1) builder — wheel 빌드 ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

WORKDIR /build

# uv 는 wheel 빌드 도구 — runtime stage 에 포함 안 됨. hadolint DL3013 면제.
# hadolint ignore=DL3013
RUN pip install --no-cache-dir uv

# Layer cache 정석 — pyproject + lockfile 변경 빈도 가장 낮음.
# README.md 는 .dockerignore 제외라 COPY 대상 아님 (pyproject 에 readme 명시 X — uv build 영향 0).
COPY pyproject.toml uv.lock ./

# Source + migrations + alembic.ini (hatch force-include 가 wheel 안 동봉).
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

# 버전 = git tag (hatch-vcs, ADR 0030). 빌드 컨텍스트엔 .git 미포함이라 tag 를 직접 주입한다.
# release.yml 이 git tag semver 를 --build-arg APP_VERSION 으로 전달 -> SETUPTOOLS_SCM_PRETEND_VERSION
# 으로 hatch-vcs 가 그 값을 버전으로 사용. 미주입(로컬 docker build 등) 시 0.0.0.
ARG APP_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${APP_VERSION}

# `uv build --wheel` — dist/ 에 단일 wheel 산출. `--out-dir /dist` 로 다음 stage 가 쉽게 COPY.
RUN uv build --wheel --out-dir /dist


# ─── (2) runtime — wheel install + non-root ───────────────────────────────
FROM python:3.12-slim AS runtime

# Python 런타임 표준 — stdout flush 즉시·pyc 생성 X·pip 메시지 차단.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# builder 의 wheel 만 가져와 install. wheel 안에 src + migrations + alembic.ini 동봉 (#hatch.force-include).
# `--no-deps` 안 함 — pip 이 transitive deps 도 install (wheel metadata 의존성 명시).
COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm /tmp/*.whl

# Non-root user (prod 정석) — 컨테이너 안 권한 최소화. uid/gid 1000 고정.
RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app && \
    chown -R app:app /app
USER app

# OCI image labels — GHCR UI / cosign / SBOM 도구가 인식.
LABEL org.opencontainers.image.title="ZConverter Cloud Assessment Engine" \
      org.opencontainers.image.description="B2B 서버 인벤토리·메트릭 수집·진단 엔진 — 3 컴포넌트 단일 이미지 (web/consumer/diagnostic-worker)" \
      org.opencontainers.image.source="https://github.com/zconverter/assessment-engine" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.vendor="ZConverter"

# 3 컴포넌트 단일 이미지 — ENTRYPOINT 가 `python -m`, CMD 가 default module.
#   default (web):           docker run image
#   consumer:                docker run image assessment_engine.consumer
#   diagnostic-worker:       docker run image assessment_engine.diagnostic
# docker compose: `command: assessment_engine.consumer` / k8s: `args: ["assessment_engine.consumer"]`.
ENTRYPOINT ["python", "-m"]
CMD ["assessment_engine.web"]
