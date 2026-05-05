#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] Docker 서비스 기동 중..."
docker compose up -d --build

echo "[2/3] web 헬스체크 대기 중..."
TIMEOUT=120
ELAPSED=0
until docker compose ps web | grep -q "healthy"; do
  if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "오류: web 헬스체크 ${TIMEOUT}초 초과. 로그를 확인하세요."
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
echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
