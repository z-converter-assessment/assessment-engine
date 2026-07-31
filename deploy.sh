#!/usr/bin/env bash
#
# deploy.sh — 엔진 rollout (배포 대상 VM 에서 실행). ADR 0048.
#
# 사용:
#   sudo /opt/assessment-engine/deploy.sh vX.Y.Z
#
# 흐름: cosign verify -> 버전 태그 compose fetch -> compose pull -> up
#   (migration 은 base compose 의 migrate init-container 가 web/consumer 기동 전 실행)
#   -> /health gate -> 실패 시 직전 정상 이미지로 rollback (capture-before-swap).
#
# 내부망 outbound-only VM 에서 사람이 실행 = 배포 게이트. public 이미지 pull (토큰 불요).
# 이미지 무결성은 privacy 가 아니라 cosign 서명이 보장. bootstrap.sh 가 docker·cosign·.env·본 스크립트를 배치.
#
# 선택 env: DEPLOY_DIR / IMAGE_REPO / RAW_BASE / REPO_ID (기본값은 아래).

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
REPO_ID="${REPO_ID:-z-converter-assessment/assessment-engine}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/${REPO_ID}}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO_ID}}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][error] %s\n' "$*" >&2; exit 1; }

VERSION="${1:-}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "사용: deploy.sh vX.Y.Z (stable semver)"
IMAGE="${IMAGE_REPO}:${VERSION#v}"

command -v docker >/dev/null 2>&1 || die "docker 미설치 — bootstrap.sh 먼저"
command -v cosign >/dev/null 2>&1 || die "cosign 미설치 — bootstrap.sh 먼저"
[[ -f "$DEPLOY_DIR/.env" ]] || die "$DEPLOY_DIR/.env 없음 — bootstrap.sh 먼저 + 운영값 채울 것"

cd "$DEPLOY_DIR"

# 공급망 게이트 — 이미지가 이 repo release.yml(GitHub OIDC)에서 서명됐는지 검증. 미통과 시 중단.
log "cosign verify $IMAGE"
cosign verify "$IMAGE" \
  --certificate-identity-regexp="^https://github.com/${REPO_ID}/.github/workflows/release.yml@refs/(heads/main|tags/v)" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" >/dev/null \
  || die "이미지 서명 검증 실패 — $IMAGE (release.yml 서명본 아님?)"

# 버전 태그의 compose 를 raw 에서 fetch — 이미지와 compose 토폴로지 버전 일치 (public repo, 토큰 불요).
log "compose fetch ($VERSION)"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.yml"         -o docker-compose.yml
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.secrets.yml" -o docker-compose.secrets.yml

# capture-before-swap — 현재 ENGINE_IMAGE(직전 정상)를 .last-good 으로 보존 후 새 버전 핀.
CURRENT="$(grep -E '^ENGINE_IMAGE=' .env | tail -1 | cut -d= -f2- || true)"
if [[ -n "$CURRENT" ]]; then
  printf '%s' "$CURRENT" > .last-good
  log "previous(last-good): $CURRENT"
else
  log "ENGINE_IMAGE 미핀(최초 배포) — rollback 불가"
fi
if grep -qE '^ENGINE_IMAGE=' .env; then
  sed -i "s#^ENGINE_IMAGE=.*#ENGINE_IMAGE=${IMAGE}#" .env
else
  printf '\nENGINE_IMAGE=%s\n' "$IMAGE" >> .env
fi

log "compose pull + up ($IMAGE)"
docker compose pull
docker compose up -d

# health gate — web /health 200 확인 (재시도). migrate init-container + 컨테이너 기동 대기.
PORT="$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2- || true)"; PORT="${PORT:-8000}"
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    log "health OK — $VERSION 배포 완료"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 5
done

# rollback — .last-good 이미지로 되돌려 재기동.
log "health 실패 — rollback 시도"
[[ -s .last-good ]] || die "health 실패 + .last-good 없음(최초 배포) — 수동 조치 필요"
LAST="$(cat .last-good)"
log "rollback -> $LAST"
sed -i "s#^ENGINE_IMAGE=.*#ENGINE_IMAGE=${LAST}#" .env
docker compose up -d
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    die "새 버전 $VERSION 배포 실패 — $LAST 로 rollback 완료. 원인 확인 후 재시도"
  fi
  sleep 5
done
die "rollback($LAST) 후에도 health 실패 — 수동 조치 필요"
