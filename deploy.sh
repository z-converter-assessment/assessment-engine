#!/usr/bin/env bash
#
# deploy.sh — 엔진 rollout. 배포 대상 VM 에서 사람이 실행한다 (ADR 0048).
#
#   sudo /opt/assessment-engine/deploy.sh vX.Y.Z
#
# 전제(docker·cosign·.env·본 스크립트)는 bootstrap.sh 가 만든다.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
REPO_ID="${REPO_ID:-z-converter-assessment/assessment-engine}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/${REPO_ID}}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO_ID}}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][error] %s\n' "$*" >&2; exit 1; }

# compose 는 값의 인라인 주석을 잘라내고 읽는다 — 같은 값을 보려면 여기서도 잘라야 한다.
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/ #.*//;s/[[:space:]]*$//' || true; }

VERSION="${1:-}"
[[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "사용: deploy.sh vX.Y.Z (stable semver)"
IMAGE="${IMAGE_REPO}:${VERSION#v}"

command -v docker >/dev/null 2>&1 || die "docker 미설치 — bootstrap.sh 먼저"
command -v cosign >/dev/null 2>&1 || die "cosign 미설치 — bootstrap.sh 먼저"
command -v flock >/dev/null 2>&1 || die "flock 미설치 (util-linux)"
[[ -f "$DEPLOY_DIR/.env" ]] || die "$DEPLOY_DIR/.env 없음 — bootstrap.sh 먼저 + 운영값 채울 것"

cd "$DEPLOY_DIR"

# 두 배포가 겹치면 .env 의 핀과 .last-good 이 서로를 덮어 rollback 대상이 뒤섞인다.
exec 9>".deploy.lock"
flock -n 9 || die "다른 배포가 진행 중"

# 포트 매핑이 어긋나면 컨테이너만 healthy 라 호스트에서도 응답을 확인한다.
PORT="$(env_get WEB_PUBLISH_PORT)"; PORT="${PORT:-8000}"

# 판정 기준은 restart 정책 — 재시작 안 하겠다고 선언한 서비스(migrate)만 exited 0 이 완료고,
# 나머지는 running 이어야 한다. 서비스 목록은 compose 에서 읽어 여기 열거하지 않는다.
services_healthy() {
  local cid state code restart health oneoff expected actual
  local -a services
  mapfile -t services < <(docker compose config --services | sort)
  [[ ${#services[@]} -gt 0 ]] || return 1
  # 잔재 하나가 이후 모든 배포를 막지 않도록 orphan(서비스 인자)과 oneoff(아래 라벨)를 거른다.
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

# 중간에 조용히 죽으면 .env 만 새 핀이라 다음 배포가 실패본을 .last-good 으로 기록한다.
rollback() {
  log "$1 — rollback 시도"
  docker compose ps -a --format '  {{.Service}} {{.State}} {{.Health}}' || true
  # 되돌아가는 것은 이미지뿐 — migrate 가 적용한 스키마는 남는다.
  log "주의: 스키마는 되돌리지 않는다 — 마이그레이션이 backward compatible 이어야 성립한다"
  [[ -s .last-good ]] || die "$1 + .last-good 없음(최초 배포) — 수동 조치 필요"
  local last; last="$(cat .last-good)"
  log "rollback -> $last"
  sed -i "s#^ENGINE_IMAGE=.*#ENGINE_IMAGE=${last}#" .env
  docker compose up -d || die "rollback($last) 기동 실패 — 수동 조치 필요"
  for _ in $(seq 1 "$HEALTH_RETRIES"); do
    healthy_now && die "새 버전 $VERSION 배포 실패 — $last 로 rollback 완료. 원인 확인 후 재시도"
    sleep "$HEALTH_INTERVAL"
  done
  die "rollback($last) 후에도 health 실패 — 수동 조치 필요"
}

# 이미지가 이 repo 의 release.yml 산출물인지 서명 신원으로 확인한다.
log "cosign verify $IMAGE"
cosign verify "$IMAGE" \
  --certificate-identity-regexp="^https://github.com/${REPO_ID}/.github/workflows/release.yml@refs/(heads/main|tags/v)" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" >/dev/null \
  || die "이미지 서명 검증 실패 — $IMAGE (release.yml 서명본 아님?)"

# 이미지와 같은 태그에서 받아 토폴로지 버전을 맞춘다. 하나만 덮고 실패하면 짝이 어긋난다.
log "compose fetch ($VERSION)"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.yml" -o .compose-base.new \
  || die "docker-compose.yml 을 받지 못했다 — $VERSION 태그에 그 파일이 있는지 확인"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.prod.yml" -o .compose-prod.new \
  || { rm -f .compose-base.new .compose-prod.new; die "docker-compose.prod.yml 을 받지 못했다 — $VERSION 태그에 그 파일이 있는지 확인"; }
mv .compose-base.new docker-compose.yml
mv .compose-prod.new docker-compose.prod.yml

# 새 핀을 쓰기 전에 현재 핀을 남긴다 — rollback 대상.
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
