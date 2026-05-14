#!/usr/bin/env bash
# install.sh — 단일 VM dev/staging 배포 자동화 (OpenStack 등 비-Lima 환경).
#
# 책임:
#   1. 사전 점검 (docker daemon 가동, .env.example 존재)
#   2. 엔진 VM IP/hostname 입력 받음 (명령행 인자 또는 prompt)
#   3. .env 생성 (.env.example cp + INSTALL_BUNDLE_URL 동적 주입)
#   4. docker compose up --build -d
#   5. web healthcheck 대기 + curl 검증
#
# 사용법:
#   ./install.sh <engine-ip-or-hostname>   # 명령행 인자
#   ./install.sh                           # interactive prompt
#   FORCE=1 ./install.sh <ip>              # .env 강제 재생성 (기존 .env 덮어쓰기)
#
# Lima 7 VM 시연은 별도 — `./dev-up.sh` 사용.
# 본 스크립트는 docker compose 단일 VM 배포 한정. agent VM 은 별도 절차.

set -euo pipefail

# ─── 0. 헬퍼 ──────────────────────────────────────────────────────────
usage() {
  echo "Usage: $0 <engine-ip-or-hostname>"
  echo ""
  echo "Examples:"
  echo "  $0 192.168.1.10           # OpenStack VM IP"
  echo "  $0 engine.internal        # 내부 DNS hostname"
  echo "  FORCE=1 $0 192.168.1.10   # .env 강제 재생성"
  exit 1
}

# sed -i 호환 (BSD macOS vs GNU Linux).
sed_inplace() {
  local expr="$1"; local file="$2"
  if sed --version >/dev/null 2>&1; then
    sed -i "$expr" "$file"
  else
    sed -i '' "$expr" "$file"
  fi
}

# ─── 1. 사전 점검 ─────────────────────────────────────────────────────
echo "[1/5] 사전 점검..."
if ! command -v docker >/dev/null 2>&1; then
  echo "  오류: docker 가 PATH 에 없다. Docker Engine 설치 후 재시도."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "  오류: docker daemon 미가동. 'sudo systemctl start docker' 후 재시도."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "  오류: docker compose v2 미설치."
  exit 1
fi
if [ ! -f .env.example ]; then
  echo "  오류: .env.example 없음. 프로젝트 루트에서 실행했나?"
  exit 1
fi
echo "  docker daemon OK"

# ─── 2. 엔진 host 입력 ────────────────────────────────────────────────
echo "[2/5] 엔진 host 입력..."
ENGINE_HOST="${1:-}"
if [ -z "$ENGINE_HOST" ]; then
  read -rp "  엔진 VM 의 IP 또는 hostname (예: 192.168.1.10): " ENGINE_HOST
fi
ENGINE_HOST="$(echo "$ENGINE_HOST" | tr -d '[:space:]')"
if [ -z "$ENGINE_HOST" ]; then
  echo "  오류: 엔진 host 입력 필수."
  usage
fi
echo "  ENGINE_HOST=$ENGINE_HOST"

INSTALL_URL="http://${ENGINE_HOST}:8000/zconverter.tar.gz"

# ─── 3. .env 생성 ─────────────────────────────────────────────────────
echo "[3/5] .env 생성..."
if [ -f .env ] && [ "${FORCE:-0}" != "1" ]; then
  echo "  .env 이미 존재 — INSTALL_BUNDLE_URL 만 갱신 (FORCE=1 로 전체 재생성)"
  if grep -q "^INSTALL_BUNDLE_URL=" .env; then
    sed_inplace "s|^INSTALL_BUNDLE_URL=.*|INSTALL_BUNDLE_URL=${INSTALL_URL}|" .env
  else
    echo "" >> .env
    echo "INSTALL_BUNDLE_URL=${INSTALL_URL}" >> .env
  fi
else
  cp .env.example .env
  if grep -q "^INSTALL_BUNDLE_URL=" .env; then
    sed_inplace "s|^INSTALL_BUNDLE_URL=.*|INSTALL_BUNDLE_URL=${INSTALL_URL}|" .env
  else
    echo "" >> .env
    echo "INSTALL_BUNDLE_URL=${INSTALL_URL}" >> .env
  fi
fi
echo "  INSTALL_BUNDLE_URL=${INSTALL_URL}"

# ─── 4. docker compose up ─────────────────────────────────────────────
echo "[4/5] Docker 서비스 기동 (build 포함)..."
docker compose up --build -d
echo "  컨테이너 기동 명령 발행 완료"

# ─── 5. healthcheck 대기 + 검증 ───────────────────────────────────────
echo "[5/5] web healthcheck 대기 (최대 180s)..."
secs=0
while [ $secs -lt 180 ]; do
  status="$(docker compose ps web --format '{{.Status}}' 2>/dev/null || true)"
  if echo "$status" | grep -q "healthy"; then
    echo "  web healthy ($secs s 경과)"
    break
  fi
  sleep 3
  secs=$((secs+3))
done
if [ $secs -ge 180 ]; then
  echo "  오류: 180s 안 healthy 안 됨. 'docker compose logs web' 확인."
  exit 1
fi

echo "  curl http://localhost:8000/health 검증..."
if curl -sf http://localhost:8000/health >/dev/null; then
  echo "  health 200 OK"
else
  echo "  오류: health 응답 실패. 'docker compose logs web --tail=30' 확인."
  exit 1
fi

# ─── 완료 ─────────────────────────────────────────────────────────────
echo ""
echo "환경 준비 완료"
echo "  Web UI       : http://${ENGINE_HOST}:8000/servers/"
echo "  RabbitMQ 콘솔 : http://${ENGINE_HOST}:15672"
echo "  pgAdmin      : 미가동 (필요 시 'docker compose --profile gui up -d pgadmin')"
echo ""
echo "agent 설치 (다른 VM):"
echo "  agent 측 RABBITMQ_HOST=${ENGINE_HOST}, INSTALL_BUNDLE_URL 은 task.install 메시지로 자동 전달."
echo "  agent.env 정합화 + agent 빌드는 ../assessment-agent 레포의 install 절차 참조."
