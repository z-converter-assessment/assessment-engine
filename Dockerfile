FROM python:3.12-slim

# Python 런타임 정석:
# - PYTHONUNBUFFERED=1: stdout/stderr를 buffer 안 거치고 즉시 flush. 컨테이너 로그 실시간 가시화 (loguru는 자체 flush지만 표준 정석으로 명시).
# - PYTHONDONTWRITEBYTECODE=1: 컨테이너 안에서 .pyc 생성 안 함. 이미지 layer 깔끔, 마운트 시 호스트 오염 방지.
# - PIP_NO_CACHE_DIR=1 / PIP_DISABLE_PIP_VERSION_CHECK=1: 이미지 크기 절감 + pip 자체 메시지 noise 차단.
# - UV_PROJECT_ENVIRONMENT=/opt/venv: uv sync 결과 venv 위치를 /app 바깥에 둔다. dev override의 `./:/app`
#   bind mount가 /app/.venv를 호스트로 마스킹하는 충돌을 회피.
# - UV_COMPILE_BYTECODE=1: install 시 .pyc 사전 컴파일 → 첫 import 지연 제거 (컨테이너 한정 OK).
# - UV_LINK_MODE=copy: hardlink 실패 경고 차단 (cache·target FS 다를 때 안전한 fallback).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# uv는 dev tool — latest 사용이 dev Dockerfile 의도. hadolint pin 룰 의도적 면제.
# hadolint ignore=DL3013
RUN pip install --no-cache-dir uv

# Layer cache 정석 — 변경 빈도가 낮은 의존성 install을 가장 안쪽 layer에 둔다.
#
# 2단 uv sync 패턴:
# (1) pyproject.toml + uv.lock만 copy 후 `--no-install-project`로 transitive deps만 install
#     → src/ 변경에 무관하게 layer cache 유지.
# (2) src copy 후 `uv sync --frozen`으로 project 자체(editable)만 추가 설치.
#
# `--frozen` = uv.lock과 pyproject.toml 정합 강제. drift 발견 시 build 실패 → reproducibility 보증.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

# venv 실행파일을 PATH 앞에 추가 — uvicorn·alembic을 절대경로 없이 호출 가능.
ENV PATH="/opt/venv/bin:$PATH"

# Non-root user (prod 정석) — 컨테이너 안 권한 최소화.
# uid/gid 1000 고정. dev override(`./:/app` 마운트)에서는 `user: "0:0"`으로 root 강제 — 호스트 uid와의 충돌 회피.
RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app && \
    chown -R app:app /app
USER app
