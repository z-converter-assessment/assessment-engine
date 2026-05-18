#!/usr/bin/env bash
# pipeline-down.sh: Lima VM 전부 stop+delete + docker compose down -v.
# LIMA_VMS는 pipeline-up.sh와 단일 진실 — source guard로 함수만 가져옴 (main 자동 실행 안 함).
set -euo pipefail

# 호출 위치 무관 정합 — SCRIPT_DIR 절대 경로 확정 후 cwd 고정 + source 절대화.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# shellcheck disable=SC1091
source "$SCRIPT_DIR/pipeline-up.sh"

echo "[1/2] Lima VM 제거 중 (${#LIMA_VMS[@]} VM)..."
if command -v limactl >/dev/null 2>&1; then
  for vm in "${LIMA_VMS[@]}"; do
    if limactl list -q 2>/dev/null | grep -qx "$vm"; then
      echo "  $vm stop -f + delete -f..."
      limactl stop -f "$vm" 2>/dev/null || true
      limactl delete -f "$vm" 2>&1 | tail -1
    else
      echo "  $vm 존재하지 않음 — 건너뜀"
    fi
  done
  # LIMA_VMS 외 본 프로젝트 명명 패턴 잔재 — 알림만 (자동 삭제 안 함, 다른 프로젝트 인스턴스 보호).
  remaining=$(limactl list -q 2>/dev/null | grep -E "(cache|app|web|db|legacy-mq|monitor|offline|container)-server-01" | grep -vxF -f <(printf '%s\n' "${LIMA_VMS[@]}") || true)
  if [ -n "$remaining" ]; then
    echo "  주의: LIMA_VMS 외 프로젝트 명명 패턴 잔재 발견 — 수동 정리 검토:"
    echo "$remaining"
  fi
else
  echo "  limactl 미설치 — 건너뜀"
fi

echo "[2/2] Docker 서비스 및 볼륨 제거 중..."
docker compose --profile gui down -v

echo ""
echo "환경 종료 완료 (Lima ${#LIMA_VMS[@]} VM + Docker 컨테이너·볼륨 제거)"
