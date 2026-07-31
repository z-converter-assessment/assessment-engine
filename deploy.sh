#!/usr/bin/env bash
#
# deploy.sh — 엔진 rollout. 배포 대상 VM 에서 사람이 실행한다 (ADR 0048).
#
#   sudo /opt/assessment-engine/deploy.sh vX.Y.Z
#
# 내부망 VM 은 outbound 만 열려 있어 밖에서 밀어넣을 수 없다. 사람이 실행하는 것 자체가 배포 게이트다.
# 저장소와 이미지가 public 이라 토큰이 필요 없고, 무결성은 접근 제한이 아니라 cosign 서명이 보장한다.
# bootstrap.sh 가 docker·cosign·.env·본 스크립트를 배치한다.

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
REPO_ID="${REPO_ID:-z-converter-assessment/assessment-engine}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/${REPO_ID}}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO_ID}}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][error] %s\n' "$*" >&2; exit 1; }

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

# 태그는 옮겨갈 수 있지만 서명 신원은 위조되지 않는다 — 이미지가 이 repo 의 release.yml 산출물인지 본다.
log "cosign verify $IMAGE"
cosign verify "$IMAGE" \
  --certificate-identity-regexp="^https://github.com/${REPO_ID}/.github/workflows/release.yml@refs/(heads/main|tags/v)" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" >/dev/null \
  || die "이미지 서명 검증 실패 — $IMAGE (release.yml 서명본 아님?)"

# 이미지와 같은 태그의 compose 를 받아 토폴로지 버전을 맞춘다.
log "compose fetch ($VERSION)"
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "${RAW_BASE}/${VERSION}/docker-compose.prod.yml" -o docker-compose.prod.yml

# 새 핀을 쓰기 전에 현재 핀을 남긴다 — 실패했을 때 되돌아갈 곳이 필요하다.
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

# 무엇이 떠 있어야 하는지는 compose 가 정한다 — 여기서 서비스 이름을 열거하면 compose 가 바뀔 때마다
# 본 파일도 따라 고쳐야 한다. 종료가 정상인지는 restart 정책으로 가른다: compose 가 재시작하지 않겠다고
# 선언한 서비스(migration 같은 init-container)만 exited 0 이 완료를 뜻하고, 나머지는 떠 있어야 한다.
services_healthy() {
  local cid state code restart health expected actual
  local -a services
  mapfile -t services < <(docker compose config --services | sort)
  [[ ${#services[@]} -gt 0 ]] || return 1
  # 선언된 서비스만 인자로 넘긴다 — 인자 없이 부르면 `run --rm` 잔재와 orphan 까지 읽어, 한 번 남은
  # 실패 컨테이너가 이후 모든 배포를 rollback 으로 끌고 간다.
  expected="$(printf '%s\n' "${services[@]}")"
  actual="$(docker compose ps -a --format '{{.Service}}' "${services[@]}" | sort -u)"
  [[ "$expected" == "$actual" ]] || return 1
  for cid in $(docker compose ps -aq "${services[@]}"); do
    IFS='|' read -r state code restart health <<< "$(docker inspect --format \
      '{{.State.Status}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$cid")"
    if [[ "$restart" == no ]]; then
      [[ "$state" == exited && "$code" == 0 ]] || return 1
    else
      [[ "$state" == running ]] || return 1
      [[ -z "$health" || "$health" == healthy ]] || return 1
    fi
  done
  return 0
}

# 컨테이너 상태와 별개로 호스트에서 실제 응답하는지 본다 — 포트 매핑이 어긋나면 컨테이너만 healthy 다.
PORT="$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2- || true)"; PORT="${PORT:-8000}"
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if services_healthy && curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    log "health OK — $VERSION 배포 완료"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep "$HEALTH_INTERVAL"
done

log "health 실패 — rollback 시도"
docker compose ps -a --format '  {{.Service}} {{.State}} {{.Health}}' || true
# 되돌아가는 것은 이미지뿐이다. migrate 가 적용한 스키마는 남으므로 구버전이 새 스키마에서 동작해야 한다.
log "주의: 스키마는 되돌리지 않는다 — 마이그레이션이 backward compatible 이어야 성립한다"
[[ -s .last-good ]] || die "health 실패 + .last-good 없음(최초 배포) — 수동 조치 필요"
LAST="$(cat .last-good)"
log "rollback -> $LAST"
sed -i "s#^ENGINE_IMAGE=.*#ENGINE_IMAGE=${LAST}#" .env
docker compose up -d
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if services_healthy && curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1; then
    die "새 버전 $VERSION 배포 실패 — $LAST 로 rollback 완료. 원인 확인 후 재시도"
  fi
  sleep "$HEALTH_INTERVAL"
done
die "rollback($LAST) 후에도 health 실패 — 수동 조치 필요"
