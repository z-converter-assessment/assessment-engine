#!/usr/bin/env bash
set -euo pipefail

echo "[1/2] Vagrant VM 제거 중..."
if vagrant status | grep -qE "running|poweroff|saved"; then
  vagrant destroy -f
else
  echo "  VM이 존재하지 않음, 건너뜀"
fi

echo "[2/2] Docker 서비스 및 볼륨 제거 중..."
docker compose down -v

echo ""
echo "환경 종료 완료 (볼륨 삭제됨)"
