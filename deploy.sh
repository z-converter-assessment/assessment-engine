#!/usr/bin/env bash

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
REPO_ID="${REPO_ID:-z-converter-assessment/assessment-engine}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/${REPO_ID}}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO_ID}}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][error] %s\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/ #.*//;s/[[:space:]]*$//;s/^"\(.*\)"$/\1/;s/^'"'"'\(.*\)'"'"'$/\1/' || true; }

VERSION="${1:-}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "사용: deploy.sh vX.Y.Z (stable semver)"
IMAGE="${IMAGE_REPO}:${VERSION#v}"

command -v docker >/dev/null 2>&1 || die "docker 미설치 — bootstrap.sh 먼저"
command -v cosign >/dev/null 2>&1 || die "cosign 미설치 — bootstrap.sh 먼저"
command -v flock >/dev/null 2>&1 || die "flock 미설치 (util-linux)"
[[ -f "$DEPLOY_DIR/.env" ]] || die "$DEPLOY_DIR/.env 없음 — bootstrap.sh 먼저 + 운영값 채울 것"

cd "$DEPLOY_DIR"

exec 9>".deploy.lock"
# 이미지 핀과 rollback 파일을 함께 갱신하므로 동시 배포를 막는다.
flock -n 9 || die "다른 배포가 진행 중"

PORT="$(env_get WEB_PUBLISH_PORT)"; PORT="${PORT:-8000}"

services_healthy() {
  local cid state code restart health oneoff expected actual
  local -a services
  mapfile -t services < <(docker compose config --services | sort)
  [[ ${#services[@]} -gt 0 ]] || return 1
  expected="$(printf '%s\n' "${services[@]}")"
  actual="$(docker compose ps -a --format '{{.Service}}' "${services[@]}" | sort -u)"
  [[ "$expected" == "$actual" ]] || return 1
  for cid in $(docker compose ps -aq "${services[@]}"); do
    IFS='|' read -r state code restart health oneoff <<< "$(docker inspect --format \
      '{{.State.Status}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{index .Config.Labels "com.docker.compose.oneoff"}}' \
      "$cid")"
    [[ "$oneoff" == True ]] && continue
    if [[ "$restart" == no ]]; then
      [[ "$state" == exited && "$code" == 0 ]] || return 1
    else
      [[ "$state" == running ]] || return 1
      [[ -z "$health" || "$health" == healthy ]] || return 1
    fi
  done
  return 0
}

healthy_now() { services_healthy && curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; }

rollback() {
  # 스키마는 되돌리지 않으므로 마이그레이션은 backward compatible이어야 한다.
  log "$1 — rollback 시도"
  docker compose ps -a --format '  {{.Service}} {{.State}} {{.Health}}' || true
  log "주의: 스키마는 되돌리지 않는다 — 마이그레이션이 backward compatible 이어야 성립한다"
  [[ -s .last-good ]] || die "$1 + .last-good 없음(최초 배포) — 수동 조치 필요"
  local last; last="$(cat .last-good)"
  log "rollback -> $last"
  sed -i "s#^ENGINE_IMAGE=.*#ENGINE_IMAGE=${last}#" .env
  if [[ -f .last-good-docker-compose.yml && -f .last-good-docker-compose.prod.yml ]]; then
    cp .last-good-docker-compose.yml docker-compose.yml
    cp .last-good-docker-compose.prod.yml docker-compose.prod.yml
    log "compose 도 직전 버전으로 복원"
  else
    log "직전 compose 사본 없음 — 이미지만 되돌린다(토폴로지가 바뀐 릴리즈면 기동이 깨질 수 있다)"
  fi
  docker compose up -d || die "rollback($last) 기동 실패 — 수동 조치 필요"
  for _ in $(seq 1 "$HEALTH_RETRIES"); do
    healthy_now && die "새 버전 $VERSION 배포 실패 — $last 로 rollback 완료. 원인 확인 후 재시도"
    sleep "$HEALTH_INTERVAL"
  done
  die "rollback($last) 후에도 health 실패 — 수동 조치 필요"
}

log "cosign verify $IMAGE"
cosign verify "$IMAGE" \
  --certificate-identity-regexp="^https://github.com/${REPO_ID}/.github/workflows/release.yml@refs/(heads/main|tags/v)" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" >/dev/null \
  || die "이미지 서명 검증 실패 — $IMAGE (release.yml 서명본 아님?)"

log "compose fetch ($VERSION)"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.yml" -o .compose-base.new \
  || die "docker-compose.yml 을 받지 못했다 — $VERSION 태그에 그 파일이 있는지 확인"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.prod.yml" -o .compose-prod.new \
  || { rm -f .compose-base.new .compose-prod.new; die "docker-compose.prod.yml 을 받지 못했다 — $VERSION 태그에 그 파일이 있는지 확인"; }
for f in docker-compose.yml docker-compose.prod.yml; do
  [[ -f "$f" ]] && cp "$f" ".last-good-$f"
done
mv .compose-base.new docker-compose.yml
mv .compose-prod.new docker-compose.prod.yml

CURRENT="$(env_get ENGINE_IMAGE)"
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
docker compose pull || rollback "이미지 pull 실패"
docker compose up -d || rollback "컨테이너 기동 실패"

for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if healthy_now; then
    log "health OK — $VERSION 배포 완료"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep "$HEALTH_INTERVAL"
done

rollback "health 실패"
