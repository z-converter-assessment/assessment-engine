FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

# Layer cache 정석 — 변경 빈도가 낮은 의존성 install을 가장 안쪽 layer에 둔다.
#
# editable install (`uv pip install -e .`)은 hatchling이 pyproject의
# `packages = ["src/assessment_engine"]` 경로 존재를 검증하지만, 안의 .py 내용은 보지 않는다.
# 따라서 빈 패키지 stub을 만들고 install을 마치면, 이후 src/ 코드만 바뀌어도
# 의존성 install layer는 cache hit으로 재실행되지 않는다.
COPY pyproject.toml .
RUN mkdir -p src/assessment_engine && touch src/assessment_engine/__init__.py
RUN uv pip install --system --no-cache -e .

# 의도 표현: src는 가장 중요한 자원이라 별도 layer로 명시. 동작상 다음 `COPY . .`과 결과 동일.
COPY src/ ./src/

COPY . .