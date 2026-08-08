#!/usr/bin/env bash

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/assessment-engine}"
HEALTH_RETRIES="${HEALTH_RETRIES:-24}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"
APP_SERVICES=(web consumer worker)

log() { printf '[rotate] %s\n' "$*"; }
pg_quote_ident() { printf '"%s"' "${1//\"/\"\"}"; }
pg_quote_literal() { printf "'%s'" "${1//\'/\'\'}"; }
die() { printf '[rotate][error] %s\n' "$*" >&2; exit 1; }

TARGET="${1:-}"
case "$TARGET" in
  postgres|rabbitmq) ;;
  *) die "사용: rotate-secret.sh {postgres|rabbitmq}" ;;
esac

command -v docker >/dev/null 2>&1 || die "docker 미설치"
command -v openssl >/dev/null 2>&1 || die "openssl 미설치"
command -v flock >/dev/null 2>&1 || die "flock 미설치 (util-linux)"
[[ -f "$DEPLOY_DIR/.env" ]] || die "$DEPLOY_DIR/.env 없음"

cd "$DEPLOY_DIR"

exec 9>".deploy.lock"
# deploy.sh와 같은 lock으로 배포와 비밀번호 교체의 경합을 막는다.
flock -n 9 || die "다른 배포·교체가 진행 중"

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/ #.*//;s/[[:space:]]*$//;s/^"\(.*\)"$/\1/;s/^'"'"'\(.*\)'"'"'$/\1/' || true; }

SECRET_FILE="secrets/${TARGET}_password"
[[ -f "$SECRET_FILE" ]] || die "$SECRET_FILE 없음 — bootstrap.sh 먼저"

DB_USER="$(env_get POSTGRES_USER)"; DB_USER="${DB_USER:-assessment}"
DB_NAME="$(env_get POSTGRES_DB)";   DB_NAME="${DB_NAME:-assessment}"
MQ_USER="$(env_get RABBITMQ_USER)"; MQ_USER="${MQ_USER:-assessment}"
PORT="$(env_get WEB_PUBLISH_PORT)"; PORT="${PORT:-8000}"

OLD="$(cat "$SECRET_FILE")"
NEW="$(openssl rand -base64 32)"

set_postgres_password() {
  # 비밀번호를 host argv에 싣지 않고 컨테이너 secret 파일에서 읽는다.
  printf "ALTER USER %s PASSWORD %s" "$(pg_quote_ident "$DB_USER")" "$(pg_quote_literal "$1")" \
    | docker compose exec -T postgres sh -c \
        'PGPASSWORD="$(cat /run/secrets/postgres_password)" exec psql -h 127.0.0.1 -U "$0" -d "$1" -v ON_ERROR_STOP=1 -f -' \
        "$DB_USER" "$DB_NAME" >/dev/null
}

set_rabbitmq_password() {
  printf '%s' "$1" | docker compose exec -T rabbitmq sh -c \
    'exec rabbitmqctl change_password "$0" "$(cat)"' "$MQ_USER" >/dev/null
}

case "$TARGET" in
  postgres)
    log "postgres 계정 비밀번호 변경 ($DB_USER)"
    set_postgres_password "$NEW" || die "변경 실패 — 서버·파일 모두 원래 값 그대로다"
    ;;
  rabbitmq)
    log "rabbitmq 계정 비밀번호 변경 ($MQ_USER)"
    set_rabbitmq_password "$NEW" || die "변경 실패 — 서버·파일 모두 원래 값 그대로다"
    ;;
esac

rollback() {
  log "$1 — 원래 값으로 되돌린다"
  restore_server || die "서버 계정 복원 실패 — 서버·파일 모두 새 비밀번호다. 앱 상태를 직접 확인할 것"
  printf '%s' "$OLD" > "$SECRET_FILE"
  chmod 644 "$SECRET_FILE"
  docker compose up -d --force-recreate "${APP_SERVICES[@]}" || log "앱 재생성 실패 — 수동 조치 필요"
  die "교체 실패 — 원래 비밀번호로 롤백했다. 앱 로그 확인 후 재시도"
}

restore_server() {
  case "$TARGET" in
    postgres) set_postgres_password "$OLD" 2>/dev/null ;;
    rabbitmq) set_rabbitmq_password "$OLD" 2>/dev/null ;;
  esac
}

printf '%s' "$NEW" > "$SECRET_FILE"
chmod 644 "$SECRET_FILE"
log "$SECRET_FILE 갱신"

log "앱 재생성 (${APP_SERVICES[*]})"
docker compose up -d --force-recreate "${APP_SERVICES[@]}" || rollback "앱 재생성 실패"

for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1 \
    && curl -fsS "http://localhost:${PORT}/servers" >/dev/null 2>&1; then
    log "완료 — $TARGET 비밀번호 교체됨"
    exit 0
  fi
  sleep "$HEALTH_INTERVAL"
done

rollback "health 실패"
