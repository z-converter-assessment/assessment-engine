#!/usr/bin/env bash

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
RAW_MAIN="https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main"
ENV_TEMPLATE_URL="${ENV_TEMPLATE_URL:-${RAW_MAIN}/.env.example}"
DEPLOY_SCRIPT_URL="${DEPLOY_SCRIPT_URL:-${RAW_MAIN}/deploy.sh}"
ROTATE_SCRIPT_URL="${ROTATE_SCRIPT_URL:-${RAW_MAIN}/rotate-secret.sh}"
PROD_COMPOSE_URL="${PROD_COMPOSE_URL:-${RAW_MAIN}/docker-compose.prod.yml}"
COSIGN_PINNED_VERSION="v3.1.2"
COSIGN_SHA256_AMD64="f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf"
COSIGN_SHA256_ARM64="90e7ae0b5dfd60f20816b52c012addf7fc055ebcc7bea4ce81c428ca8518c302"
COSIGN_VERSION="${COSIGN_VERSION:-$COSIGN_PINNED_VERSION}"

# cosign을 설치하기 전에는 서명 검증을 할 수 없어 배포 스크립트에 고정한 체크섬을 검증한다.

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap][error] %s\n' "$*" >&2; exit 1; }

fetch() {
  local url="$1" dest="$2" mode="$3" what="$4" sha="${5:-}" tmp="$2.download"
  curl -fsSL "$url" -o "$tmp" || { rm -f "$tmp"; die "받지 못했다: $what — $url"; }
  if [[ -n "$sha" ]]; then
    printf '%s  %s\n' "$sha" "$tmp" | sha256sum -c --status \
      || { rm -f "$tmp"; die "체크섬 불일치: $what — $url"; }
  fi
  chmod "$mode" "$tmp"
  mv "$tmp" "$dest"
}

[[ "$(id -u)" -eq 0 ]] || die "root 로 실행 (sudo)"

command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
command -v openssl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq openssl; }

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "docker engine + compose plugin 이미 설치됨 — skip"
else
  log "docker engine + compose plugin 설치 (Docker 공식 apt repo)"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  . /etc/os-release
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    fetch "https://download.docker.com/linux/${ID}/gpg" /etc/apt/keyrings/docker.asc 0644 "docker apt 서명 키"
  fi
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

if command -v cosign >/dev/null 2>&1; then
  log "cosign 이미 설치됨 — skip"
else
  case "$(dpkg --print-architecture)" in
    amd64) CARCH=amd64; COSIGN_SHA="$COSIGN_SHA256_AMD64" ;;
    arm64) CARCH=arm64; COSIGN_SHA="$COSIGN_SHA256_ARM64" ;;
    *) die "unsupported arch $(dpkg --print-architecture)" ;;
  esac
  if [[ "$COSIGN_VERSION" != "$COSIGN_PINNED_VERSION" ]]; then
    COSIGN_SHA=""
    log "cosign $COSIGN_VERSION — 핀한 $COSIGN_PINNED_VERSION 이 아니라 체크섬 대조를 건너뛴다"
  fi
  COSIGN_URL="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${CARCH}"
  log "cosign 설치 ($COSIGN_URL)"
  fetch "$COSIGN_URL" /usr/local/bin/cosign 0755 "cosign 바이너리" "$COSIGN_SHA"
fi

log "배포 디렉토리 구성: $DEPLOY_DIR"
install -d -m 0755 "$DEPLOY_DIR"
install -d -m 0700 "$DEPLOY_DIR/secrets"

if [[ -f "$DEPLOY_DIR/.env" ]]; then
  log ".env 이미 존재 — 보존"
else
  fetch "$ENV_TEMPLATE_URL" "$DEPLOY_DIR/.env" 0640 ".env 템플릿"
  log ".env 생성 — POSTGRES_USER 등 운영값을 채울 것"
fi

PROD_COMPOSE_YAML="$(curl -fsSL "$PROD_COMPOSE_URL")" \
  || die "prod compose 를 받지 못했다 — $PROD_COMPOSE_URL"
SECRET_KEYS="$(printf '%s\n' "$PROD_COMPOSE_YAML" |
  awk '/^secrets:/{inblock=1; next} inblock && /^[^[:space:]]/{inblock=0} inblock && /^  [A-Za-z_][A-Za-z0-9_]*:/{sub(/:.*/,""); gsub(/ /,""); print}')"
[[ -n "$SECRET_KEYS" ]] || die "prod compose 에서 secrets: 항목을 찾지 못했다 — $PROD_COMPOSE_URL"

while read -r key; do
  secret_file="$DEPLOY_DIR/secrets/$key"
  if [[ -f "$secret_file" ]]; then
    log "secret $key — 이미 존재, 보존"
  else
    printf '%s' "$(openssl rand -base64 32)" > "$secret_file"
    chmod 644 "$secret_file"
    log "secret $key — 생성"
  fi
done <<< "$SECRET_KEYS"

for pair in "deploy.sh:$DEPLOY_SCRIPT_URL" "rotate-secret.sh:$ROTATE_SCRIPT_URL"; do
  name="${pair%%:*}"; url="${pair#*:}"
  fetch "$url" "$DEPLOY_DIR/$name" 0755 "$name"
  log "$name 배치: $DEPLOY_DIR/$name"
done

log "완료. 다음: (1) $DEPLOY_DIR/.env 운영값 채우기 (2) sudo $DEPLOY_DIR/deploy.sh vX.Y.Z"
