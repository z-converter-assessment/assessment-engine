#!/usr/bin/env bash
#
# rotate-secret.sh — DB·broker 계정 비밀번호 교체. 배포 대상 VM 에서 사람이 실행한다.
#
#   sudo /opt/assessment-engine/rotate-secret.sh postgres
#   sudo /opt/assessment-engine/rotate-secret.sh rabbitmq
#
# secrets/ 파일만 바꾸면 서버 쪽 계정은 그대로라 접속이 끊긴다 — 서버에서 먼저 바꾸고 파일을 맞춘 뒤
# 앱을 재생성하는 순서를 스크립트가 강제한다. 앱 재생성 동안 수 초 다운타임이 있다.

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

# deploy.sh 와 같은 락 — 배포 중 비밀번호가 바뀌면 어느 값이 적용됐는지 추적이 끊긴다.
exec 9>".deploy.lock"
flock -n 9 || die "다른 배포·교체가 진행 중"

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/ #.*//;s/[[:space:]]*$//;s/^"\(.*\)"$/\1/;s/^'"'"'\(.*\)'"'"'$/\1/' || true; }

SECRET_FILE="secrets/${TARGET}_password"
[[ -f "$SECRET_FILE" ]] || die "$SECRET_FILE 없음 — bootstrap.sh 먼저"

DB_USER="$(env_get POSTGRES_USER)"; DB_USER="${DB_USER:-assessment}"
DB_NAME="$(env_get POSTGRES_DB)";   DB_NAME="${DB_NAME:-assessment}"
MQ_USER="$(env_get RABBITMQ_USER)"; MQ_USER="${MQ_USER:-assessment}"
PORT="$(env_get WEB_PUBLISH_PORT)"; PORT="${PORT:-8000}"

OLD="$(cat "$SECRET_FILE")"
# base64 는 영숫자와 +/= 뿐이라 SQL 리터럴·셸 인자에 그대로 넣어도 깨지지 않는다.
NEW="$(openssl rand -base64 32)"

# 비밀번호를 명령 인자로 넘기지 않는다 — 호스트 프로세스 목록에 평문이 뜬다. SQL 은 stdin 으로 넣고
# 접속 비밀번호는 컨테이너가 마운트된 secret 파일에서 직접 읽는다.
set_postgres_password() {
  printf "ALTER USER %s PASSWORD %s" "$(pg_quote_ident "$DB_USER")" "$(pg_quote_literal "$1")" \
    | docker compose exec -T postgres sh -c \
        'PGPASSWORD="$(cat /run/secrets/postgres_password)" exec psql -h 127.0.0.1 -U "$0" -d "$1" -v ON_ERROR_STOP=1 -f -' \
        "$DB_USER" "$DB_NAME" >/dev/null
}

# rabbitmqctl 은 비밀번호를 인자로만 받는다. sh 로 감싸 호스트 argv 에서는 감추되, 컨테이너 안 프로세스
# 목록에는 잠시 남는다 — 그 안은 이미 secret 을 읽을 수 있는 경계라 추가 노출이 아니다.
set_rabbitmq_password() {
  printf '%s' "$1" | docker compose exec -T rabbitmq sh -c \
    'exec rabbitmqctl change_password "$0" "$(cat)"' "$MQ_USER" >/dev/null
}

# 서버 쪽 계정을 먼저 바꾼다. 실패하면 파일도 앱도 건드리지 않았으니 그대로 중단하면 된다.
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
  # 서버 복원이 실패하면 파일은 NEW 로 둔다 — 서버=NEW/파일=OLD 로 갈라놓는 것보다 낫다.
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

# /health 는 정적 응답이라 DB 가 끊겨도 200 이다. 목록 페이지가 실제로 조회를 태우므로 함께 본다.
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -fsS "http://localhost:${PORT}/health" >/dev/null 2>&1 \
    && curl -fsS "http://localhost:${PORT}/servers" >/dev/null 2>&1; then
    log "완료 — $TARGET 비밀번호 교체됨"
    exit 0
  fi
  sleep "$HEALTH_INTERVAL"
done

rollback "health 실패"
