#!/usr/bin/env bash
#
# bootstrap.sh — 배포 대상 VM 1회성 부트스트랩 (ADR 0048). deploy.sh 가 전제하는 VM 상태를 만든다.
#
# 사용 (public repo — clone 없이 raw 에서 받아 실행):
#   curl -fsSL https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main/bootstrap.sh -o bootstrap.sh
#   sudo bash bootstrap.sh
#
# 멱등 — 이미 끝난 단계는 건너뛰므로 deploy.sh 를 갱신할 때 다시 돌려도 된다.
# 대상 OS 는 Debian/Ubuntu(apt). 다른 distro 는 docker 설치 절만 대체한다.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
RAW_MAIN="https://raw.githubusercontent.com/z-converter-assessment/assessment-engine/main"
ENV_TEMPLATE_URL="${ENV_TEMPLATE_URL:-${RAW_MAIN}/env.example}"
DEPLOY_SCRIPT_URL="${DEPLOY_SCRIPT_URL:-${RAW_MAIN}/deploy.sh}"
SECRETS_COMPOSE_URL="${SECRETS_COMPOSE_URL:-${RAW_MAIN}/docker-compose.secrets.yml}"
COSIGN_VERSION="${COSIGN_VERSION:-latest}"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap][error] %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "root 로 실행 (sudo)"

# curl — 이미지·스크립트 다운로드에 사용. 없으면 설치.
command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }

# ─── (1) docker engine + compose plugin ───────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  log "docker engine + compose plugin 이미 설치됨 — skip"
else
  log "docker engine + compose plugin 설치 (Docker 공식 apt repo)"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
  fi
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# ─── (1b) cosign (deploy.sh 의 공급망 게이트) ──────────────────────────────
if command -v cosign >/dev/null 2>&1; then
  log "cosign 이미 설치됨 — skip"
else
  ARCH="$(dpkg --print-architecture)"; case "$ARCH" in amd64) CARCH=amd64;; arm64) CARCH=arm64;; *) die "unsupported arch $ARCH";; esac
  if [[ "$COSIGN_VERSION" == "latest" ]]; then
    COSIGN_URL="https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${CARCH}"
  else
    COSIGN_URL="https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${CARCH}"
  fi
  log "cosign 설치 ($COSIGN_URL)"
  curl -fsSL "$COSIGN_URL" -o /usr/local/bin/cosign
  chmod 0755 /usr/local/bin/cosign
fi

# ─── (2) 배포 디렉토리 + secret 스캐폴딩 + .env 템플릿 ─────────────────────
log "배포 디렉토리 구성: $DEPLOY_DIR"
install -d -m 0755 "$DEPLOY_DIR"
install -d -m 0700 "$DEPLOY_DIR/secrets"

# .env 는 최초 1회만 생성 (이후 deploy.sh 가 ENGINE_IMAGE 만 갱신 — 덮어쓰지 않음).
# env.example 템플릿을 raw 에서 받아 배치 (public repo — 토큰 불요). 운영자가 값 채움.
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
  if curl -fsSL "$ENV_TEMPLATE_URL" -o "$DEPLOY_DIR/.env"; then
    chmod 0640 "$DEPLOY_DIR/.env"
    log ".env 생성 (env.example 템플릿) — POSTGRES_USER 등 운영값을 채울 것"
  else
    log "env.example 다운로드 실패 — $DEPLOY_DIR/.env 를 수동 배치할 것"
  fi
else
  log ".env 이미 존재 — 보존"
fi

# 값 생성은 운영자 책임 — 여기서 만들면 부트스트랩 로그에 비밀이 남는다.
# 파일 목록은 docker-compose.secrets.yml 의 secrets: 항목이 정하므로 받아서 읽는다. 여기 열거하면
# secret 이 늘 때마다 본 스크립트도 고쳐야 한다.
SECRET_KEYS="$(curl -fsSL "$SECRETS_COMPOSE_URL" 2>/dev/null |
  awk '/^secrets:/{inblock=1; next} inblock && /^[^[:space:]]/{inblock=0} inblock && /^  [A-Za-z_][A-Za-z0-9_]*:/{sub(/:.*/,""); gsub(/ /,""); print}')"

log "secret 파일을 배치할 것 (없으면 APP_ENV=prod 기동 거부):"
if [[ -n "$SECRET_KEYS" ]]; then
  while read -r key; do
    # shellcheck disable=SC2016  # 운영자가 복사해 실행할 명령문이라 여기서 전개되면 안 된다.
    printf '  printf %%s "$(openssl rand -base64 32)" > %s/secrets/%s\n' "$DEPLOY_DIR" "$key"
  done <<< "$SECRET_KEYS"
  printf '  chmod 644 %s/secrets/*\n' "$DEPLOY_DIR"
else
  printf '  %s 의 secrets: 항목마다 같은 이름의 파일을 만들 것 (목록 조회 실패)\n' "$SECRETS_COMPOSE_URL"
fi

# ─── (3) deploy.sh 배치 (raw 에서 받아 배치) ───────────────────────────────
log "deploy.sh 배치: $DEPLOY_DIR/deploy.sh"
curl -fsSL "$DEPLOY_SCRIPT_URL" -o "$DEPLOY_DIR/deploy.sh"
chmod 0755 "$DEPLOY_DIR/deploy.sh"

log "완료. 다음: (1) $DEPLOY_DIR/.env 운영값 (2) secrets/* 배치 (3) sudo $DEPLOY_DIR/deploy.sh vX.Y.Z"
