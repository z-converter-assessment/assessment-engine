# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv

FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# Builder와 runtime의 가상환경 경로가 같아야 entry point shebang이 유효하다.

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

RUN uv sync --frozen --no-dev --no-editable


FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

# 애플리케이션은 /opt/venv만 사용하므로 base pip를 남기지 않는다.
RUN rm -rf /usr/local/lib/python3.*/site-packages/pip \
           /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

COPY --from=builder /opt/venv /opt/venv

USER app

LABEL org.opencontainers.image.title="ZConverter Cloud Assessment Engine" \
      org.opencontainers.image.description="B2B 서버 인벤토리·메트릭 수집·진단 엔진 — web·consumer·worker·migrate 단일 이미지" \
      org.opencontainers.image.source="https://github.com/z-converter-assessment/assessment-engine" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.vendor="ZConverter"

CMD []
