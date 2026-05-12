FROM python:3.12-slim

# Python 런타임 정석:
# - PYTHONUNBUFFERED=1: stdout/stderr를 buffer 안 거치고 즉시 flush. 컨테이너 로그 실시간 가시화 (loguru는 자체 flush지만 표준 정석으로 명시).
# - PYTHONDONTWRITEBYTECODE=1: 컨테이너 안에서 .pyc 생성 안 함. 이미지 layer 깔끔, 마운트 시 호스트 오염 방지.
# - PIP_NO_CACHE_DIR=1 / PIP_DISABLE_PIP_VERSION_CHECK=1: 이미지 크기 절감 + pip 자체 메시지 noise 차단.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Layer cache 정석 — 변경 빈도가 낮은 의존성 install을 가장 안쪽 layer에 둔다.
#
# editable install (`uv pip install -e .`)은 hatchling이 pyproject의
# `packages = ["src/assessment_engine"]` 경로 존재를 검증하지만, 안의 .py 내용은 보지 않는다.
# 따라서 빈 패키지 stub을 만들고 install을 마치면, 이후 src/ 코드만 바뀌어도
# 의존성 install layer는 cache hit으로 재실행되지 않는다.
COPY pyproject.toml .
RUN mkdir -p src/assessment_engine && touch src/assessment_engine/__init__.py
RUN uv pip install --system --no-cache -e .

COPY . .

# Non-root user (prod 정석) — 컨테이너 안 권한 최소화.
# uid/gid 1000 고정. dev override(`./:/app` 마운트)에서는 `user: "0:0"`으로 root 강제 — 호스트 uid와의 충돌 회피.
RUN groupadd --system --gid 1000 app && \
    useradd  --system --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app && \
    chown -R app:app /app
USER app
