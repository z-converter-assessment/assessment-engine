#!/usr/bin/env bash
#
# bootstrap.sh — 배포 대상 VM 1회성 멱등 부트스트랩 (ADR 0048).
#
# deploy.yml(엔진 rollout)이 전제하는 VM 상태를 만든다:
#   (1) docker engine + compose plugin 설치
#   (2) 배포 디렉토리(DEPLOY_DIR) + secrets/ + .env 스캐폴딩
#   (3) GitHub Actions self-hosted runner 를 systemd 서비스로 등록
#
# 멱등 — 이미 된 단계는 건너뛴다. 여러 번 실행해도 안전.
# 대상 OS: Debian/Ubuntu (apt). 다른 distro 는 docker 설치 절만 대체.
#
# 사용:
#   sudo GITHUB_RUNNER_URL=https://github.com/<owner>/<repo> \
#        GITHUB_RUNNER_TOKEN=<repo Settings > Actions > Runners > New runner 토큰> \
#        ./bootstrap.sh
#
# 선택 env:
#   DEPLOY_DIR      배포 디렉토리 (기본 /opt/assessment-engine) — deploy.yml vars.DEPLOY_DIR 와 일치시킬 것
#   RUNNER_USER     runner·compose 실행 계정 (기본 assessment) — non-root, docker 그룹 소속
#   RUNNER_LABELS   runner 라벨 (기본 assessment-prod) — deploy.yml runs-on 라벨과 일치 필수
#   RUNNER_VERSION  actions runner 버전 (미지정 시 actions/runner 최신 릴리즈 자동 해석)

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
RUNNER_USER="${RUNNER_USER:-assessment}"
RUNNER_LABELS="${RUNNER_LABELS:-assessment-prod}"
RUNNER_VERSION="${RUNNER_VERSION:-}"   # 미지정 시 runner 설치 단계에서 actions/runner 최신 릴리즈로 자동 해석
RUNNER_DIR="/opt/actions-runner"

log() { printf '[bootstrap] %s\n' "$*"; }
die() { printf '[bootstrap][error] %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "root 로 실행 (sudo)"

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

# ─── (2) 배포 계정 + 디렉토리 + secret 스캐폴딩 ────────────────────────────
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  log "배포 계정 생성: $RUNNER_USER"
  useradd --system --create-home --shell /usr/sbin/nologin "$RUNNER_USER"
fi
# docker 그룹 소속 — compose 를 sudo 없이 실행.
usermod -aG docker "$RUNNER_USER"

log "배포 디렉토리 구성: $DEPLOY_DIR"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" "$DEPLOY_DIR" "$DEPLOY_DIR/secrets"
chmod 700 "$DEPLOY_DIR/secrets"

# .env 는 최초 1회만 생성 (이후 배포가 ENGINE_IMAGE 만 갱신 — 덮어쓰지 않음).
if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
  if [[ -f "$(dirname "$0")/env.example" ]]; then
    install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0640 "$(dirname "$0")/env.example" "$DEPLOY_DIR/.env"
    log ".env 생성 (env.example 복사) — POSTGRES_USER 등 운영값을 채울 것"
  else
    log "env.example 미발견 — $DEPLOY_DIR/.env 를 수동 배치할 것"
  fi
else
  log ".env 이미 존재 — 보존"
fi

# secret 파일 안내 (강 random 생성은 운영자 책임 — 여기서 자동 생성하지 않음).
cat <<EOF
[bootstrap] secret 파일을 아래처럼 배치할 것 (없으면 APP_ENV=prod 기동 거부):
  printf '%s' "\$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/postgres_password
  printf '%s' "\$(openssl rand -base64 32)" > $DEPLOY_DIR/secrets/rabbitmq_password
  chmod 600 $DEPLOY_DIR/secrets/*
  chown $RUNNER_USER:$RUNNER_USER $DEPLOY_DIR/secrets/*
EOF

# ─── (3) GitHub Actions self-hosted runner (systemd 서비스) ────────────────
if systemctl list-units --all --type=service | grep -q 'actions.runner'; then
  log "self-hosted runner 서비스 이미 등록됨 — skip (재등록은 기존 서비스 제거 후)"
else
  [[ -n "${GITHUB_RUNNER_URL:-}" ]]   || die "GITHUB_RUNNER_URL 미설정 (repo URL)"
  [[ -n "${GITHUB_RUNNER_TOKEN:-}" ]] || die "GITHUB_RUNNER_TOKEN 미설정 (repo Settings > Actions > Runners)"

  # runner 버전 — 미지정 시 actions/runner 최신 릴리즈 자동 해석 (하드코딩 drift 회피).
  if [[ -z "$RUNNER_VERSION" ]]; then
    command -v curl >/dev/null 2>&1 || apt-get install -y -qq curl
    RUNNER_VERSION="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
      | grep -oP '"tag_name":[[:space:]]*"v\K[^"]+')"
    [[ -n "$RUNNER_VERSION" ]] || die "actions/runner 최신 버전 조회 실패 — RUNNER_VERSION 명시"
  fi

  log "self-hosted runner 설치 (v${RUNNER_VERSION}, labels=${RUNNER_LABELS})"
  install -d -o "$RUNNER_USER" -g "$RUNNER_USER" "$RUNNER_DIR"
  ARCH="$(dpkg --print-architecture)"; case "$ARCH" in amd64) RARCH=x64;; arm64) RARCH=arm64;; *) die "unsupported arch $ARCH";; esac
  TARBALL="actions-runner-linux-${RARCH}-${RUNNER_VERSION}.tar.gz"
  if [[ ! -f "$RUNNER_DIR/config.sh" ]]; then
    curl -fsSL -o "/tmp/${TARBALL}" \
      "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"
    tar xzf "/tmp/${TARBALL}" -C "$RUNNER_DIR"
    chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"
    rm -f "/tmp/${TARBALL}"
  fi
  # 등록 — 비대화식. fork PR·비신뢰 브랜치가 self-hosted 에서 실행되지 않게 repo 설정에서
  # "Require approval for all outside collaborators" + 배포는 protected 브랜치/태그로 제한할 것.
  sudo -u "$RUNNER_USER" "$RUNNER_DIR/config.sh" \
    --url "$GITHUB_RUNNER_URL" \
    --token "$GITHUB_RUNNER_TOKEN" \
    --labels "$RUNNER_LABELS" \
    --unattended --replace
  # systemd 서비스로 상주 (outbound HTTPS 폴링).
  "$RUNNER_DIR/svc.sh" install "$RUNNER_USER"
  "$RUNNER_DIR/svc.sh" start
  log "runner 서비스 기동 완료"
fi

log "완료. 다음: (1) $DEPLOY_DIR/.env 운영값 (2) secrets/* 배치 (3) repo Environments 'production' 승인 규칙 설정 후 deploy.yml 실행"
