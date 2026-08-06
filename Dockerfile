# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded AS uv

FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS builder

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


FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

# 베이스가 딸려 보낸 pip 을 걷는다. 애플리케이션은 /opt/venv 만 쓰고 그 venv 는
# include-system-site-packages=false 라 여기를 보지 않으므로, 남겨 두면 실행되지 않는 채로
# vendor 트리(pip/_vendor)의 취약점만 이미지에 싣는다.
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

# 어느 컴포넌트를 띄울지는 compose 의 command 가 전부 정한다. 빈 CMD 는 베이스 이미지가 물려주는
# python REPL 을 지워, 명령 없이 실행하면 기동하는 대신 거부되게 한다.
CMD []
