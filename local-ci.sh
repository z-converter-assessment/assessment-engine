#!/usr/bin/env bash
# 로컬 CI — .github/workflows/{ci.yml, alembic-check.yml} 의 게이트를 로컬에서 동일하게 재현.
# 커밋/PR 전 자가 검증용. GitHub CI 와 스텝·명령을 동기화 유지 (CI 변경 시 본 스크립트도 갱신).
#
# CI 잡 매핑:
#   ci.yml lint            -> ruff check . + hadolint Dockerfile
#   ci.yml test-unit       -> pytest tests/unit (cov)
#   ci.yml build           -> uv build + wheel smoke (web/consumer/worker import + 자원 포함)
#   ci.yml test-integration-> pytest tests/integration (testcontainers, docker 필요)
#   alembic-check.yml      -> alembic upgrade head + alembic check (ephemeral postgres)
#
# 사용:
#   ./local-ci.sh          전체 (docker 필요 — integration·alembic)
#   ./local-ci.sh --fast   DB 의존 단계(integration·alembic) skip (docker 불요)
set -euo pipefail
cd "$(dirname "$0")"

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

step() { printf '\n\033[1m=== %s ===\033[0m\n' "$1"; }

step "0/6 uv sync --frozen --group dev (lockfile 신선도 게이트)"
uv sync --frozen --group dev

step "1/6 ruff check ."
uv run ruff check .

step "2/6 hadolint Dockerfile (failure-threshold=warning)"
if command -v hadolint >/dev/null 2>&1; then
  hadolint --failure-threshold warning Dockerfile
elif docker image inspect hadolint/hadolint:latest >/dev/null 2>&1; then
  docker run --rm -i hadolint/hadolint hadolint --failure-threshold warning - < Dockerfile
else
  echo "hadolint 미설치 + docker 이미지 없음 -> skip (CI 에서 검증)"
fi

step "3/6 pytest tests/unit (cov)"
uv run pytest tests/unit/ -q --cov=assessment_engine --cov-report=term:skip-covered

step "4/6 wheel build + smoke (import web/consumer/worker + 자원 포함)"
rm -rf dist
uv build
# fresh venv 에 wheel install + import — uv venv(python 3.12) 사용 (시스템 python 미의존).
SMOKE=/tmp/wheel-smoke-localci
rm -rf "$SMOKE"
uv venv "$SMOKE" --python 3.12 >/dev/null
uv pip install --python "$SMOKE/bin/python" --quiet dist/*.whl
"$SMOKE/bin/python" -c "import assessment_engine.web.main; import assessment_engine.consumer.main; import assessment_engine.worker.main"
"$SMOKE/bin/python" -c "from importlib.resources import files; p = files('assessment_engine'); assert (p / 'web' / 'templates' / 'base.html').is_file(); assert (p / 'web' / 'static' / 'js' / 'chart-utils.js').is_file(); assert (p / 'migrations' / 'env.py').is_file(); assert (p / '_alembic.ini').is_file()"
rm -rf "$SMOKE"
echo "wheel smoke OK"

if [ "$FAST" = "1" ]; then
  printf '\n\033[1;32mLOCAL CI (fast) PASS\033[0m — integration·alembic-check 는 skip (docker 필요).\n'
  exit 0
fi

step "5/6 pytest tests/integration (testcontainers)"
uv run pytest tests/integration/ -q

step "6/6 alembic upgrade head + check (ephemeral timescaledb)"
PG=assessment-localci-pg
docker rm -f "$PG" >/dev/null 2>&1 || true
docker run -d --rm --name "$PG" \
  -e POSTGRES_DB=assessment_ci -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci \
  -p 5433:5432 timescale/timescaledb-ha:pg16 >/dev/null
cleanup() { docker rm -f "$PG" >/dev/null 2>&1 || true; }
trap cleanup EXIT
for _ in $(seq 1 60); do
  docker exec "$PG" pg_isready -h 127.0.0.1 -U ci >/dev/null 2>&1 && break
  sleep 1
done
export ALEMBIC_CONFIG=src/assessment_engine/_alembic.ini
export APP_ENV=dev POSTGRES_HOST=localhost POSTGRES_PORT=5433 \
  POSTGRES_DB=assessment_ci POSTGRES_USER=ci POSTGRES_PASSWORD=ci
uv run alembic upgrade head
uv run alembic check
cleanup
trap - EXIT

printf '\n\033[1;32mLOCAL CI PASS\033[0m — 모든 CI 게이트 통과.\n'
