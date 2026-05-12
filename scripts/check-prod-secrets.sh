#!/usr/bin/env bash
# Prod 기동 전 호스트 측 secrets/ 파일 검증 (defense in depth — config.py model_validator의 보완 layer).
#
# 검증 항목:
#   1. 필수 파일 존재 (postgres_password, rabbitmq_password)
#   2. world/group readable 권한 차단 (mode `04xx` other +r 또는 `0x4x` group +r → reject)
#   3. git untracked (실수 commit 방지 — secrets/.gitignore가 `*`로 차단하지만 한 번 더 확인)
#   4. 최소 길이 32바이트 (`openssl rand -base64 32` 정도 — 약한 password fail-fast)
#
# Docker secrets가 컨테이너 안에 mount될 때는 Docker가 0444 read-only로 보장하지만, 호스트 측 원본 파일은
# 운영자 책임이다. 본 스크립트가 그 책임을 자동화한다.
#
# 사용:
#   ./scripts/check-prod-secrets.sh && \
#     docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

set -euo pipefail

readonly SECRETS_DIR="$(dirname "$0")/../secrets"
readonly REQUIRED_SECRETS=("postgres_password" "rabbitmq_password")
readonly MIN_LENGTH=32

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

warn() {
  echo "WARN: $1" >&2
}

# `stat`은 macOS(BSD)와 Linux(GNU)가 다른 syntax — 둘 다 처리.
# 출력은 권한 8진수 3자리(`644`/`400` 등).
file_mode() {
  local f="$1"
  if stat -f '%Lp' "$f" >/dev/null 2>&1; then
    stat -f '%Lp' "$f"          # macOS / BSD
  else
    stat -c '%a' "$f"           # Linux / GNU
  fi
}

for name in "${REQUIRED_SECRETS[@]}"; do
  path="${SECRETS_DIR}/${name}"

  # 1. 존재
  [ -f "$path" ] || fail "missing secret file: ${path}"

  # 2. 권한 — group/other read 금지. owner read+write만 허용 (0400/0600).
  mode="$(file_mode "$path")"
  case "$mode" in
    400|600) ;;   # OK
    *)
      fail "secret ${name} mode=${mode} (must be 0400 or 0600 — group/other readable is unsafe). " \
           "Fix: chmod 0400 ${path}"
      ;;
  esac

  # 3. git tracking — secrets/.gitignore가 막아주지만 누군가 -f로 강제 add한 경우 잡힌다.
  if git -C "$(dirname "$path")/.." ls-files --error-unmatch "secrets/${name}" >/dev/null 2>&1; then
    fail "secret ${name} is tracked by git. Fix: git rm --cached secrets/${name} && verify secrets/.gitignore"
  fi

  # 4. 최소 길이 — strong random 강제. `openssl rand -base64 32`는 44 char 출력이라 32 미만이면 약함.
  length="$(wc -c < "$path" | tr -d ' ')"
  if [ "$length" -lt "$MIN_LENGTH" ]; then
    fail "secret ${name} length=${length} bytes (minimum ${MIN_LENGTH}). " \
         "Fix: printf '%s' \"\$(openssl rand -base64 32)\" > ${path} && chmod 0400 ${path}"
  fi

  echo "OK: ${name} (mode=${mode}, length=${length})"
done

echo ""
echo "모든 prod secret 검증 통과. 이제 prod compose 기동 가능."
