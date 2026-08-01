#!/usr/bin/env bash
#
# bootstrap.sh — 배포 대상 VM 1회성 부트스트랩 (ADR 0048). deploy.sh 가 전제하는 VM 상태를 만든다.
# 멱등이라 다시 돌려도 되고, 대상 OS 는 Debian/Ubuntu(apt) 다.
#
#   curl -fsSL https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main/bootstrap.sh -o bootstrap.sh
#   sudo bash bootstrap.sh

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
RAW_MAIN="https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main"
ENV_TEMPLATE_URL="${ENV_TEMPLATE_URL:-${RAW_MAIN}/.env.example}"
DEPLOY_SCRIPT_URL="${DEPLOY_SCRIPT_URL:-${RAW_MAIN}/deploy.sh}"
ROTATE_SCRIPT_URL="${ROTATE_SCRIPT_URL:-${RAW_MAIN}/rotate-secret.sh}"
PROD_COMPOSE_URL="${PROD_COMPOSE_URL:-${RAW_MAIN}/docker-compose.prod.yml}"
# 핀한 cosign 과 그 릴리즈 바이너리 sha256 — 셋을 함께 갱신한다. 여기서는 cosign 이 아직 없어 서명으로
# 못 검증하고, 체크섬 파일도 바이너리와 같은 출처라 서버가 털리면 함께 위조된다. 그래서 값을 여기 고정한다.
COSIGN_PINNED_VERSION="v3.1.2"
COSIGN_SHA256_AMD64="f7622ed3cf22e55e1ae6377c080979ff77a22da9981c11df222a2e444991e7cf"
COSIGN_SHA256_ARM64="90e7ae0b5dfd60f20816b52c012addf7fc055ebcc7bea4ce81c428ca8518c302"
COSIGN_VERSION="${COSIGN_VERSION:-$COSIGN_PINNED_VERSION}"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap][error] %s\n' "$*" >&2; exit 1; }

# 임시 파일로 받아 성공 시에만 옮긴다 — 잘린 파일이 남으면 멱등 재실행이 그것을 보존한다.
# 5번째 인자로 sha256 을 주면 옮기기 전에 대조한다.
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

# curl 은 스크립트·템플릿 다운로드, openssl 은 secret 값 생성에 쓴다.
command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
command -v openssl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq openssl; }

# --- (1) docker engine + compose plugin ---
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "docker engine + compose plugin 이미 설치됨 — skip"
else
  log "docker engine + compose plugin 설치 (Docker 공식 apt repo)"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  . /etc/os-release
  # 키와 repo 를 같은 배포판 경로에서 가져온다 — 지금은 Docker 가 키를 공유하지만 갈리면 서명 검증이 깨진다.
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    fetch "https://download.docker.com/linux/${ID}/gpg" /etc/apt/keyrings/docker.asc 0644 "docker apt 서명 키"
  fi
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# --- (1b) cosign (deploy.sh 의 공급망 게이트) ---
if command -v cosign >/dev/null 2>&1; then
  log "cosign 이미 설치됨 — skip"
else
  case "$(dpkg --print-architecture)" in
    amd64) CARCH=amd64; COSIGN_SHA="$COSIGN_SHA256_AMD64" ;;
    arm64) CARCH=arm64; COSIGN_SHA="$COSIGN_SHA256_ARM64" ;;
    *) die "unsupported arch $(dpkg --print-architecture)" ;;
  esac
  # 버전을 override 하면 위 체크섬과 짝이 안 맞으므로 대조를 건너뛴다 — 그때는 호출자가 무결성을 책임진다.
  if [[ "$COSIGN_VERSION" != "$COSIGN_PINNED_VERSION" ]]; then
    COSIGN_SHA=""
    log "cosign $COSIGN_VERSION — 핀한 $COSIGN_PINNED_VERSION 이 아니라 체크섬 대조를 건너뛴다"
  fi
  COSIGN_URL="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${CARCH}"
  log "cosign 설치 ($COSIGN_URL)"
  fetch "$COSIGN_URL" /usr/local/bin/cosign 0755 "cosign 바이너리" "$COSIGN_SHA"
fi

# --- (2) 배포 디렉토리 + secret 스캐폴딩 + .env 템플릿 ---
log "배포 디렉토리 구성: $DEPLOY_DIR"
install -d -m 0755 "$DEPLOY_DIR"
install -d -m 0700 "$DEPLOY_DIR/secrets"

# .env 는 최초 1회만 생성한다 — 이후엔 deploy.sh 가 ENGINE_IMAGE 줄만 갱신한다.
if [[ -f "$DEPLOY_DIR/.env" ]]; then
  log ".env 이미 존재 — 보존"
else
  fetch "$ENV_TEMPLATE_URL" "$DEPLOY_DIR/.env" 0640 ".env 템플릿"
  log ".env 생성 — POSTGRES_USER 등 운영값을 채울 것"
fi

# secret 목록은 prod compose 의 secrets: 가 정하므로 받아서 읽는다 — 여기 열거하면 이중 관리다.
# fetch 와 parse 를 나눈 것은 파이프로 묶으면 pipefail 이 die 에 닿기 전에 죽이기 때문이다.
PROD_COMPOSE_YAML="$(curl -fsSL "$PROD_COMPOSE_URL")" \
  || die "prod compose 를 받지 못했다 — $PROD_COMPOSE_URL"
SECRET_KEYS="$(printf '%s\n' "$PROD_COMPOSE_YAML" |
  awk '/^secrets:/{inblock=1; next} inblock && /^[^[:space:]]/{inblock=0} inblock && /^  [A-Za-z_][A-Za-z0-9_]*:/{sub(/:.*/,""); gsub(/ /,""); print}')"
[[ -n "$SECRET_KEYS" ]] || die "prod compose 에서 secrets: 항목을 찾지 못했다 — $PROD_COMPOSE_URL"

# 없는 것만 만든다 — 기동 중인 DB 의 비번을 덮으면 접속이 끊긴다.
# 644 는 postgres 이미지가 non-root 로 읽기 때문이고, 경계는 secrets/ 의 0700 이 맡는다.
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

# --- (3) 운영 스크립트 배치 ---
for pair in "deploy.sh:$DEPLOY_SCRIPT_URL" "rotate-secret.sh:$ROTATE_SCRIPT_URL"; do
  name="${pair%%:*}"; url="${pair#*:}"
  fetch "$url" "$DEPLOY_DIR/$name" 0755 "$name"
  log "$name 배치: $DEPLOY_DIR/$name"
done

log "완료. 다음: (1) $DEPLOY_DIR/.env 운영값 채우기 (2) sudo $DEPLOY_DIR/deploy.sh vX.Y.Z"
