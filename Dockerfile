# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.14-slim AS builder

# uv 는 hardlink 로 캐시를 연결하려다 레이어 경계에서 실패한다.
# 가상환경 안 스크립트에 절대경로 shebang 이 박혀 builder 와 runtime 의 경로가 같아야 한다.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

# 의존성을 소스보다 먼저 설치해 레이어를 나눈다 — 소스만 바뀌면 이 레이어가 재사용된다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

# --no-editable 이라 파일이 복사된다 — 최종 이미지에 소스 트리가 없어도 동작한다.
RUN uv sync --frozen --no-dev --no-editable


FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# alembic 은 cwd 의 alembic.ini 를 찾는데 설정 파일이 패키지 안에 있어 못 만난다. 이미지가 값을 들고
# 있어야 compose 없이 docker run 으로도 마이그레이션 명령이 돈다.
ENV ALEMBIC_CONFIG=/opt/venv/lib/python3.14/site-packages/assessment_engine/_alembic.ini

WORKDIR /app

RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

USER app

LABEL org.opencontainers.image.title="ZConverter Cloud Assessment Engine" \
      org.opencontainers.image.description="B2B 서버 인벤토리·메트릭 수집·진단 엔진 — web·consumer·worker·migrate 단일 이미지" \
      org.opencontainers.image.source="https://github.com/z-converter-assessment/assessment-engine" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.vendor="ZConverter"

# 어느 컴포넌트를 띄울지는 compose 의 command 가 전부 정한다. 빈 CMD 는 베이스 이미지가 물려주는
# python REPL 을 지워, 명령 없이 실행하면 기동하는 대신 거부되게 한다.
CMD []
