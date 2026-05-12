#!/usr/bin/env bash
set -euo pipefail

# Compose v2.17+ 의무 (service_completed_successfully) — migrate 컨테이너가 alembic upgrade head 후 종료.
# 첫 build + postgres healthy + migrate 실행 + web 헬스체크까지 시간 여유 두고 대기.

readonly TIMEOUT=180

# 컨테이너 상태를 grep 추론 대신 `docker inspect` JSON 필드로 정확히 읽는 헬퍼.
# - service_state: docker compose 서비스 1개의 첫 컨테이너 state (running/exited/...)
# - service_health: healthcheck status (healthy/unhealthy/starting/none)
# - service_exit_code: 종료된 컨테이너의 exit code (성공 0 / 실패 != 0)
service_state() {
  local cid
  # `-a` 의무 — `migrate`처럼 exited 후 자동 제거 안 된 init container 포함.
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "missing"; return; }
  docker inspect --format='{{.State.Status}}' "$cid" 2>/dev/null || echo "missing"
}
service_health() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "none"; return; }
  docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "none"
}
service_exit_code() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "-1"; return; }
  docker inspect --format='{{.State.ExitCode}}' "$cid" 2>/dev/null || echo "-1"
}

dump_logs_and_exit() {
  echo "--- migrate 로그 (마지막 30줄) ---"
  docker compose logs migrate --tail=30 || true
  echo "--- web 로그 (마지막 30줄) ---"
  docker compose logs web --tail=30 || true
  exit 1
}

echo "[1/4] Docker 서비스 기동 중 (build 포함, postgres healthy 후 migrate 실행)..."
docker compose up -d --build

echo "[2/4] migrate(alembic upgrade head) 완료 대기 중..."
# bash 내장 SECONDS 변수 활용 — 별도 카운터 안 만든다.
SECONDS=0
while :; do
  state="$(service_state migrate)"
  if [ "$state" = "exited" ]; then
    code="$(service_exit_code migrate)"
    if [ "$code" = "0" ]; then
      echo "  migrate 완료 (exit 0)"
      break
    fi
    echo "오류: migrate 실패 (exit ${code})."
    dump_logs_and_exit
  fi
  if [ "$SECONDS" -ge "$TIMEOUT" ]; then
    echo "오류: migrate ${TIMEOUT}초 초과 (현재 state=${state})."
    dump_logs_and_exit
  fi
  sleep 2
  echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, state=${state})"
done

echo "[3/4] web 헬스체크 대기 중..."
SECONDS=0
while [ "$(service_health web)" != "healthy" ]; do
  health="$(service_health web)"
  state="$(service_state web)"
  if [ "$state" = "exited" ]; then
    echo "오류: web 컨테이너가 종료됨 (exit $(service_exit_code web))."
    dump_logs_and_exit
  fi
  if [ "$SECONDS" -ge "$TIMEOUT" ]; then
    echo "오류: web 헬스체크 ${TIMEOUT}초 초과 (health=${health})."
    dump_logs_and_exit
  fi
  sleep 3
  echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, health=${health})"
done
echo "  web healthy"

echo "[4/4] Vagrant VM 기동 중..."
vagrant up

echo ""
echo "환경 준비 완료"
echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
echo "  pgAdmin : http://localhost:${PGADMIN_PORT:-5050} (서버는 자동 등록, password만 입력)"
echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
