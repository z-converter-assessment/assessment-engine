# syntax=docker/dockerfile:1.7

# uv 버전 고정 지점.
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

# ─── (1) builder — venv 구성 ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv 는 기본값인 hardlink 로 캐시를 연결하려다 레이어 경계를 넘지 못해 경고를 낸다.
# 설치 시점에 .pyc 를 만들어 두면 컨테이너가 뜰 때마다 컴파일하지 않는다.
# 가상환경은 프로젝트 디렉토리 밖에 둔다 — dev override 가 /app 을 bind mount 하면 기본 위치(/app/.venv)가 가려진다.
# 가상환경 안 스크립트에 절대경로 shebang 이 박히므로 runtime 과 경로가 같아야 한다.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv

# 의존성만 먼저 설치한다 — 소스가 바뀌어도 이 레이어는 재사용된다.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

# --no-editable — 소스를 가리키는 링크가 아니라 파일을 복사해 넣는다. 최종 이미지에 소스 트리가 없어도 된다.
RUN uv sync --frozen --no-dev --no-editable


# ─── (2) runtime — venv 만 복사 + non-root ────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

USER app

LABEL org.opencontainers.image.title="ZConverter Cloud Assessment Engine" \
      org.opencontainers.image.description="B2B 서버 인벤토리·메트릭 수집·진단 엔진 — web·consumer·worker·migrate 단일 이미지" \
      org.opencontainers.image.source="https://github.com/zconverter/assessment-engine" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.vendor="ZConverter"

# docker run 인자가 CMD 를 덮어써서 실행할 컴포넌트를 고른다.
#   docker run image                             -> web
#   docker run image assessment_engine.consumer  -> consumer
ENTRYPOINT ["python", "-m"]
CMD ["assessment_engine.web"]
