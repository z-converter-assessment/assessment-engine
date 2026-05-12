#!/usr/bin/env bash
set -euo pipefail

# Compose v2.17+ 의무 (service_completed_successfully) — migrate 컨테이너가 alembic upgrade head 후 종료.
# 첫 build + postgres healthy + migrate 실행 + web 헬스체크까지 시간 여유 두고 대기.

echo "[1/3] Docker 서비스 기동 중 (postgres healthy → migrate alembic upgrade → 앱 서비스 기동)..."
docker compose up -d --build

echo "[2/3] web 헬스체크 대기 중..."
TIMEOUT=180
ELAPSED=0
until docker compose ps web | grep -q "healthy"; do
  if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "오류: web 헬스체크 ${TIMEOUT}초 초과."
    echo "--- migrate 로그 (마지막 30줄) ---"
    docker compose logs migrate --tail=30 || true
    echo "--- web 로그 (마지막 30줄) ---"
    docker compose logs web --tail=30
    exit 1
  fi
  sleep 3
  ELAPSED=$((ELAPSED + 3))
  echo "  대기 중... (${ELAPSED}s / ${TIMEOUT}s)"
done
echo "  web 헬스체크 통과"

echo "[3/3] Vagrant VM 기동 중..."
vagrant up

echo ""
echo "환경 준비 완료"
echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
echo "  pgAdmin : http://localhost:${PGADMIN_PORT:-5050} (서버는 자동 등록, password만 입력)"
echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
