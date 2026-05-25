#!/usr/bin/env bash
# dev 풀 파이프라인 기동: Docker → migrate → web 헬스체크 → OrbStack VM 4대 + 에이전트 설치.
#
# 책임 분담:
#   - agent 바이너리는 `dev/bin/assessment-agent` 로 산출 — 본 스크립트 `ensure_agent_binary` 단계가 확보.
#     AGENT_BINARY_URL set 시 curl fetch (향후 agent CI release artifact 자동화 분기),
#     미설정 시 `dev/agent-build/build.sh` 호출 (sibling repo cross-build, default ../assessment-agent).
#   - Docker compose는 dev/docker-compose.yml (dev 한정 #A0, ADR 0012). migrate init-container가 alembic 자동 적용.
#   - OrbStack VM 은 `orb create <distro> <name>` 로 즉시 생성 — cloud-init 이 없어 Lima 의 boot stuck 우회 로직 불필요.
#   - VM 안 작업(패키지·바이너리·systemd·합성 부하 timer)은 모두 본 스크립트 post-provision — 옛 Lima yaml provision 을 흡수.
#
# OrbStack 전환 가정 (Lima → OrbStack):
#   - VM 명령: `ssh <name>@orb` (OrbStack 이 ~/.ssh/config 에 <name>@orb 자동 등록). sudo 는 passwordless.
#   - host 파일 전달: 바이너리는 ssh stdin(tee) 으로 — host 디렉토리 자동 마운트 가정·권한 문제 회피.
#   - VM 도달: web 컨테이너 → `<name>.orb.local:22` 직접 (Lima user-mode localPort 포워딩 폐기).
#   - host 도달: VM·컨테이너 → `host.docker.internal` (OrbStack 이 양쪽에서 host 로 해석).
#
# 멱등성: 모든 단계 안전 재실행. VM 이미 있으면 create 건너뜀, post-provision은 매번 재적용.

set -euo pipefail

# 호출 위치 무관 정합 — scripts/.. = 프로젝트 루트로 cwd 고정.
# BASH_SOURCE는 source된 스크립트 자체 경로 (직접 실행 시 $0과 동일) — source 시 부모의 $0으로 잘못 cd하지 않게.
cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.."

# dev compose 단일 진실 — `docker compose` 호출이 본 파일을 자동 인식.
export COMPOSE_FILE=dev/docker-compose.yml

# ────────────────────────────────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────────────────────────────────
readonly TIMEOUT=180                         # docker compose / migrate / web 헬스체크 공통 cap
# VM 진행 순서 — 시연 가시화 우선순위 (attention 발화 가장 빠른 VM 1번).
#   (1) web-server-01     — Debian 12 + nginx + agent-restart-demo timer (1m boot + 3m 주기, 시간당 20회)
#                           → attention.agent_unstable 가장 빠른 발화 (1m 후 첫 restart 즉시 가시화)
#   (2) offline-server-01 — Debian 13 trixie + finalize_vm offline-once mode (1회 발행 후 stop)
#                           → attention.gap_warnings 5m 후 발화 + insufficient_data 분류
#   (3) cache-server-01   — Rocky 9 + redis      → over_provisioned (light 부하)
#   (4) db-server-01      — AlmaLinux 9 + postgresql-server  → over_provisioned (light 부하, RPM initdb) + swap-trigger
# ORB_VMS_FILTER env로 약식 검증 가능 (콤마 구분, 예: `ORB_VMS_FILTER=web-server-01`).
# 미설정 시 4 VM 전체 — 정합 시연용 기본값. 호스트 영향 최소화 위해 모든 VM 의 합성 부하는
# light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화, 분류는 over_provisioned 대표.
if [ -n "${ORB_VMS_FILTER:-}" ]; then
  IFS=',' read -ra ORB_VMS <<< "$ORB_VMS_FILTER"
else
  ORB_VMS=(web-server-01 offline-server-01 cache-server-01 db-server-01)
fi
readonly ORB_VMS

# VM별 dispatch — services/ext_ip/mode/distro는 pipeline-up.sh가 단일 진실로 갖는다.
# associative array 대신 함수 — macOS default bash 3.2 호환 (brew bash 의존 없음).
vm_service() {
  case "$1" in
    cache-server-01)     echo "redis" ;;
    web-server-01)       echo "nginx" ;;
    db-server-01)        echo "postgres" ;;
    offline-server-01)   echo "none"  ;;
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}
vm_ext_ip() {
  case "$1" in
    web-server-01) echo "203.0.113.10" ;;
    *)             echo "" ;;
  esac
}
# offline-once: inventory 1회 발행 후 agent stop + VM stop. 5분 후 gap_warnings 발화 (시연용).
# persistent: 일반 — agent restart로 publish 계속.
vm_mode() {
  case "$1" in
    offline-server-01) echo "offline-once" ;;
    *)                 echo "persistent" ;;
  esac
}
# orb create 이미지 태그 — 옛 Lima yaml images(qcow2 URL) 를 OrbStack distro 태그로 대체.
# OrbStack 이 arch(Apple Silicon = arm64) 와 cloud image pull 을 자동 처리.
vm_distro() {
  case "$1" in
    web-server-01)     echo "debian:12" ;;
    offline-server-01) echo "debian:13" ;;
    cache-server-01)   echo "rocky:9" ;;
    db-server-01)      echo "alma:9" ;;
    *) echo "오류: 알 수 없는 VM: $1" >&2; return 1 ;;
  esac
}

# probe 도달 시연 — OrbStack VM 은 `<name>.orb.local:22` 로 web 컨테이너가 직접 도달
# (Lima user-mode localPort 포워딩 불필요). probe 대상(db-server-01.orb.local)은 docker-compose
# DISCOVERY_DEFAULT_TARGET default 가 단일 진실. 대상은 persistent VM (offline-once 는 stop 되어 부적합).

# dev/agent.env 필수 키 (agent.env.example과 단일 진실).
readonly REQUIRED_AGENT_KEYS=(
  RABBITMQ_USER
  RABBITMQ_PASSWORD
  RABBITMQ_EXCHANGE
  RABBITMQ_ROUTING_KEY_INVENTORY
  RABBITMQ_ROUTING_KEY_METRICS
  RABBITMQ_ROUTING_KEY_ERROR
  RABBITMQ_WORKER_USER
  RABBITMQ_WORKER_PASSWORD
  WORKER_TASK_EXCHANGE
  WORKER_TASK_QUEUE_PREFIX
  WORKER_TASK_RESULT_KEY
  WORKER_DOWNLOAD_ALLOWED_HOSTS
)

# ────────────────────────────────────────────────────────────────────────────
# Docker compose helpers (docker inspect 기반 정확 상태 read)
# ────────────────────────────────────────────────────────────────────────────
service_state() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "missing"; return; }
  docker inspect --format='{{.State.Status}}' "$cid" 2>/dev/null || echo "missing"
}
service_health() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "none"; return; }
  docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "none"
}
service_exit_code() {
  local cid
  cid="$(docker compose ps -aq "$1" 2>/dev/null | head -1)"
  [ -z "$cid" ] && { echo "-1"; return; }
  docker inspect --format='{{.State.ExitCode}}' "$cid" 2>/dev/null || echo "-1"
}

dump_logs_and_exit() {
  echo "--- migrate 로그 (마지막 30줄) ---"
  docker compose logs migrate --tail=30 || true
  echo "--- web 로그 (마지막 30줄) ---"
  docker compose logs web --tail=30 || true
  exit 1
}

# ────────────────────────────────────────────────────────────────────────────
# Step 1: 사전 점검
# ────────────────────────────────────────────────────────────────────────────
check_prereqs() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "오류: docker가 PATH에 없다."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "오류: docker daemon 미가동. OrbStack 기동 후 재시도."
    exit 1
  fi
  if ! command -v orb >/dev/null 2>&1; then
    echo "오류: orb가 PATH에 없다. OrbStack 설치 후 재시도 (https://orbstack.dev)."
    exit 1
  fi
  # env 파일 자동 cp — 없으면 example 복사 (dev 한정, example default 그대로 충분).
  # 루트 .env.example 은 prod 운영자 카탈로그라 본 dev 파이프라인은 dev/.env.example 사용.
  if [ ! -f dev/.env ]; then
    echo "  dev/.env 없음 — dev/.env.example 복사"
    cp dev/.env.example dev/.env
  fi
  if [ ! -f dev/agent.env ]; then
    echo "  dev/agent.env 없음 — dev/agent.env.example 복사"
    cp dev/agent.env.example dev/agent.env
  fi
  # 본 dev 파이프라인은 Apple Silicon + arm64 VM 가정. host arch 검증.
  if [ "$(uname -m)" != "arm64" ]; then
    echo "경고: dev 파이프라인은 Apple Silicon(arm64) 가정. 다른 host arch에선 바이너리 호환 검토 필요."
  fi
}

# agent 바이너리 확보 — AGENT_BINARY_URL 우선, 미설정 시 sibling repo cross-build.
# 본 분기는 확장 전제: agent CI release artifact 자동화 후 운영자가 AGENT_BINARY_URL 만 set
# 하면 fetch 흐름으로 즉시 전환. 현재는 build 흐름이 default.
ensure_agent_binary() {
  local out_bin="dev/bin/assessment-agent"
  if [ -n "${AGENT_BINARY_URL:-}" ]; then
    echo "  AGENT_BINARY_URL set — fetch (cache 없음, 매 호출마다 검증·갱신)..."
    mkdir -p dev/bin
    if ! curl -fsSL -o "$out_bin" "$AGENT_BINARY_URL"; then
      echo "오류: agent 바이너리 fetch 실패 ($AGENT_BINARY_URL)" >&2
      exit 1
    fi
    chmod 755 "$out_bin"
  else
    echo "  AGENT_BINARY_URL 미설정 — dev/agent-build/build.sh 호출 (sibling repo cross-build)..."
    ./dev/agent-build/build.sh
  fi
  if [ ! -f "$out_bin" ]; then
    echo "오류: $out_bin 확보 실패. AGENT_BINARY_URL 또는 AGENT_REPO_PATH 확인하라." >&2
    exit 1
  fi
}

# dev/agent.env를 host env로 export + 필수 키 검증.
load_agent_env() {
  set -a
  # shellcheck disable=SC1091
  source dev/agent.env
  set +a

  local k
  for k in "${REQUIRED_AGENT_KEYS[@]}"; do
    if [ -z "${!k:-}" ]; then
      echo "오류: dev/agent.env에 ${k} 누락. dev/agent.env.example 참조해 추가하라."
      exit 1
    fi
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 2: Docker 스택
# ────────────────────────────────────────────────────────────────────────────
start_docker_stack() {
  echo "[1/4] Docker 서비스 기동 중 (build 포함, postgres healthy 후 migrate 실행)..."
  # discovery probe 는 web 컨테이너 → db-server-01.orb.local:22 직접 (docker-compose default).
  # Lima 처럼 port 를 export 할 필요 없음 — OrbStack VM 은 표준 22 SSH 직접 도달.
  docker compose --profile gui up -d --build
}

wait_migrate_completed() {
  echo "[2/4] migrate(alembic upgrade head) 완료 대기 중..."
  SECONDS=0
  while :; do
    local state code
    state="$(service_state migrate)"
    if [ "$state" = "exited" ]; then
      code="$(service_exit_code migrate)"
      if [ "$code" = "0" ]; then
        echo "  migrate 완료 (exit 0)"
        return
      fi
      echo "오류: migrate 실패 (exit ${code})."
      dump_logs_and_exit
    fi
    if [ "$SECONDS" -ge "$TIMEOUT" ]; then
      echo "오류: migrate ${TIMEOUT}초 초과 (현재 state=${state})."
      dump_logs_and_exit
    fi
    sleep 2
    echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, state=${state})"
  done
}

wait_web_healthy() {
  echo "[3/4] web 헬스체크 대기 중..."
  SECONDS=0
  while [ "$(service_health web)" != "healthy" ]; do
    local health state
    health="$(service_health web)"
    state="$(service_state web)"
    if [ "$state" = "exited" ]; then
      echo "오류: web 컨테이너가 종료됨 (exit $(service_exit_code web))."
      dump_logs_and_exit
    fi
    if [ "$SECONDS" -ge "$TIMEOUT" ]; then
      echo "오류: web 헬스체크 ${TIMEOUT}초 초과 (health=${health})."
      dump_logs_and_exit
    fi
    sleep 3
    echo "  대기 중... (${SECONDS}s / ${TIMEOUT}s, health=${health})"
  done
  echo "  web healthy"
}

# ────────────────────────────────────────────────────────────────────────────
# Step 3: OrbStack VM 기동 + 에이전트 설치
# ────────────────────────────────────────────────────────────────────────────

# ssh 헬퍼 — OrbStack 이 등록한 `<name>@orb` 호스트로 명령. StrictHostKeyChecking 끔 (dev 재생성 빈번).
orb_ssh() {
  local vm="$1"; shift
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "${vm}@orb" "$@"
}

# orb create 멱등 — 이미 있으면 start, 없으면 create (동기, cloud-init 없어 즉시 ready).
start_or_resume_vm() {
  local vm="$1"
  local distro
  distro="$(vm_distro "$vm")"

  if orb list 2>/dev/null | grep -qw "$vm"; then
    echo "  [$vm] 이미 존재 — start (멱등)"
    orb start "$vm" >/dev/null 2>&1 || true
  else
    echo "  [$vm] create ($distro, 첫 실행 시 cloud image pull)..."
    orb create "$distro" "$vm"
  fi

  # SSH 도달 검증 — orb create 완료 = ready 라 짧은 cap 으로 충분 (Lima 의 boot stuck 우회 로직 불필요).
  local secs=0
  until orb_ssh "$vm" echo ok 2>/dev/null | grep -q ok; do
    sleep 2; secs=$((secs+2))
    if [ "$secs" -ge 60 ]; then
      echo "  [$vm] SSH 60s 초과 — boot 실패"
      return 1
    fi
  done
  echo "  [$vm] boot OK"
}

# VM 안에서 /etc/assessment-agent.env 생성 + OS detect + 서비스 패키지 설치 + 바이너리 설치 + systemd unit.
#
# 바이너리 전달: host dev/bin/assessment-agent 를 ssh stdin(tee) 으로 VM /tmp 에 옮긴 뒤 SCRIPT 가 cp.
# heredoc 변수 치환 규약:
#   - outer `<<SCRIPT` (unquoted): host shell이 ${RABBITMQ_*}·$vm·$ext_ip·$service 치환 후 VM bash로 전달.
#   - VM 내부 변수($ID/$svc_pkg/$svc_unit)는 `\$` escape — host shell이 손 안 대고 VM bash가 처리.
#   - inner `<<'UNIT'` (quoted): VM bash가 그대로 받음 (systemd unit literal에 '$' 없음).
#   - inner `<<ENV` (unquoted): host expand된 값들을 inline (그 안에 '$' 없음).
post_provision_vm() {
  local vm="$1"
  local service ext_ip hostname_override
  service="$(vm_service "$vm")"
  ext_ip="$(vm_ext_ip "$vm")"
  # 모든 VM은 hostname=VM 이름 통일.
  hostname_override="$vm"

  echo "  [$vm] 바이너리 전송 (ssh stdin)..."
  orb_ssh "$vm" 'cat > /tmp/assessment-agent' < dev/bin/assessment-agent

  echo "  [$vm] post-provision (env + 패키지 + 바이너리 + systemd)..."
  orb_ssh "$vm" sudo bash -s <<SCRIPT
set -euo pipefail

# 1) /etc/assessment-agent.env — host의 dev/agent.env + host.docker.internal + VM별 hostname/ext_ip.
#    env_needs_restart=1이면 env 변경됨 → 뒤 단계에서 restart 트리거.
new_env=\$(cat <<ENV
RABBITMQ_HOST=host.docker.internal
RABBITMQ_PORT=5672
RABBITMQ_VHOST=/assessment
RABBITMQ_USER=${RABBITMQ_USER}
RABBITMQ_PASS=${RABBITMQ_PASSWORD}
RABBITMQ_EXCHANGE=${RABBITMQ_EXCHANGE}
RABBITMQ_ROUTING_KEY_INVENTORY=${RABBITMQ_ROUTING_KEY_INVENTORY}
RABBITMQ_ROUTING_KEY_METRICS=${RABBITMQ_ROUTING_KEY_METRICS}
RABBITMQ_ROUTING_KEY_ERROR=${RABBITMQ_ROUTING_KEY_ERROR}
RABBITMQ_WORKER_USER=${RABBITMQ_WORKER_USER}
RABBITMQ_WORKER_PASS=${RABBITMQ_WORKER_PASSWORD}
WORKER_TASK_EXCHANGE=${WORKER_TASK_EXCHANGE}
WORKER_TASK_QUEUE_PREFIX=${WORKER_TASK_QUEUE_PREFIX}
WORKER_TASK_RESULT_KEY=${WORKER_TASK_RESULT_KEY}
WORKER_DOWNLOAD_ALLOWED_HOSTS=${WORKER_DOWNLOAD_ALLOWED_HOSTS}
AGENT_HOSTNAME_OVERRIDE=$hostname_override
AGENT_INTERVAL_SEC=60
${ext_ip:+AGENT_EXTERNAL_IP=$ext_ip}
ENV
)
env_needs_restart=0
if [ "\$new_env" != "\$(cat /etc/assessment-agent.env 2>/dev/null)" ]; then
  echo "\$new_env" > /etc/assessment-agent.env
  chmod 644 /etc/assessment-agent.env
  env_needs_restart=1
fi

# 2) 서비스 패키지 설치 + OS detect. service($service)는 host shell이 inline한 literal.
#    agent 바이너리는 본 repo dev/bin/에 사전 commit (Apple Silicon arm64 dev 한정).
#    devel 패키지·gcc·make 불필요 — runtime OpenSSL/glibc/zlib만 동적 의존이고 모두 base distro 기본 포함.
. /etc/os-release
case "\${ID}:$service" in
  ubuntu:redis|debian:redis)                       svc_pkg="redis-server";       svc_unit="redis-server" ;;
  rocky:redis|rhel:redis|almalinux:redis)          svc_pkg="redis";              svc_unit="redis" ;;
  *:nginx)                                         svc_pkg="nginx";              svc_unit="nginx" ;;
  ubuntu:postgres|debian:postgres)                 svc_pkg="postgresql";         svc_unit="postgresql" ;;
  rocky:postgres|rhel:postgres|almalinux:postgres) svc_pkg="postgresql-server"; svc_unit="postgresql" ;;
  *:none)                                          svc_pkg="";                   svc_unit="" ;;
  *) echo "지원 안 하는 OS/service: \${ID}/$service" >&2; exit 1 ;;
esac

# openssh-server — OrbStack VM 은 표준 22 sshd 미탑재(OrbStack SSH 는 host-network proxy)라 명시 설치.
# 서버 발견 probe(web 컨테이너 -> VM IP:22 SSH 도달성) 시연 대상 — 운영자가 VM IP 입력 시 OpenSSH 도달.
case "\${ID}" in
  ubuntu|debian)
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends curl iputils-ping openssh-server \${svc_pkg}
    systemctl enable --now ssh
    ;;
  rocky|rhel|almalinux)
    # install_weak_deps=False — Recommends 차단으로 transaction 메모리·디스크 절약 (1GiB OOM 회피).
    # tsflags=nodocs — man/info 등 문서 skip.
    dnf_opts="--setopt=install_weak_deps=False --setopt=tsflags=nodocs"
    dnf install -y \${dnf_opts} epel-release
    dnf install -y \${dnf_opts} curl iputils openssh-server \${svc_pkg}
    systemctl enable --now sshd
    ;;
  *) echo "지원 안 하는 OS: \${ID}" >&2; exit 1 ;;
esac

if [ -n "\${svc_unit}" ]; then
  # RPM family postgresql은 cluster init 수동 필요. apt 계열은 install 시 자동 init라 skip.
  if [ "$service" = "postgres" ]; then
    case "\${ID}" in
      rocky|rhel|almalinux)
        # 이미 init된 경우 silent skip.
        postgresql-setup --initdb 2>/dev/null || true
        ;;
    esac
  fi
  systemctl enable --now "\${svc_unit}"
fi

# 3) 바이너리 설치 — /tmp/assessment-agent (ssh stdin 전송) → /usr/local/bin/.
#    멱등 — 바이너리·env·unit 변경 없으면 restart 건너뜀 (attention.agent_unstable false positive 회피).
needs_restart=0
if ! cmp -s /tmp/assessment-agent /usr/local/bin/assessment-agent 2>/dev/null; then
  install -m 755 /tmp/assessment-agent /usr/local/bin/assessment-agent
  needs_restart=1
fi
rm -f /tmp/assessment-agent

unit_content=\$(cat <<'UNIT'
[Unit]
Description=Assessment Agent
After=network.target

[Service]
User=root
EnvironmentFile=/etc/assessment-agent.env
ExecStart=/usr/local/bin/assessment-agent
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
)
if [ ! -f /etc/systemd/system/assessment-agent.service ] || \\
   [ "\$unit_content" != "\$(cat /etc/systemd/system/assessment-agent.service)" ]; then
  echo "\$unit_content" > /etc/systemd/system/assessment-agent.service
  systemctl daemon-reload
  needs_restart=1
fi
systemctl enable assessment-agent
if ! systemctl is-active --quiet assessment-agent; then
  # 비활성 상태(첫 install 또는 stop된 경우)면 start
  systemctl start assessment-agent
elif [ "\$needs_restart" = "1" ] || [ "\$env_needs_restart" = "1" ]; then
  # binary·unit·env 변경 있을 때만 restart — agent_started_at 갱신 회피 (attention false positive 줄임)
  systemctl restart assessment-agent
fi
SCRIPT
}

# 합성 부하 timer 설치 — persistent VM(web/cache/db) 공통. 옛 Lima yaml provision 흡수.
# light (sustained CPU 1~3s + mem 5~20MB) — 차트 변동만 가시화. host.docker.internal 로 ping/curl.
install_synthetic_load() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
cat > /usr/local/bin/synthetic-load.sh <<'EOF'
#!/bin/bash
sleep $(( RANDOM % 30 ))
duration=$(( (RANDOM % 3) + 1 ))
timeout ${duration}s sha256sum /dev/zero > /dev/null 2>&1 || true
size=$(( (RANDOM % 16) + 5 ))
head -c ${size}M /dev/urandom > /tmp/synthetic-load 2>/dev/null || true
sync
rm -f /tmp/synthetic-load
count=$(( (RANDOM % 100) + 50 ))
dd if=/dev/zero of=/tmp/synthetic-io bs=4k count=${count} 2>/dev/null || true
sync
rm -f /tmp/synthetic-io
ping -c $(( (RANDOM % 5) + 3 )) -q host.docker.internal > /dev/null 2>&1 || true
curl -s -m 2 http://host.docker.internal:8000/health > /dev/null 2>&1 || true
if [ $((RANDOM % 2)) -eq 0 ]; then
  curl -s -m 2 http://host.docker.internal:8000/static/js/chart-utils.js > /dev/null 2>&1 || true
fi
EOF
chmod 755 /usr/local/bin/synthetic-load.sh
cat > /etc/systemd/system/synthetic-load.service <<'EOF'
[Unit]
Description=Synthetic load (metrics visualization aid)
[Service]
Type=oneshot
ExecStart=/usr/local/bin/synthetic-load.sh
EOF
cat > /etc/systemd/system/synthetic-load.timer <<'EOF'
[Unit]
Description=Run synthetic-load every minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable synthetic-load.timer
systemctl start synthetic-load.timer
SCRIPT
}

# swap-trigger 설치 (db-server-01 전용) — boot 1회 swap 활성 + 메모리 압박 → swap_used 임계 초과.
# attention.under_provisioned 발화 시연용. CPU 부하 추가 없음 (호스트 영향 회피).
install_swap_trigger() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
cat > /usr/local/bin/swap-trigger.sh <<'EOF'
#!/bin/bash
# 1) swap 파일 생성·활성 (cloud image default 에는 swap 없음).
if ! swapon --show | grep -q '^/swapfile'; then
  fallocate -l 512M /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=512
  chmod 600 /swapfile
  mkswap /swapfile > /dev/null
  swapon /swapfile
fi
# 2) swappiness 높여 kernel 이 swap 적극 사용하게.
sysctl -w vm.swappiness=100 > /dev/null
# 3) 메모리 압박 — 1000MiB 점유 + lock (RAM 부족 → swap out).
nohup bash -c 'head -c 1000M /dev/urandom > /tmp/swap-pressure 2>/dev/null; sleep 86400' &
EOF
chmod 755 /usr/local/bin/swap-trigger.sh
cat > /etc/systemd/system/swap-trigger.service <<'EOF'
[Unit]
Description=Swap trigger (attention.under_provisioned 시연용)
After=multi-user.target
[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/usr/local/bin/swap-trigger.sh
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable swap-trigger.service
systemctl start swap-trigger.service
SCRIPT
}

# agent-restart-demo 설치 (web-server-01 전용) — agent 주기 restart 로 agent_unstable 항상 발화.
# 1시간 슬라이딩 윈도우 임계 3회를 큰 마진으로 초과 (OnBootSec=1min + OnUnitActiveSec=3min → 시간당 약 20회).
install_agent_restart_demo() {
  local vm="$1"
  orb_ssh "$vm" sudo bash -s <<'SCRIPT'
set -euo pipefail
cat > /etc/systemd/system/agent-restart-demo.service <<'EOF'
[Unit]
Description=Agent restart demo (attention.agent_unstable 시연용)
[Service]
Type=oneshot
ExecStart=/bin/systemctl restart assessment-agent
EOF
cat > /etc/systemd/system/agent-restart-demo.timer <<'EOF'
[Unit]
Description=Restart assessment-agent every 3 minutes (demo, 시간당 20회 — 임계 3회의 6배 마진)
[Timer]
OnBootSec=1min
OnUnitActiveSec=3min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable agent-restart-demo.timer
systemctl start agent-restart-demo.timer
SCRIPT
}

# VM별 합성 부하·시연 트리거 설치 — 옛 Lima yaml provision 분기 흡수.
install_demo_loads() {
  local vm="$1"
  local mode
  mode="$(vm_mode "$vm")"
  # offline-once 는 publish 안 하므로 부하 timer 무의미 — skip.
  [ "$mode" = "offline-once" ] && return 0
  install_synthetic_load "$vm"
  case "$vm" in
    db-server-01)  install_swap_trigger "$vm" ;;
    web-server-01) install_agent_restart_demo "$vm" ;;
  esac
}

# offline-once mode: agent inventory 1회 발행 대기 후 agent stop + VM stop.
# 5분 후 gap_warnings 발화 (시연용 오프라인 서버 표시). host 자원 0.
finalize_vm() {
  local vm="$1"
  local mode
  mode="$(vm_mode "$vm")"
  if [ "$mode" = "offline-once" ]; then
    echo "  [$vm] inventory 1회 발행 대기 (15s) 후 offline 전환..."
    sleep 15
    orb_ssh "$vm" sudo systemctl stop assessment-agent 2>/dev/null || true
    orb_ssh "$vm" sudo systemctl disable assessment-agent 2>/dev/null || true
    orb stop "$vm" 2>/dev/null || true
    echo "  [$vm] offline 완료 (agent disable + VM stop)"
  fi
}

start_orb_vms() {
  echo "[4/4] OrbStack VM 기동 + 에이전트 설치 중..."
  local vm
  for vm in "${ORB_VMS[@]}"; do
    start_or_resume_vm "$vm"
    post_provision_vm "$vm"
    install_demo_loads "$vm"
    finalize_vm "$vm"
  done
}

# ────────────────────────────────────────────────────────────────────────────
# Step 4: 결과 요약
# ────────────────────────────────────────────────────────────────────────────
print_summary() {
  echo ""
  echo "환경 준비 완료"
  echo "  Web UI  : http://localhost:${WEB_PORT:-8000}/servers/"
  echo "  RabbitMQ: http://localhost:${RABBITMQ_MANAGEMENT_PORT:-15672}"
  # 서버 발견 probe 시연 — OrbStack .orb.local 은 컨테이너 미해석이라 운영자가 VM IP 를 모달에 직접 입력.
  echo "  서버 발견 probe 대상 VM IP (모달에 입력 -> SSH 도달성 확인):"
  local vm ip
  for vm in "${ORB_VMS[@]}"; do
    [ "$(vm_mode "$vm")" = "offline-once" ] && continue
    ip="$(orb_ssh "$vm" hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$ip" ] && echo "    $vm : $ip"
  done
}

main() {
  check_prereqs
  ensure_agent_binary
  load_agent_env
  start_docker_stack
  wait_migrate_completed
  wait_web_healthy
  start_orb_vms
  print_summary
}

# 직접 실행 시만 main 호출. `source pipeline-up.sh`로 함수만 가져올 때는 자동 실행 안 함
# (단계별 디버깅·검증 시 유용 — VM별 start_or_resume_vm/post_provision_vm/install_demo_loads 개별 호출 가능).
# set -u 환경에서 BASH_SOURCE 안전 access — macOS bash 3.2 호환.
if [ "${BASH_SOURCE[0]:-}" = "${0:-}" ]; then
  main "$@"
fi
